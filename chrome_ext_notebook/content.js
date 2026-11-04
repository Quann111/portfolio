// AutoVideo Notebook Bridge – chạy trên NotebookLM: tìm ô hỏi, nút gửi, đọc câu trả lời mới nhất.
// Không gọi API nội bộ, không đụng captcha: gặp giới hạn thì báo về để người dùng xử lý.
(() => {
  const V = chrome.runtime.getManifest().version + ':' + chrome.runtime.id
  if (window.__autovideoNotebookBridge === V) return
  window.__autovideoNotebookBridge = V
  const visible = el => { const r = el.getBoundingClientRect(); return r.width > 20 && r.height > 10 && getComputedStyle(el).visibility !== 'hidden' }
  const PLACEHOLDER = /(đặt câu hỏi|tạo nội dung|câu hỏi|start typing|ask|type|nhập)/i
  const SEND = /(^send$|gửi|submit|arrow_upward|arrow_forward)/i
  const BLOCKERS = /(captcha|unusual activity|hoạt động bất thường|not a robot|daily limit|giới hạn hằng ngày|đã đạt giới hạn|reached your limit|limit reached|try again later|thử lại sau)/i
  const NEW_NB = /(sổ ghi chú mới|tạo sổ ghi chú|tạo mới|tạo sổ|create new|new notebook|create notebook|\+\s*sổ ghi chú|\+\s*tạo)/i
  const label = b => ((b.getAttribute('aria-label') || '') + ' ' + (b.title || '') + ' ' + b.textContent).trim()
  const center = el => { const r = el.getBoundingClientRect(); return { x: Math.round(r.left + r.width / 2), y: Math.round(r.top + r.height / 2) } }
  const inputText = el => el ? (el.isContentEditable ? el.innerText : el.value || '').trim() : ''

  function isSourceInput (el) {
    if (!el) return false
    const r = el.getBoundingClientRect()
    if (r.left < 340 && r.width < 360) return true
    const s = ((el.getAttribute('placeholder') || '') + ' ' + (el.getAttribute('aria-label') || '') + ' ' + (el.className || '')).toLowerCase()
    return /tìm nguồn|nguồn mới|search source|add source|source|nguồn/i.test(s) && !/đặt câu hỏi|tạo nội dung|câu hỏi/i.test(s)
  }

  function findInput () {
    const cands = [...document.querySelectorAll('textarea, [contenteditable="true"], input[type="text"]')].filter(visible).filter(el => !isSourceInput(el))
    const byPh = cands.find(el => PLACEHOLDER.test(el.getAttribute('placeholder') || el.getAttribute('aria-label') ||
      el.getAttribute('aria-placeholder') || el.dataset.placeholder || ''))
    if (byPh) return byPh

    const bottomInputs = cands.filter(el => {
      const r = el.getBoundingClientRect()
      return r.top > window.innerHeight * 0.4 && r.left > 250
    })
    if (bottomInputs.length) {
      return bottomInputs.sort((a, b) => b.getBoundingClientRect().top - a.getBoundingClientRect().top)[0]
    }
    return cands.sort((a, b) => b.getBoundingClientRect().top - a.getBoundingClientRect().top)[0] || null
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
    if (!input) return null
    let el = input
    for (let i = 0; i < 8 && el; i++) {
      el = el.parentElement
      if (!el) break
      const btns = [...el.querySelectorAll('button, [role="button"]')].filter(visible)
      const named = btns.filter(b => SEND.test(label(b)))
      if (named.length) return named[named.length - 1]
      const withIcon = btns.find(b => {
        const svg = b.querySelector('svg, mat-icon, i')
        return svg && /(arrow|send|submit)/i.test(svg.getAttribute('data-icon') || svg.className || svg.textContent || '')
      })
      if (withIcon) return withIcon
      const ir = input.getBoundingClientRect()
      const rightBtns = btns.filter(b => {
        const br = b.getBoundingClientRect()
        return br.left >= ir.left && br.top >= ir.top - 30 && br.bottom <= ir.bottom + 40
      })
      if (rightBtns.length) {
        return rightBtns.sort((a, b) => b.getBoundingClientRect().right - a.getBoundingClientRect().right)[0]
      }
    }
    const allBtns = [...document.querySelectorAll('button, [role="button"]')].filter(visible).filter(b => !isSourceInput(b))
    const namedAll = allBtns.filter(b => SEND.test(label(b)))
    return namedAll[namedAll.length - 1] || null
  }

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

  function clearInput () {
    const input = findInput()
    if (!input) return { ok: false }
    if (input.isContentEditable) input.innerText = ''
    else Object.getOwnPropertyDescriptor(Object.getPrototypeOf(input), 'value').set.call(input, '')
    input.dispatchEvent(new Event('input', { bubbles: true }))
    input.dispatchEvent(new Event('change', { bubbles: true }))
    return { ok: true, typed: inputText(input) }
  }

  function triggerInputEvents () {
    const input = findInput()
    if (!input) return { ok: false }
    input.dispatchEvent(new Event('input', { bubbles: true }))
    input.dispatchEvent(new Event('change', { bubbles: true }))
    return { ok: true }
  }

  const STOP = /(stop|dừng|ngừng|cancel)/i
  const isStop = b => !!b && STOP.test(label(b))

  // Hộp "Ngừng tạo?" lỡ hiện ra → bấm "Tiếp tục tạo" để không mất câu trả lời
  function keepGenerating () {
    const b = [...document.querySelectorAll('button, [role="button"]')].filter(visible)
      .find(el => /(tiếp tục tạo|keep generating|continue generating)/i.test(label(el)))
    if (b) { b.click(); return { ok: true, dismissed: true } }
    return { ok: true, dismissed: false }
  }

  function clickSendButton () {
    const input = findInput()
    const send = input && findSend(input)
    if (isStop(send)) return { ok: false, error: 'đang tạo câu trả lời – không bấm nút Dừng' }
    if (send) {
      send.click()
      return { ok: true, label: label(send) }
    }
    return { ok: false }
  }

  function check (expectEnd) {
    if (!job) return { state: 'error', error: 'Tab NotebookLM đã tải lại giữa chừng' }
    const txt = chatText()
    const page = pageText()
    if (BLOCKERS.test(page) && !BLOCKERS.test(job.before)) {
      return { state: 'error', error: 'NotebookLM báo giới hạn/captcha – xử lý trên tab rồi bấm Tiếp tục', pause: true }
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
    const count = (s, k) => k ? s.split(k).length - 1 : 0
    const hasEnd = expectEnd && count(fresh, expectEnd) > count(job.prompt, expectEnd) - (txt.startsWith(job.before) ? 0 : 1)
    const send = input && findSend(input)
    keepGenerating()
    const idle = !isStop(send)
    if (fresh.length > 40 && idle && ((hasEnd && job.stable >= 2) || job.stable >= 12)) {
      const out = fresh
      job = null
      return { state: 'done', text: out }
    }
    return { state: 'waiting', note: `đang trả lời (${fresh.length} ký tự)` }
  }

  function newNotebookButton () {
    const btns = [...document.querySelectorAll('button, [role="button"], a')].filter(visible)
    let b = btns.find(el => NEW_NB.test(label(el)))
    if (!b) {
      b = btns.find(el => {
        const l = label(el)
        const r = el.getBoundingClientRect()
        return r.top < 80 && (/\+|add|create/i.test(l) || el.querySelector('svg, mat-icon'))
      })
    }
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

  function autoOpenNotebook () {
    const b = newNotebookButton()
    if (b && b.ok) {
      const el = [...document.querySelectorAll('button, [role="button"], a')].filter(visible).find(e => NEW_NB.test(label(e)))
      if (el) {
        el.click()
        return { ok: true, at: b.at }
      }
    }
    return { ok: false }
  }

  function initAutoClicker () {
    if (!/\/notebook\//.test(location.href) && !findInput()) {
      let tries = 0
      const iv = setInterval(() => {
        tries++
        if (/\/notebook\//.test(location.href) || findInput() || tries > 40) {
          clearInterval(iv)
          return
        }
        const res = autoOpenNotebook()
        if (res && res.ok) {
          clearInterval(iv)
        }
      }, 1000)
    }
  }
  setTimeout(initAutoClicker, 1500)

  chrome.runtime.onMessage.addListener((msg, _s, reply) => {
    if (msg.type === 'prepare') reply(prepare(msg.prompt))
    if (msg.type === 'locateSend') reply(locateSend())
    if (msg.type === 'triggerInput') reply(triggerInputEvents())
    if (msg.type === 'clickSend') reply(clickSendButton())
    if (msg.type === 'keepGenerating') reply(keepGenerating())
    if (msg.type === 'check') reply(check(msg.expectEnd))
    if (msg.type === 'newNotebook') reply(autoOpenNotebook())
    if (msg.type === 'diagnose') reply(diagnose())
    if (msg.type === 'clear') reply(clearInput())
  })
})()
