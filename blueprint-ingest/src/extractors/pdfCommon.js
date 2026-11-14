/**
 * Shared pdfjs-dist helpers used by BOTH the classifier (is this PDF vector or scanned?) and the
 * PDF extractor. Keeps the one fragile piece - pdfjs' operator-list format - in a single place.
 *
 * pdfjs changed the `constructPath` argument layout between majors:
 *   - 6.x (and recent 5.x):  args = [paintOp, [pathData], minMax]   pathData codes: 0 move, 1 line, 2 cubic, 3 quad, 4 close
 *   - older (<= 4.x):        args = [opsArray, coordsArray]          and the paint op is a separate operator
 * Both are normalised here to { paint, subpaths }.
 */

import { IDENTITY, matMul, matApply } from '../geometry/geom.js';

/** @type {Promise<any>|null} */
let pdfjsPromise = null;

/** Lazily load the Node-compatible ("legacy") pdfjs build. */
export function getPdfjs() {
  pdfjsPromise ??= import('pdfjs-dist/legacy/build/pdf.mjs');
  return pdfjsPromise;
}

/**
 * @param {Buffer|Uint8Array} buffer
 */
export async function openPdf(buffer) {
  const pdfjs = await getPdfjs();
  const task = pdfjs.getDocument({
    data: new Uint8Array(buffer), // copy: pdfjs may detach the array it is given
    isEvalSupported: false,
    useSystemFonts: false,
    disableFontFace: true,
    verbosity: 0,
  });
  const doc = await task.promise;
  // Releasing resources lives on the loading task in pdfjs 6.x (older versions also had doc.destroy()).
  const close = async () => { try { await (task.destroy?.() ?? doc.destroy?.()); } catch { /* already closed */ } };
  return { pdfjs, doc, close };
}

/** @param {number} n */
function cubic(p0, p1, p2, p3, n) {
  const out = [];
  for (let i = 1; i <= n; i++) {
    const t = i / n, u = 1 - t;
    out.push([
      u * u * u * p0[0] + 3 * u * u * t * p1[0] + 3 * u * t * t * p2[0] + t * t * t * p3[0],
      u * u * u * p0[1] + 3 * u * u * t * p1[1] + 3 * u * t * t * p2[1] + t * t * t * p3[1],
    ]);
  }
  return out;
}

function quad(p0, c, p2, n) {
  const out = [];
  for (let i = 1; i <= n; i++) {
    const t = i / n, u = 1 - t;
    out.push([u * u * p0[0] + 2 * u * t * c[0] + t * t * p2[0], u * u * p0[1] + 2 * u * t * c[1] + t * t * p2[1]]);
  }
  return out;
}

/**
 * Parse the new (6.x) flat path data.
 * @param {ArrayLike<number>} d @param {number} samples
 * @returns {Array<{pts:number[][], closed:boolean, segments:number}>}
 */
function subpathsFromFlat(d, samples) {
  const subs = [];
  let cur = null;
  const begin = (p) => { cur = { pts: [p], closed: false, segments: 0 }; subs.push(cur); };
  for (let i = 0; i < d.length;) {
    const code = d[i++];
    if (code === 0) { begin([d[i], d[i + 1]]); i += 2; }
    else if (code === 1) { if (!cur) begin([d[i], d[i + 1]]); else { cur.pts.push([d[i], d[i + 1]]); cur.segments++; } i += 2; }
    else if (code === 2) {
      const p0 = cur ? cur.pts[cur.pts.length - 1] : [d[i], d[i + 1]];
      if (!cur) begin(p0);
      cur.pts.push(...cubic(p0, [d[i], d[i + 1]], [d[i + 2], d[i + 3]], [d[i + 4], d[i + 5]], samples)); cur.segments++; i += 6;
    } else if (code === 3) {
      const p0 = cur ? cur.pts[cur.pts.length - 1] : [d[i], d[i + 1]];
      if (!cur) begin(p0);
      cur.pts.push(...quad(p0, [d[i], d[i + 1]], [d[i + 2], d[i + 3]], samples)); cur.segments++; i += 4;
    } else if (code === 4) { if (cur) { cur.closed = true; cur.segments++; } }
    else break; // unknown code: stop rather than mis-parse
  }
  return subs;
}

