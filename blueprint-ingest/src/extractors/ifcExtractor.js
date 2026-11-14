/**
 * IFC (BIM) extractor.
 *
 * What it keeps (token-optimised): the Project/Storey/Space hierarchy, building elements with GlobalIds, names, types,
 * footprints, thickness/centerlines, door/window sizes and heights, selected property-set values, materials.
 * What it drops: all tessellated mesh data. Footprints come from IfcExtrudedAreaSolid profiles / FootPrint curves;
 * only if neither exists is `web-ifc` (optional) used to read a bounding box.
 *
 * One bundle is produced per building storey, because the schema is "one file per drawing page".
 */

import fs from 'node:fs/promises';
import { parseStepFile, unwrap, isRef } from './ifc/stepParser.js';
import { emptyBundle, makeCanvas } from './bundle.js';
import { polygonArea, polygonCenterline, minAreaRect, cleanRing, bboxOfPoints, round, circlePolygon } from '../geometry/geom.js';

// ---- IFC type tables ---------------------------------------------------------------------------------

/** element type -> [bucket, category] */
const ELEMENTS = {
  IFCWALL: ['wall'], IFCWALLSTANDARDCASE: ['wall'], IFCWALLELEMENTEDCASE: ['wall'], IFCCURTAINWALL: ['wall'],
  IFCDOOR: ['door'], IFCDOORSTANDARDCASE: ['door'], IFCWINDOW: ['window'], IFCWINDOWSTANDARDCASE: ['window'],
  IFCSPACE: ['space'],
  IFCSTAIR: ['object', 'stair'], IFCSTAIRFLIGHT: ['object', 'stair'], IFCRAMP: ['object', 'ramp'], IFCRAMPFLIGHT: ['object', 'ramp'],
  IFCCOLUMN: ['object', 'column'], IFCCOLUMNSTANDARDCASE: ['object', 'column'], IFCBEAM: ['object', 'beam'], IFCBEAMSTANDARDCASE: ['object', 'beam'],
  IFCRAILING: ['object', 'railing'], IFCFURNISHINGELEMENT: ['object', 'furniture'], IFCFURNITURE: ['object', 'furniture'], IFCSYSTEMFURNITUREELEMENT: ['object', 'furniture'],
  IFCSANITARYTERMINAL: ['object', 'sanitary_terminal'], IFCFLOWTERMINAL: ['object', 'flow_terminal'], IFCLIGHTFIXTURE: ['object', 'light_fixture'],
  IFCFLOWSEGMENT: ['object', 'flow_segment'], IFCFLOWFITTING: ['object', 'flow_fitting'], IFCDUCTSEGMENT: ['object', 'duct_segment'], IFCPIPESEGMENT: ['object', 'pipe_segment'],
  IFCCABLECARRIERSEGMENT: ['object', 'cable_carrier'], IFCELECTRICAPPLIANCE: ['object', 'electric_appliance'], IFCELECTRICDISTRIBUTIONBOARD: ['object', 'distribution_board'],
  IFCDISTRIBUTIONELEMENT: ['object', 'distribution_element'], IFCDISTRIBUTIONFLOWELEMENT: ['object', 'distribution_element'], IFCBUILDINGELEMENTPROXY: ['object', 'element'],
  IFCFIREFIGHTING: ['object', 'fire_equipment'], IFCALARM: ['object', 'alarm'], IFCSENSOR: ['object', 'sensor'],
};
/** counted in stats but not emitted: they cover whole floors/roofs and would swamp a plan */
const COUNT_ONLY = new Set(['IFCSLAB', 'IFCROOF', 'IFCCOVERING', 'IFCFOOTING', 'IFCPLATE', 'IFCMEMBER']);

const STRUCTURE = ['IFCPROJECT', 'IFCSITE', 'IFCBUILDING', 'IFCBUILDINGSTOREY', 'IFCRELAGGREGATES', 'IFCRELCONTAINEDINSPATIALSTRUCTURE',
  'IFCRELVOIDSELEMENT', 'IFCRELFILLSELEMENT', 'IFCOPENINGELEMENT', 'IFCRELDEFINESBYPROPERTIES', 'IFCRELASSOCIATESMATERIAL'];
const PROPS = ['IFCPROPERTYSET', 'IFCPROPERTYSINGLEVALUE', 'IFCPROPERTYENUMERATEDVALUE', 'IFCELEMENTQUANTITY', 'IFCQUANTITYLENGTH', 'IFCQUANTITYAREA', 'IFCQUANTITYVOLUME', 'IFCQUANTITYCOUNT',
  'IFCMATERIAL', 'IFCMATERIALLAYERSET', 'IFCMATERIALLAYERSETUSAGE', 'IFCMATERIALLAYER', 'IFCMATERIALCONSTITUENTSET', 'IFCMATERIALCONSTITUENT'];
const GEOM = ['IFCLOCALPLACEMENT', 'IFCAXIS2PLACEMENT3D', 'IFCAXIS2PLACEMENT2D', 'IFCDIRECTION', 'IFCPRODUCTDEFINITIONSHAPE', 'IFCSHAPEREPRESENTATION', 'IFCEXTRUDEDAREASOLID',
  'IFCRECTANGLEPROFILEDEF', 'IFCCIRCLEPROFILEDEF', 'IFCISHAPEPROFILEDEF', 'IFCARBITRARYCLOSEDPROFILEDEF', 'IFCARBITRARYPROFILEDEFWITHVOIDS', 'IFCPOLYLINE', 'IFCINDEXEDPOLYCURVE',
  'IFCCARTESIANPOINTLIST2D', 'IFCGEOMETRICCURVESET', 'IFCGEOMETRICSET', 'IFCBOOLEANCLIPPINGRESULT', 'IFCBOOLEANRESULT', 'IFCMAPPEDITEM', 'IFCREPRESENTATIONMAP', 'IFCCARTESIANTRANSFORMATIONOPERATOR3D'];
const UNITS = ['IFCSIUNIT', 'IFCUNITASSIGNMENT', 'IFCCONVERSIONBASEDUNIT', 'IFCMEASUREWITHUNIT'];

