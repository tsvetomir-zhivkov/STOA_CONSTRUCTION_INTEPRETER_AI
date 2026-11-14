/**
 * Non-destructive merge of an incoming schema v0.2 document into an existing one (same drawing page, same pixel frame).
 *
 * Rules
 *  - nothing is ever deleted: a better duplicate SUPERSEDES the old entity (status 'superseded', superseded_by) so history stays visible
 *  - same entity (bbox IoU >= duplicateIou, same kind/category) and not clearly better -> existing wins, only its EMPTY fields are filled
 *  - clearly better = confidence higher by at least `supersedeMargin`
 *  - texts: identical text at the same place is the same text; a role upgrades a text that has none
 *  - levels: same name = same level; units: same label = same unit
 *  - scale: vector_units > scale_note > dimension_lines > door_width > none; same source -> higher confidence
 *  - coverage: the stronger claim wins per (category, region)
 *  - every id referenced inside incoming entities is remapped when its target was renamed or deduplicated
 *  - incoming history is appended, plus one 'merge' entry; metres are recomputed afterwards by code
 */

import { bboxIou, round } from '../geometry/geom.js';
import { FrameMismatchError } from '../util/errors.js';
import { recomputeMetres } from '../storage/metres.js';

const SCALE_RANK = { vector_units: 5, scale_note: 4, dimension_lines: 3, door_width: 2, none: 0 };
const COV_RANK = { analysed: 3, partial: 2, not_analysed: 1 };
const COLLECTIONS = ['levels', 'units', 'rooms', 'walls', 'openings', 'objects', 'texts', 'edges'];
const GEOMETRIC = ['walls', 'rooms', 'openings', 'objects'];

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
  const now = opts.now ?? new Date().toISOString();
  const doc = clone(existing);
  const inc = clone(incoming);
  for (const k of COLLECTIONS) { doc[k] ??= []; inc[k] ??= []; }
  doc.coverage ??= []; doc.history ??= []; inc.coverage ??= []; inc.history ??= [];
  /** @type {MergeReport} */
  const report = { added: [], superseded: [], enriched: [], skipped: [], coverageUpdated: [], scaleChanged: false, notes: [] };

  alignFrame(doc, inc, cfg.merge.onFrameMismatch, report);

  const idMap = new Map(); // incoming id -> final id
  const usedIds = new Set();
  for (const k of COLLECTIONS) for (const e of doc[k]) usedIds.add(e.id);
  const freshId = (id) => {
    let nid = id;
    for (let n = 2; usedIds.has(nid); n++) nid = `${id}_${n}`;
    usedIds.add(nid);
    return nid;
  };
  const addNew = (key, el) => {
    const nid = freshId(el.id);
    idMap.set(el.id, nid);
    doc[key].push({ ...el, id: nid });
    report.added.push(nid);
    return nid;
  };

  // ---- levels / units: identity by name / label -------------------------------------------------------------------------------
  for (const [key, field] of [['levels', 'name'], ['units', 'label']]) {
    for (const el of inc[key]) {
      const twin = doc[key].find((x) => active(x) && norm(x[field]) === norm(el[field]));
      if (!twin) { addNew(key, el); continue; }
      idMap.set(el.id, twin.id);
      if (fillEmpty(twin, el)) report.enriched.push(twin.id); else report.skipped.push(el.id);
    }
  }

  // ---- texts (other entities reference them) ------------------------------------------------------------------------------------
  for (const t of inc.texts) {
    const twin = doc.texts.find((x) => x.text === t.text && x.bbox_px && t.bbox_px && bboxIou(x.bbox_px, t.bbox_px) >= cfg.merge.duplicateIou);
    if (!twin) { addNew('texts', t); continue; }
    idMap.set(t.id, twin.id);
    if (!twin.role && t.role) { twin.role = t.role; if (t.properties?.value && !twin.properties?.value) twin.properties = { ...(twin.properties ?? {}), value: t.properties.value }; report.enriched.push(twin.id); }
    else if (fillEmpty(twin, t)) report.enriched.push(twin.id);
    else report.skipped.push(t.id);
  }

  // ---- geometric entities -------------------------------------------------------------------------------------------------------
  // a human correction changes kind/category on purpose: it must still find (and supersede) its uncorrected twin
  const sameKind = (key, a, b) => (b.provenance?.extractor === 'human' ? true : key === 'openings' ? a.kind === b.kind : key === 'objects' ? a.category === b.category : true);
  for (const key of GEOMETRIC) {
    for (const el of inc[key]) {
      const box = bboxFor(el);
      let twin = null, best = 0;
      if (box) {
        for (const ex of doc[key]) {
          if (!active(ex) || !sameKind(key, ex, el)) continue;
          const exBox = bboxFor(ex);
          if (!exBox) continue;
          const iou = bboxIou(exBox, box);
          if (iou >= cfg.merge.duplicateIou && iou > best) { best = iou; twin = ex; }
        }
      }
      if (!twin) { addNew(key, el); continue; }
      const better = (el.confidence ?? 0) >= (twin.confidence ?? 0) + cfg.merge.supersedeMargin;
      if (!better) {
        idMap.set(el.id, twin.id);
        if (fillEmpty(twin, el)) report.enriched.push(twin.id); else report.skipped.push(el.id);
        continue;
      }
      const merged = clone(el);
      if (key === 'rooms') {
        if (merged.type === 'unknown' && twin.type !== 'unknown') { // never lose a resolved name
          merged.type = twin.type; merged.is_outdoor = twin.is_outdoor; merged.subtype ??= twin.subtype;
          if (twin.properties?.name_resolution) merged.properties = { ...(merged.properties ?? {}), name_resolution: clone(twin.properties.name_resolution) };
        }
        for (const f of ['name', 'number', 'label', 'area_written_m2', 'unit_id', 'parent_room_id', 'height_m']) merged[f] ??= twin[f] ?? null;
        merged.text_ids = [...new Set([...(twin.text_ids ?? []), ...(merged.text_ids ?? [])])];
      }
      const nid = addNew(key, merged);
      twin.status = 'superseded';
      twin.superseded_by = nid;
      report.superseded.push(twin.id);
    }
  }

  // ---- edges --------------------------------------------------------------------------------------------------------------------
  for (const e of inc.edges) {
    const from = idMap.get(e.from_id) ?? e.from_id, to = e.to != null ? (idMap.get(e.to) ?? e.to) : null;
    if (doc.edges.some((x) => active(x) && x.kind === e.kind && x.from_id === from && (x.to ?? null) === to)) { report.skipped.push(e.id); continue; }
    addNew('edges', e);
  }

  // ---- remap references inside the entities we added ----------------------------------------------------------------------------------
  const remap = (id) => (id != null && idMap.has(id) ? idMap.get(id) : id);
  const addedSet = new Set(report.added);
  for (const key of COLLECTIONS) {
    for (const el of doc[key]) {
      if (!addedSet.has(el.id)) continue;
      for (const f of ['level_id', 'unit_id', 'parent_room_id', 'room_id', 'wall_id', 'superseded_by', 'from_id', 'to']) if (el[f] != null) el[f] = remap(el[f]);
      for (const f of ['text_ids', 'room_ids']) if (Array.isArray(el[f])) el[f] = [...new Set(el[f].map(remap))];
      if (el.properties?.area_written_text_id) el.properties.area_written_text_id = remap(el.properties.area_written_text_id);
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
  return { document: doc, report };
}

/** Fill only EMPTY fields of `target` from `src`. Returns true when something changed. */
function fillEmpty(target, src) {
  let changed = false;
  const skip = new Set(['id', 'status', 'superseded_by', 'provenance', 'confidence', 'flags', 'properties', 'text_ids', 'room_ids']);
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
