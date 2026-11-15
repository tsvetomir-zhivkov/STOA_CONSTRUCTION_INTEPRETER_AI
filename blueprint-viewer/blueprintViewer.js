import { recordCategory, setRangeVisibility, identityOfRecord } from './visibility.js';
/**
 * blueprintViewer.js — owns the three.js scene for one blueprint-ingest drawing JSON.
 *
 *   const viewer = new BlueprintViewer(containerElement, { onSelect(info|null) {}, onCorrectionsChange(list) {} });
 *   const stats  = viewer.load(parsedJson);   // throws if the file has no drawable geometry
 *   viewer.resize();                          // after the container becomes visible
 *
 * Plan geometry (every polygon) comes from polygon_m in the file. Heights are NOT in these drawings,
 * so they come from the fixed constants in HEIGHTS (a wall with a numeric height_m uses that instead).
 * Stair steps are generated (from the stair's tread properties, or from its footprint) and flagged as such.
 */

import * as THREE from 'three';
import { renderProjection } from './buildingModel.js';
import { FileSession } from './session.js';
import { OrbitControls } from 'three/examples/jsm/controls/OrbitControls.js';
import { MeshBVH, computeBoundsTree, disposeBoundsTree, acceleratedRaycast } from 'three-mesh-bvh';
import { loadIfcModel, ifcMeshes, explodeIfcMesh, elementGeometry, captureWebIfcLog } from './ifcGeometry.js';
export { captureWebIfcLog };
import { checkPsetUnits, measureFromBox, measureFromFootprint } from './psetUnits.js';
import { normalizeVocabulary } from './vendor/vocabulary.js';

// BVH-accelerated raycasting (a clinic model is ~3 million triangles); meshes without a bounds tree raycast as usual
THREE.BufferGeometry.prototype.computeBoundsTree = computeBoundsTree;
THREE.BufferGeometry.prototype.disposeBoundsTree = disposeBoundsTree;
THREE.Mesh.prototype.raycast = acceleratedRaycast;

/** Fixed height assumptions in metres. Not editable at runtime. */
export const HEIGHTS = Object.freeze({
  wall: 2.7,
  doorHead: 2.1,
  windowSill: 0.9,
  fixture: 0.78,
  floorSlab: 0.08, // drawing convention, not a measured slab
  floorGap: 0.02, // floor top sits this far below the wall base ...
  floorInset: 0.01, // ... and is inset this much: room outlines and wall faces come from different layers
  doorThreshold: 0.02, // thin marker on the floor of a doorway
  doorLeaf: 0.04, // door leaf thickness
  doorSwingDeg: 35, // drawing convention: leaves are drawn open at this angle (not data)
  stairRiserFallback: 0.17, // only when a stair has no tread data
});

const WINDOW_GLASS = HEIGHTS.doorHead - HEIGHTS.windowSill; // 0.9 -> 2.1
const FLOOR_BASE = -HEIGHTS.floorGap - HEIGHTS.floorSlab; // floor slab spans FLOOR_BASE .. -floorGap
const LINTEL = HEIGHTS.wall - HEIGHTS.doorHead; // 2.1 -> 2.7

/** The groups every built object lives in (exactly one each). They persist across loads. */
export const GROUP_NAMES = Object.freeze(['floors', 'walls', 'doors', 'windows', 'fixtures', 'stairs', 'structure', 'services', 'roomNames', 'areas']);

/**
 * Fixture height (m) and colour per category. CONVENTIONS for geometry extruded from 2D (marked assumed in the inspector);
 * web-ifc geometry uses its real shape. A shower is a 50 mm tray plus a 2.0 m glass box.
 */
export const FIXTURE_STYLE = Object.freeze({
  toilet: { h: 0.78, color: '#f5f5f4' }, sink: { h: 0.85, color: '#dbeafe' }, bathtub: { h: 0.55, color: '#e0f2fe' }, shower: { h: 2.0, color: '#bae6fd' },
  stove: { h: 0.9, color: '#525252' }, fridge: { h: 1.8, color: '#e5e7eb' }, washing_machine: { h: 0.85, color: '#f1f5f9' }, closet: { h: 2.0, color: '#c8a27a' },
  radiator: { h: 0.6, color: '#fca5a5' }, elevator: { h: 2.2, color: '#a1a1aa' }, sauna_bench: { h: 0.45, color: '#d6a46c' }, other: { h: 0.78, color: '#ffffff' },
});

/** Pull-apart lift per piece kind (multiplied by the slider value and 1.6). */
const LIFT = Object.freeze({ floor: 0, fixture: 0.45, stair: 0.45, wall: 1, threshold: 1.5, doorLeaf: 1.5, windowGlass: 1.9, windowSill: 1.5, windowLintel: 2.2, doorLintel: 2.2 });
/** pull-apart lift per viewer group, for web-ifc geometry */
export const GROUP_LIFT = Object.freeze({ floors: 0, structure: 0.2, walls: 1, doors: 1.5, windows: 1.9, fixtures: 0.45, stairs: 0.45, services: 0.8 });
const EXPLODE_SPREAD = 1.35; // outward, proportional to distance from the centre
const EXPLODE_PUSH = 1.1; // constant push along the unit direction

/** Label heights above the floor top (y = 0). Labels never move vertically. */
const LABEL_Y = Object.freeze({ name: 0.14, area: 0.10 });

/** Schema v0.2 enums offered by the "Correct" control. */
export const ROOM_TYPES = Object.freeze(['bedroom', 'living', 'kitchen', 'dining', 'bathroom', 'wc', 'sauna', 'entry', 'corridor', 'closet', 'storage', 'utility', 'technical', 'stair', 'balcony', 'terrace', 'garage', 'office', 'outdoor', 'shaft', 'other', 'unknown']);
export const OPENING_KINDS = Object.freeze(['door', 'window']);
export const OBJECT_CATEGORIES = Object.freeze(['toilet', 'sink', 'shower', 'bathtub', 'sauna_bench', 'stair', 'stove', 'fridge', 'washing_machine', 'elevator', 'radiator', 'closet', 'other']);
const OUTDOOR_TYPES = new Set(['balcony', 'terrace', 'outdoor']);

/** flags -> plain words for the inspector (unknown flags are shown as they are) */
export const FLAG_TEXT = Object.freeze({
  assumed_thickness: 'thickness assumed, not measured in the drawing',
  assumed_height: 'height assumed',
  inferred_from_room: 'type inferred from the room it sits in',
  inferred_from_contents: 'room type inferred from what it contains (a stair)',
  area_mismatch: 'drawn area differs from the written area by more than 10 %',
  check_this: 'flagged for a human check',
  generated_geometry: 'step geometry generated from the footprint, not drawn in the file',
  landing_side_assumed: 'which end of the stair the landing is on is assumed (both ends fit)',
  inferred_from_room_name: 'type inferred from the room name',
});

/** Floor colour per room type (light palette). v0.2 type names share the colour of their older equivalent. */
const ROOM_COLORS = {
  bedroom: '#a8c5e6', living_room: '#f2d39b', kitchen: '#f0a88c', dining: '#e9c08a', bathroom: '#8fd3d1',
  toilet: '#a6dcc8', sauna: '#d8a878', utility: '#c6c1e6', hall: '#e3dccb', corridor: '#d9d4c7',
  closet: '#cbb9a8', storage: '#b9b2a6', balcony: '#b5d99c', terrace: '#9fcf8f', office: '#c9b3df',
  stair: '#b0b6c2', technical: '#a9adb3', garage: '#9ea4ab', other: '#d0ccc4', unknown: '#c8c8c8',
  // schema v0.2 names
  living: '#f2d39b', wc: '#a6dcc8', entry: '#e3dccb', outdoor: '#b9dfa9', shaft: '#9a9ea6',
};

const PALETTES = {
  light: { background: '#eef0f3', ground: '#e2e5ea', grid: '#c4c9d1', gridCenter: '#a9b0ba', wall: '#f4f2ee', frame: '#d6d2ca', threshold: '#8c8272', doorLeaf: '#b06a2c', glass: '#7fb8e6', fixture: '#ffffff', stair: '#cfc6b8', outline: '#d97706', hover: '#3b82f6', select: '#f59e0b', floorFactor: 1 },
  dark: { background: '#15181d', ground: '#1c2027', grid: '#2c323c', gridCenter: '#3a424e', wall: '#d9d6d0', frame: '#a9a49b', threshold: '#6f6658', doorLeaf: '#d08a4c', glass: '#6aa9dd', fixture: '#e8e8e8', stair: '#a39a8c', outline: '#fbbf24', hover: '#60a5fa', select: '#fbbf24', floorFactor: 0.72 },
};

const darkQuery = typeof window !== 'undefined' ? window.matchMedia('(prefers-color-scheme: dark)') : null;
const reducedMotionQuery = typeof window !== 'undefined' ? window.matchMedia('(prefers-reduced-motion: reduce)') : null;
const currentPalette = () => ((typeof document!=='undefined' && document.body.dataset.theme==='dark') || darkQuery?.matches ? PALETTES.dark : PALETTES.light);

// =====================================================================================================
// geometry (no renderer needed: usable from Node for checks)
// =====================================================================================================

const isActive = (el) => el && el.status === 'active';

/** polygon_m ring -> clean [[x, y], ...] (2D, finite, closing duplicate removed) or null */
export function cleanRing(ring) {
  if (!Array.isArray(ring)) return null;
  const pts = ring.filter((p) => Array.isArray(p) && Number.isFinite(p[0]) && Number.isFinite(p[1])).map((p) => [p[0], p[1]]);
  if (pts.length > 1) {
    const a = pts[0], b = pts[pts.length - 1];
    if (a[0] === b[0] && a[1] === b[1]) pts.pop();
  }
  return pts.length >= 3 ? pts : null;
}

/** Signed plan area (shoelace) */
export function ringArea(ring) {
  let a = 0;
  for (let i = 0; i < ring.length; i++) { const [x0, y0] = ring[i], [x1, y1] = ring[(i + 1) % ring.length]; a += x0 * y1 - x1 * y0; }
  return a / 2;
}

/**
 * Move every edge of a ring inward by d (outward for d < 0), vertices on the mitre; a mitre longer than 4 d is clamped.
 * For the 10 mm floor inset only: the original ring is returned when the result would flip or collapse.
 */
export function insetRing(ring, d) {
  const sgn = ringArea(ring) > 0 ? 1 : -1; // left normal points inside a counter-clockwise ring
  const n = ring.length, out = [];
  for (let i = 0; i < n; i++) {
    const p = ring[(i - 1 + n) % n], c = ring[i], q = ring[(i + 1) % n];
    const e1 = norm2([c[0] - p[0], c[1] - p[1]]), e2 = norm2([q[0] - c[0], q[1] - c[1]]);
    if (!e1 || !e2) { out.push(c); continue; }
    const n1 = [-e1[1] * sgn, e1[0] * sgn], n2 = [-e2[1] * sgn, e2[0] * sgn];
    const b = norm2([n1[0] + n2[0], n1[1] + n2[1]]);
    if (!b) { out.push([c[0] + n1[0] * d, c[1] + n1[1] * d]); continue; }
    const k = Math.min(4, 1 / Math.max(1e-6, b[0] * n1[0] + b[1] * n1[1]));
    out.push([c[0] + b[0] * d * k, c[1] + b[1] * d * k]);
  }
  const a0 = ringArea(ring), a1 = ringArea(out);
  return Math.sign(a1) === Math.sign(a0) && Math.abs(a1) < Math.abs(a0) + 1e-12 === d > 0 && Math.abs(a1) > 1e-6 ? out : ring;
}
const norm2 = (v) => { const l = Math.hypot(v[0], v[1]); return l > 1e-12 ? [v[0] / l, v[1] / l] : null; };

/** point in polygon (even-odd) */
function inRing(p, r) {
  let inside = false;
  for (let i = 0, j = r.length - 1; i < r.length; j = i++) {
    const [xi, yi] = r[i], [xj, yj] = r[j];
    if ((yi > p[1]) !== (yj > p[1]) && p[0] < ((xj - xi) * (p[1] - yi)) / (yj - yi) + xi) inside = !inside;
  }
  return inside;
}

/** Plan (x right, y down) -> shape (x - cx, -(y - cz)). */
const toShapePoints = (ring, c) => ring.map(([x, y]) => new THREE.Vector2(x - c.x, -(y - c.z)));

/** Unit-height prism standing on y = 0, world X = plan x, world Z = plan y. */
export function prismGeometry(ring, holes, centre) {
  const shape = new THREE.Shape(toShapePoints(ring, centre));
  for (const h of holes ?? []) {
    const hr = cleanRing(h);
    if (hr) shape.holes.push(new THREE.Path(toShapePoints(hr, centre)));
  }
  const g = new THREE.ExtrudeGeometry(shape, { depth: 1, bevelEnabled: false });
  g.rotateX(-Math.PI / 2);
  g.computeVertexNormals();
  return g;
}

const asArray = (v) => (Array.isArray(v) ? v : []);

/**
 * What is actually in the file, before any geometry is built. Pure: usable from Node.
 * Counts are of ACTIVE elements (superseded ones are counted separately); "drawable" = has >= 3 points in polygon_m.
 */