const WANTED = new Set([...Object.keys(ELEMENTS), ...COUNT_ONLY, ...STRUCTURE, ...PROPS, ...GEOM, ...UNITS]);
// only geometry holders reference points we care about (keeps pass B small on tessellated models)
const POINT_HOLDERS = new Set(['IFCAXIS2PLACEMENT3D', 'IFCAXIS2PLACEMENT2D', 'IFCPOLYLINE', 'IFCCARTESIANTRANSFORMATIONOPERATOR3D']);

const PREFIX = { EXA: 1e18, PETA: 1e15, TERA: 1e12, GIGA: 1e9, MEGA: 1e6, KILO: 1e3, HECTO: 1e2, DECA: 10, DECI: 0.1, CENTI: 0.01, MILLI: 1e-3, MICRO: 1e-6, NANO: 1e-9 };

// ---- tiny 3D affine helpers ------------------------------------------------------------------------------
/** @typedef {{ x:number[], y:number[], z:number[], t:number[] }} M3 */
const ID3 = () => ({ x: [1, 0, 0], y: [0, 1, 0], z: [0, 0, 1], t: [0, 0, 0] });
const dot = (a, b) => a[0] * b[0] + a[1] * b[1] + a[2] * b[2];
const cross = (a, b) => [a[1] * b[2] - a[2] * b[1], a[2] * b[0] - a[0] * b[2], a[0] * b[1] - a[1] * b[0]];
const norm = (a) => { const l = Math.hypot(...a); return l < 1e-12 ? null : a.map((v) => v / l); };
/** @param {M3} m @param {number[]} p */
const apply3 = (m, p) => [0, 1, 2].map((i) => m.x[i] * p[0] + m.y[i] * (p[1] ?? 0) + m.z[i] * (p[2] ?? 0) + m.t[i]);
/** world = a(b(p)) @param {M3} a @param {M3} b @returns {M3} */
const compose = (a, b) => ({
  x: apply3({ ...a, t: [0, 0, 0] }, b.x), y: apply3({ ...a, t: [0, 0, 0] }, b.y), z: apply3({ ...a, t: [0, 0, 0] }, b.z),
  t: apply3(a, b.t),
});
/** inverse of a rigid (orthonormal) transform @param {M3} m @returns {M3} */
const invRigid = (m) => {
  const r = [[m.x[0], m.x[1], m.x[2]], [m.y[0], m.y[1], m.y[2]], [m.z[0], m.z[1], m.z[2]]]; // rows = old columns
  const inv = { x: [r[0][0], r[1][0], r[2][0]], y: [r[0][1], r[1][1], r[2][1]], z: [r[0][2], r[1][2], r[2][2]], t: [0, 0, 0] };
  inv.t = apply3({ ...inv, t: [0, 0, 0] }, m.t).map((v) => -v);
  return inv;
};

/** "Pset_WallCommon.IsExternal" -> { Pset_WallCommon: { IsExternal } } (propsOf also stores bare names; those are skipped) */
const groupPsets = (props) => {
  const out = {};
  for (const [k, v] of props) {
    const i = k.indexOf('.');
    if (i < 0) continue;
    const set = k.slice(0, i) || '_', name = k.slice(i + 1);
    (out[set] ??= {})[name] = typeof v === 'object' ? String(v) : v;
  }
  return out;
};

/**
 * Element key from an IFC GlobalId. The GUID alphabet is [0-9A-Za-z_$]; ids may only hold [A-Za-z0-9_.-], so '_' and '$'
 * are escaped INJECTIVELY ('_' -> '_u', '$' -> '_d', anything else -> '_x' + hex): '...q$' and '...q_' stay distinct.
 * (A plain "replace with _" made 7 pairs of clinic GUIDs collide across levels.) provenance.source_ref keeps the raw GUID.
 */
export const encodeGuid = (g) => String(g).replace(/[^A-Za-z0-9.-]/g, (c) => (c === '_' ? '_u' : c === '$' ? '_d' : `_x${c.codePointAt(0).toString(16)}`));
const guidKey = (guid, fallback) => `ifc_${encodeGuid(guid ?? fallback)}`;
const slug = (s) => String(s ?? 'level').toLowerCase().replace(/[^a-z0-9]+/g, '-').replace(/^-|-$/g, '') || 'level';

/**
 * @typedef {Object} IfcExtractOptions
 * @property {import('../config.js').PipelineConfig} config
 * @property {any} [logger]
 * @property {string[]} [levels] only these storey names / GlobalIds (default: all storeys)
 */

/**
 * @param {string} filePath
 * @param {IfcExtractOptions} opts
 * @returns {Promise<import('./bundle.js').Bundle[]>}
 */
export async function extractIfc(filePath, opts) {
  const { entities, schema } = await parseStepFile(filePath, { wanted: WANTED, pointHolders: POINT_HOLDERS });
  const model = new IfcModel(entities, schema);
  const size = (await fs.stat(filePath)).size;
  return buildBundles(model, { ...opts, filePath, fileSize: size });
}

// =====================================================================================================
// model access
// =====================================================================================================

class IfcModel {
  /** @param {Map<number,{type:string,args:any[]}>} entities @param {string|null} schema */
  constructor(entities, schema) {
    this.e = entities;
    this.schema = schema;
    this.cache = new Map();
    this.lenM = this.#lengthFactor();
    this.areaM2 = this.#areaFactor();
    this.#index();
  }

  get(ref) { return isRef(ref) ? this.e.get(ref.ref) : undefined; }
  ofType(...types) { const s = new Set(types); const out = []; for (const [id, v] of this.e) if (s.has(v.type)) out.push([id, v]); return out; }

