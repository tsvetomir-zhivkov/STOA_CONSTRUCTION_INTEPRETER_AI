/**
 * Stair flights from tread lines (deterministic geometry).
 *
 * A flight = four or more roughly parallel segments (< 5 deg) of similar length (within 20 %), overlapping along their
 * own axis, whose perpendicular spacing is even (each step within 20 % of the run's median) and plausible for a tread
 * (0.15..0.45 m). Works in drawing units; thresholds in metres are converted with mPerUnit.
 *
 * Properties (schema: objects[].properties), with angles in the PLAN frame of polygon_m / polygon_px
 * (x right, y DOWN; 0 deg = +x, 90 deg = +y):
 *   tread_count       number of tread lines in the run
 *   run_direction_deg direction from the first to the last tread line
 *   run_length_m      tread_count x measured median spacing
 *   width_m           median tread line length
 *   rise_direction    "unknown" (a plan does not say which way is up unless an arrow is drawn; arrows are not read)
 */

const DEG = Math.PI / 180;

/**
 * @param {Array<{a:number[], b:number[]}>} segs drawing units
 * @param {{ mPerUnit:number }} o
 * @returns {Array<{ polygon:number[][], props:{ tread_count:number, run_direction_deg:number, run_length_m:number, width_m:number, rise_direction:'unknown' } }>}
 */
export function detectFlights(segs, { mPerUnit }) {
  const U = 1 / mPerUnit;
  const lines = segs.map(prep).filter((s) => s.len >= 0.4 * U && s.len <= 4 * U);
  const parent = lines.map((_, i) => i);
  const find = (i) => (parent[i] === i ? i : (parent[i] = find(parent[i])));
  const cos5 = Math.cos(5 * DEG);
  for (let i = 0; i < lines.length; i++) {
    for (let j = i + 1; j < lines.length; j++) {
      const a = lines[i], b = lines[j];
      if (Math.abs(a.u[0] * b.u[0] + a.u[1] * b.u[1]) < cos5) continue;
      if (Math.abs(a.len - b.len) > 0.2 * Math.max(a.len, b.len)) continue;
      if (Math.abs(sdist(a, mid(b))) > 0.6 * U) continue;
      const p0 = proj(a, b.a), p1 = proj(a, b.b);
      const ov = Math.min(a.len, Math.max(p0, p1)) - Math.max(0, Math.min(p0, p1));
      if (ov < 0.5 * Math.min(a.len, b.len)) continue;
      parent[find(i)] = find(j);
    }
  }
  const groups = new Map();
  lines.forEach((s, i) => (groups.get(find(i)) ?? groups.set(find(i), []).get(find(i))).push(s));

  const flights = [];
  for (const g of groups.values()) {
    if (g.length < 4) continue;
    const base = g[0];
    // one line per offset (drop duplicates closer than 2 cm), ordered across the flight
    const byOff = g.map((s) => ({ s, o: sdist(base, mid(s)) })).sort((x, y) => x.o - y.o)
      .filter((x, k, arr) => k === 0 || x.o - arr[k - 1].o > 0.02 * U);
    const run = longestEvenRun(byOff, U);
    if (!run || run.length < 4) continue;
    const sp = median(run.slice(1).map((x, k) => x.o - run[k].o));
    const width = median(run.map((x) => x.s.len));
    const ts = run.flatMap((x) => [proj(base, x.s.a), proj(base, x.s.b)]);
    const t0 = Math.min(...ts), t1 = Math.max(...ts);
    const o0 = run[0].o, o1 = run[run.length - 1].o + sp;
    const n = [-base.u[1], base.u[0]]; // offset grows along n
    const P = (t, o) => [base.a[0] + base.u[0] * t + n[0] * o, base.a[1] + base.u[1] * t + n[1] * o];
    // drawing y is up, plan y is down: flip y for the plan-frame angle
    const deg = ((Math.atan2(-n[1], n[0]) / DEG) % 360 + 360) % 360;
    flights.push({
      polygon: [P(t0, o0), P(t1, o0), P(t1, o1), P(t0, o1)],
      props: { tread_count: run.length, run_direction_deg: Math.round(deg * 10) / 10, run_length_m: r3(run.length * sp * mPerUnit), width_m: r3(width * mPerUnit), rise_direction: 'unknown' },
      // drawing units: walking direction, centre of the first and of the last edge, across axis, width, going (tread depth)
      geom: { n: run.length, dir: n, start: P((t0 + t1) / 2, o0), end: P((t0 + t1) / 2, o1), across: base.u, width: t1 - t0, going: sp },
    });
  }
  return flights;
}