export function fileReport(doc) {
  const act = (k) => asArray(doc?.[k]).filter(isActive);
  const poly = (list) => {
    let present = 0, nul = 0, invalid = 0;
    for (const e of list) { if (e.polygon_m == null) nul++; else if (cleanRing(e.polygon_m)) present++; else invalid++; }
    return { present, null: nul, invalid };
  };
  const openings = act('openings'), objects = act('objects'), walls = act('walls');
  const byKind = (list, f) => list.reduce((m, e) => { const k = e[f] ?? 'none'; m[k] = (m[k] ?? 0) + 1; return m; }, {});
  const assumed = walls.filter((w) => asArray(w.flags).includes('assumed_thickness')).length;
  const measured = walls.filter((w) => !asArray(w.flags).includes('assumed_thickness') && Number.isFinite(w.thickness_m)).length;
  const d = doc?.drawing ?? {};
  // elements of the same kind drawn with (nearly) the same footprint: cannot be told apart by clicking, likely duplicates
  const sameSpot = [];
  for (const [k, list] of [['door', openings.filter((o) => o.kind !== 'window')], ['window', openings.filter((o) => o.kind === 'window')], ['wall', walls]]) {
    const boxes = list.map((e) => { const r = cleanRing(e.polygon_m); return r && { e, c: ringCentroid(r), b: r.reduce((b, [x, y]) => [Math.min(b[0], x), Math.min(b[1], y), Math.max(b[2], x), Math.max(b[3], y)], [Infinity, Infinity, -Infinity, -Infinity]) }; }).filter(Boolean);
    for (let i = 0; i < boxes.length; i++) for (let j = i + 1; j < boxes.length; j++) {
      const a = boxes[i].b, b = boxes[j].b;
      // same box AND same centroid (two triangles that together fill one square share a box, not a footprint)
      if ([0, 1, 2, 3].every((q) => Math.abs(a[q] - b[q]) <= 0.03) && Math.hypot(boxes[i].c[0] - boxes[j].c[0], boxes[i].c[1] - boxes[j].c[1]) <= 0.02) sameSpot.push({ kind: k, a: boxes[i].e.id, b: boxes[j].e.id });
    }
  }
  return {
    file: d.file ?? null, route: d.route ?? null, readFrom: d.read_from ?? null, part: d.part ?? null,
    counts: {
      rooms: act('rooms').length, walls: walls.length,
      openings: openings.length, doors: openings.filter((o) => o.kind === 'door').length, doorways: openings.filter((o) => o.kind === 'doorway').length,
      windows: openings.filter((o) => o.kind === 'window').length,
      objects: objects.length, stairs: objects.filter((o) => o.category === 'stair').length, texts: asArray(doc?.texts).length,
      superseded: ['rooms', 'walls', 'openings', 'objects'].reduce((s, k) => s + asArray(doc?.[k]).filter((e) => e.status === 'superseded').length, 0),
    },
    objectCategories: byKind(objects, 'category'),
    walls: { assumedThickness: assumed, measuredThickness: measured, noThickness: walls.length - assumed - measured },
    polygonM: Object.fromEntries(['rooms', 'walls', 'openings', 'objects'].map((k) => [k, poly(act(k))])),
    coverage: asArray(doc?.coverage).map((c) => ({ category: c.category, status: c.status, confidence: c.confidence ?? null, note: c.note ?? null })),
    sameSpot,
  };
}

/** Startup report in the console: what the file contains, before anything is drawn */
export function logReport(r) {
  if (typeof console === 'undefined') return;
  const n = r.counts;
  console.groupCollapsed?.(`[blueprint-viewer] ${r.file ?? 'drawing'} (${r.route ?? 'unknown route'})`);
  console.log(`rooms ${n.rooms} · walls ${n.walls} · openings ${n.openings} (doors ${n.doors}, doorways ${n.doorways}, windows ${n.windows}) · objects ${n.objects} (stairs ${n.stairs}) · texts ${n.texts}`);
  console.log(`walls: ${r.walls.measuredThickness} measured thickness, ${r.walls.assumedThickness} assumed_thickness, ${r.walls.noThickness} without thickness`);
  console.log('polygon_m present / null / unusable:', Object.fromEntries(Object.entries(r.polygonM).map(([k, v]) => [k, `${v.present} / ${v.null} / ${v.invalid}`])));
  console.table?.(r.coverage);
  if (r.sameSpot.length) console.warn(`${r.sameSpot.length} pair(s) of elements share the same footprint (possible duplicates):`, r.sameSpot.map((p) => `${p.a} = ${p.b}`).join(', '));
  console.groupEnd?.();
}

/** Coverage category for each viewer collection (coverage entries use these names) */
const COVERAGE_OF = Object.freeze({ rooms: 'rooms', walls: 'walls', doors: 'doors', windows: 'windows', fixtures: 'fixtures', stairs: 'stairs', text: 'text' });

/**
 * "What is in this file" lines, driven by coverage. Each line: { category, count, status, state, text }
 *   state: 'present' | 'none' (analysed, zero found) | 'not_extracted' (not_analysed) | 'partial' | 'unknown' (no coverage entry)
 */
export function contentsSummary(rep) {
  const n = rep.counts;
  const count = { rooms: n.rooms, walls: n.walls, doors: n.doors + n.doorways, windows: n.windows, fixtures: n.objects - n.stairs, stairs: n.stairs, text: n.texts };
  const cov = new Map(rep.coverage.map((c) => [c.category, c]));
  return Object.entries(COVERAGE_OF).map(([key, cat]) => {
    const c = cov.get(cat), k = count[key];
    const status = c?.status ?? null;
    let state, text;
    if (status === 'not_analysed') { state = 'not_extracted'; text = k ? `${k} (not extracted yet: incomplete)` : 'not extracted yet'; }
    else if (k > 0) { state = status === 'partial' ? 'partial' : 'present'; text = status === 'partial' ? `${k} (partial coverage)` : String(k); }
    else if (status === 'analysed') { state = 'none'; text = 'none in this drawing'; }
    else if (status === 'partial') { state = 'partial'; text = '0 found (partial coverage: there may be some)'; }
    else { state = 'unknown'; text = '0 (no coverage entry: unknown)'; }
    return { category: key, count: k, status, confidence: c?.confidence ?? null, note: c?.note ?? null, state, text };
  });
}

/** Centre of the bounding box of every usable polygon_m point of rooms, walls, openings and objects. */
export function planCentre(doc) {
  let minX = Infinity, minY = Infinity, maxX = -Infinity, maxY = -Infinity, n = 0;
  for (const key of ['rooms', 'walls', 'openings', 'objects']) {
    for (const el of asArray(doc[key])) {
      if (!isActive(el)) continue;
      const ring = cleanRing(el.polygon_m);
      if (!ring) continue;
      for (const [x, y] of ring) { minX = Math.min(minX, x); maxX = Math.max(maxX, x); minY = Math.min(minY, y); maxY = Math.max(maxY, y); n++; }
    }
  }
  return n ? { x: (minX + maxX) / 2, z: (minY + maxY) / 2, minX, minY, maxX, maxY } : null;
}

/** Area-weighted centroid of a ring (vertex mean for degenerate rings). */
function ringCentroid(ring) {
  let a = 0, x = 0, y = 0;
  for (let i = 0; i < ring.length; i++) {
    const [x0, y0] = ring[i], [x1, y1] = ring[(i + 1) % ring.length];
    const f = x0 * y1 - x1 * y0;
    a += f; x += (x0 + x1) * f; y += (y0 + y1) * f;
  }
  if (Math.abs(a) < 1e-12) return [ring.reduce((s, p) => s + p[0], 0) / ring.length, ring.reduce((s, p) => s + p[1], 0) / ring.length];
  return [x / (3 * a), y / (3 * a)];
}

/** Stable angle in [0, 2pi) from an id string (FNV-1a). */
function hashAngle(id) {
  let h = 0x811c9dc5;
  for (const ch of String(id ?? '')) { h ^= ch.codePointAt(0); h = Math.imul(h, 0x01000193) >>> 0; }
  return (h / 0x100000000) * Math.PI * 2;
}

/** cx, cz in scene coordinates (centroid minus the model centre) and a unit direction; zero-length -> hashed angle. */
function explodeFrame(ring, centre, id) {
  const [px, py] = ringCentroid(ring);
  const cx = px - centre.x, cz = py - centre.z;
  const len = Math.hypot(cx, cz);
  if (len < 0.01) { const t = hashAngle(id); return { cx, cz, dir: { x: Math.cos(t), z: Math.sin(t) } }; }
  return { cx, cz, dir: { x: cx / len, z: cz / len } };
}

/** Long axis of a ring (minimum-area rectangle over edge directions): { dir, length, width } in plan metres. */
export function longAxis(ring) {
  let best = null;
  for (let i = 0; i < ring.length; i++) {
    const [x0, y0] = ring[i], [x1, y1] = ring[(i + 1) % ring.length];
    const l = Math.hypot(x1 - x0, y1 - y0);
    if (l < 1e-9) continue;
    const u = [(x1 - x0) / l, (y1 - y0) / l], v = [-u[1], u[0]];
    let a0 = Infinity, a1 = -Infinity, b0 = Infinity, b1 = -Infinity;
    for (const [x, y] of ring) { const a = x * u[0] + y * u[1], b = x * v[0] + y * v[1]; a0 = Math.min(a0, a); a1 = Math.max(a1, a); b0 = Math.min(b0, b); b1 = Math.max(b1, b); }
    const w = a1 - a0, h = b1 - b0;
    if (!best || w * h < best.area) best = w >= h ? { area: w * h, dir: u, length: w, width: h } : { area: w * h, dir: v, length: h, width: w };
  }
  return best;
}

/**
 * Steps for a stair object, in plan metres. Riser = floor height / total treads (floorHeight is an assumption unless the
 * level gives one); tread i of the whole stair tops out at (i + 1) * riser. The step geometry is always GENERATED.
 *   properties.flights[] (blueprint-ingest >= 0.2.2): each flight advances going_m per tread along direction_deg from
 *     start_m (the centre of its first edge), width_m across; landings[] are slabs at after_tread * riser
 *   older single-run properties (tread_count, run_direction_deg, run_length_m, width_m): one flight centred on the footprint
 *   nothing usable: the footprint's long axis, 0.17 m risers
 * Angles are in the plan frame of polygon_m: 0 = +x, 90 = +y (down the page).
 */
export function stairSteps(obj, ring, floorHeight = HEIGHTS.wall) {
  const p = obj.properties ?? {};
  const fin = (...v) => v.every(Number.isFinite);
  const pt = (v) => Array.isArray(v) && fin(v[0], v[1]);
  let flights = [], landings = [], from;
  if (Array.isArray(p.flights) && p.flights.length && p.flights.every((f) => fin(f.tread_count, f.direction_deg, f.width_m, f.going_m) && pt(f.start_m) && f.tread_count >= 1 && f.width_m > 0 && f.going_m > 0)) {
    flights = p.flights.map((f) => ({ n: Math.round(f.tread_count), deg: f.direction_deg, start: f.start_m, width: f.width_m, going: f.going_m }));
    landings = (Array.isArray(p.landings) ? p.landings : []).filter((l) => cleanRing(l.polygon_m) && Number.isFinite(l.after_tread)).map((l) => ({ ring: cleanRing(l.polygon_m), after: l.after_tread }));
    from = 'flights';
  } else if (fin(p.tread_count, p.run_direction_deg, p.run_length_m, p.width_m) && p.tread_count >= 1 && p.run_length_m > 0 && p.width_m > 0) {
    const t = (p.run_direction_deg * Math.PI) / 180, [cx, cy] = ringCentroid(ring);
    flights = [{ n: Math.round(p.tread_count), deg: p.run_direction_deg, start: [cx - (Math.cos(t) * p.run_length_m) / 2, cy - (Math.sin(t) * p.run_length_m) / 2], width: p.width_m, going: p.run_length_m / p.tread_count }];
    from = 'properties';
  } else {
    const ax = longAxis(ring);
    if (!ax || ax.length < 1e-6 || ax.width < 1e-6) return { steps: [], landings: [], n: 0, riser: 0, depth: 0, from: 'none' }; // degenerate footprint
    const n = Math.max(1, Math.floor(floorHeight / HEIGHTS.stairRiserFallback)), [cx, cy] = ringCentroid(ring);
    flights = [{ n, deg: (Math.atan2(ax.dir[1], ax.dir[0]) * 180) / Math.PI, start: [cx - (ax.dir[0] * ax.length) / 2, cy - (ax.dir[1] * ax.length) / 2], width: ax.width, going: ax.length / n }];
    from = 'footprint';
  }
  const total = flights.reduce((s, f) => s + f.n, 0);
  const riser = from === 'footprint' ? HEIGHTS.stairRiserFallback : floorHeight / total;
  const steps = [];
  let k = 0;
  for (const f of flights) {
    const t = (f.deg * Math.PI) / 180, dir = [Math.cos(t), Math.sin(t)], h = [(-dir[1] * f.width) / 2, (dir[0] * f.width) / 2];
    for (let i = 0; i < f.n; i++, k++) {
      const a = [f.start[0] + dir[0] * f.going * i, f.start[1] + dir[1] * f.going * i];
      const b = [a[0] + dir[0] * f.going, a[1] + dir[1] * f.going];
      steps.push({ ring: [[a[0] + h[0], a[1] + h[1]], [b[0] + h[0], b[1] + h[1]], [b[0] - h[0], b[1] - h[1]], [a[0] - h[0], a[1] - h[1]]], height: riser * (k + 1) });
    }
  }
  return { steps, landings: landings.map((l) => ({ ring: l.ring, height: riser * l.after })), n: total, flights: flights.length, riser, depth: flights[0].going, from };
}

/**
 * Door leaf footprint (plan metres): a `leaf`-thick panel hinged at one jamb, set back into the reveal and swung `deg` out
 * of the wall plane. The opening's own long axis is the wall direction; which jamb and which side are conventions.
 */
export function doorLeafRing(ring, { deg = HEIGHTS.doorSwingDeg, leaf = HEIGHTS.doorLeaf, hinge: jamb = 0, side = 1 } = {}) {
  const ax = longAxis(ring);
  if (!ax || ax.length < 0.2) return null;
  const [cx, cy] = ringCentroid(ring);
  const u = jamb ? [-ax.dir[0], -ax.dir[1]] : ax.dir, n = [-u[1] * side, u[0] * side];
  const setBack = Math.max(0, ax.width / 2 - leaf - 0.01); // the leaf's outer face sits just inside the reveal face
  const hinge = [cx - (u[0] * ax.length) / 2 + n[0] * setBack, cy - (u[1] * ax.length) / 2 + n[1] * setBack];
  const t = (deg * Math.PI) / 180;
  const d = [u[0] * Math.cos(t) + n[0] * Math.sin(t), u[1] * Math.cos(t) + n[1] * Math.sin(t)]; // swung towards +n
  const e = [-d[1] * leaf * side, d[0] * leaf * side], L = ax.length - 0.01;
  return [hinge, [hinge[0] + d[0] * L, hinge[1] + d[1] * L], [hinge[0] + d[0] * L - e[0], hinge[1] + d[1] * L - e[1]], [hinge[0] - e[0], hinge[1] - e[1]]];
}

/**
 * Build all groups from a drawing JSON. Every mesh gets its OWN material (so hover/select can tint one element) and stores
 * userData.explode = { cx, cz, dir, base, lift } once. `rebuild(element)` replaces one element's pieces (corrections).
 */
