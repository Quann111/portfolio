// shot chart: bar / line / compare — trục hairline, cột mọc từng cái (pop), giá trị mono
import * as C from '../core.js';
import { U } from '../looks/dark-keynote.js';
import { ss, seg, back, eo } from '/core/lib.js';
import { planTexts } from './_common.js';

function niceMax(v) {
  if (!(v > 0)) return 1;
  const e = Math.floor(Math.log10(v));
  const m = v / Math.pow(10, e);
  const n = m <= 1 ? 1 : m <= 2 ? 2 : m <= 5 ? 5 : 10;
  return n * Math.pow(10, e);
}
function fmtNum(v) {
  const n = Number(v) || 0;
  return Math.abs(n) >= 1000 ? n.toLocaleString('vi-VN') : String(n);
}

export function plan(scene, look, W, H, env) {
  const z = C.zone(W, H, look), u = U(W, H);
  const v = C.isPortrait(W, H), B = v ? 1.25 : 1; // dọc: tiêu đề chính to hơn
  const ch = scene.chart || {};
  const kind = ['bar', 'line', 'compare'].includes(ch.kind) ? ch.kind : 'bar';
  let labels = (ch.labels || []).map(x => String(x));
  let values = (ch.values || []).map(x => Number(x) || 0);
  if (v && labels.length > 6) { labels = labels.slice(0, 6); values = values.slice(0, 6); } // dọc: tối đa ~6 nhãn cho không chồng
  const n = values.length;
  const unit = String(ch.unit ?? '').trim();
  const items = [];
  // đơn vị (fit TRƯỚC để dọc: tiêu đề chừa bề rộng tránh đè lên đơn vị phải)
  let unitFit = null;
  if (unit) {
    unitFit = C.fitted(scene, 'unit', look,
      { W, H, fam: 'mono', wt: 500, text: unit, maxW: W * 0.2, maxH: H * 0.05, size: v ? W * 0.037 : 25 * u, minSize: v ? W * 0.036 : 14 * u, lineH: 1.2, maxLines: 1, cls: 'aux' });
  }
  // tiêu đề + đơn vị
  if (scene.title && scene.title.trim()) {
    const tMaxW = v ? Math.min(W * 0.8, z.x1 - z.x0) - (unitFit ? unitFit.w + 30 * u : 0) : W * 0.58;
    const titleFit = C.fitted(scene, 'title', look,
      { W, H, fam: 'ui', wt: 700, text: scene.title, maxW: tMaxW, maxH: H * 0.14, size: 48 * u * B, minSize: 24 * u, lineH: 1.1, maxLines: 2 });
    items.push({ key: 'title', text: scene.title, fit: titleFit, x: z.x0, y: z.y0, align: 'left', tIn: 0.3, inDur: look.ti.textIn, color: look.t1, fam: 'ui', wt: 700 });
  }
  if (unitFit) {
    // dọc: lùi 14px khỏi vạch cột an toàn phải (z.x1) cho tho, không chạm viền
    items.push({ key: 'unit', text: unit, fit: unitFit, x: v ? z.x1 - 14 * u : z.x1, y: z.y0 + 6 * u, align: 'right', tIn: 0.5, inDur: look.ti.textIn, color: look.t3, fam: 'mono', wt: 500 });
  }
  // vùng vẽ — dọc: san phẳng khoảng trống dưới tiêu đề, vùng biểu đồ chiếm ≥50% vùng an toàn
  const px0 = v ? z.x0 + W * 0.02 : z.x0 + W * 0.05, px1 = v ? z.x1 - W * 0.015 : z.x1 - W * 0.02;
  const py0 = v ? z.y0 + H * 0.105 : z.y0 + H * 0.15, pyL = z.y1 - 44 * u; // chừa hàng nhãn trục
  const yMax = v ? Math.max(...values, 0.0001) * 1.1 : niceMax(Math.max(...values, 0.0001) * 1.18);
  const geom = [];
  const accI = values.indexOf(Math.max(...values));
  if (kind === 'compare') {
    if (n) {
      const buf = Math.min(70 * u, (pyL - py0) * 0.1);
      const rows = (pyL - py0 - buf * 2) / n;
      for (let i = 0; i < n; i++) {
        geom.push({
          kind, i, v: values[i], cx: px0, trackW: W * 0.19, x0: px0 + W * 0.2, wMax: (px1 - px0 - W * 0.2) * 0.96,
          y: py0 + buf + i * rows + rows / 2, bh: v ? Math.min(72 * u, rows * 0.55) : Math.min(58 * u, rows * 0.5), acc: i === accI,
          land: look.ti.axis + look.ti.barIn + i * look.ti.barStep,
        });
      }
    }
  } else {
    const slots = (px1 - px0) / Math.max(1, n);
    for (let i = 0; i < n; i++) {
      geom.push({
        kind, i, v: values[i], x: px0 + slots * (i + 0.5), bw: kind === 'bar' ? (v ? Math.min(slots * 0.70, 260 * u) : Math.min(slots * 0.56, 132 * u)) : slots,
        acc: i === accI, yBase: pyL, yTop: py0,
        land: kind === 'bar' ? look.ti.axis + look.ti.barIn + i * look.ti.barStep
                             : look.ti.axis + (i / Math.max(1, n - 1)) * look.ti.lineDur + 0.12,
      });
    }
  }
  // nhãn trục + giá trị → TEXTS
  for (const gg of geom) {
    if (labels[gg.i]) {
      // dọc: cho nhãn gói tới 2 dòng — sàn 'axis' (0.040·W) không ép chữ tràn sang cột kế;
      // maxW rộng hơn cột một chút cho năm 4 chữ số ở cỡ lớn
      const lf = C.fitted(scene, 'xl' + gg.i, look, { W, H, fam: 'mono', wt: 500, text: labels[gg.i],
        maxW: kind === 'compare' ? gg.trackW : (v ? Math.max(gg.bw * 0.96, W * 0.135) : gg.bw * 0.96), maxH: H * (v ? 0.09 : 0.07), size: v ? W * 0.042 : 24 * u, minSize: 13 * u, lineH: 1.2, maxLines: v ? 2 : 1, cls: 'axis' });
      items.push({
        key: 'label' + gg.i, text: labels[gg.i], fit: lf,
        x: kind === 'compare' ? gg.cx + gg.trackW : gg.x,
        y: kind === 'compare' ? gg.y - lf.size * 0.86 : pyL + 10 * u,
        align: kind === 'compare' ? 'right' : 'center', tIn: look.ti.axis, inDur: 0.01,
        color: look.t3, fam: 'mono', wt: 500,
      });
    }
    const vs = fmtNum(values[gg.i]);
    if (vs) {
      const vf = C.fitted(scene, 'vv' + gg.i, look, { W, H, fam: 'mono', wt: 500, text: vs,
        maxW: kind === 'compare' ? W * 0.3 : (v ? Math.max(gg.bw * 1.1, W * 0.1) : gg.bw * 1.1), maxH: H * 0.06, size: v ? W * 0.042 : 25 * u, minSize: 13 * u, lineH: 1.2, maxLines: 1, cls: 'axis' });
      const vo = v ? vf.h + 10 * u : 34 * u; // dọc: số to → lùi xa hơn khỏi đỉnh cột
      const bx = barTopX(gg, yMax, u, vo);      items.push({
        key: 'value' + gg.i, text: vs, fit: vf,
        x: bx.x, y: bx.y,
        align: kind === 'compare' ? 'left' : 'center', tIn: gg.land + 0.12, inDur: 0.01,
        color: look.t2, fam: 'mono', wt: 500,
      });
    }
  }
  return { items, kind, values, labels, yMax, geom, plot: { x0: px0, x1: px1, y0: py0, y1: pyL }, zone: z };
}
function barTopX(gg, yMax, u, off = 34) {
  if (gg.kind === 'compare') return { x: gg.x0 + Math.abs(gg.v) / yMax * gg.wMax * 0.999 + 10, y: gg.y - gg.bh / 2 - off };
  const top = gg.yBase - Math.abs(gg.v) / yMax * (gg.yBase - gg.yTop);
  return { x: gg.x, y: top - off };
}

