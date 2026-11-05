// shot hero_image: ảnh chủ thể full khung qua camera + gradient tối + title/text dưới trái
import * as C from '../core.js';
import { U } from '../looks/dark-keynote.js';
import { planTexts } from './_common.js';

export function plan(scene, look, W, H, env) {
  const z = C.zone(W, H, look), u = U(W, H);
  const v = C.isPortrait(W, H), B = v ? 1.2 : 1; // dọc: chữ chính to hơn để đọc trên điện thoại
  const items = [];
  const bar = { x: z.x0, y: 0, tIn: 0.28 };
  const maxW = v ? Math.min(W * 0.78, z.x1 - z.x0) : Math.min(W * 0.62, z.x1 - z.x0);
  let textFit = null;
  if (scene.text && scene.text.trim()) {
    textFit = C.fitted(scene, 'text', look,
      { W, H, fam: 'ui', wt: 500, text: scene.text, maxW: maxW * 1.04, maxH: H * 0.15, size: 40 * u * B, minSize: 22 * u, lineH: 1.32, maxLines: 3 });
  }
  const gap = 28 * u;
  const textTop = textFit ? z.y1 - textFit.h : z.y1;
  let titleFit = null;
  if (scene.title && scene.title.trim()) {
    titleFit = C.fitted(scene, 'title', look,
      { W, H, fam: 'ui', wt: 700, text: scene.title, maxW, maxH: Math.max(70 * u, textTop - gap - z.y0 - 30 * u), size: 92 * u * B, minSize: 30 * u, lineH: 1.06, maxLines: 3 });
  }
  const titleTop = (textFit ? textTop - gap : z.y1) - (titleFit ? titleFit.h : 0);
  bar.y = titleTop - 22 * u;
  if (titleFit) items.push({
    key: 'title', text: scene.title, fit: titleFit, x: z.x0, y: titleTop, align: 'left',
    tIn: 0.35, inDur: look.ti.textIn + 0.3, color: look.t1, fam: 'ui', wt: 700,
  });
  if (textFit) items.push({
    key: 'text', text: scene.text, fit: textFit, x: z.x0, y: textTop, align: 'left',
    tIn: (titleFit ? 0.35 + (look.ti.textIn + 0.3) + 0.12 : 0.6), inDur: look.ti.textIn, color: look.t2, fam: 'ui', wt: 500,
  });
  return { items, bar };
}

export function draw(ctx, scene, lt, dur, look, env, W, H) {
  // dọc: ảnh ngang cover-fit xén hai bên → dùng camera hãm pan x để chủ thể
  // (giữa ảnh) luôn giữ gần tâm khung; ngang: camera mặc định như cũ.
  if (C.isPortrait(W, H)) {
    C.bgImage(ctx, scene, env, 'image', lt, dur, look, W, H, { cam: C.cameraPortrait(scene, lt, dur, W, H) });
  } else {
    C.bgImage(ctx, scene, env, 'image', lt, dur, look, W, H);
  }
  const p = plan(scene, look, W, H, env), u = U(W, H);
  look.scrim(ctx, W, H);
  if (p.items.length && p.bar) {
    const bA = C.ss(C.seg(lt, p.bar.tIn, p.bar.tIn + 0.5));
    ctx.globalAlpha = bA; ctx.fillStyle = look.accent;
    ctx.fillRect(p.bar.x, p.bar.y, 64 * u * bA, Math.max(2, 3 * u));
    ctx.globalAlpha = 1;
  }
  for (const it of p.items) {
    C.drawFitted(ctx, it.fit, {
      x: it.x, y: it.y, align: it.align, look, fam: it.fam, wt: it.wt, color: it.color,
      lt, tIn: it.tIn, inDur: it.inDur, rise: 16 * u,
    });
  }
}

export const texts = (s, look, W, H, env) => planTexts(plan(s, look, W, H, env));