export function buildModel(doc, palette = currentPalette(), opts = {}) {
  // opts.sceneFrame: polygon_m already holds scene-plan coordinates (X, Z): geometry is not re-centred, pieces explode
  // around this document's own centre. opts.pid: shared pickId allocator (several documents in one scene).
  const bounds = planCentre(doc);
  const centre = bounds && opts.sceneFrame ? { ...bounds, x: 0, z: 0 } : bounds;
  const explodeCentre = bounds;
  const root = new THREE.Group();
  root.name = 'model';
  const groups = {};
  for (const name of GROUP_NAMES) {
    groups[name] = new THREE.Group();
    groups[name].name = name;
    root.add(groups[name]);
  }
  const geometries = new Set(), materials = new Set(), textures = new Set();
  const pickables = [], labels = [], outlines = [];
  const owned = new Map(); // element -> objects built for it (meshes, labels)
  const pids = new Map(); // element -> { part -> pickId }, stable across rebuilds
  let nextPid = 0;
  const newPid = opts.pid ?? (() => ++nextPid);
  const pidOf = (el, part = 'main') => { const m = pids.get(el) ?? pids.set(el, new Map()).get(el); if (!m.has(part)) m.set(part, newPid()); return m.get(part); };
  const stats = { rooms: 0, walls: 0, openings: 0, objects: 0, stairs: 0, floorAreaM2: 0, skipped: 0 };
  const disposeObj = (o) => {
    o.traverse?.((c) => {
      if (c.geometry && !c.isSprite) { c.geometry.dispose(); geometries.delete(c.geometry); }
      if (c.material) { c.material.map?.dispose(); textures.delete(c.material.map); c.material.dispose(); materials.delete(c.material); }
    });
  };
  const dispose = () => { geometries.forEach((g) => g.dispose()); materials.forEach((m) => m.dispose()); textures.forEach((t) => t.dispose()); };
  const model = { root, groups, pickables, labels, outlines, stats, centre, dispose, rebuild, kindOf };
  if (!centre) return model;

  const kindByElement = new Map();
  // storey height for stairs: from the level when the file has one, otherwise the wall-height assumption
  const lh = asArray(doc.levels).map((l) => l?.height_m).find((h) => Number.isFinite(h) && h > 0);
  const floorHeight = lh ? { value: lh, measured: true } : { value: HEIGHTS.wall, measured: false };
  function kindOf(el) { return kindByElement.get(el); }

  const material = (colorKey, extra = {}) => {
    const m = new THREE.MeshStandardMaterial({ color: colorFor(palette, colorKey), roughness: 0.85, metalness: 0, ...extra });
    m.userData.colorKey = colorKey;
    materials.add(m);
    return m;
  };
  const own = (el, obj) => { (owned.get(el) ?? owned.set(el, []).get(el)).push(obj); };

  /** one prism; returns the mesh or null if the polygon cannot be triangulated. `piece` is a LIFT key. */
  const prism = (group, ring, holes, base, height, colorKey, info, piece, opts = {}) => {
    let g;
    try { g = prismGeometry(ring, holes, centre); } catch { return null; }
    if (!g.attributes.position || g.attributes.position.count === 0) { g.dispose(); return null; }
    geometries.add(g);
    const mesh = new THREE.Mesh(g, material(colorKey, opts.material));
    mesh.position.y = base;
    mesh.scale.y = height;
    mesh.castShadow = opts.castShadow ?? true;
    mesh.receiveShadow = true;
    const frameRing = opts.frameRing ?? ring; // pieces of one element (stair steps) explode as a unit
    // size = plan area of the element (not of this piece): the picking rule prefers smaller elements near the pointer
    const ex = explodeFrame(frameRing, explodeCentre, info.element?.id);
    mesh.userData = { ...info, piece, colorKey, size: Math.abs(ringArea(opts.sizeRing ?? frameRing)), category: info.element?.category ?? null, explode: { ...ex, base, lift: LIFT[piece] } };
    const flagged = (info.element?.flags?.length ?? 0) > 0 || info.generated;
    if (flagged && !info.band) addOutline(mesh);
    group.add(mesh);
    pickables.push(mesh);
    own(info.owner ?? info.element, mesh);
    return mesh;
  };

  function addOutline(mesh) {
    const eg = new THREE.EdgesGeometry(mesh.geometry, 25);
    geometries.add(eg);
    const lm = new THREE.LineBasicMaterial({ color: palette.outline, transparent: true, opacity: 0.9 });
    lm.userData.colorKey = 'outline';
    materials.add(lm);
    const line = new THREE.LineSegments(eg, lm);
    line.raycast = () => {};
    line.userData.outline = true;
    mesh.add(line);
    outlines.push(line);
  }

  // ---- per element builders ---------------------------------------------------------------------------

  function buildRoom(room) {
    const ring = isActive(room) ? cleanRing(room.polygon_m) : null;
    if (!ring) return false;
    kindByElement.set(room, 'rooms');
    const type = ROOM_COLORS[room.type] ? room.type : 'unknown';
    const info = { pickId: pidOf(room), kind: 'room', element: room };
    // 20 mm below the wall base and 10 mm inset: room outlines and wall faces come from different layers and do not coincide
    const holes = asArray(room.holes_m).map(cleanRing).filter(Boolean).map((h) => insetRing(h, -HEIGHTS.floorInset));
    const floor = prism(groups.floors, insetRing(ring, HEIGHTS.floorInset), holes, FLOOR_BASE, HEIGHTS.floorSlab, `room:${type}`, info, 'floor', { castShadow: false, sizeRing: ring });
    if (!floor) return false;
    const name = room.label ?? room.name ?? room.number;
    if (name) addLabel(groups.roomNames, String(name), 'name', floor, room);
    if (Number.isFinite(room.area_m2)) addLabel(groups.areas, `${room.area_m2.toFixed(1)} m²`, 'area', floor, room);
    return true;
  }

  function buildWall(wall) {
    const ring = isActive(wall) ? cleanRing(wall.polygon_m) : null;
    if (!ring) return false;
    kindByElement.set(wall, 'walls');
    const measured = Number.isFinite(wall.height_m) && wall.height_m > 0;
    const h = measured ? wall.height_m : HEIGHTS.wall;
    return !!prism(groups.walls, ring, null, 0, h, 'wall', { pickId: pidOf(wall), kind: 'wall', element: wall, height: { value: h, measured } }, 'wall');
  }

  /**
   * Openings: separate prisms over their own polygon; walls are never cut and wall_id is not used.
   * Only the glass (windows group) or the threshold (doors group) IS the opening. The bands below a sill and above a head are
   * wall material: they go to the walls group, are selected on their own and point back to their opening ("part of").
   */
  function buildOpening(op) {
    const ring = isActive(op) ? cleanRing(op.polygon_m) : null;
    if (!ring) return false;
    kindByElement.set(op, 'openings');
    const main = { pickId: pidOf(op), kind: 'opening', element: op };
    const band = (which) => ({ pickId: pidOf(op, which), kind: 'wallBand', band: which, element: op, owner: op, partOf: { pickId: main.pickId, id: op.id, kind: op.kind } });
    let made = 0;
    if (op.kind === 'window') {
      main.height = { sill: HEIGHTS.windowSill, head: HEIGHTS.doorHead, measured: false };
      made += !!prism(groups.walls, ring, null, 0, HEIGHTS.windowSill, 'wall', { ...band('below'), height: { from: 0, to: HEIGHTS.windowSill } }, 'windowSill');
      made += !!prism(groups.windows, ring, null, HEIGHTS.windowSill, WINDOW_GLASS, 'glass', main, 'windowGlass', {
        castShadow: false, material: { transparent: true, opacity: 0.32, side: THREE.DoubleSide, depthWrite: false, roughness: 0.1 },
      });
      made += !!prism(groups.walls, ring, null, HEIGHTS.doorHead, LINTEL, 'wall', { ...band('above'), height: { from: HEIGHTS.doorHead, to: HEIGHTS.wall } }, 'windowLintel');
    } else {
      // door: threshold strip + a leaf drawn open at 35 deg (a convention, not data); a 'doorway' has no leaf
      main.height = { head: HEIGHTS.doorHead, measured: false };
      made += !!prism(groups.doors, ring, null, 0, HEIGHTS.doorThreshold, 'threshold', main, 'threshold', { castShadow: false });
      const leaf = op.kind === 'doorway' ? null : placeLeaf(op, ring);
      if (leaf) {
        main.leaf = { swingDeg: HEIGHTS.doorSwingDeg, thickness: HEIGHTS.doorLeaf };
        made += !!prism(groups.doors, leaf, null, 0, HEIGHTS.doorHead, 'doorLeaf', main, 'doorLeaf', { frameRing: ring });
      }
      made += !!prism(groups.walls, ring, null, HEIGHTS.doorHead, LINTEL, 'wall', { ...band('above'), height: { from: HEIGHTS.doorHead, to: HEIGHTS.wall } }, 'doorLintel');
    }
    return made > 0;
  }

  function buildObject(obj) {
    const ring = isActive(obj) ? cleanRing(obj.polygon_m) : null;
    if (!ring) return false;
    kindByElement.set(obj, 'objects');
    if (obj.category === 'stair') {
      const s = stairSteps(obj, ring, floorHeight.value);
      const info = { pickId: pidOf(obj), kind: 'object', element: obj, generated: true, stair: { n: s.n, flights: s.flights ?? 0, landings: s.landings.length, riser: s.riser, depth: s.depth, from: s.from, floorHeight } };
      let made = 0;
      for (const st of s.steps) made += !!prism(groups.stairs, st.ring, null, 0, st.height, 'stair', info, 'stair', { frameRing: ring });
      for (const l of s.landings) made += !!prism(groups.stairs, l.ring, null, 0, l.height, 'stair', info, 'stair', { frameRing: ring });
      // no usable axis (degenerate footprint): a plain block over the footprint, still flagged as generated
      if (!made) made += !!prism(groups.stairs, ring, null, 0, HEIGHTS.fixture, 'stair', { ...info, stair: { ...info.stair, from: 'none' } }, 'stair');
      return made > 0;
    }
    const cat = FIXTURE_STYLE[obj.category] ? obj.category : 'other';
    const h = FIXTURE_STYLE[cat].h;
    const info = { pickId: pidOf(obj), kind: 'object', element: obj, height: { value: h, measured: false } };
    if (cat === 'shower') {
      // tray + glass box, both conventions
      let made = !!prism(groups.fixtures, ring, null, 0, 0.05, 'fixture:shower', info, 'fixture');
      made = !!prism(groups.fixtures, ring, null, 0.05, h - 0.05, 'glass', info, 'fixture', { castShadow: false, material: { transparent: true, opacity: 0.22, side: THREE.DoubleSide, depthWrite: false, roughness: 0.1 } }) || made;
      return made;
    }
    return !!prism(groups.fixtures, ring, null, 0, h, `fixture:${cat}`, info, 'fixture');
  }

  /**
   * Hinge jamb and swing side are conventions, so pick the one of the four whose leaf runs least through walls, other
   * openings and leaves already placed (points along the leaf, inside any of them); ties keep the first option.
   */
  function placeLeaf(op, ring) {
    let best = null;
    for (const [hinge, side] of [[0, 1], [0, -1], [1, 1], [1, -1]]) {
      const r = doorLeafRing(ring, { hinge, side });
      if (!r) continue;
      let hits = 0;
      for (let k = 1; k <= 9; k++) {
        const t = k / 10, p = [r[0][0] + (r[1][0] - r[0][0]) * t, r[0][1] + (r[1][1] - r[0][1]) * t];
        if (obstacles.some((o) => o.el !== op && p[0] >= o.bb[0] && p[0] <= o.bb[2] && p[1] >= o.bb[1] && p[1] <= o.bb[3] && inRing(p, o.ring))) hits++;
      }
      if (!best || hits < best.hits) best = { r, hits };
      if (!hits) break;
    }
    if (best) obstacles.push({ el: op, ring: best.r, bb: bbox(best.r) });
    return best?.r ?? null;
  }
  const bbox = (r) => r.reduce((b, [x, y]) => [Math.min(b[0], x), Math.min(b[1], y), Math.max(b[2], x), Math.max(b[3], y)], [Infinity, Infinity, -Infinity, -Infinity]);
  const obstacles = [...asArray(doc.walls), ...asArray(doc.openings)].filter(isActive).map((el) => ({ el, ring: cleanRing(el.polygon_m) })).filter((o) => o.ring).map((o) => ({ ...o, bb: bbox(o.ring) }));

  const builders = { rooms: buildRoom, walls: buildWall, openings: buildOpening, objects: buildObject };

  for (const room of asArray(doc.rooms)) {
    if (buildRoom(room)) { stats.rooms++; if (Number.isFinite(room.area_m2)) stats.floorAreaM2 += room.area_m2; } else stats.skipped++;
  }
  for (const wall of asArray(doc.walls)) buildWall(wall) ? stats.walls++ : stats.skipped++;
  for (const op of asArray(doc.openings)) buildOpening(op) ? stats.openings++ : stats.skipped++;
  for (const obj of asArray(doc.objects)) {
    if (buildObject(obj)) { stats.objects++; if (obj.category === 'stair') stats.stairs++; } else stats.skipped++;
  }

  /** Replace one element's pieces (after a correction changed its type). Returns the new pieces. */
  function rebuild(el) {
    const kindKey = kindByElement.get(el);
    for (const o of owned.get(el) ?? []) {
      o.parent?.remove(o);
      disposeObj(o);
      const i = pickables.indexOf(o); if (i >= 0) pickables.splice(i, 1);
      const j = labels.indexOf(o); if (j >= 0) labels.splice(j, 1);
      for (const c of o.children ?? []) { const k = outlines.indexOf(c); if (k >= 0) outlines.splice(k, 1); }
    }
    owned.delete(el);
    if (kindKey) builders[kindKey](el);
    return owned.get(el) ?? [];
  }

  return model;

  /**
   * Text sprite at the room floor's centroid. It follows the floor's dx/dz when pulled apart, never its y.
   * Skipped where there is no DOM canvas (e.g. geometry checks in Node).
   */
  function addLabel(group, text, style, floor, room) {
    if (typeof document === 'undefined') return;
    const [cx, cz] = ringCentroid(cleanRing(room.polygon_m)); // absolute plan position (scene frame)
    const texture = labelTexture(text, style);
    textures.add(texture);
    const mat = new THREE.SpriteMaterial({ map: texture, transparent: true, depthWrite: false, opacity: style === 'area' ? 0.72 : 1 });
    materials.add(mat);
    const sprite = new THREE.Sprite(mat);
    const worldH = style === 'area' ? 0.24 : 0.34;
    sprite.scale.set(worldH * texture.userData.aspect, worldH, 1);
    sprite.position.set(cx, LABEL_Y[style], cz);
    sprite.renderOrder = 2;
    sprite.userData = { label: { floor, cx, cz, y: LABEL_Y[style] } };
    group.add(sprite);
    labels.push(sprite);
    own(room, sprite);
  }
}

