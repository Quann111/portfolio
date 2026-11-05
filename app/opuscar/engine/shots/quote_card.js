// shot quote_card: panel tối giữa khung, ngoặc kép lớn, câu trích + nguồn nhỏ mono
import * as C from '../core.js';
import { U } from '../looks/dark-keynote.js';
import { ss, seg, back } from '/core/lib.js';
import { planTexts } from './_common.js';

export function plan(scene, look, W, H, env) {
  const z = C.zone(W, H, look), u = U(W, H);
  const v = C.isPortrait(W, H), B = v ? 1.25 : 1; // dọc: câu trích chính to hơn
  const items = [];
  let qFit = null, aFit = null;
  if (scene.title && scene.title.trim()) {
    qFit = C.fitted(scene, 'title', look,
      { W, H, fam: 'ui', wt: 500, text: scene.title, maxW: v ? C.centerSpan(W, H, look) : W * 0.6, maxH: H * 0.3, size: 56 * u * B, minSize: 24 * u, lineH: 1.42, maxLines: 4 });
  }
  if (scene.text && scene.text.trim()) {
    aFit = C.fitted(scene, 'text', look,
      { W, H, fam: 'mono', wt: 500, text: scene.text, maxW: v ? Math.min(W * 0.64, z.x1 - z.x0) : W * 0.56, maxH: v ? H * 0.1 : H * 0.08, size: v ? Math.max(26 * u, W * 0.037) : 25 * u, minSize: v ? W * 0.036 : 14 * u, lineH: 1.25, maxLines: 2, cls: 'aux' });
  }
  const padX = 92 * u, padTop = 84 * u, padBot = 60 * u;
  const pw = Math.max(v ? Math.min(W * 0.82, C.centerSpan(W, H, look)) : Math.min(W * 0.66, z.x1 - z.x0),
    (qFit ? qFit.w : 0) + padX * 2 * 0.5 + 60 * u);
  const innerH = (qFit ? qFit.h : 0) + (aFit ? aFit.h + 30 * u : 0);
  const ph = Math.max(H * 0.22, innerH + padTop + padBot);
  const px = W / 2 - pw / 2, py = (z.y0 + z.y1) / 2 - ph / 2;
  if (qFit) {
    const qx = px + 62 * u + (padX * 0.2), qy = py + padTop + 44 * u;
    items.push({ key: 'title', text: scene.title, fit: qFit, x: qx + qFit.w / 2, y: qy, align: 'center', tIn: 0.5, inDur: look.ti.textIn + Math.min(0.9, qFit.lines.length * 0.2), color: look.t1, fam: 'ui', wt: 500, qx });
  }
  if (aFit) {
    // nguồn: thẳng dưới câu trích, trong panel, kèm gạch nhấn
    items.push({ key: 'text', text: scene.text, fit: aFit, x: W / 2, y: py + ph - padBot - aFit.h + 14 * u, align: 'center', tIn: (qFit ? 0.5 + look.ti.textIn + Math.min(0.9, qFit.lines.length * 0.2) + 0.35 : 1.2), inDur: look.ti.textIn, color: look.t3, fam: 'mono', wt: 500 });
  }
  return { items, panel: { x: px, y: py, w: pw, h: ph } };
}

export function draw(ctx, scene, lt, dur, look, env, W, H) {
  const p = plan(scene, look, W, H, env), u = U(W, H);
  // panel vào: scale 0.97→1 mở nhanh dừng khớp
  const ps = ss(seg(lt, 0.22, 0.6));
  const pb = back(seg(lt, 0.22, 0.72), 1.3);
  if (ps > 0) {
    ctx.save();
    ctx.translate(W / 2, (p.panel.y + p.panel.h) / 2);
    ctx.scale(0.965 + 0.035 * pb, 0.955 + 0.045 * pb);
    ctx.translate(-W / 2, -(p.panel.y + p.panel.h) / 2);
    ctx.globalAlpha = ps;
    look.panel(ctx, p.panel.x, p.panel.y, p.panel.w, p.panel.h, null, 1, 0.92);
    // ngoặc kép lớn trang trí
    ctx.fillStyle = 'rgba(255,255,255,0.13)';
    const gf = C.fitted(scene, 'title', look, { W, H, fam: 'ui', wt: 700, text: scene.title || ' ', maxW: W, maxH: H, size: 56 * u, minSize: 24 * u });
    C.tex(ctx, look, 'big', 700, -0.05, Math.max(90 * u, gf.size * 2.2));
    ctx.fillText('\u201C', p.panel.x + 30 * u, p.panel.y + Math.max(90 * u, gf.size * 2.2) * 0.72);
    C.resetLs(ctx);
    // gạch nhấn dưới panel trái
    ctx.fillStyle = look.accent;
    ctx.fillRect(p.panel.x + p.panel.w - 150 * u, p.panel.y + p.panel.h - 10 * u, 96 * u, Math.max(2, 3 * u));
    ctx.restore();
  }
  let accIdxShift = 0;
  for (const it of p.items) {
    C.drawFitted(ctx, it.fit, {
      x: it.x, y: it.y, align: it.align, look, fam: it.fam, wt: it.wt, color: it.color,
      lt, tIn: it.tIn, inDur: Math.max(0.01, it.inDur), rise: 14 * u,
    });
  }
}

export const texts = (s, look, W, H, env) => planTexts(plan(s, look, W, H, env));
