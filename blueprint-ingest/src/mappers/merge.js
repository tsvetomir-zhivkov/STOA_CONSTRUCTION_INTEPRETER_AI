/**
 * Non-destructive merge of an incoming schema v0.2 document into an existing one (same drawing page, same pixel frame).
 *
 * Rules
 *  - nothing is ever deleted: a better duplicate SUPERSEDES the old entity (status 'superseded', superseded_by) so history stays visible
 *  - identity: same GlobalId = same entity; different GlobalIds are never the same entity; otherwise bbox IoU >= duplicateIou,
 *    same kind/category and same level. Twins are existing entities only (never another entity of the incoming document)
 *  - same entity and not clearly better -> existing wins, only its EMPTY fields are filled
 *  - clearly better = confidence higher by at least `supersedeMargin`
 *  - texts: identical text at the same place is the same text; a role upgrades a text that has none
 *  - levels: same name = same level; units: same label = same unit
 *  - scale: vector_units > scale_note > dimension_lines > door_width > none; same source -> higher confidence
 *  - coverage: the stronger claim wins per (category, region)
 *  - every id referenced inside incoming entities is remapped when its target was renamed or deduplicated
 *  - incoming history is appended, plus one 'merge' entry; metres are recomputed afterwards by code
 *  - schema v0.3 (both documents 0.3): element types, materials, layer sets and systems are the same when their id or
 *    GlobalId is; their instance / member lists are united. Relationships are the same when kind, from and to are.
 *    Project, site, building and sources only fill what is missing. Quantity records, schedules and statistics are rebuilt.
 */

import { bboxIou, round } from '../geometry/geom.js';
import { FrameMismatchError } from '../util/errors.js';
import { recomputeMetres } from '../storage/metres.js';
import { syncQuantities, buildSchedules, buildStatistics } from '../schema/v03.js';

const SCALE_RANK = { vector_units: 5, scale_note: 4, dimension_lines: 3, door_width: 2, none: 0 };
const COV_RANK = { analysed: 3, partial: 2, not_analysed: 1 };
const COLLECTIONS = ['levels', 'units', 'rooms', 'walls', 'openings', 'objects', 'texts', 'edges'];
const GEOMETRIC = ['walls', 'rooms', 'openings', 'objects'];
/** v0.3 collections whose identity is the id (built from GlobalId / express id) */
const V03_BY_ID = ['element_types', 'materials', 'material_layer_sets', 'systems'];

/** metre fields recomputeMetres() derives from pixels: never copied between documents */
const CODE_OWNED = new Set(['polygon_m', 'holes_m', 'area_m2', 'area_computed_m2', 'volume_m3', 'length_m', 'thickness_m', 'width_m']);

const clone = (x) => structuredClone(x);
const active = (e) => (e.status ?? 'active') === 'active';
const norm = (s) => String(s ?? '').trim().toLowerCase();

/**
 * @typedef {Object} MergeReport
 * @property {string[]} added
 * @property {string[]} superseded
 * @property {string[]} enriched
 * @property {string[]} skipped
 * @property {string[]} coverageUpdated  "category@region" keys
 * @property {boolean} scaleChanged
 * @property {string[]} notes
 */

/**
 * @param {any} existing
 * @param {any} incoming
 * @param {{ merge: { duplicateIou:number, supersedeMargin:number, onFrameMismatch:'error'|'sibling'|'reproject' } }} cfg
 * @param {{ now?: string }} [opts]
 * @returns {{ document: any, report: MergeReport }}
 */
export function mergeDocuments(existing, incoming, cfg, opts = {}) {
  return mergeCompatible(existing, incoming, cfg, opts, mergePageDocuments);
}

