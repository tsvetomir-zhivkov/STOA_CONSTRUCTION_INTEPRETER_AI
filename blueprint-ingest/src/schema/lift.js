/**
 * Bundle -> schema v0.3 document.
 *
 *   v0.2 shape (documentBuilder, corrections, vocabulary)  --migrateV02toV03-->  v0.3 shape
 *   + bundle.bim (project, site, building, units, types, materials, layer sets, systems, relationships, coverage)
 *   + per-element v0.3 extras (element.v03: GlobalId, IFC class, type, materials, systems, mark, explicit measures...)
 *   -> metres recomputed (explicit values kept) -> quantity records -> schedules -> statistics
 *
 * Nothing here invents a value: a field the bundle does not carry stays null / empty, and coverage says what was analysed.
 */

import path from 'node:path';
import { migrateV02toV03, syncQuantities, buildSchedules, buildStatistics, unknownUnit } from './v03.js';
import { disciplineOf } from './ifcClasses.js';
import { recomputeMetres } from '../storage/metres.js';

const FORMAT = { ifc: 'ifc', dxf: 'dxf', svg: 'svg', pdf: 'pdf' };

/**
 * @param {any} doc02 v0.2 document built from `bundle`
 * @param {{ bundle: import('../extractors/bundle.js').Bundle, ids: Record<string, Map<string,string>>, inputFilePath: string, file?: { size: number|null, sha256: string|null }, now?: string }} ctx
 */
