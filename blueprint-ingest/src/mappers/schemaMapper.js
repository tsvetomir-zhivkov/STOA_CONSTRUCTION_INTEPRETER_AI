/**
 * Bundles -> validated JSON files. Fully deterministic, no network.
 *
 *   bundle (deterministic geometry)  --buildDocument-->  schema document (glossary names, geometry-based text linking)
 *   -> validate (Ajv 2020-12, throw on failure) -> create or non-destructive merge -> validate again -> atomic write
 *
 * Room names that are not in the glossary stay type "unknown".
 */

import path from 'node:path';
import fs from 'node:fs';
import crypto from 'node:crypto';
import { buildDocument } from './documentBuilder.js';
import { mergeDocuments } from './merge.js';
import { applyCorrections } from './corrections.js';
import { normalizeVocabulary } from './vocabulary.js';
import { readJsonIfExists, writeJsonAtomic } from '../storage/jsonStore.js';
import { createVersionedValidator, detectVersion, migrateV02toV03, DEFAULT_OUTPUT_VERSION } from '../schema/versions.js';
import { liftToV03 } from '../schema/lift.js';
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
 * @property {'0.2'|'0.3'} [schemaVersion] output schema (default config.schemaVersion, else 0.3)
 */

/**
 * @param {MapOptions} opts
 * @returns {Promise<{ outputs: Array<{ outputJsonPath:string, drawingId:string, action:'created'|'merged'|'sibling', report: any }> }>}
 */
export async function mapToSchema(opts) {
  const { inputFilePath, outputJsonPath, mode = 'auto', bundles, config } = opts;
  const logger = opts.logger ?? silentLogger;
  const version = opts.schemaVersion ?? config.schemaVersion ?? DEFAULT_OUTPUT_VERSION;
  if (version !== '0.2' && version !== '0.3') throw new PipelineError(`unsupported output schema ${version} (0.2 or 0.3)`, 'BAD_ARGS');
  const validator = await createVersionedValidator({ schemaPaths: config.schemaPath ? { '0.2': config.schemaPath } : {} });
  if (!bundles.length) throw new PipelineError('No bundles to map', 'NO_BUNDLES');
  const fileInfo = version === '0.3' ? await fileFacts(inputFilePath) : null;

  const outputs = [];
  for (const bundle of bundles) {
    const built = buildDocument(bundle, { inputFilePath, now: opts.now, file: opts.file, source: opts.source, datasetRoot: config.datasetRoot, multiPage: bundles.length > 1 && bundle.format === 'pdf', schemaVersion: version });
    let document = built.document;
    const { pending, ids } = built;
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
    if (version === '0.3') document = liftToV03(document, { bundle, ids, inputFilePath, file: fileInfo, now: opts.now });

    const target = outputPathFor(outputJsonPath, document, bundles.length, inputFilePath);
    await finalizeOne({ document, target, mode, validator, config, logger, now: opts.now, outputs, version });
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

async function finalizeOne({ document, target, mode, validator, config, logger, now, outputs, version }) {
  let existing = await readJsonIfExists(target);
  if (mode === 'create' && existing) throw new PipelineError(`Refusing to overwrite existing ${target} in mode "create" (use "auto" or "merge")`, 'OUTPUT_EXISTS');
  if (mode === 'merge' && !existing) throw new PipelineError(`mode "merge" needs an existing ${target}`, 'OUTPUT_MISSING');

  let doc = validateOrThrow(document, { validator, label: 'new document' });
  let action = 'created';
  let report = { added: [...doc.levels, ...doc.units, ...doc.rooms, ...doc.walls, ...doc.openings, ...doc.texts, ...doc.objects].length };
  let outPath = target;

  if (existing) {
    existing = validateOrThrow(existing, { validator, label: `existing ${path.basename(target)}`, existingFile: true });
    const exV = detectVersion(existing);
    if (exV !== version) {
      if (exV === '0.2' && version === '0.3') {
        existing = migrateV02toV03(existing, { now, note: `existing ${path.basename(target)} migrated from v0.2 before merging a v0.3 extraction` });
        logger.info(`existing ${path.basename(target)} is schema 0.2: migrated to 0.3 in memory before the merge`);
      } else {
        throw new PipelineError(`existing ${target} is schema ${exV}; refusing to merge a schema ${version} extraction into it (use --schema ${exV} or a new output path)`, 'SCHEMA_MISMATCH');
      }
    }
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
  const msgs = validator.describe(r);
  throw new SchemaValidationError(r.errors, `${label} does not conform to schema ${r.version ?? '?'}${existingFile ? ' (the existing file was left untouched)' : ''}: ${msgs.slice(0, 3).join('; ')}${msgs.length > 3 ? ` (+${msgs.length - 3} more)` : ''}`);
}

/** size and sha256 of the input file (sources[0]); null values when it cannot be read */
async function fileFacts(p) {
  try {
    const size = (await fs.promises.stat(p)).size;
    const h = crypto.createHash('sha256');
    await new Promise((res, rej) => fs.createReadStream(p).on('data', (c) => h.update(c)).on('end', res).on('error', rej));
    return { size, sha256: h.digest('hex') };
  } catch {
    return { size: null, sha256: null };
  }
}
