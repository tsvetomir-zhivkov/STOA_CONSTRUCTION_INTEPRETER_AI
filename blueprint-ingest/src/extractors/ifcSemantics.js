/**
 * IFC semantics for schema v0.3, read from the parsed entity map (no geometry here, that stays in ifcExtractor.js):
 * project / application / site / building, declared units, quantities with their units, element types, materials and
 * layer sets, systems and zones, space boundaries, coverings (room finishes), classifications, aggregation and
 * wall connections.
 *
 * Rule: every value is read from the file. A unit that is not declared is reported as such (status inferred, with the
 * reason), never silently taken as metres.
 */

import { unwrap, isRef } from './ifc/stepParser.js';
import { typeKind, isTypeClass } from '../schema/ifcClasses.js';

export const SEMANTIC_TYPES = [
  'IFCOWNERHISTORY', 'IFCAPPLICATION', 'IFCORGANIZATION', 'IFCPOSTALADDRESS', 'IFCMAPCONVERSION', 'IFCPROJECTEDCRS',
  'IFCRELDEFINESBYTYPE', 'IFCDOORSTYLE', 'IFCWINDOWSTYLE',
  'IFCSYSTEM', 'IFCDISTRIBUTIONSYSTEM', 'IFCDISTRIBUTIONCIRCUIT', 'IFCBUILDINGSYSTEM', 'IFCELECTRICALCIRCUIT', 'IFCZONE', 'IFCGROUP',
  'IFCRELASSIGNSTOGROUP', 'IFCRELASSIGNSTOGROUPBYFACTOR', 'IFCRELSERVICESBUILDINGS',
  'IFCRELSPACEBOUNDARY', 'IFCRELSPACEBOUNDARY1STLEVEL', 'IFCRELSPACEBOUNDARY2NDLEVEL', 'IFCRELCOVERSSPACES', 'IFCRELCOVERSBLDGELEMENTS',
  'IFCRELASSOCIATESCLASSIFICATION', 'IFCCLASSIFICATIONREFERENCE', 'IFCCLASSIFICATION',
  'IFCRELCONNECTSPATHELEMENTS', 'IFCRELNESTS',
  'IFCMATERIALLIST', 'IFCMATERIALPROFILESET', 'IFCMATERIALPROFILE', 'IFCMATERIALPROFILESETUSAGE',
  'IFCQUANTITYWEIGHT', 'IFCQUANTITYTIME', 'IFCCONVERSIONBASEDUNITWITHOFFSET', 'IFCDERIVEDUNIT', 'IFCDERIVEDUNITELEMENT',
];
/** parse filter: the fixed lists plus every element type class (IfcDoorType, IfcWallType, ...) */
export const isSemanticType = (t) => isTypeClass(t);

const PREFIX = { EXA: 1e18, PETA: 1e15, TERA: 1e12, GIGA: 1e9, MEGA: 1e6, KILO: 1e3, HECTO: 1e2, DECA: 10, DECI: 0.1, CENTI: 0.01, MILLI: 1e-3, MICRO: 1e-6, NANO: 1e-9 };
const str = (v) => { const u = unwrap(v); return u == null || u === '*' ? null : typeof u === 'object' ? null : String(u); };
const num = (v) => { const u = Number(unwrap(v)); return v == null || !Number.isFinite(u) ? null : u; };
const enumStr = (v) => { const u = unwrap(v); return u == null || typeof u === 'object' ? null : typeof u === 'boolean' ? (u ? 'TRUE' : 'FALSE') : String(u); };
const r6 = (n) => (n == null ? null : Math.round(n * 1e6) / 1e6);

/** IFC GlobalId -> id-safe key part; same escaping as ifcExtractor.encodeGuid (injective) */
export const encodeGuid = (g) => String(g).replace(/[^A-Za-z0-9.-]/g, (c) => (c === '_' ? '_u' : c === '$' ? '_d' : `_x${c.codePointAt(0).toString(16)}`));

/**
 * @param {{ e: Map<number,{type:string,args:any[]}>, get(ref:any):any, ofType(...t:string[]):Array<[number,any]>, schema:string|null }} model
 * @param {{ fileName?: string[]|null }} [header]
 */