  #unitFactor(u) {
    if (!u) return 1;
    if (u.type === 'IFCSIUNIT') return u.args[2] ? (PREFIX[u.args[2].enum] ?? 1) : 1;
    if (u.type === 'IFCCONVERSIONBASEDUNIT') {
      const mwu = this.get(u.args[3]);
      const v = Number(unwrap(mwu?.args?.[0]));
      return (Number.isFinite(v) ? v : 1) * this.#unitFactor(this.get(mwu?.args?.[1]));
    }
    return 1;
  }

  #unitOfType(type) {
    for (const [, ua] of this.ofType('IFCUNITASSIGNMENT')) {
      for (const r of ua.args[0] ?? []) {
        const u = this.get(r);
        const t = u?.type === 'IFCSIUNIT' ? u.args[1]?.enum : u?.args?.[1]?.enum;
        if (t === type) return u;
      }
    }
    return null;
  }

  #lengthFactor() { return this.#unitFactor(this.#unitOfType('LENGTHUNIT')); }

  #areaFactor() {
    const u = this.#unitOfType('AREAUNIT');
    if (!u) return 1;
    if (u.type === 'IFCCONVERSIONBASEDUNIT') return this.#unitFactor(u);
    return 1; // SI square metre: prefix on a derived unit is ignored on purpose (practically never used)
  }

  // ---- relationships -------------------------------------------------------------------------------------
  #index() {
    /** @type {Map<number, number>} child -> parent */
    this.parent = new Map();
    this.fillerHost = new Map(); // door/window id -> wall id
    this.defs = new Map(); // object id -> [definition ids]
    this.materialOf = new Map(); // object id -> material ref
    const openingWall = new Map();
    for (const [, r] of this.e) {
      switch (r.type) {
        case 'IFCRELAGGREGATES':
          for (const c of r.args[5] ?? []) if (isRef(c) && isRef(r.args[4])) this.parent.set(c.ref, r.args[4].ref);
          break;
        case 'IFCRELCONTAINEDINSPATIALSTRUCTURE':
          for (const c of r.args[4] ?? []) if (isRef(c) && isRef(r.args[5])) this.parent.set(c.ref, r.args[5].ref);
          break;
        case 'IFCRELVOIDSELEMENT':
          if (isRef(r.args[4]) && isRef(r.args[5])) openingWall.set(r.args[5].ref, r.args[4].ref);
          break;
        case 'IFCRELDEFINESBYPROPERTIES':
          for (const o of r.args[4] ?? []) if (isRef(o) && isRef(r.args[5])) (this.defs.get(o.ref) ?? this.defs.set(o.ref, []).get(o.ref)).push(r.args[5].ref);
          break;
        case 'IFCRELASSOCIATESMATERIAL':
          for (const o of r.args[4] ?? []) if (isRef(o) && isRef(r.args[5])) this.materialOf.set(o.ref, r.args[5]);
          break;
        default:
      }
    }
    for (const [, r] of this.e) {
      if (r.type === 'IFCRELFILLSELEMENT' && isRef(r.args[4]) && isRef(r.args[5])) {
        const wall = openingWall.get(r.args[4].ref);
        if (wall) this.fillerHost.set(r.args[5].ref, wall);
      }
    }
  }

  storeyOf(id) {
    let cur = id;
    for (let i = 0; i < 12 && cur != null; i++) {
      if (this.e.get(cur)?.type === 'IFCBUILDINGSTOREY') return cur;
      cur = this.parent.get(cur);
    }
    return null;
  }

  // ---- geometry primitives -----------------------------------------------------------------------------------
  point(ref) { const p = this.get(ref); return p?.type === 'IFCCARTESIANPOINT' ? (p.args[0] ?? []).map(Number) : null; }
  dir(ref) { const d = this.get(ref); return d?.type === 'IFCDIRECTION' ? (d.args[0] ?? []).map(Number) : null; }

  /** @returns {M3} */
  axis(ref) {
    const a = this.get(ref);
    if (!a) return ID3();
    const loc = this.point(a.args[0]) ?? [0, 0, 0];
    if (a.type === 'IFCAXIS2PLACEMENT2D') {
      const rd = this.dir(a.args[1]) ?? [1, 0];
      const x = norm([rd[0], rd[1], 0]) ?? [1, 0, 0];
      return { x, y: [-x[1], x[0], 0], z: [0, 0, 1], t: [loc[0], loc[1], 0] };
    }
    const z = norm(this.dir(a.args[1]) ?? [0, 0, 1]) ?? [0, 0, 1];
    const rd = this.dir(a.args[2]) ?? [1, 0, 0];
    const d = dot(rd, z);
    let x = norm([rd[0] - d * z[0], rd[1] - d * z[1], rd[2] - d * z[2]]);
    if (!x) x = norm(cross(Math.abs(z[0]) < 0.9 ? [1, 0, 0] : [0, 1, 0], z)) ?? [1, 0, 0];
    return { x, y: cross(z, x), z, t: [loc[0], loc[1], loc[2] ?? 0] };
  }

  /** @returns {M3} world placement of a product's ObjectPlacement */
  placement(ref) {
    const pl = this.get(ref);
    if (!pl || pl.type !== 'IFCLOCALPLACEMENT') return ID3();
    if (this.cache.has(ref.ref)) return this.cache.get(ref.ref);
    const rel = this.axis(pl.args[1]);
    const m = isRef(pl.args[0]) ? compose(this.placement(pl.args[0]), rel) : rel;
    this.cache.set(ref.ref, m);
    return m;
  }

  /** profile -> 2D ring in profile-local coordinates (profile Position applied) */
  profileRing(ref) {
    const p = this.get(ref);
    if (!p) return null;
    const pos2 = (idx) => (isRef(p.args[idx]) ? this.axis(p.args[idx]) : ID3());
    let ring = null;
    switch (p.type) {
      case 'IFCRECTANGLEPROFILEDEF': {
        const [dx, dy] = [Number(p.args[3]) / 2, Number(p.args[4]) / 2];
        const m = pos2(2);
        ring = [[-dx, -dy], [dx, -dy], [dx, dy], [-dx, dy]].map((q) => apply3(m, [q[0], q[1], 0]).slice(0, 2));
        break;
      }
      case 'IFCISHAPEPROFILEDEF': {
        const [dx, dy] = [Number(p.args[3]) / 2, Number(p.args[4]) / 2]; // OverallWidth, OverallDepth: bounding rectangle
        const m = pos2(2);
        ring = [[-dx, -dy], [dx, -dy], [dx, dy], [-dx, dy]].map((q) => apply3(m, [q[0], q[1], 0]).slice(0, 2));
        break;
      }
      case 'IFCCIRCLEPROFILEDEF': {
        const m = pos2(2);
        ring = circlePolygon([0, 0], Number(p.args[3]), 16).map((q) => apply3(m, [q[0], q[1], 0]).slice(0, 2));
        break;
      }
      case 'IFCARBITRARYCLOSEDPROFILEDEF':
      case 'IFCARBITRARYPROFILEDEFWITHVOIDS':
        ring = this.curvePoints(p.args[2])?.map((q) => [q[0], q[1]]) ?? null;
        break;
      default:
    }
    return ring ? cleanRing(ring, 1e-9) : null;
  }

  /** polyline / indexed poly curve -> list of points */
  curvePoints(ref) {
    const c = this.get(ref);
    if (!c) return null;
    if (c.type === 'IFCPOLYLINE') {
      const pts = (c.args[0] ?? []).map((r) => this.point(r)).filter(Boolean);
      return pts.length >= 2 ? pts : null;
    }
    if (c.type === 'IFCINDEXEDPOLYCURVE') {
      const list = this.get(c.args[0]);
      const coords = list?.args?.[0];
      if (!Array.isArray(coords)) return null;
      const idx = [];
      if (Array.isArray(c.args[1]) && c.args[1].length) {
        for (const seg of c.args[1]) {
          const v = seg && typeof seg === 'object' && 'value' in seg ? seg.value : seg;
          if (Array.isArray(v)) for (const i of v) idx.push(Number(i) - 1);
        }
      } else coords.forEach((_, i) => idx.push(i));
      const pts = idx.map((i) => coords[i]).filter(Boolean).map((q) => q.map(Number));
      return pts.length >= 2 ? pts : null;
    }
    return null;
  }

  /**
   * Plan outline of a representation item in PRODUCT-LOCAL coordinates.
   * @returns {{ ring: number[][], depth: number|null, source: 'extrusion'|'swept'|'curve' } | null}
   */
  itemOutline(ref, depth = 0) {
    const it = this.get(ref);
    if (!it || depth > 6) return null;
    switch (it.type) {
      case 'IFCEXTRUDEDAREASOLID': {
        const prof = this.profileRing(it.args[0]);
        if (!prof) return null;
        const pos = this.axis(it.args[1]);
        const dir = this.dir(it.args[2]) ?? [0, 0, 1];
        const dLocal = apply3({ ...pos, t: [0, 0, 0] }, dir);
        const depthU = Number(it.args[3]) || 0;
        if (Math.abs(dLocal[2]) < 0.85) {
          // not vertical (a horizontal pipe, a beam): the plan footprint is the convex hull of the profile at both ends of the sweep
          const ends = prof.flatMap((q) => { const p0 = apply3(pos, [q[0], q[1], 0]); return [p0, [p0[0] + dLocal[0] * depthU, p0[1] + dLocal[1] * depthU, p0[2] + dLocal[2] * depthU]]; });
          const ring = convexHull2(ends);
          if (ring.length < 3) return null;
          const zMin = Math.min(...ends.map((p) => p[2]));
          return { ring: ring.map((p) => [p[0], p[1], zMin]), depth: Math.max(...ends.map((p) => p[2])) - zMin || null, source: 'swept' };
        }
        return { ring: prof.map((q) => apply3(pos, [q[0], q[1], 0])), depth: depthU || null, source: 'extrusion' };
      }
      case 'IFCBOOLEANCLIPPINGRESULT':
      case 'IFCBOOLEANRESULT':
        return this.itemOutline(it.args[1], depth + 1);
      case 'IFCMAPPEDITEM': {
        const map = this.get(it.args[0]);
        const rep = this.get(map?.args?.[1]);
        const op = this.get(it.args[1]);
        if (!rep) return null;
        let target = ID3();
        if (op?.type === 'IFCCARTESIANTRANSFORMATIONOPERATOR3D') {
          const x = norm(this.dir(op.args[0]) ?? [1, 0, 0]) ?? [1, 0, 0];
          const y0 = norm(this.dir(op.args[1]) ?? [0, 1, 0]) ?? [0, 1, 0];
          const org = this.point(op.args[2]) ?? [0, 0, 0];
          const sc = Number(op.args[3]) || 1;
          const z = norm(this.dir(op.args[4]) ?? cross(x, y0)) ?? [0, 0, 1];
          target = { x: x.map((v) => v * sc), y: y0.map((v) => v * sc), z: z.map((v) => v * sc), t: org };
        }
        const origin = isRef(map?.args?.[0]) ? invRigid(this.axis(map.args[0])) : ID3();
        const m = compose(target, origin);
        for (const sub of rep.args[3] ?? []) {
          const o = this.itemOutline(sub, depth + 1);
          if (o) return { ...o, ring: o.ring.map((p) => apply3(m, p)) };
        }
        return null;
      }
      case 'IFCGEOMETRICCURVESET':
      case 'IFCGEOMETRICSET': {
        // a FootPrint representation (IfcSpace in IFC2X3 exports): the largest closed curve of the set is the outline
        let best = null;
        for (const el of it.args[0] ?? []) {
          const o = this.itemOutline(el, depth + 1);
          if (o && (!best || Math.abs(polygonArea(o.ring)) > Math.abs(polygonArea(best.ring)))) best = o;
        }
        return best;
      }
      case 'IFCPOLYLINE':
      case 'IFCINDEXEDPOLYCURVE': {
        const pts = this.curvePoints(ref);
        if (!pts || pts.length < 3) return null;
        return { ring: pts.map((q) => [q[0], q[1], q[2] ?? 0]), depth: null, source: 'curve' };
      }
      default:
        return null;
    }
  }

  /** Representation of a product -> { outline, axis } (both product-local). */
  shapeOf(product) {
    const def = this.get(product.args[6]);
    if (!def || def.type !== 'IFCPRODUCTDEFINITIONSHAPE') return { outline: null, axisLine: null, meshOnly: false };
    const reps = (def.args[2] ?? []).map((r) => this.get(r)).filter((r) => r?.type === 'IFCSHAPEREPRESENTATION');
    const byId = (id) => reps.filter((r) => String(r.args[1]) === id);
    let outline = null, axisLine = null;
    for (const r of [...byId('FootPrint'), ...byId('Body')]) {
      let best = null;
      for (const it of r.args[3] ?? []) {
        const o = this.itemOutline(it);
        if (o && (!best || polygonArea(o.ring) > polygonArea(best.ring))) best = o;
      }
      if (best) { outline = best; break; }
    }
    for (const r of byId('Axis')) {
      for (const it of r.args[3] ?? []) {
        const pts = this.curvePoints(it);
        if (pts && pts.length >= 2) { axisLine = pts; break; }
      }
    }
    const meshOnly = !outline && reps.some((r) => /Brep|Tessellation|SurfaceModel|MappedRepresentation/.test(String(r.args[2])));
    return { outline, axisLine, meshOnly };
  }

  // ---- properties ----------------------------------------------------------------------------------------------
  /** @returns {{ props: Map<string, any>, quantities: Map<string, number> }} */
  propsOf(id) {
    const props = new Map(), quantities = new Map();
    for (const d of this.defs.get(id) ?? []) {
      const def = this.e.get(d);
      if (!def) continue;
      if (def.type === 'IFCPROPERTYSET') {
        const set = String(def.args[2] ?? '');
        for (const pr of def.args[4] ?? []) {
          const p = this.get(pr);
          if (!p) continue;
          let v = null;
          if (p.type === 'IFCPROPERTYSINGLEVALUE') v = unwrap(p.args[2]);
          else if (p.type === 'IFCPROPERTYENUMERATEDVALUE') v = unwrap((p.args[2] ?? [])[0]);
          if (v == null) continue;
          props.set(`${set}.${p.args[0]}`, v);
          if (!props.has(String(p.args[0]))) props.set(String(p.args[0]), v);
        }
      } else if (def.type === 'IFCELEMENTQUANTITY') {
        for (const qr of def.args[5] ?? []) {
          const q = this.get(qr);
          if (!q) continue;
          const v = Number(unwrap(q.args[3]));
          if (Number.isFinite(v)) quantities.set(String(q.args[0]), q.type === 'IFCQUANTITYAREA' ? v * this.areaM2 : q.type === 'IFCQUANTITYLENGTH' ? v * this.lenM : v);
        }
      }
    }
    return { props, quantities };
  }

  materialsOf(id) {
    const out = new Set();
    const visit = (ref, depth = 0) => {
      const m = this.get(ref);
      if (!m || depth > 4) return;
      if (m.type === 'IFCMATERIAL') out.add(String(m.args[0]));
      else if (m.type === 'IFCMATERIALLAYERSETUSAGE') visit(m.args[0], depth + 1);
      else if (m.type === 'IFCMATERIALLAYERSET') for (const l of m.args[0] ?? []) visit(l, depth + 1);
      else if (m.type === 'IFCMATERIALLAYER') visit(m.args[0], depth + 1);
      else if (m.type === 'IFCMATERIALCONSTITUENTSET') for (const c of m.args[2] ?? []) visit(c, depth + 1);
      else if (m.type === 'IFCMATERIALCONSTITUENT') visit(m.args[2], depth + 1);
    };
    const r = this.materialOf.get(id);
    if (r) visit(r);
    return [...out].slice(0, 3);
  }
}

