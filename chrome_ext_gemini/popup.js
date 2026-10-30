const $ = id => document.getElementById(id)
async function render () {
  const d = await chrome.storage.local.get(['enabled', 'status'])
  const en = d.enabled !== false
  $('btnToggle').textContent = en ? 'Tắt tự động' : 'Bật tự động'
  $('btnToggle').style.background = en ? '#ef4444' : '#2563eb'
  $('st').textContent = d.status || 'Chưa có hoạt động'
}
$('btnToggle').onclick = async () => {
  const d = await chrome.storage.local.get('enabled')
  const en = !(d.enabled !== false)
  await chrome.storage.local.set({ enabled: en })
  chrome.runtime.sendMessage({ type: 'kick' })
  render()
}
render()
setInterval(render, 1500)
