// look "dark-keynote" — palette, kiểu chữ, timing, nền sân khấu.
// Nguồn ngữ pháp màu kiểu chữ & chuyển cảnh: styles/dark-keynote/STYLE.md (chỉ lấy
// palette + type + transition grammar, KHÔNG chép code demo).
// Mọi kích thước tính theo U = min(W,H)/1080 (thiết kế ở 1920×1080, tự scale cho dọc).

const BASE = 1080; // chỉ là "đơn vị thiết kế", không phải kích thước canvas

export const U = (W, H) => Math.min(W, H) / BASE;

// —— vùng an toàn Shorts (YouTube), tính theo PHẦN CẠNH của khung DỌC ——
// Nguồn gốc duy nhất của các con số: core re-export dùng chung; mọi shot chỉ
// đọc qua core/look lên đây. Khung ngang bỏ qua (isPortrait = H > W).
//   top    12% — thanh tiêu đề / UI của YouTube
//   bottom 20% — mô tả + nút like/comment (phụ đề đặt NGAY TRÊN vạch này)
//   right  10% — cột nút dọc bên phải
export const SHORT_SAFE = { top: 0.12, bottom: 0.20, right: 0.10 };
export const isPortrait = (W, H) => H > W;

const look = {
  name: 'dark-keynote',

  // —— màu: thang trung tính hơi lạnh + MỘT màu nhấn duy nhất ——
  accent: '#B7F34A',
  accentRGB: '183,243,74',
  cool: 'rgba(96,132,255,',      // ánh sáng lạnh off-centre (nối chuỗi alpha)
  t1: '#F3F5F8',                 // chữ chính
  t2: '#A6AEBB',                 // chữ phụ
  t3: '#A8B0BC',                 // chữ nhãn/nhỏ (sàn tương phản — tối thiểu bằng #A8B0BC)
  s1: '#15171C', s2: '#1B1E25', s3: '#242832', // 3 bậc bề mặt nâng
  danger: '#FF5A45',

  fonts: { ui: 'Inter', big: 'Inter Tight', mono: 'JetBrains Mono' },
  weights: { head: 700, head2: 600, body: 500, cap: 500, num: 600 },
  track: { big: -0.045, cap: 0.14 }, // em/thước chữ (×size tính px)

  // —— hằng số thời gian dùng chung cho EV / TEXTS / draw (nghiệm một nơi) ——
  ti: {
    textIn: 0.42,       // một khối chữ hiện xong (fade + tr rise)
    word: 0.065,        // lag giữa các chữ trong head
    countDelay: 1.2,    // số lớn bắt đầu đếm (để sau khi chuyển cảnh kết thúc)
    countDur: 1.15,     // thời gian đếm
    axis: 0.55,         // trục biểu đồ/timeline vẽ xong
    barIn: 0.5,         // cột mọc xong
    barStep: 0.16,      // khoảng cách giữa các cột
    lineDur: 0.9,       // đường line vẽ xong
    evGap: 0.22,        // lag event timeline
  },

  // —— chuyển cảnh: thời lượng theo loại + emphasis ——
  trDur(type, em) {
    const base = { cut: 0, wipe: 0.6, slide: 0.62, zoom_through: 0.75, light_sweep: 0.85 }[type];
    const b = base ?? 0.6;
    const f = { low: 1.15, mid: 1, high: 0.82 }[em] ?? 1;
    return b * f;
  },

  // vùng phụ đề chiếm phía dưới; text trên hình không được đụng vào.
  // Dọc: phụ đề đặt ngay trên vạch 20% đáy (SHORT_SAFE.bottom) → chừa thêm
  // ~10%H cho bản thân pill phụ đề (tối đa 2 dòng) + khoảng thở.
  captionZone(W, H) {
    return isPortrait(W, H) ? H * (SHORT_SAFE.bottom + 0.10) : H * 0.17;
  },

  // —— cỡ chữ TỐI THIỂU tính theo S = min(W,H) — định nghĩa MỘT NƠI, áp cho mọi shot:
  // core.fitLines tự nâng minSize của mỗi khối chữ có gắn cls. không chỉnh từng con số lẻ tẻ.
  // cls 'aux'  = chữ phụ/nguồn/đơn vị/kicker/chip  ≥ 0.026·S (≈28px @1080p)
  // cls 'axis' = nhãn trục năm & số trên cột       ≥ 0.030·S (≈32px @1080p)
  // Khung DỌC: sàn nâng theo W ( Shorts đọc trên điện thoại — chữ phụ không được dưới ~39px @1080 ):
  //   'aux'  (phụ/nguồn/đơn vị/kicker/nguồn quote)   ≥ 0.036·W
  //   'mile' (nhãn mốc timeline dưới năm)             ≥ 0.038·W
  //   'axis' (nhãn trục năm & số trên cột)            ≥ 0.040·W
  // Khung ngang giữ nguyên sàn cũ theo S.
  minFont(W, H, cls) {
    if (isPortrait(W, H)) {
      if (cls === 'axis') return 0.040 * W;
      if (cls === 'mile') return 0.038 * W;
      return 0.036 * W;
    }
    const S = Math.min(W, H);
    return (cls === 'axis' ? 0.030 : 0.026) * S;
  },

  // —— nền sân khấu: gần đen, nâng sáng tâm-trên, hào quang lạnh lệch tm?, lưới tóc, vignette ——
  stage(ctx, W, H, seed) {
    const u = U(W, H);
    let g = ctx.createLinearGradient(0, 0, 0, H);
    g.addColorStop(0, '#111318'); g.addColorStop(1, '#07080B');
    ctx.fillStyle = g; ctx.fillRect(0, 0, W, H);

    // lift trắng nhỏ quanh tâm-trên
    let rl = ctx.createRadialGradient(W * 0.5, H * 0.3, 0, W * 0.5, H * 0.3, H * 0.75);
    rl.addColorStop(0, 'rgba(255,255,255,0.045)'); rl.addColorStop(1, 'rgba(255,255,255,0)');
    ctx.fillStyle = rl; ctx.fillRect(0, 0, W, H);

    // một vầng sáng lạnh lớn, lệch tâm — vị trí bám seed cho mọi cảnh khác nhau
    const h1 = 0.18 + 0.64 * frac(seed * 0.6180339887);
    const h2 = 0.2 + 0.5 * frac(seed * 0.0999 + 0.37);
    const gx = W * h1, gy = H * h2;
    let cg = ctx.createRadialGradient(gx, gy, 0, gx, gy, H * 0.62);
    cg.addColorStop(0, look.cool + '0.07)'); cg.addColorStop(1, look.cool + '0)');
    ctx.fillStyle = cg; ctx.fillRect(0, 0, W, H);

    // lưới tóc: mô-đun W/40 (≈48px @1080), mỗi đường thứ 4 đậm hơn
    const m = Math.max(18, W / 40);
    ctx.lineWidth = Math.max(1, u);
    for (let x = m, i = 1; x < W; x += m, i++) {
      ctx.strokeStyle = i % 4 === 0 ? 'rgba(255,255,255,0.06)' : 'rgba(255,255,255,0.025)';
      line(ctx, x, 0, x, H);
    }
    for (let y = m, i = 1; y < H; y += m, i++) {
      ctx.strokeStyle = i % 4 === 0 ? 'rgba(255,255,255,0.05)' : 'rgba(255,255,255,0.02)';
      line(ctx, 0, y, W, y);
    }
    ctx.strokeStyle = 'rgba(255,255,255,0)';

    // vignette góc 50–55%
    let v = ctx.createRadialGradient(W / 2, H / 2, Math.min(W, H) * 0.42, W / 2, H / 2, Math.hypot(W, H) * 0.62);
    v.addColorStop(0, 'rgba(0,0,0,0)'); v.addColorStop(1, 'rgba(0,0,0,0.52)');
    ctx.fillStyle = v; ctx.fillRect(0, 0, W, H);
  },

  // màn phủ tối cho ảnh nền (bảo đảm chữ đọc được, không đè sáng chủ thể)
  scrim(ctx, W, H) {
    let g = ctx.createLinearGradient(0, 0, 0, H);
    g.addColorStop(0, 'rgba(4,5,8,0.30)'); g.addColorStop(0.4, 'rgba(4,5,8,0.22)');
    g.addColorStop(0.72, 'rgba(4,5,8,0.62)'); g.addColorStop(1, 'rgba(4,5,8,0.92)');
    ctx.fillStyle = g; ctx.fillRect(0, 0, W, H);
  },

  // bề mặt panel (3 bậc: step 0..2), viền tóc + sáng mép trên
  panel(ctx, x, y, w, h, r, step = 1, a = 1, shadow = true) {
    const c = ctx.canvas || {}, Wd = c.width || 1920, Hd = c.height || 1080;
    const surf = ['#15171C', '#1B1E25', '#242832'][Math.max(0, Math.min(2, Math.round(step)))] || '#1B1E25';
    _panel(ctx, x, y, w, h, Math.max(4, r ?? Math.min(Wd, Hd) / 54), surf, a, shadow);
  },

  // ảnh thiếu/lỗi → nền gradient của look (không bao giờ đen, không crash)
  fallback(ctx, W, H, seed) {
    this.stage(ctx, W, H, seed);
    const u = U(W, H);
    const h = frac(seed * 0.7311); // đường chéo quét sáng nhẹ
    const x0 = W * (0.1 + 0.4 * h);
    let g = ctx.createLinearGradient(x0 - W * 0.5, H, x0 + W * 0.4, 0);
    g.addColorStop(0, 'rgba(23,26,34,0)'); g.addColorStop(0.5, 'rgba(30,34,44,0.5)');
    g.addColorStop(1, 'rgba(23,26,34,0)');
    ctx.fillStyle = g; ctx.fillRect(0, 0, W, H);
    // vòng tròn that với accent rất nhạt để có "chủ thể" thay cho ảnh
    const cx = W * 0.5, cy = H * 0.42, R = Math.min(W, H) * 0.16;
    ctx.strokeStyle = 'rgba(255,255,255,0.07)'; ctx.lineWidth = Math.max(1, 2 * u);
    ctx.beginPath(); ctx.arc(cx, cy, R, 0, Math.PI * 2); ctx.stroke();
    ctx.strokeStyle = 'rgba(255,255,255,0.04)';
    ctx.beginPath(); ctx.arc(cx, cy, R * 1.45, 0, Math.PI * 2); ctx.stroke();
  },
};
export default look;