export function readSemantics(model, header = {}) {
  const S = new Semantics(model, header);
  return S;
}

class Semantics {
  constructor(model, header) {
    this.m = model;
    this.header = header;
    this.units = this.#units();
    this.lenM = this.units.length.to_si ?? 1;
    this.#indexRelations();
  }

  // ---- units ---------------------------------------------------------------------------------------------------------------
  #unitInfo(u, depth = 0) {
    if (!u || depth > 4) return null;
    if (u.type === 'IFCSIUNIT') {
      const pre = u.args[2]?.enum ?? null, name = u.args[3]?.enum ?? null;
      const p = pre ? (PREFIX[pre] ?? 1) : 1;
      const f = name === 'METRE' ? p : name === 'SQUARE_METRE' ? p * p : name === 'CUBIC_METRE' ? p * p * p : name === 'RADIAN' ? p * (180 / Math.PI) : name === 'GRAM' ? p / 1000 : name === 'SECOND' ? p : p;
      return { label: [pre, name].filter(Boolean).join(' '), toSi: f };
    }
    if (u.type === 'IFCCONVERSIONBASEDUNIT' || u.type === 'IFCCONVERSIONBASEDUNITWITHOFFSET') {
      const mwu = this.m.get(u.args[3]);
      const v = num(mwu?.args?.[0]);
      const base = this.#unitInfo(this.m.get(mwu?.args?.[1]), depth + 1);
      return { label: str(u.args[2]) ?? 'conversion based unit', toSi: v != null && base?.toSi != null ? v * base.toSi : null };
    }
    return null;
  }

  #units() {
    const proj = this.m.ofType('IFCPROJECT')[0]?.[1];
    let assign = proj && isRef(proj.args[8]) ? this.m.get(proj.args[8]) : null;
    let assignId = proj && isRef(proj.args[8]) ? proj.args[8].ref : null;
    if (!assign) { const first = this.m.ofType('IFCUNITASSIGNMENT')[0]; assign = first?.[1] ?? null; assignId = first?.[0] ?? null; }
    const found = new Map();
    for (const r of assign?.args?.[0] ?? []) {
      const u = this.m.get(r);
      const t = u?.args?.[1]?.enum;
      if (t && !found.has(t)) found.set(t, { u, id: r.ref });
    }
    const decl = (type, fallback) => {
      const f = found.get(type);
      if (f) {
        const info = this.#unitInfo(f.u);
        if (info?.toSi != null) return { unit: info.label, to_si: r6(info.toSi) ?? info.toSi, status: 'explicit', source: `IfcUnitAssignment #${assignId}: ${f.u.type} #${f.id}` };
      }
      return fallback();
    };
    const length = decl('LENGTHUNIT', () => ({ unit: 'METRE', to_si: 1, status: 'inferred', source: 'no LENGTHUNIT in IfcUnitAssignment: geometry read as metres (flag units_not_declared)' }));
    const L = length.to_si;
    const area = decl('AREAUNIT', () => ({ unit: `(${length.unit})^2`, to_si: r6(L * L), status: 'inferred', source: 'no AREAUNIT declared: square of the length unit' }));
    const volume = decl('VOLUMEUNIT', () => ({ unit: `(${length.unit})^3`, to_si: L * L * L, status: 'inferred', source: 'no VOLUMEUNIT declared: cube of the length unit' }));
    const angle = decl('PLANEANGLEUNIT', () => ({ unit: 'RADIAN', to_si: r6(180 / Math.PI), status: 'inferred', source: 'no PLANEANGLEUNIT declared: IFC SI default radian' }));
    const mass = decl('MASSUNIT', () => ({ unit: null, to_si: null, status: 'not_available', source: 'no MASSUNIT declared' }));
    return { length, area, volume, angle, mass };
  }

  /** factor for a quantity's own Unit attribute (overrides the project unit) */
  #ownUnit(ref) { const u = this.m.get(ref); return u ? this.#unitInfo(u) : null; }

  // ---- project / site / building -----------------------------------------------------------------------------------------
  project() {
    const [, p] = this.m.ofType('IFCPROJECT')[0] ?? [];
    const oh = p ? this.m.get(p.args[1]) : null;
    const app = oh ? this.m.get(oh.args[1]) ?? this.m.get(oh.args[6]) : null;
    const dev = app ? this.m.get(app.args[0]) : null;
    const created = num(oh?.args?.[7]);
    const fn = this.header.fileName ?? null;
    return {
      name: str(p?.args?.[2]), long_name: str(p?.args?.[5]), description: str(p?.args?.[3]), phase: str(p?.args?.[6]),
      global_id: str(p?.args?.[0]), schema_identifier: this.m.schema ?? null,
      application: app || fn ? {
        name: str(app?.args?.[2]), version: str(app?.args?.[1]), developer: str(dev?.args?.[1]), identifier: str(app?.args?.[3]),
        originating_system: fn && fn.length >= 6 ? (fn[fn.length - 2] || null) : null,
      } : null,
      created_at: created != null && created > 0 ? new Date(created * 1000).toISOString() : (fn?.[1] || null),
      units: this.units,
    };
  }

  #address(ref) {
    const a = this.m.get(ref);
    if (!a || a.type !== 'IFCPOSTALADDRESS') return null;
    const lines = Array.isArray(a.args[4]) ? a.args[4].map(str).filter(Boolean) : [];
    const parts = [...lines, str(a.args[8]), str(a.args[6]), str(a.args[7]), str(a.args[9])].filter(Boolean);
    return parts.length ? parts.join(', ') : null;
  }

  static compound(v) {
    if (!Array.isArray(v) || !v.length) return null;
    const [d = 0, m = 0, s = 0, us = 0] = v.map(Number);
    const neg = [d, m, s, us].some((x) => x < 0);
    const deg = Math.abs(d) + Math.abs(m) / 60 + (Math.abs(s) + Math.abs(us) / 1e6) / 3600;
    return r6(neg ? -deg : deg);
  }

  site() {
    const [id, s] = this.m.ofType('IFCSITE')[0] ?? [];
    if (!s) return null;
    const elev = num(s.args[11]);
    return {
      id: `site_${encodeGuid(str(s.args[0]) ?? id)}`, global_id: str(s.args[0]), name: str(s.args[2]), long_name: str(s.args[7]),
      ref_latitude_deg: Semantics.compound(s.args[9]), ref_longitude_deg: Semantics.compound(s.args[10]),
      ref_elevation_m: elev != null ? r6(elev * this.lenM) : null, address: this.#address(s.args[13]),
    };
  }

  building(bid) {
    const b = bid ? this.m.e.get(bid) : null;
    if (!b) return null;
    const e1 = num(b.args[9]), e2 = num(b.args[10]);
    return {
      id: `building_${encodeGuid(str(b.args[0]) ?? bid)}`, global_id: str(b.args[0]), name: str(b.args[2]), long_name: str(b.args[7]),
      elevation_of_ref_height_m: e1 != null ? r6(e1 * this.lenM) : null, elevation_of_terrain_m: e2 != null ? r6(e2 * this.lenM) : null,
      address: this.#address(b.args[11]),
    };
  }

  georeference() {
    const [, mc] = this.m.ofType('IFCMAPCONVERSION')[0] ?? [];
    if (!mc) return null;
    const crs = this.m.get(mc.args[1]);
    return { eastings: num(mc.args[2]), northings: num(mc.args[3]), orthogonal_height: num(mc.args[4]), x_axis_abscissa: num(mc.args[5]), x_axis_ordinate: num(mc.args[6]), scale: num(mc.args[7]), target_crs: str(crs?.args?.[0]) };
  }

  // ---- relations ------------------------------------------------------------------------------------------------------------
  #indexRelations() {
    const push = (map, k, v) => (map.get(k) ?? map.set(k, []).get(k)).push(v);
    this.typeOf = new Map(); // element id -> type id
    this.groupsOf = new Map(); // object id -> [group ids]
    this.groupMembers = new Map(); // group id -> [object ids]
    this.servesBuildings = []; // [system id, building id, rel guid]
    this.boundaries = []; // [space id, element id]
    this.coversSpaces = []; // [space id, covering id, rel guid]
    this.coversElements = []; // [element id, covering id, rel guid]
    this.classOf = new Map(); // object id -> [classification ref ids]
    this.connects = []; // [a, b, rel guid, props]
    this.nests = []; // [whole, part, rel guid]
    this.aggregates = []; // [whole, part, rel guid]
    for (const [, r] of this.m.e) {
      switch (r.type) {
        case 'IFCRELDEFINESBYTYPE':
          for (const o of r.args[4] ?? []) if (isRef(o) && isRef(r.args[5])) this.typeOf.set(o.ref, r.args[5].ref);
          break;
        case 'IFCRELASSIGNSTOGROUP':
        case 'IFCRELASSIGNSTOGROUPBYFACTOR':
          if (!isRef(r.args[6])) break;
          for (const o of r.args[4] ?? []) if (isRef(o)) { push(this.groupsOf, o.ref, r.args[6].ref); push(this.groupMembers, r.args[6].ref, o.ref); }
          break;
        case 'IFCRELSERVICESBUILDINGS':
          for (const b of r.args[5] ?? []) if (isRef(b) && isRef(r.args[4])) this.servesBuildings.push([r.args[4].ref, b.ref, str(r.args[0])]);
          break;
        case 'IFCRELSPACEBOUNDARY':
        case 'IFCRELSPACEBOUNDARY1STLEVEL':
        case 'IFCRELSPACEBOUNDARY2NDLEVEL':
          if (isRef(r.args[4]) && isRef(r.args[5])) this.boundaries.push([r.args[4].ref, r.args[5].ref]);
          break;
        case 'IFCRELCOVERSSPACES':
          for (const c of r.args[5] ?? []) if (isRef(c) && isRef(r.args[4])) this.coversSpaces.push([r.args[4].ref, c.ref, str(r.args[0])]);
          break;
        case 'IFCRELCOVERSBLDGELEMENTS':
          for (const c of r.args[5] ?? []) if (isRef(c) && isRef(r.args[4])) this.coversElements.push([r.args[4].ref, c.ref, str(r.args[0])]);
          break;
        case 'IFCRELASSOCIATESCLASSIFICATION':
          for (const o of r.args[4] ?? []) if (isRef(o) && isRef(r.args[5])) push(this.classOf, o.ref, r.args[5].ref);
          break;
        case 'IFCRELCONNECTSPATHELEMENTS':
          if (isRef(r.args[5]) && isRef(r.args[6])) this.connects.push([r.args[5].ref, r.args[6].ref, str(r.args[0]), { relating_connection: enumStr(r.args[10]), related_connection: enumStr(r.args[9]) }]);
          break;
        case 'IFCRELNESTS':
          for (const o of r.args[5] ?? []) if (isRef(o) && isRef(r.args[4])) this.nests.push([r.args[4].ref, o.ref, str(r.args[0])]);
          break;
        case 'IFCRELAGGREGATES':
          for (const o of r.args[5] ?? []) if (isRef(o) && isRef(r.args[4])) this.aggregates.push([r.args[4].ref, o.ref, str(r.args[0])]);
          break;
        default:
      }
    }
  }

  // ---- property sets and quantities ----------------------------------------------------------------------------------------
  /** IfcElementQuantity -> records keyed "<QtoSet>.<Name>", values converted to SI with the declared (or own) unit */
  quantitiesOfDefs(defIds) {
    const out = {};
    for (const d of defIds) {
      const def = this.m.e.get(d);
      if (def?.type !== 'IFCELEMENTQUANTITY') continue;
      const set = str(def.args[2]) ?? 'ElementQuantity';
      for (const qr of def.args[5] ?? []) {
        const q = this.m.get(qr);
        if (!q) continue;
        const raw = num(q.args[3]);
        if (raw == null) continue;
        const own = isRef(q.args[2]) ? this.#ownUnit(q.args[2]) : null;
        const kind = q.type.replace('IFCQUANTITY', '').toLowerCase(); // length, area, volume, count, weight, time
        const decl = kind === 'length' ? this.units.length : kind === 'area' ? this.units.area : kind === 'volume' ? this.units.volume : kind === 'weight' ? this.units.mass : null;
        const f = own?.toSi ?? (kind === 'count' || kind === 'time' ? 1 : decl?.to_si ?? null);
        const unit = { length: 'm', area: 'm2', volume: 'm3', weight: 'kg', count: 'count', time: 's' }[kind] ?? null;
        const status = f == null ? 'not_available' : own ? 'explicit' : kind === 'count' || kind === 'time' || decl?.status === 'explicit' ? 'explicit' : 'inferred';
        out[`${set}.${str(q.args[0])}`] = {
          value: f == null ? null : r6(raw * f), unit, status, method: q.type,
          source: `${set}.${str(q.args[0])}${own ? ` (own unit ${own.label})` : decl ? ` (${decl.unit})` : ''}`,
          raw: { value: raw, unit: own?.label ?? (kind === 'count' ? 'count' : decl?.unit ?? null) },
        };
      }
    }
    return out;
  }

  psetsOfDefs(defIds) {
    const out = {};
    for (const d of defIds) {
      const def = this.m.e.get(d);
      if (def?.type !== 'IFCPROPERTYSET') continue;
      const set = str(def.args[2]) ?? '_';
      for (const pr of def.args[4] ?? []) {
        const p = this.m.get(pr);
        if (!p) continue;
        let v = null;
        if (p.type === 'IFCPROPERTYSINGLEVALUE') v = unwrap(p.args[2]);
        else if (p.type === 'IFCPROPERTYENUMERATEDVALUE') v = unwrap((p.args[2] ?? [])[0]);
        if (v == null) continue;
        (out[set] ??= {})[String(p.args[0])] = typeof v === 'object' ? String(v) : v;
      }
    }
    return out;
  }

  // ---- classification -----------------------------------------------------------------------------------------------------
  classificationsOf(id) {
    const out = [];
    for (const r of this.classOf.get(id) ?? []) {
      const c = this.m.e.get(r);
      if (!c) continue;
      if (c.type === 'IFCCLASSIFICATIONREFERENCE') {
        let src = this.m.get(c.args[3]);
        for (let k = 0; k < 6 && src?.type === 'IFCCLASSIFICATIONREFERENCE'; k++) src = this.m.get(src.args[3]);
        out.push({ system: src?.type === 'IFCCLASSIFICATION' ? str(src.args[3]) : null, code: str(c.args[1]), name: str(c.args[2]) });
      } else if (c.type === 'IFCCLASSIFICATION') out.push({ system: str(c.args[3]), code: null, name: null });
    }
    return out;
  }

  // ---- materials ------------------------------------------------------------------------------------------------------------
  /**
   * Resolve a material association. Registers materials / sets in `reg` and returns keys.
   * @returns {{ materialKeys: string[], layerSetKey: string|null }}
   */
  resolveMaterial(assocRef, reg) {
    const out = { materialKeys: [], layerSetKey: null };
    const m = this.m.get(assocRef);
    if (!m) return out;
    const mat = (ref) => {
      const x = this.m.get(ref);
      if (x?.type !== 'IFCMATERIAL') return null;
      const key = `mat_${ref.ref}`;
      if (!reg.materials.has(key)) reg.materials.set(key, { key, sourceRef: `#${ref.ref}`, name: str(x.args[0]) ?? `material #${ref.ref}`, description: str(x.args[1]), category: str(x.args[2]), classifications: this.classificationsOf(ref.ref) });
      return key;
    };
    const set = (ref, kind, layers, name) => {
      const key = `mls_${ref.ref}`;
      if (!reg.layerSets.has(key)) reg.layerSets.set(key, { key, sourceRef: `#${ref.ref}`, ifcClass: this.m.get(ref).type, kind, name, layers });
      return key;
    };
    const layerSet = (ref) => {
      const ls = this.m.get(ref);
      if (ls?.type !== 'IFCMATERIALLAYERSET') return null;
      const layers = (ls.args[0] ?? []).map((lr) => {
        const l = this.m.get(lr);
        if (l?.type !== 'IFCMATERIALLAYER') return null;
        const t = num(l.args[1]);
        return { materialKey: isRef(l.args[0]) ? mat(l.args[0]) : null, thicknessM: t != null ? r6(t * this.lenM) : null, isVentilated: ((v) => (v === 'U' ? null : v ?? null))(unwrap(l.args[2])), name: str(l.args[3]), category: str(l.args[5]), priority: num(l.args[6]) };
      }).filter(Boolean);
      return set(ref, 'layer_set', layers, str(ls.args[1]));
    };
    switch (m.type) {
      case 'IFCMATERIAL': { const k = mat(assocRef); if (k) out.materialKeys.push(k); break; }
      case 'IFCMATERIALLAYERSETUSAGE': out.layerSetKey = layerSet(m.args[0]); break;
      case 'IFCMATERIALLAYERSET': out.layerSetKey = layerSet(assocRef); break;
      case 'IFCMATERIALLAYER': { const k = isRef(m.args[0]) ? mat(m.args[0]) : null; if (k) out.materialKeys.push(k); break; }
      case 'IFCMATERIALLIST': {
        const layers = (m.args[0] ?? []).map((r) => ({ materialKey: mat(r), thicknessM: null, name: null, category: null }));
        out.layerSetKey = set(assocRef, 'list', layers, null);
        break;
      }
      case 'IFCMATERIALCONSTITUENTSET': {
        const layers = (m.args[2] ?? []).map((cr) => { const c = this.m.get(cr); return c ? { materialKey: isRef(c.args[2]) ? mat(c.args[2]) : null, thicknessM: null, name: str(c.args[0]), category: str(c.args[4]) } : null; }).filter(Boolean);
        out.layerSetKey = set(assocRef, 'constituent_set', layers, str(m.args[0]));
        break;
      }
      case 'IFCMATERIALPROFILESETUSAGE':
      case 'IFCMATERIALPROFILESET': {
        const ps = m.type === 'IFCMATERIALPROFILESET' ? m : this.m.get(m.args[0]);
        const psRef = m.type === 'IFCMATERIALPROFILESET' ? assocRef : m.args[0];
        if (ps?.type !== 'IFCMATERIALPROFILESET') break;
        const layers = (ps.args[2] ?? []).map((pr) => { const p = this.m.get(pr); return p ? { materialKey: isRef(p.args[2]) ? mat(p.args[2]) : null, thicknessM: null, name: str(p.args[0]), category: str(p.args[5]), priority: num(p.args[4]) } : null; }).filter(Boolean);
        out.layerSetKey = set(psRef, 'profile_set', layers, str(ps.args[0]));
        break;
      }
      default:
    }
    // every material of a set also counts as a material of the element
    if (out.layerSetKey) for (const l of reg.layerSets.get(out.layerSetKey).layers) if (l.materialKey) out.materialKeys.push(l.materialKey);
    out.materialKeys = [...new Set(out.materialKeys)];
    return out;
  }

  // ---- types -------------------------------------------------------------------------------------------------------------------
  /** element type record (once per type id) */
  typeRecord(typeId, materialOf, reg) {
    const t = this.m.e.get(typeId);
    if (!t) return null;
    const guid = str(t.args[0]);
    const key = `type_${encodeGuid(guid ?? typeId)}`;
    if (reg.types.has(typeId)) return reg.types.get(typeId);
    const defs = (t.args[5] ?? []).filter(isRef).map((r) => r.ref);
    const psets = this.psetsOfDefs(defs);
    let operation = null, predefined = null;
    if (t.type === 'IFCDOORSTYLE') operation = enumStr(t.args[8]);
    else if (t.type === 'IFCWINDOWSTYLE') operation = enumStr(t.args[9]);
    else {
      predefined = enumStr(t.args[9]);
      if (t.type === 'IFCDOORTYPE' || t.type === 'IFCWINDOWTYPE') operation = enumStr(t.args[10]);
    }
    const mat = materialOf.get(typeId) ? this.resolveMaterial(materialOf.get(typeId), reg) : { materialKeys: [], layerSetKey: null };
    const rec = {
      key, typeId, globalId: guid, ifcClass: t.type, sourceRef: guid ?? `#${typeId}`, name: str(t.args[2]), kind: typeKind(t.type),
      predefinedType: predefined, operation, tag: str(t.args[7]), materialKeys: mat.materialKeys, layerSetKey: mat.layerSetKey,
      classifications: this.classificationsOf(typeId),
      measures: quantityMeasures(this.quantitiesOfDefs(defs), {}),
      properties: Object.keys(psets).length ? { psets } : undefined,
    };
    reg.types.set(typeId, rec);
    return rec;
  }

  // ---- systems ----------------------------------------------------------------------------------------------------------------
  systemRecord(gid, reg) {
    if (reg.systems.has(gid)) return reg.systems.get(gid);
    const g = this.m.e.get(gid);
    if (!g) return null;
    const kind = { IFCSYSTEM: 'system', IFCDISTRIBUTIONSYSTEM: 'distribution_system', IFCDISTRIBUTIONCIRCUIT: 'distribution_circuit', IFCBUILDINGSYSTEM: 'building_system', IFCELECTRICALCIRCUIT: 'distribution_circuit', IFCZONE: 'zone', IFCGROUP: 'group' }[g.type] ?? 'other';
    const guid = str(g.args[0]);
    const longName = g.type === 'IFCDISTRIBUTIONSYSTEM' || g.type === 'IFCDISTRIBUTIONCIRCUIT' || g.type === 'IFCZONE' ? str(g.args[5]) : g.type === 'IFCBUILDINGSYSTEM' ? str(g.args[6]) : null;
    const predefined = g.type === 'IFCDISTRIBUTIONSYSTEM' || g.type === 'IFCDISTRIBUTIONCIRCUIT' ? enumStr(g.args[6]) : g.type === 'IFCBUILDINGSYSTEM' ? enumStr(g.args[5]) : null;
    const psets = this.psetsOfDefs(this.m.defs?.get(gid) ?? []);
    const rec = {
      key: `sys_${encodeGuid(guid ?? gid)}`, gid, globalId: guid, ifcClass: g.type, sourceRef: guid ?? `#${gid}`, name: str(g.args[2]), longName,
      kind, predefinedType: predefined, objectType: str(g.args[4]), memberIds: this.groupMembers.get(gid) ?? [],
      properties: Object.keys(psets).length ? { psets } : undefined,
    };
    reg.systems.set(gid, rec);
    return rec;
  }
}

