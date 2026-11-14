/** Read-only render projections; component facts remain in the validated building. */
export const ENTITY_COLLECTIONS = ['levels','units','rooms','walls','openings','objects','texts','edges','types','systems','schedules'];
export function indexBuilding(building) {
  const entities = new Map(), byGuid = new Map();
  for (const collection of ENTITY_COLLECTIONS) for (const entity of building[collection] ?? []) {
    entities.set(entity.id, { entity, collection });
    if (entity.ifc_global_id) byGuid.set(entity.ifc_global_id, entity.id);
  }
  return { building, entities, byGuid, levels:new Map((building.levels ?? []).map(e => [e.id,e])) };
}
export function inverseAffine(matrix, point) {
  const [[a,b,c],[d,e,f]] = matrix;
  const det = a*e-b*d;
  if (!Number.isFinite(det) || Math.abs(det)<1e-12) throw new Error('Non-invertible source transform');
  const x=point[0]-c, y=point[1]-f;
  return [(e*x-b*y)/det,(-d*x+a*y)/det];
}
export function renderProjection(building) {
  if (!building.building_id || !Array.isArray(building.drawings)) return building;
  const drawings = new Map(building.drawings.map(d => [d.id,d]));
  const project = entity => {
    const d = drawings.get(entity.source_drawing_id) ?? building.drawings[0];
    const g=entity.geometry;
    let polygon=null, holes=[];
    if (g?.coordinate_system==='world' && g.unit==='m' && g.footprints?.length) {
      polygon=g.footprints[0].exterior; holes=g.footprints[0].holes ?? [];
    } else {
      const transform=d?.transforms?.find(t=>t.kind==='affine' && t.matrix);
      const obs=entity.observations?.find(o=>o.drawing_id===d?.id);
      const pixels=obs?.polygon_px ?? entity.polygon_px;
      if (transform && pixels) {
        polygon=pixels.map(p=>inverseAffine(transform.matrix,p));
        holes=(obs?.holes_px ?? entity.holes_px ?? []).map(h=>h.map(p=>inverseAffine(transform.matrix,p)));
      } else if (entity.polygon_m) { polygon=entity.polygon_m; holes=entity.holes_m ?? []; }
    }
    return { ...entity, global_id:entity.ifc_global_id, polygon_px:polygon, polygon_m:polygon, holes_px:holes, holes_m:holes, quantities:Object.fromEntries((entity.quantities ?? []).map(q=>[q.name,{...q,status:q.origin==='source'?'explicit':q.origin}])) };
  };
  return { ...building, _canonical:building,
    drawing:{ ...(building.drawings[0] ?? {}), scale:{m_per_px:1,source:'vector_units',confidence:1}, transforms:[{kind:'affine',matrix:[[1,0,0],[0,1,0],[0,0,1]]}] },
    ...Object.fromEntries(ENTITY_COLLECTIONS.map(c=>[c,(building[c] ?? []).map(project)])),
    element_types:building.types, materials:[], material_layer_sets:[],
  };
}