// =====================================================================================================
// bundle construction
// =====================================================================================================

/**
 * @param {IfcModel} model
 * @param {IfcExtractOptions & { filePath: string, fileSize: number }} opts
 */
async function buildBundles(model, opts) {
  const { config } = opts;
  const lenM = model.lenM;
  const warnings = [];

  // ---- collect element records --------------------------------------------------------------------------------
  /** @type {Array<Record<string, any>>} */
  const recs = [];
  const countOnly = {};
  const typeCounts = {};
  for (const [id, ent] of model.e) {
    if (COUNT_ONLY.has(ent.type)) { countOnly[ent.type] = (countOnly[ent.type] ?? 0) + 1; continue; }
    const cls = ELEMENTS[ent.type];
    if (!cls) continue;
    typeCounts[ent.type] = (typeCounts[ent.type] ?? 0) + 1;
    const guid = ent.args[0];
    const shape = model.shapeOf(ent);
    const world = isRef(ent.args[5]) ? model.placement(ent.args[5]) : ID3();
    const { props, quantities } = model.propsOf(id);
    recs.push({
      id, ent, bucket: cls[0], category: cls[1] ?? null, key: guidKey(guid, id), name: ent.args[2] ?? null,
      shape, world, props, quantities, host: model.fillerHost.get(id) ?? null,
    });
  }

  // ---- optional bounding boxes for elements that have no analytic footprint ---------------------------------------
  const needGeom = recs.filter((r) => !r.shape.outline && r.shape.meshOnly);
  /** @type {Map<number, {minX:number,maxX:number,minY:number,maxY:number,minZ:number,maxZ:number}>} */
  let meshBounds = new Map();
  if (needGeom.length && config.ifc.webIfc !== 'off' && opts.fileSize <= config.ifc.webIfcMaxBytes) {
    try {
      meshBounds = await webIfcBounds(opts.filePath, new Set(needGeom.map((r) => r.id)));
      if (meshBounds.size) warnings.push(`web-ifc supplied bounding boxes for ${meshBounds.size} mesh-only elements (approximate footprints)`);
    } catch (err) {
      opts.logger?.warn?.(`web-ifc geometry fallback unavailable: ${err.message}`);
    }
  }

  // ---- storeys ---------------------------------------------------------------------------------------------------------
  const storeys = model.ofType('IFCBUILDINGSTOREY').map(([id, s]) => ({
    id, name: String(s.args[2] ?? s.args[7] ?? `storey_${id}`), longName: s.args[7] ?? null, guid: s.args[0],
    elevationM: (Number(s.args[9]) || 0) * lenM,
  })).sort((a, b) => a.elevationM - b.elevationM);
  // storey height = distance to the next storey up (unknown for the top one)
  storeys.forEach((s, i) => { const up = storeys[i + 1]; s.heightM = up && up.elevationM > s.elevationM ? round(up.elevationM - s.elevationM, 3) : null; });

  const groups = new Map(); // storey id (or 0) -> records
  const storeyForElevation = (zM) => storeys.reduce((best, s) => (best == null || Math.abs(s.elevationM - zM) < Math.abs(best.elevationM - zM) ? s : best), null);
  for (const r of recs) {
    let sid = model.storeyOf(r.id) ?? (r.host ? model.storeyOf(r.host) : null);
    if (sid == null) sid = storeys.length ? storeyForElevation(r.world.t[2] * lenM).id : 0;
    (groups.get(sid) ?? groups.set(sid, []).get(sid)).push(r);
  }

  // ---- buildings: ONE bundle (one JSON) per IfcBuilding, all its storeys as levels on one shared canvas ------------------
  const wanted = opts.levels?.length ? new Set(opts.levels.map((s) => s.toLowerCase())) : null;
  const order = storeys.length ? storeys : [{ id: 0, name: null, elevationM: 0, guid: null }];
  const buildingOf = (sid) => { let c = sid; for (let k = 0; k < 16 && c != null; k++) { if (model.e.get(c)?.type === 'IFCBUILDING') return c; c = model.parent.get(c); } return 0; };
  const byBuilding = new Map(); // building id (0 = none) -> [{ st, list }]
  for (const st of order) {
    if (wanted && !(wanted.has(String(st.name).toLowerCase()) || wanted.has(String(st.guid).toLowerCase()))) continue;
    const list = groups.get(st.id) ?? [];
    if (!list.length) continue;
    const bid = st.id ? buildingOf(st.id) : 0;
    (byBuilding.get(bid) ?? byBuilding.set(bid, []).get(bid)).push({ st, list });
  }
  const bundles = [];
  const usedSuffix = new Map(); // two buildings may share a name: keep output file names unique
  for (const [bid, parts] of byBuilding) {
    const be = bid ? model.e.get(bid) : null;
    const building = { id: bid, name: be?.args?.[2] != null ? String(be.args[2]) : null, longName: be?.args?.[7] != null ? String(be.args[7]) : null, guid: be?.args?.[0] ?? null };
    const b = buildingBundle(model, building, parts, meshBounds, { config, lenM, typeCounts, countOnly, warnings: [...warnings], schema: model.schema, fileName: opts.filePath });
    // file suffix only when the model has several buildings (one building -> exactly the requested output path)
    b.page.labelSuffix = byBuilding.size > 1 ? slug(building.name ?? building.longName ?? `building-${bid}`) : null;
    const sfx = b.page.labelSuffix;
    if (sfx) {
      const n = (usedSuffix.get(sfx) ?? 0) + 1;
      usedSuffix.set(sfx, n);
      if (n > 1) b.page.labelSuffix = `${sfx}-${n}`;
    }
    bundles.push(b);
  }
  return bundles;
}

