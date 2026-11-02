const $ = id => document.getElementById(id)
async function render () {
  const d = await chrome.storage.local.get(['enabled', 'status', 'profileName'])
  const en = d.enabled !== false
  $('btnToggle').textContent = en ? 'Tắt tự động' : 'Bật tự động'
  $('btnToggle').style.background = en ? '#ef4444' : '#2563eb'
  $('st').textContent = d.status || 'Chưa có hoạt động'
  if (document.activeElement !== $('profileName')) {
    $('profileName').value = d.profileName || ''
  }
}
$('btnToggle').onclick = async () => {
  const d = await chrome.storage.local.get('enabled')
  const en = !(d.enabled !== false)
  await chrome.storage.local.set({ enabled: en })
  chrome.runtime.sendMessage({ type: 'kick' })
  render()
}
$('profileName').onchange = () => chrome.storage.local.set({ profileName: $('profileName').value.trim().slice(0, 30) })
render()
setInterval(render, 1500)
