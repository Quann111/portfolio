const $ = id => document.getElementById(id)
const KEYS = ['enabled', 'minGap', 'maxGap', 'timeoutSec', 'status', 'doneCount', 'profileName', 'workerId', 'profiles']

async function render () {
  const s = { enabled: false, minGap: 8, maxGap: 20, timeoutSec: 240, ...(await chrome.storage.local.get(KEYS)) }
  $('status').textContent = s.status || 'Chưa chạy'
  $('toggle').textContent = s.enabled ? '■ Tắt tự động' : '▶ Bật tự động'
  $('toggle').className = s.enabled ? 'on' : 'off'
  for (const k of ['minGap', 'maxGap', 'timeoutSec', 'profileName']) {
    if (document.activeElement !== $(k)) $(k).value = s[k] || ''
  }
  $('done').textContent = s.doneCount || 0
  $('profiles').textContent = s.profiles || 0
}

$('toggle').onclick = async () => {
  const { enabled } = await chrome.storage.local.get('enabled')
  await chrome.storage.local.set({ enabled: !enabled })
  if (!enabled) chrome.runtime.sendMessage({ type: 'kick' })
  render()
}
for (const k of ['minGap', 'maxGap', 'timeoutSec']) {
  $(k).onchange = () => chrome.storage.local.set({ [k]: Math.max(3, Number($(k).value) || 0) })
}
$('profileName').onchange = () => chrome.storage.local.set({ profileName: $('profileName').value.trim().slice(0, 30) })
// "Tiếp tục" chỉ mở lại profile này (captcha/giới hạn ở tài khoản này) – các profile khác không bị ảnh hưởng
$('resume').onclick = async () => {
  const { workerId } = await chrome.storage.local.get('workerId')
  try {
    await fetch('http://127.0.0.1:8765/flow/resume', { method: 'POST', headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ w: workerId || '' }) })
  } catch {}
  chrome.runtime.sendMessage({ type: 'kick' })
  $('status').textContent = 'Đã tiếp tục (profile này)'
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