function mergePageDocuments(existing, incoming, cfg, opts = {}) {
  const now = opts.now ?? new Date().toISOString();
  const doc = clone(existing);
  const inc = clone(incoming);
  const v03 = doc.schema_version === '0.3';
  if (v03 !== (inc.schema_version === '0.3')) throw new Error(`mergeDocuments: schema ${doc.schema_version} and ${inc.schema_version} cannot be merged (migrate first)`);
  const ALL = v03 ? [...COLLECTIONS, ...V03_BY_ID] : COLLECTIONS;
  for (const k of ALL) { doc[k] ??= []; inc[k] ??= []; }
  doc.coverage ??= []; doc.history ??= []; inc.coverage ??= []; inc.history ??= [];
  /** @type {MergeReport} */
  const report = { added: [], superseded: [], enriched: [], skipped: [], coverageUpdated: [], scaleChanged: false, notes: [] };

  alignFrame(doc, inc, cfg.merge.onFrameMismatch, report);

  const idMap = new Map(); // incoming id -> final id
  const usedIds = new Set();
  for (const k of ALL) for (const e of doc[k]) usedIds.add(e.id);
  if (v03) for (const k of ['relationships', 'schedules', 'statistics', 'sources']) for (const e of doc[k] ?? []) usedIds.add(e.id);
  const freshId = (id) => {
    let nid = id;
    for (let n = 2; usedIds.has(nid); n++) nid = `${id}_${n}`;
    usedIds.add(nid);
    return nid;
  };
  // Three phases, so that every id reference copied out of the incoming document is remapped exactly once and only after
  // EVERY identity is known: (1) decide, per incoming entity, add / fill its twin / supersede its twin, and fix the final id;
  // (2) remap all references inside the incoming entities; (3) apply. Copying a reference before (2) (as fillEmpty did)
  // leaves an incoming id that no longer exists; remapping twice can chain one id into another.
  /** @type {Array<{ key: string, el: any, op: 'add'|'fill'|'supersede', twin?: any, nid?: string }>} */
  const plan = [];
  const planAdd = (key, el) => { const nid = freshId(el.id); idMap.set(el.id, nid); plan.push({ key, el, op: 'add', nid }); };
  const planFill = (key, el, twin) => { idMap.set(el.id, twin.id); plan.push({ key, el, op: 'fill', twin }); };

  // ---- (1a) levels / units: identity by name / label ---------------------------------------------------------------------------
  for (const [key, field] of [['levels', 'name'], ['units', 'label']]) {
    for (const el of inc[key]) {
      const twin = doc[key].find((x) => active(x) && norm(x[field]) === norm(el[field]));
      if (twin) planFill(key, el, twin); else planAdd(key, el);
    }
  }

  // ---- (1b) texts ----------------------------------------------------------------------------------------------------------------
  for (const t of inc.texts) {
    const twin = doc.texts.find((x) => x.text === t.text && x.bbox_px && t.bbox_px && bboxIou(x.bbox_px, t.bbox_px) >= cfg.merge.duplicateIou);
    if (twin) planFill('texts', t, twin); else planAdd('texts', t);
  }

  // ---- (1c) geometric entities ---------------------------------------------------------------------------------------------------
  // Identity: the same GlobalId is the same element; two DIFFERENT GlobalIds are never the same element, however well their
  // boxes overlap (an IFC building shares one pixel frame across storeys, so stacked slabs / walls have IoU 1). Without a
  // GlobalId on both sides, the box decides, but only on the same level. Twins are searched among the EXISTING entities
  // only: the incoming document is one extraction whose entities are distinct by construction.
  // A human correction changes kind/category on purpose: it must still find (and supersede) its uncorrected twin.
  const sameKind = (key, a, b) => (b.provenance?.extractor === 'human' ? true : key === 'openings' ? a.kind === b.kind : key === 'objects' ? a.category === b.category : true);
  const claimed = new Set(); // existing entities an incoming one supersedes (no longer active for matching)
  const live = (ex) => active(ex) && !claimed.has(ex);
  for (const key of GEOMETRIC) {
    const byGuid = new Map();
    for (const ex of doc[key]) if (ex.global_id && !byGuid.has(ex.global_id)) byGuid.set(ex.global_id, ex);
    for (const el of inc[key]) {
      let twin = el.global_id ? byGuid.get(el.global_id) ?? null : null;
      if (twin && !live(twin)) twin = null;
      const box = bboxFor(el);
      if (!twin && box) {
        const lvl = el.level_id != null ? remapId(idMap, el.level_id) : null;
        let best = 0;
        for (const ex of doc[key]) {
          if (!live(ex) || !sameKind(key, ex, el)) continue;
          if (el.global_id && ex.global_id && el.global_id !== ex.global_id) continue;
          if (lvl != null && ex.level_id != null && ex.level_id !== lvl) continue;
          const exBox = bboxFor(ex);
          if (!exBox) continue;
          const iou = bboxIou(exBox, box);
          if (iou >= cfg.merge.duplicateIou && iou > best) { best = iou; twin = ex; }
        }
      }
      if (!twin) { planAdd(key, el); continue; }
      const better = (el.confidence ?? 0) >= (twin.confidence ?? 0) + cfg.merge.supersedeMargin;
      if (!better) { planFill(key, el, twin); continue; }
      const nid = freshId(el.id);
      idMap.set(el.id, nid);
      claimed.add(twin);
      plan.push({ key, el, op: 'supersede', twin, nid });
    }
  }

  // ---- (1d) v0.3: types, materials, layer sets, systems (identity = id or GlobalId) ------------------------------------------------
  if (v03) {
    for (const key of V03_BY_ID) {
      for (const el of inc[key]) {
        const twin = doc[key].find((x) => x.id === el.id || (el.global_id && x.global_id === el.global_id));
        if (twin) planFill(key, el, twin); else planAdd(key, el);
      }
    }
  }

  // ---- (2) remap every reference inside the incoming entities, once ------------------------------------------------------------------
  const remap = (id) => remapId(idMap, id);
  for (const key of ALL) for (const el of inc[key]) remapRefs(el, remap);

  // ---- (3) apply -------------------------------------------------------------------------------------------------------------------
  const push = (key, el, nid) => { doc[key].push({ ...el, id: nid }); report.added.push(nid); };
  for (const { key, el, op, twin, nid } of plan) {
    if (op === 'add') { push(key, el, nid); continue; }
    if (op === 'fill') {
      if (key === 'texts' && !twin.role && el.role) {
        twin.role = el.role;
        if (el.properties?.value && !twin.properties?.value) twin.properties = { ...(twin.properties ?? {}), value: el.properties.value };
        report.enriched.push(twin.id);
        continue;
      }
      let changed = false;
      if (V03_BY_ID.includes(key)) {
        for (const f of ['instance_ids', 'member_ids']) {
          if (!Array.isArray(el[f])) continue;
          const before = twin[f]?.length ?? 0;
          twin[f] = [...new Set([...(twin[f] ?? []), ...el[f]])];
          if (twin[f].length !== before) changed = true;
        }
      }
      if (fillEmpty(twin, el) || changed) report.enriched.push(twin.id); else report.skipped.push(el.id);
      continue;
    }
    // supersede
    const merged = clone(el);
    if (key === 'rooms') {
      if (merged.type === 'unknown' && twin.type !== 'unknown') { // never lose a resolved name
        merged.type = twin.type; merged.is_outdoor = twin.is_outdoor; merged.subtype ??= twin.subtype;
        if (twin.properties?.name_resolution) merged.properties = { ...(merged.properties ?? {}), name_resolution: clone(twin.properties.name_resolution) };
      }
      for (const f of ['name', 'number', 'label', 'area_written_m2', 'unit_id', 'parent_room_id', 'height_m']) merged[f] ??= twin[f] ?? null;
      merged.text_ids = [...new Set([...(twin.text_ids ?? []), ...(merged.text_ids ?? [])])];
    }
    push(key, merged, nid);
    twin.status = 'superseded';
    twin.superseded_by = nid;
    report.superseded.push(twin.id);
  }

  // ---- edges (from / to already remapped) --------------------------------------------------------------------------------------------
  for (const e of inc.edges) {
    if (doc.edges.some((x) => active(x) && x.kind === e.kind && x.from_id === e.from_id && (x.to ?? null) === (e.to ?? null))) { report.skipped.push(e.id); continue; }
    const nid = freshId(e.id);
    idMap.set(e.id, nid);
    push('edges', e, nid);
  }

  // ---- v0.3: relationships, project / site / building / sources --------------------------------------------------------------------
  if (v03) {
    const relKey = (r) => `${r.kind}|${r.from_id}|${r.to_id}`;
    const have = new Set(doc.relationships.map(relKey));
    for (const r of inc.relationships ?? []) {
      const nr = { ...clone(r), from_id: remap(r.from_id), to_id: remap(r.to_id) };
      if (have.has(relKey(nr))) continue;
      have.add(relKey(nr));
      nr.id = freshId(r.id);
      doc.relationships.push(nr);
      report.added.push(nr.id);
    }
    const fillObj = (a, b) => { for (const [k, v] of Object.entries(b ?? {})) if (a[k] == null && v != null) a[k] = clone(v); };
    fillObj(doc.project, inc.project);
    for (const [k, u] of Object.entries(inc.project?.units ?? {})) if (doc.project.units[k]?.status === 'not_available' && u.status !== 'not_available') doc.project.units[k] = clone(u);
    if (!doc.site && inc.site) doc.site = clone(inc.site); else if (doc.site && inc.site) fillObj(doc.site, inc.site);
    if (!doc.building && inc.building) doc.building = clone(inc.building); else if (doc.building && inc.building) fillObj(doc.building, inc.building);
    for (const src of inc.sources ?? []) {
      const twin = doc.sources.find((x) => x.file === src.file && (x.sha256 ?? null) === (src.sha256 ?? null));
      if (twin) { fillObj(twin, src); continue; }
      doc.sources.push({ ...clone(src), id: freshId(src.id), role: 'merged' });
    }
  }

  // ---- scale (lives in drawing.scale) ---------------------------------------------------------------------------------------------------
  const a = doc.drawing.scale ?? { m_per_px: null, source: 'none', confidence: 0 }, b = inc.drawing.scale;
  if (b) {
    const rankA = SCALE_RANK[a.source] ?? 0, rankB = SCALE_RANK[b.source] ?? 0;
    const incomingWins = b.m_per_px != null && (a.m_per_px == null || rankB > rankA || (rankB === rankA && b.confidence > a.confidence + 1e-9 && b.m_per_px !== a.m_per_px));
    if (incomingWins) {
      doc.drawing.scale = clone(b);
      report.scaleChanged = true;
      report.notes.push(`scale changed ${a.source} -> ${b.source}; all metre fields recomputed`);
    }
  }

  // ---- coverage -------------------------------------------------------------------------------------------------------------------------
  for (const c of inc.coverage) {
    const regionKey = JSON.stringify(c.region);
    const ex = doc.coverage.find((x) => x.category === c.category && JSON.stringify(x.region) === regionKey);
    const tag = `${c.category}@${c.region === 'page' ? 'page' : regionKey}`;
    if (!ex) { doc.coverage.push(clone(c)); report.coverageUpdated.push(tag); continue; }
    const ra = COV_RANK[ex.status] ?? 0, rb = COV_RANK[c.status] ?? 0;
    if (rb > ra || (rb === ra && c.confidence > ex.confidence + 0.05)) { Object.assign(ex, clone(c)); report.coverageUpdated.push(tag); }
  }

  // ---- drawing metadata: only fill what is missing -------------------------------------------------------------------------------------------
  const d = doc.drawing, id = inc.drawing;
  for (const f of ['page_index', 'page_count', 'part', 'translator_version']) if (d[f] == null && id[f] != null) d[f] = id[f];
  if (!d.image && id.image) d.image = clone(id.image);
  if (d.image && id.image && d.image.dpi == null && id.image.dpi != null) d.image.dpi = id.image.dpi;
  if (!d.transforms?.length && id.transforms?.length) d.transforms = clone(id.transforms);

  // ---- history ----------------------------------------------------------------------------------------------------------------------------------
  const changed = report.added.length || report.superseded.length || report.enriched.length || report.coverageUpdated.length || report.scaleChanged;
  if (changed) {
    for (const h of inc.history) doc.history.push({ ...clone(h), ...(h.entity_ids ? { entity_ids: [...new Set(h.entity_ids.map(remap))] } : {}) }); // ids as they are in THIS file
    const ids = [...report.added, ...report.superseded];
    doc.history.push({
      at: now, extractor: inc.history[0]?.extractor ?? 'merge', action: 'merge',
      entity_ids: ids.slice(0, 500),
      note: `${report.added.length} added, ${report.superseded.length} superseded, ${report.enriched.length} enriched, ${report.coverageUpdated.length} coverage entries updated${report.scaleChanged ? ', scale changed' : ''}${ids.length > 500 ? `; entity_ids lists the first 500 of ${ids.length}` : ''}`,
    });
  }

  recomputeMetres(doc);
  if (v03) {
    syncQuantities(doc, { origin: 'extracted' });
    doc.schedules = buildSchedules(doc);
    doc.statistics = buildStatistics(doc);
  }
  return { document: doc, report };
}

