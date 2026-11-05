// opuscar engine — core: timeline, camera world→screen, layout chữ, phụ đề,
// chuyển cảnh, EV, TEXTS. Tất định tuyệt đối: seed từ scene.id, không Date.now(),
// không Math.random() có seed; render(t) không phụ thuộc khung trước.
import { clamp, lerp, seg, ss, back, spring, vnoise } from '/core/lib.js';
import lookDarkKeynote, { SHORT_SAFE, isPortrait } from './looks/dark-keynote.js';
import * as SHOTS from './shots/index.js';

// re-export lib helpers để shots dùng lại một chỗ
export { clamp, lerp, seg, ss, back, spring, vnoise };
// vùng an toàn Shorts + phép thử khung dọc: ĐỊNH NGHĨA một chỗ (look), dùng chung qua đây
export { SHORT_SAFE, isPortrait };

// —— hệ số cỡ chữ "chữ chính" cho khung dọc: tiêu đề/số liệu chính phải to hơn
// tương ứng để đọc trên điện thoại. sàn chữ phụ (aux/axis) KHÔNG dùng — nó vẫn
// theo S=min(W,H) qua look.minFont (phần 3).
export const textBoost = (W, H) => (isPortrait(W, H) ? 1.25 : 1);

// bề rộng tối đa cho một khối CHỮ CĂN GIỮA trong khung dọc: kẹp biên phải vào
// cột an toàn 10% của YouTube. Khung ngang trả về bề rộng vùng nội dung (như cũ).
export function centerSpan(W, H, look) {
  const z = zone(W, H, look);
  return isPortrait(W, H) ? Math.max(120, 2 * (z.x1 - W / 2) - 24) : z.x1 - z.x0;
}

export const LOOKS = { 'dark-keynote': lookDarkKeynote };
export const getLook = name => LOOKS[name] || lookDarkKeynote;

// ————— hash & seed (FNV-1a 32 bit) —————
export function hashStr(s) {
  let h = 0x811c9dc5 | 0;
  for (const c of String(s)) {
    const cp = c.codePointAt(0);
    h ^= cp & 0xff; h = Math.imul(h, 0x01000193);
    h ^= cp >>> 8;  h = Math.imul(h, 0x01000193);
  }
  return h >>> 0;
}
export const seedOf = s => { const k = '_seed'; if (s[k] === undefined) s[k] = hashStr(s.id || s.shot || 's'); return s[k]; };
// chuỗi tất định bám scene.id (mulberry, dùng bản cục bộ để không lệ thuộc trạng thái chung)
export function rand(s) {
  let v = seedOf(s) | 0;
  return function () {
    v = v + 0x6D2B79F5 | 0;
    let T = Math.imul(v ^ v >>> 15, 1 | v);
    T = T + Math.imul(T ^ T >>> 7, 61 | T) ^ T;
    return ((T ^ T >>> 14) >>> 0) / 4294967296;
  };
}
export const frac = x => x - Math.floor(x);

// ————— timeline —————
export const sortScenes = SC => SC.slice().sort((a, b) => a.t0 - b.t0);
export const lastT1 = SC => (SC.length ? Math.max(...SC.map(x => x.t1)) : 0);
export function sceneAt(SC, t) {
  if (!SC.length) return null;
  let cur = SC[0];
  for (const s of SC) { if (t >= s.t0 && t < s.t1) return s; if (s.t0 <= t) cur = s; }
  return SC[SC.length - 1];
}
export function prevOf(SC, cur) {
  let p = null;
  for (const s of SC) if (s.t1 <= cur.t0 + 1e-9 && (!p || s.t1 > p.t1)) p = s;
  return p;
}
export const durOf = s => Math.max(0.01, (s.t1 ?? 1) - (s.t0 ?? 0));
export const clampLt = (lt, dur) => clamp(lt, 0, dur);

