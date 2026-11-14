/**
 * ifcGeometry.js — real 3D geometry of an IFC file, read with web-ifc (browser or Node: the caller passes the API).
 *
 * Frame. web-ifc already returns Y-UP geometry in metres: IFC (x, y_north, z_up) -> (x, z_up, -y_north). The scene uses
 * exactly that frame, so NOTHING is rotated (a -90 deg rotation about X would lay the building on its side). Model
 * coordinates can be huge (georeferenced sites: 538 km east, 6 591 km north), so every vertex is shifted by the scene
 * origin in double precision BEFORE it is stored as float32. Vertically each vertex is stored relative to its storey's
 * world height, so the storey can be placed at its elevation (and separated) like any JSON level.
 *
 * Batching. A clinic level is ~2 200 elements, a model ~4 000+. Geometry is merged into one mesh per
 * (storey, group, fixture category, colour) with an `elementIndex` vertex attribute, and every element keeps its index
 * ranges, so picking and highlighting still resolve to ONE element.
 */

import * as THREE from 'three';

/** viewer group of an IFC element (fixture category decides sinks/toilets among flow terminals) */
export function ifcGroupOf(typeName, category) {
  const T = String(typeName).toUpperCase();
  if (/^IFC(WALL|CURTAINWALL)/.test(T)) return 'walls';
  if (/^IFCDOOR/.test(T)) return 'doors';
  if (/^IFCWINDOW/.test(T)) return 'windows';
  if (/^IFC(STAIR|RAMP)/.test(T)) return 'stairs';
  if (/^IFC(SANITARYTERMINAL|FURNISHINGELEMENT|FURNITURE|SYSTEMFURNITUREELEMENT|ELECTRICAPPLIANCE)/.test(T)) return 'fixtures';
  if (/^IFCFLOWTERMINAL/.test(T) && category && category !== 'other') return 'fixtures';
  if (/^IFC(FLOW|PIPE|DUCT|CABLE|DISTRIBUTION|ENERGYCONVERSION|VALVE|PUMP|TANK|FAN|BOILER|CHILLER|AIRTERMINAL|LIGHTFIXTURE|OUTLET|SWITCHINGDEVICE|ALARM|SENSOR|FIRESUPPRESSION|UNITARYEQUIPMENT|ELECTRIC)/.test(T)) return 'services';
  return 'structure'; // slabs, roofs, coverings, columns, beams, members, plates, footings, railings, proxies
}

/** fixture category from an element's name / object type (same words as the pipeline) */
const CATEGORY_RULES = [
  ['toilet', /toilet|\bwc\b|water.?closet|klosett|urinal/i], ['bathtub', /bath.?tub|badewanne|baignoire|\btub\b/i],
  ['shower', /shower|dusche|douche/i], ['sink', /sink|basin|lavatory|lavabo|waschtisch|évier/i], ['stove', /stove|cooker|range|herd/i],
  ['fridge', /fridge|refrigerator|kühlschrank/i], ['washing_machine', /washing.?machine|washer|waschmaschine/i],
  ['radiator', /radiator|heizkörper/i], ['elevator', /elevator|\blift\b|aufzug/i], ['closet', /wardrobe|closet|cupboard|schrank/i],
];
export function ifcCategoryOf(typeName, name, objectType) {
  const T = String(typeName).toUpperCase();
  if (/^IFC(STAIR)/.test(T)) return 'stair';
  if (!/^IFC(FLOWTERMINAL|SANITARYTERMINAL|FURNISHINGELEMENT|FURNITURE|ELECTRICAPPLIANCE|TRANSPORTELEMENT|SPACEHEATER)/.test(T)) return null;
  const hay = `${name ?? ''} ${objectType ?? ''}`;
  for (const [cat, re] of CATEGORY_RULES) if (re.test(hay)) return cat;
  if (/^IFCTRANSPORTELEMENT/.test(T)) return 'elevator';
  if (/^IFCSPACEHEATER/.test(T)) return 'radiator';
  return 'other';
}

/** float32 array that grows by doubling */
class Grow {
  constructor(Kind, n = 1 << 14) { this.K = Kind; this.a = new Kind(n); this.n = 0; }
  need(k) { if (this.n + k <= this.a.length) return; let m = this.a.length * 2; while (m < this.n + k) m *= 2; const b = new this.K(m); b.set(this.a.subarray(0, this.n)); this.a = b; }
  done() { return this.a.slice(0, this.n); }
}