const remapId = (idMap, id) => (id != null && idMap.has(id) ? idMap.get(id) : id);

/** id fields of an entity (the same set validateRelationships checks) */
const REF_FIELDS = ['level_id', 'unit_id', 'parent_room_id', 'room_id', 'wall_id', 'superseded_by', 'from_id', 'to', 'type_id', 'material_layer_set_id', 'host_id'];
const REF_LISTS = ['text_ids', 'room_ids', 'system_ids', 'material_ids', 'boundary_element_ids', 'instance_ids', 'member_ids'];

/** Rewrite every id reference inside one entity (in place). */
function remapRefs(el, remap) {
  for (const f of REF_FIELDS) if (el[f] != null) el[f] = remap(el[f]);
  for (const f of REF_LISTS) if (Array.isArray(el[f])) el[f] = [...new Set(el[f].map(remap))];
  for (const fin of Object.values(el.finishes ?? {})) if (fin) { fin.covering_ids = [...new Set((fin.covering_ids ?? []).map(remap))]; fin.material_ids = [...new Set((fin.material_ids ?? []).map(remap))]; }
  for (const l of el.layers ?? []) if (l.material_id) l.material_id = remap(l.material_id);
  if (el.properties?.area_written_text_id) el.properties.area_written_text_id = remap(el.properties.area_written_text_id);
}

