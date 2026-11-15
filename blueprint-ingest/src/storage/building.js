/** Central v0.2 compatibility and building identity. Matches translator/building.py. */
import crypto from 'node:crypto';
import path from 'node:path';

export const COLLECTIONS = ['levels', 'units', 'rooms', 'walls', 'openings', 'objects', 'texts', 'edges', 'types', 'systems', 'schedules'];
const PIXELS = ['polygon_px', 'polyline_px', 'bbox_px', 'holes_px'];
const SINGLE = { level_id: 'levels', unit_id: 'units', parent_room_id: 'rooms', room_id: 'rooms', wall_id: 'walls', type_id: 'types', parent_id: null, superseded_by: null, from_id: null, to_id: null };
const LIST = { room_ids: 'rooms', text_ids: 'texts', system_ids: 'systems', schedule_ids: 'schedules', component_ids: null, instance_ids: null, entity_ids: null, level_ids: 'levels', boundary_ids: null };
export const stableId = (prefix, ...parts) => `${prefix}-${crypto.createHash('sha256').update(parts.map(p => p == null ? '' : String(p)).join('\n')).digest('hex').slice(0, 16)}`;
export const emptyBuilding = identity => ({ schema_version: '0.3', building_id: identity, project: {}, source_documents: [], drawings: [], ...Object.fromEntries(COLLECTIONS.map(c => [c, []])), relationships: [], coverage: [], history: [] });
const source = (file, group) => { file = file.replaceAll('\\', '/'); return { id: stableId('source', file), file, format: path.posix.extname(file).slice(1).toLowerCase() || 'unknown', source: group ?? null }; };
const equal = (a, b) => JSON.stringify(canonical(a)) === JSON.stringify(canonical(b));
const canonical = v => Array.isArray(v) ? v.map(canonical) : v && typeof v === 'object' ? Object.fromEntries(Object.keys(v).sort().map(k => [k, canonical(v[k])])) : v;
const union = (a, b) => [...a, ...b.filter(x => !a.some(y => equal(x, y)))];

function remap(v, ids) {
  if (Array.isArray(v)) return v.map(x => remap(x, ids));
  if (!v || typeof v !== 'object') return v;
  return Object.fromEntries(Object.entries(v).map(([k, x]) => [k,
    ['properties', 'details', 'provenance'].includes(k) ? x : k in SINGLE ? (ids.get(x) ?? x) : k in LIST && Array.isArray(x) ? x.map(i => ids.get(i) ?? i) : remap(x, ids)]));
}
function ifcGuid(e, drawing) {
  const candidate = e.ifc_global_id ?? e.properties?.global_id ?? (drawing.route === 'ifc' ? e.provenance?.source_ref : null);
  return candidate && /^[0-3][0-9A-Za-z_$]{21}$/.test(candidate) ? candidate : null;
}
export function migrate(document) {
  if (document.schema_version === '0.3') return structuredClone(document);
  if (document.schema_version !== '0.2') throw new Error(`Unsupported schema version: ${document.schema_version}`);
  const old = structuredClone(document), drawing = old.drawing;
  const src = source(drawing.read_from, drawing.source);
  const did = stableId('drawing', drawing.file, drawing.page_index, drawing.part);
  const out = emptyBuilding(stableId('building', src.file));
  out.source_documents.push(src);
  if (drawing.file !== src.file) out.source_documents.push(source(drawing.file, drawing.source));
  const ids = new Map();
  for (const c of COLLECTIONS) for (const e of old[c] ?? []) {
    const guid = ifcGuid(e, drawing); ids.set(e.id, guid ? `ifc:${guid}` : `${did}:${e.id}`);
  }
  for (const c of COLLECTIONS) for (let e of old[c] ?? []) {
    const guid = ifcGuid(e, drawing); e.id = ids.get(e.id); e = remap(e, ids);
    e.ifc_global_id = guid;
    e.source_refs = [{ source_document_id: src.id, drawing_id: did, source_ref: e.provenance.source_ref, extractor: e.provenance.extractor }];
    e.source_drawing_id = did;
    e.observations = [{ drawing_id: did, ...Object.fromEntries(PIXELS.filter(k => k in e).map(k => [k, e[k]])) }];
    const props = e.properties ?? {};
    e.ifc_class ??= props.ifc_class ?? props.ifc_type ?? null;
    for (const k of ['description', 'predefined_type', 'type_name']) if (!(k in e)) e[k] = props[k] ?? null;
    if (c === 'openings') e.is_physical ??= ['door', 'window'].includes(e.kind);
    e.quantities ??= [];
    for (const [field, value] of Object.entries(e)) {
      const unit = ['m3', 'm2', 'm'].find(u => field.endsWith(`_${u}`));
      if (!unit || typeof value !== 'number' || e.quantities.some(q => q.name === field)) continue;
      e.quantities.push({ name: field, value, unit, origin: field.includes('_written_') ? 'source' : field.includes('_computed_') ? 'calculated' : 'unknown', method: 'v0.2 migration; original provenance retained', source_refs: structuredClone(e.source_refs) });
    }
    out[c].push(e);
  }
  out.drawings = [{ ...drawing, id: did, source_document_id: src.id, level_ids: out.levels.map(e => e.id) }];
  out.coverage = old.coverage.map(c => ({ ...c, drawing_id: did }));
  out.history = remap(old.history, ids);
  return out;
}