/**
 * Group flights into staircases (flights whose footprints come within `near` of each other) and order each staircase:
 *   - flights are chained so each one starts where the previous one ended (orientation chosen per flight)
 *   - between consecutive flights a LANDING is inferred: a rectangle from the previous flight's last edge to the next
 *     flight's first edge; for a U stair (flights 180 deg apart, side by side) it is as deep as the stair is wide
 *   - for a U stair both ends of the pair are equally plausible; the end whose landing lies inside the stair room wins
 *     (`side: 'room'`), otherwise the first is taken and the landing is marked `side: 'assumed'`
 * Drawing units in and out. Which way is UP is never known from a plan (rise_direction stays "unknown").
 * @param {ReturnType<typeof detectFlights>} flights
 * @param {{ mPerUnit:number, room?: number[][]|null }} o
 * @returns {Array<{ polygon:number[][], flights:any[], landings:Array<{ polygon:number[][], afterTread:number, side:string }>, treads:number }>}
 */
export function assembleStaircases(flights, { mPerUnit, room = null }) {
  const U = 1 / mPerUnit;
  const near = 1.5 * U;
  const parent = flights.map((_, i) => i);
  const find = (i) => (parent[i] === i ? i : (parent[i] = find(parent[i])));
  for (let i = 0; i < flights.length; i++) for (let j = i + 1; j < flights.length; j++) if (polyDist(flights[i].polygon, flights[j].polygon) <= near) parent[find(i)] = find(j);
  const groups = new Map();
  flights.forEach((f, i) => (groups.get(find(i)) ?? groups.set(find(i), []).get(find(i))).push(f));

  const out = [];
  for (const g of groups.values()) {
    const fl = g.map((f) => ({ ...f.geom }));
    const order = chain(fl, room);
    const landings = [];
    let treads = 0;
    for (let k = 0; k < order.length; k++) {
      treads += order[k].n;
      if (k + 1 < order.length) landings.push({ ...landingBetween(order[k], order[k + 1]), afterTread: treads, side: order[k + 1].side ?? 'measured' });
    }
    const pts = [...g.flatMap((f) => f.polygon), ...landings.flatMap((l) => l.polygon)];
    out.push({ polygon: hull(pts), flights: order, landings, treads });
  }
  return out;
}

const flip = (f) => ({ ...f, dir: [-f.dir[0], -f.dir[1]], start: f.end, end: f.start });
const dist = (p, q) => Math.hypot(p[0] - q[0], p[1] - q[1]);

/** order and orient flights so each starts nearest to where the previous ended */
function chain(fl, room) {
  if (fl.length === 1) return fl;
  // first flight: try both orientations, keep the one whose best continuation is closest; ties -> room test
  const rest = fl.slice(1);
  const options = [fl[0], flip(fl[0])].map((first) => {
    const seq = [first];
    const left = [...rest];
    while (left.length) {
      const last = seq[seq.length - 1];
      let best = null;
      left.forEach((f, i) => { for (const c of [f, flip(f)]) { const d = dist(last.end, c.start); if (!best || d < best.d - 1e-9) best = { d, i, c }; } });
      seq.push(best.c);
      left.splice(best.i, 1);
    }
    const cost = seq.slice(1).reduce((s, f, k) => s + dist(seq[k].end, f.start), 0);
    const inRoom = room ? seq.slice(1).every((f, k) => landingBetween(seq[k], f).polygon.every((p) => inside(p, room, landingBetween(seq[k], f).polygon))) : null;
    return { seq, cost, inRoom };
  });
  const [a, b] = options;
  const tie = Math.abs(a.cost - b.cost) <= 0.05 * Math.max(a.seq[0].width, 1e-9);
  let pick = a.cost <= b.cost ? a : b, side = 'measured';
  if (tie) {
    if (a.inRoom !== b.inRoom && a.inRoom != null) { pick = a.inRoom ? a : b; side = 'room'; }
    else side = 'assumed';
  }
  return pick.seq.map((f, k) => (k ? { ...f, side } : f));
}

/** landing rectangle in the frame of flight A: from A's last edge to B's first edge (U stair: as deep as the stair is wide) */
function landingBetween(A, B) {
  const u = A.dir, v = A.across;
  const h = (f, p) => [[p[0] + f.across[0] * f.width / 2, p[1] + f.across[1] * f.width / 2], [p[0] - f.across[0] * f.width / 2, p[1] - f.across[1] * f.width / 2]];
  const pts = [...h(A, A.end), ...h(B, B.start)];
  const al = pts.map((p) => (p[0] - A.end[0]) * u[0] + (p[1] - A.end[1]) * u[1]);
  const ac = pts.map((p) => (p[0] - A.end[0]) * v[0] + (p[1] - A.end[1]) * v[1]);
  const antiparallel = u[0] * B.dir[0] + u[1] * B.dir[1] < -0.9;
  const depth = Math.max(Math.max(0, ...al), antiparallel ? Math.max(A.width, B.width) : 0);
  const b0 = Math.min(...ac), b1 = Math.max(...ac);
  const Pt = (a, b) => [A.end[0] + u[0] * a + v[0] * b, A.end[1] + u[1] * a + v[1] * b];
  return { polygon: [Pt(0, b0), Pt(depth, b0), Pt(depth, b1), Pt(0, b1)], depth };
}

