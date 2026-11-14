/**
 * Vector-PDF extractor (pdfjs-dist). One Bundle per vector page.
 *
 * Deterministic output only: text runs with coordinates, closed-polygon room candidates, filled thin rectangles as
 * wall candidates, scale from a printed note ("1:100"). Semantics (room types, text roles...) are added later.
 * Doors/windows/fixtures are NOT inferred from bare vector paths; coverage says so, and the mapper/vision agent may fill them.
 *
 * Optional: `understandPdfWithGemini` uploads the PDF with the Gemini Files API (and ALWAYS deletes it in `finally`) to read
 * legend/schedule-like pages that geometry cannot explain.
 */

import fs from 'node:fs/promises';
import { openPdf, scanOperators, measureText } from './pdfCommon.js';
import { emptyBundle } from './bundle.js';
import { bboxOfPoints, polygonArea, polygonCentroid, pointInPolygon, cleanRing, minAreaRect, bboxIou, round } from '../geometry/geom.js';
import { classifyTextContent, parseScaleDenominator, cleanText } from './textRoles.js';

const PT_MM = 25.4 / 72;

/**
 * @param {string} filePath
 * @param {{ config: import('../config.js').PipelineConfig, logger?: any, pages?: number[] }} opts zero-based page indices (default: all)
 * @returns {Promise<import('./bundle.js').Bundle[]>}
 */
export async function extractPdf(filePath, { config, logger, pages }) {
  const buf = await fs.readFile(filePath);
  const { pdfjs, doc, close } = await openPdf(buf);
  try {
    const out = [];
    const wanted = pages ?? Array.from({ length: doc.numPages }, (_, i) => i);
    for (const idx of wanted) {
      if (idx < 0 || idx >= doc.numPages) continue;
      const page = await doc.getPage(idx + 1);
      try {
        out.push(await pageBundle(page, pdfjs, idx, config, logger, doc.numPages));
      } finally {
        page.cleanup?.();
      }
    }
    return out;
  } finally {
    await close();
  }
}

