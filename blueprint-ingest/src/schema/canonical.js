/** Explicit boundary for the two historical layouts named 0.3. */
import fs from 'node:fs';
import { SCHEMA_PATH } from '../config.js';
import { migrate, emptyBuilding, stableId, COLLECTIONS } from '../storage/building.js';

const schema = JSON.parse(fs.readFileSync(SCHEMA_PATH, 'utf8'));
const defs = schema.$defs;
const names = { levels: 'level', units: 'unit', rooms: 'room', walls: 'wall', openings: 'opening', objects: 'object', texts: 'text', edges: 'edge', types: 'type_definition', systems: 'system', schedules: 'schedule' };
const allowed = name => new Set([...Object.keys(defs[name]?.properties ?? {}), ...(defs[name]?.allOf ? Object.keys(defs.entity.properties) : [])]);
function clean(name, value) {
  const keys = allowed(name);
  return Object.fromEntries(Object.entries(value).filter(([key]) => keys.has(key)));
}

export function toCanonical(input) {
  if (input.building_id && Array.isArray(input.drawings)) {
    if (input.drawing) throw new Error('Ambiguous mixed building and drawing document');
    return structuredClone(input);
  }
  if (input.schema_version === '0.2') return migrate(input);
  if (input.schema_version !== '0.3' || !input.drawing || !Array.isArray(input.element_types)) throw new Error('Unsupported building document layout');
  const original = structuredClone(input);
  const drawing = input.drawing;
  const file = drawing.read_from ?? drawing.file;
  const sid = stableId('source', file), did = stableId('drawing', drawing.file, drawing.page_index, drawing.part);
  const out = emptyBuilding(input.building?.global_id ? `ifc:${input.building.global_id}` : stableId('building', file));
  const ids = new Map();
  for (const collection of COLLECTIONS) for (const entity of input[collection === 'types' ? 'element_types' : collection] ?? []) {
    ids.set(entity.id, entity.global_id ? `ifc:${entity.global_id}` : `${did}:${entity.id}`);
  }
  const ref = entity => ({ source_document_id: sid, drawing_id: did, source_ref: entity.provenance?.source_ref ?? entity.global_id ?? entity.id, extractor: entity.provenance?.extractor ?? 'dialect-adapter' });
  const oneRefs = new Set(['level_id','unit_id','parent_room_id','room_id','wall_id','type_id','parent_id','superseded_by','from_id','to_id']);
  const listRefs = new Set(['room_ids','text_ids','system_ids','schedule_ids','component_ids','instance_ids','entity_ids','level_ids','boundary_ids']);
  function remap(value) {
    if (Array.isArray(value)) return value.map(remap);
    if (!value || typeof value !== 'object') return value;
    return Object.fromEntries(Object.entries(value).map(([k,v]) => [k,
      ['properties','provenance','details'].includes(k) ? v : oneRefs.has(k) ? ids.get(v) ?? v : listRefs.has(k) && Array.isArray(v) ? v.map(id => ids.get(id) ?? id) : remap(v)]));
  }
  const material = id => {
    const m = input.materials?.find(m => m.id === id);
    return { ...clean('material', m ?? {}), properties: { source_material_id: id, ...(m?.properties ?? {}) } };
  };
  for (const collection of COLLECTIONS) {
    if (collection === 'schedules') continue; // original generated schedules retained in the source archive
    for (const source of input[collection === 'types' ? 'element_types' : collection] ?? []) {
      const e = clean(names[collection], source), sr = ref(source);
      Object.assign(e, { id: ids.get(source.id), ifc_global_id: source.global_id ?? null, source_refs: [sr], source_drawing_id: did });
      e.properties = { ...source.properties, imported_v03: Object.fromEntries(Object.entries(source).filter(([k]) => !allowed(names[collection]).has(k))) };
      e.observations = [{ drawing_id: did, ...Object.fromEntries(['polygon_px','polyline_px','bbox_px','holes_px'].filter(k => k in source).map(k => [k,source[k]])) }];
      e.quantities = Object.entries(source.quantities ?? {}).filter(([,q]) => ['m','m2','m3','deg','count','1'].includes(q.unit)).map(([name,q]) => ({
        name, value: q.value ?? null, unit: q.unit,
        origin: { explicit:'source', calculated:'calculated', inferred:'estimated', not_available:'unknown' }[q.status] ?? 'unknown',
        method: q.method ?? null, source_refs: [{ ...sr, details: { original_record: q } }],
        original_value: typeof q.raw?.value === 'number' ? q.raw.value : null,
        original_unit: typeof q.raw?.unit === 'string' ? q.raw.unit : null,
      }));
      e.materials = (source.material_ids ?? []).map(material);
      const layers = input.material_layer_sets?.find(m => m.id === source.material_layer_set_id);
      if (layers?.kind === 'layer_set') e.material_layers = layers.layers.map(l => ({ material: material(l.material_id), thickness_m: l.thickness_m ?? null, is_ventilated: l.is_ventilated ?? null, properties: { original_layer: l } }));
      if (collection === 'types') { e.entity_kind = source.kind ?? 'other'; e.instance_ids = source.instance_ids ?? []; e.type_mark = source.tag ?? null; }
      if (collection === 'systems') { e.component_ids = source.member_ids ?? []; e.system_type = 'other'; }
      if (collection === 'rooms') {
        e.boundary_ids = source.boundary_element_ids ?? [];
        e.finishes = Object.entries(source.finishes ?? {}).filter(([,f]) => f.value != null).map(([surface,f]) => ({ surface, description: f.value, source_refs: [sr] }));
      }
      if (collection === 'openings') e.is_physical = ['door','window'].includes(e.kind);
      out[collection].push(remap(e));
    }
  }
  out.source_documents = [{ id:sid, file, format:file.split('.').pop().toLowerCase(), units:input.project?.units ?? {}, properties:{ imported_v03:{ dialect:'drawing-building', adapter_version:1, document:original, id_map:Object.fromEntries(ids) } } }];
  out.drawings = [{ ...clean('drawing', drawing), id:did, source_document_id:sid, level_ids:out.levels.map(l => l.id) }];
  out.project = { name:input.project?.name ?? null, building_name:input.building?.name ?? null, site_name:input.site?.name ?? null, ifc_schema:input.project?.schema_identifier ?? null, ifc_project_global_id:input.project?.global_id ?? null, ifc_building_global_id:input.building?.global_id ?? null, properties:{ imported_project:input.project ?? {} } };
  out.coverage = (input.coverage ?? []).map(c => ({ ...clean('coverage',c), drawing_id:did }));
  out.history = (input.history ?? []).map(h => remap(clean('history',h)));
  return out;
}
