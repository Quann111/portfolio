// AutoVideo Gemini Bridge – chạy trên gemini.google.com
// Tìm ô nhập prompt (rich-textarea / ProseMirror), nút gửi, và đọc câu trả lời mới nhất từ Gemini.
(() => {
  const V = chrome.runtime.getManifest().version + ':' + chrome.runtime.id
  if (window.__autovideoGeminiBridge === V) return
  window.__autovideoGeminiBridge = V

  const visible = el => {
    const r = el.getBoundingClientRect()
    return r.width > 15 && r.height > 10 && getComputedStyle(el).visibility !== 'hidden'
  }

  const SEND = /(^send$|gửi|submit|arrow_forward|send_message)/i
  const STOP = /(^stop$|dừng|stop generating)/i
  const BLOCKERS = /(captcha|unusual activity|hoạt động bất thường|not a robot|limit reached|hết lượt|try again later|thử lại sau)/i
  const NEW_CHAT = /(cuộc trò chuyện mới|trò chuyện mới|new chat)/i

  const label = b => ((b.getAttribute('aria-label') || '') + ' ' + (b.title || '') + ' ' + b.textContent).trim()
  const center = el => {
    const r = el.getBoundingClientRect()
    return { x: Math.round(r.left + r.width / 2), y: Math.round(r.top + r.height / 2) }
  }

  function findInput () {
    // Gemini dùng thẻ có role="textbox" hoặc class rich-textarea, contenteditable="true"
    const cands = [...document.querySelectorAll('[contenteditable="true"], [role="textbox"], textarea')].filter(visible)
    const byPh = cands.find(el => /(nhập câu hỏi|mô tả tác vụ|ask gemini|type something|prompt)/i.test(
      el.getAttribute('placeholder') || el.getAttribute('aria-label') || el.dataset.placeholder || ''
    ))
    return byPh || cands.sort((a, b) => b.getBoundingClientRect().top - a.getBoundingClientRect().top)[0] || null
  }

  function composer (input) {
    let el = input
    for (let i = 0; i < 8 && el; i++) {
      el = el.parentElement
      if (el && el.querySelectorAll('button, [role="button"]').length >= 1) return el
    }
    return document
  }

  function findSend (input) {
    const box = composer(input)
    const btns = [...box.querySelectorAll('button, [role="button"]')].filter(visible)
    const named = btns.filter(b => SEND.test(label(b)))
    if (named.length) return named[named.length - 1]
    return btns.sort((a, b) => {
      const ra = a.getBoundingClientRect(), rb = b.getBoundingClientRect()
      return (rb.right + rb.bottom) - (ra.right + ra.bottom)
    })[0] || null
  }

  function chatBox () {
    // Vùng chứa lịch sử hội thoại trên Gemini
    for (const sel of ['main', 'infinite-scroller', '[role="region"]', '.chat-history', 'chat-window']) {
      const el = document.querySelector(sel)
      if (el && visible(el)) return el
    }
    return document.body
  }

  const chatText = () => (chatBox().innerText || '').trim()
  const pageText = () => document.body ? document.body.innerText.slice(-3000) : ''
  const inputText = el => el ? (el.isContentEditable ? el.innerText : el.value || '').trim() : ''

  let job = null

  function prepare (prompt) {
    const input = findInput()
    if (!input) return { ok: false, error: 'Không thấy ô nhập prompt Gemini – mở tab gemini.google.com', pause: true }
    if (BLOCKERS.test(pageText())) return { ok: false, error: 'Gemini báo giới hạn/captcha – xử lý trên tab rồi bấm Tiếp tục', pause: true }
    input.scrollIntoView({ block: 'center' })
    job = { before: chatText(), prompt, last: '', stable: 0, at: Date.now() }
    return { ok: true, input: center(input) }
  }

  function locateSend () {
    const input = findInput()
    const send = input && findSend(input)
    return {
      ok: !!send,
      send: send && center(send),
      sendLabel: send && label(send).slice(0, 30),
      disabled: !!(send && (send.disabled || send.getAttribute('aria-disabled') === 'true')),
      typed: inputText(input).slice(0, 40)
    }
  }

  function clearInput () {
    const input = findInput()
    if (!input) return { ok: false }
    if (input.isContentEditable) {
      input.innerText = ''
      input.innerHTML = ''
    } else {
      input.value = ''
    }
    input.dispatchEvent(new Event('input', { bubbles: true }))
    input.dispatchEvent(new Event('change', { bubbles: true }))
    return { ok: true, typed: inputText(input) }
  }

  function check (expectEnd) {
    if (!job) return { state: 'error', error: 'Tab Gemini đã tải lại giữa chừng' }
    const txt = chatText()
    const page = pageText()
    if (BLOCKERS.test(page) && !BLOCKERS.test(job.before)) {
      return { state: 'error', error: 'Gemini báo giới hạn/captcha – xử lý trên tab rồi bấm Tiếp tục', pause: true }
    }
    let fresh = txt.startsWith(job.before) ? txt.slice(job.before.length) : txt
    if (fresh === txt) {
      const key = job.prompt.slice(-80).trim()
      const i = txt.lastIndexOf(key)
      if (i >= 0) fresh = txt.slice(i + key.length)
    }
    fresh = fresh.trim()
    if (fresh === job.last) job.stable++
    else { job.stable = 0; job.last = fresh }

    const input = findInput()
    const stuck = inputText(input).slice(0, 40) === job.prompt.slice(0, 40) && Date.now() - job.at > 15000
    if (stuck) return { state: 'waiting', note: 'chưa gửi được câu hỏi' }

    // Kiểm tra nút Stop Generating còn xuất hiện không
    const send = input && findSend(input)
    const isGenerating = document.querySelector('button[aria-label*="Stop"], button[aria-label*="dừng"], [class*="stop"]')
    const idle = !isGenerating

    const count = (s, k) => k ? s.split(k).length - 1 : 0
    const hasEnd = expectEnd && count(fresh, expectEnd) > count(job.prompt, expectEnd) - (txt.startsWith(job.before) ? 0 : 1)

    // Gemini xong khi: có kết quả tương đối dài, không còn biểu tượng generating, và văn bản ổn định qua 3 chu kỳ
    if (fresh.length > 40 && idle && ((hasEnd && job.stable >= 2) || job.stable >= 8)) {
      const out = fresh
      job = null
      return { state: 'done', text: out }
    }
    return { state: 'waiting', note: `Gemini đang trả lời (${fresh.length} ký tự)` }
  }

  function newChatButton () {
    const b = [...document.querySelectorAll('button, a, div[role="button"]')].filter(visible).find(el => NEW_CHAT.test(label(el)))
    return b ? { ok: true, at: center(b), label: label(b).slice(0, 40) } : { ok: false }
  }

  function diagnose () {
    const input = findInput()
    const send = input && findSend(input)
    const d = s => s && { tag: s.tagName, ph: s.getAttribute('placeholder') || s.getAttribute('aria-label') || '', text: (s.textContent || '').trim().slice(0, 40) }
    const box = chatBox()
    return {
      url: location.href,
      input: d(input),
      send: d(send),
      newChat: newChatButton(),
      chatBox: box.tagName + '.' + (box.className || '').toString().slice(0, 60),
      chatChars: chatText().length,
      chatTail: chatText().slice(-200),
      blocker: BLOCKERS.test(pageText())
    }
  }

  chrome.runtime.onMessage.addListener((msg, _s, reply) => {
    if (msg.type === 'prepare') reply(prepare(msg.prompt))
    if (msg.type === 'locateSend') reply(locateSend())
    if (msg.type === 'check') reply(check(msg.expectEnd))
    if (msg.type === 'newChat') reply(newChatButton())
    if (msg.type === 'diagnose') reply(diagnose())
    if (msg.type === 'clear') reply(clearInput())
  })
})()