/** 2D convex hull (x, y of 3D points), counter-clockwise */
function convexHull2(points) {
  const p = [...points].map((q) => [q[0], q[1]]).sort((a, b) => a[0] - b[0] || a[1] - b[1]);
  if (p.length < 3) return p;
  const cr = (o, a, b) => (a[0] - o[0]) * (b[1] - o[1]) - (a[1] - o[1]) * (b[0] - o[0]);
  const lo = [], up = [];
  for (const q of p) { while (lo.length >= 2 && cr(lo[lo.length - 2], lo[lo.length - 1], q) <= 0) lo.pop(); lo.push(q); }
  for (const q of p.reverse()) { while (up.length >= 2 && cr(up[up.length - 2], up[up.length - 1], q) <= 0) up.pop(); up.push(q); }
  return [...lo.slice(0, -1), ...up.slice(0, -1)];
}

/** Convert an element's outline to world XY (metres). Returns ring or null, plus method & elevation. */
function worldFootprint(model, r, lenM, meshBounds) {
  if (r.shape.outline) {
    const ring = r.shape.outline.ring.map((p) => apply3(r.world, p));
    return { ring: cleanRing(ring.map((p) => [p[0] * lenM, p[1] * lenM]), 1e-9), z: Math.min(...ring.map((p) => p[2])) * lenM, depthM: r.shape.outline.depth != null ? r.shape.outline.depth * lenM : null, method: 'extrusion', confidence: 0.95 };
  }
  const mb = meshBounds.get(r.id);
  if (mb) {
    return { ring: [[mb.minX, mb.minY], [mb.maxX, mb.minY], [mb.maxX, mb.maxY], [mb.minX, mb.maxY]], z: mb.minZ, depthM: mb.maxZ - mb.minZ, method: 'mesh_bbox', confidence: 0.6 };
  }
  // attribute-only fallbacks (rectangle at the placement origin)
  const origin = r.world;
  const box = (len, thick, centered) => {
    const ring = [[0, -thick / 2], [len, -thick / 2], [len, thick / 2], [0, thick / 2]].map(([x, y]) => apply3(origin, [x, centered ? y : y + thick / 2, 0]));
    return ring.map((p) => [p[0] * lenM, p[1] * lenM]);
  };
  if (r.bucket === 'door' || r.bucket === 'window') {
    const w = Number(r.ent.args[9]);
    if (Number.isFinite(w) && w > 0) return { ring: box(w, 0.1 / lenM, true), z: origin.t[2] * lenM, depthM: null, method: 'overall_width', confidence: 0.5 };
  }
  if (r.bucket === 'wall') {
    const L = r.quantities.get('Length') != null ? r.quantities.get('Length') / lenM : null;
    const W = (r.quantities.get('Width') ?? r.quantities.get('Thickness')) != null ? (r.quantities.get('Width') ?? r.quantities.get('Thickness')) / lenM : null;
    if (L && W) return { ring: box(L, W, true), z: origin.t[2] * lenM, depthM: null, method: 'quantities', confidence: 0.5 };
  }
  return null;
}