/** Canvas texture with a rounded pill behind the text (readable on light and dark floors). */
function labelTexture(text, style) {
  const px = style === 'area' ? 40 : 52;
  const font = `${style === 'area' ? 500 : 650} ${px}px system-ui, -apple-system, "Segoe UI", sans-serif`;
  const canvas = document.createElement('canvas');
  const ctx = canvas.getContext('2d');
  ctx.font = font;
  const padX = px * 0.45, padY = px * 0.28;
  const w = Math.ceil(ctx.measureText(text).width + padX * 2), h = Math.ceil(px * 1.2 + padY * 2);
  canvas.width = w; canvas.height = h;
  ctx.font = font;
  ctx.fillStyle = style === 'area' ? 'rgba(255,255,255,0.78)' : 'rgba(255,255,255,0.92)';
  ctx.beginPath();
  ctx.roundRect(0, 0, w, h, h / 2);
  ctx.fill();
  ctx.fillStyle = style === 'area' ? '#4b5563' : '#111827';
  ctx.textAlign = 'center';
  ctx.textBaseline = 'middle';
  ctx.fillText(text, w / 2, h / 2 + px * 0.04);
  const tex = new THREE.CanvasTexture(canvas);
  tex.colorSpace = THREE.SRGBColorSpace;
  tex.anisotropy = 4;
  tex.userData.aspect = w / h;
  return tex;
}

function colorFor(palette, key) {
  if (key.startsWith('fixture:')) return new THREE.Color(FIXTURE_STYLE[key.slice(8)]?.color ?? palette.fixture).multiplyScalar(palette.floorFactor < 1 ? 0.85 : 1);
  if (key.startsWith('room:')) {
    const c = new THREE.Color(ROOM_COLORS[key.slice(5)] ?? ROOM_COLORS.unknown);
    return c.multiplyScalar(palette.floorFactor);
  }
  return new THREE.Color(palette[key]);
}

// =====================================================================================================
// corrections (a lookup table for blueprint-ingest's corrections.json; no model)
// =====================================================================================================

const fold = (s) => String(s ?? '').normalize('NFD').replace(/\p{M}+/gu, '').toLowerCase().trim();

/**
 * Keys that may carry a correction for this element, most specific scope first in what the pipeline matches.
 *   block:NAME  when the element has properties.source_block
 *   room:LABEL  for rooms
 *   otherwise   ref:FILE#SOURCE_REF (this element of this drawing only) or layer:NAME (every element on its layer)
 */
export function correctionScopes(kindKey, el, file) {
  if (el.properties?.source_block) return [{ key: `block:${el.properties.source_block}`, label: `every block "${el.properties.source_block}"` }];
  if (kindKey === 'rooms') return [{ key: `room:${el.label ?? el.name ?? el.number ?? el.id}`, label: `rooms labelled "${el.label ?? el.name ?? el.number ?? el.id}"` }];
  const out = [];
  if (el.provenance?.source_ref && file) out.push({ key: `ref:${file}#${el.provenance.source_ref}`, label: 'this element only' });
  if (el.provenance?.layer) out.push({ key: `layer:${el.provenance.layer}`, label: `every element on layer ${el.provenance.layer}` });
  return out;
}

/** The value object corrections.json stores for a key, in the shape blueprint-ingest reads. */
export function correctionValue(kindKey, key, value) {
  if (kindKey === 'rooms') return { type: value };
  if (kindKey === 'objects') return { category: value };
  return key.startsWith('layer:') ? { as: value } : { kind: value }; // openings: a layer rule re-reads the layer
}

/** Does `key` select element `el` (same rules as the pipeline, case- and accent-insensitive)? */
function keyMatches(key, kindKey, el, file) {
  const i = key.indexOf(':'), kind = key.slice(0, i), name = fold(key.slice(i + 1));
  if (kind === 'ref') { const h = name.lastIndexOf('#'); return h >= 0 && name.slice(0, h) === fold(file) && name.slice(h + 1) === fold(el.provenance?.source_ref); }
  if (kind === 'block') return el.properties?.source_block != null && fold(el.properties.source_block) === name;
  if (kind === 'room') return kindKey === 'rooms' && (fold(el.label) === name || fold(el.name) === name);
  if (kind === 'layer') return el.provenance?.layer != null && fold(el.provenance.layer) === name;
  return false;
}

// =====================================================================================================
// one frame for every source: IFC world metres (x east, y north), scene origin fixed once
// =====================================================================================================

/**
 * How a drawing JSON maps to world metres.
 *   drawing.transforms[0] (affine model units -> page px, [[sx,0,ox],[0,sy,oy]]) + scale.m_per_px:
 *     world = (px - ox) * m_per_px * sign(sx), (py - oy) * m_per_px * sign(sy). For IFC sources that IS the IFC world frame,
 *     so files rendered at different px/m (clinic: 45, 51, 55) land on top of each other and on the web-ifc geometry.
 *   no transform: polygon_m in the page frame (flagged: alignment with other files not guaranteed)
 *   no scale at all (raster): a nominal 1 px = 1 cm (flagged: unscaled)
 */
export function docFrame(doc) {
  const k = doc?.drawing?.scale?.m_per_px;
  const t = asArray(doc?.drawing?.transforms).find((x) => x?.kind === 'affine' && Array.isArray(x.matrix));
  if (t && Number.isFinite(k) && k > 0) {
    const [[sx, , ox], [, sy, oy]] = t.matrix;
    if (Number.isFinite(sx) && Number.isFinite(sy) && sx && sy && Number.isFinite(ox) && Number.isFinite(oy)) {
      const gx = Math.sign(sx), gy = Math.sign(sy);
      return { kind: 'world', m: k, world: (p) => [(p[0] - ox) * k * gx, (p[1] - oy) * k * gy], dir: (dx, dy) => [dx * gx, dy * gy], note: null };
    }
  }
  if (Number.isFinite(k) && k > 0) return { kind: 'page', m: k, world: (p) => [p[0] * k, -p[1] * k], dir: (dx, dy) => [dx, -dy], note: 'no drawing.transforms entry: placed from polygon_m; alignment with other files is not guaranteed' };
  const N = 0.01;
  return { kind: 'unscaled', m: N, world: (p) => [p[0] * N, -p[1] * N], dir: (dx, dy) => [dx, -dy], note: 'no scale in this file: drawn at a nominal 1 px = 1 cm, so sizes and areas are not real' };
}

const pxRing = (doc, el, pxKey = 'polygon_px', mKey = 'polygon_m') => {
  if (Array.isArray(el?.[pxKey])) return el[pxKey];
  const k = doc?.drawing?.scale?.m_per_px;
  return Array.isArray(el?.[mKey]) && k > 0 ? el[mKey].map((p) => [p[0] / k, p[1] / k]) : null;
};

/** world bounds [minX, minY, maxX, maxY] of a document */
export function worldBounds(doc, frame) {
  let b = null;
  for (const key of ['rooms', 'walls', 'openings', 'objects']) for (const el of asArray(doc[key])) {
    for (const p of asArray(pxRing(doc, el))) {
      if (!Array.isArray(p) || !Number.isFinite(p[0]) || !Number.isFinite(p[1])) continue;
      const [x, y] = frame.world(p);
      b = b ? [Math.min(b[0], x), Math.min(b[1], y), Math.max(b[2], x), Math.max(b[3], y)] : [x, y, x, y];
    }
  }
  return b;
}

/**
 * The document with every plan coordinate moved into the scene frame (X = east - origin, Z = -(north - origin)), written
 * into polygon_m / holes_m / stair start_m so buildModel draws it as before. Elements are shallow clones (`_src` is the
 * file's own element); every value the inspector shows is the file's.
 */
export function sceneDoc(doc, frame, origin) {
  const S = (p) => { const w = frame.world(p); return [w[0] - origin.x, -(w[1] - origin.y)]; };
  const ring = (pts) => (Array.isArray(pts) ? pts.filter((p) => Array.isArray(p) && Number.isFinite(p[0]) && Number.isFinite(p[1])).map(S) : null);
  const deg = (d) => { const t = (d * Math.PI) / 180; const [wx, wy] = frame.dir(Math.cos(t), Math.sin(t)); return (Math.atan2(-wy, wx) * 180) / Math.PI; };
  const conv = (el) => {
    const c = { ...el, polygon_m: ring(pxRing(doc, el)), _src: el };
    if (Array.isArray(el.holes_px) || Array.isArray(el.holes_m)) c.holes_m = (Array.isArray(el.holes_px) ? el.holes_px : asArray(el.holes_m).map((h) => h.map((p) => [p[0] / frame.m, p[1] / frame.m]))).map(ring);
    const p = el.properties;
    if (Array.isArray(p?.flights)) {
      c.properties = {
        ...p,
        flights: p.flights.map((f) => ({ ...f, start_m: f.start_px ? S(f.start_px) : null, direction_deg: Number.isFinite(f.direction_deg) ? deg(f.direction_deg) : f.direction_deg, width_m: f.width_m ?? (f.width_px != null ? f.width_px * frame.m : null), going_m: f.going_m ?? (f.going_px != null ? f.going_px * frame.m : null) })),
        landings: asArray(p.landings).map((l) => ({ ...l, polygon_m: ring(l.polygon_px) })),
      };
    } else if (Number.isFinite(p?.run_direction_deg)) c.properties = { ...p, run_direction_deg: deg(p.run_direction_deg) };
    return c;
  };
  return { ...doc, rooms: asArray(doc.rooms).map(conv), walls: asArray(doc.walls).map(conv), openings: asArray(doc.openings).map(conv), objects: asArray(doc.objects).map(conv) };
}

// =====================================================================================================
// the scene: sources, levels, IFC <-> JSON links. No renderer, no DOM: Node checks drive exactly this.
// =====================================================================================================

const levelKeyOf = (name) => `L:${fold(name ?? '(no level)')}`;

export class BlueprintScene {
  /** @param {{ palette?: () => any }} [o] */
  constructor(o = {}) {
    this.root = new THREE.Group();
    this.root.name = 'scene';
    this.palette = o.palette ?? currentPalette;
    this.sources = [];
    this.levels = new Map();
    this.models = [];
    this.origin = null; // IFC world metres { x, y }, fixed by the first file loaded
    this.pidCounter = 0;
    this.pid = () => ++this.pidCounter;
    this.nextSourceId = 0;
    this.pickables = [];
    this.pickIndex = new Map();
    this.overlays = new Map(); // 'select' | 'hover' -> mesh
    this.ifcOutlines = [];
    this.unitCheck = [];
    this.hiddenCategories = new Set();
    this.removedEntities = new Set();
    this.view = { groups: Object.fromEntries(GROUP_NAMES.map((n) => [n, true])), explode: 0, separation: 0, cut: null, highlight: true, isolateLevel: null, hiddenLevels: new Set(), category: null };
  }

  // ---- sources ---------------------------------------------------------------------------------------------

  /** add a drawing JSON (vocabulary mapped, inventory computed); returns the source record */
  addJson(doc, name = doc?.drawing?.file ?? 'drawing.json', scope = null) {
    if (!doc || typeof doc !== 'object' || Array.isArray(doc)) throw new Error('not a drawing JSON object');
    if (!doc.drawing && !['rooms', 'walls', 'openings', 'objects'].some((k) => Array.isArray(doc[k]))) throw new Error('not a blueprint-ingest drawing JSON (no drawing, rooms, walls, openings or objects)');
    doc = renderProjection(doc);
    const vocabulary = doc._canonical ? {changed:[],unmapped:[]} : normalizeVocabulary(doc);
    const frame = docFrame(doc);
    const report = fileReport(doc);
    if (!this.origin) { const b = worldBounds(doc, frame); if (b) this.origin = { x: Math.round((b[0] + b[2]) / 2), y: Math.round((b[1] + b[3]) / 2) }; }
    const src = { id: ++this.nextSourceId, name, kind: 'json', doc, frame, report, vocabulary, scope };
    this.sources.push(src);
    try { this.assemble(); }
    catch(error) { this.remove(src.id); throw error; }
    return src;
  }

  /** add an IFC file streamed with web-ifc (api initialised by the caller); returns the source record */
  addIfc(api, W, bytes, name = 'model.ifc', scope = null) {
    const model = loadIfcModel(api, W, bytes, { origin: this.origin });
    try { return this.addIfcModel(model, name, scope, api); }
    catch(error) { api.CloseModel(model.modelID); throw error; }
  }

  addIfcModel(model, name, scope = null, api = null) {
    if (!this.origin) this.origin = model.origin;
    const c = model.elements.reduce((b, e) => [Math.min(b[0], e.bbox.min[0]), Math.min(b[1], e.bbox.min[2]), Math.max(b[2], e.bbox.max[0]), Math.max(b[3], e.bbox.max[2])], [Infinity, Infinity, -Infinity, -Infinity]);
    const meshes = ifcMeshes(model, { lift: GROUP_LIFT, centre: { x: (c[0] + c[2]) / 2 || 0, z: (c[1] + c[3]) / 2 || 0 }, categoryColor: Object.fromEntries(Object.entries(FIXTURE_STYLE).map(([k, v]) => [k, v.color])) });
    const heavy = model.stats.triangles > 500000;
    try {
    for (const m of meshes) {
      // BVH so picking stays fast on millions of triangles. `indirect`: the BVH must NOT reorder the index buffer, because every
      // element keeps its own index ranges (highlight overlays, outlines)
      const bvh=m.userData.ifc.batch.bvh;
      if(bvh) { m.geometry.boundsTree=MeshBVH.deserialize(bvh,m.geometry); delete m.userData.ifc.batch.bvh; }
      else m.geometry.computeBoundsTree?.({ indirect: true });
      if (heavy) m.castShadow = false; // the shadow pass would double the cost of a 3M-triangle model
    }
    for (const el of model.elements) el.pickId = this.pid();
    if (model.stats.unsupported) console.warn(`[blueprint-viewer] ${name}: web-ifc could not mesh ${model.stats.unsupported} representation(s) (unsupported type), e.g. ${model.stats.unsupportedSamples[0] ?? ''}`);
    const src = { id: ++this.nextSourceId, name, kind: 'ifc', model, meshes, api, scope };
    this.sources.push(src);
    this.assemble();
    return src;
    } catch(error) {
      const src=this.sources.find(s=>s.model===model);
      if(src){src.api=null;this.remove(src.id);}
      else {for(const mesh of meshes){mesh.geometry.disposeBoundsTree?.();mesh.geometry.dispose();mesh.material.dispose();}if(!this.sources.length)this.origin=null;}
      throw error;
    }
  }