// ————— camera world→screen —————
// cam = {z, x, y}: zoom quanh tâm khung + pan theo pixel "thế giới".
export function camera(scene, lt, dur, W, H) {
  const p = dur > 0 ? clamp(lt / dur) : 0;
  const k = seedOf(scene);
  const drift = (f, w) => (vnoise(lt * 0.55 + f * 997 + k * 1e-3) - 0.5) * w;
  let z = 1.06, x = 0.008 * W * drift(1, 1), y = 0.010 * H * drift(2, 1);
  const kx = 0.05 * W, ky = 0.06 * H;
  switch (scene.camera || 'static') {
    case 'push': z = lerp(1.03, 1.17, ss(p)); break;
    case 'pull': z = lerp(1.17, 1.03, ss(p)); break;
    case 'pan_left': z = 1.12; x = lerp(-kx, kx, ss(p)); break;
    case 'pan_right': z = 1.12; x = lerp(kx, -kx, ss(p)); break;
    case 'tilt': z = 1.13; y = lerp(-ky, ky, ss(p)); break;
    default: z = 1.05 + 0.012 * drift(3, 1);
  }
  return { z, x, y };
}
export function applyCam(ctx, cam, W, H) {
  ctx.translate(W / 2, H / 2); ctx.scale(cam.z, cam.z); ctx.translate(-W / 2 - cam.x, -H / 2 - cam.y);
}
export const worldToScreen = (cam, W, H, wx, wy) => ({
  x: (wx - cam.x - W / 2) * cam.z + W / 2,
  y: (wy - cam.y - H / 2) * cam.z + H / 2,
});

// camera cho khung DỌC (dùng cho ảnh ngang cover-fit vào khung dọc, vd hero_image):
// ảnh ngang bị xén hai bên mạnh nên pan ngang nhanh sẽ lệch chủ thể (giữa ảnh)
// khỏi tâm — hãm biên độ pan x, giữ pan dọc tự nhiên. Khung ngang không dùng.
export function cameraPortrait(scene, lt, dur, W, H) {
  const c = camera(scene, lt, dur, W, H);
  c.x = clamp(c.x, -W * 0.014, W * 0.014);
  c.y = clamp(c.y, -H * 0.045, H * 0.045);
  return c;
}

// ————— đo chữ: co cỡ chữ + ngắt dòng tiếng Việt (cắt từ; từ dài quá vạch thì cắt cứng theo ký tự) —————
let meas = null;
export function mctx() {
  if (!meas) { meas = document.createElement('canvas'); meas.width = meas.height = 16; meas._g = meas.getContext('2d'); }
  return meas._g;
}
export function tex(g, look, fam, wt, trackEm, size) {
  g.font = `${wt} ${Math.max(8, size)}px "${look.fonts[fam] || fam}", sans-serif`;
  g.letterSpacing = `${(trackEm ? trackEm * size : 0).toFixed(2)}px`;
}
export const resetLs = c => { c.letterSpacing = '0px'; };

// fitLines → {size, lines, lh, w, h}: co size đến khi khối ≤ maxH và ≤ maxLines dòng
export function fitLines(look, fam, wt, trackEm, text, o) {
  const g = mctx();
  const s0 = o.size;
  // sàn cỡ chữ theo look (cls 'aux'/'axis' — xén một chỗ trong look.minFont): chỉ nâng, không hạ
  const floor = o.cls && look.minFont ? look.minFont(o.W, o.H, o.cls) : 0;
  const smin = Math.min(s0, Math.max(o.minSize ?? s0 * 0.5, floor)), lineH = o.lineH ?? 1.18, maxLines = o.maxLines ?? 99;
  const wordsAll = String(text ?? '').trim().split(/\s+/).filter(Boolean);
  if (!wordsAll.length) return { size: s0, lines: [], lh: s0 * lineH, w: 0, h: 0 };
  const wrapAt = s => {
    tex(g, look, fam, wt, trackEm, s);
    const lines = []; let cur = '';
    for (let wStr of wordsAll) {
      if (!cur && g.measureText(wStr).width > o.maxW) { // một từ dài hơn cả vạch: cắt cứng
        let rest = wStr;
        while (rest.length && g.measureText(rest).width > o.maxW) {
          let k = rest.length;
          while (k > 1 && g.measureText(rest.slice(0, k)).width > o.maxW) k--;
          lines.push(rest.slice(0, k)); rest = rest.slice(k);
        }
        cur = rest; continue;
      }
      const test = cur ? cur + ' ' + wStr : wStr;
      if (g.measureText(test).width <= o.maxW) cur = test;
      else { lines.push(cur); cur = wStr; }
    }
    if (cur) lines.push(cur);
    const lh = s * lineH;
    return { size: s, lines, lh, h: lines.length * lh, w: Math.max(1, ...lines.map(l => g.measureText(l).width)) };
  };
  let size = s0, best = null;
  while (size > smin) {
    const r = wrapAt(size);
    if (r.h <= o.maxH && r.lines.length <= maxLines) { best = r; break; }
    size -= Math.max(1, Math.round((s0 - smin) / 12));
  }
  if (!best) best = wrapAt(smin);
  tex(g, look, fam, wt, trackEm, Math.max(8, smin));
  return best;
}