const val = (x) => (x && typeof x === 'object' && 'value' in x ? x.value : x);

/** IFC length unit -> metres (placements are in model units; web-ifc meshes are already metres) */
function lengthUnit(api, W, id) {
  const ids = api.GetLineIDsWithType(id, W.IFCSIUNIT);
  const PREFIX = { MILLI: 1e-3, CENTI: 1e-2, DECI: 1e-1, KILO: 1e3 };
  for (let i = 0; i < ids.size(); i++) {
    const u = api.GetLine(id, ids.get(i));
    if (val(u.UnitType) === 'LENGTHUNIT' && val(u.Name) === 'METRE') return PREFIX[val(u.Prefix)] ?? 1;
  }
  const conv = api.GetLineIDsWithType(id, W.IFCCONVERSIONBASEDUNIT);
  for (let i = 0; i < conv.size(); i++) {
    const u = api.GetLine(id, conv.get(i));
    if (val(u.UnitType) === 'LENGTHUNIT' && /FOOT/i.test(val(u.Name))) return 0.3048;
    if (val(u.UnitType) === 'LENGTHUNIT' && /INCH/i.test(val(u.Name))) return 0.0254;
  }
  return 1;
}

/** world z of a placement (sum of the relative placement chain), in model units */
function placementZ(api, id, ref) {
  let z = 0;
  for (let k = 0; ref && k < 20; k++) {
    const p = api.GetLine(id, val(ref));
    const rel = p?.RelativePlacement ? api.GetLine(id, val(p.RelativePlacement)) : null;
    const loc = rel?.Location ? api.GetLine(id, val(rel.Location)) : null;
    z += Number(val(loc?.Coordinates?.[2])) || 0;
    ref = p?.PlacementRelTo;
  }
  return z;
}

/**
 * Open and stream one IFC file.
 * @param {any} api initialised web-ifc IfcAPI
 * @param {any} W the web-ifc module namespace (IFC type codes)
 * @param {Uint8Array} bytes
 * @param {{ origin?: {x:number, y:number} | null, lift?: Record<string, number> }} [o] origin = scene origin in IFC world metres (x east, y north)
 */
/**
 * web-ifc prints one "[WEB-IFC][error] ... unexpected mesh type" line per representation it cannot mesh (12 000+ on some
 * models). Emscripten binds its print functions ONCE, when the module is instantiated (inside api.Init()), so the filter
 * must be installed before Init: those lines are then counted (per model, reported in the panel and as one warning)
 * instead of flooding the console. Everything else passes through unchanged.
 */
export const webIfcLog = { count: 0, samples: [] };
export function captureWebIfcLog() {
  const saved = { log: console.log, warn: console.warn, error: console.error };
  const wrap = (orig) => function (...a) {
    const t = String(a[0] ?? '');
    if (t.startsWith('[WEB-IFC]')) { webIfcLog.count++; if (webIfcLog.samples.length < 3) webIfcLog.samples.push(t); return undefined; }
    return orig.apply(console, a);
  };
  console.log = wrap(saved.log); console.warn = wrap(saved.warn); console.error = wrap(saved.error);
  return () => Object.assign(console, saved); // call after Init: the module keeps the wrappers it bound
}

export function loadIfcModel(api, W, bytes, o = {}) {
  const t0 = Date.now();
  const before = webIfcLog.count;
  webIfcLog.samples = [];
  const res = loadIfcModelInner(api, W, bytes, o, t0);
  res.stats.unsupported = webIfcLog.count - before;
  res.stats.unsupportedSamples = [...webIfcLog.samples];
  return res;
}

