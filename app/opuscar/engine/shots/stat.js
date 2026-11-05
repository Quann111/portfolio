// shot stat: con số khổng lồ (Inter Tight 600, tracking -4.5%) đếm lên + nhãn + nguồn
import * as C from '../core.js';
import { U } from '../looks/dark-keynote.js';
import { ss, seg, eo } from '/core/lib.js';
import { planTexts } from './_common.js';

const fmtV = v => {
  const n = Number(v) || 0;
  if (Math.abs(n) >= 1000) return Math.round(n).toLocaleString('vi-VN'); // dấu phân tách ổn định
  return String(n);
};

export function plan(scene, look, W, H, env) {
  const z = C.zone(W, H, look), u = U(W, H);
  const v = C.isPortrait(W, H), B = v ? 1.25 : 1; // dọc: số/nhãn chính to hơn cho điện thoại
  const st = scene.stat || {};
  const numStr = fmtV(st.value);
  const unit = String(st.unit ?? '').trim();
  // dọc: chừa sẵn chỗ cho đơn vị để cả hàng số vẫn trong cột an toàn phải
  const numMaxW = v ? C.centerSpan(W, H, look) - (unit ? W * 0.12 : 0) : W * 0.62;
  const numFit = C.fitted(scene, 'num', look,
    { W, H, fam: 'big', wt: 600, trackEm: look.track.big, text: numStr, maxW: numMaxW, maxH: H * 0.34, size: 250 * u * B, minSize: 60 * u, lineH: 1.0, maxLines: 1 });
  const uSize = Math.max(30 * u, numFit.size * 0.42);
  const rowW = numFit.w + (unit ? uSize * 0.7 : 0);
  const rowY = (z.y0 + z.y1) / 2 - H * 0.72 * numFit.h / H; // hơi lệch lên trên cho nhãn
  let rowX = W / 2 - rowW / 2;
  if (v) rowX = Math.max(z.x0, Math.min(rowX, z.x1 - rowW)); // dọc: cả hàng trong vùng an toàn
  const rowY2 = (z.y0 + z.y1) / 2 - numFit.h * 0.6;
  const items = [];
  const numTIn = look.ti.countDelay + look.ti.countDur; // TEXTS chỉ báo số CHỐT (giá trị cuối) sau khi đếm xong
  items.push({ key: 'num', text: numStr, fit: numFit, x: rowX, y: rowY2, align: 'left', tIn: numTIn, inDur: 0.01, color: look.t1, fam: 'big', wt: 600, trackEm: look.track.big });
  let labelFit = null, srcFit = null;
  if (st.label && String(st.label).trim()) {
    labelFit = C.fitted(scene, 'label', look,
      { W, H, fam: 'ui', wt: 500, text: String(st.label), maxW: v ? C.centerSpan(W, H, look) : W * 0.56, maxH: H * 0.15, size: 36 * u * B, minSize: 20 * u, lineH: 1.3, maxLines: 2 });
    items.push({ key: 'label', text: String(st.label), fit: labelFit, x: W / 2, y: rowY2 + numFit.h + 34 * u, align: 'center', tIn: look.ti.countDelay - 0.05, inDur: look.ti.textIn, color: look.t2, fam: 'ui', wt: 500 });
  }
  if (st.source && String(st.source).trim()) {
    srcFit = C.fitted(scene, 'source', look,
      { W, H, fam: 'mono', wt: 500, text: String(st.source), maxW: v ? Math.min(W * 0.62, z.x1 - z.x0) : W * 0.62, maxH: H * 0.06, size: v ? Math.max(24 * u, W * 0.037) : 23 * u, minSize: v ? W * 0.036 : 15 * u, lineH: 1.2, maxLines: 1, cls: 'aux' });
    items.push({ key: 'source', text: String(st.source), fit: srcFit, x: W / 2, y: z.y1 - srcFit.h + 6 * u, align: 'center', tIn: look.ti.countDelay + look.ti.countDur + 0.15, inDur: look.ti.textIn, color: look.t3, fam: 'mono', wt: 500 });
  }
  return { items, numFit, rowX, rowY2, rowW, unit, uSize, val: Number(st.value) || 0, srcFit };
}

export function draw(ctx, scene, lt, dur, look, env, W, H) {
  const p = plan(scene, look, W, H, env), u = U(W, H);
  // vầng sáng accent sau số
  const gcx = W / 2, gcy = p.rowY2 + p.numFit.h / 2;
  let g = ctx.createRadialGradient(gcx, gcy, 0, gcx, gcy, p.numFit.w * 0.8);
  g.addColorStop(0, `rgba(${look.accentRGB},0.09)`); g.addColorStop(1, 'rgba(0,0,0,0)');
  ctx.fillStyle = g; ctx.fillRect(0, 0, W, H);

  // số đếm: 0 → value
  const q = seg(lt, look.ti.countDelay, look.ti.countDelay + look.ti.countDur);
  const cnt = Math.round(p.val * eo(clamp01(q)));
  const curStr = fmtV(cnt);
  C.tex(ctx, look, 'big', 600, look.track.big, p.numFit.size);
  ctx.textBaseline = 'alphabetic';
  ctx.fillStyle = look.t1;
  ctx.fillText(curStr, p.rowX, p.rowY2 + p.numFit.size * 0.86);
  const curW = ctx.measureText(curStr).width;
  C.resetLs(ctx);

  // đơn vị: hiện cùng lúc bắt đầu đếm
  if (p.unit) {
    const ua = ss(seg(lt, look.ti.countDelay * 0.6, look.ti.countDelay * 0.6 + 0.4));
    C.tex(ctx, look, 'big', 600, look.track.big, p.uSize);
    ctx.globalAlpha = ua;
    ctx.fillStyle = look.accent;
    ctx.fillText(p.unit, p.rowX + curW + p.uSize * 0.22, p.rowY2 + p.numFit.size * 0.86);
    ctx.globalAlpha = 1;
    C.resetLs(ctx);
  }

  // gạch nhấn dưới số khi đếm xong
  const done = lt >= look.ti.countDelay + look.ti.countDur;
  if (done) {
    const dw = ss(seg(lt, look.ti.countDelay + look.ti.countDur, look.ti.countDelay + look.ti.countDur + 0.4));
    ctx.fillStyle = look.accent; ctx.globalAlpha = dw;
    ctx.fillRect(gcx - 40 * u * dw, p.rowY2 + p.numFit.h + 10 * u, 80 * u * dw, Math.max(2, 3 * u));
    ctx.globalAlpha = 1;
  }

  // nhãn + nguồn
  for (const key of ['label', 'source']) {
    const it = p.items.find(x => x.key === key);
    if (!it) continue;
    C.drawFitted(ctx, it.fit, {
      x: it.x, y: it.y, align: it.align, look, fam: it.fam, wt: it.wt, color: it.color,
      lt, tIn: it.tIn, inDur: it.inDur, rise: 14 * u,
    });
  }
}
const clamp01 = x => Math.max(0, Math.min(1, x));

export const texts = (s, look, W, H, env) => planTexts(plan(s, look, W, H, env));