// cache fit theo (khung, cảnh, key) → draw và TEXTS luôn cùng một box
const FIT = new Map();
export function fitted(scene, key, look, o) {
  const ck = `${o.W}x${o.H}|${scene.id}|${key}`;
  let f = FIT.get(ck);
  if (!f) { f = fitLines(look, o.fam, o.wt, o.trackEm ?? 0, o.text, o); FIT.set(ck, f); }
  return f;
}
export const clearFitCache = () => FIT.clear();

// ————— vùng nội dung: chừa vùng phụ đề dưới đáy, chữ trên hình không đụng vào —————
// Khung dọc: chừa thêm vùng an toàn Shorts — 12% trên, cột 10% bên phải
// (SHORT_SAFE); không đổi gì cho khung ngang.
export function zone(W, H, look) {
  const cap = look.captionZone(W, H);
  const v = isPortrait(W, H);
  return {
    x0: W * 0.072,
    y0: v ? H * SHORT_SAFE.top : H * 0.075,
    x1: v ? Math.min(W * 0.928, W * (1 - SHORT_SAFE.right)) : W * 0.928,
    y1: H - cap - H * 0.045,
    W, H, cap,
  };
}

// ————— khối chữ đã fit: mỗi từ rise+fade theo lag —————
// o: {x, y(top), align, look, fam, wt, trackEm, color, lt, tIn, inDur, rise, alpha, accIdx, accColor}
export function drawFitted(ctx, fit, o) {
  const look = o.look, rise = o.rise ?? 18;
  tex(ctx, look, o.fam || 'ui', o.wt ?? 'head', o.trackEm || 0, fit.size);
  ctx.textBaseline = 'alphabetic';
  // whole: MOT khoi hien len nguyen ven (fade+rise chung) - khong co trang thai "nua tu"
  // giữa chừng. Dành cho khung dọc (chapter_card) để tiêu đề không bao giờ hiện dở.
  if (o.whole) {
    const a = seg(o.lt ?? 0, o.tIn ?? 0, (o.tIn ?? 0) + Math.max(0.01, o.inDur || 0.2));
    if (a > 0.001) {
      const dy = (1 - ss(a)) * rise;
      ctx.globalAlpha = (o.alpha ?? 1) * ss(a);
      ctx.fillStyle = o.color; // nguyên khối: một màu, không chữ nhấn từng từ
      for (let li = 0; li < fit.lines.length; li++) {
        const ln = fit.lines[li];
        const lw = ctx.measureText(ln).width;
        const lx = o.align === 'center' ? o.x - lw / 2 : o.align === 'right' ? o.x - lw : o.x;
        ctx.fillText(ln, lx, o.y + li * fit.lh + fit.size * 0.86 + dy);
      }
    }
    ctx.globalAlpha = 1; resetLs(ctx);
    return blockBox(fit, o);
  }
  const total = Math.max(1, fit.lines.reduce((n, l) => n + l.split(' ').length, 0));
  const wDur = Math.min(0.3, (o.inDur || 0.2) * 0.6);
  const stp = Math.max(0, Math.min(look.ti.word * 2.2, ((o.inDur || 0.2) - wDur) / total));
  let idx = 0;
  for (let li = 0; li < fit.lines.length; li++) {
    const ln = fit.lines[li];
    const lw = ctx.measureText(ln).width;
    const lx = o.align === 'center' ? o.x - lw / 2 : o.align === 'right' ? o.x - lw : o.x;
    const ly = o.y + li * fit.lh + fit.size * 0.86;
    let wx = lx;
    for (const wd of ln.split(' ')) {
      const adv = ctx.measureText(wd + ' ').width;
      const a = seg(o.lt ?? 0, o.tIn + idx * stp, o.tIn + idx * stp + wDur);
      if (a > 0.001) {
        ctx.globalAlpha = (o.alpha ?? 1) * ss(a);
        ctx.fillStyle = idx === o.accIdx ? (o.accColor || o.color) : o.color;
        ctx.fillText(wd, wx, ly + (1 - ss(a)) * rise);
      }
      wx += adv; idx++;
    }
  }
  ctx.globalAlpha = 1; resetLs(ctx);
  return blockBox(fit, o);
}
export function blockBox(fit, o) {
  const xL = o.align === 'center' ? o.x - fit.w / 2 : o.align === 'right' ? o.x - fit.w : o.x;
  return { x0: xL, y0: o.y, x1: xL + fit.w, y1: o.y + fit.h };
}

