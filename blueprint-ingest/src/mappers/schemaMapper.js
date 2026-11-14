/**
 * Bundles -> validated JSON files. Fully deterministic, no network.
 *
 *   bundle (deterministic geometry)  --buildDocument-->  schema document (glossary names, geometry-based text linking)
 *   -> validate (Ajv 2020-12, throw on failure) -> create or non-destructive merge -> validate again -> atomic write
 *
 * Room names that are not in the glossary stay type "unknown".
 */

import path from 'node:path';
import { buildDocument } from './documentBuilder.js';
import { mergeDocuments } from './merge.js';
import { applyCorrections } from './corrections.js';
import { normalizeVocabulary } from './vocabulary.js';
import { createValidator, readJsonIfExists, writeJsonAtomic, describeErrors } from '../storage/jsonStore.js';
import { PipelineError, SchemaValidationError, FrameMismatchError } from '../util/errors.js';
import { silentLogger } from '../util/logger.js';

/**
 * @typedef {Object} MapOptions
 * @property {string} inputFilePath
 * @property {string} outputJsonPath
 * @property {'auto'|'create'|'merge'} [mode='auto'] auto: merge if the file exists, else create. create: refuse to touch an existing file. merge: the file must exist.
 * @property {import('../extractors/bundle.js').Bundle[]} bundles
 * @property {import('../config.js').PipelineConfig} config
 * @property {any} [logger]
 * @property {string} [now]
 * @property {string} [file] drawing.file override (dataset-relative path)
 * @property {string} [source] drawing.source override (dataset folder)
 * @property {import('./corrections.js').Corrections} [corrections] human corrections lookup
 */

/**
 * @param {MapOptions} opts
 * @returns {Promise<{ outputs: Array<{ outputJsonPath:string, drawingId:string, action:'created'|'merged'|'sibling', report: any }> }>}
 */
export async function mapToSchema(opts) {
  const { inputFilePath, outputJsonPath, mode = 'auto', bundles, config } = opts;
  const logger = opts.logger ?? silentLogger;
  const validator = await createValidator(config.schemaPath);
  if (!bundles.length) throw new PipelineError('No bundles to map', 'NO_BUNDLES');

  const outputs = [];
  for (const bundle of bundles) {
    const { document, pending } = buildDocument(bundle, { inputFilePath, now: opts.now, file: opts.file, source: opts.source, datasetRoot: config.datasetRoot, multiPage: bundles.length > 1 && bundle.format === 'pdf' });
    const fired = applyCorrections(document, opts.corrections, { now: opts.now });
    if (fired.length) logger.info(`corrections: ${fired.map((f) => `${f.rule} (${f.ids.length})`).join(', ')}`);
    // one vocabulary for every route: aliases mapped, anything unmapped reported (and flagged), never passed through silently
    const voc = normalizeVocabulary(document);
    if (voc.changed.length) logger.info(`vocabulary: ${voc.changed.length} value(s) mapped onto the schema enums (${[...new Set(voc.changed.map((c) => `${c.from}->${c.to}`))].slice(0, 8).join(', ')})`);
    if (voc.unmapped.length) {
      logger.warn(`vocabulary: ${voc.unmapped.length} value(s) did not map (${[...new Set(voc.unmapped.map((u) => `${u.field} "${u.value}"`))].slice(0, 8).join(', ')}); set to unknown/other, raw value kept, flag unmapped_vocabulary`);
      document.history.push({ at: opts.now ?? new Date().toISOString(), extractor: 'vocabulary', action: 'map', entity_ids: voc.unmapped.map((u) => u.id).slice(0, 500), note: `unmapped values: ${[...new Set(voc.unmapped.map((u) => `${u.field}=${u.value}`))].slice(0, 20).join(', ')}` });
    }
    if (pending.roomNames.length) logger.info(`${pending.roomNames.length} room name(s) not in the glossary; they stay type "unknown"`);

    const target = outputPathFor(outputJsonPath, document, bundles.length, inputFilePath);
    await finalizeOne({ document, target, mode, validator, config, logger, now: opts.now, outputs });
  }
  return { outputs };
}

/** one file per drawing page: if a source yields several pages/levels, suffix the drawing id before the extension */
export function outputPathFor(outputJsonPath, document, total, inputFilePath) {
  if (total <= 1) return outputJsonPath;
  const ext = path.extname(outputJsonPath);
  const base = outputJsonPath.slice(0, outputJsonPath.length - ext.length);
  const part = document.drawing.part ?? `p${(document.drawing.page_index ?? 0) + 1}`;
  return `${base}.${String(part).replace(/[^A-Za-z0-9_.-]/g, '_')}${ext || '.json'}`;
}

/** human-readable identity of a document: file plus part, e.g. "data/x/plan.ifc#level-1" */
export const drawingKey = (d) => `${d.file}${d.part ? `#${d.part}` : ''}`;

// =====================================================================================================
// persistence
// =====================================================================================================

async function finalizeOne({ document, target, mode, validator, config, logger, now, outputs }) {
  let existing = await readJsonIfExists(target);
  if (mode === 'create' && existing) throw new PipelineError(`Refusing to overwrite existing ${target} in mode "create" (use "auto" or "merge")`, 'OUTPUT_EXISTS');
  if (mode === 'merge' && !existing) throw new PipelineError(`mode "merge" needs an existing ${target}`, 'OUTPUT_MISSING');

  let doc = validateOrThrow(document, { validator, label: 'new document' });
  let action = 'created';
  let report = { added: [...doc.levels, ...doc.units, ...doc.rooms, ...doc.walls, ...doc.openings, ...doc.texts, ...doc.objects].length };
  let outPath = target;

  if (existing) {
    existing = validateOrThrow(existing, { validator, label: `existing ${path.basename(target)}`, existingFile: true });
    try {
      const m = mergeDocuments(existing, doc, config, { now });
      doc = m.document; report = m.report; action = 'merged';
    } catch (err) {
      if (err instanceof FrameMismatchError && config.merge.onFrameMismatch === 'sibling') {
        const ext = path.extname(target);
        outPath = `${target.slice(0, target.length - ext.length)}.alt-${doc.drawing.image.width_px}x${doc.drawing.image.height_px}${ext}`;
        action = 'sibling';
        logger.warn(`pixel frame differs from ${target}; writing ${outPath} instead`);
        const prior = await readJsonIfExists(outPath);
        if (prior) { const m = mergeDocuments(prior, doc, config, { now }); doc = m.document; report = m.report; } else report = { note: 'new sibling file' };
      } else throw err;
    }
    // merged result must be valid too (a bug or a stale file must never be persisted silently)
    doc = validateOrThrow(doc, { validator, label: 'merged document' });
  }
  await writeJsonAtomic(outPath, doc);
  outputs.push({ outputJsonPath: outPath, drawingId: drawingKey(doc.drawing), action, report });
}

/**
 * Validate with Ajv; throw SchemaValidationError (first errors in the message) when invalid. Nothing is written then.
 * @returns {any} the same document
 */
function validateOrThrow(doc, { validator, label, existingFile = false }) {
  const r = validator.validate(doc);
  if (r.valid) return doc;
  throw new SchemaValidationError(r.errors, `${label} does not conform to the schema${existingFile ? ' (the existing file was left untouched)' : ''}: ${describeErrors(r.errors).slice(0, 3).join('; ')}`);
}
