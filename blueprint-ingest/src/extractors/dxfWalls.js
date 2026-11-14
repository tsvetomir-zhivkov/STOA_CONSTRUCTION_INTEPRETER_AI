/**
 * Walls from line pairing (DXF). Works in drawing units; thresholds are given in metres and converted with mPerUnit.
 *
 *   1. every segment on a wall-classified layer (polylines split into segments), per layer
 *   2. segments that face a parallel partner (a pair under rule 3) are split where the partner starts/ends, so one long face
 *      opposite several shorter faces (a wall interrupted by junctions) yields one piece per partner; each piece is used once
 *   3. a pair = angle < 3 deg, perpendicular distance 0.05..0.60 m, overlap >= 60 % of the shorter piece, (combined) overlap at least
 *      as long as the distance (a wall run, not a cross-section through a wall end), and (for closed outlines) the strip
 *      between them lies inside the outline, i.e. it is wall material and not a gap between walls
 *   4. greedy, closest partner first; ONE wall per pair: the overlap strip, thickness = measured perpendicular distance
 *   5. pieces without a partner: short ones whose both ends touch a paired face are wall-end caps / junction edges and
 *      are dropped; the rest fall back to the old assumed 0.2 m thickness (flag assumed_thickness)
 *   6. collinear walls (< 3 deg, same thickness within 0.02 m, ends within 0.05 m) are merged into one longer wall
 */

import { pointInPolygon } from '../geometry/geom.js';

const DEG = Math.PI / 180;

/**
 * @typedef {{ a:number[], b:number[], layer:string, ring:number[][]|null, thinRing:boolean }} WallSeg
 * @typedef {{ polygon:number[][], centerline:number[][], thickness:number, assumed:boolean, thinRing:boolean, layer:string, merged:number }} WallOut
 */

/**
 * @param {WallSeg[]} segs
 * @param {{ mPerUnit:number }} o
 * @returns {{ walls: WallOut[], stats: { measured:number, assumed:number, merged:number, capsDropped:number, pairs:number } }}
 */
