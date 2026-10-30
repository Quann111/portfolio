// AutoVideo Flow Bridge – background: lấy task từ app, giao cho tab flow.google.com, gửi ảnh về app.
const API = 'http://127.0.0.1:8765'
const DEFAULTS = { enabled: false, minGap: 8, maxGap: 20, timeoutSec: 240, profileName: '' }

// mỗi Chrome profile có storage riêng → id riêng; app chia ảnh cho mọi profile đang bật và gom về đúng job
async function workerId () {
  let { workerId } = await chrome.storage.local.get('workerId')
  if (!workerId) {
    workerId = 'p' + Math.random().toString(36).slice(2, 10)
    await chrome.storage.local.set({ workerId })
  }
  return workerId
}
let busy = false

async function settings () {
  return { ...DEFAULTS, ...(await chrome.storage.local.get(Object.keys(DEFAULTS))) }
}

async function setStatus (text, extra = {}) {
  await chrome.storage.local.set({ status: text, statusAt: Date.now(), ...extra })
}

async function flowTab () {
  const tabs = await chrome.tabs.query({ url: 'https://flow.google.com/*' })
  // ưu tiên tab đang ở một project (có ô nhập prompt)
  return tabs.find(t => /\/project\//.test(t.url)) || tabs[0] || null
}

async function api (path, body) {
  const r = await fetch(API + path, body
    ? { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(body) }
    : {})
  if (!r.ok) throw new Error(`${path} → HTTP ${r.status}`)
  return r.json()
}

async function fetchAsDataUrl (url) {
  const r = await fetch(url, { credentials: 'include' })
  if (!r.ok) throw new Error('tải ảnh HTTP ' + r.status)
  const blob = await r.blob()
  const buf = new Uint8Array(await blob.arrayBuffer())
  let bin = ''
  for (let i = 0; i < buf.length; i += 0x8000) bin += String.fromCharCode.apply(null, buf.subarray(i, i + 0x8000))
  return { data: `data:${blob.type};base64,` + btoa(bin), mime: blob.type || 'image/png' }
}

const sleep = ms => new Promise(r => setTimeout(r, ms))
// chờ lâu: chia nhỏ + ping app → service worker không bị Chrome tắt, dashboard không báo mất kết nối
async function nap (ms) {
  for (const end = Date.now() + ms; Date.now() < end;) { await sleep(Math.min(5000, end - Date.now())); checkFocus() }
}

// Tab Flow mở trước khi cài/cập nhật extension sẽ chưa có content script → tự nạp vào
async function sendToTab (tabId, msg) {
  try {
    return await chrome.tabs.sendMessage(tabId, msg)
  } catch (e) {
    if (!/Receiving end does not exist|Could not establish connection/i.test(e.message)) throw e
    await chrome.scripting.executeScript({ target: { tabId }, files: ['content.js'] })
    await sleep(500)
    return await chrome.tabs.sendMessage(tabId, msg)
  }
}

// ---- Click/gõ phím THẬT qua Chrome DevTools Protocol (Flow bỏ qua sự kiện giả của script) ----
const attached = new Set()
async function cdp (tabId, method, params = {}) {
  if (!attached.has(tabId)) {
    try { await chrome.debugger.attach({ tabId }, '1.3') } catch (e) {
      if (!/already attached/i.test(e.message)) throw e
    }
    attached.add(tabId)
  }
  return chrome.debugger.sendCommand({ tabId }, method, params)
}
chrome.debugger.onDetach.addListener(src => attached.delete(src.tabId))

async function click (tabId, { x, y }) {
  for (const type of ['mouseMoved', 'mousePressed', 'mouseReleased']) {
    await cdp(tabId, 'Input.dispatchMouseEvent', { type, x, y, button: 'left', clickCount: 1 })
    await sleep(40 + Math.random() * 60)
  }
}

async function key (tabId, keyName, code, vk, modifiers = 0) {
  await cdp(tabId, 'Input.dispatchKeyEvent', { type: 'rawKeyDown', key: keyName, code, windowsVirtualKeyCode: vk, modifiers })
  await cdp(tabId, 'Input.dispatchKeyEvent', { type: 'keyUp', key: keyName, code, windowsVirtualKeyCode: vk, modifiers })
}

// Flow để x2/x3/x4 ảnh mỗi prompt (cài đặt lưu theo tài khoản – mở tab mới vẫn giữ) → tự bấm nút xN rồi chọn x1
async function fixMulti (tabId) {
  let seen = []
  for (let n = 0; n < 3; n++) {
    const m = await sendToTab(tabId, { type: 'multiButton' }).catch(() => null)
    if (!m || !m.at) return true
    await setStatus(`🔧 Flow đang để ${m.label} ảnh/prompt – tự chuyển về x1 (lần ${n + 1}/3)…`)
    await click(tabId, m.at)
    await sleep(900)
    const o = await sendToTab(tabId, { type: 'x1Option' }).catch(() => null)
    seen = (o && o.seen) || seen
    if (o && o.at) {
      await click(tabId, o.at)
      await sleep(700)
    }
    await key(tabId, 'Escape', 'Escape', 27)      // đóng menu nếu còn mở
    await sleep(600)
  }
  const m = await sendToTab(tabId, { type: 'multiButton' }).catch(() => null)
  if (m && m.at) {
    lastMultiSeen = seen
    return false
  }
  return true
}
let lastMultiSeen = []

async function typeAndSend (tabId, prompt) {
  let prep = await sendToTab(tabId, { type: 'prepare', prompt })
  if (prep && prep.multi) {
    if (await fixMulti(tabId)) prep = await sendToTab(tabId, { type: 'prepare', prompt })
    else if (prep.error) prep.error += ` (tự chuyển x1 không được – menu thấy: ${lastMultiSeen.join(' | ') || 'không mở được menu'})`
  }
  // Flow còn ảnh tạo dở (x2 / prompt trước quá giờ) → chờ tối đa 3 phút cho xong, tránh gán ảnh nhầm cảnh
  for (let i = 0; i < 72 && prep && prep.busy; i++) {
    await setStatus('Chờ Flow tạo xong ảnh dở trước khi gửi prompt mới…')
    await sleep(2500)
    prep = await sendToTab(tabId, { type: 'prepare', prompt })
  }
  if (!prep || !prep.ok) return prep || { ok: false, error: 'không chuẩn bị được tab Flow' }
  // gõ hụt (thường gặp ngay sau F5: trang chưa dựng xong) → lấy lại toạ độ và gõ lại; lần cuối điền bằng script
  let loc
  for (let n = 0; n < 4; n++) {
    if (n) {
      await setStatus(`Gõ prompt chưa vào ô – thử lại (${n}/3)…`)
      await nap(3000)
      if (n < 3) {
        const p2 = await sendToTab(tabId, { type: 'prepare', prompt })
        if (!p2 || !p2.ok) return p2 || { ok: false, error: 'không chuẩn bị được tab Flow' }
        prep = p2
      }
    }
    if (n < 3) {
      await click(tabId, prep.input)
      if (n) { await sleep(200); await click(tabId, prep.input) }
      await sleep(300)
      await key(tabId, 'a', 'KeyA', 65, 2)              // Ctrl+A
      await key(tabId, 'Backspace', 'Backspace', 8)     // xoá prompt cũ còn sót
      await sleep(200)
      await cdp(tabId, 'Input.insertText', { text: prompt })
    } else {
      await sendToTab(tabId, { type: 'fill', prompt })
    }
    await sleep(900 + Math.random() * 600)
    loc = await sendToTab(tabId, { type: 'locateSend' })
    if (loc.typed && loc.typed.slice(0, 20) === prompt.slice(0, 20)) break
  }
  if (!loc.typed || loc.typed.slice(0, 20) !== prompt.slice(0, 20)) {
    return { ok: false, error: `Gõ prompt vào ô không thành công (ô đang có: "${loc.typed || ''}")` }
  }
  if (loc.ok && !loc.disabled) {
    await click(tabId, loc.send)
  } else {
    await key(tabId, 'Enter', 'Enter', 13)
    await sleep(500)
    await sendToTab(tabId, { type: 'pressSendFallback' }).catch(() => {})
  }
  // Flow bản mới GIỮ chữ prompt trong ô sau khi gửi → không dùng "chữ còn trong ô" để đoán chưa gửi (gây gửi 2 lần).
  // Đã gửi = Flow bắt đầu tạo (có ô "%" mới / ảnh mới). Chờ tối đa 8s; vẫn không bắt đầu thì mới bấm Enter 1 lần.
  for (let i = 0; i < 8; i++) {
    await sleep(1000)
    const st = await sendToTab(tabId, { type: 'started' })
    if (st && st.started) return { ok: true, sendLabel: loc.sendLabel }
  }
  await key(tabId, 'Enter', 'Enter', 13)
  return { ok: true, sendLabel: loc.sendLabel, retried: true }
}

// Flow trả thẻ "Không thành công – hoạt động bất thường" → xoá thẻ lỗi, F5, gửi lại (tối đa 3 lần rồi mới tạm dừng)
async function recoverUnusual (tabId) {
  const del = await sendToTab(tabId, { type: 'deleteFailed' }).catch(() => null)
  for (const b of (del && del.buttons) || []) {
    await click(tabId, b)
    await sleep(1200)
    const c = await sendToTab(tabId, { type: 'confirmDelete' }).catch(() => null)
    if (c && c.button) { await click(tabId, c.button); await sleep(1200) }
  }
  await chrome.tabs.reload(tabId)
  attached.delete(tabId)                       // tải lại có thể gỡ DevTools → gắn lại ở lần click sau
  for (let i = 0; i < 60; i++) {
    await nap(1000)
    const t = await chrome.tabs.get(tabId).catch(() => null)
    if (t && t.status === 'complete') break
  }
  for (let i = 0; i < 30; i++) {               // chờ tới khi trang Flow dựng xong ô nhập prompt
    const r = await sendToTab(tabId, { type: 'ready' }).catch(() => null)
    if (r && r.ready) break
    await nap(1000)
  }
  await nap(3000)
  return ((del && del.buttons) || []).length
}

const TASK_MAX_MS = 9 * 60 * 1000   // app tự giao lại task sau 10 phút → dừng trước đó
async function generate (tabId, prompt, timeoutSec) {
  const t0 = Date.now()
  for (let n = 1; ; n++) {
    const left = Math.floor((TASK_MAX_MS - (Date.now() - t0)) / 1000)
    if (left < 60) return { ok: false, error: 'Quá 9 phút cho một ảnh (đã gửi lại nhiều lần) – trả task về hàng đợi' }
    const out = await generateOnce(tabId, prompt, Math.min(timeoutSec, left))
    if (!out || out.ok || !out.retry) return out
    if (n > 3) return { ok: false, pause: true, error: out.error + ' – đã tự xoá/tải lại 3 lần vẫn lỗi, xử lý trên tab rồi bấm Tiếp tục' }
    const k = await recoverUnusual(tabId).catch(() => 0)
    const wait = 20 + Math.round(Math.random() * 20)
    await setStatus(`Flow báo hoạt động bất thường – đã xoá ${k} ảnh lỗi, tải lại trang, gửi lại sau ${wait}s (lần ${n}/3)`)
    await nap(wait * 1000)
  }
}

// Vòng chờ chạy ở service worker: tab Flow có thể nằm nền (bạn làm việc ở tab khác) mà không bị chậm.
async function generateOnce (tabId, prompt, timeoutSec) {
  try {
    const sub = await typeAndSend(tabId, prompt)
    if (!sub || !sub.ok) {
      await autoRepair(tabId).catch(() => {})
      return sub || { ok: false, retry: true, error: 'Không gửi được prompt - đã tự động mở lại tab Flow để thử lại' }
    }
    const t0 = Date.now()
    while (Date.now() - t0 < timeoutSec * 1000) {
      await sleep(2500)
      checkFocus()                                 // vẫn nhận yêu cầu "đưa lên trước" khi đang chờ ảnh
      const r = await sendToTab(tabId, { type: 'check' })
      if (r.state === 'done') return { ok: true, dataUrl: r.dataUrl, mime: r.mime, url: r.url, rect: r.rect, tabId }
      if (r.state === 'error') return { ok: false, error: r.error, pause: r.pause, retry: r.retry }
      await setStatus(`Đang chờ ảnh… ${Math.round((Date.now() - t0) / 1000)}s – ${r.note || ''}`)
      if (r.note && r.note.startsWith('chưa gửi') && Date.now() - t0 > 30000) {
        // Tự động reload/sửa lỗi tab và thử lại thay vì đứng chờ người dùng bấm
        await autoRepair(tabId).catch(() => {})
        return { ok: false, retry: true, error: 'Nút gửi Flow phản hồi chậm – đã tự động sửa lỗi và thử lại' }
      }
    }
    return { ok: false, error: `Quá ${timeoutSec}s chưa thấy ảnh mới` }
  } catch (e) {
    if (/debugger|Cannot access|Another debugger/i.test(e.message)) {
      // Tự động gỡ đính kèm cũ và thử sửa tab, không đặt pause cứng
      attached.delete(tabId)
      await autoRepair(tabId).catch(() => {})
      return { ok: false, retry: true, error: 'DevTools tab bị ngắt kết nối – đã tự nạp lại trang để thử lại' }
    }
    return { ok: false, error: 'không liên lạc được với tab Flow (tab bị đóng hoặc đang tải lại): ' + e.message }
  }
}


// Tự sửa lỗi tab Flow: Đóng tab bị lỗi và mở lại tab mới tinh như lúc khởi động ban đầu
async function autoRepair (tabId) {
  try {
    attached.delete(tabId)
    let windowId = null
    try {
      const oldTab = await chrome.tabs.get(tabId).catch(() => null)
      if (oldTab) windowId = oldTab.windowId
    } catch {}

    // 1. Tạo tab mới mở https://flow.google.com
    const createProps = { url: 'https://flow.google.com', active: true }
    if (windowId) createProps.windowId = windowId
    const newTab = await chrome.tabs.create(createProps)

    // 2. Đóng tab cũ bị lỗi
    try {
      if (tabId && tabId !== newTab.id) {
        await chrome.tabs.remove(tabId).catch(() => {})
      }
    } catch {}

    // 3. Chờ tab mới tải xong
    for (let i = 0; i < 40; i++) {
      await sleep(1000)
      const t = await chrome.tabs.get(newTab.id).catch(() => null)
      if (t && t.status === 'complete') break
    }
    await sleep(2500)

    // 4. Nếu tab đang ở trang chủ, tự động click nút "Dự án mới"
    await ensureProjectOpen(newTab.id)
    for (let i = 0; i < 25; i++) {
      const r = await sendToTab(newTab.id, { type: 'ready' }).catch(() => null)
      if (r && r.ready) break
      await sleep(1000)
    }
    await fixMulti(newTab.id)                       // tab mới vẫn giữ x2 của tài khoản → chuyển x1 luôn
    return { ok: true, msg: 'Đã đóng tab lỗi và mở tab Flow mới thành công' }
  } catch (e) {
    return { ok: false, error: e.message }
  }
}

async function ensureProjectOpen (tabId) {
  try {
    const tab = await chrome.tabs.get(tabId).catch(() => null)
    if (!tab) return false
    // Nếu tab bị 404 thì chuyển hướng về trang chủ flow.google.com
    if (/\/404/.test(tab.url)) {
      await chrome.tabs.update(tabId, { url: 'https://flow.google.com' })
      await sleep(3000)
      return false
    }
    // Nếu URL chưa có /project/, trang đang ở trang chủ danh sách dự án
    if (!/\/project\//.test(tab.url)) {
      // bấm "Dự án mới" bằng click thật, chờ tới 15s cho URL sang /project/ (thử 2 lần)
      for (let n = 0; n < 2; n++) {
        const np = await sendToTab(tabId, { type: 'findNewProject' }).catch(() => null)
        if (!(np && np.ok && np.at)) break
        await setStatus(`🔧 Tự bấm "${np.label || 'Dự án mới'}" trên Flow (lần ${n + 1}/2)…`)
        await sleep(400)
        const np2 = await sendToTab(tabId, { type: 'findNewProject' }).catch(() => null)   // toạ độ sau khi cuộn
        await click(tabId, (np2 && np2.at) || np.at)
        for (let i = 0; i < 15; i++) {
          await sleep(1000)
          const t = await chrome.tabs.get(tabId).catch(() => null)
          if (t && /\/project\//.test(t.url)) { await sleep(2500); return true }
        }
      }
      await sendToTab(tabId, { type: 'autoOpenProject' }).catch(() => null)
      await sleep(1500)
    }
  } catch {}
  return false
}

// Dashboard bấm tên profile này → đưa tab Flow lên trước + kéo cửa sổ Chrome của profile lên trên cùng
async function checkFocus () {
  try {
    const r = await api(`/flow/ping?w=${await workerId()}`)
    if (r.action === 'repair') {
      const tab = await flowTab()
      if (tab) {
        await setStatus('🔧 Đang tự động sửa lỗi profile: tải lại tab Flow và kết nối lại…')
        await autoRepair(tab.id)
        const diag = await sendToTab(tab.id, { type: 'diagnose' }).catch(() => null)
        try { await api('/flow/diag', { worker: await workerId(), diag }) } catch {}

        // Kiểm tra sau khi nạp lại tab: nếu có captcha hoặc cảnh báo chặn
        if (diag && diag.blocker) {
          const pauseMsg = diag.blocker.kind === 'captcha'
            ? 'Flow hiện xác minh captcha - xử lý trên tab rồi bấm Tiếp tục profile này'
            : (diag.blocker.text || 'Tạm dừng: cần xác minh trên tab rồi bấm Tiếp tục profile này')
          try {
            await api('/flow/pause', { w: await workerId(), reason: pauseMsg })
          } catch {}
          await setStatus('⏸ ' + pauseMsg)
          return
        }

        await setStatus('✔ Đã tự sửa lỗi và làm mới kết nối tab Flow')
        setTimeout(tick, 1000)
      }
    }
    if (r.focus) {
      const tab = await flowTab()
      if (tab) {
        await chrome.tabs.update(tab.id, { active: true })
        await chrome.windows.update(tab.windowId, { focused: true, drawAttention: true, state: 'normal' })
        await setStatus('👀 Đã đưa tab Flow của profile này lên trước (yêu cầu từ dashboard)')
      }
    }
  } catch {}
}

async function tick () {
  checkFocus()
  if (busy) return
  const s = await settings()
  if (!s.enabled) return
  busy = true
  let task = null
  try {
    const tab = await flowTab()
    const w = await workerId()
    const name = encodeURIComponent(s.profileName || '')
    const res = await api(`/flow/next?v=${chrome.runtime.getManifest().version}&tab=${tab ? 1 : 0}&w=${w}&name=${name}`)
    await chrome.storage.local.set({ profiles: res.profiles || 0 })
    if (res.paused) { await setStatus('Tạm dừng (profile này): ' + res.paused); return }
    if (!tab) { await setStatus('Chưa mở tab flow.google.com (mở một project và giữ tab đó)'); return }
    // Tự kiểm tra nếu tab đang ở trang chủ flow.google.com chưa vào project -> tự click vào Dự án mới
    if (!/\/project\//.test(tab.url)) {
      await ensureProjectOpen(tab.id)
    }
    task = res.task
    if (!task) { await setStatus('Đang chờ prompt từ AutoVideo…'); return }
    await setStatus(`Đang tạo ảnh task #${task.id} (job ${task.job}, cảnh ${task.scene})`, { current: task })
    const out = await generate(tab.id, task.prompt, s.timeoutSec)
    if (!out || !out.ok) {
      const pause = !!(out && out.pause)
      await api(`/flow/tasks/${task.id}/error`, { message: (out && out.error) || 'lỗi không rõ', pause, w: await workerId() })
      await setStatus((pause ? '⏸ Tạm dừng – ' : '✖ ') + ((out && out.error) || 'lỗi'), { current: null })
      return
    }
    let img = null
    let why = []
    if (out.dataUrl) img = { data: out.dataUrl, mime: out.mime }
    if (!img && out.url) {
      try { img = await fetchAsDataUrl(out.url) } catch (e) { why.push('tải trực tiếp: ' + e.message) }
    }
    if (!img && out.rect) {
      // chụp đúng vùng ảnh qua DevTools, phóng về độ phân giải gốc của ảnh
      try {
        const scale = Math.max(1, Math.min(4, (out.rect.naturalWidth || out.rect.width) / out.rect.width))
        await sleep(500)
        const shot = await cdp(out.tabId, 'Page.captureScreenshot', {
          format: 'png', captureBeyondViewport: false,   // toạ độ rect là theo khung nhìn (đã cuộn ảnh vào giữa)
          clip: { x: out.rect.x, y: out.rect.y, width: out.rect.width, height: out.rect.height, scale }
        })
        img = { data: 'data:image/png;base64,' + shot.data, mime: 'image/png' }
      } catch (e) { why.push('chụp vùng ảnh: ' + e.message) }
    }
    // luôn hiện lại khung prompt đã ẩn tạm (kể cả khi lấy được ảnh bằng cách tải trực tiếp, không cần chụp)
    try { await sendToTab(out.tabId, { type: 'restore' }) } catch {}
    if (!img) throw new Error('đã có ảnh trên Flow nhưng không lấy về được – ' + why.join(' | '))
    await api(`/flow/tasks/${task.id}/result`, { ...img, w: await workerId() })
    const n = ((await chrome.storage.local.get('doneCount')).doneCount || 0) + 1
    await setStatus(`✔ Đã gửi ảnh task #${task.id} về AutoVideo`, { current: null, doneCount: n })
    task = null
    // giãn cách như người dùng, không dồn dập
    await sleep(1000 * (s.minGap + Math.random() * Math.max(0, s.maxGap - s.minGap)))
  } catch (e) {
    if (task) {
      // đã nhận task mà hỏng giữa chừng → luôn báo lỗi về app (không để task treo im lặng)
      try { await api(`/flow/tasks/${task.id}/error`, { message: 'extension: ' + e.message, w: await workerId() }) } catch {}
      await setStatus('✖ ' + e.message, { current: null })
    } else {
      await setStatus('Không kết nối được AutoVideo (127.0.0.1:8765) – mở app AutoVideo: ' + e.message)
    }
  } finally {
    busy = false
  }
}

chrome.alarms.create('poll', { periodInMinutes: 0.25 })
chrome.alarms.onAlarm.addListener(a => { if (a.name === 'poll') tick() })
chrome.runtime.onMessage.addListener((msg, _s, reply) => {
  if (msg.type === 'kick') { tick(); reply({ ok: true }) }
  if (msg.type === 'diagnose') {
    flowTab().then(async tab => {
      if (!tab) return reply({ ok: false, error: 'Chưa mở tab flow.google.com' })
      try {
        const d = await sendToTab(tab.id, { type: 'diagnose' })
        try { await api('/flow/diag', d) } catch {}
        reply({ ok: true, diag: d })
      } catch (e) { reply({ ok: false, error: 'Tải lại tab Flow (F5): ' + e.message }) }
    })
    return true
  }
})