// ————— ảnh: entry {ok, el, natw, nath}; vẽ cover-fit vào rect —————
export function drawImage(ctx, img, x, y, w, h, alpha = 1) {
  if (!img || !img.ok) return false;
  const s = Math.max(w / img.natw, h / img.nath);
  const iw = img.natw * s, ih = img.nath * s;
  ctx.globalAlpha = alpha;
  ctx.drawImage(img.el, x - (iw - w) / 2, y - (ih - h) / 2, iw, ih);
  ctx.globalAlpha = 1;
  return true;
}
export const hasImg = img => !!(img && img.ok);

// ảnh nền qua camera; thiếu/lỗi ảnh → gradient của look, không crash.
// nền vẽ "tràn viền" (margin) để pan/zoom không làm lộ mép.
export function bgImage(ctx, scene, env, key, lt, dur, look, W, H, opt = {}) {
  const mg = opt.margin ?? 0.1;
  const tw = (opt.w ?? W) * (1 + mg * 2), th = (opt.h ?? H) * (1 + mg * 2);
  const tx = (opt.x ?? 0) - (opt.w ?? W) * mg, ty = (opt.y ?? 0) - (opt.h ?? H) * mg;
  ctx.save();
  if (!opt.noCam) applyCam(ctx, opt.cam || camera(scene, lt, dur, W, H), W, H);
  const ok = drawImage(ctx, env ? env.img(scene, key) : null, tx, ty, tw, th, opt.alpha ?? 1);
  if (!ok) look.fallback(ctx, W, H, opt.fbSeed ?? seedOf(scene));
  ctx.restore();
}

// ————— chuyển cảnh: thông tin tại thời điểm trong cảnh —————
export function transInfo(cur, prev, lt, look) {
  const type = cur.transition || 'cut';
  const d = look.trDur(type, cur.emphasis);
  if (!prev || type === 'cut' || !(d > 0.08) || lt > d) return null;
  return { type, p: clamp(lt / d), dir: frac(seedOf(cur) * 0.3711) < 0.5 ? 1 : -1 };
}
// hộp chữ qua transform của transition; trả {box, vis}
export function trBoxVis(tr, b, which, W, H) {
  if (!tr) return { box: b, vis: true };
  const k = ss(tr.p);
  const inFrame = bb => bb.x0 >= -2 && bb.y0 >= -2 && bb.x1 <= W + 2 && bb.y1 <= H + 2;
  const sl = s => Math.max(0, s) * (W + 80) - 40;
  if (which === 'prev') {
    if (tr.type === 'slide') {
      const dx = -tr.dir * 0.3 * W * 0.22 * k;
      const bb = { x0: b.x0 + dx, y0: b.y0, x1: b.x1 + dx, y1: b.y1 };
      return { box: bb, vis: inFrame(bb) };
    }
    if (tr.type === 'zoom_through') {
      const z = 1 + 0.34 * k;
      return { box: scaleBox(b, z, W, H), vis: true }; // đk tràn khung lọc bên ngoài
    }
    return { box: b, vis: true };
  }
  switch (tr.type) {
    case 'wipe': {
      const rw = Math.max(0, sl(k));
      const r0 = tr.dir > 0 ? 0 : W - rw, r1 = tr.dir > 0 ? rw : W;
      return { box: b, vis: b.x0 + 6 >= r0 && b.x1 - 6 <= r1 };
    }
    case 'light_sweep': {
      const front = -0.15 * W + k * 1.32 * W;
      return { box: b, vis: b.x1 <= front };
    }
    case 'slide': {
      const dx = tr.dir * 0.3 * W * (1 - k);
      const bb = { x0: b.x0 + dx, y0: b.y0, x1: b.x1 + dx, y1: b.y1 };
      return { box: bb, vis: inFrame(bb) };
    }
    case 'zoom_through': {
      const z = 1.34 - 0.34 * k;
      const bb = scaleBox(b, z, W, H);
      return { box: bb, vis: inFrame(bb) };
    }
    default: return { box: b, vis: true };
  }
}
export function scaleBox(b, z, W, H) {
  const f = (v, c) => c + (v - c) * z;
  return { x0: f(b.x0, W / 2), y0: f(b.y0, H / 2), x1: f(b.x1, W / 2), y1: f(b.y1, H / 2) };
}