export function mergeBuildings(documents, buildingId) {
  const docs = documents.map(migrate);
  if (!docs.length) throw new Error('At least one source is required');
  const out = docs[0]; out.building_id = buildingId ?? out.building_id;
  for (const doc of docs.slice(1)) {
    for (const [k, v] of Object.entries(doc.project)) {
      if (out.project[k] != null && !equal(out.project[k], v)) throw new Error(`Conflicting project fact: ${k}`);
      out.project[k] = v;
    }
    for (const c of ['source_documents', 'drawings', ...COLLECTIONS, 'relationships']) {
      const existing = new Map(out[c].map(e => [e.id, e]));
      for (const item of doc[c]) {
        if (!existing.has(item.id)) { out[c].push(item); existing.set(item.id, item); continue; }
        const prev = existing.get(item.id);
        for (const [k, v] of Object.entries(item)) {
          if (['source_refs', 'observations', 'quantities', 'level_ids'].includes(k)) prev[k] = union(prev[k] ?? [], v);
          else if ([...PIXELS, 'source_drawing_id', 'provenance', 'created_at'].includes(k)) continue;
          else if (prev[k] == null) prev[k] = v;
          else if (v != null && !equal(prev[k], v)) throw new Error(`Conflicting ${c}/${item.id}/${k}; reconcile sources explicitly`);
        }
      }
    }
    out.coverage = union(out.coverage, doc.coverage); out.history = union(out.history, doc.history);
  }
  delete out.statistics;
  return out;
}

export function referenceErrors(doc) {
  const errors = [], groups = Object.fromEntries(COLLECTIONS.map(c => [c, new Set(doc[c].map(e => e.id))]));
  const all = new Set([doc.building_id]);
  for (const c of [...COLLECTIONS, 'source_documents', 'drawings', 'relationships']) for (const e of doc[c]) {
    if (all.has(e.id)) errors.push(`Duplicate id ${e.id}`); all.add(e.id);
  }
  const entities = new Set([doc.building_id, ...COLLECTIONS.flatMap(c => [...groups[c]])]);
  const sources = new Set(doc.source_documents.map(s => s.id)), drawings = new Set(doc.drawings.map(d => d.id));
  function walk(v, path = '') {
    if (Array.isArray(v)) return v.forEach((x, i) => walk(x, `${path}/${i}`));
    if (!v || typeof v !== 'object') return;
    for (const [k, x] of Object.entries(v)) {
      if (['properties', 'details', 'provenance'].includes(k)) continue;
      const target = k in SINGLE ? (groups[SINGLE[k]] ?? entities) : k in LIST ? (groups[LIST[k]] ?? entities) : k === 'source_document_id' ? sources : ['drawing_id', 'source_drawing_id', 'source_sheet_id'].includes(k) ? drawings : null;
      if (target) for (const id of Array.isArray(x) ? x : [x]) if (id != null && !target.has(id)) errors.push(`${path}/${k}: unknown reference ${id}`);
      walk(x, `${path}/${k}`);
    }
  }
  walk(doc);
  for (const e of doc.openings) if (['doorway', 'void'].includes(e.kind) && e.is_physical === true) errors.push(`${e.id}: an unfilled opening is not a physical door/window`);
  return errors;
}

/** Only the compatibility boundary chooses between legacy frame merge and building merge. */
export function mergeCompatible(existing, incoming, config, opts, mergePage) {
  if (existing.schema_version === '0.2' && incoming.schema_version === '0.2') return mergePage(existing, incoming, config, opts);
  return { document: mergeBuildings([existing, incoming]), report: { notes: ['Merged source-scoped building entities; conflicting facts require explicit reconciliation'] } };
}
