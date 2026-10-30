const $ = id => document.getElementById(id)
const KEYS = ['enabled', 'minGap', 'maxGap', 'timeoutSec', 'status', 'doneCount']

async function render () {
  const s = { enabled: false, minGap: 3, maxGap: 8, timeoutSec: 420, ...(await chrome.storage.local.get(KEYS)) }
  $('status').textContent = s.status || 'Chưa chạy'
  $('toggle').textContent = s.enabled ? '■ Tắt tự động' : '▶ Bật tự động'
  $('toggle').className = s.enabled ? 'on' : 'off'
  for (const k of ['minGap', 'maxGap', 'timeoutSec']) if (document.activeElement !== $(k)) $(k).value = s[k]
  $('done').textContent = s.doneCount || 0
}

$('toggle').onclick = async () => {
  const { enabled } = await chrome.storage.local.get('enabled')
  await chrome.storage.local.set({ enabled: !enabled })
  if (!enabled) chrome.runtime.sendMessage({ type: 'kick' })
  render()
}
for (const k of ['minGap', 'maxGap', 'timeoutSec']) {
  $(k).onchange = () => chrome.storage.local.set({ [k]: Math.max(1, Number($(k).value) || 0) })
}
$('resume').onclick = async () => {
  try { await fetch('http://127.0.0.1:8765/nb/resume', { method: 'POST', body: '{}' }) } catch {}
  chrome.runtime.sendMessage({ type: 'kick' })
  $('status').textContent = 'Đã tiếp tục'
}
$('diag').onclick = () => {
  $('out').hidden = false
  $('out').textContent = 'Đang kiểm tra…'
  chrome.runtime.sendMessage({ type: 'diagnose' }, r => {
    $('out').textContent = JSON.stringify(r && (r.diag || r), null, 1)
  })
}
chrome.storage.onChanged.addListener(render)
render()
