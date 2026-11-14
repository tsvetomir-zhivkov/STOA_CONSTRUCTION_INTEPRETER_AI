import { mergeDocuments } from '../mappers/merge.js';
import { readJsonIfExists, writeJsonAtomic } from './jsonStore.js';
import { mergeBuildings } from './building.js';
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
    } else result = mergeBuildings([toCanonical(existing), document]);
  }
  validator.assertValid(result);
  await writeJsonAtomic(target, result, validator);
  outputs.push({ outputJsonPath: target, buildingId: result.building_id, drawingId: result.building_id, action: existing ? 'merged' : 'created', report: {} });
}