export function wallsFromSegments(segs, { mPerUnit }) {
  const U = 1 / mPerUnit; // drawing units per metre
  const cfg = { minD: 0.05 * U, maxD: 0.6 * U, cosTol: Math.cos(3 * DEG), overlap: 0.6, minLen: 0.2 * U, tol: 0.01 * U, splitEps: 0.01 * U };
  const out = [];
  const stats = { measured: 0, assumed: 0, merged: 0, capsDropped: 0, pairs: 0 };
  const byLayer = new Map();
  for (const s of segs) (byLayer.get(s.layer) ?? byLayer.set(s.layer, []).get(s.layer)).push(s);

  for (const layerSegs of byLayer.values()) {
    const pieces = split(layerSegs.map(geom).filter((s) => s.len > cfg.tol), cfg);
    const cands = pairCandidates(pieces, cfg).map(({ i, j, r }) => ({ i, j, ...r }));
    cands.sort((x, y) => x.d - y.d || y.ov - x.ov);
    const used = new Uint8Array(pieces.length);
    const paired = [];
    for (const c of cands) {
      if (used[c.i] || used[c.j]) continue;
      used[c.i] = used[c.j] = 1;
      paired.push(c);
      out.push(pairWall(pieces[c.i], pieces[c.j], c));
      stats.pairs++;
    }
    // endpoints of paired faces, to recognise caps
    const ends = [];
    for (const c of paired) for (const k of [c.i, c.j]) ends.push(pieces[k].a, pieces[k].b);
    const touches = (p) => ends.some((q) => Math.hypot(p[0] - q[0], p[1] - q[1]) <= cfg.tol);
    // a small closed outline none of whose sides paired (a pier between windows, a column, a wall stub thicker than 0.60 m)
    // IS its own measured footprint: one solid wall, instead of four assumed strips around a hole
    const ringPaired = new Map();
    pieces.forEach((s, k) => { if (s.ring) ringPaired.set(s.ring, (ringPaired.get(s.ring) ?? false) || !!used[k]); });
    const solidRings = new Set();
    for (const [ring, anyPaired] of ringPaired) {
      if (anyPaired) continue;
      const w = ringWall(ring, s0(layerSegs, ring), cfg, U);
      if (w) { out.push(w); solidRings.add(ring); stats.solidRings = (stats.solidRings ?? 0) + 1; }
    }
    // end caps: a CHAIN of unpaired, collinear pieces of one outline (a cap split by a vertex) whose two outer ends touch
    // paired faces and whose total length is at most the pairing distance
    const cap = new Uint8Array(pieces.length);
    const free = pieces.map((p, k) => k).filter((k) => !used[k] && pieces[k].ring && !solidRings.has(pieces[k].ring));
    const same = (p, q) => Math.hypot(p[0] - q[0], p[1] - q[1]) <= cfg.tol;
    const seen = new Set();
    for (const k of free) {
      if (seen.has(k)) continue;
      const chain = [k]; seen.add(k);
      for (let grow = true; grow;) {
        grow = false;
        for (const m of free) {
          if (seen.has(m) || pieces[m].ring !== pieces[k].ring || Math.abs(pieces[m].u[0] * pieces[k].u[0] + pieces[m].u[1] * pieces[k].u[1]) < cfg.cosTol) continue;
          if (chain.some((c) => [pieces[c].a, pieces[c].b].some((p) => same(p, pieces[m].a) || same(p, pieces[m].b)))) { chain.push(m); seen.add(m); grow = true; }
        }
      }
      // the chain's outer ends: endpoints not shared by another piece of the chain
      const pts = chain.flatMap((c) => [pieces[c].a, pieces[c].b]);
      const outer = pts.filter((p, i) => !pts.some((q, j) => j !== i && same(p, q)));
      const len = chain.reduce((t, c) => t + pieces[c].len, 0);
      if (len <= cfg.maxD && outer.length === 2 && outer.every(touches)) for (const c of chain) cap[c] = 1;
    }
    pieces.forEach((s, k) => {
      if (used[k] || solidRings.has(s.ring)) return;
      if (cap[k]) { stats.capsDropped++; return; }
      if (s.len < cfg.minLen) return;
      out.push(assumedWall(s, 0.2 * U));
    });
  }
  const merged = mergeCollinear(out, { cosTol: cfg.cosTol, dt: 0.02 * U, lateral: 0.03 * U, gap: 0.05 * U });
  stats.merged = out.length - merged.length;
  const br = bridgeJunctions(merged, { cosTol: cfg.cosTol, dt: 0.02 * U, lateral: 0.03 * U, maxGap: cfg.maxD, tol: 0.05 * U });
  stats.bridged = br.bridged;
  merged.splice(0, merged.length, ...br.walls);
  const j = joinWalls(merged);
  stats.junctions = j.corners + j.tees;
  stats.corners = j.corners;
  stats.tees = j.tees;
  // an assumed wall whose whole line already lies on or inside measured walls adds nothing (typically the outer faces of
  // a corner square, which the mitre now fills): drop it instead of drawing a 0.2 m blob that sticks out of the corner
  const kept = dropCovered(merged, 0.02 * U);
  stats.covered = merged.length - kept.length;
  for (const w of kept) w.assumed ? stats.assumed++ : stats.measured++;
  return { walls: kept, stats };
}

function dropCovered(walls, tol) {
  const measured = walls.filter((w) => !w.assumed);
  const segD = (p, a, b) => { const dx = b[0] - a[0], dy = b[1] - a[1], L = dx * dx + dy * dy; const t = L ? Math.max(0, Math.min(1, ((p[0] - a[0]) * dx + (p[1] - a[1]) * dy) / L)) : 0; return Math.hypot(p[0] - a[0] - t * dx, p[1] - a[1] - t * dy); };
  const onOrIn = (p, poly) => pointInPolygon(p, poly) || poly.some((q, i) => segD(p, q, poly[(i + 1) % poly.length]) <= tol);
  return walls.filter((w) => {
    if (!w.assumed) return true;
    const [a, b] = w.centerline;
    const pts = [0, 0.25, 0.5, 0.75, 1].map((t) => [a[0] + (b[0] - a[0]) * t, a[1] + (b[1] - a[1]) * t]);
    return !pts.every((p) => measured.some((m) => onOrIn(p, m.polygon)));
  });
}

// ---- junctions --------------------------------------------------------------------------------------------------

