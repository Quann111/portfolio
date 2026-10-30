// AutoVideo Gemini Bridge – background: nhận task từ AutoVideo (127.0.0.1:8765), gửi sang tab gemini.google.com, lấy kết quả về.
const API = 'http://127.0.0.1:8765'
const END = '===KẾT THÚC==='
const DEFAULTS = { enabled: true, minGap: 2, maxGap: 5, timeoutSec: 300 }
let busy = false

async function settings () {
  return { ...DEFAULTS, ...(await chrome.storage.local.get(Object.keys(DEFAULTS))) }
}

async function setStatus (text, extra = {}) {
  await chrome.storage.local.set({ status: text, statusAt: Date.now(), ...extra })
}

async function geminiTab () {
  const tabs = await chrome.tabs.query({ url: 'https://gemini.google.com/*' })
  return tabs.find(t => /\/app/.test(t.url)) || tabs[0] || null
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

// ---- Click / gõ phím THẬT qua Chrome DevTools Protocol ----
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

async function typeAndSend (tabId, prompt) {
  const prep = await sendToTab(tabId, { type: 'prepare', prompt })
  if (!prep || !prep.ok) return prep || { ok: false, error: 'không chuẩn bị được tab Gemini' }

  await sendToTab(tabId, { type: 'clear' })
  await click(tabId, prep.input)
  await sleep(200)

  // Điền văn bản vào ô prompt
  await cdp(tabId, 'Input.insertText', { text: prompt })
  await sleep(500)

  const loc = await sendToTab(tabId, { type: 'locateSend' })
  if (loc && loc.ok && !loc.disabled && loc.send) {
    await click(tabId, loc.send)
  } else {
    await key(tabId, 'Enter', 'Enter', 13)
  }

  return { ok: true }
}

async function ask (tabId, prompt, timeoutSec) {
  const t0 = Date.now()
  const sub = await typeAndSend(tabId, prompt)
  if (!sub || !sub.ok) return sub

  while (Date.now() - t0 < timeoutSec * 1000) {
    await sleep(2500)
    checkFocus()
    const r = await sendToTab(tabId, { type: 'check', expectEnd: END })
    if (r.state === 'done') return { ok: true, text: r.text }
    if (r.state === 'error') return { ok: false, error: r.error, pause: r.pause }
    await setStatus(`Đang chờ Gemini… ${Math.round((Date.now() - t0) / 1000)}s – ${r.note || ''}`)
  }
  return { ok: false, error: `Quá ${timeoutSec}s Gemini chưa trả lời xong` }
}

async function autoRepairGemini (tabId) {
  try {
    await chrome.tabs.reload(tabId)
    attached.delete(tabId)
    for (let i = 0; i < 40; i++) {
      await sleep(1000)
      const t = await chrome.tabs.get(tabId).catch(() => null)
      if (t && t.status === 'complete') break
    }
    await sleep(2500)
    return { ok: true, msg: 'Đã tải lại trang Gemini' }
  } catch (e) {
    return { ok: false, error: e.message }
  }
}

async function checkFocus () {
  try {
    const r = await api('/gemini/ping')
    if (r && r.action === 'repair') {
      const tab = await geminiTab()
      if (tab) {
        await setStatus('🔧 Đang tự động sửa lỗi tab Gemini: tải lại trang…')
        await autoRepairGemini(tab.id)
        await setStatus('✔ Đã tự sửa lỗi tab Gemini')
        setTimeout(tick, 1000)
      }
    }
    if (r && r.focus) {
      const tab = await geminiTab()
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
    try { await api(`/gemini/next?v=${v}&en=0`) } catch {}
    await setStatus('Đang tắt – bấm "Bật tự động"')
    return
  }
  busy = true
  let task = null
  try {
    const tab = await geminiTab()
    const res = await api(`/gemini/next?v=${v}&en=1&tab=${tab ? 1 : 0}`)
    if (res.paused) { await setStatus('Tạm dừng: ' + res.paused); return }
    if (!tab) { await setStatus('Chưa mở tab gemini.google.com (mở tab và đăng nhập)'); return }
    task = res.task
    if (!task) { await setStatus('Sẵn sàng – chờ yêu cầu từ AutoVideo…'); return }
    await setStatus(`Task #${task.id} (job ${task.job}, ${task.step})`, { current: task })

    const out = await ask(tab.id, task.prompt, s.timeoutSec)
    if (!out || !out.ok) {
      await api(`/gemini/tasks/${task.id}/error`, { message: (out && out.error) || 'lỗi', pause: !!(out && out.pause) })
      await setStatus('✖ ' + ((out && out.error) || 'lỗi'), { current: null })
      return
    }
    await api(`/gemini/tasks/${task.id}/result`, { text: out.text })
    await setStatus(`✔ Đã xong task #${task.id}`, { current: null })
    task = null
    await sleep(2000)
  } catch (e) {
    if (task) {
      try { await api(`/gemini/tasks/${task.id}/error`, { message: e.message }) } catch {}
    }
  } finally {
    busy = false
  }
}

chrome.alarms.create('gemini_poll', { periodInMinutes: 0.2 })
chrome.alarms.onAlarm.addListener(a => { if (a.name === 'gemini_poll') tick() })
chrome.runtime.onMessage.addListener((msg, _s, reply) => {
  if (msg.type === 'kick') { tick(); reply({ ok: true }) }
})
