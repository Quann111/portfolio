// shot timeline: trục ngang mọc dần, mỗi event một năm + nhãn (dot pop)
import * as C from '../core.js';
import { U } from '../looks/dark-keynote.js';
import { ss, seg, spring } from '/core/lib.js';
import { planTexts } from './_common.js';

export function plan(scene, look, W, H, env) {
  const z = C.zone(W, H, look), u = U(W, H);
  const v = C.isPortrait(W, H);
  const evs = (scene.events || []).filter(e => e && e.label).map(e => ({ year: String(e.year ?? ''), label: String(e.label) }));
  const items = [];
  if (scene.title && scene.title.trim()) {
    // dọc: tiêu đề to hơn (textBoost), chiếm bề rộng vùng an toàn
    const B = v ? 1.25 : 1;
    const tf = C.fitted(scene, 'title', look,
      { W, H, fam: 'ui', wt: 700, text: scene.title, maxW: v ? (z.x1 - z.x0) : W * 0.58, maxH: H * 0.14, size: 48 * u * B, minSize: 24 * u, lineH: 1.1, maxLines: 2 });
    items.push({ key: 'title', text: scene.title, fit: tf, x: z.x0, y: z.y0, align: 'left', tIn: 0.3, inDur: look.ti.textIn, color: look.t1, fam: 'ui', wt: 700 });
  }
  if (v) {
    // —— dọc: trục đứng, mỗi event một nấc (năm trên chấm, nhãn dưới chấm) ——
    // mốc giãn hết chiều cao khả dụng: trục kéo sát vùng an toàn trên và chạm
    // sâu xuống gần băng phụ đề (tổng ≥60% chiều cao vùng an toàn, thực tế ~80%)
    const ax = z.x0 + W * 0.075;
    const yTop = z.y0 + H * 0.045, yBot = Math.max(z.y0 + H * 0.6, Math.min(H * 0.72, z.y1 + H * 0.055));
    const step = evs.length ? (yBot - yTop) / evs.length : 0;
    const tx = ax + 38 * u;                       // chữ lệch phải trục, hết cột an toàn trái
    const maxW = Math.max(60 * u, z.x1 - tx);
    const eg = [];
    evs.forEach((e, i) => {
      const y = yTop + step * (i + 0.5);
      const yf = e.year ? C.fitted(scene, 'year' + i, look,
        { W, H, fam: 'big', wt: 600, trackEm: -0.01, text: e.year, maxW: maxW * 0.6, maxH: H * 0.08, size: v ? 60 * u : 46 * u, minSize: 20 * u, lineH: 1.05, maxLines: 1, cls: 'axis' }) : null;
      const lf = C.fitted(scene, 'ev' + i, look,
        { W, H, fam: 'ui', wt: 500, text: e.label, maxW, maxH: Math.max(40 * u, step - (26 + 30) * u - 8 * u), size: v ? Math.max(28 * u, W * 0.039) : 27 * u, minSize: 16 * u, lineH: 1.25, maxLines: 3, cls: 'mile' });
      const tIn = look.ti.axis + i * look.ti.evGap;
      if (yf) items.push({ key: 'year' + i, text: e.year, fit: yf, x: tx, y: y - 10 * u - yf.h - 24 * u, align: 'left', tIn, inDur: look.ti.textIn, color: look.t1, fam: 'big', wt: 600, trackEm: -0.01 });
      items.push({ key: 'ev' + i, text: e.label, fit: lf, x: tx, y: y + 26 * u, align: 'left', tIn: tIn + 0.08, inDur: look.ti.textIn, color: look.t2, fam: 'ui', wt: 500 });
      eg.push({ x: ax, y, tIn, i });
    });
    return { items, eg, v, ax, yTop, yBot, zone: z };
  }
  // —— ngang: không đổi ——
  const x0 = z.x0 + W * 0.05, x1 = z.x1 - W * 0.06;
  const step = evs.length ? (x1 - x0) / evs.length : 0;
  const axisY = (z.y0 + z.y1) / 2 + H * 0.02;
  const eg = [];
  evs.forEach((e, i) => {
    const x = x0 + step * (i + 0.5);
    const yf = e.year ? C.fitted(scene, 'year' + i, look,
      { W, H, fam: 'big', wt: 600, trackEm: -0.01, text: e.year, maxW: step * 0.94, maxH: H * 0.08, size: 46 * u, minSize: 20 * u, lineH: 1.05, maxLines: 1, cls: 'axis' }) : null;
    const lf = C.fitted(scene, 'ev' + i, look,
      { W, H, fam: 'ui', wt: 500, text: e.label, maxW: step * 0.92, maxH: z.y1 - (axisY + 34 * u) - 8 * u, size: 29 * u, minSize: 16 * u, lineH: 1.25, maxLines: 4 });
    const tIn = look.ti.axis + i * look.ti.evGap;
    if (yf) items.push({ key: 'year' + i, text: e.year, fit: yf, x, y: axisY - 30 * u - yf.h, align: 'center', tIn, inDur: look.ti.textIn, color: look.t1, fam: 'big', wt: 600, trackEm: -0.01 });
    items.push({ key: 'ev' + i, text: e.label, fit: lf, x, y: axisY + 34 * u, align: 'center', tIn: tIn + 0.08, inDur: look.ti.textIn, color: look.t2, fam: 'ui', wt: 500 });
    eg.push({ x, tIn, i });
  });
  return { items, eg, v, axisY, x0, x1, zone: z };
}