function loadIfcModelInner(api, W, bytes, o, t0) {
  const modelID = api.OpenModel(bytes, { COORDINATE_TO_ORIGIN: false });
  if (modelID < 0) throw new Error('web-ifc could not open the file');
  try {
  const lenM = lengthUnit(api, W, modelID);

  // spatial structure: element -> container (contained-in + aggregates), up to a storey
  const parent = new Map();
  const rels = (type, from, to) => {
    const ids = api.GetLineIDsWithType(modelID, type);
    for (let i = 0; i < ids.size(); i++) {
      const r = api.GetLine(modelID, ids.get(i));
      const p = val(r[to]);
      for (const c of r[from] ?? []) parent.set(val(c), p);
    }
  };
  rels(W.IFCRELCONTAINEDINSPATIALSTRUCTURE, 'RelatedElements', 'RelatingStructure');
  rels(W.IFCRELAGGREGATES, 'RelatedObjects', 'RelatingObject');
  const storeyIds = api.GetLineIDsWithType(modelID, W.IFCBUILDINGSTOREY);
  const storeys = new Map();
  for (let i = 0; i < storeyIds.size(); i++) {
    const sid = storeyIds.get(i), s = api.GetLine(modelID, sid);
    const elev = Number(val(s.Elevation));
    storeys.set(sid, { expressID: sid, name: String(val(s.Name) ?? val(s.LongName) ?? `storey ${sid}`), elevation: Number.isFinite(elev) ? elev * lenM : null, worldZ: placementZ(api, modelID, s.ObjectPlacement) * lenM, globalId: val(s.GlobalId) });
  }
  // vertical datum: storey world z minus its elevation (site / project base height, e.g. +14.3 m), used for elements without a storey
  const datum = [...storeys.values()].map((s) => s.worldZ - (s.elevation ?? s.worldZ)).sort((a, b) => a - b)[0] ?? 0;
  const storeyOf = (eid) => { let c = eid; for (let k = 0; k < 16 && c != null; k++) { if (storeys.has(c)) return c; c = parent.get(c); } return null; };

  const typeName = new Map();
  const nameOfType = (code) => typeName.get(code) ?? typeName.set(code, String(api.GetNameFromTypeCode(code) ?? `type ${code}`).toUpperCase()).get(code);

  let origin = o.origin ?? null; // fixed once: first vertex seen when the scene has no origin yet
  const elements = [];
  const batches = new Map();
  let triangles = 0, geometries = 0;
  const batchOf = (key, info) => batches.get(key) ?? batches.set(key, { key, ...info, pos: new Grow(Float32Array), nrm: new Grow(Float32Array), elem: new Grow(Float32Array), idx: new Grow(Uint32Array), verts: 0 }).get(key);

  api.StreamAllMeshes(modelID, (mesh) => {
    const eid = mesh.expressID;
    const line = api.GetLine(modelID, eid);
    const type = nameOfType(api.GetLineType(modelID, eid));
    const name = val(line?.Name) ?? null, objectType = val(line?.ObjectType) ?? null;
    const category = ifcCategoryOf(type, name, objectType);
    const group = ifcGroupOf(type, category);
    const sid = storeyOf(eid);
    const zBase = sid != null ? storeys.get(sid).worldZ : datum;
    const el = { index: elements.length, expressID: eid, globalId: val(line?.GlobalId) ?? null, ifcType: type, name, objectType, storeyId: sid, group, category, ranges: [], bbox: null };
    const bb = [Infinity, Infinity, Infinity, -Infinity, -Infinity, -Infinity];
    for (let gi = 0; gi < mesh.geometries.size(); gi++) {
      const pg = mesh.geometries.get(gi);
      const g = api.GetGeometry(modelID, pg.geometryExpressID);
      const v = api.GetVertexArray(g.GetVertexData(), g.GetVertexDataSize());
      const ix = api.GetIndexArray(g.GetIndexData(), g.GetIndexDataSize());
      g.delete(); // copied out: free the WASM-side geometry now (a 4 000-element model leaks otherwise)
      if (!v.length || !ix.length) continue;
      const m = pg.flatTransformation, c = pg.color;
      if (!origin) origin = { x: m[0] * v[0] + m[4] * v[1] + m[8] * v[2] + m[12], y: -(m[2] * v[0] + m[6] * v[1] + m[10] * v[2] + m[14]) };
      const rgba = [c.x, c.y, c.z, c.w].map((q) => Math.round(q * 255));
      const key = `${sid ?? 'none'}|${group}|${group === 'fixtures' ? category : ''}|${rgba.join(',')}`;
      const b = batchOf(key, { storeyId: sid, group, category: group === 'fixtures' ? category : null, rgba });
      const nv = v.length / 6;
      b.pos.need(nv * 3); b.nrm.need(nv * 3); b.elem.need(nv); b.idx.need(ix.length);
      const P = b.pos.a, N = b.nrm.a, E = b.elem.a;
      for (let k = 0, j = b.pos.n; k < v.length; k += 6, j += 3) {
        const x = v[k], y = v[k + 1], z = v[k + 2], nx = v[k + 3], ny = v[k + 4], nz = v[k + 5];
        const wx = m[0] * x + m[4] * y + m[8] * z + m[12], wy = m[1] * x + m[5] * y + m[9] * z + m[13], wz = m[2] * x + m[6] * y + m[10] * z + m[14];
        if (wx < bb[0]) bb[0] = wx; if (wy < bb[1]) bb[1] = wy; if (wz < bb[2]) bb[2] = wz;
        if (wx > bb[3]) bb[3] = wx; if (wy > bb[4]) bb[4] = wy; if (wz > bb[5]) bb[5] = wz;
        // scene: X = east - origin, Y = up relative to the storey, Z = -(north - origin) = wz + origin.y (double precision first)
        P[j] = wx - origin.x; P[j + 1] = wy - zBase; P[j + 2] = wz + origin.y;
        const ux = m[0] * nx + m[4] * ny + m[8] * nz, uy = m[1] * nx + m[5] * ny + m[9] * nz, uz = m[2] * nx + m[6] * ny + m[10] * nz;
        const l = Math.hypot(ux, uy, uz) || 1;
        N[j] = ux / l; N[j + 1] = uy / l; N[j + 2] = uz / l;
      }
      E.fill(el.index, b.elem.n, b.elem.n + nv);
      const I = b.idx.a, base = b.verts;
      for (let k = 0; k < ix.length; k++) I[b.idx.n + k] = base + ix[k];
      el.ranges.push({ key, start: b.idx.n, count: ix.length });
      b.pos.n += nv * 3; b.nrm.n += nv * 3; b.elem.n += nv; b.idx.n += ix.length; b.verts += nv;
      triangles += ix.length / 3; geometries++;
    }
    if (!el.ranges.length) return;
    // bbox in scene frame (relative to the storey for y), plan size for the picking rule
    el.bbox = { min: [bb[0] - origin.x, bb[1] - zBase, bb[2] + origin.y], max: [bb[3] - origin.x, bb[4] - zBase, bb[5] + origin.y] };
    el.size = Math.max(1e-4, (bb[3] - bb[0]) * (bb[5] - bb[2]));
    el.dims = { x: bb[3] - bb[0], y: bb[4] - bb[1], z: bb[5] - bb[2] }; // metres, measured from the real geometry
    elements.push(el);
  });

  return {
    modelID, lenM, origin, datum, elements, storeys: [...storeys.values()],
    batches: [...batches.values()].map((b) => ({ key: b.key, storeyId: b.storeyId, group: b.group, category: b.category, rgba: b.rgba, position: b.pos.done(), normal: b.nrm.done(), elementIndex: b.elem.done(), index: b.idx.done() })),
    stats: { elements: elements.length, geometries, triangles, batches: batches.size, ms: Date.now() - t0, unsupported: 0, unsupportedSamples: [] },
  };
  } catch (error) { api.CloseModel(modelID); throw error; }
}

