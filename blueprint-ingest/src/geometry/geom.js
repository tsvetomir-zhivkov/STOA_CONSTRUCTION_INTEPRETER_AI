/**
 * Small, dependency-free 2D geometry toolkit. Points are [x, y]; bboxes are [xmin, ymin, xmax, ymax];
 * affine matrices are PDF-style [a, b, c, d, e, f] (x' = a*x + c*y + e, y' = b*x + d*y + f).
 */

/** @typedef {[number, number]} Pt */
/** @typedef {[number, number, number, number]} BBox */

export const round = (n, d = 2) => {
  const f = 10 ** d;
  return Math.round(n * f) / f;
};

/** @param {Pt[]} pts @returns {BBox} */
export function bboxOfPoints(pts) {
  let x0 = Infinity, y0 = Infinity, x1 = -Infinity, y1 = -Infinity;
  for (const [x, y] of pts) {
    if (x < x0) x0 = x;
    if (y < y0) y0 = y;
    if (x > x1) x1 = x;
    if (y > y1) y1 = y;
  }
  return [x0, y0, x1, y1];
}

/** @param {BBox} a @param {BBox} b @returns {BBox} */
export const bboxUnion = (a, b) => [Math.min(a[0], b[0]), Math.min(a[1], b[1]), Math.max(a[2], b[2]), Math.max(a[3], b[3])];

/** @param {BBox} b */
export const bboxArea = (b) => Math.max(0, b[2] - b[0]) * Math.max(0, b[3] - b[1]);

/** @param {BBox} a @param {BBox} b */
export function bboxIou(a, b) {
  const iw = Math.min(a[2], b[2]) - Math.max(a[0], b[0]);
  const ih = Math.min(a[3], b[3]) - Math.max(a[1], b[1]);
  if (iw <= 0 || ih <= 0) return 0;
  const inter = iw * ih;
  return inter / (bboxArea(a) + bboxArea(b) - inter);
}

/** @param {BBox} b @param {Pt} p */
export const bboxContainsPoint = (b, p) => p[0] >= b[0] && p[0] <= b[2] && p[1] >= b[1] && p[1] <= b[3];

/** @param {BBox} b */
export const bboxCenter = (b) => /** @type {Pt} */ ([(b[0] + b[2]) / 2, (b[1] + b[3]) / 2]);

/** @param {Pt[]} poly absolute shoelace area */
export function polygonArea(poly) {
  let s = 0;
  for (let i = 0, n = poly.length; i < n; i++) {
    const [x1, y1] = poly[i];
    const [x2, y2] = poly[(i + 1) % n];
    s += x1 * y2 - x2 * y1;
  }
  return Math.abs(s) / 2;
}

/** @param {Pt[]} poly @returns {Pt} area-weighted centroid (falls back to vertex mean for degenerate shapes) */
export function polygonCentroid(poly) {
  let a = 0, cx = 0, cy = 0;
  for (let i = 0, n = poly.length; i < n; i++) {
    const [x1, y1] = poly[i];
    const [x2, y2] = poly[(i + 1) % n];
    const f = x1 * y2 - x2 * y1;
    a += f;
    cx += (x1 + x2) * f;
    cy += (y1 + y2) * f;
  }
  if (Math.abs(a) < 1e-9) {
    const n = poly.length || 1;
    return [poly.reduce((s, p) => s + p[0], 0) / n, poly.reduce((s, p) => s + p[1], 0) / n];
  }
  return [cx / (3 * a), cy / (3 * a)];
}

/** Ray casting. @param {Pt} p @param {Pt[]} poly */
export function pointInPolygon(p, poly) {
  const [x, y] = p;
  let inside = false;
  for (let i = 0, j = poly.length - 1; i < poly.length; j = i++) {
    const [xi, yi] = poly[i];
    const [xj, yj] = poly[j];
    if (yi > y !== yj > y && x < ((xj - xi) * (y - yi)) / (yj - yi) + xi) inside = !inside;
  }
  return inside;
}

/** Drop consecutive duplicates and a repeated closing point. @param {Pt[]} pts @param {number} [eps] */
export function cleanRing(pts, eps = 1e-6) {
  const out = [];
  for (const p of pts) {
    const last = out[out.length - 1];
    if (!last || Math.abs(last[0] - p[0]) > eps || Math.abs(last[1] - p[1]) > eps) out.push(p);
  }
  if (out.length > 1) {
    const a = out[0], b = out[out.length - 1];
    if (Math.abs(a[0] - b[0]) <= eps && Math.abs(a[1] - b[1]) <= eps) out.pop();
  }
  return out;
}

/** Remove vertices that lie on a straight line. @param {Pt[]} ring @param {number} [eps] */
export function removeCollinear(ring, eps = 1e-3) {
  if (ring.length <= 3) return ring;
  const out = [];
  const n = ring.length;
  for (let i = 0; i < n; i++) {
    const a = ring[(i + n - 1) % n], b = ring[i], c = ring[(i + 1) % n];
    const cross = (b[0] - a[0]) * (c[1] - b[1]) - (b[1] - a[1]) * (c[0] - b[0]);
    const scale = Math.hypot(b[0] - a[0], b[1] - a[1]) * Math.hypot(c[0] - b[0], c[1] - b[1]) || 1;
    if (Math.abs(cross) / scale > eps) out.push(b);
  }
  return out.length >= 3 ? out : ring;
}

