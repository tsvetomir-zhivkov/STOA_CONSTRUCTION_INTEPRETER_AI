/**
 * Central configuration. Everything tunable lives here so modules stay free of magic numbers.
 * Environment variables override the defaults (see .env.example).
 */

import path from 'node:path';
import { fileURLToPath } from 'node:url';

const HERE = path.dirname(fileURLToPath(import.meta.url));

export const SCHEMA_PATH = path.resolve(HERE, '..', 'schema', 'blueprint.schema.json');

/**
 * @typedef {ReturnType<typeof loadConfig>} PipelineConfig
 */

/**
 * @param {NodeJS.ProcessEnv} [env]
 */
export function loadConfig(env = process.env) {
  return {
    schemaPath: SCHEMA_PATH,
    /** drawing.file is made relative to this folder unless the input path contains data/<source>/ (default: cwd) */
    datasetRoot: env.DATASET_ROOT || null,
    /** human corrections lookup (see src/mappers/corrections.js); default: corrections.json at the repo root. Missing = none. */
    correctionsPath: env.CORRECTIONS_FILE || path.resolve(HERE, '..', '..', 'corrections.json'),
    classifier: {
      /** a PDF page is "vector" when it has at least this many drawn path segments... */
      minVectorSegments: 40,
      /** ...or at least this many legible text characters (and is not raster-dominated) */
      minLegibleChars: 60,
      /** embedded raster covering this share of the page makes it "scanned" unless it has rich vectors */
      rasterDominantCoverage: 0.5,
      richVectorSegments: 400,
      /** share of non-space chars that must look like real text (letters/digits/punct) */
      minLegibleRatio: 0.8,
      maxPdfPagesInspected: 40,
      headerSniffBytes: 4096,
    },
    pdf: {
      maxRenderDpi: 150,
      maxPixelSide: 4096,
      curveSamples: 6,
      minRoomAreaFrac: 0.0015,
      maxRoomAreaFrac: 0.45,
    },
    cad: {
      targetPixelSide: 3000,
      maxInsertDepth: 4,
      maxObjects: 5000,
    },
    ifc: {
      targetPixelSide: 3000,
      maxObjects: 5000,
      /** 'auto' uses web-ifc only to fill in missing footprints (if the package is installed) */
      webIfc: 'auto',
      webIfcMaxBytes: 300 * 1024 * 1024,
    },
    merge: {
      /** bbox IoU above which two elements of the same kind are treated as the same thing */
      duplicateIou: 0.6,
      /** incoming must beat existing confidence by this much to supersede it */
      supersedeMargin: 0.15,
      /** 'error' | 'sibling' | 'reproject' when the incoming pixel frame differs */
      onFrameMismatch: 'error',
    },
    visionAgentModule: env.VISION_AGENT_MODULE || null,
  };
}
