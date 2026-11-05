// shot kinetic_text: chữ lớn trung tâm, từ hiện dần (một từ màu nhấn), text phụ dưới
import * as C from '../core.js';
import { U } from '../looks/dark-keynote.js';
import { planTexts } from './_common.js';

export function plan(scene, look, W, H, env) {
  const z = C.zone(W, H, look), u = U(W, H);
  const v = C.isPortrait(W, H);
  // dọc: tiêu đề/nhãn chính to hơn tương ứng để đọc trên điện thoại; khối vẫn
  // căn giữa nhưng bề rộng kẹp vào cột an toàn 10% bên phải (centerSpan)
  const span = C.centerSpan(W, H, look);
  const items = [];
  let titleFit = null, textFit = null;
  if (scene.title && scene.title.trim()) {
    titleFit = C.fitted(scene, 'title', look,
      { W, H, fam: 'ui', wt: 700, text: scene.title, maxW: v ? span : W * 0.74, maxH: H * 0.4, size: 104 * u * (v ? 1.25 : 1), minSize: 34 * u, lineH: 1.05, maxLines: 4 });
  }
  if (scene.text && scene.text.trim()) {
    textFit = C.fitted(scene, 'text', look,
      { W, H, fam: 'ui', wt: 500, text: scene.text, maxW: v ? span : W * 0.6, maxH: H * 0.15, size: 38 * u * (v ? 1.15 : 1), minSize: 22 * u, lineH: 1.32, maxLines: 3 });
  }
  const gap = 34 * u;
  const totalH = (titleFit ? titleFit.h : 0) + (textFit ? textFit.h + gap : 0);
  const cy = (z.y0 + z.y1) / 2;
  let y = cy - totalH / 2;
  // chọn 1 từ nền nhấn (bám seed, không bao giờ là từ đầu) — tất định
  let accIdx = -1;
  if (titleFit) {
    const nW = titleFit.lines.reduce((n, l) => n + l.split(' ').length, 0);
    if (nW > 2) accIdx = 1 + (Math.floor(C.frac(C.hashStr(scene.id + '|acc') * 0.999331) * (nW - 1)) % (nW - 1));
  }
  if (titleFit) items.push({
    key: 'title', text: scene.title, fit: titleFit, x: W / 2, y, align: 'center',
    tIn: 0.35, inDur: look.ti.textIn + (titleFit.lines.length * 0.18), color: look.t1, fam: 'ui', wt: 700,
    accIdx, accColor: look.accent,
  });
  if (textFit) items.push({
    key: 'text', text: scene.text, fit: textFit, x: W / 2, y: y + (titleFit ? titleFit.h + gap : 0), align: 'center',
    tIn: titleFit ? 0.35 + look.ti.textIn + 0.55 : 0.5, inDur: look.ti.textIn + 0.4, color: look.t2, fam: 'ui', wt: 500,
  });
  return { items, cy };
}

export function draw(ctx, scene, lt, dur, look, env, W, H) {
  const p = plan(scene, look, W, H, env), u = U(W, H);
  const img = env ? env.img(scene, 'image') : null;
  if (C.hasImg(img)) {
    // có ảnh → dùng làm nền mờ (không phải chủ thể)
    C.bgImage(ctx, scene, env, 'image', lt, dur, look, W, H, { alpha: 0.24 });
    ctx.fillStyle = 'rgba(6,7,10,0.45)'; ctx.fillRect(0, 0, W, H);
  } else {
    // không ảnh → vầng sáng accent rất nhạt sau chữ (gradient của look vẫn là nền)
    const g = ctx.createRadialGradient(W / 2, p.cy, 0, W / 2, p.cy, Math.min(W, H) * 0.42);
    g.addColorStop(0, `rgba(${look.accentRGB},0.05)`); g.addColorStop(1, 'rgba(0,0,0,0)');
    ctx.fillStyle = g; ctx.fillRect(0, 0, W, H);
  }
  for (const it of p.items) {
    C.drawFitted(ctx, it.fit, {
      x: it.x, y: it.y, align: it.align, look, fam: it.fam, wt: it.wt, color: it.color,
      lt, tIn: it.tIn, inDur: it.inDur, rise: 20 * u, accIdx: it.accIdx, accColor: it.accColor,
    });
  }
}

export const texts = (s, look, W, H, env) => planTexts(plan(s, look, W, H, env));
