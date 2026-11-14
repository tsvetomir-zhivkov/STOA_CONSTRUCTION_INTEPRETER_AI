/**
 * Step 1 - Deterministic format & readability filter.
 *
 * Fully deterministic. Decision order: magic bytes -> header text signatures -> optional `file-type` -> extension.
 * When all of them are inconclusive the file is "unsupported" with reason "unknown_format".
 * For PDFs the page content is inspected with pdfjs to tell vector/text-rich pages from scans.
 */

import fs from 'node:fs/promises';
import path from 'node:path';
import { loadConfig } from '../config.js';
import { silentLogger } from '../util/logger.js';
import { openPdf, inspectPage } from '../extractors/pdfCommon.js';

/** @typedef {'ifc'|'pdf'|'dxf'|'svg'|'dwg'|'raster'|'unknown'} DetectedFormat */

/**
 * @typedef {Object} PageDecision
 * @property {number} index zero-based page index
 * @property {'vector_extraction'|'vision_agent'} route
 * @property {string} reason
 * @property {Record<string, number|boolean>} metrics
 *
 * @typedef {Object} ClassificationResult
 * @property {string} filePath
 * @property {string} fileName
 * @property {string} extension lower-case, without dot
 * @property {number} size
 * @property {{ format: DetectedFormat, mime: string|null, by: 'magic'|'extension'|'header'|'none', extensionMismatch: boolean }} detected
 * @property {'vector_extraction'|'vision_agent'|'mixed'|'unsupported'} route
 * @property {string} reason machine-readable reason, e.g. "scanned_pdf_no_vector_data"
 * @property {PageDecision[]} [pages] PDF only
 * @property {string[]} warnings
 */

export const RASTER_MIME = {
  png: 'image/png', jpg: 'image/jpeg', jpeg: 'image/jpeg', webp: 'image/webp', tif: 'image/tiff', tiff: 'image/tiff',
  bmp: 'image/bmp', gif: 'image/gif', heic: 'image/heic', heif: 'image/heif', avif: 'image/avif', jp2: 'image/jp2',
};
const EXT_FORMAT = { pdf: 'pdf', ifc: 'ifc', dxf: 'dxf', svg: 'svg', dwg: 'dwg', ...Object.fromEntries(Object.keys(RASTER_MIME).map((e) => [e, 'raster'])), jfif: 'raster' };
const FORMAT_MIME = { pdf: 'application/pdf', ifc: 'application/x-step', dxf: 'image/vnd.dxf', svg: 'image/svg+xml', dwg: 'image/vnd.dwg' };

/**
 * Binary signatures. Returns the detected raster/pdf/dwg type or null.
 * @param {Buffer} b
 * @returns {{ format: DetectedFormat, mime: string } | null}
 */
export function sniffBinary(b) {
  if (b.length < 4) return null;
  const ascii = (s, o = 0) => b.subarray(o, o + s.length).toString('latin1') === s;
  if (b.subarray(0, 1024).toString('latin1').includes('%PDF-')) return { format: 'pdf', mime: 'application/pdf' };
  if (b[0] === 0x89 && ascii('PNG', 1)) return { format: 'raster', mime: 'image/png' };
  if (b[0] === 0xff && b[1] === 0xd8 && b[2] === 0xff) return { format: 'raster', mime: 'image/jpeg' };
  if (ascii('GIF8')) return { format: 'raster', mime: 'image/gif' };
  if (ascii('RIFF') && ascii('WEBP', 8)) return { format: 'raster', mime: 'image/webp' };
  if ((b[0] === 0x49 && b[1] === 0x49 && b[2] === 0x2a && b[3] === 0) || (b[0] === 0x4d && b[1] === 0x4d && b[2] === 0 && b[3] === 0x2a)) return { format: 'raster', mime: 'image/tiff' };
  if (ascii('BM') && b.length > 18 && [12, 40, 52, 56, 64, 108, 124].includes(b.readUInt32LE(14))) return { format: 'raster', mime: 'image/bmp' };
  if (ascii('ftyp', 4)) {
    const brand = b.subarray(8, 12).toString('latin1');
    if (/^(heic|heix|hevc|mif1|msf1)/.test(brand)) return { format: 'raster', mime: 'image/heic' };
    if (/^avif|^avis/.test(brand)) return { format: 'raster', mime: 'image/avif' };
  }
  if (b[0] === 0 && b[1] === 0 && b[2] === 0 && b[3] === 0x0c && ascii('jP', 4)) return { format: 'raster', mime: 'image/jp2' };
  if (/^AC10\d\d/.test(b.subarray(0, 6).toString('latin1'))) return { format: 'dwg', mime: 'image/vnd.dwg' };
  return null;
}

/**
 * Text signatures for IFC (STEP), SVG and ASCII DXF.
 * @param {Buffer} b
 * @returns {{ format: DetectedFormat, mime: string, note?: string } | null}
 */
