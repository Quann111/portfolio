// shot split_compare: hai nửa ảnh so sánh, chip nhãn trên mỗi nửa, vạch giữa + chấm nhấn
import * as C from '../core.js';
import { U } from '../looks/dark-keynote.js';
import { ss, seg, spring } from '/core/lib.js';
import { planTexts } from './_common.js';
import { roundRect } from '../looks/dark-keynote.js';

export function plan(scene, look, W, H, env) {
  const z = C.zone(W, H, look), u = U(W, H);
  const v = C.isPortrait(W, H);
  const t = String(scene.title ?? '');
  const bar = t.indexOf('|');
  const lLabel = bar >= 0 ? t.slice(0, bar).trim() : t.trim();
  const rLabel = bar >= 0 ? t.slice(bar + 1).trim() : '';

  const items = [];
  const chipW = W * 0.42, chipH = 0;
  if (lLabel && lLabel.trim()) {
    const lf = C.fitted(scene, 'chipL', look, { W, H, fam: 'mono', wt: 500, text: lLabel, maxW: chipW, maxH: H * 0.05, size: 26 * u, minSize: 15 * u, lineH: 1.15, maxLines: 1, cls: 'aux' });
    const cw = lf.w + 36 * u, ch = lf.h + 22 * u;
    // ngang: chip trên-trái cả hai nửa. dọc: chip ở mép TRÊN của nửa của nó
    // (nửa trên y=0.155H dưới vạch an toàn 12%; nửa dưới y=0.55H dưới vạch giữa)
    const cy0 = v ? H * 0.155 : H * 0.05;
    items.push({ key: 'chipL', text: lLabel, fit: lf, x: W * 0.045 + cw / 2, y: cy0 + (ch - lf.h) / 2 * 0.86 + 10 * u, align: 'center', tIn: 0.4, inDur: look.ti.textIn, color: look.t1, fam: 'mono', wt: 500, chip: { x: W * 0.045, y: cy0, w: cw, h: ch } });
  }
  if (rLabel && rLabel.trim()) {
    const rf = C.fitted(scene, 'chipR', look, { W, H, fam: 'mono', wt: 500, text: rLabel, maxW: chipW, maxH: H * 0.05, size: 26 * u, minSize: 15 * u, lineH: 1.15, maxLines: 1, cls: 'aux' });
    const cw = rf.w + 36 * u, ch = rf.h + 22 * u;
    const cx = v ? W * 0.045 : W - W * 0.045 - cw; // dọc: chip nửa dưới dồn trái, tránh cột nút phải
    const cy0 = v ? H / 2 + H * 0.05 : H * 0.05;
    items.push({ key: 'chipR', text: rLabel, fit: rf, x: cx + cw / 2, y: cy0 + (ch - rf.h) / 2 * 0.86 + 10 * u, align: 'center', tIn: 0.55, inDur: look.ti.textIn, color: look.t1, fam: 'mono', wt: 500, chip: { x: cx, y: cy0, w: cw, h: ch } });
  }
  if (scene.text && scene.text.trim()) {
    // dọc: text vào bề rộng vùng an toàn (chip căn giữa phải nằm trong cột phải)
    const maxW = v ? Math.min(W * 0.86, z.x1 - z.x0) : W * 0.64;
    const tf = C.fitted(scene, 'text', look, { W, H, fam: 'ui', wt: 500, text: scene.text, maxW, maxH: H * 0.1, size: 34 * u, minSize: 20 * u, lineH: 1.3, maxLines: 2 });
    items.push({ key: 'text', text: scene.text, fit: tf, x: W / 2, y: z.y1 - tf.h - H * 0.02, align: 'center', tIn: 0.9, inDur: look.ti.textIn, color: look.t1, fam: 'ui', wt: 500 });
  }
  return { items, lLabel, rLabel };
}

// camera riêng cho từng nửa: push nhẹ, kéo ra hai phía (mở', mở')
// — ngang kéo ngang (x), dọc kéo dọc (y)
function halfCam(lt, dur, dir) {
  const q = dur > 0 ? ss(seg(lt, 0, dur)) : 0;
  return { z: 1.05 + 0.08 * q, x: dir * lerpC(0.03, 0.05, q), y: 0 };
}
function halfCamV(lt, dur, dir, h) {
  const q = dur > 0 ? ss(seg(lt, 0, dur)) : 0;
  return { z: 1.05 + 0.08 * q, x: 0, y: dir * lerpC(0.03, 0.05, q) * (h / 2) };
}
const lerpC = (a, b, t) => a + (b - a) * t;