/**
 * At a T or X junction the through-wall's face opposite the stem is a short unpaired piece (dropped as an end cap), so the
 * through-wall arrives as two collinear halves with a stem-wide hole. Merge such halves across a gap of up to `maxGap`, but
 * ONLY when a third, non-parallel wall ends inside the gap (a doorway gap has no wall ending in it and stays open).
 */
export function bridgeJunctions(walls, { cosTol, dt, lateral, maxGap, tol }) {
  let list = walls.map((w) => ({ ...w }));
  let bridged = 0;
  const axis = (w) => { const [a, b] = w.centerline; const len = Math.hypot(b[0] - a[0], b[1] - a[1]); return { a, b, len, u: len ? [(b[0] - a[0]) / len, (b[1] - a[1]) / len] : [1, 0] }; };
  for (let changed = true; changed;) {
    changed = false;
    outer: for (let i = 0; i < list.length; i++) {
      for (let j = i + 1; j < list.length; j++) {
        const w1 = list[i], w2 = list[j];
        if (w1.layer !== w2.layer || Math.abs(w1.thickness - w2.thickness) > dt) continue;
        const A = axis(w1), B = axis(w2);
        if (!A.len || !B.len || Math.abs(dot(A.u, B.u)) < cosTol) continue;
        const sd = (p) => crs(A.u, sub(p, A.a)), pr = (p) => dot(sub(p, A.a), A.u);
        if (Math.abs(sd(B.a)) > lateral || Math.abs(sd(B.b)) > lateral) continue;
        const lo = Math.min(pr(B.a), pr(B.b)), hi = Math.max(pr(B.a), pr(B.b));
        const [g0, g1] = hi < 0 ? [hi, 0] : [A.len, lo];
        if (g1 - g0 <= 0 || g1 - g0 > maxGap) continue; // overlapping/touching ones were merged already
        const stem = list.some((K, k) => {
          if (k === i || k === j) return false;
          const C = axis(K);
          if (!C.len || Math.abs(crs(A.u, C.u)) < Math.sin(15 * DEG)) return false;
          return [C.a, C.b].some((E) => pr(E) > g0 - tol && pr(E) < g1 + tol && Math.abs(sd(E)) <= w1.thickness / 2 + K.thickness / 2 + tol);
        });
        if (!stem) continue;
        const m = tryMerge(w1, w2, { cosTol, dt, lateral, gap: maxGap + 1e-9 });
        if (!m) continue;
        list[i] = m; list.splice(j, 1); bridged++; changed = true;
        break outer;
      }
    }
  }
  return { walls: list, bridged };
}

const sub = (p, q) => [p[0] - q[0], p[1] - q[1]];
const add = (p, q) => [p[0] + q[0], p[1] + q[1]];
const mul = (p, k) => [p[0] * k, p[1] * k];
const dot = (p, q) => p[0] * q[0] + p[1] * q[1];
const crs = (p, q) => p[0] * q[1] - p[1] * q[0];
/** intersection of the lines p + s*u and q + t*v, or null when (nearly) parallel */
function lineX(p, u, q, v) {
  const den = crs(u, v);
  if (Math.abs(den) < 1e-12) return null;
  return add(p, mul(u, crs(sub(q, p), v) / den));
}

/**
 * Close wall corners (in place). Each wall is its centreline a->b plus thickness t; its two faces are the lines a +- n*t/2.
 * Two walls whose centrelines meet (cross, or would cross within half their combined thickness of a wall end) are joined:
 *   L corner (the meeting point is near an end of BOTH walls): both faces of each wall are cut at the mitre, the diagonal
 *     through the intersections of the inner faces and of the outer faces, so the two polygons share that edge exactly
 *   T junction (near an end of one wall, inside the other): the stem's faces are cut at the near face of the through-wall
 *   X crossing (inside both): left alone
 * Each wall end is joined at most once (nearest meeting point first). Pairs closer than 15 degrees to parallel are not
 * joined (a mitre would become a long spike), and no end moves by more than its own wall's thickness plus the reach, so
 * a join never overlaps or extends a wall by more than a thickness.
 * @returns {{ corners:number, tees:number }}
 */
