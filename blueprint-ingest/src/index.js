#!/usr/bin/env node
/**
 * Blueprint ingestion pipeline entry point.
 *
 *   processBlueprint({ inputFilePath, outputJsonPath, mode })
 *     1. classify (magic bytes + extension + content; scanned/unreadable -> vision agent)
 *     2. vector extraction (IFC / DXF / SVG / vector PDF)  -> deterministic bundles
 *     3. deterministic schema mapping + validation + create-or-merge + atomic write
 *
 * CLI:  node src/index.js <input> [--out out.json] [--mode auto|create|merge] [--frame-mismatch error|sibling|reproject] [--levels "Level 1,Level 2"] [--file data/x/plan.pdf] [--source x]
 *       node src/index.js --validate-only <file.json>
 */

import fs from 'node:fs/promises';
import path from 'node:path';
import { fileURLToPath } from 'node:url';
import { parseArgs } from 'node:util';
import { loadConfig } from './config.js';
import { createLogger } from './util/logger.js';
import { UnsupportedFormatError, PipelineError } from './util/errors.js';
import { classifyBlueprint } from './classifier/blueprintClassifier.js';
import { extractIfcBuildings } from './extractors/ifcExtractor.js';
import { extractPdf } from './extractors/pdfExtractor.js';
import { extractDxf, extractSvg } from './extractors/cadExtractor.js';
import { mapToSchema } from './mappers/schemaMapper.js';
import { loadCorrections } from './mappers/corrections.js';
import { routeToVisionAgent } from './agents/visionAgentAdapter.js';
import { createValidator, describeErrors } from './storage/jsonStore.js';

/**
 * @typedef {Object} ProcessOptions
 * @property {string} inputFilePath
 * @property {string} [outputJsonPath] default: ./out/<input name>.json
 * @property {'auto'|'create'|'merge'} [mode='auto']
 * @property {Partial<import('./config.js').PipelineConfig>|Record<string,any>} [configOverrides] shallow-merged per top-level key
 * @property {NodeJS.ProcessEnv} [env]
 * @property {any} [logger]
 * @property {string[]} [levels] IFC: only these storeys
 * @property {string} [file] drawing.file: dataset-relative path (default: derived from the input path, `data/<source>/...`)
 * @property {string} [source] drawing.source: dataset folder (default: derived from the input path)
 */

/**
 * @param {ProcessOptions} opts
 * @returns {Promise<
 *   | { status:'routed_to_vision_agent', filePath:string, reason:string, outputJsonPath:string, agentInvoked:boolean, agentResult?:any }
 *   | { status:'processed'|'processed_with_vision_routing', route:string, classification:any, outputs:any[], vision?:any }
 * >}
 */
export async function processBlueprint(opts) {
  const env = opts.env ?? process.env;
  const config = { ...loadConfig(env), ...(opts.configOverrides ?? {}) };
  if (opts.configOverrides?.merge) config.merge = { ...loadConfig(env).merge, ...opts.configOverrides.merge };
  const logger = opts.logger ?? createLogger();
  const inputFilePath = path.resolve(opts.inputFilePath);
  const outputJsonPath = path.resolve(opts.outputJsonPath ?? path.join('out', `${path.basename(inputFilePath, path.extname(inputFilePath))}.json`));

  const classification = await classifyBlueprint(inputFilePath, { config, logger });
  logger.info(`classified ${path.basename(inputFilePath)} as ${classification.detected.format} -> ${classification.route} (${classification.reason})`);

  const toVision = (details) => routeToVisionAgent(
    { filePath: inputFilePath, file: opts.file, source: opts.source, mimeType: classification.detected.mime, detectionDetails: { reason: classification.reason, format: classification.detected.format, route: classification.route, warnings: classification.warnings, ...details }, outputJsonPath, schemaPath: config.schemaPath },
    { agentModule: config.visionAgentModule, logger },
  );

  switch (classification.route) {
    case 'unsupported':
      throw new UnsupportedFormatError(`${path.basename(inputFilePath)}: ${classification.reason}`, { classification });
    case 'vision_agent':
      return toVision({});
    case 'vector_extraction':
    case 'mixed':
      break;
    default:
      throw new PipelineError(`Unknown route ${classification.route}`, 'BAD_ROUTE');
  }

  const fmt = classification.detected.format;
  const vectorPages = classification.pages?.filter((p) => p.route === 'vector_extraction').map((p) => p.index);
  const scannedPages = classification.pages?.filter((p) => p.route !== 'vector_extraction').map((p) => p.index) ?? [];
  const corrections = await loadCorrections(config.correctionsPath, { logger });
  if (corrections.rules.length) logger.info(`${corrections.rules.length} correction rule(s) from ${corrections.path}`);
  const exOpts = { config, logger, corrections };
  /** @type {import('./extractors/bundle.js').Bundle[]} */
  let bundles;
  if (fmt === 'ifc') {
    const documents = await extractIfcBuildings(inputFilePath, { ...exOpts, levels: opts.levels, file: opts.file, source: opts.source });
    const validator = await createValidator(config.schemaPath), outputs = [];
    for (const document of documents) {
      const suffix = document.building_id.replace(/[^A-Za-z0-9_-]/g, '_');
      const target = documents.length === 1 ? outputJsonPath : outputJsonPath.replace(/\.json$/i, '') + `.${suffix}.json`;
      await persistBuilding({ document, target, mode: opts.mode, validator, outputs, usage });
    }
    return { status: 'processed', route: classification.route, classification: summarize(classification), outputs, usage: usage.snapshot() };
  }
  else if (fmt === 'dxf') bundles = await extractDxf(inputFilePath, exOpts);
  else if (fmt === 'svg') bundles = await extractSvg(inputFilePath, exOpts);
  else if (fmt === 'pdf') bundles = await extractPdf(inputFilePath, { ...exOpts, pages: vectorPages });
  else throw new UnsupportedFormatError(`No vector extractor for ${fmt}`, { classification });

  if (!bundles.length) {
    if (classification.route === 'vector_extraction' && fmt !== 'pdf') throw new PipelineError(`${path.basename(inputFilePath)} produced no drawing pages (no usable plan geometry)`, 'EMPTY_EXTRACTION');
  }

  const mapped = bundles.length
    ? await mapToSchema({ inputFilePath, outputJsonPath, mode: opts.mode ?? 'auto', bundles, config, logger, corrections, file: opts.file, source: opts.source })
    : { outputs: [] };

  const result = { status: /** @type {'processed'|'processed_with_vision_routing'} */ ('processed'), route: classification.route, classification: summarize(classification), outputs: mapped.outputs };
  if (classification.route === 'mixed' && scannedPages.length) {
    result.vision = await toVision({ pages: scannedPages });
    result.status = 'processed_with_vision_routing';
  }
  return result;
}

