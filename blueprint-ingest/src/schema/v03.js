/**
 * Schema v0.3: the ONE compatibility layer between v0.2 and v0.3.
 *
 *   migrateV02toV03(doc)      v0.2 document -> v0.3 document. Maps fields only: what v0.2 cannot know (materials, types,
 *                             systems, declared units) becomes empty / null / not_available, never a guess.
 *   syncQuantities(doc)       (re)writes the quantity record of every measured field, keeping explicit ones.
 *   validateRelationships(doc) every id reference resolves, ids are unique, quantity records agree with their fields.
 *   buildSchedules(doc), buildStatistics(doc)
 *
 * The IFC route produces v0.3 by building the v0.2 shape (documentBuilder), lifting it with migrateV02toV03 and then
 * enriching it from the bundle (src/schema/lift.js). So a migrated v0.2 file and a fresh extraction have the same shape.
 */

import { disciplineOf } from './ifcClasses.js';

export const SCHEMA_VERSION_V03 = '0.3';

/** collections of entities (all share the entity base) */
export const ENTITY_COLLECTIONS = ['levels', 'units', 'rooms', 'walls', 'openings', 'objects', 'texts', 'edges', 'element_types', 'materials', 'material_layer_sets', 'systems'];
/** every collection whose items have an id */
export const ID_COLLECTIONS = [...ENTITY_COLLECTIONS, 'schedules', 'relationships', 'statistics', 'sources'];
/** collections added by v0.3 */
export const V03_COLLECTIONS = ['element_types', 'materials', 'material_layer_sets', 'systems', 'schedules', 'relationships'];

const GUID = /^[0-9A-Za-z_$]{22}$/;
const ROUTE_FORMAT = { ifc: 'ifc', dxf: 'dxf', svg: 'svg', vector_pdf: 'pdf', raster: 'raster', vlm: 'raster', none: 'other' };

export const detectVersion = (doc) => (doc && typeof doc === 'object' ? String(doc.schema_version ?? '') || null : null);

/** unit declaration for "the document does not say" */
export const unknownUnit = (source = 'not recorded in the source document') => ({ unit: null, to_si: null, status: 'not_available', source });

/**
 * @param {any} v02 a v0.2 document (not modified)
 * @param {{ now?: string, note?: string }} [opts]
 * @returns {any} v0.3 document
 */
