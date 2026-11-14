/**
 * Central configuration. Everything tunable lives here so modules stay free of magic numbers.
 * Environment variables override the defaults (see .env.example).
 */

import path from 'node:path';
import { fileURLToPath } from 'node:url';

const HERE = path.dirname(fileURLToPath(import.meta.url));

export const SCHEMA_PATH = path.resolve(HERE, '..', 'schema', 'blueprint.schema.json');

/** @param {string|undefined} v @param {number} d */
const num = (v, d) => (v === undefined || v === '' || Number.isNaN(Number(v)) ? d : Number(v));
/** @param {string|undefined} v */
const list = (v) => (v ? v.split(',').map((s) => s.trim()).filter(Boolean) : []);

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
    gemini: {
      apiKey: env.GEMINI_API_KEY || '',
      models: {
        primary: env.GEMINI_MODEL || 'gemini-4-hydrogen',
        heavy: env.GEMINI_MODEL_HEAVY || 'gemini-4-carbon',
        light: env.GEMINI_MODEL_LIGHT || 'gemini-3.8-flash',
        fallbacks: list(env.GEMINI_MODEL_FALLBACKS ?? 'gemini-3.6-flash,gemini-3.5-flash'),
      },
      retry: {
        retries: num(env.GEMINI_MAX_RETRIES, 5),
        baseMs: num(env.GEMINI_RETRY_BASE_MS, 800),
        maxMs: num(env.GEMINI_RETRY_MAX_MS, 30000),
      },
      pricing: {
        pricePerMInput: env.GEMINI_PRICE_PER_M_INPUT ? Number(env.GEMINI_PRICE_PER_M_INPUT) : null,
        pricePerMOutput: env.GEMINI_PRICE_PER_M_OUTPUT ? Number(env.GEMINI_PRICE_PER_M_OUTPUT) : null,
      },
    },
    mapper: {
      /** unique room names / unknown texts sent per Gemini call */
      batchSize: num(env.MAPPER_BATCH_SIZE, 120),
      /** texts shown to the model for page-level facts (level name, languages) */
      factTextSamples: 40,
      maxTextChars: 80,
    },
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
      /** 'auto' uploads the PDF to Gemini only for legend/schedule-like pages */
      filesApi: 'auto',
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