/** Andrew monotone chain. @param {Pt[]} pts @returns {Pt[]} */
export function convexHull(pts) {
  const p = [...pts].sort((a, b) => a[0] - b[0] || a[1] - b[1]);
  if (p.length <= 2) return p;
  const cross = (o, a, b) => (a[0] - o[0]) * (b[1] - o[1]) - (a[1] - o[1]) * (b[0] - o[0]);
  const lower = [];
  for (const q of p) {
    while (lower.length >= 2 && cross(lower[lower.length - 2], lower[lower.length - 1], q) <= 0) lower.pop();
    lower.push(q);
  }
  const upper = [];
  for (const q of [...p].reverse()) {
    while (upper.length >= 2 && cross(upper[upper.length - 2], upper[upper.length - 1], q) <= 0) upper.pop();
    upper.push(q);
  }
  return lower.slice(0, -1).concat(upper.slice(0, -1));
}

/**
 * Minimum-area oriented rectangle (rotating calipers brute force over hull edges).
 * @param {Pt[]} pts
 * @returns {{ length:number, width:number, angle:number, center:Pt, corners:Pt[] } | null}
 *   length >= width; angle = direction of the long side in radians.
 */
export function minAreaRect(pts) {
  const hull = convexHull(pts);
  if (hull.length < 3) return null;
  let best = null;
  for (let i = 0; i < hull.length; i++) {
    const [x1, y1] = hull[i];
    const [x2, y2] = hull[(i + 1) % hull.length];
    const ang = Math.atan2(y2 - y1, x2 - x1);
    const c = Math.cos(-ang), s = Math.sin(-ang);
    let minX = Infinity, maxX = -Infinity, minY = Infinity, maxY = -Infinity;
    for (const [x, y] of hull) {
      const rx = x * c - y * s, ry = x * s + y * c;
      if (rx < minX) minX = rx;
      if (rx > maxX) maxX = rx;
      if (ry < minY) minY = ry;
      if (ry > maxY) maxY = ry;
    }
    const area = (maxX - minX) * (maxY - minY);
    if (!best || area < best.area) best = { area, ang, minX, maxX, minY, maxY };
  }
  const { ang, minX, maxX, minY, maxY } = best;
  const back = (rx, ry) => /** @type {Pt} */ ([rx * Math.cos(ang) - ry * Math.sin(ang), rx * Math.sin(ang) + ry * Math.cos(ang)]);
  const w = maxX - minX, h = maxY - minY;
  const corners = [back(minX, minY), back(maxX, minY), back(maxX, maxY), back(minX, maxY)];
  const center = back((minX + maxX) / 2, (minY + maxY) / 2);
  return w >= h
    ? { length: w, width: h, angle: ang, center, corners }
    : { length: h, width: w, angle: ang + Math.PI / 2, center, corners };
}

/** Centerline of a wall-like polygon: long axis of its oriented bounding rectangle. @param {Pt[]} poly */
export function polygonCenterline(poly) {
  const r = minAreaRect(poly);
  if (!r) return null;
  const dx = (Math.cos(r.angle) * r.length) / 2, dy = (Math.sin(r.angle) * r.length) / 2;
  return { a: /** @type {Pt} */ ([r.center[0] - dx, r.center[1] - dy]), b: /** @type {Pt} */ ([r.center[0] + dx, r.center[1] + dy]), thickness: r.width, length: r.length };
}

/** @param {Pt[]} line */
export function polylineLength(line) {
  let l = 0;
  for (let i = 1; i < line.length; i++) l += Math.hypot(line[i][0] - line[i - 1][0], line[i][1] - line[i - 1][1]);
  return l;
}

/** Regular polygon approximating a circle. @param {Pt} c @param {number} r @param {number} [n] */
export function circlePolygon(c, r, n = 16) {
  return Array.from({ length: n }, (_, i) => /** @type {Pt} */ ([c[0] + r * Math.cos((2 * Math.PI * i) / n), c[1] + r * Math.sin((2 * Math.PI * i) / n)]));
}

/** @param {BBox} b @returns {Pt[]} */
export const bboxPolygon = (b) => [[b[0], b[1]], [b[2], b[1]], [b[2], b[3]], [b[0], b[3]]];

// ---- affine matrices -------------------------------------------------------------

export const IDENTITY = /** @type {[number,number,number,number,number,number]} */ ([1, 0, 0, 1, 0, 0]);

/** Compose: apply `a` first, then `b`  (PDF convention: result = a x b). */
export function matMul(a, b) {
  return /** @type {[number,number,number,number,number,number]} */ ([
    a[0] * b[0] + a[1] * b[2],
    a[0] * b[1] + a[1] * b[3],
    a[2] * b[0] + a[3] * b[2],
    a[2] * b[1] + a[3] * b[3],
    a[4] * b[0] + a[5] * b[2] + b[4],
    a[4] * b[1] + a[5] * b[3] + b[5],
  ]);
}

/** @param {number[]} m @param {Pt} p @returns {Pt} */
export const matApply = (m, p) => [m[0] * p[0] + m[2] * p[1] + m[4], m[1] * p[0] + m[3] * p[1] + m[5]];

/** Do two bboxes overlap at all? */
export const bboxesOverlap = (a, b) => a[0] < b[2] && b[0] < a[2] && a[1] < b[3] && b[1] < a[3];