const doorSubtype = (op, isExternal) => {
  if (isExternal) return 'entrance';
  const s = String(op ?? '').toUpperCase();
  if (s.includes('SINGLE_SWING')) return 'single_swing';
  if (s.includes('DOUBLE_SWING') || s.includes('DOUBLE_DOOR')) return 'double_swing';
  if (s.includes('SLIDING')) return 'sliding';
  if (s.includes('FOLDING')) return 'folding';
  if (s.includes('REVOLVING')) return 'revolving';
  if (s.includes('ROLLINGUP')) return 'rolling';
  return null;
};

/**
 * One bundle for one building: every storey becomes a level, every element keeps its storey (`levelKey`), and all
 * storeys share ONE canvas (one page frame, one transform), so coordinates are consistent across floors.
 * @param {{ id:number, name:string|null, longName:string|null, guid:any }} building
 * @param {Array<{ st:any, list:any[] }>} parts storeys of the building, lowest first, with their element records
 */
function buildingBundle(model, building, parts, meshBounds, ctx) {
  const { lenM, config } = ctx;
  const warnings = [...ctx.warnings];
  /** @type {Array<{r:any, fp:any, st:any}>} */
  const placed = [];
  let noGeometry = 0;
  for (const { st, list } of parts) {
    for (const r of list) {
      const fp = worldFootprint(model, r, lenM, meshBounds);
      if (!fp || fp.ring.length < 3) { noGeometry++; continue; }
      placed.push({ r, fp, st });
    }
  }
  if (noGeometry) warnings.push(`${noGeometry} elements had no usable plan footprint and were skipped`);

  const all = placed.flatMap((p) => p.fp.ring);
  const ext = all.length ? (([x0, y0, x1, y1]) => ({ minX: x0, minY: y0, maxX: x1, maxY: y1 }))(bboxOfPoints(all)) : { minX: 0, minY: 0, maxX: 1, maxY: 1 };
  const canvas = makeCanvas(ext, 1, config.ifc.targetPixelSide);
  const ppm = canvas.pxPerUnit;
  const px = (ring) => ring.map(([x, y]) => canvas.toPx(x, y).map((v) => round(v, 2)));

  const levelKeyOf = (st) => (st.name != null ? `storey_${st.id}` : null);
  const bundle = emptyBundle({
    format: 'ifc',
    extractor: { id: 'vector_ifc', name: 'IFC STEP extractor (streaming)', version: model.schema },
    page: { widthPx: canvas.widthPx, heightPx: canvas.heightPx, renderDpi: null, pageIndex: 0, pageCount: null, title: building.longName ?? building.name ?? null, labelSuffix: null, transforms: [canvas.transform] },
    // every storey of the building, lowest first; elements point to theirs through levelKey
    levels: parts.filter(({ st }) => st.name != null).map(({ st }) => ({
      key: levelKeyOf(st), name: String(st.name), sourceRef: st.guid ? String(st.guid) : null, elevationM: round(st.elevationM, 3), heightM: st.heightM ?? null, confidence: 1,
      longName: st.longName && st.longName !== st.name ? String(st.longName) : null,
    })),
    scale: { metresPerPx: canvas.metresPerPx, source: 'vector_units', confidence: 1, scaleText: null },
  });

  /** provenance + free-form IFC data for the schema entity base */
  const common = (r) => ({
    sourceRef: r.ent.args[0] != null ? String(r.ent.args[0]) : `#${r.id}`,
    properties: { ifc_type: r.ent.type, ...(r.name != null ? { ifc_name: String(r.name) } : {}), ...(r.props.size ? { psets: groupPsets(r.props) } : {}), ...(r.quantities.size ? { quantities: Object.fromEntries(r.quantities) } : {}) },
  });
  const keyOf = new Set(placed.map((p) => p.r.key));
  const materials = new Set();
  let objCapped = 0;
  const objCount = new Map(); // the object cap applies per storey, as before
  for (const { r, fp, st } of placed) {
    const poly = px(fp.ring);
    const levelKey = levelKeyOf(st);
    const isExt = r.props.get('IsExternal');
    const mats = model.materialsOf(r.id);
    mats.forEach((m) => materials.add(m));
    const zRel = fp.z - st.elevationM;
    switch (r.bucket) {
      case 'space': {
        // IfcSpace: Name is usually the room number ("2.10"), LongName the room name ("Kitchen")
        const nm = r.ent.args[2] != null ? String(r.ent.args[2]) : null, ln = r.ent.args[7] != null ? String(r.ent.args[7]) : null;
        bundle.rooms.push({
          ...common(r), key: r.key, levelKey, polygonPx: poly, rawName: ln || nm || null, rawNumber: ln && nm && nm !== ln ? nm : null,
          writtenAreaM2: r.quantities.get('NetFloorArea') ?? r.quantities.get('GrossFloorArea') ?? null,
          heightM: fp.depthM != null ? round(fp.depthM, 3) : (r.quantities.get('Height') ?? null), floorElevationM: round(st.elevationM, 3), confidence: fp.confidence,
        });
        break;
      }
      case 'wall': {
        let centerline = null, thickness = null;
        if (r.shape.axisLine) {
          const pts = r.shape.axisLine.map((p) => apply3(r.world, p)).map((p) => canvas.toPx(p[0] * lenM, p[1] * lenM).map((v) => round(v, 2)));
          centerline = [pts[0], pts[pts.length - 1]];
        }
        const cl = polygonCenterline(poly);
        if (cl) { thickness = round(cl.thickness, 2); if (!centerline) centerline = [cl.a, cl.b].map((q) => q.map((v) => round(v, 2))); }
        const lb = r.props.get('LoadBearing');
        bundle.walls.push({ ...common(r), key: r.key, levelKey, polygonPx: poly, centerlinePx: centerline, thicknessPx: thickness, isExterior: typeof isExt === 'boolean' ? isExt : null, isLoadBearing: typeof lb === 'boolean' ? lb : null,
          netSideAreaM2: r.quantities.get('NetSideArea') ?? null, netVolumeM3: r.quantities.get('NetVolume') ?? null, heightM: fp.depthM != null ? round(fp.depthM, 3) : (r.quantities.get('Height') ?? null), confidence: fp.confidence });
        break;
      }
      case 'door':
      case 'window': {
        const wM = Number(r.ent.args[9]) * lenM, hM = Number(r.ent.args[8]) * lenM;
        const rect = minAreaRect(poly);
        const sill = Math.max(0, round(zRel, 3));
        bundle.openings.push({
          ...common(r), key: r.key, levelKey, kind: r.bucket, heightM: Number.isFinite(hM) && hM > 0 ? round(hM, 3) : null, isEntrance: r.bucket === 'door' && isExt === true ? true : null,
          subtype: r.bucket === 'door' ? doorSubtype(unwrap(r.ent.args[11]), isExt === true) : (unwrap(r.ent.args[11]) ? String(unwrap(r.ent.args[11])).toLowerCase() : null),
          polygonPx: poly, widthPx: Number.isFinite(wM) && wM > 0 ? round(wM * ppm, 2) : (rect ? round(rect.length, 2) : null),
          wallKey: r.host != null && keyOf.has(guidKey(model.e.get(r.host)?.args?.[0], r.host)) ? guidKey(model.e.get(r.host).args[0], r.host) : null,
          sillHeightM: sill, headHeightM: Number.isFinite(hM) && hM > 0 ? round(sill + hM, 3) : null, confidence: fp.confidence,
        });
        break;
      }
      case 'object': {
        if ((objCount.get(st.id) ?? 0) >= config.ifc.maxObjects) { objCapped++; break; }
        objCount.set(st.id, (objCount.get(st.id) ?? 0) + 1);
        bundle.objects.push({ ...common(r), key: r.key, levelKey, category: r.category, name: r.name ? String(r.name) : null, polygonPx: poly, netVolumeM3: r.quantities.get('NetVolume') ?? null, confidence: fp.confidence });
        break;
      }
      default:
    }
  }
  if (objCapped) warnings.push(`${objCapped} objects beyond the cap of ${config.ifc.maxObjects} per storey were not emitted`);

  const has = (arr) => arr.length > 0;
  const cov = (category, status, confidence, note) => bundle.coverage.push({ category, status, confidence, ...(note ? { note } : {}) });
  cov('rooms', 'analysed', 0.95, has(bundle.rooms) ? undefined : 'IFC contains no IfcSpace elements for this building');
  cov('walls', 'analysed', 0.95);
  cov('doors', 'analysed', 0.95);
  cov('windows', 'analysed', 0.95);
  cov('stairs', 'analysed', 0.9);
  cov('fixtures', 'analysed', 0.8, objCapped ? 'capped' : undefined);
  cov('scale', 'analysed', 1, 'exact: from IFC length units');
  cov('text', 'not_analysed', 0.5, 'IFC carries no drawing text');
  cov('dimensions', 'not_analysed', 0.5, 'IFC carries no dimension annotations');
  cov('symbols', 'not_analysed', 0.5);
  cov('legend', 'not_analysed', 0.5);

  bundle.stats = { elements: placed.length, skippedNoGeometry: noGeometry, rooms: bundle.rooms.length, walls: bundle.walls.length, openings: bundle.openings.length, objects: bundle.objects.length, lengthUnitMetres: lenM };
  bundle.semantic = { schema: ctx.schema, building: building.name, buildingRef: building.guid ?? null, storeys: parts.map(({ st }) => ({ name: st.name, elevationM: round(st.elevationM, 3) })), elementTypes: ctx.typeCounts, ignoredTypes: ctx.countOnly, materials: [...materials].slice(0, 12), pxPerMetre: ppm };
  bundle.warnings = warnings;
  return bundle;
}

