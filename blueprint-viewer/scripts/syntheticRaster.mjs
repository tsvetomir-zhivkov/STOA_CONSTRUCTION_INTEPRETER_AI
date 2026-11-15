// A schema v0.3 drawing document in the shape the raster (CubiCasa) route is specified to emit: route raster, no scale.
// There is no raster route in this repository yet (translator/extract/raster is a stub), so verify.mjs uses this file.
//
// `names` sets the room types and the object category. With the defaults the document is valid against
// blueprint-ingest/schema/building.schema.v0.3.json. verify.mjs passes raw CubiCasa class names instead (living_room, hall,
// fixture/toilet) to check that the viewer's vocabulary maps a model's names onto the v0.3 enums.

const ext = (extractor, source_ref) => ({ extractor, source_ref });
const notAvailable = { unit: null, to_si: null, status: 'not_available', source: 'not recorded in the source document' };

export function syntheticRasterV03(names = {}) {
  const { living = 'living', outdoor = 'outdoor', entry = 'entry', category = 'toilet', subtype = 'toilet' } = names;
  const room = (id, type, ring, extra = {}) => ({
    id, status: 'active', name: null, number: null, type, subtype: null, label: null, is_outdoor: type === outdoor,
    unit_id: null, parent_room_id: null, level_id: null, confidence: 0.7, polygon_px: ring, polygon_m: null, holes_px: [],
    area_m2: null, area_written_m2: null, area_computed_m2: null, height_m: null, text_ids: [], flags: [],
    provenance: ext('cubicasa', id.replace('r', 'seg')), global_id: null, ifc_class: null, classifications: [], boundary_element_ids: [], ...extra,
  });
  const cov = (category, status, confidence) => ({ category, region: 'page', status, extractor: 'cubicasa', confidence });
  return {
    schema_version: '0.3',
    project: { name: null, global_id: null, schema_identifier: null, application: null, units: { length: notAvailable, area: notAvailable, volume: notAvailable, angle: notAvailable } },
    site: null,
    building: null,
    drawing: { file: 'synthetic/high_quality-0001.png', source: 'synthetic', read_from: 'synthetic/high_quality-0001.png', route: 'raster', page_index: 0, image: { width_px: 600, height_px: 450, dpi: null }, scale: { m_per_px: null, source: 'none', confidence: 0 }, transforms: [] },
    sources: [{ id: 'source_1', file: 'synthetic/high_quality-0001.png', role: 'primary', format: 'raster', schema_identifier: null, size_bytes: null, sha256: null, georeference: null }],
    levels: [], units: [], walls: [], openings: [], texts: [], edges: [],
    element_types: [], materials: [], material_layer_sets: [], systems: [], schedules: [], relationships: [],
    rooms: [
      room('r1', living, [[0, 0], [400, 0], [400, 300], [0, 300]]),
      room('r2', outdoor, [[400, 0], [600, 0], [600, 300], [400, 300]]),
      room('r3', entry, [[0, 300], [300, 300], [300, 450], [0, 450]]),
    ],
    objects: [{
      id: 'o1', status: 'active', category, subtype, room_id: null, level_id: null, confidence: 0.6,
      polygon_px: [[20, 320], [60, 320], [60, 380], [20, 380]], polygon_m: null, flags: [], properties: {},
      provenance: ext('cubicasa', 'icon1'), global_id: null, ifc_class: null, classifications: [], discipline: 'mep',
      type_id: null, system_ids: [], material_ids: [], material_layer_set_id: null, host_id: null,
    }],
    coverage: [cov('rooms', 'analysed', 0.7), cov('fixtures', 'partial', 0.5), cov('stairs', 'not_analysed', 0.3)],
    history: [],
  };
}