export function migrateV02toV03(v02, opts = {}) {
  if (detectVersion(v02) === SCHEMA_VERSION_V03) return structuredClone(v02);
  if (detectVersion(v02) !== '0.2') throw new Error(`migrateV02toV03: expected schema_version 0.2, got ${JSON.stringify(v02?.schema_version)}`);
  const d = structuredClone(v02);
  const now = opts.now ?? new Date().toISOString();
  const route = d.drawing?.route ?? 'none';
  const isIfc = route === 'ifc';
  const lenStatus = d.drawing?.scale?.source === 'vector_units'
    ? unknownUnit('v0.2 records that vector units were used, not which unit')
    : unknownUnit();

  const out = {
    schema_version: SCHEMA_VERSION_V03,
    project: { name: null, global_id: null, schema_identifier: null, application: null, units: { length: lenStatus, area: unknownUnit(), volume: unknownUnit(), angle: unknownUnit() } },
    site: null,
    building: null,
    drawing: d.drawing,
    sources: [{ id: 'source_1', file: d.drawing?.read_from ?? d.drawing?.file ?? 'unknown', role: 'primary', format: ROUTE_FORMAT[route] ?? 'other', schema_identifier: null, size_bytes: null, sha256: null, georeference: null }],
    levels: d.levels ?? [], units: d.units ?? [], rooms: d.rooms ?? [], walls: d.walls ?? [], openings: d.openings ?? [], objects: d.objects ?? [],
    element_types: [], materials: [], material_layer_sets: [], systems: [], schedules: [], relationships: [],
    texts: d.texts ?? [], edges: d.edges ?? [], coverage: d.coverage ?? [], history: d.history ?? [],
  };

  const entityV03 = (e) => {
    const ifcType = e.properties?.ifc_type;
    e.global_id ??= isIfc && GUID.test(String(e.provenance?.source_ref ?? '')) ? String(e.provenance.source_ref) : null;
    e.ifc_class ??= ifcType ? String(ifcType).toUpperCase() : null;
    e.classifications ??= [];
  };
  for (const k of ['levels', 'units', 'rooms', 'walls', 'openings', 'objects', 'texts', 'edges']) for (const e of out[k]) entityV03(e);
  for (const l of out.levels) if (l.long_name === undefined) l.long_name = l.properties?.long_name ?? null;
  for (const w of out.walls) { w.type_id ??= null; w.material_layer_set_id ??= null; w.material_ids ??= []; }
  for (const o of out.openings) { o.type_id ??= null; o.mark ??= null; o.operation ??= null; o.material_ids ??= []; }
  for (const o of out.objects) {
    o.discipline ??= disciplineOf(o.ifc_class, o.category);
    o.type_id ??= null; o.system_ids ??= []; o.material_ids ??= []; o.material_layer_set_id ??= null; o.host_id ??= null;
  }
  for (const r of out.rooms) { r.boundary_element_ids ??= []; }

  for (const cat of ['materials', 'element_types', 'systems', 'schedules', 'relationships']) {
    if (!out.coverage.some((c) => c.category === cat)) out.coverage.push({ category: cat, region: 'page', status: 'not_analysed', extractor: 'schema_migration', confidence: 0, note: 'not present in the v0.2 source document' });
  }
  out.history.push({ at: now, extractor: 'schema_migration', action: 'migrate', note: opts.note ?? 'schema v0.2 -> v0.3: fields mapped, nothing inferred; v0.3-only data (materials, types, systems, declared units) not available' });
  syncQuantities(out, { origin: 'migrated' });
  out.schedules = buildSchedules(out);
  out.statistics = buildStatistics(out);
  return out;
}

// =====================================================================================================
// quantities
// =====================================================================================================

/** measured fields per collection: field -> [unit, default method for a code-derived value, default source] */
const MEASURES = {
  rooms: {
    area_written_m2: ['m2', 'written area', null],
    area_computed_m2: ['m2', 'polygon area (holes subtracted) x m_per_px^2', 'polygon_px'],
    area_m2: ['m2', null, null], // explicit when it is the written area, else the computed one
    height_m: ['m', null, null],
    volume_m3: ['m3', 'area_m2 x height_m', 'area_m2, height_m'],
  },
  walls: {
    length_m: ['m', 'axis polyline length x m_per_px', 'polyline_px'],
    thickness_m: ['m', 'thickness_px x m_per_px (footprint / paired face lines)', 'properties.thickness_px'],
    height_m: ['m', null, null],
    net_side_area_m2: ['m2', null, null],
    net_volume_m3: ['m3', null, null],
  },
  openings: {
    width_m: ['m', 'width_px x m_per_px', 'properties.width_px'],
    height_m: ['m', null, null],
  },
  objects: {
    area_m2: ['m2', 'footprint polygon area x m_per_px^2', 'polygon_px'],
    net_volume_m3: ['m3', null, null],
  },
  units: {
    area_m2: ['m2', 'polygon area x m_per_px^2', 'polygon_px'],
  },
  levels: {
    elevation_m: ['m', null, null],
    height_m: ['m', 'distance to the next storey up', 'levels[].elevation_m'],
  },
};

const sameNum = (a, b) => (a == null && b == null) || (typeof a === 'number' && typeof b === 'number' && Math.abs(a - b) <= 1e-9 + 1e-6 * Math.max(Math.abs(a), Math.abs(b)));

/**
 * Write a quantity record for every non-null measured field. Records whose status is explicit (set by an extractor from
 * the source) are kept, and their field is held to their value. A null field has no record: null means not available.
 * @param {any} doc v0.3 document (modified in place)
 * @param {{ origin?: 'migrated'|'extracted' }} [o]
 */
