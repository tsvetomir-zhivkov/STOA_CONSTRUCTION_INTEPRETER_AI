/**
 * Hand-off point to the EXISTING Image-Interpreter (vision) agent.
 *
 * This repository deliberately does not contain that agent. Everything that is not machine-readable vector data
 * (photos, scans, raster images, scanned PDFs) is routed here with the reason the classifier gave.
 *
 * HOW TO PLUG IN YOUR AGENT (pick one):
 *   1. Environment: set VISION_AGENT_MODULE=./path/to/agent.js. Its default export (or a named `routeToVisionAgent`
 *      / `run`) is called with the same single argument object documented on `routeToVisionAgent` below and may return anything
 *      JSON-serialisable; the value is passed back under `agentResult`.
 *   2. Code: replace the body of `invokeAgent()` with a direct call, e.g.
 *        import { interpretImage } from '../../existing-agent/index.js';
 *        return interpretImage({ imagePath: filePath, outputJsonPath, schemaPath });
 *
 * CONTRACT FOR THE AGENT
 *   - Write a v0.3 building document at outputJsonPath using the supplied schemaPath.
 *   - Use source_documents[], drawings[] (route raster or vlm), source_refs and source_drawing_id.
 *   - Keep original page pixels and record every transform on its drawing; merge all source pages.
 *   - Preserve typed schedules separately from physical entities. Unknown values remain null.
 *   - outputJsonPath is a staging path. This adapter validates schema and references before publishing.
 *   - Legacy v0.2 plugin output is migrated at this boundary; invalid output leaves the target untouched.
 *   - detectionDetails.pages identifies the zero-based pages that need vision extraction.
 */

import path from 'node:path';
import fs from 'node:fs/promises';
import { pathToFileURL } from 'node:url';
import { PipelineError } from '../util/errors.js';
import { silentLogger } from '../util/logger.js';
import { createValidator, readJsonIfExists, writeJsonAtomic } from '../storage/jsonStore.js';
import { migrate, mergeBuildings } from '../storage/building.js';

/**
 * @typedef {Object} VisionRouteArgs
 * @property {string} filePath absolute or relative path of the source file
 * @property {string|null} mimeType detected MIME type, e.g. "image/png" or "application/pdf"
 * @property {{ reason:string, format?:string, route?:string, pages?:number[], warnings?:string[], [k:string]: any }} detectionDetails what the classifier saw; `reason` e.g. "scanned_pdf_no_vector_data", "raster_image"
 * @property {string} outputJsonPath where the agent should write its schema-conformant JSON
 * @property {string} schemaPath path of the JSON Schema the output must satisfy
 * @property {string} [file] drawing.file to use (dataset-relative path), when the caller knows it
 * @property {string} [source] drawing.source to use (dataset folder), when the caller knows it
 */

/**
 * @typedef {Object} VisionRouteResult
 * @property {'routed_to_vision_agent'} status
 * @property {string} filePath
 * @property {string} reason
 * @property {string} outputJsonPath
 * @property {boolean} agentInvoked false when no agent module is configured (this is only a routing stub then)
 * @property {any} [agentResult] whatever the agent returned
 */

/**
 * Route a non-vector file to the vision agent.
 *
 * @param {VisionRouteArgs} args
 * @param {{ agentModule?: string|null, logger?: any }} [opts]
 * @returns {Promise<VisionRouteResult>}
 */
export async function routeToVisionAgent(args, opts = {}) {
  const logger = opts.logger ?? silentLogger;
  const { filePath, outputJsonPath, detectionDetails } = args;
  const reason = detectionDetails?.reason ?? 'unspecified';
  /** @type {VisionRouteResult} */
  const result = { status: 'routed_to_vision_agent', filePath, reason, outputJsonPath, agentInvoked: false };

  const modulePath = opts.agentModule ?? process.env.VISION_AGENT_MODULE ?? null;
  if (!modulePath) {
    logger.info(`vision agent not configured; routed ${path.basename(filePath)} (${reason}) - set VISION_AGENT_MODULE to hand it over`);
    return result;
  }
  try {
    await fs.mkdir(path.dirname(outputJsonPath), { recursive: true });
    const staging = await fs.mkdtemp(path.join(path.dirname(outputJsonPath), '.vision-'));
    try {
      const stagedPath = path.join(staging, 'building.json');
      const existing = await readJsonIfExists(outputJsonPath);
      if (existing) await writeJsonAtomic(stagedPath, migrate(existing));
      result.agentResult = await invokeAgent(modulePath, { ...args, outputJsonPath: stagedPath });
      const emitted = await readJsonIfExists(stagedPath);
      if (!emitted) throw new Error('Vision agent did not write its building document');
      const validator = await createValidator(args.schemaPath);
      const building = existing ? mergeBuildings([migrate(existing), migrate(emitted)]) : migrate(emitted);
      validator.assertValid(building);
      await writeJsonAtomic(outputJsonPath, building);
      result.agentInvoked = true;
    } finally {
      await fs.rm(staging, { recursive: true, force: true });
    }
  } catch (err) {
    throw new PipelineError(`Vision agent failed for ${path.basename(filePath)} (${reason}): ${err.message}`, 'VISION_AGENT_FAILED', { cause: err.message });
  }
  return result;
}

/**
 * >>> PLUG-IN POINT <<<  Replace or extend this function to call your existing agent directly.
 * @param {string} modulePath
 * @param {VisionRouteArgs} args
 */
async function invokeAgent(modulePath, args) {
  const href = pathToFileURL(path.resolve(modulePath)).href;
  const mod = await import(href);
  const fn = mod.default ?? mod.routeToVisionAgent ?? mod.run;
  if (typeof fn !== 'function') throw new Error(`${modulePath} must export a function (default, routeToVisionAgent or run)`);
  return fn(args);
}