async function pageBundle(page, pdfjs, idx, config, logger, pageCount) {
  const [x0, y0, x1, y1] = page.view;
  const wPt = x1 - x0, hPt = y1 - y0;
  const dpi = Math.min(config.pdf.maxRenderDpi, (config.pdf.maxPixelSide / Math.max(wPt, hPt)) * 72);
  const k = dpi / 72; // px per pt
  const P = (p) => /** @type {[number,number]} */ ([round((p[0] - x0) * k, 2), round((y1 - p[1]) * k, 2)]);
  const warnings = [];

  const bundle = emptyBundle({
    format: 'pdf',
    extractor: { id: 'vector_pdf', name: 'PDF vector extractor (pdfjs-dist)', version: pdfjs.version ?? null },
    page: { widthPx: Math.round(wPt * k), heightPx: Math.round(hPt * k), renderDpi: round(dpi, 1), pageIndex: idx, pageCount, transforms: [{ kind: 'resize', from: 'pdf_points', to: 'page_px', scale_x: round(k, 6), scale_y: round(k, 6), note: `rendered at ${round(dpi, 1)} dpi, y flipped (PDF origin bottom-left)` }] },
  });

  // ---- text -----------------------------------------------------------------------------------------------------------
  const tc = await page.getTextContent();
  let runs = [];
  for (const it of tc.items) {
    if (typeof it.str !== 'string' || !it.str.trim()) continue;
    const [a, b, , , e, f] = it.transform;
    const h = Math.hypot(a, b) || it.height || 10;
    const rot = (Math.atan2(b, a) * 180) / Math.PI;
    const w = it.width || it.str.length * h * 0.5;
    const th = (rot * Math.PI) / 180, c = Math.cos(th), s = Math.sin(th);
    runs.push({ str: it.str, e, f, h, rot, w, cos: c, sin: s });
  }
  runs = mergeRuns(runs);
  let nText = 0;
  for (const r of runs) {
    const raw = cleanText(r.str);
    if (!raw) continue;
    const m = measureText(raw);
    if (m.chars && m.legible / m.chars < 0.5) continue; // glyph soup
    const corners = [[0, 0], [r.w, 0], [r.w, r.h], [0, r.h]].map(([u, v]) => P([r.e + u * r.cos - v * r.sin, r.f + u * r.sin + v * r.cos]));
    const bb = bboxOfPoints(corners);
    const cls = classifyTextContent(raw);
    bundle.texts.push({ key: `text${++nText}`, raw, bboxPx: bb.map((v) => round(v, 2)), rotationDeg: round(-r.rot, 1), fontSizePx: round(r.h * k, 2), value: cls.value ?? null, confidence: 0.95 });
  }

  // ---- geometry ---------------------------------------------------------------------------------------------------------
  const opList = await page.getOperatorList();
  const pageArea = wPt * hPt;
  /** @type {Array<{ring:number[][], area:number, filled:boolean}>} */
  const closed = [];
  let segs = 0, imgArea = 0;
  scanOperators(opList, pdfjs.OPS, {
    curveSamples: config.pdf.curveSamples,
    onPath: ({ paint, subpaths }) => {
      for (const sp of subpaths) {
        segs += Math.max(1, sp.segments);
        // treat as closed when explicitly closed or when the end meets the start (re / stroked loops)
        const first = sp.pts[0], last = sp.pts[sp.pts.length - 1];
        const loops = sp.closed || Math.hypot(first[0] - last[0], first[1] - last[1]) < 0.01 * Math.sqrt(pageArea) * 0.01;
        if (!loops || sp.pts.length < 4 - (sp.closed ? 1 : 0)) continue;
        const ring = cleanRing(sp.pts, 1e-6);
        if (ring.length < 3) continue;
        closed.push({ ring, area: polygonArea(ring), filled: paint === 'fill' || paint === 'both' });
      }
    },
    onImage: ({ area }) => { imgArea += area; },
  });
  if (imgArea / pageArea > 0.3) warnings.push('Page also contains large embedded raster(s); only vector content was extracted');

  // rooms: mid-sized closed polygons, de-duplicated, without "container" outlines
  const minA = config.pdf.minRoomAreaFrac * pageArea, maxA = config.pdf.maxRoomAreaFrac * pageArea;
  let cands = closed.filter((c) => c.area >= minA && c.area <= maxA && !(c.filled && isThin(c.ring)));
  cands = cands.filter((c, i) => cands.findIndex((d) => bboxIou(bboxOfPoints(c.ring), bboxOfPoints(d.ring)) > 0.9) === i);
  const cents = cands.map((c) => polygonCentroid(c.ring));
  const rooms = cands.filter((c, i) => {
    let inside = 0;
    cands.forEach((d, j) => { if (j !== i && d.area < c.area * 0.95 && pointInPolygon(cents[j], c.ring)) inside++; });
    return inside < 2; // an outline that encloses two or more other candidates is a container, not a room
  });
  for (const r of rooms) bundle.rooms.push({ key: `room${bundle.rooms.length + 1}`, polygonPx: r.ring.map(P), rawName: null, confidence: 0.6 });

  // walls: filled thin rectangles
  for (const c of closed) {
    if (!c.filled || c.area < 1) continue;
    const r = minAreaRect(c.ring);
    if (!r || r.width <= 0 || r.length / r.width < 4 || r.length < 0.02 * Math.sqrt(pageArea)) continue;
    if (bundle.walls.length >= 3000) { warnings.push('wall candidates capped at 3000'); break; }
    bundle.walls.push({ key: `wall${bundle.walls.length + 1}`, polygonPx: c.ring.map(P), centerlinePx: null, thicknessPx: round(r.width * k, 2), confidence: 0.45 });
  }

  // ---- scale from a printed note -----------------------------------------------------------------------------------------------
  const noteText = bundle.texts.find((t) => classifyTextContent(t.raw).role === 'scale_note');
  if (noteText) {
    const denom = parseScaleDenominator(noteText.raw);
    const metresPerPt = (PT_MM / 1000) * denom;
    bundle.scale = { metresPerPx: metresPerPt / k, source: 'scale_note', confidence: 0.6, scaleText: noteText.raw, evidenceTextKey: noteText.key };
    warnings.push('scale derived from the printed note assuming the PDF page is at its true print size; verify with a dimension');
  } else {
    warnings.push('no scale note found on this page');
  }

  const textChars = bundle.texts.reduce((n, t) => n + t.raw.length, 0);
  const cov = (category, status, confidence, note) => bundle.coverage.push({ category, status, confidence, ...(note ? { note } : {}) });
  cov('text', bundle.texts.length ? 'analysed' : 'not_analysed', 0.9);
  cov('rooms', bundle.rooms.length ? 'partial' : 'not_analysed', 0.6, 'closed polygons only; open-plan rooms may be missed');
  cov('walls', bundle.walls.length ? 'partial' : 'not_analysed', 0.45, 'only filled thin rectangles are treated as walls');
  cov('doors', 'not_analysed', 0.3, 'doors are not inferred from bare PDF paths');
  cov('windows', 'not_analysed', 0.3);
  cov('fixtures', 'not_analysed', 0.3);
  cov('stairs', 'not_analysed', 0.3);
  cov('symbols', 'not_analysed', 0.3);
  cov('dimensions', bundle.texts.some((t) => t.value && classifyTextContent(t.raw).role === 'dimension') ? 'partial' : 'not_analysed', 0.5);
  cov('scale', bundle.scale.source === 'none' ? 'not_analysed' : 'partial', bundle.scale.confidence);
  cov('legend', 'not_analysed', 0.3);

  const looksLikeSchedule = textChars > 800 && bundle.rooms.length === 0;
  bundle.stats = { pathSegments: segs, closedShapes: closed.length, rooms: bundle.rooms.length, walls: bundle.walls.length, texts: bundle.texts.length, textChars };
  bundle.semantic = { pageSizePt: [round(wPt, 1), round(hPt, 1)], looksLikeLegendOrSchedule: looksLikeSchedule, pxPerPt: round(k, 4) };
  bundle.warnings = warnings;
  logger?.debug?.(`pdf page ${idx + 1}: ${bundle.rooms.length} rooms, ${bundle.walls.length} walls, ${bundle.texts.length} texts`);
  return bundle;
}