/**
 * THREE meshes for a loaded model. Each mesh carries userData.ifc = { model, batch } and an `elementIndex` attribute.
 * Materials are shared per batch (one draw call each); highlighting uses overlay meshes built from an element's ranges.
 * `lift` gives the pull-apart lift per group; explode vectors are stored per vertex (CPU side) so picking stays exact.
 */
/**
 * Colour per group for elements the file gives NO style: web-ifc then returns pure white (1, 1, 1, 1), which on a light
 * background makes a whole plumbing model disappear. Real IFC colours are always kept.
 */
export const DEFAULT_GROUP_COLOR = Object.freeze({ services: '#5b84b1', fixtures: '#e8e6e1', walls: '#e8e5df', doors: '#9a7b5f', windows: '#8fc1e3', stairs: '#b9b1a3', structure: '#b8b4ac', floors: '#d6d2ca' });

export function ifcMeshes(model, { lift = {}, centre = { x: 0, z: 0 }, categoryColor = {} } = {}) {
  const meshes = [];
  const SPREAD = 1.35, PUSH = 1.1;
  for (const b of model.batches) {
    const g = new THREE.BufferGeometry();
    g.setAttribute('position', new THREE.BufferAttribute(b.position, 3));
    g.setAttribute('normal', new THREE.BufferAttribute(b.normal, 3));
    g.setAttribute('elementIndex', new THREE.BufferAttribute(b.elementIndex, 1));
    g.setIndex(new THREE.BufferAttribute(b.index, 1));
    g.computeBoundingBox(); g.computeBoundingSphere();
    const [r, gg, bl, a] = b.rgba;
    const unstyled = r === 255 && gg === 255 && bl === 255 && a === 255;
    const color = unstyled ? new THREE.Color(categoryColor[b.category] ?? DEFAULT_GROUP_COLOR[b.group] ?? '#b8b4ac') : new THREE.Color().setRGB(r / 255, gg / 255, bl / 255, THREE.SRGBColorSpace);
    const mat = new THREE.MeshStandardMaterial({ color, roughness: 0.75, metalness: 0.05, side: THREE.DoubleSide, transparent: a < 255, opacity: a / 255, depthWrite: a >= 255 });
    mat.userData.ifc = true;
    const mesh = new THREE.Mesh(g, mat);
    mesh.castShadow = a >= 255; mesh.receiveShadow = true;
    // pull-apart vector per vertex: dx = cx*1.35 + dir.x*1.1 (per element), dy = lift*1.6
    const base = b.position.slice(), ev = new Float32Array(b.position.length);
    const L = (lift[b.group] ?? 0.6) * 1.6;
    for (let i = 0, n = b.elementIndex.length; i < n; i++) {
      const el = model.elements[b.elementIndex[i]];
      if (!el.explode) {
        const cx = (el.bbox.min[0] + el.bbox.max[0]) / 2 - centre.x, cz = (el.bbox.min[2] + el.bbox.max[2]) / 2 - centre.z;
        const l = Math.hypot(cx, cz) || 1;
        el.explode = [cx * SPREAD + (cx / l) * PUSH, cz * SPREAD + (cz / l) * PUSH];
      }
      ev[i * 3] = el.explode[0]; ev[i * 3 + 1] = L; ev[i * 3 + 2] = el.explode[1];
    }
    mesh.userData = { ifc: { model, batch: b }, group: b.group, category: b.category, storeyId: b.storeyId, explodeBase: base, explodeVec: ev, explodeAt: 0, colour: unstyled ? 'viewer default (the file gives no style)' : 'from the file' };
    meshes.push(mesh);
  }
  return meshes;
}