/** Fill only EMPTY fields of `target` from `src`. Returns true when something changed. */
function fillEmpty(target, src) {
  let changed = false;
  const skip = new Set(['id', 'status', 'superseded_by', 'provenance', 'confidence', 'flags', 'properties', 'text_ids', 'room_ids', 'instance_ids', 'member_ids', 'quantities']);
  for (const [f, v] of Object.entries(src)) {
    if (skip.has(f) || CODE_OWNED.has(f)) continue;
    const empty = target[f] == null || (Array.isArray(target[f]) && target[f].length === 0 && f !== 'holes_px');
    if (empty && v != null && !(Array.isArray(v) && v.length === 0)) { target[f] = clone(v); changed = true; }
  }
  if (target.type === 'unknown' && src.type && src.type !== 'unknown') {
    target.type = src.type; target.is_outdoor = src.is_outdoor ?? target.is_outdoor;
    if (src.properties?.name_resolution) target.properties = { ...(target.properties ?? {}), name_resolution: clone(src.properties.name_resolution) };
    changed = true;
  }
  for (const f of ['text_ids', 'room_ids']) {
    if (!src[f]?.length) continue;
    const before = target[f]?.length ?? 0;
    target[f] = [...new Set([...(target[f] ?? []), ...src[f]])];
    if (target[f].length !== before) changed = true;
  }
  if (src.quantities) {
    for (const [k, q] of Object.entries(src.quantities)) {
      // a quantity record describes its field: copy it only together with a value the target did not have
      if (target.quantities?.[k] == null && (!(k in target) || target[k] === q.value)) { (target.quantities ??= {})[k] = clone(q); changed = true; }
    }
  }
  if (src.properties) {
    for (const [k, v] of Object.entries(src.properties)) {
      if (target.properties?.[k] == null && v != null) { (target.properties ??= {})[k] = clone(v); changed = true; }
    }
  }
  return changed;
}

