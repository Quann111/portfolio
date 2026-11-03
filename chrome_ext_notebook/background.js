// AutoVideo Notebook Bridge – background: quản lý pool N slot (tab) NotebookLM chạy song song cho profile VR.
const API = 'http://127.0.0.1:8765'
const END = '===KẾT THÚC==='
const DEFAULTS = { enabled: true, minGap: 20, maxGap: 20, timeoutSec: 420, profileName: '' }

// Trạng thái cục bộ của extension
let slotBusy = new Map()      // slot -> boolean
let slotWindows = null        // windowId dành cho các tab NotebookLM
let lastPromptGlobalTime = 0  // timestamp (ms) lần cuối cùng gửi prompt (bất kể slot nào)
let staggerSec = 20           // lấy từ /nb/config
let cachedConfig = null

async function settings () {
  const stored = await chrome.storage.local.get([...Object.keys(DEFAULTS), 'workerId'])
  let profileName = stored.profileName || ''
  // KHÔNG tự động coi là VR nữa nếu để trống!
  return { ...DEFAULTS, ...stored, profileName }
}

async function setStatus (text, extra = {}) {
  await chrome.storage.local.set({ status: text, statusAt: Date.now(), ...extra })
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

// so đầu câu: chuẩn hoá Unicode + khoảng trắng
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
    await sendToTab(tabId, { type: 'clear' })
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
    const r = await sendToTab(tabId, { type: 'check', expectEnd: prompt.includes(END) ? END : '' })
    if (r.state === 'done') return { ok: true, text: r.text }
    if (r.state === 'error') return { ok: false, error: r.error, pause: r.pause }
    if (r.note === 'chưa gửi được câu hỏi' && Date.now() - t0 > 40000) {
      return { ok: false, error: 'Không bấm được nút gửi của NotebookLM' }
    }
  }
  return { ok: false, error: `Quá ${timeoutSec}s NotebookLM chưa trả lời xong` }
}

// ------------------------------------------------------------------ Quản lý Tab Pool theo Slot
async function ensureSlotTabs (cfg) {
  if (!cfg || !cfg.slots || !cfg.slots.length) return {}
  const saved = (await chrome.storage.local.get('slotsPool')).slotsPool || {}
  const activePool = {}

  // Kiểm tra window riêng cho các tab slot
  const storedWinId = (await chrome.storage.local.get('nbWindowId')).nbWindowId
  let winId = storedWinId
  if (winId) {
    const w = await chrome.windows.get(winId).catch(() => null)
    if (!w) winId = null
  }

  for (const s of cfg.slots) {
    const slotNum = s.slot
    const expectedUrl = (s.notebook_url || '').trim()
    let tabId = saved[slotNum] ? saved[slotNum].tabId : null

    // Kiểm tra tabId còn sống không
    if (tabId) {
      const t = await chrome.tabs.get(tabId).catch(() => null)
      if (!t) tabId = null
    }

    // Nếu có expectedUrl hợp lệ mà chưa có tab -> mở tab mới
    if (!tabId && expectedUrl && !expectedUrl.startsWith('https://notebooklm.google.com/notebook/TODO')) {
      if (!winId) {
        // Tạo cửa sổ mới để chứa các tab NotebookLM
        const newWin = await chrome.windows.create({ url: expectedUrl, focused: false })
        winId = newWin.id
        await chrome.storage.local.set({ nbWindowId: winId })
        const initialTab = newWin.tabs && newWin.tabs[0]
        tabId = initialTab ? initialTab.id : null
      } else {
        const newTab = await chrome.tabs.create({ windowId: winId, url: expectedUrl, active: false })
        tabId = newTab.id
      }
    }

    // Fallback: nếu slot #1 chưa có tab (do URL là TODO hoặc chưa cấu hình sổ riêng),
    // tự động tìm tab NotebookLM bất kỳ đang mở trong profile VR
    if (!tabId && slotNum === 1) {
      const existingTabs = await chrome.tabs.query({ url: ['https://notebooklm.google.com/*', 'https://notebook.google.com/*'] })
      if (existingTabs && existingTabs.length > 0) {
        tabId = existingTabs[0].id
      }
    }

    activePool[slotNum] = { tabId, url: expectedUrl }
  }

  await chrome.storage.local.set({ slotsPool: activePool })
  return activePool
}