// —— helpers dùng chung nhỏ ——
export const frac = x => x - Math.floor(x);
export function line(ctx, x0, y0, x1, y1) { ctx.beginPath(); ctx.moveTo(x0, y0); ctx.lineTo(x1, y1); ctx.stroke(); }
export function _panel(ctx, x, y, w, h, r, fill, a, shadow) {
  ctx.save();
  ctx.globalAlpha = a;
  if (shadow) {
    ctx.shadowColor = 'rgba(0,0,0,0.5)'; ctx.shadowBlur = r * 2.2; ctx.shadowOffsetY = r * 0.35;
  }
  ctx.beginPath(); roundRect(ctx, x, y, w, h, r); ctx.fillStyle = fill; ctx.fill();
  ctx.shadowColor = 'rgba(0,0,0,0)';
  // 1px viền sáng trên + viền tóc
  ctx.strokeStyle = 'rgba(255,255,255,0.1)'; ctx.lineWidth = 1;
  ctx.beginPath(); roundRect(ctx, x + 0.5, y + 0.5, w - 1, h - 1, r); ctx.stroke();
  ctx.strokeStyle = 'rgba(255,255,255,0.09)';
  ctx.beginPath(); ctx.moveTo(x + r, y + 1); ctx.lineTo(x + w - r, y + 1); ctx.stroke();
  ctx.restore();
}
export function roundRect(ctx, x, y, w, h, r) {
  r = Math.min(r, w / 2, h / 2);
  ctx.moveTo(x + r, y);
  ctx.arcTo(x + w, y, x + w, y + h, r);
  ctx.arcTo(x + w, y + h, x, y + h, r);
  ctx.arcTo(x, y + h, x, y, r);
  ctx.arcTo(x, y, x + w, y, r);
  ctx.closePath();
}
export function setFont(ctx, look, fam, wt, size, trackEm) {
  ctx.font = `${wt} ${Math.max(8, size)}px "${look.fonts[fam] || fam}", sans-serif`;
  ctx.letterSpacing = `${(trackEm ? trackEm * size : 0).toFixed(2)}px`;
}
export function resetSpace(ctx) { ctx.letterSpacing = '0px'; }