/** point inside the room, tested slightly towards the landing centre (its corners may touch the room outline) */
function inside(p, room, poly) {
  const c = [poly.reduce((s, q) => s + q[0], 0) / poly.length, poly.reduce((s, q) => s + q[1], 0) / poly.length];
  const q = [p[0] + (c[0] - p[0]) * 0.1, p[1] + (c[1] - p[1]) * 0.1];
  let inn = false;
  for (let i = 0, j = room.length - 1; i < room.length; j = i++) {
    const [xi, yi] = room[i], [xj, yj] = room[j];
    if ((yi > q[1]) !== (yj > q[1]) && q[0] < ((xj - xi) * (q[1] - yi)) / (yj - yi) + xi) inn = !inn;
  }
  return inn;
}

/** smallest vertex-to-vertex / vertex-to-edge distance between two polygons (0 when they touch or overlap at a vertex) */
function polyDist(A, B) {
  const segD = (p, a, b) => { const dx = b[0] - a[0], dy = b[1] - a[1], L = dx * dx + dy * dy; const t = L ? Math.max(0, Math.min(1, ((p[0] - a[0]) * dx + (p[1] - a[1]) * dy) / L)) : 0; return Math.hypot(p[0] - a[0] - t * dx, p[1] - a[1] - t * dy); };
  let d = Infinity;
  for (const [P1, P2] of [[A, B], [B, A]]) for (const p of P1) for (let i = 0; i < P2.length; i++) d = Math.min(d, segD(p, P2[i], P2[(i + 1) % P2.length]));
  return d;
}

/** convex hull (monotone chain), counter-clockwise */
function hull(points) {
  const p = [...points].sort((a, b) => a[0] - b[0] || a[1] - b[1]);
  if (p.length < 3) return p;
  const cr = (o, a, b) => (a[0] - o[0]) * (b[1] - o[1]) - (a[1] - o[1]) * (b[0] - o[0]);
  const lo = [], up = [];
  for (const q of p) { while (lo.length >= 2 && cr(lo[lo.length - 2], lo[lo.length - 1], q) <= 0) lo.pop(); lo.push(q); }
  for (const q of p.reverse()) { while (up.length >= 2 && cr(up[up.length - 2], up[up.length - 1], q) <= 0) up.pop(); up.push(q); }
  return [...lo.slice(0, -1), ...up.slice(0, -1)];
}

/** longest run whose spacings are each within 20 % of the run's median and between 0.15 and 0.45 m */
function longestEvenRun(items, U) {
  let best = null;
  for (let s = 0; s < items.length; s++) {
    for (let e = s + 3; e < items.length; e++) {
      const run = items.slice(s, e + 1);
      const gaps = run.slice(1).map((x, k) => x.o - run[k].o);
      const m = median(gaps);
      if (m < 0.15 * U || m > 0.45 * U || gaps.some((g) => Math.abs(g - m) > 0.2 * m)) continue;
      if (!best || run.length > best.length) best = run;
    }
  }
  return best;
}

function prep(s) {
  const dx = s.b[0] - s.a[0], dy = s.b[1] - s.a[1], len = Math.hypot(dx, dy);
  return { ...s, len, u: len ? [dx / len, dy / len] : [1, 0] };
}
const mid = (s) => [(s.a[0] + s.b[0]) / 2, (s.a[1] + s.b[1]) / 2];
const proj = (s, p) => (p[0] - s.a[0]) * s.u[0] + (p[1] - s.a[1]) * s.u[1];
const sdist = (s, p) => (p[0] - s.a[0]) * -s.u[1] + (p[1] - s.a[1]) * s.u[0];
const median = (a) => { const s = [...a].sort((x, y) => x - y); const m = s.length >> 1; return s.length % 2 ? s[m] : (s[m - 1] + s[m]) / 2; };
const r3 = (v) => Math.round(v * 1000) / 1000;

/** Stair words for room names and layer names (case-insensitive). */
export const STAIR_LAYER_RE = /stair|strs|escalier|treppe|portaat|trappa|escalera|scala|trap/i;