export function draw(ctx, scene, lt, dur, look, env, W, H) {
  const p = plan(scene, look, W, H, env), u = U(W, H);
  const v = C.isPortrait(W, H);
  // hai nửa: ngang = trái/phải (như trước), dọc = trên/dưới; ảnh nửa dưới dùng image2
  const halves = v
    ? [
        { x: 0, y: 0, hw: W, hh: H / 2, img: env ? env.img(scene, 'image') : null, cam: halfCamV(lt, dur, +1, H) },
        { x: 0, y: H / 2, hw: W, hh: H / 2, img: env ? env.img(scene, 'image2') : null, cam: halfCamV(lt, dur, -1, H) },
      ]
    : [
        { x: 0, y: 0, hw: W / 2, hh: H, img: env ? env.img(scene, 'image') : null, cam: halfCam(lt, dur, +1) },
        { x: W / 2, y: 0, hw: W / 2, hh: H, img: env ? env.img(scene, 'image2') : null, cam: halfCam(lt, dur, -1) },
      ];
  for (const hf of halves) {
    ctx.save();
    ctx.beginPath(); ctx.rect(hf.x, hf.y, hf.hw, hf.hh); ctx.clip();
    const cx = hf.x + hf.hw / 2, cy = hf.y + hf.hh / 2;
    ctx.save();
    ctx.translate(cx, cy);
    ctx.scale(hf.cam.z, hf.cam.z);
    ctx.translate(-cx - hf.cam.x, -cy - hf.cam.y);
    const mg = 0.09;
    const ok = C.drawImage(ctx, hf.img, hf.x - hf.hw * mg, hf.y - hf.hh * mg, hf.hw * (1 + mg * 2), hf.hh * (1 + mg * 2));
    ctx.restore();
    if (!ok) {
      // fallback: gradient phía nửa trong không gian clip (nửa đầu sáng nhẹ hơn — theo trục chia đôi)
      const later = v ? hf.y > 0 : hf.x > 0;
      const g = ctx.createLinearGradient(hf.x, hf.y, hf.x + hf.hw, hf.y + hf.hh);
      g.addColorStop(0, later ? '#111318' : '#0B0D12');
      g.addColorStop(1, later ? '#0B0D12' : '#14161B');
      ctx.fillStyle = g; ctx.fillRect(hf.x, hf.y, hf.hw, hf.hh);
      // vòng tròn chút đó như hero
      ctx.strokeStyle = 'rgba(255,255,255,0.07)'; ctx.lineWidth = Math.max(1, 2 * u);
      ctx.beginPath(); ctx.arc(hf.x + hf.hw / 2, hf.y + hf.hh * 0.42, Math.min(hf.hw, hf.hh) * 0.1, 0, Math.PI * 2); ctx.stroke();
    }
    // scrim nhẹ phía trên mỗi nửa để chip đọc được
    const sg = ctx.createLinearGradient(0, hf.y, 0, hf.y + hf.hh * 0.3);
    sg.addColorStop(0, 'rgba(4,5,8,0.55)'); sg.addColorStop(1, 'rgba(4,5,8,0)');
    ctx.fillStyle = sg; ctx.fillRect(hf.x, hf.y, hf.hw, hf.hh * 0.3);
    ctx.restore();
  }

  // band tối đáy (nằm sau text, phủ cả hai nửa) để dòng chú thích đọc nổi trên ảnh sáng
  {
    const bh = H * 0.3;
    const bTop = H - bh;
    // alpha vào dần theo lt để không "bật" đột ngột khi chuyển cảnh
    const ba = ss(seg(lt, 0, 0.3));
    if (ba > 0) {
      const bg = ctx.createLinearGradient(0, bTop, 0, H);
      bg.addColorStop(0, 'rgba(4,5,8,0)');
      bg.addColorStop(0.3, `rgba(4,5,8,${0.66 * ba})`);
      bg.addColorStop(0.65, `rgba(4,5,8,${0.8 * ba})`);
      bg.addColorStop(1, `rgba(4,5,8,${0.86 * ba})`);
      ctx.fillStyle = bg; ctx.fillRect(0, bTop, W, bh);
    }
  }

  // vạch giữa mọc dần từ giữa + chấm accent — ngang: đường dọc; dọc: đường ngang
  const da = ss(seg(lt, 0.25, 0.6));
  if (da > 0) {
    const hh = (v ? W : H) * da;
    ctx.save();
    ctx.strokeStyle = 'rgba(255,255,255,0.2)'; ctx.lineWidth = Math.max(1, 1.5 * u);
    ctx.beginPath();
    if (v) { ctx.moveTo((W - hh) / 2, H / 2); ctx.lineTo((W + hh) / 2, H / 2); }
    else { ctx.moveTo(W / 2, (H - hh) / 2); ctx.lineTo(W / 2, (H + hh) / 2); }
    ctx.stroke();
    const sp = spring(seg(lt, 0.55, 1.05), 7, 0.32);
    if (sp > 0) {
      ctx.fillStyle = look.accent;
      ctx.shadowColor = `rgba(${look.accentRGB},0.5)`; ctx.shadowBlur = 12 * u;
      ctx.beginPath(); ctx.arc(W / 2, H / 2, Math.max(0.5, 7 * u * Math.max(0.2, sp)), 0, Math.PI * 2); ctx.fill();
    }
    ctx.restore();
  }

  // chip + text
  for (const it of p.items) {
    if (it.chip) {
      const ca = ss(seg(lt, it.tIn - 0.12, it.tIn + 0.06));
      if (ca <= 0.001) continue;
      ctx.save();
      ctx.globalAlpha = ca * 0.88;
      look.panel(ctx, it.chip.x, it.chip.y, it.chip.w, it.chip.h, it.chip.h / 2, 2, 1, true);
      ctx.restore();
    }
    C.drawFitted(ctx, it.fit, {
      x: it.x, y: it.y, align: it.align, look, fam: it.fam, wt: it.wt, color: it.color,
      lt, tIn: it.tIn, inDur: Math.max(0.01, it.inDur), rise: 12 * u,
    });
  }
}

export const texts = (s, look, W, H, env) => planTexts(plan(s, look, W, H, env));