/** move IFC vertices for the pull-apart value e (positions only; the BVH is refit so picking stays exact) */
export function explodeIfcMesh(mesh, e) {
  const u = mesh.userData;
  if (u.explodeAt === e) return;
  const p = mesh.geometry.attributes.position.array, b = u.explodeBase, v = u.explodeVec;
  for (let i = 0; i < p.length; i++) p[i] = b[i] + e * v[i];
  mesh.geometry.attributes.position.needsUpdate = true;
  mesh.geometry.computeBoundingBox(); mesh.geometry.computeBoundingSphere();
  mesh.geometry.boundsTree?.refit?.();
  u.explodeAt = e;
}

/** triangles of one element (current positions), as a standalone geometry for highlight overlays */
export function elementGeometry(model, el, meshByKey) {
  const parts = [];
  for (const r of el.ranges) {
    const mesh = meshByKey.get(r.key);
    if (!mesh) continue;
    const P = mesh.geometry.attributes.position.array, I = mesh.geometry.index.array;
    const out = new Float32Array(r.count * 3);
    for (let k = 0; k < r.count; k++) { const vi = I[r.start + k] * 3; out[k * 3] = P[vi]; out[k * 3 + 1] = P[vi + 1]; out[k * 3 + 2] = P[vi + 2]; }
    parts.push({ out, mesh });
  }
  if (!parts.length) return null;
  const all = new Float32Array(parts.reduce((s, q) => s + q.out.length, 0));
  let o = 0; for (const q of parts) { all.set(q.out, o); o += q.out.length; }
  const g = new THREE.BufferGeometry();
  g.setAttribute('position', new THREE.BufferAttribute(all, 3));
  g.computeVertexNormals();
  return { geometry: g, mesh: parts[0].mesh };
}