function isThin(ring) {
  const r = minAreaRect(ring);
  return !!r && r.width > 0 && r.length / r.width >= 4;
}

/** Join neighbouring same-baseline runs ("12,5" + "m2") so area / dimension strings stay whole. */
function mergeRuns(runs) {
  const sorted = [...runs].sort((a, b) => a.rot - b.rot || Math.round(b.f) - Math.round(a.f) || a.e - b.e);
  const out = [];
  for (const r of sorted) {
    const p = out[out.length - 1];
    if (p && Math.abs(p.rot - r.rot) < 1 && Math.abs(p.f - r.f) < 0.25 * p.h && r.rot === 0) {
      const gap = r.e - (p.e + p.w);
      if (gap < 0.6 * p.h && gap > -0.2 * p.h && Math.abs(p.h - r.h) < 0.2 * p.h) {
        const space = gap > 0.15 * p.h ? ' ' : '';
        p.str += space + r.str; p.w = r.e + r.w - p.e; continue;
      }
    }
    out.push({ ...r });
  }
  return out;
}

// =====================================================================================================
// optional Gemini Files-API understanding of whole PDF pages (legends / schedules / title blocks)
// =====================================================================================================

const UNDERSTAND_SCHEMA = {
  type: 'object',
  properties: {
    page_kind: { type: 'string', enum: ['floor_plan', 'section', 'elevation', 'site_plan', 'detail', 'legend', 'schedule', 'title_sheet', 'other'] },
    title: { type: 'string', nullable: true },
    level: { type: 'string', nullable: true },
    scale_note: { type: 'string', nullable: true },
    legend: { type: 'array', items: { type: 'object', properties: { symbol: { type: 'string' }, meaning: { type: 'string' } }, required: ['symbol', 'meaning'] } },
  },
  required: ['page_kind', 'legend'],
};

/**
 * Upload the PDF, ask for page-level facts, delete the upload no matter what.
 * `gemini` is the client from mappers/geminiClient.js (uploadFile / deleteFile / generateJson).
 *
 * @param {string} filePath
 * @param {{ gemini: any, pageIndex: number, logger?: any }} opts
 * @returns {Promise<{ page_kind:string, title?:string|null, level?:string|null, scale_note?:string|null, legend:Array<{symbol:string,meaning:string}> } | null>}
 */
export async function understandPdfWithGemini(filePath, { gemini, pageIndex, logger }) {
  if (!gemini?.isConfigured) return null;
  let uploaded = null;
  try {
    uploaded = await gemini.uploadFile(filePath, 'application/pdf');
    const { json } = await gemini.generateJson({
      tier: 'primary',
      label: 'pdf_page_understanding',
      prompt: `You are reading page ${pageIndex + 1} (1-based) of an architectural drawing PDF. Report only what is printed on that page: its kind, title, level, printed scale note, and legend entries (symbol -> meaning). Do not invent anything; use null when absent.`,
      schema: UNDERSTAND_SCHEMA,
      file: uploaded,
    });
    return json;
  } catch (err) {
    logger?.warn?.(`Gemini PDF understanding failed: ${err.message}`);
    return null;
  } finally {
    if (uploaded?.name) {
      try { await gemini.deleteFile(uploaded.name); } catch (err) { logger?.warn?.(`could not delete uploaded file ${uploaded.name}: ${err.message}`); }
    }
  }
}