// ————— phụ đề: dưới khung, tối đa 2 dòng, hiện ≥ nhìn; KHÔNG báo vào TEXTS (hợp đồng readcheck) —————
export function activeCues(scene, t) {
  const out = [];
  for (const c of scene.subtitle || []) if (c && c.text && t >= (c.t0 ?? 0) && t < (c.t1 ?? 0)) out.push(c);
  return out;
}
export function drawCaptions(ctx, scenes, t, look, W, H) {
  const u = Math.min(W, H) / 1080;
  const v = isPortrait(W, H);
  // đáy pill phụ đề: dọc = ngay trên vạch 20% đáy (băng nút của YouTube); ngang giữ y cũ
  const capBottom = v ? H * (1 - SHORT_SAFE.bottom) : H - H * 0.045;
  // pill căn giữa: kẹp biên phải vào cột an toàn 10% (ngang: chưa từng bị giới hạn này)
  const capMaxW = v ? Math.min(W * 0.74, 2 * (W * (1 - SHORT_SAFE.right) - W / 2 - 28 * u)) : W * 0.7;
  let cues = [];
  for (const s of scenes) if (s) for (const c of activeCues(s, t)) cues.push(c);
  if (!cues.length) return;
  cues = cues.slice(0, 2); // không bao giờ xếp chồng quá 2 dòng/cue
  cues.forEach((cue, si) => {
    const ea = ss(seg(t, cue.t0, cue.t0 + 0.16)); // vào kiểu toast: 0.16s rise + fade
    if (ea <= 0.002) return;
    const size = 38 * u;
    const f = fitted({ id: 'sub' }, hashStr(cue.text), look,
      { W, H, fam: 'ui', wt: 500, text: cue.text, maxW: capMaxW, maxH: 2 * size * 1.3, size, minSize: 22 * u, lineH: 1.3, maxLines: 2 });
    const dy = (1 - ea) * 14 * u;
    const padX = 30 * u, padY = 15 * u;
    const pw = f.w + padX * 2 + 30 * u, ph = f.h + padY * 2;
    const py1 = capBottom - si * (ph + 10 * u), py0 = py1 - ph;
    const px1 = W / 2 + pw / 2, px0 = W / 2 - pw / 2;
    ctx.save();
    ctx.globalAlpha = ea;
    ctx.shadowColor = 'rgba(0,0,0,0.5)'; ctx.shadowBlur = 20 * u; ctx.shadowOffsetY = 6 * u;
    ctx.beginPath(); roundRectPath(ctx, px0, py0 + dy, pw, ph, ph / 2);
    ctx.fillStyle = 'rgba(12,14,19,0.8)'; ctx.fill();
    ctx.shadowColor = 'rgba(0,0,0,0)';
    ctx.strokeStyle = 'rgba(255,255,255,0.14)'; ctx.lineWidth = 1;
    ctx.stroke();
    // chấm accent "đang nói"
    const dotA = 0.55 + 0.45 * (0.5 + 0.5 * Math.sin(t * 5.1 + seedOf(cue) * 7));
    ctx.beginPath(); ctx.arc(px0 + 30 * u, (py0 + py1) / 2 + dy, 5 * u, 0, Math.PI * 2);
    ctx.fillStyle = look.accent; ctx.globalAlpha = ea * dotA; ctx.fill(); ctx.globalAlpha = ea;
    ctx.fillStyle = '#EDF1F6';
    ctx.font = `500 ${size}px "${look.fonts.ui}", sans-serif`;
    ctx.textBaseline = 'alphabetic';
    ctx.letterSpacing = '0px';
    f.lines.forEach((ln, li) => {
      const lw = ctx.measureText(ln).width;
      ctx.fillText(ln, xSubText(px0, pw, lw), py0 + padY + dy + li * f.lh + size * 0.86);
    });
    ctx.restore();
  });
}
export function xSubText(px0, pw, lw) { return px0 + (pw - lw) / 2; } // căn giữa trong pill
function roundRectPath(ctx, x, y, w, h, r) {
  r = Math.min(r, w / 2, h / 2);
  ctx.moveTo(x + r, y);
  ctx.arcTo(x + w, y, x + w, y + h, r);
  ctx.arcTo(x + w, y + h, x, y + h, r);
  ctx.arcTo(x, y + h, x, y, r);
  ctx.arcTo(x, y, x + w, y, r);
  ctx.closePath();
}
export const seedOfCue = c => hashStr((c.text || '') + (c.t0 ?? 0));