/**
 * Map IFC quantity records onto schema fields (explicit values win over geometry). Only names with an unambiguous
 * meaning are mapped; everything else stays in quantities under its <QtoSet>.<Name> key.
 * @param {Record<string, any>} q quantity records keyed "<Set>.<Name>"
 * @param {Record<string, string[]>} fieldNames schema field -> accepted quantity names, in order of preference
 */
export function quantityMeasures(q, fieldNames) {
  const out = {};
  for (const [field, names] of Object.entries(fieldNames)) {
    for (const n of names) {
      const hit = Object.entries(q).find(([k, v]) => k.endsWith(`.${n}`) && v.status !== 'not_available');
      if (hit) { out[field] = { ...hit[1], source: hit[1].source }; break; }
    }
  }
  return out;
}

/** schema field -> IFC quantity names, per bucket */
export const QUANTITY_FIELDS = {
  space: { area_written_m2: ['NetFloorArea', 'GrossFloorArea'], height_m: ['Height', 'FinishCeilingHeight'], volume_m3: ['NetVolume', 'GrossVolume'] },
  wall: { length_m: ['Length'], thickness_m: ['Width', 'Thickness'], height_m: ['Height'], net_side_area_m2: ['NetSideArea'], net_volume_m3: ['NetVolume'] },
  door: { width_m: ['Width'], height_m: ['Height'] },
  window: { width_m: ['Width'], height_m: ['Height'] },
  object: { net_volume_m3: ['NetVolume'] },
};