export function joinWalls(walls) {
  const W = walls.map((w) => {
    const [a, b] = w.centerline;
    const len = Math.hypot(b[0] - a[0], b[1] - a[1]);
    const u = len ? [(b[0] - a[0]) / len, (b[1] - a[1]) / len] : [1, 0];
    // an exact closed outline (solidRing) is never reshaped: len 0 keeps it out of every join
    return { w, a, b, len: w.solidRing ? 0 : len, u, n: [-u[1], u[0]], t: w.thickness, cut: [null, null] };
  });
  const minSin = Math.sin(15 * DEG);
  const cands = [];
  for (let i = 0; i < W.length; i++) {
    for (let j = i + 1; j < W.length; j++) {
      const A = W[i], B = W[j];
      if (!A.len || !B.len || Math.abs(crs(A.u, B.u)) < minSin) continue;
      const P = lineX(A.a, A.u, B.a, B.u);
      if (!P) continue;
      const reach = (A.t + B.t) / 2;
      const where = (S) => {
        const s = dot(sub(P, S.a), S.u);
        if (Math.abs(s) <= reach) return { end: 0, d: Math.abs(s) };
        if (Math.abs(s - S.len) <= reach) return { end: 1, d: Math.abs(s - S.len) };
        return s > 0 && s < S.len ? { inside: true } : null;
      };
      const wa = where(A), wb = where(B);
      if (!wa || !wb) continue;
      if (wa.end != null && wb.end != null) cands.push({ type: 'L', i, j, ei: wa.end, ej: wb.end, P, d: wa.d + wb.d });
      else if (wa.end != null && wb.inside) cands.push({ type: 'T', stem: i, through: j, e: wa.end, P, d: wa.d });
      else if (wb.end != null && wa.inside) cands.push({ type: 'T', stem: j, through: i, e: wb.end, P, d: wb.d });
    }
  }
  cands.sort((x, y) => x.d - y.d);
  const taken = new Set(); // "wall:end"
  let corners = 0, tees = 0;
  for (const c of cands) {
    if (c.type === 'L') {
      if (taken.has(`${c.i}:${c.ei}`) || taken.has(`${c.j}:${c.ej}`)) continue;
      const A = W[c.i], B = W[c.j];
      // d = unit direction pointing OUT of each wall at the joined end
      const dA = c.ei ? A.u : mul(A.u, -1), dB = c.ej ? B.u : mul(B.u, -1);
      // the 4 face intersections; inner = behind both ends (inside both bodies), outer = ahead of both
      const fA = [add(A.a, mul(A.n, A.t / 2)), add(A.a, mul(A.n, -A.t / 2))];
      const fB = [add(B.a, mul(B.n, B.t / 2)), add(B.a, mul(B.n, -B.t / 2))];
      const X = [];
      for (const p of fA) for (const q of fB) X.push(lineX(p, A.u, q, B.u));
      if (X.some((x) => !x)) continue;
      const inner = X.find((x) => dot(sub(x, c.P), dA) < 0 && dot(sub(x, c.P), dB) < 0);
      const outer = X.find((x) => dot(sub(x, c.P), dA) > 0 && dot(sub(x, c.P), dB) > 0);
      if (!inner || !outer) continue;
      const m = { p: inner, v: sub(outer, inner) };
      if (!cutEnd(A, c.ei, m) || !cutEnd(B, c.ej, m)) continue;
      taken.add(`${c.i}:${c.ei}`); taken.add(`${c.j}:${c.ej}`);
      corners++;
    } else {
      if (taken.has(`${c.stem}:${c.e}`)) continue;
      const S = W[c.stem], T = W[c.through];
      // the through-wall face on the stem's side (the stem body lies behind its joined end)
      const body = c.e ? mul(S.u, -1) : S.u;
      const side = dot(body, T.n) > 0 ? 1 : -1;
      const m = { p: add(T.a, mul(T.n, (side * T.t) / 2)), v: T.u };
      if (!cutEnd(S, c.e, m)) continue;
      taken.add(`${c.stem}:${c.e}`);
      tees++;
    }
  }
  for (const S of W) if (S.cut[0] || S.cut[1]) rebuild(S);
  return { corners, tees };

  /** set end `e` of wall S to the cut line m, if it moves that end by at most t + reach (no spikes, no deep overlaps) */
  function cutEnd(S, e, m) {
    const ends = [];
    for (const k of [1, -1]) {
      const face = add(S.a, mul(S.n, (k * S.t) / 2));
      const x = lineX(face, S.u, m.p, m.v);
      if (!x) return false;
      ends.push(x);
    }
    const c = lineX(S.a, S.u, m.p, m.v);
    if (!c) return false;
    const was = e ? S.len : 0;
    for (const x of [...ends, c]) if (Math.abs(dot(sub(x, S.a), S.u) - was) > 2 * S.t + 1e-9) return false;
    // never cut past the other end
    const s = dot(sub(c, S.a), S.u);
    if (e ? s <= 0 : s >= S.len) return false;
    S.cut[e] = { left: ends[0], right: ends[1], centre: c };
    return true;
  }

  function rebuild(S) {
    const L0 = S.cut[0]?.left ?? add(S.a, mul(S.n, S.t / 2)), R0 = S.cut[0]?.right ?? add(S.a, mul(S.n, -S.t / 2));
    const L1 = S.cut[1]?.left ?? add(S.b, mul(S.n, S.t / 2)), R1 = S.cut[1]?.right ?? add(S.b, mul(S.n, -S.t / 2));
    S.w.polygon = [L0, L1, R1, R0];
    S.w.centerline = [S.cut[0]?.centre ?? S.a, S.cut[1]?.centre ?? S.b];
    S.w.joined = (S.cut[0] ? 1 : 0) + (S.cut[1] ? 1 : 0);
  }
}