// —————举起 buffers offscreen cho transitions chồng 2 cảnh —————
const POOL = { A: null, B: null, w: 0, h: 0 };
function bufs(W, H) {
  if (POOL.w !== W || POOL.h !== H) {
    for (const k of ['A', 'B']) { POOL[k] = document.createElement('canvas'); POOL[k].width = W; POOL[k].height = H; }
    POOL.w = W; POOL.h = H;
  }
  return POOL;
}
function drawOn(buf, fn) {
  const g = buf.getContext('2d');
  g.clearRect(0, 0, buf.width, buf.height);
  g.save(); fn(g); g.restore(); resetLs(g);
}

// ————— vẽ MỘT cảnh (gọi shot.draw; shot lạ → fallback chung) —————
export function drawScene(g, scene, lt, dur, look, env, W, H) {
  g.save();
  try {
    const sp = SHOTS[scene.shot];
    if (sp && sp.draw) sp.draw(g, scene, lt, dur, look, env, W, H);
    else genericScene(g, scene, lt, dur, look, env, W, H);
  } finally { g.restore(); resetLs(g); }
}
// shot type không nhận biết: vẫn dựng nền + title/text dưới independent của khung
function genericScene(g, scene, lt, dur, look, env, W, H) {
  const z = zone(W, H, look), u = Math.min(W, H) / 1080, B = textBoost(W, H);
  C_drawFit(g, scene, 'title', look, { W, H, fam: 'ui', wt: 700, text: scene.title, maxW: z.x1 - z.x0, maxH: H * 0.3, size: 88 * u * B, minSize: 28 * u, maxLines: 3 },
    { x: z.x0, y: z.y1 - H * 0.3, align: 'left', color: look.t1, lt, tIn: 0.35, inDur: look.ti.textIn + 0.2 });
  C_drawFit(g, scene, 'text', look, { W, H, fam: 'ui', wt: 500, text: scene.text, maxW: z.x1 - z.x0, maxH: H * 0.14, size: 38 * u * (isPortrait(W, H) ? 1.15 : 1), minSize: 22 * u, maxLines: 2 },
    { x: z.x0, y: z.y1 - H * 0.12, align: 'left', color: look.t2, lt, tIn: 0.8, inDur: look.ti.textIn });
}
function C_drawFit(g, scene, key, look, fo, do_) {
  const f = fitted(scene, key, look, fo);
  drawFitted(g, f, { ...do_, look, fam: fo.fam, wt: fo.wt, trackEm: fo.trackEm });
}