  /** remove a source; an IFC model is closed in web-ifc (its WASM memory is released for reuse) */
  remove(id, rebuild = true) {
    const i = this.sources.findIndex((s) => s.id === id);
    if (i < 0) return;
    const [s] = this.sources.splice(i, 1);
    if (s.kind === 'ifc') {
      for (const m of s.meshes) { m.parent?.remove(m); m.geometry.disposeBoundsTree?.(); m.geometry.dispose(); m.material.dispose(); }
      s.api?.CloseModel(s.model.modelID);
    }
    if (!this.sources.length) this.origin = null;
    if(rebuild)this.assemble();
  }

  clearAll() { for (const s of [...this.sources]) this.remove(s.id,false); this.assemble(); }

  // ---- assembly --------------------------------------------------------------------------------------------

  /** (re)build everything that depends on the set of sources: JSON geometry, levels, links, pick index */
  assemble() {
    this.setOverlay('select', null); this.setOverlay('hover', null);
    for (const o of this.ifcOutlines) { o.parent?.remove(o); o.geometry.dispose(); o.material.dispose(); }
    this.ifcOutlines = [];
    for (const m of this.models) m.dispose();
    for (const lv of this.levels.values()) { for (const cat of Object.values(lv.cats)) for (const c of [...cat.children]) cat.remove(c); this.root.remove(lv.group); }
    this.models = [];
    const levels = new Map();
    const levelFor = (name, elevation, src, known = Number.isFinite(elevation), identity = null) => {
      const scope = src.scope ?? String(src.doc?.drawing?.read_from ?? src.name).replaceAll('\\','/').split('/').pop().replace(/\.json$/i,'');
      const key = `${scope}:${identity ?? levelKeyOf(name)}`;
      const storey=src.model?.storeys.find(s=>(s.globalId??s.expressID)===identity);
      const buildingName=src.doc?.project?.building_name??storey?.buildingName??src.name;
      let lv = levels.get(key);
      if (!lv) {
        lv = { key, name: name ?? '(no level)', elevation: known ? elevation : 0, known, sources: new Set(), group: new THREE.Group(), cats: {}, notes: new Set(), rank: 0 };
        lv.group.name = `level ${lv.name}`;
        lv.buildingName=buildingName;lv.buildingKey=`${scope}:${storey?.buildingGuid??src.doc?.building_id??buildingName}`;
        for (const g of GROUP_NAMES) { const c = new THREE.Group(); c.name = g; lv.cats[g] = c; lv.group.add(c); }
        levels.set(key, lv);
      } else if (known && !lv.known) { lv.elevation = elevation; lv.known = true; }
      else if (known && lv.known && Math.abs(lv.elevation - elevation) > 0.01) lv.notes.add(`${src.name} gives ${elevation} m; ${lv.elevation} m kept`);
      if (!known) lv.notes.add(`${src.name}: no elevation in the file, placed at 0 m`);
      lv.sources.add(src.id);
      return lv;
    };

    // IFC elements by GlobalId (match on provenance.source_ref = the RAW GUID, never on the sanitised element id)
    const byGuid = new Map();
    for (const s of this.sources) if (s.kind === 'ifc') for (const el of s.model.elements) { el.json = null; el.jsonSource = null; if (el.globalId) { const list = byGuid.get(el.globalId) ?? []; list.push({el,source:s}); byGuid.set(el.globalId,list); } }

    const origin = this.origin ?? { x: 0, y: 0 };
    for (const s of this.sources) {
      if (s.kind !== 'json') continue;
      const sd = sceneDoc(s.doc, s.frame, origin);
      s.match = { elements: 0, withMesh: 0, withoutMesh: 0, roomsNoMesh: 0 };
      const keep = (list, kindKey) => list.filter((e) => {
        if (!isActive(e)) return true;
        s.match.elements++;
        const gid = e.ifc_global_id ?? e.global_id ?? e.provenance?.source_ref; // v0.3 has global_id; v0.2 keeps the raw GUID in source_ref
        const matches = gid ? (byGuid.get(gid) ?? []).filter(m => !s.scope || m.source.scope === s.scope) : [];
        const el = matches.length === 1 ? matches[0].el : null;
        if (el) { el.json = e; el.jsonSource = s; el.jsonKind = kindKey; s.match.withMesh++; return false; } // web-ifc draws it
        s.match.withoutMesh++;
        if (kindKey === 'rooms') s.match.roomsNoMesh++;
        return true;
      });
      for (const k of ['rooms', 'walls', 'openings', 'objects']) sd[k] = keep(sd[k], k);
      // one model per level of the document
      const lvls = asArray(s.doc.levels);
      const parts = new Map();
      for (const L of lvls) parts.set(L.id, { rooms:[], walls:[], openings:[], objects:[] });
      for (const k of ['rooms', 'walls', 'openings', 'objects']) for (const e of sd[k]) {
        const lid = e.level_id && lvls.some((l) => l.id === e.level_id) ? e.level_id : null;
        const p = parts.get(lid) ?? parts.set(lid, { rooms: [], walls: [], openings: [], objects: [] }).get(lid);
        p[k].push(e);
      }
      if (!parts.size) parts.set(lvls[0]?.id ?? null, { rooms: [], walls: [], openings: [], objects: [] });
      for (const [lid, p] of parts) {
        const L = lvls.find((l) => l.id === lid) ?? null;
        const lv = levelFor(L?.name ?? 'Unassigned', L?.elevation_m, s, Number.isFinite(L?.elevation_m), L?.ifc_global_id ?? L?.global_id ?? L?.provenance?.source_ref ?? L?.id ?? 'unassigned');
        lv.entityId = L?.id ?? null;
        const model = buildModel({ ...sd, levels: L ? [L] : [], ...p }, this.palette(), { sceneFrame: true, pid: this.pid });
        model.source = s; model.level = lv;
        this.models.push(model);
        for (const g of GROUP_NAMES) for (const c of [...model.groups[g].children]) lv.cats[g].add(c);
      }
      s.geometry = s.match.withMesh ? `web-ifc for ${s.match.withMesh} matched element(s); JSON polygons for ${s.match.withoutMesh} (${s.match.roomsNoMesh} rooms: web-ifc does not stream IfcSpace)` : 'JSON polygons (extruded from 2D, heights assumed)';
      s.approximate = !s.match.withMesh && s.doc.drawing?.route === 'ifc';
    }

    for (const s of this.sources) {
      if (s.kind !== 'ifc') continue;
      const st = new Map(s.model.storeys.map((x) => [x.expressID, x]));
      for (const sto of st.values()) levelFor(sto.name, sto.elevation, s, Number.isFinite(sto.elevation), sto.globalId ?? sto.expressID);
      for (const mesh of s.meshes) {
        const sto = st.get(mesh.userData.storeyId);
        const lv = levelFor(sto?.name ?? 'Unassigned', sto?.elevation, s, sto ? Number.isFinite(sto.elevation) : false, sto?.globalId ?? sto?.expressID ?? 'unassigned');
        lv.cats[mesh.userData.group].add(mesh);
      }
      const withJson = s.model.elements.filter((e) => e.json).length;
      s.match = { meshes: s.model.elements.length, withJson, withoutJson: s.model.elements.length - withJson, unmatchedTypes: topTypes(s.model.elements.filter((e) => !e.json)) };
      s.geometry = 'web-ifc (real 3D solids)';
    }

    this.levels = levels;
    // duplicate elevations get a rank among equals, so "Separate levels" can still pull them apart
    const sorted = [...levels.values()].sort((a, b) => a.elevation - b.elevation || a.name.localeCompare(b.name));
    sorted.forEach((lv, i) => { lv.rank = i; lv.dup = sorted.filter((o, j) => j < i && Math.abs(o.elevation - lv.elevation) < 1e-3).length; this.root.add(lv.group); });

    this.pickables = [];
    this.pickIndex = new Map();
    for (const m of this.models) for (const p of m.pickables) {
      this.pickables.push(p);
      const e = this.pickIndex.get(p.userData.pickId) ?? this.pickIndex.set(p.userData.pickId, { kind: 'json', model: m, meshes: [] }).get(p.userData.pickId);
      e.meshes.push(p);
    }
    for (const s of this.sources) if (s.kind === 'ifc') {
      for (const m of s.meshes) this.pickables.push(m);
      const meshByKey = new Map(s.meshes.map((m) => [m.userData.ifc.batch.key, m]));
      for (const el of s.model.elements) this.pickIndex.set(el.pickId, { kind: 'ifc', source: s, el, meshByKey });
    }
    this.unitCheck = this.computeUnitCheck();
    this.applyView();
  }

  /** pset unit check over every loaded pipe-like element (real geometry when web-ifc has it, else the 2D footprint) */
  computeUnitCheck() {
    const samples = [];
    const seen = new Set();
    for (const s of this.sources) if (s.kind === 'ifc') for (const el of s.model.elements) {
      if (!el.json?.properties?.psets) continue;
      seen.add(el.json._src);
      samples.push({ psets: el.json.properties.psets, measured: measureFromBox(el.dims), from: 'web-ifc' });
    }
    for (const s of this.sources) if (s.kind === 'json') for (const o of asArray(s.doc.objects)) {
      if (!o.properties?.psets || seen.has(o)) continue;
      const r = cleanRing(o.polygon_m); const ax = r ? longAxis(r) : null;
      samples.push({ psets: o.properties.psets, measured: measureFromFootprint(ax), from: 'footprint' });
    }
    const res = checkPsetUnits(samples);
    for (const r of res) r.source = samples.some((x) => x.from === 'web-ifc') ? 'real geometry (web-ifc)' : 'plan footprints (JSON)';
    return samples.length ? res : [];
  }

  // ---- view state ------------------------------------------------------------------------------------------

  applyView() {
    const v = this.view;
    for (const lv of this.levels.values()) {
      for (const g of GROUP_NAMES) lv.cats[g].visible = v.groups[g];
      lv.group.visible = v.isolateLevel ? lv.key === v.isolateLevel : !v.hiddenLevels.has(lv.key);
    }
    this.setSeparation(v.separation);
    this.setExplode(v.explode, true);
    this.isolateCategory(v.category);
    this.setHighlightAssumptions(v.highlight);
    this.applyEntityVisibility();
  }

  setGroupVisible(name, on) { this.view.groups[name] = !!on; for (const lv of this.levels.values()) if (lv.cats[name]) lv.cats[name].visible = !!on; }
  setLevelVisible(key, on) { on ? this.view.hiddenLevels.delete(key) : this.view.hiddenLevels.add(key); this.applyView(); }
  isolateLevel(key) { this.view.isolateLevel = key || null; this.applyView(); }

  /** level y: real elevation, spread up to 3x the real spacing at s = 1 (levels at the same elevation get 3 m per rank) */
  levelY(lv, s = this.view.separation) {
    const all = [...this.levels.values()];
    const minE = Math.min(...all.map((l) => l.elevation));
    return minE + (lv.elevation - minE) * (1 + 2 * s) + s * 3 * (lv.dup ?? 0);
  }
  setSeparation(s) {
    this.view.separation = Math.min(1, Math.max(0, Number(s) || 0));
    for (const lv of this.levels.values()) lv.group.position.y = this.levelY(lv);
    this.root.updateMatrixWorld(true);
  }

  /** pull apart (per piece, independent of level separation) */
  setExplode(e, force = false) {
    const k = Math.min(1, Math.max(0, Number(e) || 0));
    if (!force && k === this.view.explode) return;
    this.view.explode = k;
    for (const m of this.models) {
      for (const p of m.pickables) { const x = p.userData.explode; p.position.set(x.cx * k * EXPLODE_SPREAD + x.dir.x * k * EXPLODE_PUSH, x.base + k * x.lift * 1.6, x.cz * k * EXPLODE_SPREAD + x.dir.z * k * EXPLODE_PUSH); }
      for (const s of m.labels) { const l = s.userData.label; s.position.set(l.cx + l.floor.position.x, l.y, l.cz + l.floor.position.z); }
    }
    for (const s of this.sources) if (s.kind === 'ifc') for (const mesh of s.meshes) explodeIfcMesh(mesh, k);
    for (const o of this.ifcOutlines) o.userData.refresh?.();
    this.refreshOverlays();
    this.root.updateMatrixWorld(true);
  }

  /** fixture legend: category -> { count, color, from } over JSON-drawn objects and web-ifc fixtures */
  legend() {
    const out = new Map();
    const add = (cat, from) => { const c = FIXTURE_STYLE[cat] ? cat : cat === 'stair' ? 'stair' : 'other'; const e = out.get(c) ?? out.set(c, { category: c, count: 0, color: c === 'stair' ? PALETTES.light.stair : FIXTURE_STYLE[c].color, from: new Set() }).get(c); e.count++; e.from.add(from); };
    for (const m of this.models) for (const p of m.pickables) if (p.parent?.name === 'fixtures' && p.userData.kind === 'object' && p.userData.piece === 'fixture' && p.userData.colorKey !== 'glass') add(p.userData.element.category, 'JSON polygons');
    for (const s of this.sources) if (s.kind === 'ifc') for (const el of s.model.elements) if (el.group === 'fixtures') add(el.json?.category ?? el.category ?? 'other', 'web-ifc');
    return [...out.values()].sort((a, b) => b.count - a.count).map((e) => ({ ...e, from: [...e.from] }));
  }
  /** show only one fixture category (null = all) */
  isolateCategory(cat) {
    this.view.category = cat || null;
    for (const m of this.models) for (const p of m.pickables) if (p.parent?.name === 'fixtures') p.visible = !cat || (FIXTURE_STYLE[p.userData.element.category] ? p.userData.element.category : 'other') === cat;
    for (const s of this.sources) if (s.kind === 'ifc') for (const mesh of s.meshes) if (mesh.userData.group === 'fixtures') mesh.visible = !cat || (mesh.userData.category ?? 'other') === cat;
  }

