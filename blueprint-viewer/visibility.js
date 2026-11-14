export function recordCategory(record) {
  const entity = record.kind === 'ifc' ? record.el.json : record.meshes[0]?.userData.element;
  const cls = entity?.ifc_class ?? record.el?.ifcType;
  if (cls) return String(cls).replace(/^IFC/i,'').replace(/STANDARDCASE$/i,'').toLowerCase();
  if (entity?.kind) return entity.kind;
  if (entity?.category) return entity.category;
  return record.model?.kindOf(entity)?.replace(/s$/,'') ?? 'other';
}

/** Degenerate hidden triangles in place. Original ranges and BVH triangle identity stay stable. */
export function setRangeVisibility(mesh, ranges, visible) {
  const index = mesh.geometry.index;
  mesh.userData.originalIndex ??= index.array.slice();
  const original = mesh.userData.originalIndex;
  for (const r of ranges) for (let i=r.start;i<r.start+r.count;i+=3) {
    index.array[i]=original[i];
    index.array[i+1]=visible?original[i+1]:original[i];
    index.array[i+2]=visible?original[i+2]:original[i];
  }
  index.needsUpdate=true;
}

export function identityOfRecord(record) {
  if (record.kind==='ifc') return `${record.source.scope ?? record.source.id}:${record.el.globalId ?? record.el.expressID}`;
  return `${record.model.source.scope ?? record.model.source.id}:${record.meshes[0]?.userData.element.id}`;
}