export function syncQuantities(doc, o = {}) {
  const migrated = o.origin === 'migrated';
  for (const [coll, fields] of Object.entries(MEASURES)) {
    for (const e of doc[coll] ?? []) {
      const q = (e.quantities ??= {});
      for (const [field, [unit, method, source]] of Object.entries(fields)) {
        const v = e[field];
        const cur = q[field];
        if (cur && cur.status === 'explicit') { if (typeof cur.value === 'number') e[field] = cur.value; continue; }
        if (typeof v !== 'number' || !Number.isFinite(v)) { delete q[field]; continue; }
        if (cur && cur.status !== 'not_available' && sameNum(cur.value, v)) continue; // still describes the value
        q[field] = defaultQuantity(coll, field, e, v, unit, method, source, migrated, doc);
      }
      if (!Object.keys(q).length) delete e.quantities;
    }
  }
  return doc;
}

function defaultQuantity(coll, field, e, v, unit, method, source, migrated, doc) {
  const rec = (status, m, s) => ({ value: v, unit, status, method: m, source: s });
  if (coll === 'rooms' && field === 'area_written_m2') return rec('explicit', e.provenance?.extractor === 'vector_ifc' ? 'IFC quantity' : 'written area on the drawing', e.properties?.area_written_text_id ?? 'area_written_m2');
  if (coll === 'rooms' && field === 'area_m2') {
    if (e.area_written_m2 != null && sameNum(e.area_written_m2, v)) return { ...(e.quantities?.area_written_m2 ?? rec('explicit', 'written area', 'area_written_m2')), value: v };
    return rec('calculated', MEASURES.rooms.area_computed_m2[1], 'polygon_px');
  }
  if (coll === 'levels' && field === 'elevation_m') return rec(migrated ? 'calculated' : 'explicit', migrated ? 'migrated from v0.2 (origin not recorded)' : 'storey elevation', 'levels[].elevation_m');
  if (method) return rec('calculated', method, source);
  // fields whose origin only the extractor knows (heights, IFC quantities); a migrated value keeps v0.2's meaning
  return rec('calculated', migrated ? 'migrated from v0.2 (origin not recorded)' : 'derived by the extractor', `${coll}[].${field}`);
}

// =====================================================================================================
// relationship validation
// =====================================================================================================

/**
 * Check that every id reference resolves, that ids are unique, and that each quantity record agrees with its field.
 * @param {any} doc v0.3 document
 * @returns {string[]} error messages (empty = valid)
 */
