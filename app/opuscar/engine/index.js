// Bootstrap engine opuscar: nạp content.json → look → fonts → ảnh xong mới READY.
// Khai báo window.DUR / window.render(t) / window.EV / window.TEXTS(t).
import * as C from './core.js';

const cvs = document.getElementById('film') || document.createElement('canvas');
if (!cvs.id) { cvs.id = 'film'; document.body.appendChild(cvs); }
const ctx = cvs.getContext('2d');
let W = 0, H = 0;
const debugSafe = new URLSearchParams(location.search).get('safe') === '1'; // chỉ overlay dò vùng an toàn
function resize() {
  W = Math.max(16, Math.floor(window.innerWidth));
  H = Math.max(16, Math.floor(window.innerHeight));
  if (cvs.width !== W) cvs.width = W;
  if (cvs.height !== H) cvs.height = H;
  C.clearFitCache();
}

// ——— trạng thái film ———
const CNT = { scenes: [], t: 0, dur: 0, stageSeed: 1, ready: false, look: null, format: 'long' };

// DEBUG (?safe=1): phác khung vùng an toàn Shorts lên ảnh (chỉ phục vụ vẫn tra
// still, KHÔNG dùng khi render video thật — không truyền ?safe=1 là không có).
function drawSafeOverlay(g, W, H) {
  const top = H * C.SHORT_SAFE.top, bot = H * (1 - C.SHORT_SAFE.bottom), xr = W * (1 - C.SHORT_SAFE.right);
  g.save();
  g.setLineDash([14, 10]);
  g.strokeStyle = 'rgba(255,90,69,0.95)'; g.lineWidth = 2;
  g.strokeRect(0, top, xr, bot - top);
  g.strokeStyle = 'rgba(255,90,69,0.5)';
  g.beginPath(); g.moveTo(xr, 0); g.lineTo(xr, H); g.stroke();
  g.beginPath(); g.moveTo(0, top); g.lineTo(W, top); g.stroke();
  g.beginPath(); g.moveTo(0, bot); g.lineTo(W, bot); g.stroke();
  g.restore();
}

// ——— fonts OFL tự host ———
const FONTS = [
  ['Inter', 'Inter.ttf', '100 900'],
  ['Inter Tight', 'InterTight.ttf', '100 900'],
  ['JetBrains Mono', 'JetBrainsMono.ttf', '100 800'],
];
async function loadFonts() {
  await Promise.allSettled(FONTS.map(async ([fam, file, weight]) => {
    try {
      const url = new URL('fonts/' + file, import.meta.url).href;
      const face = new FontFace(fam, `url(${url})`, { weight });
      await face.load();
      document.fonts.add(face);
    } catch (e) { console.error('[engine] font load failed:', fam, e && e.message); }
  }));
}

// ——— ảnh: đường dẫn trong content.json là tương đối so với content.json ———
const IMGS = new Map(); // URL tuyệt đối → entry {ok, el, natw, nath}
function loadImg(url) {
  if (IMGS.has(url)) return;
  IMGS.set(url, { ok: false });
  const im = new Image();
  im.onload = () => IMGS.set(url, { ok: true, el: im, natw: im.naturalWidth || im.width, nath: im.naturalHeight || im.height });
  im.onerror = () => { IMGS.set(url, { ok: false }); console.error('[engine] image missing (dùng nền gradient của look):', url); };
  im.src = url;
}
const IMG_ENV = {
  img(scene, key) { return IMGS.get(scene[key]) || null; },
};

async function init() {
  // vẽ khung nền ngay trước khi có dữ liệu (không để trắng/xanh trống)
  resize();
  C.renderFilm(ctx, CNT, C.getLook('dark-keynote'), IMG_ENV, W, H);
  try {
    const q = new URLSearchParams(location.search).get('content') || 'content.json';
    const contentURL = new URL(q, location.href);
    const res = await fetch(contentURL.href);
    if (!res.ok) throw new Error('content HTTP ' + res.status);
    const data = await res.json();
    // "format": "long" | "short" — trường gốc TUỲ CHỌN, mặc định long. Engine KHÔNG
    // rẽ nhánh bố cục theo nó: mọi chiều dọc/đặt chữ đều suy từ tỉ lệ khung thực
    // (innerWidth/innerHeight, tool vendor truyền --size). Chỉ lưu lại để tag.
    CNT.format = data.format === 'short' ? 'short' : 'long';
    if (data.format && CNT.format !== String(data.format)) {
      console.warn('[engine] format lạ:', data.format, '→ coi như long (bố cục vẫn theo tỉ lệ khung)');
    }
    const look = C.getLook(data.look);

    // chuẩn hoá cảnh: t0<t1 hợp lệ, hạt giống sẽ gắn khi dùng
    CNT.scenes = C.sortScenes((data.scenes || []).filter(s => s && (s.t1 ?? 0) > (s.t0 ?? 0) && s.shot).map(s => {
      const c = { ...s, subtitle: Array.isArray(s.subtitle) ? s.subtitle.slice() : [], stat: s.stat ? { ...s.stat } : null, chart: s.chart ? { ...s.chart } : null, events: Array.isArray(s.events) ? s.events.slice() : null };
      return c;
    }));
    CNT.dur = C.lastT1(CNT.scenes);
    CNT.stageSeed = C.hashStr('stage|' + String(data.lang || 'vi')) || 1;
    CNT.look = look;

    // nạp song song fonts + ảnh
    const jobs = [];
    for (const s of CNT.scenes) {
      for (const k of ['image', 'image2']) {
        const p = s[k];
        if (!p || typeof p !== 'string') { s[k] = null; continue; }
        s[k] = new URL(p, contentURL).href;
        jobs.push(loadImg(s[k]));
      }
    }
    await Promise.all([loadFonts(), Promise.allSettled(jobs)]);

    C.clearFitCache();
    window.DUR = CNT.dur;
    window.FORMAT = CNT.format;
    window.EV = C.buildEV(CNT, look, W, H, IMG_ENV);
    window.render = t => {
      CNT.t = Math.max(0, Math.min(Number(t) || 0, CNT.dur));
      C.renderFilm(ctx, CNT, look, IMG_ENV, W, H);
      if (debugSafe) drawSafeOverlay(ctx, W, H);
    };
    window.TEXTS = t => {
      const tt = Math.max(0, Math.min(Number(t) || 0, CNT.dur));
      CNT.t = tt;
      return C.texts(CNT, tt, look, W, H, IMG_ENV);
    };
    CNT.ready = true;
    window.READY = true;
  } catch (e) {
    console.error('[engine] init failed:', e && e.message, e && e.stack);
    // vẫn READY + nền look; never hang, never blank
    window.render = () => { CNT.look = CNT.look || C.getLook('dark-keynote'); C.renderFilm(ctx, CNT, CNT.look, IMG_ENV, W, H); };
    window.DUR = 0; window.EV = []; window.TEXTS = () => [];
    window.READY = true;
  }
}
window.addEventListener('resize', () => { resize(); if (CNT.ready && window.render) window.render(CNT.t); });
init();
