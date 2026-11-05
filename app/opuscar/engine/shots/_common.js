// helper dùng chung cho các shot: dựng TEXTS từ "plan items"
import * as C from '../core.js';

// item = {key, text, fit, x, y, align, tIn, ...}
export const planTexts = plan =>
  (plan.items || []).map(i => ({ key: i.key, text: i.text, box: C.blockBox(i.fit, i), tIn: i.tIn }));