  /** outline every piece whose element carries a flag (JSON pieces, and web-ifc elements whose JSON element is flagged) */
  setHighlightAssumptions(on) {
    this.view.highlight = !!on;
    for (const m of this.models) for (const l of m.outlines) l.visible = this.view.highlight;
    if (this.view.highlight && !this.ifcOutlines.length) {
      for (const s of this.sources) if (s.kind === 'ifc') {
        const meshByKey = new Map(s.meshes.map((m) => [m.userData.ifc.batch.key, m]));
        for (const el of s.model.elements) {
          if (!(el.json?.flags?.length)) continue;
          const eg = elementGeometry(s.model, el, meshByKey);
          if (!eg) continue;
          const line = new THREE.LineSegments(new THREE.EdgesGeometry(eg.geometry, 25), new THREE.LineBasicMaterial({ color: this.palette().outline }));
          eg.geometry.dispose();
          line.raycast = () => {};
          eg.mesh.parent?.add(line);
          this.ifcOutlines.push(line);
        }
      }
    }
    for (const o of this.ifcOutlines) o.visible = this.view.highlight;
  }

  categories() {
    const counts=new Map();
    for (const e of this.pickIndex.values()) { const c=recordCategory(e); counts.set(c,(counts.get(c)??0)+1); }
    return [...counts].sort(([a],[b])=>a.localeCompare(b)).map(([name,count])=>({name,count,visible:!this.hiddenCategories.has(name)}));
  }
  setCategoryVisible(category, visible) {
    visible ? this.hiddenCategories.delete(category) : this.hiddenCategories.add(category);
    this.applyEntityVisibility();
  }
  applyEntityVisibility() {
    for (const e of this.pickIndex.values()) {
      const visible=!this.hiddenCategories.has(recordCategory(e)) && !this.removedEntities.has(identityOfRecord(e));
      e.workspaceVisible=visible;
      if (e.kind==='json') for (const mesh of e.meshes) mesh.visible=visible && (!this.view.category || mesh.userData.element.category===this.view.category);
      else for (const range of e.el.ranges) {
        const mesh=e.meshByKey.get(range.key);
        if(mesh) setRangeVisibility(mesh,[range],visible);
      }
    }
  }

  // ---- picking / highlighting --------------------------------------------------------------------------------

  /** ordered candidates under the pointer (see pickCandidates) */
  candidates(raycaster, camera, ndc, o = {}) { return pickCandidates(raycaster, camera, this.pickables, ndc, { ...o, cut: this.view.cut }).filter(id=>this.isShown(id)); }

  /** is any piece of this element in a visible group / level? (a hidden layer is never selectable) */
  isShown(id) {
    const e = this.pickIndex.get(id);
    if (!e || e.workspaceVisible === false) return false;
    if (e.kind === 'json') return e.meshes.some(shown);
    return e.el.ranges.some((r) => { const m = e.meshByKey.get(r.key); return m && shown(m); });
  }

  /** overlay (selection / hover) for an IFC element; JSON pieces are tinted by the viewer */
  setOverlay(which, id, color) {
    const old = this.overlays.get(which);
    if (old) { old.parent?.remove(old); old.geometry.dispose(); old.material.dispose(); this.overlays.delete(which); }
    const e = id != null ? this.pickIndex.get(id) : null;
    if (!e || e.kind !== 'ifc') return;
    const eg = elementGeometry(e.source.model, e.el, e.meshByKey);
    if (!eg) return;
    const mat = new THREE.MeshBasicMaterial({ color, transparent: true, opacity: which === 'select' ? 0.55 : 0.35, depthWrite: false, side: THREE.DoubleSide, polygonOffset: true, polygonOffsetFactor: -2, polygonOffsetUnits: -2 });
    const o = new THREE.Mesh(eg.geometry, mat);
    o.raycast = () => {};
    o.renderOrder = 3;
    o.userData = { overlay: which, id, color };
    eg.mesh.parent?.add(o);
    this.overlays.set(which, o);
  }
  refreshOverlays() { for (const [which, o] of [...this.overlays]) this.setOverlay(which, o.userData.id, o.userData.color); }

  /** inspector content for a pickId */
  describe(id) {
    const e = this.pickIndex.get(id);
    if (!e) return null;
    if (e.kind === 'ifc') return describeIfc(e.el, e.source, this.unitCheck);
    const u = e.meshes[0].userData;
    const info = describe(u, e.model.kindOf(u.element), e.model.source?.doc?.drawing?.file, twinsOf(e.model.source, u.element), this.unitCheck, e.model.source?.doc);
    info.source = e.model.source?.name;
    info.geometry = 'JSON polygons, extruded (heights are conventions)';
    return info;
  }

  /** combined bounds (current positions) */
  bounds() { const b = new THREE.Box3(); for (const p of this.pickables) if (p.visible !== false) b.expandByObject(p); return b; }

  /** everything the side panels show */
  summary() {
    return {
      origin: this.origin,
      categories:this.categories(),
      sources: this.sources.map((s) => ({
        id: s.id, name: s.name, kind: s.kind, geometry: s.geometry, match: s.match, approximate: !!s.approximate,
        frame: s.frame ? { kind: s.frame.kind, note: s.frame.note } : null,
        report: s.report ?? null, contents: s.report ? contentsSummary(s.report) : null, vocabulary: s.vocabulary ? { changed: s.vocabulary.changed.length, unmapped: s.vocabulary.unmapped } : null,
        ifc: s.kind === 'ifc' ? { stats: s.model.stats, storeys: s.model.storeys.map((x) => ({ name: x.name, elevation: x.elevation })), groups: countBy(s.model.elements, (e) => e.group), types: topTypes(s.model.elements, 8) } : null,
      })),
      levels: [...this.levels.values()].sort((a, b) => a.rank - b.rank).map((lv) => ({ key: lv.key, name: lv.name,buildingName:lv.buildingName,buildingKey:lv.buildingKey, elevation: lv.elevation, known: lv.known, notes: [...lv.notes], visible: lv.group.visible, sources: [...lv.sources] })),
      legend: this.legend(),
      unitCheck: this.unitCheck,
    };
  }
}

const countBy = (list, f) => list.reduce((m, x) => { const k = f(x); m[k] = (m[k] ?? 0) + 1; return m; }, {});
function topTypes(els, n = 6) { return Object.entries(countBy(els, (e) => e.ifcType)).sort((a, b) => b[1] - a[1]).slice(0, n).map(([t, c]) => `${t} ${c}`); }
function twinsOf(src, el) {
  const id = el?.id;
  return src?.report?.sameSpot?.flatMap((p) => (p.a === id ? [p.b] : p.b === id ? [p.a] : [])) ?? [];
}

// =====================================================================================================
// viewer: renderer, camera, pointer around a BlueprintScene
// =====================================================================================================

/** web-ifc lives next to the page (vendor/web-ifc/, copied there by `npm install`); the .wasm is served, not bundled */
const WASM_DIR = typeof location !== 'undefined' ? new URL('./vendor/web-ifc/', import.meta.url).href : null;

export class BlueprintViewer {
  /**
   * @param {HTMLElement} container
   * @param {{ onSelect?: (info:any) => void, onCorrectionsChange?: (list:any[]) => void, onChange?: (summary:any) => void }} [opts]
   */
  constructor(container, opts = {}) {
    this.container = container;
    this.listeners = new AbortController();
    this.session = new FileSession(opts.onState);
    this.disposed = false;
    this.onSelect = opts.onSelect ?? (() => {});
    this.onCorrectionsChange = opts.onCorrectionsChange ?? (() => {});
    this.onChange = opts.onChange ?? (() => {});
    this.bp = new BlueprintScene();
    this.hovered = null;
    this.selected = null;
    this.corrections = [];
    this.cutPlane = new THREE.Plane(new THREE.Vector3(0, -1, 0), HEIGHTS.wall);
    this.cutPlanes = [this.cutPlane];
    this.ifc = null; // { api, W } once web-ifc is initialised

    const renderer = new THREE.WebGLRenderer({ antialias: true });
    renderer.setPixelRatio(Math.min(window.devicePixelRatio || 1, 2));
    renderer.shadowMap.enabled = true;
    renderer.shadowMap.type = THREE.PCFSoftShadowMap;
    container.appendChild(renderer.domElement);
    this.renderer = renderer;
    this.scene = new THREE.Scene();
    this.camera = new THREE.PerspectiveCamera(46, 1, 0.05, 4000);
    this.hemi = new THREE.HemisphereLight(0xffffff, 0x8a8f99, 1.1);
    this.sun = new THREE.DirectionalLight(0xffffff, 2.2);
    this.sun.castShadow = true;
    this.sun.shadow.mapSize.set(2048, 2048);
    this.sun.shadow.bias = -0.0004;
    this.sun.shadow.normalBias = 0.02;
    this.fill = new THREE.DirectionalLight(0xdfe8ff, 0.45);
    this.scene.add(this.hemi, this.sun, this.sun.target, this.fill, this.bp.root);
    this.controls = new OrbitControls(this.camera, renderer.domElement);
    this.controls.enableDamping = !reducedMotionQuery?.matches;
    this.controls.dampingFactor = 0.08;
    this.raycaster = new THREE.Raycaster();
    this.raycaster.firstHitOnly = false;
    this.downAt = null;
    this.bindPointer();
    this.onResize = () => this.resize();
    this.listen(window, 'resize', this.onResize);
    this.listen(reducedMotionQuery, 'change', (e) => { this.controls.enableDamping = !e.matches; });
    this.listen(darkQuery, 'change', () => this.applyPalette());
    this.applyPalette();
    this.resize();
    // frame timing for the panel (rolling average over the last 60 frames)
    this.frameTimes = [];
    let last = performance.now();
    this.tick = () => {
      const now = performance.now();
      this.frameTimes.push(now - last); last = now;
      if (this.frameTimes.length > 60) this.frameTimes.shift();
      this.controls.update();
      renderer.render(this.scene, this.camera);
    };
    renderer.setAnimationLoop(this.tick);
  }

  get fps() { const a = this.frameTimes; return a.length ? 1000 / (a.reduce((s, x) => s + x, 0) / a.length) : 0; }
  get drawStats() { return { calls: this.renderer.info.render.calls, triangles: this.renderer.info.render.triangles }; }

  /**
   * Initialise web-ifc once. A wrong wasm path would otherwise fail as a silently EMPTY scene, so a failed Init is diagnosed
   * (is the .wasm served at all?) and a successful one is verified (the module really instantiated).
   */
  async webIfc() {
    if (this.ifcPromise) return this.ifcPromise;
    this.ifcPromise = this.initWebIfc().catch(error => { this.ifcPromise = null; throw error; });
    return this.ifcPromise;
  }

  async initWebIfc() {
    if (this.ifc) return this.ifc;
    const wasmUrl = `${WASM_DIR}web-ifc.wasm`;
    const W = await import('web-ifc');
    const api = new W.IfcAPI();
    api.SetWasmPath(WASM_DIR, true);
    const restore = captureWebIfcLog(); // before Init: emscripten binds console once, at instantiation
    try {
      await api.Init(undefined, true); // single-threaded: the multi-threaded build needs cross-origin isolation headers
    } catch (err) {
      const r = await fetch(wasmUrl, { method: 'HEAD' }).catch((e) => ({ ok: false, statusText: e.message }));
      throw new Error(r.ok ? `web-ifc failed to initialise from ${wasmUrl}: ${err.message}` : `web-ifc.wasm is not served at ${wasmUrl} (${r.status ?? ''} ${r.statusText ?? ''}). Run "npm install" in blueprint-viewer: it copies the wasm into vendor/web-ifc/.`);
    } finally { restore(); }
    if (!api.wasmModule) throw new Error(`web-ifc did not instantiate (wasm at ${wasmUrl})`);
    if (this.disposed) { api.Dispose?.(); throw new Error('Viewer was disposed'); }
    this.ifc = { api, W, wasmUrl };
    return this.ifc;
  }

  /**
   * Add files (.json and .ifc, any mix). Adds to the model, never replaces it. Returns per-file results.
   * @param {Array<File | { name:string, text?:() => Promise<string>, arrayBuffer?:() => Promise<ArrayBuffer> }>} files
   */
  addFiles(files) {
    if (this.disposed) return Promise.reject(new Error('Viewer was disposed'));
    return this.session.run(token => this.importFiles(files, token), () => this.bp.sources.length > 0);
  }

  async importFiles(files, token) {
    const results = [];
    for (const f of files) {
      if (!token.current() || this.disposed) break;
      try {
        if (/\.ifc$/i.test(f.name)) {
          const t0 = performance.now();
          const bytes = new Uint8Array(await f.arrayBuffer());
          if (!token.current()) break;
          token.processing();
          let src;
          if(typeof Worker!=='undefined') {
            const model=await this.parseIfcWorker(bytes,token.signal);
            if(!token.current())break;
            src=this.bp.addIfcModel(model,f.name,f.documentId ?? null);
          } else {
            const {api,W}=await this.webIfc();if(!token.current())break;
            src=this.bp.addIfc(api,W,bytes,f.name,f.documentId ?? null);
          }
          results.push({ name: f.name, ok: true, kind: 'ifc', ms: Math.round(performance.now() - t0), stats: src.model.stats });
        } else {
          const doc = JSON.parse(await f.text());
          if (!token.current()) break;
          token.processing();
          const src = this.bp.addJson(doc, f.name, f.documentId ?? null);
          logReport(src.report);
          results.push({ name: f.name, ok: true, kind: 'json' });
        }
      } catch (err) {
        console.error(err);
        results.push({ name: f.name, ok: false, error: err.message });
      }
    }
    if (token.current()) this.afterChange(true);
    return results;
  }

  parseIfcWorker(bytes,signal) {
    return new Promise((resolve,reject)=>{
      const worker=new Worker(new URL('./ifc.worker.js',import.meta.url),{type:'module'});
      const done=()=>{worker.terminate();signal.removeEventListener('abort',cancel);};
      const cancel=()=>{done();reject(new DOMException('Import cancelled','AbortError'));};
      signal.addEventListener('abort',cancel,{once:true});
      if(signal.aborted)return cancel();
      worker.onmessage=event=>{done();event.data.error?reject(Error(event.data.error)):resolve(event.data.model);};
      worker.onerror=event=>{done();reject(Error(event.message));};
      worker.postMessage({bytes:bytes.buffer,origin:this.bp.origin},[bytes.buffer]);
    });
  }

