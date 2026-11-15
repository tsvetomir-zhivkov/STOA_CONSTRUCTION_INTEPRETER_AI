/**
 * Fixtures drawn as plain primitives on a fixture layer (no block names, no fixture word in the layer name), as in the
 * Swiss Dwellings DXFs: layer FIXTURE holds only LWPOLYLINEs and CIRCLEs. A fixture is recognised from its shape, the same
 * rules as translator/extract/swiss_dxf.py (which answers the dev-set fixture counts correctly):
 *
 *   curve  = closed polyline with >= 12 vertices (a smooth outline: bowl, tub)
 *            fill (area / its minimum rectangle) >= 0.9           -> bathtub   (rounded rectangle; an ellipse is pi/4)
 *            else a drain circle inside                          -> sink, subtype washbasin
 *            else                                                -> toilet (the lines touching the bowl are its parts)
 *   drain  = circle with r < 0.05 m not used by a curve           -> shower (its tray: the rectangles around the drain)
 *   rest   = rectangles, open lines and hob circles (0.05 <= r < 0.12 m), grouped where they touch:
 *            a group with a hob circle                           -> other, subtype kitchen_counter
 *            a group with >= 3 open lines                        -> stair
 *            otherwise                                           -> other (confidence 0.5)
 *
 * Every primitive ends up in exactly one fixture (nothing is dropped); each fixture lists the DXF handles of its parts.
 * Coordinates are drawing units; thresholds are metres and converted with mPerUnit. The fixture shape is its body rectangle
 * (the smallest rectangle around the curve) or the minimum rectangle of the group.
 */

import { polygonArea, pointInPolygon, minAreaRect, circlePolygon } from '../geometry/geom.js';

const CURVE_MIN_VERTICES = 12;
const TUB_MIN_FILL = 0.9;
const DRAIN_MAX_R = 0.05; // m
const HOB_MAX_R = 0.12; // m
const STAIR_MIN_LINES = 3;
const TOUCH = 0.05; // m
const GROUP_GAP = 0.01; // m: two parts of one group are within 2 x 5 mm of each other (the reference buffers by 5 mm)

/** distance from point p to segment ab */
function pointSeg(p, a, b) {
  const dx = b[0] - a[0], dy = b[1] - a[1];
  const l2 = dx * dx + dy * dy;
  const t = l2 ? Math.max(0, Math.min(1, ((p[0] - a[0]) * dx + (p[1] - a[1]) * dy) / l2)) : 0;
  return Math.hypot(p[0] - a[0] - t * dx, p[1] - a[1] - t * dy);
}
const cross = (o, a, b) => (a[0] - o[0]) * (b[1] - o[1]) - (a[1] - o[1]) * (b[0] - o[0]);
function segsCross(a, b, c, d) {
  const d1 = cross(c, d, a), d2 = cross(c, d, b), d3 = cross(a, b, c), d4 = cross(a, b, d);
  return ((d1 > 0) !== (d2 > 0)) && ((d3 > 0) !== (d4 > 0)) && d1 !== 0 && d2 !== 0 && d3 !== 0 && d4 !== 0;
}
const segsOf = (s) => {
  if (s.pts.length === 1) return [[s.pts[0], s.pts[0]]];
  const out = [];
  for (let i = 1; i < s.pts.length; i++) out.push([s.pts[i - 1], s.pts[i]]);
  if (s.filled) out.push([s.pts[s.pts.length - 1], s.pts[0]]);
  return out;
};

/**
 * Euclidean distance between two shapes { pts, filled } (a point is one vertex, a line is open, a filled shape is a ring);
 * 0 when they touch or one contains the other.
 */
export function shapeDistance(a, b) {
  if (b.filled && a.pts.some((p) => pointInPolygon(p, b.pts))) return 0;
  if (a.filled && b.pts.some((p) => pointInPolygon(p, a.pts))) return 0;
  let best = Infinity;
  const sa = segsOf(a), sb = segsOf(b);
  for (const [p, q] of sa) for (const [r, s] of sb) {
    if (segsCross(p, q, r, s)) return 0;
    best = Math.min(best, pointSeg(p, r, s), pointSeg(q, r, s), pointSeg(r, p, q), pointSeg(s, p, q));
  }
  return best;
}

/** every point of `inner` lies inside `ring` or within `tol` of its outline (shapely: ring.buffer(tol).contains(inner)) */
function containsWithin(ring, inner, tol) {
  const edges = segsOf({ pts: ring, filled: true });
  return inner.every((p) => pointInPolygon(p, ring) || edges.some(([a, b]) => pointSeg(p, a, b) <= tol));
}

const dropClosingDuplicate = (pts) => (pts.length > 1 && Math.hypot(pts[0][0] - pts.at(-1)[0], pts[0][1] - pts.at(-1)[1]) < 1e-9 ? pts.slice(0, -1) : pts);

/**
 * @param {Array<{ handle:string|null, type:'LWPOLYLINE'|'LINE'|'CIRCLE', pts?:number[][], closed?:boolean, c?:number[], r?:number }>} prims
 *        primitives of one fixture layer, in drawing units
 * @param {number} mPerUnit
 * @returns {Array<{ category:string, subtype:string|null, ring:number[][], handle:string|null, parts:string[], confidence:number, rule:string }>}
 */