// ————— chuyển cảnh vẽ chồng —————
export function drawTransition(ctx, prev, cur, tr, lt, dur, look, env, W, H) {
  const pd = durOf(prev);
  const { A, B } = bufs(W, H);
  const pScene = () => drawScene(ctx, prev, pd, pd, look, env, W, H); // trạng thái kết thúc của cảnh trước
  const pBuf = () => drawOn(A, g => drawScene(g, prev, pd, pd, look, env, W, H));
  const cBuf = () => drawOn(B, g => drawScene(g, cur, lt, dur, look, env, W, H));
  const put = (buf, { alpha = 1, dx = 0, z = 1 } = {}) => {
    ctx.save();
    if (z !== 1) { ctx.translate(W / 2, H / 2); ctx.scale(z, z); ctx.translate(-W / 2, -H / 2); }
    if (dx) ctx.translate(dx, 0);
    ctx.globalAlpha = clamp(alpha, 0, 1);
    ctx.drawImage(buf, 0, 0);
    ctx.restore();
  };
  const k = ss(tr.p);
  switch (tr.type) {
    case 'wipe': {
      pScene();
      cBuf();
      const rw = Math.max(0, k * (W + 80) - 40);
      if (rw < W) {
        ctx.save();
        ctx.beginPath();
        if (tr.dir > 0) ctx.rect(0, 0, rw, H); else ctx.rect(W - rw, 0, rw, H);
        ctx.clip();
        put(B);
        ctx.restore();
        edgeGlow(ctx, tr.dir > 0 ? rw : W - rw, W, H, look, true);
      } else put(B);
      break;
    }
    case 'slide': {
      const dx = tr.dir * 0.3 * W * (1 - k), dout = tr.dir * 0.3 * W * 0.22 * k;
      pBuf(); cBuf();
      put(A, { dx: -dout });
      put(B, { dx });
      break;
    }
    case 'zoom_through': {
      pBuf(); cBuf();
      put(A, { z: 1 + 0.34 * k, alpha: 1 - 0.62 * k });
      put(B, { z: 1.34 - 0.34 * k, alpha: k });
      break;
    }
    case 'light_sweep': {
      pScene();
      cBuf();
      const front = -0.15 * W + k * 1.32 * W;
      ctx.save();
      ctx.beginPath(); ctx.rect(0, 0, Math.min(W + 1, Math.max(0, front)), H); ctx.clip();
      put(B);
      ctx.restore();
      lightBand(ctx, front, W, H, look);
      break;
    }
    default: pScene(); putB(); // cut hay lạ: dựa cẩn thận
  }
  function putB() { drawScene(ctx, cur, lt, dur, look, env, W, H); }
}

// dải sáng ở mép wipe: accent mềm + hairline
function edgeGlow(ctx, ex, W, H, look, hairline) {
  const bw = Math.max(40, W * 0.05);
  const acc = look.accentRGB || '183,243,74';
  ctx.save();
  ctx.globalAlpha = 0.5;
  const g0 = ctx.createLinearGradient(Math.max(0, ex - bw), 0, ex, 0);
  g0.addColorStop(0, 'rgba(255,255,255,0)'); g0.addColorStop(1, 'rgba(255,255,255,0.06)');
  ctx.fillStyle = g0;
  if (ex > 0) ctx.fillRect(Math.max(0, ex - bw), 0, Math.min(bw, ex), H);
  const g2 = ctx.createLinearGradient(ex, 0, ex + bw, 0);
  g2.addColorStop(0, `rgba(${acc},0.18)`); g2.addColorStop(1, `rgba(${acc},0)`);
  ctx.fillStyle = g2; ctx.fillRect(ex, 0, bw, H);
  if (hairline && ex > 1 && ex < W - 1) {
    ctx.globalAlpha = 0.8; ctx.strokeStyle = `rgba(${acc},0.6)`; ctx.lineWidth = 1.5;
    ctx.beginPath(); ctx.moveTo(ex, 0); ctx.lineTo(ex, H); ctx.stroke();
  }
  ctx.restore();
}
// dải quét sáng light_sweep: soft front, glint ≤20%
function lightBand(ctx, front, W, H, look) {
  if (front < -0.2 * W || front > 1.3 * W) return;
  const bw = W * 0.3, acc = look.accentRGB || '183,243,74';
  ctx.save();
  const g = ctx.createLinearGradient(front - bw / 2, 0, front + bw / 2, 0);
  g.addColorStop(0, 'rgba(255,255,255,0)');
  g.addColorStop(0.5, 'rgba(255,255,255,0.10)');
  g.addColorStop(0.72, `rgba(${acc},0.07)`);
  g.addColorStop(1, 'rgba(255,255,255,0)');
  ctx.fillStyle = g; ctx.fillRect(front - bw / 2, 0, bw, H);
  ctx.globalAlpha = 0.35; ctx.strokeStyle = `rgba(${acc},0.5)`; ctx.lineWidth = 1;
  if (front > 0 && front < W) { ctx.beginPath(); ctx.moveTo(front, 0); ctx.lineTo(front, H); ctx.stroke(); }
  ctx.restore();
}

