// shot chapter_card: panel chương giữa khung + vạch nhấn hai bên kicker + Light sweep
import * as C from '../core.js';
import { U } from '../looks/dark-keynote.js';
import { ss, seg } from '/core/lib.js';
import { planTexts } from './_common.js';

// tách "Phần 3 — Cuộc đua" → {kicker:'PHẦN 3', main:'Cuộc đua'} hoặc toàn bộ là chính
export function splitTitle(scene) {
  const t = String(scene.title ?? '');
  const m = t.match(/^\s*([^—–|·]{1,24})\s*[—–|·]\s*(.+)$/);
  if (m) return { kicker: m[1].trim(), main: m[2].trim() };
  return { kicker: '', main: t.trim() };
}

export function plan(scene, look, W, H, env) {
  const z = C.zone(W, H, look), u = U(W, H);
  const v = C.isPortrait(W, H), B = v ? 1.25 : 1; // dọc: chữ chính to hơn cho điện thoại
  const span = C.centerSpan(W, H, look);
  const { kicker, main } = splitTitle(scene);
  const items = [];
  let mainFit = null, textFit = null, kickFit = null;
  if (main) {
    mainFit = C.fitted(scene, 'main', look,
      { W, H, fam: 'ui', wt: 700, text: main, maxW: v ? span : W * 0.72, maxH: H * 0.26, size: 96 * u * B, minSize: 34 * u, lineH: 1.08, maxLines: 2 });
  }
  if (scene.text && scene.text.trim()) {
    textFit = C.fitted(scene, 'text', look,
      { W, H, fam: 'ui', wt: 500, text: scene.text, maxW: v ? span : W * 0.6, maxH: H * 0.1, size: 30 * u * (v ? 1.15 : 1), minSize: 18 * u, lineH: 1.3, maxLines: 2 });
  }
  if (kicker) {
    kickFit = C.fitted(scene, 'kicker', look,
      { W, H, fam: 'mono', wt: 500, text: kicker, maxW: W * 0.4, maxH: H * 0.05, size: v ? Math.max(29 * u, W * 0.037) : 28 * u, minSize: v ? W * 0.036 : 16 * u, lineH: 1.2, maxLines: 1, cls: 'aux' });
  }
  const gapK = 40 * u, gapT = 42 * u;
  const totalH = (mainFit ? mainFit.h : 0) + (textFit ? textFit.h + gapT : 0);
  let mainY = (z.y0 + z.y1) / 2 - totalH / 2;
  if (kickFit) mainY += gapK * 0.5;
  // dọc: tiêu đề hiện LÊN NGUYÊN KHỐI (whole) và ĐỢI light_sweep quét xong mới hiện
  // — không bao giờ có chữ mờ nửa từ ở mép quét như trước
  const mainTIn = v ? Math.max(0.78, look.trDur('light_sweep', scene.emphasis) + 0.1) : 0.6;
  if (mainFit) items.push({ key: 'main', text: main, fit: mainFit, x: W / 2, y: mainY, align: 'center', tIn: mainTIn, inDur: look.ti.textIn + Math.min(0.8, mainFit.lines.length * 0.25), color: look.t1, fam: 'ui', wt: 700, whole: v });
  if (textFit) items.push({ key: 'text', text: scene.text, fit: textFit, x: W / 2, y: mainY + mainFit.h + gapT, align: 'center', tIn: v ? mainTIn + look.ti.textIn + 0.15 : (mainFit ? 1.5 : 0.6), inDur: look.ti.textIn, color: look.t2, fam: 'ui', wt: 500, whole: v });
  if (kickFit) items.push({ key: 'kicker', text: kicker, fit: kickFit, x: W / 2, y: mainY - kickFit.h - gapK, align: 'center', tIn: v ? 0.5 : 0.4, inDur: look.ti.textIn, color: look.t2, fam: 'mono', wt: 500, trackEm: look.track.cap, whole: v });
  return { items, mainFit, kickFit, mainY };
}

export function draw(ctx, scene, lt, dur, look, env, W, H) {
  const p = plan(scene, look, W, H, env), u = U(W, H);
  // vạch nhấn hai bên kicker
  const kick = p.items.find(i => i.key === 'kicker');
  if (kick) {
    const ka = ss(seg(lt, 0.35, 0.8));
    const sides = ka * Math.min(0.1 * W);
    ctx.save();
    ctx.globalAlpha = ka; ctx.strokeStyle = `rgba(${look.accentRGB},0.7)`; ctx.lineWidth = Math.max(1, 1.5 * u);
    const kickTop = kick.y + kick.fit.size * 0.5;
    ctx.beginPath(); ctx.moveTo(W / 2 - kick.fit.w / 2 - 26 * u - sides, kickTop); ctx.lineTo(W / 2 - kick.fit.w / 2 - 26 * u, kickTop); ctx.stroke();
    ctx.beginPath(); ctx.moveTo(W / 2 + kick.fit.w / 2 + 26 * u, kickTop); ctx.lineTo(W / 2 + kick.fit.w / 2 + 26 * u + sides, kickTop); ctx.stroke();
    ctx.restore();
  }
  // chữ
  for (const it of p.items) {
    C.drawFitted(ctx, it.fit, {
      x: it.x, y: it.y, align: it.align, look, fam: it.fam, wt: it.wt, color: it.color,
      lt, tIn: it.tIn, inDur: Math.max(0.01, it.inDur), rise: 18 * u, trackEm: it.trackEm, whole: it.whole,
    });
  }
  // light sweep quét qua hàng chữ (sau khi chữ hiện xong) — soft front
  if (p.mainFit && p.items.length) {
    const main = p.items.find(i => i.key === 'main');
    const endIn = main ? main.tIn + Math.max(0.01, main.inDur) : 0.9;
    const sq = seg(lt, endIn, endIn + 0.7);
    if (sq > 0 && sq < 1) {
      const mx = -W * 0.15 + sq * (W * 1.3);
      const bw = W * 0.26;
      const gy0 = (main ? main.y : 0) - 20 * u, gy1 = (main ? main.y + p.mainFit.h : H);
      ctx.save();
      const g = ctx.createLinearGradient(mx - bw / 2, 0, mx + bw / 2, 0);
      g.addColorStop(0, 'rgba(255,255,255,0)'); g.addColorStop(0.5, 'rgba(255,255,255,0.09)');
      g.addColorStop(0.75, `rgba(${look.accentRGB},0.06)`); g.addColorStop(1, 'rgba(255,255,255,0)');
      ctx.fillStyle = g;
      ctx.fillRect(mx - bw / 2, gy0, bw, gy1 - gy0);
      ctx.restore();
    }
  }
}

export const texts = (s, look, W, H, env) => planTexts(plan(s, look, W, H, env));