// =====================================================================================================
// optional web-ifc bounding boxes (EXPERIMENTAL - exercised only when the package is installed)
// =====================================================================================================

/**
 * Stream meshes with web-ifc and keep only an axis-aligned bounding box per requested express id; vertex data is
 * discarded immediately so memory stays flat. Coordinates come back in metres, Y-up; they are converted to IFC Z-up here.
 *
 * @param {string} filePath
 * @param {Set<number>} ids
 * @returns {Promise<Map<number, {minX:number,maxX:number,minY:number,maxY:number,minZ:number,maxZ:number}>>}
 */
export async function webIfcBounds(filePath, ids) {
  const WebIFC = await import('web-ifc'); // throws if not installed -> caller degrades gracefully
  const api = new WebIFC.IfcAPI();
  await api.Init();
  const data = new Uint8Array(await fs.readFile(filePath));
  const modelId = api.OpenModel(data, { COORDINATE_TO_ORIGIN: false });
  const out = new Map();
  try {
    api.StreamAllMeshes(modelId, (mesh) => {
      if (!ids.has(mesh.expressID)) return;
      let b = null;
      const geoms = mesh.geometries;
      for (let i = 0; i < geoms.size(); i++) {
        const pg = geoms.get(i);
        const g = api.GetGeometry(modelId, pg.geometryExpressID);
        const v = api.GetVertexArray(g.GetVertexData(), g.GetVertexDataSize());
        const m = pg.flatTransformation; // column-major 4x4
        for (let k = 0; k < v.length; k += 6) {
          const x = v[k], y = v[k + 1], z = v[k + 2];
          // web-ifc returns Y-UP geometry (IFC x, z, -y): convert back to IFC Z-up (x, y = north, z = up)
          const ux = m[0] * x + m[4] * y + m[8] * z + m[12], uy = m[1] * x + m[5] * y + m[9] * z + m[13], uz = m[2] * x + m[6] * y + m[10] * z + m[14];
          const wx = ux, wy = -uz, wz = uy;
          b = b ? { minX: Math.min(b.minX, wx), maxX: Math.max(b.maxX, wx), minY: Math.min(b.minY, wy), maxY: Math.max(b.maxY, wy), minZ: Math.min(b.minZ, wz), maxZ: Math.max(b.maxZ, wz) } : { minX: wx, maxX: wx, minY: wy, maxY: wy, minZ: wz, maxZ: wz };
        }
        g.delete?.();
      }
      if (b) out.set(mesh.expressID, b);
    });
  } finally {
    api.CloseModel(modelId);
  }
  return out;
}