// ---- geometry helpers ---------------------------------------------------------------------------------------------

function geom(s) {
  const dx = s.b[0] - s.a[0], dy = s.b[1] - s.a[1];
  const len = Math.hypot(dx, dy);
  return { ...s, len, u: len ? [dx / len, dy / len] : [1, 0] };
}

const proj = (s, p) => (p[0] - s.a[0]) * s.u[0] + (p[1] - s.a[1]) * s.u[1];
const sdist = (s, p) => (p[0] - s.a[0]) * -s.u[1] + (p[1] - s.a[1]) * s.u[0]; // signed, left of s positive
const at = (s, t) => [s.a[0] + s.u[0] * t, s.a[1] + s.u[1] * t];

/** parallel + in distance range + overlapping: { d, sd, t0, t1, ov, solid } relative to s1, else null */
function relation(s1, s2, cfg) {
  if (Math.abs(s1.u[0] * s2.u[0] + s1.u[1] * s2.u[1]) < cfg.cosTol) return null;
  const d1 = sdist(s1, s2.a), d2 = sdist(s1, s2.b);
  if (Math.sign(d1) !== Math.sign(d2) && Math.abs(d1) > cfg.tol && Math.abs(d2) > cfg.tol) return null;
  const sd = (d1 + d2) / 2, d = Math.abs(sd);
  if (d < cfg.minD || d > cfg.maxD) return null;
  const p0 = proj(s1, s2.a), p1 = proj(s1, s2.b);
  const t0 = Math.max(0, Math.min(p0, p1)), t1 = Math.min(s1.len, Math.max(p0, p1));
  const ov = t1 - t0;
  if (ov <= cfg.tol) return null;
  // the strip between the faces must be wall material (inside an outline) when the faces come from closed outlines
  const mid = at(s1, (t0 + t1) / 2);
  const c = [mid[0] - s1.u[1] * sd / 2, mid[1] + s1.u[0] * sd / 2];
  const rings = [s1.ring, s2.ring].filter(Boolean);
  const solid = !rings.length || rings.some((r) => pointInPolygon(c, r));
  return { d, sd, t0, t1, ov, solid };
}

/** rule 3 without the run-length test: overlap >= 60 % of the shorter, strip is wall material */
const basePair = (c, s1, s2, cfg) => !!c && c.solid && c.ov >= cfg.overlap * Math.min(s1.len, s2.len);

/**
 * All valid pairs. The run-length test (overlap at least as long as the distance: a wall run, not a cross-section through a
 * wall end) is applied to the COMBINED overlap of the collinear pieces facing the same face, so a face interrupted by a
 * T junction (two short pieces either side of the stem) still pairs with the continuous face opposite.
 */