/**
 * Parse the legacy op list + coords form.
 * @param {number[]} ops @param {ArrayLike<number>} c @param {any} OPS @param {number} samples
 */
function subpathsFromLegacy(ops, c, OPS, samples) {
  const subs = [];
  let cur = null, k = 0;
  const begin = (p) => { cur = { pts: [p], closed: false, segments: 0 }; subs.push(cur); };
  for (const op of ops) {
    if (op === OPS.moveTo) { begin([c[k], c[k + 1]]); k += 2; }
    else if (op === OPS.lineTo) { if (!cur) begin([c[k], c[k + 1]]); else { cur.pts.push([c[k], c[k + 1]]); cur.segments++; } k += 2; }
    else if (op === OPS.curveTo) { const p0 = cur.pts[cur.pts.length - 1]; cur.pts.push(...cubic(p0, [c[k], c[k + 1]], [c[k + 2], c[k + 3]], [c[k + 4], c[k + 5]], samples)); cur.segments++; k += 6; }
    else if (op === OPS.curveTo2) { const p0 = cur.pts[cur.pts.length - 1]; cur.pts.push(...cubic(p0, p0, [c[k], c[k + 1]], [c[k + 2], c[k + 3]], samples)); cur.segments++; k += 4; }
    else if (op === OPS.curveTo3) { const p0 = cur.pts[cur.pts.length - 1]; cur.pts.push(...cubic(p0, [c[k], c[k + 1]], [c[k + 2], c[k + 3]], [c[k + 2], c[k + 3]], samples)); cur.segments++; k += 4; }
    else if (op === OPS.closePath) { if (cur) { cur.closed = true; cur.segments++; } }
    else if (op === OPS.rectangle) {
      const [x, y, w, h] = [c[k], c[k + 1], c[k + 2], c[k + 3]]; k += 4;
      subs.push({ pts: [[x, y], [x + w, y], [x + w, y + h], [x, y + h]], closed: true, segments: 4 });
      cur = null;
    }
  }
  return subs;
}

/**
 * Walk a page's operator list, tracking the CTM, and report drawn paths and images.
 * Coordinates reported to the callbacks are PDF user space AFTER the CTM (i.e. page space, y-up).
 *
 * @param {{fnArray:number[], argsArray:any[]}} opList
 * @param {any} OPS pdfjs.OPS
 * @param {{ curveSamples?: number, onPath?: (p:{ paint:'stroke'|'fill'|'both'|'none', subpaths:Array<{pts:number[][],closed:boolean,segments:number}> }) => void, onImage?: (img:{ area:number, bbox:number[] }) => void }} handlers
 */