  remove(id) {
    this.session.invalidate();
    this.bp.remove(id);
    if (!this.bp.sources.length) this.resetFilters();
    this.afterChange(!this.bp.sources.length);
    this.session.set(this.bp.sources.length ? 'READY' : 'EMPTY');
  }
  resetFilters() {
    Object.assign(this.bp.view, { isolateLevel: null, category: null, cut: null, explode: 0, separation: 0 });
    this.bp.view.hiddenLevels.clear();
    this.bp.hiddenCategories.clear();
    this.bp.removedEntities.clear();
    for (const key of GROUP_NAMES) this.bp.view.groups[key] = true;
    this.renderer.clippingPlanes = [];
  }
  clear() {
    this.session.invalidate();
    this.bp.clearAll();
    this.corrections = [];
    this.onCorrectionsChange([]);
    this.resetFilters();
    this.afterChange(true);
    this.session.set('EMPTY');
  }
  listen(target, type, handler, options = {}) {
    target?.addEventListener(type, handler, { ...options, signal: this.listeners.signal });
  }
  dispose() {
    if (this.disposed) return;
    this.clear();
    this.disposed = true;
    this.listeners.abort();
    this.renderer.setAnimationLoop(null);
    this.controls.dispose();
    this.sun.shadow.map?.dispose();
    this.renderer.renderLists.dispose();
    this.renderer.dispose();
    this.renderer.domElement.remove();
    this.ifc?.api.Dispose?.();
  }

  afterChange(reframe) {
    this.renderer.setAnimationLoop(this.bp.sources.length ? this.tick : null);
    this.setHovered(null);
    this.select(null);
    this.disposeEnvironment();
    if (this.bp.pickables.length) { this.buildEnvironment(); if (reframe) this.frame(); }
    this.applyPalette();
    this.onChange(this.bp.summary());
  }

  summary() { return this.bp.summary(); }

  buildEnvironment() {
    const box = this.bp.bounds();
    if (box.isEmpty()) return;
    const size = box.getSize(new THREE.Vector3()), c = box.getCenter(new THREE.Vector3());
    const span = Math.max(size.x, size.z, 1);
    const extent = Math.ceil(span * 1.6 + 4);
    const p = currentPalette();
    const groundGeo = new THREE.PlaneGeometry(extent * 3, extent * 3);
    groundGeo.rotateX(-Math.PI / 2);
    this.ground = new THREE.Mesh(groundGeo, new THREE.MeshStandardMaterial({ color: p.ground, roughness: 1 }));
    this.ground.position.set(c.x, box.min.y - 0.012, c.z);
    this.ground.receiveShadow = true;
    this.grid = new THREE.GridHelper(extent, extent, p.gridCenter, p.grid);
    this.grid.position.set(Math.round(c.x), box.min.y - 0.006, Math.round(c.z));
    this.scene.add(this.ground, this.grid);
    const r = span * 0.5 * (1 + EXPLODE_SPREAD) + EXPLODE_PUSH + 6 + size.y;
    this.sun.position.set(c.x + span * 0.6, c.y + span * 1.2 + 5, c.z + span * 0.45);
    this.sun.target.position.copy(c);
    Object.assign(this.sun.shadow.camera, { left: -r, right: r, top: r, bottom: -r, near: 0.5, far: span * 4 + 40 });
    this.sun.shadow.camera.updateProjectionMatrix();
    this.fill.position.set(c.x - span * 0.6, c.y + span * 0.8, c.z - span * 0.5);
  }
  disposeEnvironment() {
    for (const k of ['ground', 'grid']) if (this[k]) { this.scene.remove(this[k]); this[k].geometry.dispose(); this[k].material.dispose(); this[k] = null; }
  }
  frame() {
    const box = this.bp.bounds();
    if (box.isEmpty()) return;
    const c = box.getCenter(new THREE.Vector3()), size = box.getSize(new THREE.Vector3());
    this.resize();
    const span = Math.max(size.x, size.z, size.y, 1);
    const halfFov = Math.atan(Math.tan(THREE.MathUtils.degToRad(this.camera.fov / 2)) * Math.min(1, this.camera.aspect));
    const d = Math.max(size.length() / 2, .5) / Math.sin(halfFov) * 1.12 / Math.sqrt(3);
    this.camera.position.set(c.x + d, c.y + d, c.z + d);
    this.camera.near = Math.max(0.01, span / 2000);
    this.camera.far = span * 20 + 200;
    this.camera.updateProjectionMatrix();
    this.controls.target.copy(c);
    this.controls.update();
  }
  /** top of the model, for the cut slider range */
  get modelTop() { const b = this.bp.bounds(); return b.isEmpty() ? HEIGHTS.wall : b.max.y; }
  get modelBottom() { const b = this.bp.bounds(); return b.isEmpty() ? 0 : b.min.y; }

  resize() {
    const w = this.container.clientWidth, h = this.container.clientHeight;
    if (!w || !h) return;
    this.renderer.setSize(w, h, false);
    this.renderer.domElement.style.width = '100%';
    this.renderer.domElement.style.height = '100%';
    this.camera.aspect = w / h;
    this.camera.updateProjectionMatrix();
  }
  applyPalette() {
    const p = currentPalette();
    this.scene.background = new THREE.Color(p.background);
    if (this.ground) this.ground.material.color.set(p.ground);
    for (const m of this.bp.models) {
      for (const q of m.pickables) q.material.color.copy(colorFor(p, q.userData.colorKey));
      for (const l of m.outlines) l.material.color.set(p.outline);
    }
    this.refreshTints();
  }

  // ---- view controls ----------------------------------------------------------------------------------
  setGroupVisible(name, visible) { this.bp.setGroupVisible(name, visible); this.dropHidden(); }
  setCategoryVisible(key, visible) { this.bp.setCategoryVisible(key,visible); this.dropHidden(); this.onChange(this.summary()); }
  setLevelVisible(key, visible) { this.bp.setLevelVisible(key, visible); this.dropHidden(); }
  isolateLevel(key) { this.bp.isolateLevel(key); this.dropHidden(); }
  setLevelSeparation(s) { this.bp.setSeparation(s); this.bp.refreshOverlays(); }
  isolateCategory(cat) { this.bp.isolateCategory(cat); this.dropHidden(); }
  setHighlightAssumptions(on) { this.bp.setHighlightAssumptions(on); }
  setExplode(e) { this.bp.setExplode(e); }
  setCut(height) {
    if (height == null || !Number.isFinite(height)) { this.bp.view.cut = null; this.renderer.clippingPlanes = []; return; }
    this.bp.view.cut = height;
    this.cutPlane.constant = height;
    this.renderer.clippingPlanes = this.cutPlanes;
  }
  dropHidden() {
    if (this.hovered && !this.bp.isShown(this.hovered)) this.setHovered(null);
    if (this.selected && !this.bp.isShown(this.selected)) this.select(null);
    this.onVisibilityChange?.();
  }

  // ---- corrections (JSON elements only: a lookup table for blueprint-ingest) --------------------------------
  applyCorrection(pickId, key, value) {
    const e = this.bp.pickIndex.get(pickId);
    const clone = e?.kind === 'json' ? e.meshes[0].userData.element : e?.el?.json;
    const src = e?.kind === 'json' ? e.model.source : e?.el?.jsonSource;
    if (!clone || !src) return null;
    const kindKey = e.kind === 'json' ? e.model.kindOf(clone) : e.el.jsonKind;
    const field = { rooms: 'type', openings: 'kind', objects: 'category' }[kindKey];
    if (!field) return null;
    const targets = asArray(src.doc[kindKey]).filter((t) => isActive(t) && keyMatches(key, kindKey, t, src.doc.drawing?.file));
    if (!targets.includes(clone._src)) targets.push(clone._src);
    for (const t of targets) {
      t._pendingCorrection = { key, from: t._pendingCorrection?.from ?? t[field], to: value };
      t[field] = value;
      if (kindKey === 'rooms') t.is_outdoor = OUTDOOR_TYPES.has(value);
    }
    const elementId = clone.id;
    this.bp.assemble();
    const rec = { key, value, elementId, at: new Date().toISOString(), kindKey };
    this.corrections = [...this.corrections.filter((c) => c.key !== key), rec];
    this.onCorrectionsChange(this.corrections);
    // reselect the same element (pickIds are reissued on assembly)
    const again = [...this.bp.pickIndex.entries()].find(([, v]) => (v.kind === 'json' ? v.meshes[0].userData.element.id === elementId && v.meshes[0].userData.kind !== 'wallBand' : v.el.json?.id === elementId));
    this.select(again?.[0] ?? null);
    this.onChange(this.bp.summary());
    return { record: rec, changed: targets.length };
  }
  correctionsFile() {
    const out = {};
    for (const c of this.corrections) out[c.key] = correctionValue(c.kindKey, c.key, c.value);
    return out;
  }

  // ---- picking ------------------------------------------------------------------------------------------
  bindPointer() {
    const el = this.renderer.domElement;
    this.listen(el, 'pointerdown', (e) => { this.downAt = { x: e.clientX, y: e.clientY }; });
    this.listen(el, 'pointermove', (e) => { if (e.buttons === 0) this.setHovered(this.pick(e)); });
    this.listen(el, 'pointerleave', () => this.setHovered(null));
    this.listen(el, 'pointerup', (e) => {
      if (!this.downAt) return;
      const moved = Math.hypot(e.clientX - this.downAt.x, e.clientY - this.downAt.y);
      this.downAt = null;
      if (moved >= 6) return;
      const list = this.pick(e, true);
      const i = list.indexOf(this.selected);
      this.select(i >= 0 && list.length > 1 ? list[(i + 1) % list.length] : (list[0] ?? null));
    });
  }
  pick(e, all = false) {
    const rect = this.renderer.domElement.getBoundingClientRect();
    const ndc = { x: ((e.clientX - rect.left) / rect.width) * 2 - 1, y: -((e.clientY - rect.top) / rect.height) * 2 + 1 };
    const list = this.bp.candidates(this.raycaster, this.camera, ndc, { width: rect.width, height: rect.height });
    return all ? list : (list[0] ?? null);
  }
  setHovered(id) {
    if (id === this.hovered) return;
    this.hovered = id;
    this.onHover?.(id);
    this.renderer.domElement.style.cursor = id ? 'pointer' : '';
    this.refreshTints();
  }
  select(id) {
    if (id && !this.bp.isShown(id)) id = null; // never select something hidden
    this.selected = id ?? null;
    this.refreshTints();
    this.onSelect(this.selected ? this.bp.describe(this.selected) : null);
  }
  refreshTints() {
    const p = currentPalette();
    for (const m of this.bp.models) for (const q of m.pickables) {
      const id = q.userData.pickId;
      if (id === this.selected) { q.material.emissive.set(p.select); q.material.emissiveIntensity = 0.55; }
      else if (id === this.hovered) { q.material.emissive.set(p.hover); q.material.emissiveIntensity = 0.35; }
      else { q.material.emissive.set(0x000000); q.material.emissiveIntensity = 1; }
    }
    this.bp.setOverlay('select', this.selected, p.select);
    this.bp.setOverlay('hover', this.hovered !== this.selected ? this.hovered : null, p.hover);
  }
}

// ---- picking (pure: also used by the Node checks) --------------------------------------------------------------

/** a piece can be picked only when it and every group above it are visible (a hidden layer is never selectable) */
const shown = (m) => { for (let o = m; o; o = o.parent) if (!o.visible) return false; return true; };

/**
 * Nearest visible hit under the pointer wins. Small pieces are hard to hit, so the pointer also probes a ring of rays
 * `radiusPx` around itself: when one of those (each taking its own nearest hit, so nothing hidden behind a wall) lands on
 * a SMALLER element (plan area) than the nearest hit, the smallest such element is picked instead.
 * Hits above an active cut height are ignored (that part is not drawn). Returns a pickId or null.
 */
export function pickAt(raycaster, camera, pickables, ndc, opts = {}) {
  return pickCandidates(raycaster, camera, pickables, ndc, opts)[0] ?? null;
}

/**
 * Same rule, but returns every element under the pointer: the picked one first, then elements whose surface the centre ray
 * meets at the same depth (within 1 mm: drawn on top of each other, duplicates in the file), then every element further
 * along the ray. Clicking again on the same spot cycles through them in that order.
 */
export function pickCandidates(raycaster, camera, pickables, ndc, { width, height, cut = null, radiusPx = 4 } = {}) {
  const cands = pickables.filter(shown);
  if (!cands.length) return [];
  const v = new THREE.Vector2();
  const visibleHits = (x, y) => {
    raycaster.setFromCamera(v.set(x, y), camera);
    return raycaster.intersectObjects(cands, false).filter((h) => cut == null || h.point.y <= cut + 1e-6).map(resolveHit).filter((h) => h.id != null);
  };
  // nearest hit of a ray; at equal depth (a small symbol drawn inside a larger one at the same height) the smaller wins
  const nearest = (hits) => hits.reduce((b, h) => (h.distance - hits[0].distance <= 1e-3 && h.size < b.size ? h : b), hits[0]);
  const centre = visibleHits(ndc.x, ndc.y);
  const h0 = centre[0];
  if (!h0) return [];
  let best = nearest(centre);
  if (width && height && radiusPx > 0) {
    for (let k = 0; k < 8; k++) {
      const t = (k * Math.PI) / 4;
      const hits = visibleHits(ndc.x + (Math.cos(t) * radiusPx * 2) / width, ndc.y + (Math.sin(t) * radiusPx * 2) / height);
      const h = hits.length ? nearest(hits) : null;
      if (h && h.size < best.size) best = h;
    }
  }
  // then: elements at the same depth (drawn on top of each other), then everything further along the ray, so repeated
  // clicks on one spot reach elements that are enclosed by others (a fitting inside a pipe, a floor under a fixture)
  const out = [best.id];
  for (const h of centre) if (h.distance - h0.distance <= 1e-3 && !out.includes(h.id)) out.push(h.id);
  for (const h of centre) if (!out.includes(h.id)) out.push(h.id);
  return out;
}

/** a ray hit -> { id, size }: a JSON piece carries them; a batched web-ifc mesh resolves the hit triangle to ONE element */
function resolveHit(h) {
  const u = h.object.userData;
  if (u.ifc) {
    const ei = h.object.geometry.attributes.elementIndex.getX(h.face.a);
    const el = u.ifc.model.elements[ei];
    return { ...h, id: el?.pickId ?? null, size: el?.size ?? Infinity, element: el };
  }
  return { ...h, id: u.pickId, size: u.size };
}

// ---- inspector content: values straight from the JSON ----------------------------------------------------