function pairCandidates(segs, cfg) {
  const base = [];
  for (let i = 0; i < segs.length; i++) {
    for (let j = i + 1; j < segs.length; j++) {
      const r = relation(segs[i], segs[j], cfg);
      if (basePair(r, segs[i], segs[j], cfg)) base.push({ i, j, r });
    }
  }
  // collinear pieces form one face line; the run-length test uses the total overlap between the two face lines
  const lineOf = new Int32Array(segs.length).fill(-1), refs = [];
  segs.forEach((sg, k) => {
    const m = [(sg.a[0] + sg.b[0]) / 2, (sg.a[1] + sg.b[1]) / 2];
    const L = refs.findIndex((r) => Math.abs(r.u[0] * sg.u[0] + r.u[1] * sg.u[1]) >= cfg.cosTol && Math.abs(sdist(r, m)) <= cfg.tol);
    lineOf[k] = L >= 0 ? L : refs.push(sg) - 1;
  });
  const total = new Map();
  const key = (i, j) => (lineOf[i] < lineOf[j] ? `${lineOf[i]}|${lineOf[j]}` : `${lineOf[j]}|${lineOf[i]}`);
  for (const { i, j, r } of base) total.set(key(i, j), (total.get(key(i, j)) ?? 0) + r.ov);
  return base.filter(({ i, j, r }) => r.ov >= r.d || total.get(key(i, j)) >= r.d);
}

/** split each piece where a parallel, facing partner begins or ends */
function split(segs, cfg) {
  const cuts = segs.map(() => []);
  for (const { i, j } of pairCandidates(segs, cfg)) {
    for (const p of [segs[j].a, segs[j].b]) cuts[i].push(proj(segs[i], p));
    for (const p of [segs[i].a, segs[i].b]) cuts[j].push(proj(segs[j], p));
  }
  const pieces = [];
  segs.forEach((s, k) => {
    const ts = [0, ...cuts[k].filter((t) => t > cfg.splitEps && t < s.len - cfg.splitEps).sort((x, y) => x - y), s.len];
    let prev = 0;
    for (const t of ts.slice(1)) {
      if (t - prev < cfg.splitEps) continue;
      pieces.push(geom({ ...s, a: at(s, prev), b: at(s, t) }));
      prev = t;
    }
  });
  return pieces;
}

function pairWall(s1, s2, c) {
  const n = [-s1.u[1] * c.sd, s1.u[0] * c.sd];
  const A0 = at(s1, c.t0), A1 = at(s1, c.t1);
  const B1 = [A1[0] + n[0], A1[1] + n[1]], B0 = [A0[0] + n[0], A0[1] + n[1]];
  const half = [n[0] / 2, n[1] / 2];
  return {
    polygon: [A0, A1, B1, B0], centerline: [[A0[0] + half[0], A0[1] + half[1]], [A1[0] + half[0], A1[1] + half[1]]],
    thickness: c.d, assumed: false, thinRing: s1.thinRing && s2.thinRing, layer: s1.layer, merged: 0,
  };
}

const s0 = (segs, ring) => segs.find((x) => x.ring === ring);

/** closed ring -> one wall over its exact outline, if it is small (<= 4 m2) and wall-like (min-area rectangle width >= 0.05 m) */
function ringWall(ring, seg, cfg, U) {
  if (!seg || ring.length < 3) return null;
  let area = 0;
  for (let i = 0; i < ring.length; i++) { const [x0, y0] = ring[i], [x1, y1] = ring[(i + 1) % ring.length]; area += x0 * y1 - x1 * y0; }
  area = Math.abs(area) / 2;
  if (area > 4 * U * U || area < 1e-9) return null;
  let best = null;
  for (let i = 0; i < ring.length; i++) {
    const [x0, y0] = ring[i], [x1, y1] = ring[(i + 1) % ring.length];
    const l = Math.hypot(x1 - x0, y1 - y0);
    if (l < 1e-9) continue;
    const u = [(x1 - x0) / l, (y1 - y0) / l], v = [-u[1], u[0]];
    let a0 = Infinity, a1 = -Infinity, b0 = Infinity, b1 = -Infinity;
    for (const [x, y] of ring) { const a = x * u[0] + y * u[1], b = x * v[0] + y * v[1]; a0 = Math.min(a0, a); a1 = Math.max(a1, a); b0 = Math.min(b0, b); b1 = Math.max(b1, b); }
    const r = (a1 - a0) >= (b1 - b0) ? { u, v, a0, a1, b0, b1 } : { u: v, v: [-u[0], -u[1]], a0: b0, a1: b1, b0: -a1, b1: -a0 };
    const ar = (r.a1 - r.a0) * (r.b1 - r.b0);
    if (!best || ar < best.ar) best = { ...r, ar };
  }
  const t = best.b1 - best.b0;
  if (t < cfg.minD) return null;
  const mid = (best.b0 + best.b1) / 2;
  const P = (a, b) => [best.u[0] * a + best.v[0] * b, best.u[1] * a + best.v[1] * b];
  return { polygon: ring.map((p) => [p[0], p[1]]), centerline: [P(best.a0, mid), P(best.a1, mid)], thickness: t, assumed: false, thinRing: false, solidRing: true, layer: seg.layer, merged: 0 };
}