export function scanOperators(opList, OPS, handlers) {
  const samples = handlers.curveSamples ?? 6;
  const PAINT = new Map([
    [OPS.stroke, 'stroke'], [OPS.closeStroke, 'stroke'],
    [OPS.fill, 'fill'], [OPS.eoFill, 'fill'],
    [OPS.fillStroke, 'both'], [OPS.eoFillStroke, 'both'], [OPS.closeFillStroke, 'both'], [OPS.closeEOFillStroke, 'both'],
    [OPS.endPath, 'none'],
  ]);
  let ctm = IDENTITY;
  const stack = [];
  let legacyPending = null;

  const emit = (paint, subs) => {
    const mapped = subs
      .map((s) => ({ pts: s.pts.map((p) => matApply(ctm, p)), closed: s.closed, segments: s.segments }))
      .filter((s) => s.pts.length >= 2);
    if (mapped.length) handlers.onPath?.({ paint, subpaths: mapped });
  };

  for (let i = 0; i < opList.fnArray.length; i++) {
    const fn = opList.fnArray[i], args = opList.argsArray[i];
    if (fn === OPS.save) stack.push(ctm);
    else if (fn === OPS.restore) ctm = stack.pop() ?? IDENTITY;
    else if (fn === OPS.transform) ctm = matMul(/** @type {any} */ (Array.from(args)), ctm);
    else if (fn === OPS.constructPath) {
      if (typeof args[0] === 'number') {
        // 6.x: paint op is bundled
        const data = Array.isArray(args[1]) ? args[1][0] : args[1];
        const paint = PAINT.get(args[0]);
        if (paint && data) emit(paint, subpathsFromFlat(data, samples));
      } else {
        legacyPending = subpathsFromLegacy(args[0], args[1], OPS, samples);
      }
    } else if (PAINT.has(fn) && legacyPending) {
      emit(PAINT.get(fn), legacyPending);
      legacyPending = null;
    } else if (
      fn === OPS.paintImageXObject || fn === OPS.paintInlineImageXObject || fn === OPS.paintImageMaskXObject ||
      fn === OPS.paintImageXObjectRepeat || fn === OPS.paintJpegXObject
    ) {
      const corners = [[0, 0], [1, 0], [1, 1], [0, 1]].map((p) => matApply(ctm, /** @type {any} */ (p)));
      const area = Math.abs(ctm[0] * ctm[3] - ctm[1] * ctm[2]);
      const xs = corners.map((p) => p[0]), ys = corners.map((p) => p[1]);
      handlers.onImage?.({ area, bbox: [Math.min(...xs), Math.min(...ys), Math.max(...xs), Math.max(...ys)] });
    }
  }
}

// ---- text legibility ---------------------------------------------------------------

const LEGIBLE = /[\p{L}\p{N}\p{P}\p{Sc}°²³±×÷+=<>|~^]/u;
// eslint-disable-next-line no-control-regex
const GARBLED = /[�-\u0000-\u0008\u000E-\u001F]/u;

/**
 * @param {string} text
 * @returns {{ chars:number, legible:number, garbled:number }}
 */
export function measureText(text) {
  let chars = 0, legible = 0, garbled = 0;
  for (const ch of text.replace(/\s+/g, '')) {
    chars++;
    if (GARBLED.test(ch)) garbled++;
    else if (LEGIBLE.test(ch)) legible++;
  }
  return { chars, legible, garbled };
}

/**
 * Collect the metrics the classifier needs for one page.
 * @param {any} page pdfjs page
 * @param {any} pdfjs pdfjs module
 * @param {{ curveSamples?: number }} [opts]
 */
export async function inspectPage(page, pdfjs, opts = {}) {
  const [x0, y0, x1, y1] = page.view;
  const pageArea = Math.max(1, (x1 - x0) * (y1 - y0));
  const textContent = await page.getTextContent();
  let chars = 0, legible = 0, garbled = 0;
  for (const it of textContent.items) {
    if (typeof it.str !== 'string') continue;
    const m = measureText(it.str);
    chars += m.chars; legible += m.legible; garbled += m.garbled;
  }
  const opList = await page.getOperatorList();
  let pathCount = 0, pathSegments = 0, imageCount = 0, imageArea = 0;
  scanOperators(opList, pdfjs.OPS, {
    curveSamples: opts.curveSamples ?? 2,
    onPath: ({ subpaths }) => {
      pathCount += 1;
      for (const s of subpaths) pathSegments += Math.max(1, s.segments);
    },
    onImage: ({ area }) => { imageCount += 1; imageArea += area; },
  });
  return {
    widthPt: x1 - x0,
    heightPt: y1 - y0,
    textChars: chars,
    legibleChars: legible,
    garbledChars: garbled,
    legibleRatio: chars ? legible / chars : 0,
    pathCount,
    pathSegments,
    imageCount,
    imageCoverage: Math.min(1, imageArea / pageArea),
  };
}