export function liftToV03(doc02, ctx) {
  const { bundle, ids } = ctx;
  const now = ctx.now ?? new Date().toISOString();
  const bim = bundle.bim ?? null;
  const d = migrateV02toV03(doc02, { now, note: `built as schema v0.3 from the ${bundle.format} extraction${bim ? ' (with IFC project, type, material and system data)' : ''}` });
  d.history[d.history.length - 1].extractor = 'schema_v0.3';
  d.history[d.history.length - 1].action = 'build';
  // the migration wrote "origin not recorded" records; this is a fresh extraction, so the records are rebuilt below
  for (const k of ['levels', 'units', 'rooms', 'walls', 'openings', 'objects']) for (const e of d[k]) delete e.quantities;

  // ---- project / site / building / sources ---------------------------------------------------------------------------------
  d.sources[0] = {
    id: 'source_1', file: path.basename(ctx.inputFilePath), role: 'primary', format: FORMAT[bundle.format] ?? 'other',
    schema_identifier: bim?.project?.schema_identifier ?? null, size_bytes: ctx.file?.size ?? null, sha256: ctx.file?.sha256 ?? null,
    georeference: bim?.georeference ?? null,
  };
  if (bim?.project) d.project = bim.project;
  else {
    // no IFC project: the length unit is what the page scale says (DXF: its declared $INSUNITS); nothing else is declared
    const sc = d.drawing.scale;
    if (sc?.m_per_px != null && sc.source !== 'none') {
      d.project.units.length = { unit: 'page pixel', to_si: sc.m_per_px, status: sc.source === 'vector_units' ? 'explicit' : 'calculated', source: `drawing.scale (${sc.source}${sc.note ? `: ${sc.note.split(' | ')[0]}` : ''})` };
    }
    if (bundle.declaredUnits) d.project.units = { ...d.project.units, ...bundle.declaredUnits };
    for (const k of ['area', 'volume', 'angle']) if (d.project.units[k].status === 'not_available') d.project.units[k].source = `a ${bundle.format.toUpperCase()} source declares no ${k} unit${k === 'angle' ? '' : ` (${k}s are derived by code in ${k === 'area' ? 'm2' : 'm3'})`}`;
    for (const c of d.coverage) {
      if (c.extractor !== 'schema_migration') continue;
      c.extractor = bundle.extractor.id;
      c.note = ['materials', 'element_types', 'systems'].includes(c.category) ? `a ${bundle.format.toUpperCase()} drawing carries no ${c.category.replace('_', ' ')}` : `not derived for the ${bundle.format.toUpperCase()} route`;
    }
  }
  if (bim) { d.site = bim.site ?? null; d.building = bim.building ?? null; }

  // ---- key -> id over everything the document will hold --------------------------------------------------------------------
  const keyToId = new Map();
  for (const m of Object.values(ids)) for (const [k, v] of m) keyToId.set(k, v);
  const byId = new Map();
  for (const k of ['levels', 'rooms', 'walls', 'openings', 'objects', 'texts', 'units']) for (const e of d[k]) byId.set(e.id, e);

  // ---- v0.3 collections from the bundle (only what this document's elements use) ----------------------------------------------
  const usedTypes = new Map(), usedMats = new Set(), usedSets = new Set();
  const typeById = new Map((bim?.types ?? []).map((t) => [t.key, t]));
  const setById = new Map((bim?.layerSets ?? []).map((s) => [s.key, s]));
  const systemsByKey = new Map((bim?.systems ?? []).map((s) => [s.key, s]));
  const systemMembers = new Map();
  let unresolvedRefs = 0;
  const unresolvedByClass = new Map(); // ifc class -> boundary references recorded in rooms[].properties.unresolved_references
  const bundleByKey = new Map();
  for (const k of ['rooms', 'walls', 'openings', 'objects']) for (const el of bundle[k] ?? []) bundleByKey.set(el.key, el);

  const elementsOf = (coll) => (coll === 'rooms' ? bundle.rooms : coll === 'walls' ? bundle.walls : coll === 'openings' ? bundle.openings : bundle.objects);
  for (const coll of ['rooms', 'walls', 'openings', 'objects']) {
    for (const el of elementsOf(coll)) {
      const id = ids[coll]?.get(el.key);
      const e = id ? byId.get(id) : null;
      if (!e) continue;
      const x = el.v03;
      if (!x) continue;
      if (x.globalId !== undefined) e.global_id = x.globalId;
      if (x.ifcClass) e.ifc_class = x.ifcClass;
      if (x.classifications?.length) e.classifications = x.classifications;
      if (coll === 'objects') e.discipline = disciplineOf(e.ifc_class, e.category);
      if (x.typeKey && typeById.has(x.typeKey)) {
        e.type_id = x.typeKey;
        (usedTypes.get(x.typeKey) ?? usedTypes.set(x.typeKey, []).get(x.typeKey)).push(e.id);
      }
      if (x.materialKeys?.length && 'material_ids' in e) { e.material_ids = [...new Set(x.materialKeys)]; x.materialKeys.forEach((m) => usedMats.add(m)); }
      if (x.layerSetKey && setById.has(x.layerSetKey) && 'material_layer_set_id' in e) { e.material_layer_set_id = x.layerSetKey; usedSets.add(x.layerSetKey); }
      if (coll === 'objects' && x.systemKeys?.length) e.system_ids = x.systemKeys.filter((k) => systemsByKey.has(k));
      for (const s of x.systemKeys ?? []) (systemMembers.get(s) ?? systemMembers.set(s, []).get(s)).push(e.id);
      if (coll === 'openings') { if (x.mark !== undefined) e.mark = x.mark; if (x.operation !== undefined) e.operation = x.operation; }
      if (coll === 'objects' && x.hostKey) { const h = keyToId.get(x.hostKey); if (h) e.host_id = h; else unresolvedRefs++; }
      if (x.roomKeys?.length && 'room_ids' in e) {
        const rids = x.roomKeys.map((k) => keyToId.get(k)).filter(Boolean);
        unresolvedRefs += x.roomKeys.length - rids.length;
        e.room_ids = [...new Set([...(e.room_ids ?? []), ...rids])];
      }
      if (coll === 'rooms' && (x.boundaryKeys?.length || x.unresolvedBoundaries?.length)) {
        const bids = [];
        const unresolved = (x.unresolvedBoundaries ?? []).map((u) => unresolvedReference('boundary_element_ids', u));
        for (const k of x.boundaryKeys ?? []) {
          const id = keyToId.get(k);
          if (id) { bids.push(id); continue; }
          // emitted by the extractor but not in this document (e.g. a v0.2-only filter): recorded, not dropped
          const t = bundleByKey.get(k)?.v03;
          unresolved.push(unresolvedReference('boundary_element_ids', { globalId: t?.globalId ?? null, ifcClass: t?.ifcClass ?? null, reason: 'not_in_document' }));
        }
        e.boundary_element_ids = [...new Set(bids)];
        if (unresolved.length) {
          (e.properties ??= {}).unresolved_references = unresolved;
          for (const u of unresolved) unresolvedByClass.set(u.ifc_class ?? 'unknown', (unresolvedByClass.get(u.ifc_class ?? 'unknown') ?? 0) + 1);
        }
      }
      if (coll === 'rooms' && x.finishes) {
        e.finishes = {};
        for (const [k, f] of Object.entries(x.finishes)) {
          if (!f) continue;
          const cov = (f.coveringKeys ?? []).map((c) => keyToId.get(c)).filter((c) => c && byId.get(c) && d.objects.includes(byId.get(c)));
          (f.materialKeys ?? []).forEach((m) => usedMats.add(m));
          e.finishes[k] = { value: f.value ?? null, status: f.status, source: f.source ?? null, covering_ids: cov, material_ids: [...new Set(f.materialKeys ?? [])] };
        }
      }
      // explicit measures read from the source win over code-derived ones
      for (const [field, q] of Object.entries(x.measures ?? {})) {
        if (!(field in e) || !q) continue;
        (e.quantities ??= {})[field] = q;
        e[field] = q.status === 'not_available' ? null : q.value;
      }
      if (x.rawQuantities && Object.keys(x.rawQuantities).length) {
        e.quantities = { ...(e.quantities ?? {}), ...x.rawQuantities };
        if (e.properties?.quantities) delete e.properties.quantities; // moved, with units, into quantities
      }
    }
  }

  // types
  for (const t of bim?.types ?? []) {
    if (!usedTypes.has(t.key)) continue;
    (t.materialKeys ?? []).forEach((m) => usedMats.add(m));
    if (t.layerSetKey) usedSets.add(t.layerSetKey);
    d.element_types.push({
      id: t.key, confidence: 1, provenance: { extractor: bundle.extractor.id, source_ref: t.sourceRef }, status: 'active', superseded_by: null, level_id: null, flags: [],
      ...(t.properties && Object.keys(t.properties).length ? { properties: t.properties } : {}),
      global_id: t.globalId ?? null, ifc_class: t.ifcClass, classifications: t.classifications ?? [],
      ...(t.measures && Object.keys(t.measures).length ? { quantities: t.measures } : {}),
      name: t.name ?? null, kind: t.kind, predefined_type: t.predefinedType ?? null, operation: t.operation ?? null, tag: t.tag ?? null,
      width_m: t.measures?.width_m?.value ?? null, height_m: t.measures?.height_m?.value ?? null,
      material_ids: [...new Set(t.materialKeys ?? [])], material_layer_set_id: t.layerSetKey && setById.has(t.layerSetKey) ? t.layerSetKey : null,
      instance_ids: usedTypes.get(t.key),
    });
  }
  // layer sets (their materials count as used)
  for (const s of bim?.layerSets ?? []) {
    if (!usedSets.has(s.key)) continue;
    s.layers.forEach((l) => l.materialKey && usedMats.add(l.materialKey));
    const total = s.kind === 'layer_set' && s.layers.length && s.layers.every((l) => typeof l.thicknessM === 'number') ? Math.round(s.layers.reduce((a, l) => a + l.thicknessM, 0) * 1e6) / 1e6 : null;
    d.material_layer_sets.push({
      id: s.key, confidence: 1, provenance: { extractor: bundle.extractor.id, source_ref: s.sourceRef }, status: 'active', superseded_by: null, level_id: null, flags: [],
      global_id: null, ifc_class: s.ifcClass, classifications: [],
      ...(total != null ? { quantities: { total_thickness_m: { value: total, unit: 'm', status: 'calculated', method: 'sum of IfcMaterialLayer.LayerThickness', source: `${s.ifcClass} #${s.sourceRef.replace(/^#/, '')}` } } } : {}),
      name: s.name ?? null, kind: s.kind,
      layers: s.layers.map((l) => ({ material_id: l.materialKey ?? null, thickness_m: l.thicknessM ?? null, name: l.name ?? null, category: l.category ?? null, is_ventilated: l.isVentilated ?? null, priority: Number.isInteger(l.priority) ? l.priority : null })),
      total_thickness_m: total,
    });
  }
  // materials
  for (const m of bim?.materials ?? []) {
    if (!usedMats.has(m.key)) continue;
    d.materials.push({
      id: m.key, confidence: 1, provenance: { extractor: bundle.extractor.id, source_ref: m.sourceRef }, status: 'active', superseded_by: null, level_id: null, flags: [],
      global_id: null, ifc_class: 'IFCMATERIAL', classifications: m.classifications ?? [], name: m.name, category: m.category ?? null, description: m.description ?? null,
    });
  }
  // a material referenced but not readable (should not happen): drop the reference rather than leave it dangling
  const matIds = new Set(d.materials.map((m) => m.id));
  for (const coll of ['walls', 'openings', 'objects', 'element_types']) for (const e of d[coll]) if (Array.isArray(e.material_ids)) e.material_ids = e.material_ids.filter((m) => matIds.has(m));
  for (const r of d.rooms) for (const f of Object.values(r.finishes ?? {})) if (f) f.material_ids = f.material_ids.filter((m) => matIds.has(m));
  for (const s of d.material_layer_sets) for (const l of s.layers) if (l.material_id && !matIds.has(l.material_id)) l.material_id = null;

  // systems: members are resolved against THIS document; members elsewhere are counted, not invented
  for (const s of bim?.systems ?? []) {
    const members = [...new Set([...(systemMembers.get(s.key) ?? []), ...(s.memberKeys ?? []).map((k) => keyToId.get(k)).filter(Boolean)])];
    if (!members.length) continue;
    const memberKeys = new Set(s.memberKeys ?? []);
    const resolvedKeys = [...memberKeys].filter((k) => keyToId.has(k)).length;
    d.systems.push({
      id: s.key, confidence: 1, provenance: { extractor: bundle.extractor.id, source_ref: s.sourceRef }, status: 'active', superseded_by: null, level_id: null, flags: [],
      ...(s.properties && Object.keys(s.properties).length ? { properties: s.properties } : {}),
      global_id: s.globalId ?? null, ifc_class: s.ifcClass, classifications: [],
      name: s.name ?? null, long_name: s.longName ?? null, kind: s.kind, predefined_type: s.predefinedType ?? null, object_type: s.objectType ?? null,
      member_ids: members, unresolved_member_count: Math.max(0, memberKeys.size - resolvedKeys),
    });
  }
  const sysIds = new Set(d.systems.map((s) => s.id));
  for (const o of d.objects) if (Array.isArray(o.system_ids)) o.system_ids = o.system_ids.filter((s) => sysIds.has(s));

  // relationships: both ends must be in this document
  let dropped = 0;
  const relKeys = new Set();
  for (const r of bim?.relationships ?? []) {
    const from = keyToId.get(r.fromKey) ?? (sysIds.has(r.fromKey) ? r.fromKey : null), to = keyToId.get(r.toKey) ?? (r.toKey === d.building?.id ? r.toKey : null);
    if (!from || !to || from === to) { dropped++; continue; }
    const k = `${r.kind}|${from}|${to}`;
    if (relKeys.has(k)) continue;
    relKeys.add(k);
    d.relationships.push({ id: `rel_${d.relationships.length + 1}`, kind: r.kind, from_id: from, to_id: to, provenance: { extractor: bundle.extractor.id, source_ref: r.sourceRef }, ...(r.properties ? { properties: r.properties } : {}) });
  }

  // ---- coverage of the v0.3 categories -----------------------------------------------------------------------------------------
  for (const c of bim?.coverage ?? bundle.coverageV03 ?? []) {
    const nBoundary = [...unresolvedByClass.values()].reduce((a, b) => a + b, 0);
    const note = c.category === 'relationships' && (dropped || unresolvedRefs || nBoundary)
      ? [
        c.note,
        dropped || unresolvedRefs ? `${dropped} relationship(s) and ${unresolvedRefs} reference(s) point outside this document (other building, or an element that is not emitted)` : null,
        nBoundary ? `${nBoundary} space-boundary reference(s) to elements not emitted, listed in rooms[].properties.unresolved_references (${[...unresolvedByClass].sort().map(([k, n]) => `${k} ${n}`).join(', ')})` : null,
      ].filter(Boolean).join('; ')
      : c.note;
    const entry = { category: c.category, region: 'page', status: c.status, extractor: bundle.extractor.id, confidence: c.confidence, ...(note ? { note } : {}) };
    const i = d.coverage.findIndex((x) => x.category === c.category);
    if (i >= 0) d.coverage[i] = entry; else d.coverage.push(entry);
  }

  recomputeMetres(d);
  syncQuantities(d, { origin: 'extracted' });
  d.schedules = buildSchedules(d);
  d.statistics = buildStatistics(d);
  return d;
}

/**
 * One structured record of a reference that is not in the document: the referencing field, the target's GlobalId and
 * IFC class, and why it is not there. It is stored in the referencing entity's properties.unresolved_references, so the
 * id list stays strictly valid and nothing is silently lost.
 * @param {string} field e.g. 'boundary_element_ids'
 * @param {{ globalId?: string|null, ifcClass?: string|null, expressId?: number, reason: string, detail?: string }} ref
 */
export function unresolvedReference(field, ref) {
  return {
    field, global_id: ref.globalId ?? null, ifc_class: ref.ifcClass ?? null,
    ...(ref.expressId != null ? { express_id: ref.expressId } : {}), reason: ref.reason, ...(ref.detail ? { detail: ref.detail } : {}),
  };
}

export { unknownUnit };
