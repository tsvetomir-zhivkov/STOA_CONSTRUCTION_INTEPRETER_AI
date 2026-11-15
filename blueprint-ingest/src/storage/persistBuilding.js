import { mergeDocuments } from '../mappers/merge.js';
import { readJsonIfExists, writeJsonAtomic } from './jsonStore.js';
import { mergeBuildings, COLLECTIONS } from './building.js';
import { toCanonical } from '../schema/canonical.js';
import { createVersionedValidator } from '../schema/versions.js';
import { PipelineError } from '../util/errors.js';

export async function persistBuilding({ document, target, mode = 'auto', validator, outputs }) {
  const existing = await readJsonIfExists(target);
  if (mode === 'create' && existing) throw new PipelineError('Refusing to overwrite existing output', 'OUTPUT_EXISTS');
  if (mode === 'merge' && !existing) throw new PipelineError('Merge requires an existing output', 'OUTPUT_MISSING');
  validator.assertValid(document);
  let result = document;
  if (existing) {
    (await createVersionedValidator()).assertValid(existing, 'existing file (left untouched on error)');
    const before = existing.source_documents?.[0]?.properties?.imported_v03?.document;
    const after = document.source_documents?.[0]?.properties?.imported_v03?.document;
    if (before && after && before.drawing.file === after.drawing.file && before.drawing.part === after.drawing.part) {
      result = toCanonical(mergeDocuments(before, after, { merge: { duplicateIou:0.6, supersedeMargin:0.15, onFrameMismatch:'error' } }).document);
    } else if(existing.schema_version==='0.2' && existing.drawing.route==='ifc' && document.drawings.some(d=>d.file===existing.drawing.file)) {
      result=mergeLegacyIfc(existing,document);
    } else result = mergeBuildings([toCanonical(existing), document]);
  }
  validator.assertValid(result);
  await writeJsonAtomic(target, result, validator);
  const entities=d=>['levels','units','rooms','walls','openings','objects','texts','types','systems'].flatMap(k=>d[k]??[]);
  const previous=new Set(existing?entities(toCanonical(existing)).map(e=>e.id):[]);
  outputs.push({ outputJsonPath: target, buildingId: result.building_id, drawingId: result.building_id, action: existing ? 'merged' : 'created', report: {added:entities(result).filter(e=>!previous.has(e.id)).map(e=>e.id)} });
}

/** Exact source identifiers reconcile a fresh IFC extraction with its earlier drawing export.
 * Keep the complete prior document and conflicting old facts as provenance, never as duplicate instances.
 */
function mergeLegacyIfc(existing,incoming){
  const legacy=toCanonical(existing),out=structuredClone(incoming),aliases=new Map([[legacy.building_id,out.building_id]]);
  for(const c of COLLECTIONS){
    const byRef=new Map(out[c].map(e=>[e.provenance?.source_ref,e]));
    for(const e of legacy[c]){const match=byRef.get(e.provenance?.source_ref);if(match)aliases.set(e.id,match.id);}
  }
  const one=new Set(['level_id','unit_id','parent_room_id','room_id','wall_id','type_id','parent_id','superseded_by','from_id','to_id']);
  const many=new Set(['room_ids','text_ids','system_ids','schedule_ids','component_ids','instance_ids','entity_ids','level_ids','boundary_ids']);
  const remap=v=>Array.isArray(v)?v.map(remap):!v||typeof v!=='object'?v:Object.fromEntries(Object.entries(v).map(([k,x])=>[k,['properties','provenance','details'].includes(k)?x:one.has(k)?aliases.get(x)??x:many.has(k)&&Array.isArray(x)?x.map(id=>aliases.get(id)??id):remap(x)]));
  for(const c of COLLECTIONS)for(const old of legacy[c]){
    const match=out[c].find(e=>e.id===aliases.get(old.id));
    if(match){match.properties??={};match.properties.migrated_record=old;match.observations=[...(match.observations??[]),...(old.observations??[])];match.source_refs.push(...old.source_refs);}
    else out[c].push(remap(old));
  }
  for(const c of ['source_documents','drawings'])for(const old of legacy[c])if(!out[c].some(e=>e.id===old.id))out[c].push(remap(old));
  out.source_documents[0].properties??={};out.source_documents[0].properties.previous_v02=existing;
  out.history.push(...remap(legacy.history));
  return out;
}