function assumedWall(s, t) {
  const nx = -s.u[1] * (t / 2), ny = s.u[0] * (t / 2);
  return {
    polygon: [[s.a[0] + nx, s.a[1] + ny], [s.b[0] + nx, s.b[1] + ny], [s.b[0] - nx, s.b[1] - ny], [s.a[0] - nx, s.a[1] - ny]],
    centerline: [s.a, s.b], thickness: t, assumed: true, thinRing: false, layer: s.layer, merged: 0,
  };
}

/** merge collinear walls of the same layer, same assumption status and similar thickness whose ends meet */
export function mergeCollinear(walls, { cosTol, dt, lateral, gap }) {
  let list = walls.map((w) => ({ ...w }));
  for (let changed = true; changed;) {
    changed = false;
    for (let i = 0; i < list.length; i++) {
      for (let j = i + 1; j < list.length; j++) {
        const m = tryMerge(list[i], list[j], { cosTol, dt, lateral, gap });
        // list[i] grew: drop j and rescan i's partners from the start
        if (m) { list[i] = m; list.splice(j, 1); changed = true; j = i; }
      }
    }
  }
  return list;
}

function tryMerge(w1, w2, o) {
  if (w1.solidRing || w2.solidRing) return null; // an exact closed outline is never reshaped
  if (w1.layer !== w2.layer || w1.assumed !== w2.assumed || Math.abs(w1.thickness - w2.thickness) > o.dt) return null;
  const s1 = geom({ a: w1.centerline[0], b: w1.centerline[1] }), s2 = geom({ a: w2.centerline[0], b: w2.centerline[1] });
  if (!s1.len || !s2.len || Math.abs(s1.u[0] * s2.u[0] + s1.u[1] * s2.u[1]) < o.cosTol) return null;
  if (Math.abs(sdist(s1, s2.a)) > o.lateral || Math.abs(sdist(s1, s2.b)) > o.lateral) return null;
  const p0 = proj(s1, s2.a), p1 = proj(s1, s2.b);
  const lo = Math.min(p0, p1), hi = Math.max(p0, p1);
  const gap = Math.max(lo - s1.len, -hi); // > 0: distance between the ends; <= 0: they overlap
  if (gap > o.gap) return null;
  const tMin = Math.min(0, lo), tMax = Math.max(s1.len, hi);
  const off = (sdist(s1, s2.a) + sdist(s1, s2.b)) / 2 * (s2.len / (s1.len + s2.len)); // length-weighted axis
  const n = [-s1.u[1], s1.u[0]];
  const a = [s1.a[0] + s1.u[0] * tMin + n[0] * off, s1.a[1] + s1.u[1] * tMin + n[1] * off];
  const b = [s1.a[0] + s1.u[0] * tMax + n[0] * off, s1.a[1] + s1.u[1] * tMax + n[1] * off];
  const t = (w1.thickness * s1.len + w2.thickness * s2.len) / (s1.len + s2.len);
  const h = [n[0] * t / 2, n[1] * t / 2];
  return {
    polygon: [[a[0] + h[0], a[1] + h[1]], [b[0] + h[0], b[1] + h[1]], [b[0] - h[0], b[1] - h[1]], [a[0] - h[0], a[1] - h[1]]],
    centerline: [a, b], thickness: t, assumed: w1.assumed, thinRing: w1.thinRing && w2.thinRing, layer: w1.layer, merged: w1.merged + w2.merged + 1,
  };
}