function bboxFor(e) {
  if (e.bbox_px) return e.bbox_px;
  const poly = e.polygon_px ?? e.polyline_px;
  if (!poly?.length) return null;
  const xs = poly.map((p) => p[0]), ys = poly.map((p) => p[1]);
  return [Math.min(...xs), Math.min(...ys), Math.max(...xs), Math.max(...ys)];
}

/** Make the incoming document live in the existing pixel frame, or refuse. */
function alignFrame(doc, inc, policy, report) {
  const A = doc.drawing.image, B = inc.drawing.image;
  if (!A || !B || (A.width_px === B.width_px && A.height_px === B.height_px)) return;
  const aspect = (A.width_px / A.height_px) / (B.width_px / B.height_px);
  if (policy === 'reproject' && Math.abs(aspect - 1) < 0.01) {
    const f = A.width_px / B.width_px;
    const sp = (p) => [round(p[0] * f, 2), round(p[1] * f, 2), ...p.slice(2)];
    const sb = (b) => b.map((v) => round(v * f, 2));
    for (const k of COLLECTIONS) {
      for (const e of inc[k] ?? []) {
        if (e.polygon_px) e.polygon_px = e.polygon_px.map(sp);
        if (e.polyline_px) e.polyline_px = e.polyline_px.map(sp);
        if (e.holes_px) e.holes_px = e.holes_px.map((h) => h.map(sp));
        if (e.bbox_px) e.bbox_px = sb(e.bbox_px);
        if (e.properties?.thickness_px != null) e.properties.thickness_px = round(e.properties.thickness_px * f, 2);
        if (e.properties?.width_px != null) e.properties.width_px = round(e.properties.width_px * f, 2);
      }
    }
    for (const c of inc.coverage ?? []) if (Array.isArray(c.region)) c.region = sb(c.region);
    if (inc.drawing.scale?.m_per_px != null) inc.drawing.scale.m_per_px /= f;
    (doc.drawing.transforms ??= []).push({ kind: 'resize', from: `${B.width_px}x${B.height_px}`, to: `${A.width_px}x${A.height_px}`, scale_x: round(f, 6), scale_y: round(f, 6), note: 'incoming merge reprojected into this frame' });
    report.notes.push(`incoming frame ${B.width_px}x${B.height_px} reprojected to ${A.width_px}x${A.height_px} (x${round(f, 4)})`);
    return;
  }
  throw new FrameMismatchError(
    `Incoming pixel frame ${B.width_px}x${B.height_px} differs from the existing document's ${A.width_px}x${A.height_px}. Pixel coordinates are the source of truth, so they cannot be mixed. Re-run with onFrameMismatch='sibling' to write a separate file${Math.abs(aspect - 1) < 0.01 ? " or 'reproject' (same aspect ratio)" : ''}.`,
    { existing: A, incoming: B },
  );
}
