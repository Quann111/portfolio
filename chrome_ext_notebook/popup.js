const $ = id => document.getElementById(id)
const KEYS = ['enabled', 'minGap', 'maxGap', 'timeoutSec', 'status', 'doneCount', 'profileName', 'slotsData']

async function render () {
  const s = { enabled: true, minGap: 20, maxGap: 20, timeoutSec: 420, ...(await chrome.storage.local.get(KEYS)) }
  $('status').textContent = s.status || 'Chưa chạy'
  $('toggle').textContent = s.enabled ? '■ Tắt tự động' : '▶ Bật tự động'
  $('toggle').className = s.enabled ? 'on' : 'off'
  for (const k of ['minGap', 'maxGap', 'timeoutSec', 'profileName']) {
    if (document.activeElement !== $(k)) $(k).value = s[k] || ''
  }
  $('done').textContent = s.doneCount || 0

  // Render bảng slot
  const slotListEl = $('slotList')
  if (slotListEl) {
    const slots = s.slotsData || []
    if (!slots.length) {
      slotListEl.innerHTML = '<span style="color:#888">Chưa có thông tin slot (đang kết nối app…)</span>'
    } else {
      let h = '<table style="width:100%;border-collapse:collapse;font-size:10px;text-align:left">'
      h += '<tr style="border-bottom:1px solid #eee;color:#666"><th>Slot</th><th>Sổ</th><th>Job</th><th>Bước</th><th>Trạng thái</th></tr>'
      for (const slot of slots) {
        const bookShort = slot.notebook_url ? (slot.notebook_url.split('/').pop() || 'sổ').slice(0, 8) + '…' : '<span style="color:red">chưa có</span>'
        const jobStr = slot.job_id ? '#' + slot.job_id : '-'
        const stepStr = slot.step || '-'
        let stColor = '#555'
        if (slot.state === 'busy') stColor = '#2980b9'
        else if (slot.state === 'idle') stColor = '#27ae60'
        else if (slot.state === 'paused') stColor = '#e67e22'
        else if (slot.state === 'no_notebook') stColor = '#c0392b'
        h += `<tr style="border-bottom:1px solid #f5f5f5"><td><b>#${slot.slot}</b></td><td>${bookShort}</td><td>${jobStr}</td><td>${stepStr}</td><td style="color:${stColor};font-weight:600">${slot.state}</td></tr>`
      }
      h += '</table>'
      slotListEl.innerHTML = h
    }
  }
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

// Bấm nút "Gợi ý VR" chỉ điền chữ VR vào ô, KHÔNG tự động lưu
$('btnSuggestVR').onclick = () => {
  $('profileName').value = 'VR'
  $('profileName').focus()
}

// Chỉ lưu khi bấm nút Lưu hoặc nhấn Enter
$('btnSaveProfile').onclick = async () => {
  const name = $('profileName').value.trim().slice(0, 30)
  await chrome.storage.local.set({ profileName: name })
  chrome.runtime.sendMessage({ type: 'kick' })
  $('status').textContent = name ? `Đã lưu profile: "${name}"` : 'Đã xóa tên profile'
  render()
}

$('profileName').onkeydown = e => {
  if (e.key === 'Enter') {
    $('btnSaveProfile').click()
  }
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