export function draw(ctx, scene, lt, dur, look, env, W, H) {
  const p = plan(scene, look, W, H, env), u = U(W, H);
  const { kind, geom, plot, yMax } = p;
  // lưới hairline
  const ga = ss(seg(lt, 0.12, look.ti.axis));
  if (ga > 0) {
    ctx.globalAlpha = 0.55 * ga;
    ctx.strokeStyle = 'rgba(255,255,255,0.09)'; ctx.lineWidth = 1;
    for (let k = 1; k <= 4; k++) {
      const yy = plot.y0 + (plot.y1 - plot.y0) * k / 4;
      ctx.beginPath(); ctx.moveTo(plot.x0, yy); ctx.lineTo(plot.x1, yy); ctx.stroke();
    }
    ctx.globalAlpha = 1;
  }

  if (kind === 'bar') {
    for (const gg of geom) {
      const bStart = look.ti.axis + gg.i * look.ti.barStep;
      const gr = back(seg(lt, bStart, bStart + look.ti.barIn), 1.25);
      if (gr <= 0.002) continue;
      const h = Math.max(2, Math.abs(gg.v) / yMax * (plot.y1 - plot.y0)) * gr;
      const top = plot.y1 - h;
      ctx.save();
      ctx.fillStyle = gg.acc ? `rgba(${look.accentRGB},0.95)` : 'rgba(255,255,255,0.17)';
      if (gg.acc) { ctx.shadowColor = `rgba(${look.accentRGB},0.4)`; ctx.shadowBlur = 26 * u; }
      ctx.beginPath(); rTop(ctx, gg.x - gg.bw / 2, top, gg.bw, h, 8 * u); ctx.fill();
      ctx.restore();
    }
  } else if (kind === 'compare') {
    for (const gg of geom) {
      const bStart = look.ti.axis + gg.i * look.ti.barStep;
      const gr = back(seg(lt, bStart, bStart + look.ti.barIn), 1.25);
      if (gr <= 0.002) continue;
      const w = Math.abs(gg.v) / yMax * gg.wMax * gr;
      ctx.save();
      ctx.fillStyle = gg.acc ? `rgba(${look.accentRGB},0.95)` : 'rgba(255,255,255,0.17)';
      if (gg.acc) { ctx.shadowColor = `rgba(${look.accentRGB},0.4)`; ctx.shadowBlur = 22 * u; }
      ctx.beginPath(); rRight(ctx, gg.x0, gg.y - gg.bh / 2, w, gg.bh, 6 * u); ctx.fill();
      ctx.restore();
    }
  } else { // line
    const q = eo(seg(lt, look.ti.axis, look.ti.axis + look.ti.lineDur));
    const pts = geom.map(gg => ({ x: gg.x, y: plot.y1 - (gg.v / yMax) * (plot.y1 - plot.y0) }));
    if (pts.length > 1) {
      // vùng sáng dưới đường
      ctx.save();
      ctx.beginPath(); ctx.moveTo(pts[0].x, plot.y1);
      pts.forEach(pt => ctx.lineTo(pt.x, pt.y));
      ctx.lineTo(pts[pts.length - 1].x, plot.y1); ctx.closePath();
      ctx.fillStyle = `rgba(${look.accentRGB},0.08)`; ctx.fill();
      ctx.restore();
      // độ dài từng khúc + đúng thời điểm chấm mọc
      const segs = pts.slice(1).map((pt, i) => Math.hypot(pt.x - pts[i].x, pt.y - pts[i].y));
      const total = segs.reduce((a, b) => a + b, 0) || 1;
      const cums = [0]; let cs = 0;
      for (const s2 of segs) { cs += s2; cums.push(cs); }
      // đường vẽ dần
      ctx.save();
      ctx.strokeStyle = look.accent; ctx.lineWidth = Math.max(2, 3 * u);
      ctx.lineJoin = 'round'; ctx.lineCap = 'round';
      ctx.beginPath(); ctx.moveTo(pts[0].x, pts[0].y);
      let budget = q * total;
      for (let i = 0; i < segs.length && budget > 0; i++) {
        if (budget >= segs[i]) { ctx.lineTo(pts[i + 1].x, pts[i + 1].y); budget -= segs[i]; }
        else {
          const f = budget / segs[i];
          ctx.lineTo(pts[i].x + (pts[i + 1].x - pts[i].x) * f, pts[i].y + (pts[i + 1].y - pts[i].y) * f);
          budget = 0;
        }
      }
      ctx.stroke();
      // chấm mọc đúng lúc đường đi ngang qua
      pts.forEach((pt, i) => {
        const arrive = cums[i] / total;
        const a = ss(seg(q, Math.max(0, arrive - 0.03), arrive + 0.05));
        if (a <= 0.001) return;
        ctx.beginPath(); ctx.arc(pt.x, pt.y, 6 * u, 0, Math.PI * 2);
        ctx.fillStyle = look.t1; ctx.globalAlpha = a; ctx.fill(); ctx.globalAlpha = 1;
      });
      ctx.restore();
    } else if (pts.length === 1) {
      ctx.beginPath(); ctx.arc(pts[0].x, pts[0].y, 6 * u, 0, Math.PI * 2);
      ctx.fillStyle = look.accent; ctx.fill();
    }
  }

  // chữ
  for (const it of p.items) {
    C.drawFitted(ctx, it.fit, {
      x: it.x, y: it.y, align: it.align, look, fam: it.fam, wt: it.wt, color: it.color,
      lt, tIn: it.tIn, inDur: Math.max(0.01, it.inDur), rise: 10 * u,
    });
  }
}
function rTop(ctx, x, y, w, h, r) {
  r = Math.min(r, w / 2, h);
  ctx.moveTo(x, y + h);
  ctx.lineTo(x, y + r); ctx.arcTo(x, y, x + r, y, r);
  ctx.lineTo(x + w - r, y); ctx.arcTo(x + w, y, x + w, y + r, r);
  ctx.lineTo(x + w, y + h); ctx.closePath();
}
function rRight(ctx, x, y, w, h, r) {
  r = Math.min(r, h / 2, w);
  ctx.moveTo(x, y);
  ctx.lineTo(x + w - r, y); ctx.arcTo(x + w, y, x + w, y + r, r);
  ctx.lineTo(x + w, y + h - r); ctx.arcTo(x + w, y + h, x + w - r, y + h, r);
  ctx.lineTo(x, y + h); ctx.closePath();
}

export const texts = (s, look, W, H, env) => planTexts(plan(s, look, W, H, env));
