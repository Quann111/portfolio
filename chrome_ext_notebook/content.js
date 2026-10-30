// AutoVideo Notebook Bridge – chạy trên NotebookLM: tìm ô hỏi, nút gửi, đọc câu trả lời mới nhất.
// Không gọi API nội bộ, không đụng captcha: gặp giới hạn thì báo về để người dùng xử lý.
(() => {
  const V = chrome.runtime.getManifest().version + ':' + chrome.runtime.id
  if (window.__autovideoNotebookBridge === V) return
  window.__autovideoNotebookBridge = V
  const visible = el => { const r = el.getBoundingClientRect(); return r.width > 20 && r.height > 10 && getComputedStyle(el).visibility !== 'hidden' }
  const PLACEHOLDER = /(đặt câu hỏi|câu hỏi|start typing|ask|type|nhập)/i
  const SEND = /(^send$|gửi|submit|arrow_upward|arrow_forward)/i
  const BLOCKERS = /(captcha|unusual activity|hoạt động bất thường|not a robot|daily limit|giới hạn hằng ngày|đã đạt giới hạn|reached your limit|limit reached|try again later|thử lại sau)/i
  const NEW_NB = /(tạo sổ ghi chú|tạo mới|create new|new notebook|create notebook)/i
  const label = b => ((b.getAttribute('aria-label') || '') + ' ' + (b.title || '') + ' ' + b.textContent).trim()
  const center = el => { const r = el.getBoundingClientRect(); return { x: Math.round(r.left + r.width / 2), y: Math.round(r.top + r.height / 2) } }
  const inputText = el => el ? (el.isContentEditable ? el.innerText : el.value || '').trim() : ''

  function findInput () {
    const cands = [...document.querySelectorAll('textarea, [contenteditable="true"], input[type="text"]')].filter(visible)
    const byPh = cands.find(el => PLACEHOLDER.test(el.getAttribute('placeholder') || el.getAttribute('aria-label') ||
      el.getAttribute('aria-placeholder') || el.dataset.placeholder || ''))
    // ô hỏi của chat nằm dưới cùng cột giữa
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
    const btns = [...composer(input).querySelectorAll('button, [role="button"]')].filter(visible)
    const named = btns.filter(b => SEND.test(label(b)))
    if (named.length) return named[named.length - 1]
    return btns.sort((a, b) => {
      const ra = a.getBoundingClientRect(), rb = b.getBoundingClientRect()
      return (rb.right + rb.bottom) - (ra.right + ra.bottom)
    })[0] || null
  }

  // khung hội thoại = phần tử cuộn lớn nhất nằm cùng cột với ô hỏi
  function chatBox () {
    const input = findInput()
    const x = input ? center(input).x : innerWidth / 2
    let best = null, area = 0
    for (const el of document.querySelectorAll('div, section, main, mat-sidenav-content')) {
      if (el.scrollHeight <= el.clientHeight + 40) continue
      const st = getComputedStyle(el).overflowY
      if (st !== 'auto' && st !== 'scroll') continue
      const r = el.getBoundingClientRect()
      if (r.left > x || r.right < x || r.width < 200) continue
      if (r.width * r.height > area) { area = r.width * r.height; best = el }
    }
    return best || document.body
  }

  const chatText = () => (chatBox().innerText || '').trim()
  const pageText = () => document.body ? document.body.innerText.slice(-3000) : ''

  let job = null

  function prepare (prompt) {
    const input = findInput()
    if (!input) return { ok: false, error: 'Không thấy ô đặt câu hỏi – mở một sổ ghi chú NotebookLM', pause: true }
    if (BLOCKERS.test(pageText())) return { ok: false, error: 'NotebookLM báo giới hạn/captcha – xử lý trên tab rồi bấm Tiếp tục', pause: true }
    input.scrollIntoView({ block: 'center' })
    job = { before: chatText(), prompt, last: '', stable: 0, at: Date.now() }
    return { ok: true, input: center(input) }
  }

  function locateSend () {
    const input = findInput()
    const send = input && findSend(input)
    return { ok: !!send, send: send && center(send), sendLabel: send && label(send).slice(0, 30),
      disabled: !!(send && (send.disabled || send.getAttribute('aria-disabled') === 'true')),
      typed: inputText(input).slice(0, 40) }
  }

  // xoá thẳng ô hỏi (Ctrl+A/Backspace qua CDP có lúc không ăn → chữ cũ kẹt lại làm hỏng các job sau)
  function clearInput () {
    const input = findInput()
    if (!input) return { ok: false }
    if (input.isContentEditable) input.innerText = ''
    else Object.getOwnPropertyDescriptor(Object.getPrototypeOf(input), 'value').set.call(input, '')
    input.dispatchEvent(new Event('input', { bubbles: true }))
    input.dispatchEvent(new Event('change', { bubbles: true }))
    return { ok: true, typed: inputText(input) }
  }

  // câu trả lời = phần chữ mới xuất hiện trong khung chat sau khi gửi; xong khi chữ đứng yên đủ lâu
  function check (expectEnd) {
    if (!job) return { state: 'error', error: 'Tab NotebookLM đã tải lại giữa chừng' }
    const txt = chatText()
    const page = pageText()
    if (BLOCKERS.test(page) && !BLOCKERS.test(job.before)) {
      return { state: 'error', error: 'NotebookLM báo giới hạn/captcha – xử lý trên tab rồi bấm Tiếp tục', pause: true }
    }
    let fresh = txt.startsWith(job.before) ? txt.slice(job.before.length) : txt
    if (fresh === txt) {   // khung chat bị cuộn/ảo hoá: lấy phần sau lần xuất hiện cuối của câu hỏi
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
    // câu hỏi hiện lại trong khung chat cũng chứa dấu kết thúc → cần thêm 1 lần xuất hiện nữa (của câu trả lời)
    const count = (s, k) => k ? s.split(k).length - 1 : 0
    const hasEnd = expectEnd && count(fresh, expectEnd) > count(job.prompt, expectEnd) - (txt.startsWith(job.before) ? 0 : 1)
    const send = input && findSend(input)
    const idle = !send || !(send.getAttribute('aria-label') || '').match(/stop|dừng/i)
    if (fresh.length > 40 && idle && ((hasEnd && job.stable >= 2) || job.stable >= 12)) {
      const out = fresh
      job = null
      return { state: 'done', text: out }
    }
    return { state: 'waiting', note: `đang trả lời (${fresh.length} ký tự)` }
  }

  function newNotebookButton () {
    const b = [...document.querySelectorAll('button, [role="button"], a')].filter(visible).find(el => NEW_NB.test(label(el)))
    return b ? { ok: true, at: center(b), label: label(b).slice(0, 40) } : { ok: false }
  }

  function diagnose () {
    const input = findInput()
    const send = input && findSend(input)
    const d = s => s && { tag: s.tagName, ph: s.getAttribute('placeholder') || s.getAttribute('aria-label') || '',
      text: (s.textContent || '').trim().slice(0, 40) }
    const box = chatBox()
    return { url: location.href, input: d(input), send: d(send), newNotebook: newNotebookButton(),
      chatBox: box.tagName + '.' + (box.className || '').toString().slice(0, 60), chatChars: chatText().length,
      chatTail: chatText().slice(-200), blocker: BLOCKERS.test(pageText()) }
  }

  chrome.runtime.onMessage.addListener((msg, _s, reply) => {
    if (msg.type === 'prepare') reply(prepare(msg.prompt))
    if (msg.type === 'locateSend') reply(locateSend())
    if (msg.type === 'check') reply(check(msg.expectEnd))
    if (msg.type === 'newNotebook') reply(newNotebookButton())
    if (msg.type === 'diagnose') reply(diagnose())
    if (msg.type === 'clear') reply(clearInput())
  })
})()
