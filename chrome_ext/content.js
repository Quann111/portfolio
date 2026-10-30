// AutoVideo Flow Bridge – chạy trên flow.google.com: thao tác giao diện như người dùng (điền prompt, bấm tạo,
// chờ ảnh mới). Không đụng tới captcha/token: gặp captcha hay lỗi giới hạn thì báo về để người dùng xử lý.
(() => {
  // tránh nạp 2 lần cùng phiên bản; khi extension được Reload thì bản mới vẫn nạp được
  const V = chrome.runtime.getManifest().version + ':' + chrome.runtime.id
  if (window.__autovideoFlowBridge === V) return
  window.__autovideoFlowBridge = V
  const sleep = ms => new Promise(r => setTimeout(r, ms))
  const visible = el => { const r = el.getBoundingClientRect(); return r.width > 20 && r.height > 10 && getComputedStyle(el).visibility !== 'hidden' }
  const PLACEHOLDER = /(muốn tạo|what do you want|describe|mô tả|prompt|nhập)/i
  const SEND = /(^send$|gửi|tạo|create|generate|submit|arrow_forward|arrow_upward)/i
  const BLOCKERS = /(captcha|unusual activity|hoạt động bất thường|xác minh bạn không phải|not a robot|quota|giới hạn|limit reached|hết lượt|try again later|thử lại sau)/i
  const CAPTCHA_RE = /(captcha|unusual activity|hoạt động bất thường|xác minh bạn không phải|not a robot)/i

  // Chỉ xét HỘP THOẠI / THÔNG BÁO NỔI / khung reCAPTCHA thật – không dò chữ toàn trang (dễ báo nhầm vì chữ
  // "giới hạn", "limit" có thể nằm ở chỗ khác trên giao diện). Trả về {kind, text} hoặc null.
  function blocker () {
    const frame = [...document.querySelectorAll('iframe')].find(f => visible(f) &&
      /recaptcha|captcha|challenge/i.test((f.src || '') + ' ' + (f.title || '')))
    if (frame) return { kind: 'captcha', text: 'khung xác minh reCAPTCHA' }
    const card = failedCards()[0]
    if (card) return { kind: 'unusual', text: (card.innerText || '').replace(/\s+/g, ' ').trim().slice(0, 160) }
    const sel = '[role="alert"], [role="alertdialog"], [role="dialog"], [aria-live="assertive"], [aria-live="polite"], ' +
      '[class*="toast" i], [class*="snack" i], [class*="error" i], [class*="banner" i]'
    for (const el of document.querySelectorAll(sel)) {
      if (!visible(el)) continue
      const t = (el.innerText || '').replace(/\s+/g, ' ').trim()
      const m = t.match(BLOCKERS)
      if (m && t.length < 600) {
        const i = Math.max(0, m.index - 60)
        return { kind: CAPTCHA_RE.test(t) ? 'captcha' : 'limit', text: t.slice(i, i + 160) }
      }
    }
    return null
  }
  // Thẻ ảnh lỗi trên lưới: "Không thành công · …hoạt động bất thường…" (không phải reCAPTCHA thật, chưa tính phí)
  // → tự xoá thẻ, tải lại trang, gửi lại prompt (background.js: recoverUnusual)
  const FAILED = /(không thành công|failed|something went wrong)/i
  const UNUSUAL = /(hoạt động bất thường|unusual activity)/i
  function failedCards () {
    const all = [...document.querySelectorAll('div')].filter(el => {
      const t = el.innerText || ''
      return t.length < 600 && FAILED.test(t) && UNUSUAL.test(t) && visible(el) &&
        el.querySelectorAll('button, [role="button"]').length > 0
    })
    // chỉ giữ thẻ nhỏ nhất (không chứa thẻ khác cũng khớp)
    return all.filter(el => !all.some(o => o !== el && el.contains(o) && o.querySelectorAll('button, [role="button"]').length > 0))
  }
  const DEL = /(delete|xoá|xóa|thùng rác|trash|remove)/i
  function deleteButtons () {
    return failedCards().map(card => {
      const btns = [...card.querySelectorAll('button, [role="button"]')].filter(visible)
      const b = btns.find(x => DEL.test(label(x))) || btns[btns.length - 1]
      if (b) b.scrollIntoView({ block: 'center' })
      return b
    }).filter(Boolean).map(center)
  }
  function confirmButton () {
    const b = [...document.querySelectorAll('[role="dialog"] button, [role="alertdialog"] button')].filter(visible)
      .find(x => (DEL.test(label(x)) || /xác nhận|confirm/i.test(label(x))) && !/huỷ|hủy|cancel/i.test(label(x)))
    return b ? center(b) : null
  }
  const blockMsg = b => b.kind === 'unusual' ? `Flow báo hoạt động bất thường: "${b.text}"` : (b.kind === 'captcha'
    ? `Flow hiện xác minh captcha – xử lý trên tab rồi bấm Tiếp tục. Flow báo: "${b.text}"`
    : `Flow báo giới hạn/hết lượt – profile tự thử lại sau 30 phút. Flow báo: "${b.text}"`)
  const POLICY = /(vi phạm|policy|không thể tạo|couldn.t generate|can.t generate|unable to generate|nội dung bị chặn)/i

  
  // Tìm nút tạo dự án mới trên trang chủ flow.google.com (Dự án mới / New project)
  const NEW_PROJECT_RE = /(dự án mới|tạo dự án|new project|create project|\+\s*dự án|\+\s*project)/i
  function findNewProjectButton () {
    // 0. Ưu tiên tìm chính xác icon/chữ hoặc class chứa "+ Dự án mới"
    for (const el of document.querySelectorAll('button, a, div, span, mat-card, [role="button"]')) {
      const t = (el.innerText || el.textContent || '').trim()
      if (/^\+\s*Dự án mới$/i.test(t) || /^Dự án mới$/i.test(t)) {
        return el.closest('button, a, [role="button"], [class*="mat-focus-indicator"]') || el
      }
    }
    // 1. Quét tìm button/card có text chứa "Dự án mới" / "New project"
    const all = [...document.querySelectorAll('button, a, div[role="button"], [class*="mat-focus-indicator"], mat-card, div')].filter(visible)
    for (const el of all) {
      const t = ((el.getAttribute('aria-label') || '') + ' ' + (el.innerText || '')).trim()
      if (t && NEW_PROJECT_RE.test(t) && t.length < 60) {
        return el
      }
    }
    // 2. Tìm thẻ mat-card hoặc div có class mat-focus-indicator
    for (const el of document.querySelectorAll('[class*="mat-focus-indicator"]')) {
      if (visible(el)) {
        const t = (el.innerText || '').trim()
        if (/dự án|project|\+/i.test(t) && t.length < 50) return el
      }
    }
    // 3. Tìm nút quay lại trang chủ nếu đang bị lạc vào flow.google.com/404
    const homeBtn = [...document.querySelectorAll('button, a')].filter(visible)
      .find(b => /trang chủ|home/i.test(b.innerText || ''))
    if (homeBtn && /\/404/.test(location.href)) return homeBtn

    // 4. Tìm thẻ có icon '+'
    const plusCards = all.filter(el => {
      const t = (el.innerText || '').trim()
      return /^\+\s*/.test(t) && t.length < 30
    })
    return plusCards[0] || null
  }

  function autoOpenProject () {
    const btn = findNewProjectButton()
    if (btn) {
      const pos = center(btn)
      btn.click()
      return { ok: true, clicked: label(btn).slice(0, 30), at: pos }
    }
    return { ok: false }
  }

  function findInput () {
    const cands = [...document.querySelectorAll('textarea, [contenteditable="true"], input[type="text"]')].filter(visible)
    const ph = el => el.getAttribute('placeholder') || el.getAttribute('aria-label') || el.getAttribute('aria-placeholder') ||
      el.dataset.placeholder || ''
    // ô chính của Flow là "Bạn muốn tạo những gì?"; khung chat "Tác nhân" (agent) bên phải là "Bạn muốn tạo gì?" → bỏ qua
    const main = cands.find(el => /tạo những gì|what do you want to create/i.test(ph(el)))
    if (main) return main
    const byPh = cands.find(el => !/^bạn muốn tạo gì/i.test(ph(el).trim()) && PLACEHOLDER.test(el.getAttribute('placeholder') || el.getAttribute('aria-label') ||
      el.getAttribute('aria-placeholder') || el.dataset.placeholder || ''))
    if (byPh) return byPh
    // ô nhập lớn nhất ở nửa dưới màn hình (ô prompt của Flow nằm dưới cùng)
    return cands.sort((a, b) => b.getBoundingClientRect().top - a.getBoundingClientRect().top)[0] || null
  }

  const NOT_SEND = /(close|đóng|xoá|xóa|clear|tác nhân|agent|nano|banana|imagen|veo|model|thêm|add|attach|\+|x1|x2|x4|thông tin|info|information|icon)/i
  const label = b => ((b.getAttribute('aria-label') || '') + ' ' + (b.title || '') + ' ' + b.textContent).trim()

  // khung soạn prompt = tổ tiên gần nhất của ô nhập có ≥ 2 nút
  function composer (input) {
    let el = input
    for (let i = 0; i < 8 && el; i++) {
      el = el.parentElement
      if (el && el.querySelectorAll('button, [role="button"]').length >= 2) return el
    }
    return document
  }

  function findSend (input) {
    const box = composer(input)
    // Lọc bỏ nút chứa icon info / chữ i / nút đóng
    const btns = [...box.querySelectorAll('button, [role="button"]')].filter(visible)
      .filter(b => !NOT_SEND.test(label(b)))
      .filter(b => !b.querySelector('svg[data-icon="info"], [aria-label*="info" i], [aria-label*="chi tiết" i]'))
    // Ưu tiên cao nhất: nút có icon mũi tên (arrow_forward, arrow_upward, send)
    const arrowBtn = btns.find(b => /(arrow_forward|arrow_upward|send|gửi|submit)/i.test(b.innerText + ' ' + (b.getAttribute('aria-label') || '')))
    if (arrowBtn) return arrowBtn
    const named = btns.filter(b => SEND.test(label(b)))
    if (named.length) return named[named.length - 1]
    // Flow: nút gửi là nút tròn nhỏ ở góc DƯỚI-PHẢI của khung soạn (mũi tên →), loại trừ nút info có màu cam/nổi
    const small = btns.filter(b => {
      const r = b.getBoundingClientRect()
      return r.width <= 64 && r.height <= 64 && !/info|thông tin/i.test(label(b))
    })
    return (small.length ? small : btns).sort((a, b) => {
      const ra = a.getBoundingClientRect(), rb = b.getBoundingClientRect()
      return (rb.right + rb.bottom) - (ra.right + ra.bottom)
    })[0] || null
  }

  const inputText = el => el ? (el.isContentEditable ? el.innerText : el.value || '').trim() : ''

  function pressSend (input) {
    const send = findSend(input)
    if (send && !send.disabled) { send.click(); return 'click:' + label(send).slice(0, 30) }
    for (const type of ['keydown', 'keypress', 'keyup']) {
      input.dispatchEvent(new KeyboardEvent(type, { key: 'Enter', code: 'Enter', keyCode: 13, which: 13, bubbles: true }))
    }
    return 'enter'
  }

  function setPrompt (el, text) {
    el.focus()
    if (el.isContentEditable) {
      document.execCommand('selectAll', false)
      document.execCommand('insertText', false, text)
    } else {
      const proto = el.tagName === 'TEXTAREA' ? HTMLTextAreaElement.prototype : HTMLInputElement.prototype
      Object.getOwnPropertyDescriptor(proto, 'value').set.call(el, text)
      el.dispatchEvent(new Event('input', { bubbles: true }))
      el.dispatchEvent(new Event('change', { bubbles: true }))
    }
  }

  const bigImages = () => [...document.images].filter(i => i.complete && i.naturalWidth >= 400 && i.naturalHeight >= 220)

  function pageText () { return document.body ? document.body.innerText.slice(-4000) : '' }

  async function toDataUrl (src) {
    if (!src.startsWith('blob:') && !src.startsWith('data:')) return null
    const blob = await (await fetch(src)).blob()
    return await new Promise(res => { const r = new FileReader(); r.onload = () => res({ dataUrl: r.result, mime: blob.type }); r.readAsDataURL(blob) })
  }

  // Vòng chờ nằm ở service worker (không bị Chrome làm chậm khi tab Flow ở nền);
  // tab chỉ làm 2 việc ngắn: submit (điền + bấm tạo) và check (đã có ảnh mới chưa).
  let job = null

  const center = el => { const r = el.getBoundingClientRect(); return { x: Math.round(r.left + r.width / 2), y: Math.round(r.top + r.height / 2) } }

  // Bước 1 (trước khi service worker gõ/click thật qua DevTools): ghi lại trạng thái, trả toạ độ ô nhập
  // Flow còn ảnh đang tạo dở (ô hiện "13%", "99%"…) – vd ảnh thứ 2 của chế độ x2, hoặc ảnh của prompt trước bị
  // quá giờ. Gửi prompt mới lúc này thì ảnh về trễ sẽ bị nhận nhầm là ảnh của prompt mới → sai cảnh.
  function generating () {
    return [...document.querySelectorAll('div, span')].some(el => el.childElementCount === 0 && visible(el) &&
      /^\d{1,3}\s?%$/.test((el.textContent || '').trim()))
  }

  // Flow đã nhận prompt chưa: có ô đang tạo ("%") hoặc đã có ảnh mới so với lúc chuẩn bị
  function started () {
    if (!job) return { started: false }
    const fresh = bigImages().some(i => !job.before.has(i.currentSrc || i.src))
    return { started: generating() || fresh }
  }

  function prepare (prompt) {
    restoreOverlays()          // phòng khi lần trước chưa hiện lại khung prompt
    const input = findInput()
    if (!input) return { ok: false, error: 'Không thấy ô nhập prompt – mở một project trên flow.google.com', pause: true }
    const blk = blocker()
    if (blk) return { ok: false, error: blockMsg(blk), pause: blk.kind !== 'unusual', retry: blk.kind === 'unusual', block: blk.kind }
    if (generating()) return { ok: false, busy: true, error: 'Flow còn ảnh đang tạo dở – chờ xong rồi mới gửi prompt mới' }
    // Flow đang để tạo nhiều ảnh/1 prompt (x2, x3, x4) → tốn gấp đôi lượt; dừng riêng profile này, nhờ chỉnh về x1
    const multi = [...composer(input).querySelectorAll('button, [role="button"]')].filter(visible)
      .map(b => label(b)).find(t => /(^|\s)x[2-4](\s|$)/i.test(t))
    if (multi) {
      return { ok: false, pause: true, error: `Flow đang để tạo ${multi.match(/x[2-4]/i)[0]} ảnh mỗi prompt – bấm chữ ` +
        `"${multi.match(/x[2-4]/i)[0]}" cạnh "Nano Banana" ở ô nhập, chọn x1, rồi bấm "Tiếp tục profile này"` }
    }
    input.scrollIntoView({ block: 'center' })
    job = { before: new Set(bigImages().map(i => i.currentSrc || i.src)), textBefore: pageText(), seenAt: 0,
      input, prompt, submittedAt: Date.now(), presses: ['cdp'] }
    return { ok: true, input: center(input) }
  }

  // Bước 2: toạ độ nút gửi + xác nhận chữ đã vào ô
  function locateSend () {
    const input = findInput()
    const send = input && findSend(input)
    return { ok: !!send, send: send && center(send), sendLabel: send && label(send).slice(0, 30),
      disabled: !!(send && (send.disabled || send.getAttribute('aria-disabled') === 'true')),
      typed: inputText(input).slice(0, 40) }
  }

  function submit (prompt) {
    const input = findInput()
    if (!input) return { ok: false, error: 'Không thấy ô nhập prompt – mở một project trên flow.google.com', pause: true }
    const blk = blocker()
    if (blk) return { ok: false, error: blockMsg(blk), pause: blk.kind !== 'unusual', retry: blk.kind === 'unusual', block: blk.kind }
    job = { before: new Set(bigImages().map(i => i.currentSrc || i.src)), textBefore: pageText(), seenAt: 0,
      input, prompt, submittedAt: Date.now(), presses: [] }
    setPrompt(input, prompt)
    setTimeout(() => job && job.presses.push(pressSend(input)), 800)
    return { ok: true }
  }

  // prompt vẫn còn nguyên trong ô sau 6s → nút gửi chưa ăn → bấm lại / Enter (tối đa 2 lần)
  function ensureSent () {
    if (!job || job.presses.length >= 3 || Date.now() - job.submittedAt < 6000 * job.presses.length) return
    const now = inputText(findInput())
    if (now && now.slice(0, 40) === job.prompt.slice(0, 40)) {
      job.presses.push(job.presses.length === 1 ? pressSend(findInput()) : (() => {
        const el = findInput()
        for (const type of ['keydown', 'keyup']) {
          el.dispatchEvent(new KeyboardEvent(type, { key: 'Enter', code: 'Enter', keyCode: 13, which: 13, bubbles: true }))
        }
        return 'enter'
      })())
    }
  }

  async function check () {
    if (!job) return { state: 'error', error: 'Tab Flow đã tải lại giữa chừng' }
    const txt = pageText()
    const blk = blocker()
    if (blk) return { state: 'error', error: blockMsg(blk), pause: blk.kind !== 'unusual', retry: blk.kind === 'unusual', block: blk.kind }
    // prompt rơi vào trợ lý "Tác nhân" (agent) → nó trả lời bằng chữ thay vì tạo ảnh thường
    const AGENT = /I'?m going to generate|default setting is \d+x|Tôi sẽ tạo \d+ (hình|ảnh)|cài đặt mặc định của bạn là/i
    if (txt !== job.textBefore && AGENT.test(txt) && !AGENT.test(job.textBefore)) {
      return { state: 'error', pause: true,
        error: 'Flow đang ở chế độ "Tác nhân" (trợ lý chat) – bấm nút "Tác nhân" để TẮT, đóng khung chat, chọn x1 rồi bấm Tiếp tục' }
    }
    if (txt !== job.textBefore && POLICY.test(txt) && !POLICY.test(job.textBefore)) {
      return { state: 'error', error: 'Flow từ chối prompt (chính sách nội dung)' }
    }
    const fresh = bigImages().filter(i => !job.before.has(i.currentSrc || i.src))
    if (!fresh.length) {
      if (job.presses[0] !== 'cdp') ensureSent()
      // chưa gửi được = sau 20s Flow vẫn chưa bắt đầu tạo gì (không dựa vào chữ còn trong ô – Flow giữ lại chữ)
      const stuck = !started().started && Date.now() - job.submittedAt > 20000
      return { state: 'waiting', note: stuck ? `chưa gửi được (đã thử: ${job.presses.join(', ')})` : 'đang tạo' }
    }
    if (!job.seenAt) { job.seenAt = Date.now(); return { state: 'waiting' } }   // đợi thêm 1 nhịp cho ảnh nét
    // còn ảnh khác của CHÍNH prompt này đang tạo (x2) → chờ xong hết, để không ảnh nào của prompt này về trễ
    if (generating() && Date.now() - job.seenAt < 90000) return { state: 'waiting', note: 'chờ các ảnh cùng prompt xong' }
    const img = fresh.sort((a, b) => b.naturalWidth * b.naturalHeight - a.naturalWidth * a.naturalHeight)[0]
    const src = img.currentSrc || img.src
    job = null
    let data = await toDataUrl(src).catch(() => null)
    if (!data) {   // ảnh https: thử tải trong tab (có cookie của Flow)
      data = await fetch(src, { credentials: 'include' }).then(r => r.ok ? r.blob() : null)
        .then(b => b && new Promise(res => { const fr = new FileReader(); fr.onload = () => res({ dataUrl: fr.result, mime: b.type }); fr.readAsDataURL(b) }))
        .catch(() => null)
    }
    img.scrollIntoView({ block: 'center' })
    const r = img.getBoundingClientRect()
    if (!data) hideOverlays(img, r)      // sắp phải chụp vùng ảnh → ẩn khung prompt/khung chat/chú thích đè lên
    return { state: 'done', ...(data || {}), url: src,
      rect: { x: r.left, y: r.top, width: r.width, height: r.height, naturalWidth: img.naturalWidth } }
  }

  // Mọi phần tử nằm ĐÈ lên ảnh (khung "Bạn muốn tạo…", khung Tác nhân, chú thích khi rê chuột) → ẩn tạm để chụp sạch
  let hidden = []
  function hideOverlays (img, r) {
    restoreOverlays()
    const pts = []
    for (const fx of [0.05, 0.3, 0.5, 0.7, 0.95]) {
      for (const fy of [0.05, 0.3, 0.5, 0.7, 0.95]) pts.push([r.left + r.width * fx, r.top + r.height * fy])
    }
    const seen = new Set()
    for (const [x, y] of pts) {
      for (const el of document.elementsFromPoint(x, y)) {
        if (el === img) break                                 // phía dưới ảnh không cần quan tâm
        if (seen.has(el) || el.contains(img)) continue
        seen.add(el)
        hidden.push([el, el.style.visibility])
        el.style.visibility = 'hidden'
      }
    }
  }
  function restoreOverlays () {
    for (const [el, v] of hidden) el.style.visibility = v
    hidden = []
  }

  function diagnose () {
    const input = findInput()
    const send = input && findSend(input)
    const d = s => s && { tag: s.tagName, ph: s.getAttribute('placeholder') || s.getAttribute('aria-label') || '',
      cls: (s.className || '').toString().slice(0, 80), text: (s.textContent || '').trim().slice(0, 40) }
    const box = input && composer(input)
    const all = box ? [...box.querySelectorAll('button, [role="button"]')].filter(visible).map(b => {
      const r = b.getBoundingClientRect()
      return `${label(b).slice(0, 25) || '(trống)'} @${Math.round(r.right)},${Math.round(r.bottom)} ${Math.round(r.width)}x${Math.round(r.height)}${b.disabled ? ' disabled' : ''}`
    }) : []
    return { url: location.href, input: d(input), send: d(send), buttons: all, bigImages: bigImages().length,
      blocker: blocker(), sampleImage: (bigImages()[0] || {}).src?.slice(0, 80) || null }
  }

  // Tự động kiểm tra và click Dự án mới khi tải trang nếu đang ở trang chủ flow.google.com
  function initAutoClicker () {
    if (!/\/project\//.test(location.href) && !findInput()) {
      let tries = 0
      const iv = setInterval(() => {
        tries++
        if (/\/project\//.test(location.href) || findInput() || tries > 40) {
          clearInterval(iv)
          return
        }
        const btn = findNewProjectButton()
        if (btn) {
          btn.click()
          clearInterval(iv)
        }
      }, 1000)
    }
  }
  setTimeout(initAutoClicker, 1500)

  chrome.runtime.onMessage.addListener((msg, _s, reply) => {
    if (msg.type === 'submit') { reply(submit(msg.prompt)) }
    if (msg.type === 'prepare') { reply(prepare(msg.prompt)) }
    if (msg.type === 'locateSend') { reply(locateSend()) }
    if (msg.type === 'check') { check().then(reply); return true }
    if (msg.type === 'diagnose') { reply(diagnose()) }
    if (msg.type === 'restore') { restoreOverlays(); reply({ ok: true }) }
    if (msg.type === 'started') { reply(started()) }
    if (msg.type === 'fill') { const el = findInput(); if (el) setPrompt(el, msg.prompt); reply({ ok: !!el }) }
    if (msg.type === 'ready') { reply({ ready: !!findInput() && document.readyState === 'complete' }) }
    if (msg.type === 'deleteFailed') { reply({ buttons: deleteButtons() }) }
    if (msg.type === 'confirmDelete') { reply({ button: confirmButton() }) }
    if (msg.type === 'pressSendFallback') {
      const input = findInput()
      if (input) reply({ res: pressSend(input) })
      else reply({ res: false })
    }
    if (msg.type === 'autoOpenProject') { reply(autoOpenProject()) }
    if (msg.type === 'findNewProject') { const b = findNewProjectButton(); reply({ ok: !!b, at: b ? center(b) : null, label: b ? label(b).slice(0, 30) : '' }) }
  })
})()