export function sniffText(b) {
  if (b.includes(0)) {
    if (b.subarray(0, 22).toString('latin1').startsWith('AutoCAD Binary DXF')) return { format: 'dxf', mime: FORMAT_MIME.dxf, note: 'dxf_binary' };
    return null; // binary data: not a text format we know
  }
  let s = b.toString('utf8');
  if (s.charCodeAt(0) === 0xfeff) s = s.slice(1);
  if (/^\s*ISO-10303-21\s*;/i.test(s) || s.slice(0, 1024).includes('ISO-10303-21')) {
    return /FILE_SCHEMA\s*\(\s*\(\s*'IFC/i.test(s) ? { format: 'ifc', mime: FORMAT_MIME.ifc } : null;
  }
  if (/^\s*0\s*\r?\n\s*SECTION\s*\r?\n\s*2\s*\r?\n\s*(HEADER|ENTITIES|CLASSES|TABLES|BLOCKS)\b/.test(s)) return { format: 'dxf', mime: FORMAT_MIME.dxf };
  // SVG: allow an XML declaration, doctype and comments before the root element
  if (/^\s*(?:<\?xml[^>]*\?>\s*)?(?:<!--[\s\S]*?-->\s*)*(?:<!DOCTYPE[^>]*>\s*)?(?:<!--[\s\S]*?-->\s*)*<svg[\s>]/i.test(s)) return { format: 'svg', mime: FORMAT_MIME.svg };
  return null;
}

/**
 * @param {string} filePath
 * @param {{ config?: import('../config.js').PipelineConfig, logger?: any }} [opts]
 * @returns {Promise<ClassificationResult>}
 */
export async function classifyBlueprint(filePath, opts = {}) {
  const config = opts.config ?? loadConfig();
  const logger = opts.logger ?? silentLogger;
  const warnings = [];
  const stat = await fs.stat(filePath);
  if (!stat.isFile()) throw new Error(`Not a file: ${filePath}`);
  const extension = path.extname(filePath).slice(1).toLowerCase();
  const fileName = path.basename(filePath);

  const fh = await fs.open(filePath, 'r');
  let head;
  try {
    head = Buffer.alloc(Math.min(stat.size, config.classifier.headerSniffBytes));
    await fh.read(head, 0, head.length, 0);
  } finally {
    await fh.close();
  }

  /** @type {ClassificationResult['detected']} */
  let detected = { format: 'unknown', mime: null, by: 'none', extensionMismatch: false };
  /** @type {string|null} */
  let note = null;

  const bin = sniffBinary(head);
  if (bin) detected = { format: bin.format, mime: bin.mime, by: 'magic', extensionMismatch: false };
  else {
    const txt = sniffText(head);
    if (txt) { detected = { format: txt.format, mime: txt.mime, by: 'magic', extensionMismatch: false }; note = txt.note ?? null; }
  }

  // optional broader binary detection via `file-type` (not required; own table covers our formats)
  if (detected.format === 'unknown') {
    try {
      const mod = await import('file-type');
      const ft = await mod.fileTypeFromFile(filePath);
      if (ft) {
        const fmt = ft.mime.startsWith('image/') ? 'raster' : ft.mime === 'application/pdf' ? 'pdf' : 'unknown';
        if (fmt !== 'unknown') detected = { format: fmt, mime: ft.mime, by: 'magic', extensionMismatch: false };
      }
    } catch { /* file-type not installed: fine */ }
  }

  const extFormat = EXT_FORMAT[extension] ?? null;
  if (detected.format !== 'unknown') {
    if (extFormat && extFormat !== detected.format) {
      detected.extensionMismatch = true;
      warnings.push(`extension .${extension} does not match detected content (${detected.format}); trusting file contents`);
    }
  } else if (extFormat) {
    // header gave nothing: fall back to the extension, but never for formats we can only trust by content
    if (extFormat === 'raster') detected = { format: 'raster', mime: RASTER_MIME[extension] ?? null, by: 'extension', extensionMismatch: false };
    else if (['ifc', 'dxf', 'svg'].includes(extFormat)) detected = { format: /** @type {DetectedFormat} */ (extFormat), mime: FORMAT_MIME[extFormat], by: 'extension', extensionMismatch: false };
    if (detected.by === 'extension') warnings.push('format decided by extension only (content signature not recognised)');
  }

  /** @type {ClassificationResult} */
  const base = { filePath, fileName, extension, size: stat.size, detected, route: 'unsupported', reason: 'unknown_format', warnings };

  // ---- ambiguous: nothing recognised the content -> unsupported / unknown_format ----------------
  if (detected.format === 'unknown') return base;

  // ---- routing ---------------------------------------------------------------------------
  switch (detected.format) {
    case 'raster':
      return { ...base, route: 'vision_agent', reason: 'raster_image' };
    case 'ifc':
    case 'svg':
      return { ...base, route: 'vector_extraction', reason: `structured_${detected.format}` };
    case 'dxf':
      if (note === 'dxf_binary') return { ...base, route: 'unsupported', reason: 'binary_dxf_requires_conversion_to_ascii' };
      return { ...base, route: 'vector_extraction', reason: 'structured_dxf' };
    case 'dwg':
      return { ...base, route: 'unsupported', reason: 'dwg_binary_requires_conversion_to_dxf_or_ifc' };
    case 'pdf':
      return classifyPdf(base, filePath, config, logger);
    default:
      return base;
  }
}

/**
 * Inspect the pages of a PDF and decide vector vs scanned per page.
 * @param {ClassificationResult} base @param {string} filePath @param {import('../config.js').PipelineConfig} config
 * @returns {Promise<ClassificationResult>}
 */
async function classifyPdf(base, filePath, config, logger) {
  const c = config.classifier;
  let doc, pdfjs, close;
  try {
    ({ doc, pdfjs, close } = await openPdf(await fs.readFile(filePath)));
  } catch (err) {
    const reason = /password/i.test(err?.name + err?.message) ? 'pdf_encrypted' : 'pdf_unreadable';
    return { ...base, route: 'unsupported', reason, warnings: [...base.warnings, err.message] };
  }
  try {
    const n = Math.min(doc.numPages, c.maxPdfPagesInspected);
    /** @type {PageDecision[]} */
    const pages = [];
    for (let i = 1; i <= n; i++) {
      const page = await doc.getPage(i);
      const m = await inspectPage(page, pdfjs);
      page.cleanup();
      pages.push({ index: i - 1, ...decidePdfPage(m, c), metrics: summariseMetrics(m) });
    }
    if (doc.numPages > n) {
      const majority = pages.filter((p) => p.route === 'vector_extraction').length >= n / 2 ? 'vector_extraction' : 'vision_agent';
      for (let i = n; i < doc.numPages; i++) pages.push({ index: i, route: majority, reason: 'not_inspected_assumed_like_majority', metrics: {} });
      base.warnings.push(`only the first ${n} of ${doc.numPages} pages were inspected`);
    }
    const vec = pages.filter((p) => p.route === 'vector_extraction');
    if (vec.length === pages.length) return { ...base, route: 'vector_extraction', reason: pages.some((p) => p.metrics.textGarbled) ? 'vector_pdf_text_garbled' : 'vector_pdf', pages };
    if (vec.length === 0) return { ...base, route: 'vision_agent', reason: uniformReason(pages) ?? 'scanned_pdf_no_vector_data', pages };
    return { ...base, route: 'mixed', reason: 'pdf_has_vector_and_scanned_pages', pages };
  } finally {
    logger.debug?.('pdf inspected');
    await close();
  }
}

/** @param {number[]} _ */
function summariseMetrics(m) {
  return {
    textChars: m.textChars, legibleChars: m.legibleChars, legibleRatio: Number(m.legibleRatio.toFixed(3)),
    pathSegments: m.pathSegments, imageCount: m.imageCount, imageCoverage: Number(m.imageCoverage.toFixed(3)),
    textGarbled: m.textChars > 0 && m.legibleRatio < 0.8,
  };
}

function uniformReason(pages) {
  const set = new Set(pages.map((p) => p.reason));
  return set.size === 1 ? [...set][0] : null;
}

/**
 * Per-page vector-vs-scan rule. Exported for unit tests.
 * @param {Awaited<ReturnType<typeof inspectPage>>} m
 * @param {import('../config.js').PipelineConfig['classifier']} c
 * @returns {{ route: 'vector_extraction'|'vision_agent', reason: string }}
 */
export function decidePdfPage(m, c) {
  const rasterDominant = m.imageCoverage >= c.rasterDominantCoverage;
  const textOk = m.legibleChars >= c.minLegibleChars && m.legibleRatio >= c.minLegibleRatio;
  if (rasterDominant && m.pathSegments < c.richVectorSegments) {
    // a scan with an OCR text layer still has its GEOMETRY in the raster
    return { route: 'vision_agent', reason: textOk ? 'scanned_pdf_raster_dominant_with_text_layer' : 'scanned_pdf_no_vector_data' };
  }
  if (m.pathSegments >= c.minVectorSegments) {
    return { route: 'vector_extraction', reason: textOk || m.textChars === 0 ? 'vector_paths' : 'vector_paths_text_garbled' };
  }
  if (textOk) return { route: 'vector_extraction', reason: 'text_only_vector_page' };
  if (m.imageCount > 0) return { route: 'vision_agent', reason: 'scanned_pdf_no_vector_data' };
  return { route: 'vision_agent', reason: 'pdf_page_negligible_content' };
}
