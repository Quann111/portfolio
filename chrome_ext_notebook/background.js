// AutoVideo Notebook Bridge – background: lấy yêu cầu từ app, hỏi NotebookLM trên tab đã đăng nhập, gửi câu trả lời về.
const API = 'http://127.0.0.1:8765'
const END = '===KẾT THÚC==='
const DEFAULTS = { enabled: false, minGap: 3, maxGap: 8, timeoutSec: 420 }
let busy = false

async function settings () {
  return { ...DEFAULTS, ...(await chrome.storage.local.get(Object.keys(DEFAULTS))) }
}

async function setStatus (text, extra = {}) {
  await chrome.storage.local.set({ status: text, statusAt: Date.now(), ...extra })
}

async function nbTab () {
  const tabs = await chrome.tabs.query({ url: ['https://notebooklm.google.com/*', 'https://notebook.google.com/*'] })
  return tabs.find(t => /\/notebook\//.test(t.url)) || tabs[0] || null
}

async function api (path, body) {
  const r = await fetch(API + path, body
    ? { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(body) }
    : {})
  if (!r.ok) throw new Error(`${path} → HTTP ${r.status}`)
  return r.json()
}

const sleep = ms => new Promise(r => setTimeout(r, ms))

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

// ---- Click/gõ phím THẬT qua Chrome DevTools Protocol ----
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

// so đầu câu: chuẩn hoá Unicode + khoảng trắng (tiếng Việt dựng sẵn/tổ hợp khác nhau vẫn là một)
const head = s => (s || '').normalize('NFC').replace(/\s+/g, ' ').trim().slice(0, 20)
const same = (a, b) => !!a && head(a) === head(b)

async function typeAndSend (tabId, prompt) {
  const prep = await sendToTab(tabId, { type: 'prepare', prompt })
  if (!prep || !prep.ok) return prep || { ok: false, error: 'không chuẩn bị được tab NotebookLM' }
  let loc
  for (let attempt = 0; attempt < 2; attempt++) {
    await sendToTab(tabId, { type: 'clear' })
    await click(tabId, prep.input)
    await sleep(300)
    await key(tabId, 'a', 'KeyA', 65, 2)
    await key(tabId, 'Backspace', 'Backspace', 8)
    await sleep(200)
    await cdp(tabId, 'Input.insertText', { text: prompt })
    await sleep(1000 + Math.random() * 600)
    loc = await sendToTab(tabId, { type: 'locateSend' })
    if (same(loc.typed, prompt)) break
  }
  if (!same(loc.typed, prompt)) {
    await sendToTab(tabId, { type: 'clear' })   // không để chữ kẹt lại cho câu hỏi sau
    return { ok: false, error: `Gõ câu hỏi vào ô không thành công (ô đang có: "${loc.typed || ''}")` }
  }
  if (loc.ok && !loc.disabled) await click(tabId, loc.send)
  else await key(tabId, 'Enter', 'Enter', 13)
  await sleep(2500)
  const after = await sendToTab(tabId, { type: 'locateSend' })
  if (same(after.typed, prompt)) await key(tabId, 'Enter', 'Enter', 13)
  return { ok: true }
}

async function ask (tabId, prompt, timeoutSec) {
  const sub = await typeAndSend(tabId, prompt)
  if (!sub || !sub.ok) return sub || { ok: false, error: 'không gửi được câu hỏi' }
  const t0 = Date.now()
  while (Date.now() - t0 < timeoutSec * 1000) {
    await sleep(2500)
    checkFocus()
    const r = await sendToTab(tabId, { type: 'check', expectEnd: prompt.includes(END) ? END : '' })
    if (r.state === 'done') return { ok: true, text: r.text }
    if (r.state === 'error') return { ok: false, error: r.error, pause: r.pause }
    await setStatus(`Đang chờ NotebookLM… ${Math.round((Date.now() - t0) / 1000)}s – ${r.note || ''}`)
    if (r.note === 'chưa gửi được câu hỏi' && Date.now() - t0 > 40000) {
      return { ok: false, error: 'Không bấm được nút gửi của NotebookLM – bấm "Kiểm tra trang" và gửi kết quả cho AutoVideo' }
    }
  }
  return { ok: false, error: `Quá ${timeoutSec}s NotebookLM chưa trả lời xong` }
}

// tạo sổ ghi chú mới: bấm nút "Tạo sổ ghi chú", chờ URL đổi sang sổ mới, đóng hộp thoại thêm nguồn
async function newNotebook (tab) {
  const b = await sendToTab(tab.id, { type: 'newNotebook' })
  if (!b || !b.ok) return { ok: false, error: 'không thấy nút "Tạo sổ ghi chú"' }
  const before = tab.url
  await click(tab.id, b.at)
  for (let i = 0; i < 30; i++) {
    await sleep(1000)
    const t = await chrome.tabs.get(tab.id)
    if (t.url !== before && /\/notebook\//.test(t.url) && t.status === 'complete') {
      await sleep(2500)
      try { await key(tab.id, 'Escape', 'Escape', 27) } catch {}
      await sleep(800)
      return { ok: true, text: t.url }
    }
  }
  return { ok: false, error: 'bấm tạo sổ mới nhưng trang không chuyển' }
}


async function autoRepairNb (tabId) {
  try {
    await chrome.tabs.reload(tabId)
    attached.delete(tabId)
    for (let i = 0; i < 40; i++) {
      await sleep(1000)
      const t = await chrome.tabs.get(tabId).catch(() => null)
      if (t && t.status === 'complete') break
    }
    await sleep(2500)
    return { ok: true, msg: 'Đã tải lại trang NotebookLM' }
  } catch (e) {
    return { ok: false, error: e.message }
  }
}

// Dashboard bấm dòng NotebookLM → đưa tab NotebookLM lên trước + kéo cửa sổ Chrome lên trên cùng
async function checkFocus () {
  try {
    const r = await api('/nb/ping')
    if (r.action === 'repair') {
      const tab = await nbTab()
      if (tab) {
        await setStatus('🔧 Đang tự động sửa lỗi tab NotebookLM: tải lại trang…')
        await autoRepairNb(tab.id)
        const diag = await sendToTab(tab.id, { type: 'diagnose' }).catch(() => null)
        try { await api('/nb/diag', { diag }) } catch {}
        await setStatus('✔ Đã tự sửa lỗi tab NotebookLM')
        setTimeout(tick, 1000)
      }
    }
    if (r.focus) {
      const tab = await nbTab()
      if (tab) {
        await chrome.tabs.update(tab.id, { active: true })
        await chrome.windows.update(tab.windowId, { focused: true, drawAttention: true, state: 'normal' })
      }
    }
  } catch {}
}

async function tick () {
  checkFocus()
  if (busy) return
  const s = await settings()
  const v = chrome.runtime.getManifest().version
  if (!s.enabled) {
    // vẫn báo cho app biết extension có mặt nhưng đang TẮT (app sẽ không chờ)
    try { await api(`/nb/next?v=${v}&en=0`) } catch {}
    await setStatus('Đang tắt – bấm "Bật tự động"')
    return
  }
  busy = true
  let task = null
  try {
    const tab = await nbTab()
    const res = await api(`/nb/next?v=${v}&en=1&tab=${tab ? 1 : 0}`)
    if (res.paused) { await setStatus('Tạm dừng: ' + res.paused); return }
    if (!tab) { await setStatus('Chưa mở tab NotebookLM (mở một sổ ghi chú và giữ tab đó)'); return }
    task = res.task
    if (!task) { await setStatus('Đang chờ yêu cầu từ AutoVideo…'); return }
    await setStatus(`Task #${task.id} (job ${task.job}, ${task.step})`, { current: task })
    let out
    try {
      out = task.kind === 'new' ? await newNotebook(tab) : await ask(tab.id, task.prompt, s.timeoutSec)
    } catch (e) {
      out = /debugger|Cannot access|Another debugger/i.test(e.message)
        ? { ok: false, pause: true, error: 'Không gắn được DevTools vào tab NotebookLM (đóng F12, không bấm Huỷ thanh "đang gỡ lỗi"): ' + e.message }
        : { ok: false, error: 'không liên lạc được với tab NotebookLM: ' + e.message }
    }
    if (!out || !out.ok) {
      const pause = !!(out && out.pause)
      await api(`/nb/tasks/${task.id}/error`, { message: (out && out.error) || 'lỗi không rõ', pause })
      await setStatus((pause ? '⏸ Tạm dừng – ' : '✖ ') + ((out && out.error) || 'lỗi'), { current: null })
      return
    }
    await api(`/nb/tasks/${task.id}/result`, { text: out.text || '' })
    const n = ((await chrome.storage.local.get('doneCount')).doneCount || 0) + 1
    await setStatus(`✔ Đã gửi task #${task.id} (${task.step}) về AutoVideo`, { current: null, doneCount: n })
    task = null
    await sleep(1000 * (s.minGap + Math.random() * Math.max(0, s.maxGap - s.minGap)))
    busy = false
    tick()   // còn task thì làm luôn, không đợi nhịp 15s
    return
  } catch (e) {
    if (task) {
      try { await api(`/nb/tasks/${task.id}/error`, { message: 'extension: ' + e.message }) } catch {}
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
    nbTab().then(async tab => {
      if (!tab) return reply({ ok: false, error: 'Chưa mở tab NotebookLM' })
      try {
        const d = await sendToTab(tab.id, { type: 'diagnose' })
        try { await api('/nb/diag', d) } catch {}
        reply({ ok: true, diag: d })
      } catch (e) { reply({ ok: false, error: 'Tải lại tab NotebookLM (F5): ' + e.message }) }
    })
    return true
  }
})