export function draw(ctx, scene, lt, dur, look, env, W, H) {
  const p = plan(scene, look, W, H, env), u = U(W, H);
  const axA = ss(seg(lt, 0.1, look.ti.axis));
  if (axA > 0) {
    ctx.save();
    ctx.globalAlpha = axA;
    ctx.strokeStyle = 'rgba(255,255,255,0.22)'; ctx.lineWidth = Math.max(1, 1.5 * u);
    if (p.v) {
      // dọc: trục rũ xuống từ trên, mũi tên accent chĩa xuống dưới
      ctx.beginPath(); ctx.moveTo(p.ax, p.yTop); ctx.lineTo(p.ax, p.yTop + (p.yBot - p.yTop) * axA); ctx.stroke();
      if (axA >= 1) {
        ctx.fillStyle = look.accent;
        const ay = p.yBot + 8 * u;
        ctx.beginPath(); ctx.moveTo(p.ax - 7 * u, ay); ctx.lineTo(p.ax, ay + 10 * u); ctx.lineTo(p.ax + 7 * u, ay); ctx.closePath(); ctx.fill();
      }
      ctx.restore();
    } else {
      ctx.beginPath(); ctx.moveTo(p.x0, p.axisY); ctx.lineTo(p.x0 + (p.x1 - p.x0) * axA, p.axisY); ctx.stroke();
      // mũi tên nhỏ accent ở đầu trục
      if (axA >= 1) {
        ctx.fillStyle = look.accent;
        const ax = p.x1 + 8 * u;
        ctx.beginPath(); ctx.moveTo(ax, p.axisY - 7 * u); ctx.lineTo(ax + 10 * u, p.axisY); ctx.lineTo(ax, p.axisY + 7 * u); ctx.closePath(); ctx.fill();
      }
      ctx.restore();
    }
  }
  for (const d of p.eg) {
    const t0 = d.tIn;
    const sp = spring(seg(lt, t0, t0 + 0.55), 7, 0.32);
    if (sp <= 0.001) continue;
    const r = 9 * u * Math.max(0, sp);
    const dx = d.x, dy = p.v ? d.y : p.axisY;
    ctx.save();
    // vòng ring nhạt
    ctx.strokeStyle = 'rgba(255,255,255,0.25)'; ctx.lineWidth = 1.5;
    ctx.beginPath(); ctx.arc(dx, dy, r + 5 * u, 0, Math.PI * 2); ctx.stroke();
    // chấm accent
    ctx.fillStyle = look.accent;
    ctx.shadowColor = `rgba(${look.accentRGB},0.5)`; ctx.shadowBlur = 14 * u;
    ctx.beginPath(); ctx.arc(dx, dy, Math.max(0.5, r), 0, Math.PI * 2); ctx.fill();
    ctx.restore();
  }
  for (const it of p.items) {
    C.drawFitted(ctx, it.fit, {
      x: it.x, y: it.y, align: it.align, look, fam: it.fam, wt: it.wt, color: it.color,
      lt, tIn: it.tIn, inDur: Math.max(0.01, it.inDur), rise: 14 * u, trackEm: it.trackEm,
    });
  }
}

export const texts = (s, look, W, H, env) => planTexts(plan(s, look, W, H, env));