// ————— render(t) —————
export function renderFilm(ctx, C, look, env, W, H) {
  // nền sân khấu luôn được vẽ trước → không bao giờ có khung đen/trống
  look.stage(ctx, W, H, C.stageSeed ?? 1);
  const cur = sceneAt(C.scenes, C.t);
  if (!cur) return;
  const dur = durOf(cur), lt = clampLt(C.t - cur.t0, dur);
  const prev = prevOf(C.scenes, cur);
  const tr = transInfo(cur, prev, lt, look);
  let capScenes = [cur];
  try {
    if (tr) {
      capScenes = [prev, cur];
      drawTransition(ctx, prev, cur, tr, lt, dur, look, env, W, H);
    } else {
      drawScene(ctx, cur, lt, dur, look, env, W, H);
    }
  } catch (e) { console.error('[engine] scene draw error at', cur.id, e && e.message); }
  try { drawCaptions(ctx, capScenes, C.t, look, W, H); }
  catch (e) { console.error('[engine] captions error', e && e.message); }
}

// ————— TEXTS(t): mọi chữ trên hình đang hiện + hộp bao (phụ đề KHÔNG báo theo hợp đồng readcheck) —————
export function texts(C, t, look, W, H, env) {
  const out = [];
  const cur = sceneAt(C.scenes, t);
  if (!cur) return out;
  const prev = prevOf(C.scenes, cur);
  const tr = transInfo(cur, prev, clampLt(t - cur.t0, durOf(cur)), look);
  const push = (scene, which) => {
    if (!scene) return;
    const sp = SHOTS[scene.shot];
    if (!sp || !sp.texts) return;
    for (const it of sp.texts(scene, look, W, H, env)) {
      const lt = which === 'prev' ? durOf(scene) : t - scene.t0;
      if (lt < (it.tIn ?? 0)) continue;
      const tv = trBoxVis(tr, it.box, which, W, H);
      if (!tv.vis) continue;
      const b = tv.box;
      if (!(b.x0 >= -1 && b.y0 >= -1 && b.x1 <= W + 1 && b.y1 <= H + 1)) continue;
      out.push({ id: scene.id + '.' + it.key, text: it.text, x0: b.x0, y0: b.y0, x1: b.x1, y1: b.y1 });
    }
  };
  push(prev, 'prev'); push(cur, 'cur');
  return out;
}

// ————— buildEV: whoosh / tick / paper / pop — thời điểm tính đứng trước từ look.ti —————
export function buildEV(C, look, W, H, env) {
  const ev = [];
  C.scenes.forEach((s, i) => {
    const type = s.transition || 'cut';
    if (i > 0 && type !== 'cut') ev.push({ t: s.t0, type: 'whoosh', shot: s.shot });
    // event riêng theo dạng shot
    if (s.shot === 'stat' && s.stat) {
      ev.push({ t: s.t0 + look.ti.countDelay + look.ti.countDur, type: 'tick', value: s.stat.value });
    }
    if (s.shot === 'chart' && s.chart) {
      const kind = s.chart.kind || 'bar';
      if (kind === 'line') ev.push({ t: s.t0 + look.ti.axis + look.ti.lineDur, type: 'tick' });
      else for (let j = 0; j < (s.chart.values || []).length; j++) {
        ev.push({ t: s.t0 + look.ti.axis + j * look.ti.barStep + look.ti.barIn, type: 'pop', i: j });
      }
    }
    // paper: mỗi khối chữ trên hình + mỗi cue phụ đề
    const sp = SHOTS[s.shot];
    if (sp && sp.texts) for (const it of sp.texts(s, look, W, H, env)) {
      ev.push({ t: s.t0 + (it.tIn ?? 0), type: 'paper', id: s.id + '.' + it.key });
    }
    for (const c of s.subtitle || []) if (c && c.text) ev.push({ t: c.t0 ?? 0, type: 'paper', id: s.id + '.sub' });
  });
  return ev.sort((a, b) => a.t - b.t);
}