export function classifyFixturePrimitives(prims, mPerUnit) {
  const U = (m) => m / mPerUnit; // metres -> drawing units
  const curves = [], rects = [], lines = [], drains = [], hobs = [];
  prims.forEach((e, i) => {
    const h = e.handle ?? `#${i}`;
    if (e.type === 'CIRCLE') {
      const r = e.r;
      if (r < U(DRAIN_MAX_R)) drains.push({ h, c: e.c });
      else if (r < U(HOB_MAX_R)) hobs.push({ h, shape: { pts: [e.c], filled: false } });
      else rects.push({ h, ring: circlePolygon(e.c, r, 32), shape: null });
      return;
    }
    const pts = e.pts ?? [];
    const isRing = pts.length >= 3 && (e.closed || Math.hypot(pts[0][0] - pts.at(-1)[0], pts[0][1] - pts.at(-1)[1]) < 1e-6);
    const ring = isRing ? dropClosingDuplicate(pts) : null;
    if (ring && ring.length >= CURVE_MIN_VERTICES) curves.push({ h, ring });
    else if (ring && ring.length >= 3) rects.push({ h, ring });
    else if (pts.length >= 2) lines.push({ h, shape: { pts, filled: false } });
  });
  for (const r of rects) r.shape = { pts: r.ring, filled: true };

  const out = [];
  const used = new Set();
  const emit = (category, h, ring, parts, { subtype = null, confidence = 1, rule }) => {
    for (const p of parts) used.add(p);
    out.push({ category, subtype, ring, handle: h, parts: [...new Set(parts)].sort(), confidence, rule });
  };
  const body = (ring) => {
    let best = null;
    for (const r of rects) if (containsWithin(r.ring, ring, U(TOUCH))) { const a = Math.abs(polygonArea(r.ring)); if (!best || a < best.a) best = { a, r }; }
    return best?.r ?? null;
  };

  for (const c of curves) {
    const mr = minAreaRect(c.ring);
    const fill = Math.abs(polygonArea(c.ring)) / Math.max(mr ? mr.length * mr.width : 0, 1e-12);
    const rect = body(c.ring);
    const parts = [c.h, ...(rect ? [rect.h] : [])];
    const shape = rect ? rect.ring : c.ring;
    const inner = drains.filter((d) => pointInPolygon(d.c, c.ring)).map((d) => d.h);
    if (fill >= TUB_MIN_FILL) { emit('bathtub', c.h, shape, [...parts, ...inner], { rule: `curve, fill ${fill.toFixed(2)} >= ${TUB_MIN_FILL}` }); continue; }
    if (inner.length) { emit('sink', c.h, shape, [...parts, ...inner], { subtype: 'washbasin', rule: `curve, fill ${fill.toFixed(2)}, drain inside` }); continue; }
    const bowl = { pts: c.ring, filled: true };
    const near = lines.filter((l) => shapeDistance(l.shape, bowl) <= U(TOUCH)).map((l) => l.h);
    emit('toilet', c.h, shape, [...parts, ...near], { rule: `curve, fill ${fill.toFixed(2)}, no drain` });
  }
  for (const d of drains) {
    if (used.has(d.h)) continue;
    const tray = rects.filter((r) => !used.has(r.h) && pointInPolygon(d.c, r.ring)).sort((a, b) => Math.abs(polygonArea(a.ring)) - Math.abs(polygonArea(b.ring)));
    const shape = tray.length ? tray.at(-1).ring : circlePolygon(d.c, U(0.4), 16);
    emit('shower', d.h, shape, [d.h, ...tray.map((r) => r.h)], { rule: tray.length ? 'drain outside any curve, tray around it' : 'drain outside any curve, no tray (0.4 m around the drain)' });
  }

  // the rest: group touching parts (union-find), then name each group
  const rest = [...rects.map((r) => ({ ...r, kind: 'rect' })), ...lines.map((l) => ({ ...l, kind: 'line' })), ...hobs.map((o) => ({ ...o, kind: 'hob' }))].filter((x) => !used.has(x.h));
  const parent = rest.map((_, i) => i);
  const find = (i) => (parent[i] === i ? i : (parent[i] = find(parent[i])));
  const box = (s) => s.pts.reduce((b, p) => [Math.min(b[0], p[0]), Math.min(b[1], p[1]), Math.max(b[2], p[0]), Math.max(b[3], p[1])], [Infinity, Infinity, -Infinity, -Infinity]);
  const boxes = rest.map((x) => box(x.shape));
  const gap = U(GROUP_GAP);
  for (let i = 0; i < rest.length; i++) for (let j = i + 1; j < rest.length; j++) {
    const a = boxes[i], b = boxes[j];
    if (a[0] - gap > b[2] || b[0] - gap > a[2] || a[1] - gap > b[3] || b[1] - gap > a[3]) continue;
    if (find(i) !== find(j) && shapeDistance(rest[i].shape, rest[j].shape) <= gap) parent[find(i)] = find(j);
  }
  const groups = new Map();
  rest.forEach((x, i) => { const g = find(i); if (!groups.has(g)) groups.set(g, []); groups.get(g).push(x); });
  for (const members of groups.values()) {
    const hs = members.map((m) => m.h);
    const r = minAreaRect(members.flatMap((m) => m.shape.pts));
    const shape = r ? r.corners : members[0].shape.pts.length >= 3 ? members[0].shape.pts : circlePolygon(members[0].shape.pts[0], U(0.1), 8);
    const nHob = members.filter((m) => m.kind === 'hob').length, nLines = members.filter((m) => m.kind === 'line').length;
    if (nHob) emit('other', hs[0], shape, hs, { subtype: 'kitchen_counter', rule: `${nHob} hob circle(s)` });
    else if (nLines >= STAIR_MIN_LINES) emit('stair', hs[0], shape, hs, { rule: `${nLines} tread lines` });
    else emit('other', hs[0], shape, hs, { confidence: 0.5, rule: 'no fixture rule matched' });
  }
  return out;
}