export function validateRelationships(doc) {
  const errors = [];
  const where = new Map(); // id -> collection
  for (const coll of ID_COLLECTIONS) {
    for (const [i, e] of (doc[coll] ?? []).entries()) {
      if (e?.id == null) continue;
      if (where.has(e.id)) errors.push(`duplicate id "${e.id}" in ${coll}[${i}] (also in ${where.get(e.id)})`);
      else where.set(e.id, coll);
    }
  }
  for (const s of [doc.site, doc.building]) if (s?.id) { if (where.has(s.id)) errors.push(`duplicate id "${s.id}" (site/building)`); else where.set(s.id, 'site/building'); }
  const ref = (path, id, colls) => {
    if (id == null) return;
    const c = where.get(id);
    if (!c) errors.push(`${path}: "${id}" does not exist`);
    else if (colls && !colls.includes(c)) errors.push(`${path}: "${id}" is in ${c}, expected ${colls.join('|')}`);
  };
  const refs = (path, arr, colls) => (Array.isArray(arr) ? arr.forEach((id, k) => ref(`${path}[${k}]`, id, colls)) : undefined);

  for (const coll of ENTITY_COLLECTIONS) {
    for (const [i, e] of (doc[coll] ?? []).entries()) {
      const p = `${coll}[${i}]`;
      ref(`${p}.level_id`, e.level_id, ['levels']);
      ref(`${p}.superseded_by`, e.superseded_by, [coll]);
      ref(`${p}.unit_id`, e.unit_id, ['units']);
      ref(`${p}.parent_room_id`, e.parent_room_id, ['rooms']);
      ref(`${p}.room_id`, e.room_id, ['rooms']);
      refs(`${p}.room_ids`, e.room_ids, ['rooms']);
      ref(`${p}.wall_id`, e.wall_id, ['walls']);
      ref(`${p}.type_id`, e.type_id, ['element_types']);
      refs(`${p}.system_ids`, e.system_ids, ['systems']);
      refs(`${p}.material_ids`, e.material_ids, ['materials']);
      ref(`${p}.material_layer_set_id`, e.material_layer_set_id, ['material_layer_sets']);
      ref(`${p}.host_id`, e.host_id);
      refs(`${p}.text_ids`, e.text_ids, ['texts']);
      refs(`${p}.boundary_element_ids`, e.boundary_element_ids);
      refs(`${p}.instance_ids`, e.instance_ids);
      refs(`${p}.member_ids`, e.member_ids);
      if (coll === 'edges') ref(`${p}.from_id`, e.from_id);
      for (const [k, f] of Object.entries(e.finishes ?? {})) { refs(`${p}.finishes.${k}.covering_ids`, f?.covering_ids, ['objects']); refs(`${p}.finishes.${k}.material_ids`, f?.material_ids, ['materials']); }
      for (const [k, l] of (e.layers ?? []).entries()) ref(`${p}.layers[${k}].material_id`, l.material_id, ['materials']);
      for (const [k, q] of Object.entries(e.quantities ?? {})) {
        if (!(k in e)) continue; // an IFC quantity (QtoSet.Name), not bound to a field
        if (q.status === 'not_available' ? e[k] != null : !sameNum(q.value, e[k])) errors.push(`${p}.quantities.${k}: value ${q.value} (${q.status}) disagrees with ${k} = ${e[k]}`);
      }
    }
  }
  for (const [i, s] of (doc.schedules ?? []).entries()) for (const [j, en] of (s.entries ?? []).entries()) { ref(`schedules[${i}].entries[${j}].type_id`, en.type_id, ['element_types']); refs(`schedules[${i}].entries[${j}].instance_ids`, en.instance_ids); }
  for (const [i, r] of (doc.relationships ?? []).entries()) { ref(`relationships[${i}].from_id`, r.from_id); ref(`relationships[${i}].to_id`, r.to_id); }
  for (const [i, s] of (doc.statistics ?? []).entries()) ref(`statistics[${i}].scope.level_id`, s.scope?.level_id, ['levels']);
  return errors;
}

// =====================================================================================================
// schedules and statistics (derived, so status calculated)
// =====================================================================================================

const active = (e) => (e.status ?? 'active') === 'active';
const commonValue = (vals) => (vals.length && vals.every((v) => v != null && sameNum(v, vals[0])) ? vals[0] : null);

/**
 * Door / window schedules: one entry per type (instances grouped by type_id); without types, per instance mark.
 * The schedule is DERIVED from the instances (status calculated); no schedule is made when neither exists.
 */
export function buildSchedules(doc) {
  const types = new Map((doc.element_types ?? []).map((t) => [t.id, t]));
  const out = [];
  for (const kind of ['door', 'window']) {
    const inst = (doc.openings ?? []).filter((o) => active(o) && o.kind === kind);
    if (!inst.length) continue;
    const byType = inst.some((o) => o.type_id);
    const byMark = !byType && inst.some((o) => o.mark);
    if (!byType && !byMark) continue;
    const groups = new Map();
    for (const o of inst) { const k = byType ? (o.type_id ?? '') : (o.mark ?? ''); (groups.get(k) ?? groups.set(k, []).get(k)).push(o); }
    const entries = [...groups].map(([k, list]) => {
      const t = byType && k ? types.get(k) : null;
      return {
        mark: t ? (t.tag ?? null) : (byMark && k ? k : null), type_id: t ? t.id : null, name: t ? (t.name ?? null) : null,
        instance_ids: list.map((o) => o.id), count: list.length,
        width_m: t?.width_m ?? commonValue(list.map((o) => o.width_m)), height_m: t?.height_m ?? commonValue(list.map((o) => o.height_m)),
      };
    }).sort((a, b) => b.count - a.count || String(a.name ?? a.mark).localeCompare(String(b.name ?? b.mark)));
    out.push({
      id: `schedule_${kind}`, name: `${kind} schedule`, kind, status: 'calculated',
      method: byType ? 'instances grouped by type_id; width/height from the type, else the value all instances share' : 'instances grouped by mark',
      source: byType ? 'element_types (IfcRelDefinesByType)' : 'openings[].mark', entries,
    });
  }
  return out;
}

