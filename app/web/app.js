// AutoVideo Studio – dashboard (dùng API sẵn có của app tại cùng địa chỉ)
const $ = s => document.querySelector(s)
const $$ = s => [...document.querySelectorAll(s)]
const api = async (p, body, method) => {
  const r = await fetch(p, body !== undefined || method
    ? { method: method || 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(body || {}) } : {})
  const j = await r.json().catch(() => ({}))
  if (!r.ok) throw new Error(j.error || r.status)
  return j
}
const esc = s => String(s ?? '').replace(/[&<>"]/g, c => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;' }[c]))
const fmt = sec => { sec = Math.max(0, Math.round(sec || 0)); const h = sec / 3600 | 0, m = sec % 3600 / 60 | 0, s = sec % 60
  return `${String(h).padStart(2, '0')}:${String(m).padStart(2, '0')}:${String(s).padStart(2, '0')}` }
const human = sec => { sec = Math.round(sec || 0); if (!sec) return '–'; const h = sec / 3600 | 0, m = sec % 3600 / 60 | 0
  return h ? `${h}g ${m}p` : m ? `${m}p ${sec % 60}s` : `${sec}s` }
const STATUS = { queued: 'Chờ', running: 'Đang chạy', done: 'Xong', failed: 'Lỗi', review: 'Chờ duyệt', preparing: 'Đang chuẩn bị' }
const STAGE = { research: '🔎 Nghiên cứu', script: '✍️ Kịch bản', tts: '🎙 Giọng đọc', image: '🖼 Ảnh + QC',
  render: '✏️ Vẽ whiteboard', assemble: '🎬 Ghép video', qc: '✅ Kiểm tra cuối', package: '📦 Đóng gói', done: '🏁 Xong' }
const COLORS = { research: '#22d3ee', script: '#a78bfa', tts: '#f472b6', image: '#fbbf24', render: '#a3e635',
  assemble: '#fb7185', qc: '#34d399', package: '#60a5fa' }

let S = { jobs: [], status: {}, current: null, timing: null, stopAfter: null }

function toast (msg, kind = '') {
  const t = document.createElement('div'); t.className = 'toast ' + kind; t.textContent = msg
  $('#toasts').appendChild(t); setTimeout(() => t.remove(), 5000)
}

// ---------------- header / status
async function loadStatus () {
  const s = await api('/status').catch(() => null); if (!s) return
  S.status = s; S.stopAfter = s.stop_after
  $('#runState').innerHTML = s.running ? '<span style="color:#6ee7b7">● ĐANG CHẠY</span>' : '<span style="color:#fda4af">● Đã dừng</span>'
  $('#mode').value = s.mode || 'auto'
  if (s.style && document.activeElement !== $('#style')) $('#style').value = s.style
  if (s.target_minutes && document.activeElement !== $('#targetMinutesDefault')) {
    const selEl = $('#targetMinutesDefault')
    if (selEl) {
      const match = [...selEl.options].find(o => +o.value === +s.target_minutes)
      if (match) selEl.value = match.value
      else {
        // custom duration
        let opt = selEl.querySelector('option[data-custom="1"]')
        if (!opt) {
          opt = document.createElement('option')
          opt.dataset.custom = '1'
          selEl.insertBefore(opt, selEl.lastElementChild)
        }
        opt.value = s.target_minutes
        opt.textContent = `⏱ ${s.target_minutes} phút (Tùy chỉnh)`
        selEl.value = s.target_minutes
      }
    }
  }
  const r = s.res || {}
  $('#pills').innerHTML = [
    ['GPU', r.temp != null ? `${Math.round(r.temp)}°C · ${Math.round(r.util ?? 0)}%` : '?', (r.temp || 0) >= 85],
    ['VRAM', r.vram_used_gb != null ? `${r.vram_used_gb.toFixed(1)}/${r.vram_total_gb.toFixed(0)} GB` : '?'],
    ['RAM', r.ram_used_pct != null ? `${r.ram_used_pct}%` : '?'],
    ['Đĩa trống', `${r.disk_free_gb ?? '?'} GB`],
    ['LM Studio', s.lmstudio?.up ? (s.lmstudio.loaded[0] || 'chưa nạp model').split('/').pop() : 'TẮT', !s.lmstudio?.up],
    ['Flow', `${s.flow_busy ?? 0}/${s.flow_online ?? 0} profile`, !s.flow_online],
    ['NotebookLM', {busy: 'đang viết', idle: 'sẵn sàng', paused: 'tạm dừng', offline: 'mất kết nối', off: 'đang tắt'}[s.notebook] || s.notebook, s.notebook === 'offline' || s.notebook === 'paused'],
    ['Gemini Web', {busy: 'đang xử lý', idle: 'sẵn sàng', paused: 'tạm dừng', offline: 'mất kết nối', off: 'đang tắt'}[s.gemini] || s.gemini, s.gemini === 'offline' || s.gemini === 'paused']
  ].map(([k, v, hot]) => `<span class="pill ${hot ? 'hot' : ''}">${k} <b>${v}</b></span>`).join('')
  const sa = s.stop_after
  $('#afterBanner').classList.toggle('hidden', !sa)
  if (sa) $('#afterBanner').innerHTML = sa.job_id ? `🏁 Sẽ dừng sau khi xong chủ đề <b>#${sa.job_id}</b>`
    : `🏁 Sẽ dừng sau <b>${sa.count}</b> chủ đề nữa (đã xong ${sa.done || 0})`
  $('#fatal').classList.toggle('hidden', !s.fatal); if (s.fatal) $('#fatal').textContent = '⛔ ' + s.fatal
}

// ---------------- hàng đợi
function rowActions (j) {
  const flag = S.stopAfter && S.stopAfter.job_id === j.id
  return `<div class="acts">
    <button class="ib" title="Mở thư mục job" data-a="open" data-id="${j.id}">📂</button>
    <button class="ib" title="Xem nhanh ảnh, kịch bản, video" data-a="view" data-id="${j.id}">👁</button>
    <button class="ib yt-${j.yt || 'none'}" title="Bộ đăng YouTube: thumbnail, tiêu đề, mô tả (${({ running: 'đang viết', thumbs: 'đang tạo thumbnail', done: 'xong', partial: 'thiếu thumbnail', error: 'lỗi' })[j.yt] || 'chưa có'})" data-a="yt" data-id="${j.id}">📺${j.yt === 'done' ? '' : j.yt === 'error' ? '⚠' : j.yt ? '⏳' : ''}</button>
    <button class="ib ${flag ? 'on' : ''}" title="Dừng sau khi xong chủ đề này" data-a="flag" data-id="${j.id}">🏁</button>
    <button class="ib" title="Chạy lại" data-a="retry" data-id="${j.id}">🔁</button>
    <button class="ib" title="Lên" data-a="up" data-id="${j.id}">⬆</button>
    <button class="ib" title="Xuống" data-a="down" data-id="${j.id}">⬇</button>
    <button class="ib del" title="Xoá job + thư mục (vào Thùng rác)" data-a="delete" data-id="${j.id}">🗑</button></div>`
}
async function loadJobs () {
  const r = await api('/jobs').catch(() => null); if (!r) return
  S.jobs = r.jobs
  const cur = r.jobs.find(j => j.status === 'running')
  if (cur?.id !== S.current?.id) S.current = cur || null
  else S.current = cur || null
  if (document.activeElement?.matches('.rstyle, .rvoice')) return
  if (S.dragging) return            // đang kéo thả – không vẽ lại bảng
  $('#queueBody').innerHTML = r.jobs.map(j => `<tr data-id="${j.id}" ${j.status === 'queued' ? 'draggable="true" title="Kéo để đổi thứ tự"' : ''}
    class="${j.status === 'running' ? 'run' : ''} ${j.status === 'queued' ? 'dragme' : ''} ${S.stopAfter?.job_id === j.id ? 'flagged' : ''}">
    <td>${j.id}</td><td class="topic" title="${esc(j.topic)}">${j.status === 'queued' || j.status === 'error'
      ? `<select class="rstyle" data-id="${j.id}" title="Kiểu dựng của chủ đề này"><option value="classic" ${j.style === 'classic' ? 'selected' : ''}>🎞 Cổ điển</option><option value="nikola" ${j.style === 'nikola' ? 'selected' : ''}>✍️ Nikola</option></select> `
      : j.style === 'nikola' ? '<span class="sbadge" title="Kiểu Nikola – bảng hai đảo">✍️</span> ' : '<span class="sbadge" title="Kiểu Cổ điển">🎞</span> '}${
      j.status === 'queued' || j.status === 'error'
        ? `<select class="rvoice" data-id="${j.id}" title="Giọng đọc của chủ đề này">${voiceOpts(j.voice)}</select> `
        : `<span class="sbadge" title="Giọng: ${esc(voiceLabel(j.voice))}">🎙</span> `}${esc(j.topic)}</td>
    <td><button class="chip" data-dur="${j.id}" title="${j.status === 'queued' ? 'Bấm để đổi độ dài video' : 'Thời lượng video'}" style="cursor:${j.status === 'queued' ? 'pointer' : 'default'}">⏱ ${j.target_minutes ? j.target_minutes + 'p' : '20p'}</button></td>
    <td><span class="badge b-${j.status}">${STATUS[j.status] || j.status}</span></td>
    <td>${STAGE[j.stage] || j.stage}</td><td>${human(j.active_sec)}</td>
    <td class="score">${j.score ?? ''}</td><td>${rowActions(j)}</td></tr>`).join('')
  const done = r.jobs.filter(j => j.status === 'done')
  $('#libGrid').innerHTML = done.map(j => `<div class="vcard"><div class="cv" data-a="view" data-id="${j.id}"
    style="background-image:url('/projects/${j.id}/cover')"><span class="play">▶</span></div><div class="bd">
    <div class="tt">${esc(j.topic)}</div><div class="pill">⭐ <b>${j.score ?? '–'}</b></div> <div class="pill">⏱ <b>${human(j.active_sec)}</b></div>
    <div class="acts" style="margin-top:8px">${rowActions(j)}</div></div></div>`).join('') || '<p>Chưa có video nào.</p>'
}

// ---------------- giọng đọc
S.voices = []
const voiceLabel = id => S.voices.find(o => o.id === id)?.label || id || ''
const voiceOpts = cur => S.voices.map(o => `<option value="${esc(o.id)}" ${o.id === cur ? 'selected' : ''}>${esc(o.label)}</option>`).join('')
async function loadVoices () {
  const r = await api('/voices').catch(() => null); if (!r) return
  S.voices = r.options
  $('#voice').innerHTML = voiceOpts(r.default)
  const keep = $('#addVoice').value
  $('#addVoice').innerHTML = '<option value="">Giọng: theo mặc định</option>' + voiceOpts(keep)
  const curPreview = $('#previewVoiceSelect')?.value || r.default
  if ($('#previewVoiceSelect')) {
    $('#previewVoiceSelect').innerHTML = voiceOpts(curPreview)
    updateDeleteBtn()
  }
}
$('#voice').onchange = async e => { await api('/voice', { voice: e.target.value }); toast('🎙 Giọng mặc định: ' + voiceLabel(e.target.value), 'ok') }
async function handleVoiceUpload(file) {
  if (!file) return
  if (file.size > 20e6) return toast('File giọng quá lớn (tối đa 20 MB)', 'err')
  const data = await new Promise(res => { const rd = new FileReader(); rd.onload = () => res(rd.result.split(',')[1]); rd.readAsDataURL(file) })
  try {
    toast('⏳ Đang xử lý và lưu giọng mẫu...')
    const r = await api('/voices/upload', { name: file.name, data })
    await loadVoices()
    if ($('#addVoice')) $('#addVoice').value = `voicebox|u:${r.file}`
    if ($('#previewVoiceSelect')) $('#previewVoiceSelect').value = `voicebox|u:${r.file}`
    toast(`🎙 Đã thêm giọng ${r.file} thành công!`, 'ok')
    loadJobs()
  } catch (err) { toast(err.message, 'err') }
}

$('#btnVoiceUp').onclick = () => $('#voiceFile').click()
$('#voiceFile').onchange = e => { handleVoiceUpload(e.target.files[0]); e.target.value = '' }

$('#btnPreviewVoiceUp')?.addEventListener('click', () => $('#previewVoiceFile').click())
$('#previewVoiceFile')?.addEventListener('change', e => { handleVoiceUpload(e.target.files[0]); e.target.value = '' })

function updateDeleteBtn() {
  const sel = $('#previewVoiceSelect')?.value || ''
  const isUpload = sel.includes('|u:')
  const btnDel = $('#btnDeleteVoice')
  if (btnDel) {
    btnDel.style.display = isUpload ? 'inline-block' : 'none'
  }
}

$('#previewVoiceSelect')?.addEventListener('change', updateDeleteBtn)

$('#btnDeleteVoice')?.addEventListener('click', async () => {
  const sel = $('#previewVoiceSelect')?.value || ''
  if (!sel.includes('|u:')) return
  const file = sel.split('|u:')[1]
  if (!confirm(`Bạn có chắc chắn muốn xoá giọng "${file}" khỏi hệ thống?`)) return
  toast('⏳ Đang xoá giọng...')
  try {
    const res = await api('/voices/delete', { file })
    await loadVoices()
    toast(`🗑 Đã xoá thành công giọng ${file}`, 'ok')
    loadJobs()
  } catch (err) { toast('Lỗi xoá giọng: ' + err.message, 'err') }
})

$('#btnSetAsDefaultVoice')?.addEventListener('click', async () => {
  const v = $('#previewVoiceSelect')?.value
  if (!v) return
  try {
    await api('/voice', { voice: v })
    $('#voice').value = v
    toast('⭐ Đã đặt làm giọng mặc định: ' + voiceLabel(v), 'ok')
  } catch (err) { toast('Lỗi: ' + err.message, 'err') }
})

$('#btnSpeakTest')?.addEventListener('click', async () => {
  const voice = $('#previewVoiceSelect')?.value
  const text = $('#previewText')?.value.trim()
  if (!text) return toast('Vui lòng nhập câu để đọc thử', 'err')
  const btn = $('#btnSpeakTest')
  btn.disabled = true
  btn.textContent = '⏳ Đang tạo...'
  toast('🎙 Đang tổng hợp giọng thử nghiệm...')
  try {
    const res = await api('/voices/speak', { voice, text })
    const player = $('#voiceAudioPlayer')
    if (player && res.audio) {
      player.src = res.audio
      player.style.display = 'block'
      player.play()
      toast('🔊 Đang phát giọng đọc thử', 'ok')
    }
  } catch (err) {
    toast('Lỗi đọc thử: ' + err.message, 'err')
  } finally {
    btn.disabled = false
    btn.textContent = '🔊 Đọc thử'
  }
})
loadVoices()

// ---- khe model LM Studio: bật/tắt, chọn model (danh sách model đã tải, tự cập nhật), GPU/CPU
async function loadLLM () {
  const box = $('#llmSlots')
  if (box.contains(document.activeElement) && document.activeElement.tagName === 'SELECT') return
  const r = await api('/llm').catch(() => null); if (!r) return
  const opts = sel => '<option value="">– chọn model –</option>' + r.models.map(m =>
    `<option value="${m.id}" ${m.id === sel ? 'selected' : ''}>${m.id.split('/').pop()}${m.vl ? ' · VL' : ''}${m.size ? ' · ' + m.size : ''}</option>`).join('') +
    (sel && !r.models.some(m => m.id === sel) ? `<option value="${sel}" selected>${sel} (không còn trong LM Studio)</option>` : '')
  box.innerHTML = r.slots.map(s => `
    <div class="llmrow ${s.on ? 'on' : ''}" data-i="${s.i}">
      <label class="sw"><input type="checkbox" class="llm-on" ${s.on ? 'checked' : ''}> Khe ${s.i + 1}</label>
      <select class="btn ghost llm-model">${opts(s.model)}</select>
      <select class="btn ghost llm-dev">
        <option value="gpu" ${s.device === 'gpu' ? 'selected' : ''}>GPU</option>
        <option value="cpu" ${s.device === 'cpu' ? 'selected' : ''}>CPU</option>
      </select>
      <span class="llm-st">${!s.on ? '⚪ tắt' : s.loaded ? '🟢 đã nạp' : '🟡 nạp khi cần'}</span>
    </div>`).join('')
}
$('#llmSlots').addEventListener('change', async e => {
  const row = e.target.closest('.llmrow'); if (!row) return
  const body = { i: +row.dataset.i }
  if (e.target.classList.contains('llm-on')) body.on = e.target.checked
  if (e.target.classList.contains('llm-model')) body.model = e.target.value
  if (e.target.classList.contains('llm-dev')) body.device = e.target.value
  try {
    await api('/llm/slot', body)
    toast(`🧠 Khe ${body.i + 1}: đã lưu`, 'ok')
  } catch (err) { toast(err.message, 'err') }
  e.target.blur(); loadLLM()
})
loadLLM(); setInterval(loadLLM, 10000)

document.addEventListener('change', async e => {
  const sel = e.target.closest('.rvoice'); if (!sel) return
  try {
    await api(`/projects/${sel.dataset.id}/voice`, { voice: sel.value })
    toast(`Chủ đề #${sel.dataset.id}: 🎙 ${voiceLabel(sel.value)}`, 'ok')
  } catch (err) { toast(err.message, 'err') }
  sel.blur(); loadJobs()
})

document.addEventListener('change', async e => {
  const sel = e.target.closest('.rstyle'); if (!sel) return
  try {
    await api(`/projects/${sel.dataset.id}/style`, { style: sel.value })
    toast(`Chủ đề #${sel.dataset.id}: kiểu ${sel.value === 'nikola' ? '✍️ Nikola' : '🎞 Cổ điển'}`, 'ok')
  } catch (err) { toast(err.message, 'err') }
  sel.blur(); loadJobs()
})

document.addEventListener('click', async e => {
  const b = e.target.closest('[data-a]'); if (!b) return
  const id = +b.dataset.id, a = b.dataset.a, j = S.jobs.find(x => x.id === id)
  try {
    if (a === 'open') { await api(`/projects/${id}/open`, {}); toast('📂 Đã mở thư mục job ' + id) }
    if (a === 'view') return openView(id)
    if (a === 'yt') return openYT(id)
    if (a === 'ytregen') { await api(`/projects/${id}/youtube/regen`, {}); toast('🔁 Đang tạo lại bộ YouTube job ' + id); return }
    if (a === 'ytopen') { await api(`/projects/${id}/youtube/open`, {}); toast('📂 Đã mở thư mục youtube/ của job ' + id); return }
    if (a === 'copy') { await navigator.clipboard.writeText(b.dataset.text || $(b.dataset.src).value); toast('📋 Đã copy'); return }
    if (a === 'thumb') { $('#ytBig').src = b.dataset.src; return }
    if (a === 'ytlang') { S.ytLang = b.dataset.lang; return openYT(id) }
    if (a === 'flag') {
      const on = S.stopAfter?.job_id === id
      await api('/stop_after', on ? {} : { job_id: id }); toast(on ? 'Đã huỷ mốc dừng' : `🏁 Sẽ dừng sau chủ đề #${id}`)
    }
    if (a === 'retry') { await api(`/projects/${id}/retry`, {}); toast('🔁 Đã xếp chạy lại job ' + id) }
    if (a === 'up' || a === 'down') await api(`/projects/${id}/${a}`, {})
    if (a === 'delete') {
      if (j?.status === 'running') return toast('Job đang chạy – dừng trước khi xoá', 'err')
      if (!confirm(`Xoá job #${id} "${j?.topic}" cùng toàn bộ thư mục của nó?\n(Thư mục được đưa vào Thùng rác, có thể khôi phục)`)) return
      const r = await api(`/projects/${id}/delete`, {}); toast(`🗑 Đã xoá job ${id} (${r.mb} MB vào Thùng rác)`, 'ok')
    }
  } catch (err) { toast('Lỗi: ' + err.message, 'err') }
  loadJobs(); loadStatus()
})

// ---------------- kéo thả đổi thứ tự hàng chờ
document.addEventListener('dragstart', e => {
  const tr = e.target.closest('tr.dragme'); if (!tr) return
  S.dragging = +tr.dataset.id; tr.classList.add('dragging'); e.dataTransfer.effectAllowed = 'move'
})
document.addEventListener('dragover', e => {
  const tr = e.target.closest('tr.dragme'); if (!tr || !S.dragging) return
  e.preventDefault(); $$('tr.dropto').forEach(x => x.classList.remove('dropto')); tr.classList.add('dropto')
})
document.addEventListener('drop', async e => {
  const tr = e.target.closest('tr.dragme'); const id = S.dragging; S.dragging = null
  $$('tr.dropto,tr.dragging').forEach(x => x.classList.remove('dropto', 'dragging'))
  if (!tr || !id || +tr.dataset.id === id) return loadJobs()
  e.preventDefault(); await api(`/projects/${id}/move`, { before: +tr.dataset.id }); toast('↕ Đã đổi thứ tự hàng chờ'); loadJobs()
})
document.addEventListener('dragend', () => { if (S.dragging) { S.dragging = null; loadJobs() } })

// ---------------- job đang chạy: timeline + đồng hồ
async function loadTiming () {
  const j = S.current
  if (!j) { $('#nowTitle').textContent = S.status.running ? '⏳ Đang chờ chủ đề / tìm chủ đề mới…' : 'Chưa chạy'
    $('#nowSub').textContent = ''; $('#timeline').innerHTML = ''; $('#stackBar').innerHTML = ''; S.timing = null; return }
  const t = await api(`/projects/${j.id}/timing`).catch(() => null); if (!t) return
  S.timing = { ...t, at: Date.now() }
  $('#nowTitle').textContent = `#${j.id} · ${j.topic}`
  const run = t.steps.find(s => s.state === 'running')
  $('#nowSub').textContent = run ? `Đang: ${run.icon} ${run.name}` : ''
  $('#timeline').innerHTML = timelineHTML(t)
  $('#stackBar').innerHTML = stackHTML(t)
  $('#eta').textContent = `chạy thật · chờ ${human(t.wait_sec)} · tổng ${human(t.wall_sec)}` + (run?.eta_sec ? ` · bước này còn ~${human(run.eta_sec)}` : '')
}
// timeline 8 bước lớn + 13 bước con (dùng cho job đang chạy và khi xem lại job đã xong)
function timelineHTML (t) {
  return t.steps.map(s => {
    const pct = s.total ? Math.round(100 * (s.done || 0) / s.total) : 0
    const subs = (s.subs || []).filter(b => b.state !== 'pending' || s.state === 'running')
    return `<li class="st ${s.state}"><div class="dot">${s.state === 'done' ? '✓' : s.state === 'error' ? '✕' : s.icon}</div>
      <div><div class="nm">${s.name}</div>
      <div class="nt">${s.total ? `${s.done || 0}/${s.total}` : ''} ${s.runs > 1 ? `· chạy ${s.runs} lần` : ''} ${esc(s.note || '')}
      ${s.eta_sec ? ` · còn ~${human(s.eta_sec)}` : ''}${s.wait_sec ? ` · <span class="w">chờ ${human(s.wait_sec)}${s.wait_why ? ' (' + esc(s.wait_why) + ')' : ''}</span>` : ''}</div>
      ${s.state === 'running' && s.total ? `<div class="bar"><i style="width:${pct}%"></i></div>` : ''}
      ${subs.length ? `<ul class="subs">${subs.map(b => `<li class="sb ${b.state}"><span>${b.state === 'done' ? '✓' : b.state === 'running' ? '▸' : '·'} ${esc(b.name)}</span>
        <span>${b.total ? `${b.done ?? 0}/${b.total} ` : ''}${esc(b.note || '')} ${b.active_sec ? human(b.active_sec) : ''}</span></li>`).join('')}</ul>` : ''}</div>
      <div class="tm">${s.active_sec ? human(s.active_sec) : ''}</div></li>`
  }).join('')
}
function stackHTML (t) {
  const tot = Math.max(1, t.active_sec)
  return t.steps.filter(s => s.active_sec).map(s =>
    `<i title="${s.name}: ${human(s.active_sec)}" style="width:${100 * s.active_sec / tot}%;background:${COLORS[s.key]}"></i>`).join('')
}
function metricsHTML (m) {
  if (!m) return ''
  return `<div class="metrics">
    <span class="pill">🎞 <b>${m.so_canh}</b> cảnh · <b>${m.phut_video}</b> phút video</span>
    <span class="pill">⚙ <b>${m.phut_lam_moi_phut_video ?? '–'}</b> phút làm / 1 phút video</span>
    <span class="pill">✅ QC đạt lần đầu <b>${m.qc_dat_lan_dau_pct ?? '–'}%</b></span>
    ${Object.entries(m.giay_moi_canh || {}).map(([k, v]) => `<span class="pill">${STAGE[k]} <b>${v}s</b>/cảnh</span>`).join('')}
    ${(m.profile || []).map(p => `<span class="pill">🎨 ${esc(p.ten)} <b>${p.anh}</b> ảnh · ${p.giay_moi_anh}s/ảnh</span>`).join('')}</div>`
}
setInterval(() => { // đồng hồ chạy từng giây giữa hai lần hỏi server
  if (!S.timing) { $('#clock').textContent = '00:00:00'; return }
  const running = S.timing.steps.some(s => s.state === 'running')
  $('#clock').textContent = fmt(S.timing.active_sec + (running ? (Date.now() - S.timing.at) / 1000 : 0))
}, 1000)

// ---------------- trình duyệt / extension
async function loadBrowsers () {
  const r = await api('/browsers').catch(() => null); if (!r) return
  $('#brSum').textContent = `· ${r.flow_online} profile Flow · ${r.flow_busy} đang tạo · ~${r.per_hour} ảnh/giờ`
  const ST = { busy: 'đang tạo', idle: 'chờ việc', paused: 'tạm dừng', offline: 'mất kết nối', off: 'đang tắt' }
  $('#browsers').innerHTML = r.browsers.map(b => `<div class="br s-${b.state}"><span class="led"></span>
    <div><div class="nm"><a class="focus" data-focus="${b.kind === 'flow' ? b.id : b.kind === 'gemini' ? 'gemini' : 'notebooklm'}" title="Đưa tab của profile này lên trước">${b.kind === 'notebook' ? '📓' : b.kind === 'gemini' ? '✨' : '🎨'} ${esc(b.name)} ↗</a> <small class="meta">v${esc(b.version || '?')} · ${ST[b.state]}</small>
      ${b.outdated ? `<small class="old">⚠ bản cũ – Reload lên ${esc(b.latest)}</small>` : ''}</div>
      <div class="meta">${esc(b.doing || '')}</div>${b.paused ? `<div class="why">⏸ ${esc(b.paused)}</div>` : ''}</div>
    <div class="num">${b.kind === 'flow' ? `${b.done} ảnh · ${b.per_hour}/giờ<br>` : ''}${human(b.last_sec)} trước
      <div style="display:flex;gap:4px;margin-top:4px;justify-content:flex-end">
        <button class="chip" data-repair="${b.kind === 'flow' ? b.id : b.kind === 'gemini' ? 'gem' : 'nb'}" title="Tự động tải lại tab và kết nối lại">🔧 Tự sửa</button>
        ${b.paused ? `<button class="chip" data-resume="${b.kind === 'flow' ? b.id : b.kind === 'gemini' ? 'gem' : 'nb'}">▶ Tiếp tục</button>` : ''}
      </div></div></div>`).join('')
    || '<p class="meta">Chưa có trình duyệt nào kết nối – mở Chrome, tab Flow / NotebookLM và bật extension.</p>'
}
document.addEventListener('click', async e => {
  const b = e.target.closest('[data-resume]'); if (!b) return
  await (b.dataset.resume === 'gem' ? api('/gemini/resume', {}) : b.dataset.resume === 'nb' ? api('/nb/resume', {}) : api('/flow/resume', { w: b.dataset.resume }))
  toast('▶ Đã cho profile chạy lại'); loadBrowsers()
})

document.addEventListener('click', async e => {
  const b = e.target.closest('[data-repair]'); if (!b) return
  const w = b.dataset.repair === 'nb' ? 'notebooklm' : b.dataset.repair
  toast('🔧 Đang kích hoạt tự động sửa lỗi cho profile...')
  try {
    await api('/repair', { w })
    toast('✔ Đã gửi lệnh tự sửa lỗi và tải lại tab cho profile', 'ok')
    setTimeout(loadBrowsers, 2000)
  } catch (err) { toast('Lỗi: ' + err.message, 'err') }
})

$('#btnOpenAllProfiles')?.addEventListener('click', async () => {
  toast('🚀 Đang khởi động mở toàn bộ 9 profile Chrome...')
  try {
    const res = await api('/browsers/open_all', {})
    toast('✔ ' + (res.msg || 'Đang mở toàn bộ profile'), 'ok')
    setTimeout(loadBrowsers, 3000)
  } catch (err) { toast('Lỗi mở trình duyệt: ' + err.message, 'err') }
})

$('#btnCloseAllProfiles')?.addEventListener('click', async () => {
  if (!confirm('Bạn có chắc chắn muốn đóng toàn bộ các cửa sổ Chrome đang mở?')) return
  toast('⏹ Đang đóng toàn bộ trình duyệt Chrome...')
  try {
    const res = await api('/browsers/close_all', {})
    toast('✔ ' + (res.msg || 'Đã đóng toàn bộ profile Chrome'), 'ok')
    setTimeout(loadBrowsers, 1000)
  } catch (err) { toast('Lỗi đóng trình duyệt: ' + err.message, 'err') }
})

$('#btnTestAll')?.addEventListener('click', async () => {
  toast('🩺 Đang kiểm tra và tự sửa lỗi toàn bộ profile...')
  try {
    const res = await api('/test_all', {})
    if (res.repaired && res.repaired.length) {
      toast('✔ Đã tự động kích hoạt sửa lỗi cho: ' + res.repaired.join(', '), 'ok')
    } else {
      toast('✔ Tất cả profile đang hoạt động tốt!', 'ok')
    }
    setTimeout(loadBrowsers, 2000)
  } catch (err) { toast('Lỗi: ' + err.message, 'err') }
})

document.addEventListener('click', async e => {
  const a = e.target.closest('[data-focus]'); if (!a) return
  await api('/focus', { w: a.dataset.focus })
  toast(`👀 Đang đưa tab của "${a.textContent.replace('↗', '').trim()}" lên trước (vài giây)`)
})

// ---------------- bộ đăng YouTube
async function openYT (id) {
  const j = S.jobs.find(x => x.id === id)
  const y = await api(`/projects/${id}/youtube`)
  const L = S.ytLang || 'vi', d = y.data?.[L]
  const st = y.status || {}
  const stTxt = ({ running: '⏳ NotebookLM đang viết tiêu đề & mô tả…', thumbs: '⏳ Đang chờ Flow tạo ảnh nền thumbnail…',
    done: '✅ Đã xong', partial: '⚠ Có chữ nhưng chưa có thumbnail', error: '⚠ Lỗi: ' + esc(st.error || '') })[st.state] || 'Chưa tạo'
  const big = y.thumbs[0] ? y.url + y.thumbs[0] : ''
  $('#modalBody').innerHTML = `<h2>📺 Bộ đăng YouTube · #${id} ${esc(j?.topic || '')}</h2>
    <div class="actions" style="margin-bottom:12px">
      <span class="pill">${stTxt}</span>
      <button class="btn ghost" data-a="ytlang" data-lang="vi" data-id="${id}" ${L === 'vi' ? 'style="outline:2px solid #22d3ee"' : ''}>🇻🇳 Tiếng Việt</button>
      <button class="btn ghost" data-a="ytlang" data-lang="en" data-id="${id}" ${L === 'en' ? 'style="outline:2px solid #22d3ee"' : ''}>🇬🇧 English</button>
      <button class="btn ghost" data-a="ytopen" data-id="${id}">📂 Mở thư mục youtube/</button>
      <button class="btn ghost" data-a="ytregen" data-id="${id}">🔁 Tạo lại</button></div>
    ${big ? `<img id="ytBig" class="ytbig" src="${big}">
      <div class="ytthumbs">${y.thumbs.map(t => `<img data-a="thumb" data-id="${id}" data-src="${y.url + t}" src="${y.url + t}" title="${t}">`).join('')}</div>` : '<p class="meta">Chưa có thumbnail.</p>'}
    ${d ? `<h3>📝 Tiêu đề (Title)</h3><ol class="yttitles">${d.titles.map(t => `<li><span>${esc(t)}</span> <small>${t.length} ký tự</small>
        <button class="ib" data-a="copy" data-id="${id}" data-text="${esc(t)}">📋</button></li>`).join('')}</ol>
      <h3>📄 Mô tả (Description) <button class="ib" data-a="copy" data-id="${id}" data-src="#ytDesc">📋 Copy tất cả</button></h3>
      <textarea id="ytDesc" class="ytdesc" readonly>${esc(y.description[L] || '')}</textarea>
      <h3>🏷 Thẻ (Tags) <button class="ib" data-a="copy" data-id="${id}" data-src="#ytTags">📋</button></h3>
      <textarea id="ytTags" class="ytdesc" style="height:70px" readonly>${esc(y.tags || '')}</textarea>` : ''}
    <p class="meta">📁 ${esc(y.folder)}</p>`
  $('#modal').classList.remove('hidden')
}

// ---------------- xem nhanh job
async function openView (id) {
  const j = S.jobs.find(x => x.id === id)
  const [sc, fl, t] = await Promise.all([api(`/projects/${id}/scenes`).catch(() => []),
    api(`/projects/${id}/files`).catch(() => ({ files: [] })), api(`/projects/${id}/timing`).catch(() => null)])
  const vid = fl.files.find(f => f.endsWith('.mp4'))
  const scr = fl.files.find(f => f.endsWith('script.md'))
  $('#modalBody').innerHTML = `<h2>👁 #${id} · ${esc(j?.topic)}</h2>
    <div class="actions" style="margin-bottom:12px">
      <button class="btn ghost" data-a="open" data-id="${id}">📂 Mở thư mục</button>
      ${scr ? `<a class="btn ghost" target="_blank" href="/files/${scr}">📄 Kịch bản</a>` : ''}
      <span class="pill">⏱ chạy thật <b>${human(t?.active_sec)}</b></span><span class="pill">tổng <b>${human(t?.wall_sec)}</b></span>
      <span class="pill">⭐ <b>${j?.score ?? '–'}</b></span><span class="pill">📁 <b>${esc(fl.folder || '')}</b></span></div>
    ${vid ? `<video controls src="/files/${vid}"></video>` : ''}
    ${t ? `${t.estimated ? '<p class="meta">⚠ Thời gian là ước tính từ nhật ký (job làm trước khi có đồng hồ)</p>' : ''}
      ${metricsHTML(t.metrics)}<div class="stack">${stackHTML(t)}</div>
      <div class="legend">${t.steps.filter(s => s.active_sec).map(s => `<span><i style="background:${COLORS[s.key]}"></i>${s.name}: ${human(s.active_sec)}</span>`).join('')}</div>
      <details ${j?.status === 'done' ? '' : 'open'}><summary>⏱ Timeline các bước của job này</summary><ol class="timeline">${timelineHTML(t)}</ol></details>` : ''}
    <div class="scenes">${sc.map(s => `<div class="scene">${s.image ? `<img loading="lazy" src="/files/${s.image}">` : '<div class="noimg">chưa có ảnh</div>'}
      <p><b>${s.idx + 1}.</b> ${esc(s.narration)}</p></div>`).join('')}</div>`
  $('#modal').classList.remove('hidden')
}
$('#modalX').onclick = () => $('#modal').classList.add('hidden')
$('#modal').onclick = e => { if (e.target.id === 'modal') $('#modal').classList.add('hidden') }

// ---------------- thống kê thời gian
async function loadStats () {
  const r = await api('/stats/timing').catch(() => []); if (!r.length) { $('#statsBox').innerHTML = '<p>Chưa có job hoàn tất có dữ liệu thời gian.</p>'; return }
  const keys = Object.keys(COLORS), avg = r.reduce((a, j) => a + j.active_sec, 0) / r.length
  const max = Math.max(...r.map(j => j.active_sec), 1)
  $('#statsBox').innerHTML = `<div class="legend">${keys.map(k => `<span><i style="background:${COLORS[k]}"></i>${STAGE[k]}</span>`).join('')}</div>
    <p>Trung bình <b>${human(avg)}</b>/video · nhanh nhất <b>${human(Math.min(...r.map(j => j.active_sec)))}</b> · chậm nhất <b>${human(max)}</b></p>
    ${r.map(j => `<div class="statrow"><div class="topic" title="${esc(j.topic)}">#${j.id} ${esc(j.topic)}</div>
      <div class="stack" style="width:${100 * j.active_sec / max}%">${keys.filter(k => j.steps[k]).map(k =>
        `<i title="${STAGE[k]}: ${human(j.steps[k])}" style="width:${100 * j.steps[k] / Math.max(1, j.active_sec)}%;background:${COLORS[k]}"></i>`).join('')}</div>
      <div>${human(j.active_sec)}</div></div>`).join('')}`
}

// ---------------- log trực tiếp (SSE)
let logFilter = 'all'
function addLog (e) {
  const m = e.data.msg || ''
  const cls = /✖|lỗi|Lỗi|⛔|không đạt/.test(m) ? 'err' : /✔|Xong|xong|📦|🏁/.test(m) ? 'ok' : /⏸|chờ|Chờ/.test(m) ? 'wait' : /🖼|Ảnh/.test(m) ? 'img' : ''
  const d = document.createElement('div'); d.className = cls; d.dataset.k = cls
  d.textContent = `[${e.data.time || new Date(e.t * 1000).toLocaleTimeString()}] ${m}`
  if (logFilter !== 'all' && cls !== logFilter) d.style.display = 'none'
  const L = $('#log'); const stick = L.scrollTop + L.clientHeight >= L.scrollHeight - 30
  L.appendChild(d); while (L.children.length > 600) L.firstChild.remove()
  if (stick) L.scrollTop = L.scrollHeight
}
$$('.chip[data-f]').forEach(c => c.onclick = () => {
  $$('.chip[data-f]').forEach(x => x.classList.remove('active')); c.classList.add('active'); logFilter = c.dataset.f
  $$('#log div').forEach(d => d.style.display = logFilter === 'all' || d.dataset.k === logFilter ? '' : 'none')
})
function addThumb (img) {
  if (!img) return
  const m = img.replace(/\\/g, '/').match(/(?:exchange|projects)\/(.+)$/); if (!m) return
  const box = $('#thumbs'); if ([...box.children].some(i => i.dataset.p === m[1])) return
  const el = document.createElement('img'); el.src = '/files/' + m[1]; el.dataset.p = m[1]
  box.prepend(el); while (box.children.length > 12) box.lastChild.remove()
}
function connect () {
  const es = new EventSource('/events')
  es.onmessage = ev => {
    const e = JSON.parse(ev.data)
    if (e.kind === 'log') addLog(e)
    if (e.kind === 'progress' && e.data.image) addThumb(e.data.image)
    if (e.kind === 'step') { loadTiming(); loadJobs() }
    if (e.kind === 'done') { confetti(); toast(`🎉 Xong video: ${e.data.topic}`, 'ok'); loadJobs(); loadStats() }
  }
  es.onerror = () => { es.close(); setTimeout(connect, 3000) }
}

// ---------------- pháo giấy khi xong video
function confetti () {
  const c = $('#confetti'), x = c.getContext('2d'); c.width = innerWidth; c.height = innerHeight
  const cols = ['#f472b6', '#a78bfa', '#22d3ee', '#a3e635', '#fbbf24']
  const P = Array.from({ length: 180 }, () => ({ x: innerWidth / 2, y: innerHeight / 3, vx: (Math.random() - .5) * 16,
    vy: Math.random() * -14 - 4, s: Math.random() * 8 + 4, c: cols[Math.random() * 5 | 0], r: Math.random() * 6 }))
  let f = 0; (function tick () {
    x.clearRect(0, 0, c.width, c.height)
    P.forEach(p => { p.vy += .35; p.x += p.vx; p.y += p.vy; p.r += .2; x.save(); x.translate(p.x, p.y); x.rotate(p.r)
      x.fillStyle = p.c; x.fillRect(-p.s / 2, -p.s / 2, p.s, p.s * .6); x.restore() })
    if (++f < 160) requestAnimationFrame(tick); else x.clearRect(0, 0, c.width, c.height)
  })()
}

// ---------------- nút điều khiển
$('#btnStart').onclick = async () => { await api('/start', {}); toast('▶ Bắt đầu chạy'); loadStatus() }
$('#btnStop').onclick = async () => { await api('/stop', {}); toast('■ Sẽ dừng sau bước hiện tại'); loadStatus() }
$('#btnKill').onclick = async () => { if (confirm('Dừng ngay tiến trình đang chạy?')) { await api('/stop', { immediate: true }); loadStatus() } }
$('#btnScout')?.addEventListener('click', async () => {
    toast('🔍 AI đang phân tích tin tức & YouTube để tìm 3 chủ đề hay nhất...', 'ok')
    try {
      const res = await api('/topics/scout', { count: 3 })
      toast('✔ ' + (res.msg || 'Đang quét chủ đề...'), 'ok')
      setTimeout(loadJobs, 4000)
    } catch (err) { toast('Lỗi tìm chủ đề: ' + err.message, 'err') }
  })

  $('#btnAdd').onclick = async () => {
  const t = $('#topics').value.split('\n').map(s => s.trim()).filter(Boolean); if (!t.length) return
  const st = $('#addStyle').value, vo = $('#addVoice').value
  let dur = $('#addDuration')?.value
  if (dur === 'custom') {
    const inp = prompt('Nhập số phút cho các chủ đề này (ví dụ: 10, 18, 45):', '15')
    if (inp && !isNaN(+inp) && +inp > 0) dur = +inp
    else dur = null
  }
  const body = { topics: t, ...(st && { style: st }), ...(vo && { voice: vo }), ...(dur && { target_minutes: +dur }) }
  const r = await api('/topics', body)
  $('#topics').value = ''
  toast(`＋ Đã thêm ${r.added.length} chủ đề${dur ? ` (${dur} phút)` : ''}`, 'ok')
  loadJobs()
}
$('#style').onchange = async e => { await api('/style', { style: e.target.value }); toast('Kiểu dựng mặc định: ' + e.target.selectedOptions[0].text) }
$('#targetMinutesDefault').onchange = async e => {
  let val = e.target.value
  if (val === 'custom') {
    const inp = prompt('Nhập số phút mục tiêu cho video (ví dụ: 10, 18, 45):', '20')
    if (!inp || isNaN(+inp) || +inp <= 0) { e.target.blur(); return loadStatus() }
    val = +inp
  }
  try {
    const r = await api('/target_minutes', { minutes: +val })
    toast(`⏱ Thời lượng video mặc định: ${r.target_minutes} phút`, 'ok')
    loadStatus()
  } catch (err) { toast(err.message, 'err') }
}
$('#mode').onchange = async e => { await api('/mode', { mode: e.target.value }); toast('Chế độ: ' + e.target.selectedOptions[0].text) }
$('#btnAfter').onclick = () => $('.dropdown').classList.toggle('open')
document.addEventListener('click', e => { if (!e.target.closest('.dropdown')) $('.dropdown').classList.remove('open') })
$$('[data-after]').forEach(b => b.onclick = async () => {
  const k = b.dataset.after
  if (k === 'current') { if (!S.current) return toast('Không có job đang chạy', 'err'); await api('/stop_after', { job_id: S.current.id }) }
  if (k === 'count') await api('/stop_after', { count: +$('#afterCount').value || 1 })
  if (k === 'clear') await api('/stop_after', {})
  $('.dropdown').classList.remove('open'); loadStatus(); loadJobs()
})
$$('.tab').forEach(t => t.onclick = () => {
  $$('.tab').forEach(x => x.classList.remove('active')); t.classList.add('active')
  $$('.tabpane').forEach(p => p.classList.add('hidden')); $('#tab-' + t.dataset.tab).classList.remove('hidden')
  if (t.dataset.tab === 'stats') loadStats()
})

// ---------------- giao diện: hacker (mặc định) ⇄ màu
function setTheme (t) {
  document.body.dataset.theme = t
  try { localStorage.setItem('av-theme', t) } catch {}
}
try { setTheme(localStorage.getItem('av-theme') || 'hacker') } catch { setTheme('hacker') }
// xoay vòng 3 giao diện: hacker → nhiều màu → cổ điển (Windows 95)
const THEMES = ['hacker', 'color', 'retro']
const THEME_NAME = { hacker: '🖥 Hacker', color: '🎨 Nhiều màu', retro: '🪟 Cổ điển' }
$('#btnTheme').onclick = () => {
  const t = THEMES[(THEMES.indexOf(document.body.dataset.theme) + 1) % THEMES.length]
  setTheme(t); toast('Giao diện: ' + THEME_NAME[t])
}

// mưa ký tự kiểu Matrix
;(function matrix () {
  const c = $('#matrix'), x = c.getContext('2d'), chars = 'アカサタナハマヤラワ01ABCDEF<>/{}#$%&*=+AUTOVIDEO'
  let cols, drops
  const size = () => { c.width = innerWidth; c.height = innerHeight; cols = Math.floor(c.width / 16); drops = Array(cols).fill(1) }
  size(); addEventListener('resize', size)
  setInterval(() => {
    if (document.body.dataset.theme !== 'hacker') return
    x.fillStyle = 'rgba(0,0,0,0.08)'; x.fillRect(0, 0, c.width, c.height)
    x.font = '15px monospace'
    for (let i = 0; i < cols; i++) {
      x.fillStyle = Math.random() > .975 ? '#d7ffe6' : '#00ff66'
      x.fillText(chars[Math.random() * chars.length | 0], i * 16, drops[i] * 16)
      if (drops[i] * 16 > c.height && Math.random() > .975) drops[i] = 0
      drops[i]++
    }
  }, 55)
})()

// ---------------- vòng cập nhật
async function tick () { await loadStatus(); await loadJobs(); loadTiming() }
tick(); loadBrowsers(); connect()
setInterval(tick, 3000); setInterval(loadBrowsers, 3000)

document.addEventListener('click', async e => {
  const b = e.target.closest('[data-dur]'); if (!b) return
  const id = +b.dataset.dur
  const j = S.jobs.find(x => x.id === id)
  if (!j || j.status !== 'queued') return
  const cur = j.target_minutes || 20
  const inp = prompt(`Đổi thời lượng cho Job #${id} "${j.topic}" (phút):`, cur)
  if (!inp || isNaN(+inp) || +inp <= 0 || +inp === cur) return
  try {
    await api(`/projects/${id}/duration`, { minutes: +inp })
    toast(`⏱ Job #${id}: Đã đặt thời lượng ${+inp} phút`, 'ok')
    loadJobs()
  } catch (err) { toast(err.message, 'err') }
})


// ---- Test tạo ảnh theo chủ đề ngay trên giao diện
let testPollTimer = null
$('#btnTestImage')?.addEventListener('click', async () => {
  const topic = ($('#testImageTopic')?.value || '').trim()
  if (!topic) return toast('Vui lòng nhập chủ đề để test', 'err')
  const btn = $('#btnTestImage')
  const box = $('#testImagePreviewBox')
  const img = $('#testImageImg')
  const promptEl = $('#testImagePrompt')
  const statusEl = $('#testImageStatus')
  const openFolderBtn = $('#btnOpenTestFolder')

  btn.disabled = true
  btn.textContent = '⏳ Đang gửi...'
  if (box) box.style.display = 'block'
  if (statusEl) statusEl.textContent = 'Đang xếp task vào Flow Bridge...'
  if (img) img.style.display = 'none'
  if (promptEl) promptEl.style.display = 'none'

  try {
    const res = await api('/test_image', { topic })
    toast(res.msg || 'Đã gửi prompt test vào Flow Bridge', 'ok')
    if (promptEl) {
      promptEl.textContent = 'Prompt: ' + res.prompt
      promptEl.style.display = 'block'
    }
    if (statusEl) statusEl.textContent = 'Đang chờ profile Chrome vẽ ảnh (Task #' + res.task_id + ')...'
    if (openFolderBtn) openFolderBtn.style.display = 'inline-block'

    if (testPollTimer) clearInterval(testPollTimer)
    testPollTimer = setInterval(async () => {
      try {
        const check = await api(`/test_image/status?task_id=${res.task_id}`, null, 'GET')
        if (check.status === 'done' && check.image_url) {
          clearInterval(testPollTimer)
          testPollTimer = null
          if (img) {
            img.src = check.image_url + '?t=' + Date.now()
            img.style.display = 'block'
          }
          if (statusEl) statusEl.innerHTML = '<span style="color:#4ade80">✔ Đã tạo xong! Lưu tại: ' + (check.saved_path || '') + '</span>'
          btn.disabled = false
          btn.textContent = '🎨 Test tạo ảnh Flow'
          toast('✔ Ảnh test đã tạo xong!', 'ok')
        } else if (check.status === 'error') {
          clearInterval(testPollTimer)
          testPollTimer = null
          if (statusEl) statusEl.innerHTML = '<span style="color:#f87171">❌ Lỗi: ' + (check.error || 'không tạo được ảnh') + '</span>'
          btn.disabled = false
          btn.textContent = '🎨 Test tạo ảnh Flow'
          toast('Lỗi tạo ảnh: ' + (check.error || ''), 'err')
        }
      } catch (e) {}
    }, 2500)
  } catch (err) {
    toast('Lỗi test tạo ảnh: ' + err.message, 'err')
    btn.disabled = false
    btn.textContent = '🎨 Test tạo ảnh Flow'
    if (statusEl) statusEl.textContent = 'Lỗi: ' + err.message
  }
})

$('#btnOpenTestFolder')?.addEventListener('click', async () => {
  try {
    await api('/test_image/open_folder', {})
  } catch (err) {
    toast('Không mở được thư mục: ' + err.message, 'err')
  }
})