async function handleSlot (slotNum, slotCfg, pool, s, v) {
  if (slotBusy.get(slotNum)) return
  slotBusy.set(slotNum, true)

  let task = null
  try {
    const slotInfo = pool[slotNum] || {}
    const tabId = slotInfo.tabId
    const name = encodeURIComponent(s.profileName || '')

    // Gọi API lấy task cho slot này
    const res = await api(`/nb/next?v=${v}&en=1&slot=${slotNum}&tab=${tabId ? 1 : 0}&tab_id=${tabId || 0}&name=${name}`)
    if (res.reject === 'not_vr') {
      await setStatus(`Từ chối: Profile "${s.profileName || 'chưa đặt'}" không phải VR`)
      return
    }
    if (res.paused) {
      // Slot bị pause hoặc toàn cục pause
      return
    }

    task = res.task
    if (!task) return // không có task nào cho slot này

    if (!tabId) {
      await api(`/nb/tasks/${task.id}/error`, { slot: slotNum, message: `Slot #${slotNum} chưa mở được tab NotebookLM` })
      return
    }

    // Kiểm tra giãn cách gửi câu hỏi toàn cục (nb_stagger_sec)
    const now = Date.now()
    const diff = (now - lastPromptGlobalTime) / 1000
    if (diff < staggerSec) {
      await sleep((staggerSec - diff) * 1000)
    }
    lastPromptGlobalTime = Date.now()

    await setStatus(`[Slot #${slotNum}] Task #${task.id} (job ${task.job}, ${task.step})`, { currentSlot: slotNum, currentTask: task })
    let out
    try {
      out = await ask(tabId, task.prompt, s.timeoutSec)
    } catch (e) {
      out = /debugger|Cannot access|Another debugger/i.test(e.message)
        ? { ok: false, pause: true, error: `DevTools slot #${slotNum}: ` + e.message }
        : { ok: false, error: `Lỗi slot #${slotNum}: ` + e.message }
    }

    if (!out || !out.ok) {
      const pause = !!(out && out.pause)
      await api(`/nb/tasks/${task.id}/error`, { slot: slotNum, message: (out && out.error) || 'lỗi không rõ', pause })
      return
    }

    await api(`/nb/tasks/${task.id}/result`, { slot: slotNum, text: out.text || '' })
    const n = ((await chrome.storage.local.get('doneCount')).doneCount || 0) + 1
    await chrome.storage.local.set({ doneCount: n })
    await setStatus(`✔ [Slot #${slotNum}] Xong task #${task.id} (${task.step})`)
    task = null
  } catch (e) {
    if (task) {
      try { await api(`/nb/tasks/${task.id}/error`, { slot: slotNum, message: 'extension error: ' + e.message }) } catch {}
    }
  } finally {
    slotBusy.set(slotNum, false)
  }
}

async function checkFocus () {
  try {
    const s = await settings()
    const name = encodeURIComponent(s.profileName || '')
    const r = await api(`/nb/ping?name=${name}`)
    if (r && r.action === 'close') {
      if (!r.name || r.name.toLowerCase() === (s.profileName || '').toLowerCase()) {
        const wins = await chrome.windows.getAll()
        for (const w of wins) chrome.windows.remove(w.id).catch(() => {})
        return
      }
    }
  } catch {}
}

async function tick () {
  checkFocus()
  const s = await settings()
  const v = chrome.runtime.getManifest().version

  if (!s.enabled) {
    const name = encodeURIComponent(s.profileName || '')
    try { await api(`/nb/next?v=${v}&en=0&name=${name}`) } catch {}
    await setStatus('Đang tắt – bấm "Bật tự động"')
    return
  }

  // Nếu profileName trống -> gửi báo lỗi và dừng
  if (!s.profileName) {
    const res = await api(`/nb/next?v=${v}&en=1&name=`).catch(() => ({}))
    if (res.reject === 'not_vr') {
      await setStatus('Từ chối: Chưa đặt tên profile (yêu cầu profile VR)')
    } else {
      await setStatus('Chưa đặt tên profile cho extension (bấm vào popup để lưu profile "VR")')
    }
    return
  }

  try {
    // 1. Lấy cấu hình slots từ server
    cachedConfig = await api('/nb/config')
    staggerSec = cachedConfig.stagger || 20

    // Cập nhật dữ liệu slot vào storage để popup hiển thị
    if (cachedConfig.slots) {
      await chrome.storage.local.set({ slotsData: cachedConfig.slots })
    }

    // 2. Bảo đảm các tab của các slot mở đúng URL
    const pool = await ensureSlotTabs(cachedConfig)

    // 3. Duyệt song song qua tất cả các slot có sẵn
    const slotPromises = []
    for (const slotCfg of cachedConfig.slots || []) {
      const slotNum = slotCfg.slot
      // Nếu slot chưa bận, dispatch thực hiện task
      if (!slotBusy.get(slotNum)) {
        slotPromises.push(handleSlot(slotNum, slotCfg, pool, s, v))
      }
    }
    await Promise.all(slotPromises)
  } catch (e) {
    await setStatus('Không kết nối được AutoVideo (127.0.0.1:8765): ' + e.message)
  }
}

chrome.alarms.create('poll', { periodInMinutes: 0.15 })
chrome.alarms.onAlarm.addListener(a => { if (a.name === 'poll') tick() })
chrome.runtime.onMessage.addListener((msg, _s, reply) => {
  if (msg.type === 'kick') { tick(); reply({ ok: true }) }
})