/**
 * Counts and sums with method, source and exactness. A count is exact when the category was analysed and not capped;
 * a sum is exact when every input is explicit and none is missing.
 */
export function buildStatistics(doc) {
  const stats = [];
  const cov = (cat) => (doc.coverage ?? []).find((c) => c.category === cat);
  const covExact = (cat) => { const c = cov(cat); return !!c && c.status === 'analysed' && !/cap/i.test(c.note ?? ''); };
  const add = (name, value, unit, status, method, source, exact, scope, extra = {}) => stats.push({ id: `stat_${stats.length + 1}`, name, value, unit, status, method, source, exact, ...(scope ? { scope } : {}), ...extra });
  const count = (name, coll, filter, covCat, scope) => {
    const n = (doc[coll] ?? []).filter((e) => active(e) && filter(e)).length;
    add(name, n, 'count', 'calculated', 'count of active entities', coll, covExact(covCat), scope);
  };
  count('count.levels', 'levels', () => true, 'levels', { collection: 'levels' });
  count('count.rooms', 'rooms', () => true, 'rooms', { collection: 'rooms' });
  count('count.walls', 'walls', () => true, 'walls', { collection: 'walls' });
  count('count.openings.door', 'openings', (o) => o.kind === 'door', 'doors', { collection: 'openings', category: 'door' });
  count('count.openings.window', 'openings', (o) => o.kind === 'window', 'windows', { collection: 'openings', category: 'window' });
  const cats = [...new Set((doc.objects ?? []).filter(active).map((o) => o.category))].sort();
  for (const c of cats) count(`count.objects.${c}`, 'objects', (o) => o.category === c, c === 'stair' ? 'stairs' : 'fixtures', { collection: 'objects', category: c });
  const classes = [...new Set(ENTITY_COLLECTIONS.flatMap((k) => (['rooms', 'walls', 'openings', 'objects'].includes(k) ? doc[k] ?? [] : [])).filter((e) => active(e) && e.ifc_class).map((e) => e.ifc_class))].sort();
  for (const cls of classes) {
    const n = ['rooms', 'walls', 'openings', 'objects'].reduce((s, k) => s + (doc[k] ?? []).filter((e) => active(e) && e.ifc_class === cls).length, 0);
    add(`count.ifc_class.${cls}`, n, 'count', 'calculated', 'count of active entities with this ifc_class', 'rooms, walls, openings, objects', covExact('ifc_classes'), { ifc_class: cls });
  }

  const sum = (name, coll, field, unit, scope, filter = () => true) => {
    const items = (doc[coll] ?? []).filter((e) => active(e) && filter(e));
    const vals = items.map((e) => e[field]).filter((v) => typeof v === 'number');
    const missing = items.length - vals.length;
    const allExplicit = items.length > 0 && missing === 0 && items.every((e) => e.quantities?.[field]?.status === 'explicit');
    const value = vals.length ? Math.round(vals.reduce((a, b) => a + b, 0) * 1000) / 1000 : null;
    add(name, value, unit, value == null ? 'not_available' : allExplicit ? 'explicit' : 'calculated', `sum of ${coll}[].${field}`, `${coll}[].${field}`, allExplicit, scope, { inputs: items.length, missing });
  };
  sum('sum.rooms.area_m2', 'rooms', 'area_m2', 'm2', { collection: 'rooms' });
  sum('sum.walls.length_m', 'walls', 'length_m', 'm', { collection: 'walls' });
  for (const lv of (doc.levels ?? []).filter(active)) {
    if ((doc.levels ?? []).length < 2) break;
    sum('sum.rooms.area_m2', 'rooms', 'area_m2', 'm2', { collection: 'rooms', level_id: lv.id }, (r) => r.level_id === lv.id);
  }
  // a sum's status is explicit only when every input is; a sum of explicit values is still computed by code
  for (const s of stats) if (s.status === 'explicit') { s.status = 'calculated'; s.method += ' (all inputs explicit)'; }
  return stats;
}