function summarize(c) {
  return { format: c.detected.format, mime: c.detected.mime, route: c.route, reason: c.reason, extensionMismatch: c.detected.extensionMismatch, warnings: c.warnings, pages: c.pages?.map((p) => ({ index: p.index, route: p.route, reason: p.reason })) };
}

// =====================================================================================================
// CLI
// =====================================================================================================

async function main(argv) {
  try { process.loadEnvFile(); } catch { /* no .env file: fine */ }
  const { values, positionals } = parseArgs({
    args: argv,
    allowPositionals: true,
    options: {
      out: { type: 'string', short: 'o' },
      mode: { type: 'string', default: 'auto' },
      'frame-mismatch': { type: 'string' },
      levels: { type: 'string' },
      file: { type: 'string' },
      source: { type: 'string' },
      'validate-only': { type: 'boolean', default: false },
      quiet: { type: 'boolean', short: 'q', default: false },
      help: { type: 'boolean', short: 'h', default: false },
    },
  });
  if (values.help || !positionals.length) {
    process.stdout.write('Usage: blueprint-ingest <input> [--out out.json] [--mode auto|create|merge] [--frame-mismatch error|sibling|reproject] [--levels "A,B"] [--file data/<source>/x.pdf] [--source <source>]\n       blueprint-ingest --validate-only <file.json>\n');
    return values.help ? 0 : 1;
  }
  if (values['validate-only']) {
    const { validate } = await createValidator(loadConfig().schemaPath);
    const r = validate(migrate(JSON.parse(await fs.readFile(positionals[0], 'utf8'))));
    process.stdout.write(r.valid ? 'valid\n' : `INVALID\n${describeErrors(r.errors).join('\n')}\n`);
    return r.valid ? 0 : 1;
  }
  if (!['auto', 'create', 'merge'].includes(values.mode)) throw new PipelineError(`--mode must be auto|create|merge`, 'BAD_ARGS');
  const logger = createLogger(values.quiet ? 'error' : undefined);
  const res = await processBlueprint({
    inputFilePath: positionals[0],
    outputJsonPath: values.out,
    mode: /** @type {any} */ (values.mode),
    levels: values.levels?.split(',').map((s) => s.trim()).filter(Boolean),
    file: values.file,
    source: values.source,
    configOverrides: values['frame-mismatch'] ? { merge: { onFrameMismatch: values['frame-mismatch'] } } : undefined,
    logger,
  });
  process.stdout.write(`${JSON.stringify(res, null, 2)}\n`);
  return 0;
}

const isMain = process.argv[1] && (await fs.realpath(process.argv[1]).catch(() => process.argv[1])) === fileURLToPath(import.meta.url);
if (isMain) {
  main(process.argv.slice(2)).then((code) => { process.exitCode = code; }, (err) => {
    process.stderr.write(`${err.name ?? 'Error'}: ${err.message}\n`);
    if (err.details?.errors) process.stderr.write(`${describeErrors(err.details.errors).slice(0, 10).join('\n')}\n`);
    process.exitCode = err.code === 'UNSUPPORTED_FORMAT' ? 3 : 1;
  });
}