const fm = (v) => `${v.toFixed(2)} m`;
const cap = (s) => String(s).charAt(0).toUpperCase() + String(s).slice(1);
const num = (v) => (Number.isInteger(v) ? String(v) : String(Math.round(v * 1000) / 1000));
/** top-level fields that are not measurements of the element */
const NOT_SHOWN = new Set(['confidence']);

/**
 * every numeric field the element actually has (top level, then properties.*), never a null or a default. Schema v0.3:
 * each measured field says how it was obtained (explicit / calculated / inferred, with the method); an inferred value is
 * shown in the assumption colour. IFC quantities not bound to a field are listed with their unit.
 */
function numericRows(el) {
  const rows = [];
  const q = el.quantities ?? {};
  const how = (r) => (r ? ` — ${r.status.replace('_', ' ')}${r.method ? `: ${r.method}` : ''}` : '');
  for (const [k, v] of Object.entries(el)) if (typeof v === 'number' && Number.isFinite(v) && !NOT_SHOWN.has(k)) rows.push([k, `${num(v)}${how(q[k])}`, q[k]?.status === 'inferred' ? 'assumed' : null]);
  for (const [k, v] of Object.entries(el.properties ?? {})) if (typeof v === 'number' && Number.isFinite(v)) rows.push([`properties.${k}`, num(v)]);
  for (const [k, r] of Object.entries(q)) {
    if (k in el || r.value == null) continue;
    rows.push([`quantity ${k}`, `${num(r.value)} ${r.unit ?? ''}${r.raw && r.raw.unit && r.raw.value !== r.value ? ` (file: ${num(r.raw.value)} ${r.raw.unit})` : ''} — ${r.status.replace('_', ' ')}`.trim(), r.status === 'inferred' ? 'assumed' : null]);
  }
  return rows;
}

const v03Index = new WeakMap();
/** id -> element over the v0.3 collections of a document (cached per document) */
function v03ById(doc) {
  if (v03Index.has(doc)) return v03Index.get(doc);
  const m = new Map();
  for (const k of ['element_types', 'materials', 'material_layer_sets', 'systems', 'walls', 'objects', 'rooms', 'openings']) for (const e of asArray(doc[k])) m.set(e.id, e);
  v03Index.set(doc, m);
  return m;
}

/** schema v0.3 rows: class, type, mark, materials and layers, systems, finishes, the file's declared units. Only what the file holds. */
function v03Rows(el, doc) {
  if (!doc || doc.schema_version !== '0.3') return [];
  const rows = [];
  const push = (k, v, t = null) => { if (v !== null && v !== undefined && v !== '') rows.push([k, String(v), t]); };
  const by = v03ById(doc);
  push('ifc_class', el.ifc_class);
  push('GlobalId', el.global_id);
  const t = el.type_id ? by.get(el.type_id) : null;
  if (t) push('type', `${[t.name, t.predefined_type, t.ifc_class].filter(Boolean).join(' · ')} (${asArray(t.instance_ids).length} instances)`);
  push('mark', el.mark);
  push('operation', el.operation);
  const set = el.material_layer_set_id ? by.get(el.material_layer_set_id) : null;
  if (set) {
    const parts = asArray(set.layers).map((l) => `${by.get(l.material_id)?.name ?? 'unnamed'}${Number.isFinite(l.thickness_m) ? ` ${num(l.thickness_m)} m` : ''}`);
    push(set.kind === 'layer_set' ? 'material layers' : `materials (${String(set.kind).replace('_', ' ')})`, `${parts.join(' + ')}${Number.isFinite(set.total_thickness_m) ? ` = ${num(set.total_thickness_m)} m` : ''}`);
  } else if (asArray(el.material_ids).length) push('materials', el.material_ids.map((m) => by.get(m)?.name ?? m).join(', '));
  if (asArray(el.system_ids).length) push('systems', el.system_ids.map((sid) => { const sy = by.get(sid); return sy ? `${sy.name ?? sid} (${sy.kind})` : sid; }).join(', '));
  for (const [k, f] of Object.entries(el.finishes ?? {})) if (f) push(`finish ${k}`, f.status === 'not_available' ? 'not in the file' : `${f.value} (${f.source})`);
  if (asArray(el.boundary_element_ids).length) push('bounded by', `${el.boundary_element_ids.length} elements (space boundaries)`);
  const u = doc.project?.units;
  if (u) push('file units', ['length', 'area', 'volume'].map((k) => `${k} ${u[k]?.unit ?? 'not declared'}${u[k]?.status && u[k].status !== 'explicit' ? ` (${u[k].status.replace('_', ' ')})` : ''}`).join(', '), ['length', 'area', 'volume'].some((k) => u[k]?.status === 'inferred') ? 'assumed' : null);
  return rows;
}

/**
 * Inspector content. File values are shown as they are; viewer conventions are separate rows marked "(assumed)".
 * @returns {{ title:string, rows:Array<[string,string]>, confidence:number|null, flags:string[], notes:string[],
 *   partOf: null | { label:string, pickId:number }, correct: null | { field:string, options:string[], current:string, scopes:Array<{key:string,label:string}>, pending:any } }}
 */
/** rows of an element's provenance (always shown) */
function provenanceRows(el) {
  const p = el.provenance ?? {}, rows = [];
  if (p.extractor) rows.push(['provenance.extractor', String(p.extractor)]);
  if (p.source_ref) rows.push(['provenance.source_ref', String(p.source_ref)]);
  if (p.layer) rows.push(['provenance.layer', String(p.layer)]);
  if (p.details?.rule) rows.push(['provenance.rule', String(p.details.rule)]);
  return rows;
}

/**
 * numeric pset values. A value whose unit the pset check VERIFIED is shown converted (and named); every other pset number
 * is labelled raw: IFC psets are copied as they are, and their units are not consistent.
 */
function psetRows(psets, unitCheck = []) {
  const rows = [];
  for (const [pset, props] of Object.entries(psets ?? {})) for (const [key, v] of Object.entries(props ?? {})) {
    if (typeof v !== 'number' || !Number.isFinite(v)) continue;
    // converted only when THIS pset.key is the one whose unit was verified against geometry
    const r = unitCheck.find((x) => x.verdict === 'verified' && x.pset === pset && x.psetKey === key);
    rows.push(r ? [`${pset}.${key}`, `${num(v * r.toMetres)} m (file: ${num(v)} ${r.unit}, unit verified against geometry)`] : [`${pset}.${key}`, `${num(v)} (raw: unit not verified)`, 'raw']);
  }
  return rows;
}

function describe(u, kindKey, file, twins = [], unitCheck = [], doc = null) {
  const el = u.element;
  const rows = [];
  const notes = [];
  let title, partOf = null, correct = null;
  const confidence = Number.isFinite(el.confidence) ? el.confidence : null;
  const flags = (el.flags ?? []).map((f) => FLAG_TEXT[f] ?? f.replace(/_/g, ' '));
  // a row whose label says "(assumed)" is a convention, not file data: the page shows it in the assumption colour
  const push = (k, v) => { if (v !== null && v !== undefined && v !== '') rows.push([k, String(v), /\(assumed\)/.test(k) ? 'assumed' : null]); };
  const layer = () => push('layer', el.provenance?.layer ?? (el.properties?.ifc_type ? `${el.properties.ifc_type} (IFC)` : null));

  if (u.kind === 'wallBand') {
    // wall material above/below an opening: a WALL piece; the opening is a separate, clickable "part of" row
    title = u.band === 'below' ? 'Wall below window' : el.kind === 'window' ? 'Wall above window' : 'Wall above door';
    push('kind', 'wall');
    push('height (assumed)', `${u.height.from.toFixed(2)}–${u.height.to.toFixed(2)} m`);
    if (Number.isFinite(el.width_m)) push('length (opening width_m)', num(el.width_m));
    layer();
    partOf = { label: `${el.kind ?? 'opening'} ${el.id}`, pickId: u.partOf.pickId };
    return { title, rows, confidence: null, flags: [], notes: ['wall band over the opening footprint, not an element of the file; its heights are viewer assumptions'], partOf, correct };
  }

  push('id', el.id);
  if (u.kind === 'room') {
    title = el.label ?? el.name ?? 'Room';
    push('kind', 'room'); push('type', el.type); push('name', el.label ?? el.name); push('number', el.number);
    // a floor has no height: the slab is a drawing convention
    notes.push('floor drawn 20 mm below the walls and 10 mm inset: room outlines and wall faces come from different layers');
  } else if (u.kind === 'wall') {
    title = 'Wall';
    push('kind', 'wall');
  } else if (u.kind === 'opening') {
    title = cap(el.kind ?? 'opening');
    push('kind', el.kind); push('subtype', el.subtype);
  } else if (el.category === 'stair') {
    title = 'Stair';
    push('kind', 'object'); push('category', 'stair');
  } else {
    title = cap(el.category ?? 'object');
    push('kind', 'object'); push('category', [el.category, el.subtype].filter(Boolean).join(' / '));
    push('source_block', el.properties?.source_block); push('name', el.properties?.name ?? el.properties?.ifc_name);
  }
  rows.push(...numericRows(el));
  layer();
  if (el.properties?.ifc_type && !el.ifc_class) { push('ifc_type', el.properties.ifc_type); push('GlobalId', el.provenance?.source_ref); }
  rows.push(...v03Rows(el, doc));
  rows.push(...provenanceRows(el));
  if (el.properties?.psets) rows.push(...psetRows(el.properties.psets, unitCheck));

  // viewer conventions, always labelled
  if (u.kind === 'wall') push(`height${u.height.measured ? '' : ' (assumed)'}`, fm(u.height.value));
  else if (u.kind === 'opening' && el.kind === 'window') push('sill / head (assumed)', `${fm(u.height.sill)} / ${fm(u.height.head)}`);
  else if (u.kind === 'opening') {
    push('head (assumed)', fm(u.height.head));
    if (u.leaf) push('leaf swing (assumed)', `${u.leaf.swingDeg}° open, ${Math.round(u.leaf.thickness * 1000)} mm thick`);
    if (u.leaf) notes.push('the leaf angle and hinge side are a drawing convention, not data');
    if (el.kind === 'doorway') notes.push('doorway without a leaf: no leaf is drawn');
  } else if (el.category === 'stair') {
    const p = el.properties ?? {}, st = u.stair;
    if (Array.isArray(p.flights)) p.flights.forEach((f, i) => push(`flight ${i + 1}`, [f.tread_count != null && `${f.tread_count} treads`, Number.isFinite(f.direction_deg) && `${f.direction_deg}°`, Number.isFinite(f.going_m) && `going ${f.going_m} m`, Number.isFinite(f.width_m) && `width ${f.width_m} m`].filter(Boolean).join(', ')));
    if (Array.isArray(p.landings)) p.landings.forEach((l, i) => push(`landing ${i + 1}`, `after tread ${l.after_tread}${l.side === 'assumed' ? ' (side assumed)' : ''}`));
    push('rise_direction', p.rise_direction);
    push(`steps drawn (${st.floorHeight.measured ? 'level height' : 'assumed height'})`, st.n ? `${st.n} × riser ${st.riser.toFixed(3)} m${st.landings ? ` + ${st.landings} landing(s)` : ''}` : 'none (footprint has no usable axis): drawn as a block');
    notes.push(FLAG_TEXT.generated_geometry);
    if (st.from === 'footprint') notes.push('no tread data in the file: long axis of the footprint, 0.17 m risers');
    if (p.rise_direction !== 'up') notes.push('which way is up is not known from the drawing; steps rise in flight order');
  } else if (u.height) push('height (assumed)', fm(u.height.value));
  if (el.provenance?.extractor === 'human') notes.push(`corrected by a human (${el.provenance.details?.rule ?? 'corrections.json'})`);
  if (twins?.length) notes.push(`same footprint as ${twins.join(', ')} (possibly a duplicate in the drawing): click again to cycle`);

  const field = { rooms: 'type', openings: 'kind', objects: 'category' }[kindKey];
  if (field) {
    const options = { rooms: ROOM_TYPES, openings: OPENING_KINDS, objects: OBJECT_CATEGORIES }[kindKey];
    correct = { field, options: options.includes(el[field]) ? options : [el[field], ...options], current: el[field], scopes: correctionScopes(kindKey, el, file), pending: el._pendingCorrection ?? null };
  }
  if (el.properties?.ifc_type) notes.push('geometry extruded from the 2D footprint (load the .ifc for the real solid): heights are conventions');
  return { title, rows, confidence, flags, notes, partOf, correct };
}

/**
 * Inspector content for a web-ifc element. Its geometry is REAL: nothing vertical is assumed. The meaning (confidence,
 * flags, provenance, type) comes from the matched drawing-JSON element when one is loaded.
 */
function describeIfc(el, src, unitCheck = []) {
  const j = el.json;
  const rows = [];
  const push = (k, v) => { if (v !== null && v !== undefined && v !== '') rows.push([k, String(v)]); };
  const title = j ? (j.label ?? j.name ?? cap(j.category ?? j.kind ?? j.type ?? el.ifcType)) : cap(el.name ?? el.ifcType);
  if (j) { push('id', j.id); push('kind', el.jsonKind?.replace(/s$/, '')); push('type', j.type); push('category', j.category); push('opening kind', j.kind); }
  push('ifc_type', el.ifcType); push('GlobalId', el.globalId); push('expressID', el.expressID); push('name', el.name); push('object type', el.objectType);
  push('measured size (x × y × z)', `${num(el.dims.x)} × ${num(el.dims.z)} × ${num(el.dims.y)} m (from the real geometry)`);
  if (j) { rows.push(...numericRows(j)); rows.push(...v03Rows(j, el.jsonSource?.doc).filter(([k]) => k !== 'ifc_class' && k !== 'GlobalId')); rows.push(...provenanceRows(j)); if (j.properties?.psets) rows.push(...psetRows(j.properties.psets, unitCheck)); }
  const notes = ['geometry: real 3D solid read with web-ifc; nothing vertical is assumed'];
  if (!j) notes.push('not in any loaded drawing JSON: no confidence, flags or coverage for it (load the matching JSON to add its meaning)');
  return {
    title, rows, notes, partOf: null, correct: null, source: src.name, geometry: 'web-ifc (real 3D)',
    confidence: j && Number.isFinite(j.confidence) ? j.confidence : null,
    flags: (j?.flags ?? []).map((f) => FLAG_TEXT[f] ?? f.replace(/_/g, ' ')),
  };
}
