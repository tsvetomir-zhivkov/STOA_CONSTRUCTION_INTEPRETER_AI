/**
 * CAD vector extractors: DXF (own minimal parser) and SVG (own tiny XML reader).
 * Both produce ONE Bundle (a DXF model space / an SVG is one drawing page).
 *
 * Semantics come from layer / class / id / block names via a multilingual keyword table. Geometry that cannot be
 * classified is NOT guessed at: it is counted in `semantic.unclassified` and left out.
 */

import fs from 'node:fs/promises';
import { parseDxf, INSUNITS_M } from './cad/dxfParser.js';
import { parseXml } from './cad/xml.js';
import { emptyBundle, makeCanvas } from './bundle.js';
import { bboxOfPoints, polygonArea, minAreaRect, cleanRing, circlePolygon, round, bboxesOverlap, matMul, matApply, IDENTITY } from '../geometry/geom.js';
import { classifyTextContent, parseScaleDenominator, cleanText } from './textRoles.js';

// ---- name -> class ------------------------------------------------------------------------------------------

const CLASS_RULES = [
  ['window', /window|ikkuna|fenster|fen[eê]tre|ventana|glaz/i],
  ['door', /door|ovi\b|\bovet\b|t[uü]r\b|porte|puerta|dr\b|\bdoor/i],
  ['wall', /wall|sein[aä]|mur\b|wand|muro|a-wall|partition|v[aä]ggar?/i],
  ['room', /\broom|space|zone|huone|tila|raum|pi[eè]ce|habitaci|a-area|a-room|\barea\b/i],
  ['stair', /stair|portaat|treppe|escalier|escalera/i],
  ['dimension', /dim|mitta|bema|cotation/i],
  ['text', /text|annot|label|teksti|beschrift/i],
  ['object', /furn|fixt|sanit|equip|kalust|m[oö]bel|meuble|toilet|wc\b|appliance|column|pilari|pylv/i],
];

/** @param {string} s @returns {string|null} */
export function classifyName(s) {
  for (const [cls, re] of CLASS_RULES) if (re.test(s)) return cls;
  return null;
}

const humanize = (s) => s.replace(/([a-z])([A-Z])/g, '$1 $2').replace(/[_-]+/g, ' ').trim();

// ---- shared bundle finishing -------------------------------------------------------------------------------------

const rectFromSegment = (a, b, t) => {
  const dx = b[0] - a[0], dy = b[1] - a[1];
  const l = Math.hypot(dx, dy) || 1;
  const nx = (-dy / l) * (t / 2), ny = (dx / l) * (t / 2);
  return [[a[0] + nx, a[1] + ny], [b[0] + nx, b[1] + ny], [b[0] - nx, b[1] - ny], [a[0] - nx, a[1] - ny]];
};

/**
 * Link every opening without a wall to the wall it overlaps most (bbox overlap area).
 * @param {import('./bundle.js').Bundle} b
 */
function linkOpeningsToWalls(b) {
  const wb = b.walls.map((w) => ({ key: w.key, box: bboxOfPoints(w.polygonPx) }));
  for (const o of b.openings) {
    if (o.wallKey) continue;
    const raw = bboxOfPoints(o.polygonPx);
    // inflate: a swing symbol usually only touches the wall face, it does not overlap it
    const pad = Math.max(10, 0.1 * Math.max(raw[2] - raw[0], raw[3] - raw[1]));
    const ob = [raw[0] - pad, raw[1] - pad, raw[2] + pad, raw[3] + pad];
    let best = null, bestA = 0;
    for (const w of wb) {
      if (!bboxesOverlap(ob, w.box)) continue;
      const a = (Math.min(ob[2], w.box[2]) - Math.max(ob[0], w.box[0])) * (Math.min(ob[3], w.box[3]) - Math.max(ob[1], w.box[1]));
      if (a > bestA) { bestA = a; best = w.key; }
    }
    o.wallKey = best;
  }
}

function addCoverage(b, flags) {
  const cov = (category, status, confidence, note) => b.coverage.push({ category, status, confidence, ...(note ? { note } : {}) });
  const classified = (n) => (n ? 'analysed' : 'partial');
  cov('walls', classified(b.walls.length), 0.8, b.walls.length ? undefined : 'no wall layer/class found');
  cov('rooms', classified(b.rooms.length), 0.8, b.rooms.length ? undefined : 'no room/space layer or class found');
  cov('doors', classified(b.openings.some((o) => o.kind === 'door')), 0.8);
  cov('windows', classified(b.openings.some((o) => o.kind === 'window')), 0.8);
  cov('text', b.texts.length ? 'analysed' : 'partial', 0.9);
  cov('dimensions', flags.dimensions ? 'analysed' : 'partial', 0.8);
  cov('fixtures', b.objects.length ? 'partial' : 'not_analysed', 0.5);
  cov('scale', b.scale.source === 'vector_units' ? 'analysed' : b.scale.source === 'none' ? 'not_analysed' : 'partial', b.scale.confidence);
  cov('stairs', 'not_analysed', 0.3);
  cov('symbols', 'not_analysed', 0.3);
  cov('legend', 'not_analysed', 0.3);
}

// =====================================================================================================
// DXF
// =====================================================================================================

/**
 * @param {string} filePath
 * @param {{ config: import('../config.js').PipelineConfig, logger?: any }} opts
 * @returns {Promise<import('./bundle.js').Bundle[]>}
 */
export async function extractDxf(filePath, { config }) {
  const text = await fs.readFile(filePath, 'utf8');
  if (/^\s*AutoCAD Binary DXF/.test(text.slice(0, 40))) throw new Error('Binary DXF is not supported; export as ASCII DXF');
  const dxf = parseDxf(text);
  const warnings = [];

  // ---- flatten entities (expanding INSERTs) into primitives in world coordinates ---------------------------
  /** @type {Array<{kind:'poly', pts:number[][], closed:boolean, layer:string, cls:string|null}>} */
  const polys = [];
  /** @type {Array<{layer:string, name:string, cls:string|null, pts:number[][], lineLens:number[]}>} */
  const inserts = [];
  const rawTexts = [];
  const rawDims = [];
  const unclassified = {};

  const arcPts = (c, r, a0, a1) => {
    let s = (a0 * Math.PI) / 180, e = (a1 * Math.PI) / 180;
    if (e <= s) e += 2 * Math.PI;
    const n = Math.max(4, Math.ceil(((e - s) / (Math.PI / 2)) * 12));
    return Array.from({ length: n + 1 }, (_, i) => [c[0] + r * Math.cos(s + ((e - s) * i) / n), c[1] + r * Math.sin(s + ((e - s) * i) / n)]);
  };

  /** world transform for an insert */
  const insertMatrix = (ins, base) => {
    const th = (ins.rot * Math.PI) / 180, c = Math.cos(th), s = Math.sin(th);
    // p' = R(S(p - base)) + at
    const m1 = [1, 0, 0, 1, -base[0], -base[1]];
    const m2 = [ins.sx, 0, 0, ins.sy, 0, 0];
    const m3 = [c, s, -s, c, ins.at[0], ins.at[1]];
    return matMul(matMul(m1, m2), m3);
  };

  /**
   * Collect geometry points of a block (used to size doors / objects).
   * @returns {{ pts:number[][], lineLens:number[] }}
   */
  const blockGeometry = (name, m, depth) => {
    const out = { pts: [], lineLens: [] };
    const blk = dxf.blocks.get(name);
    if (!blk || depth > config.cad.maxInsertDepth) return out;
    for (const e of blk.entities) {
      if (e.type === 'LINE') { const p = e.pts.map((q) => matApply(m, q)); out.pts.push(...p); out.lineLens.push(Math.hypot(p[1][0] - p[0][0], p[1][1] - p[0][1])); }
      else if (e.type === 'LWPOLYLINE') out.pts.push(...e.pts.map((q) => matApply(m, q)));
      else if (e.type === 'CIRCLE') out.pts.push(...circlePolygon(e.c, e.r, 12).map((q) => matApply(m, q)));
      else if (e.type === 'ARC') out.pts.push(...arcPts(e.c, e.r, e.a0, e.a1).map((q) => matApply(m, q)));
      else if (e.type === 'INSERT') {
        const sub = blockGeometry(e.name, matMul(insertMatrix(e, dxf.blocks.get(e.name)?.base ?? [0, 0]), m), depth + 1);
        out.pts.push(...sub.pts); out.lineLens.push(...sub.lineLens);
      }
    }
    return out;
  };

  for (const e of dxf.entities) {
    const cls = classifyName(e.layer);
    switch (e.type) {
      case 'LINE':
      case 'LWPOLYLINE':
        if (cls) polys.push({ kind: 'poly', pts: e.pts, closed: e.closed, layer: e.layer, cls });
        else unclassified[e.layer] = (unclassified[e.layer] ?? 0) + 1;
        break;
      case 'CIRCLE':
        if (cls) polys.push({ kind: 'poly', pts: circlePolygon(e.c, e.r, 16), closed: true, layer: e.layer, cls });
        break;
      case 'TEXT': case 'MTEXT':
        if (e.text) rawTexts.push(e);
        break;
      case 'DIMENSION':
        rawDims.push(e);
        break;
      case 'INSERT': {
        const nameCls = classifyName(e.name);
        const icls = cls === 'wall' || cls === 'room' ? cls : (nameCls ?? cls ?? 'object');
        const g = blockGeometry(e.name, insertMatrix(e, dxf.blocks.get(e.name)?.base ?? [0, 0]), 0);
        if (g.pts.length) inserts.push({ layer: e.layer, name: e.name, cls: icls, pts: g.pts, lineLens: g.lineLens });
        break;
      }
      default:
    }
  }

  // ---- unit handling & canvas -----------------------------------------------------------------------------------
  /** @type {number[][]} */
  const pool = [...polys.flatMap((p) => p.pts), ...inserts.flatMap((i) => i.pts)];
  if (!pool.length) {
    const hasText = rawTexts.length > 0;
    warnings.push(hasText ? 'No classifiable geometry layers found; only text was extracted' : 'DXF contains no usable geometry');
  }
  const ext0 = pool.length ? bboxOfPoints(pool) : [0, 0, 1, 1];
  let mPerUnit = INSUNITS_M[dxf.insUnits ?? 0];
  let unitGuessed = false;
  if (!mPerUnit) {
    unitGuessed = true;
    const span = Math.max(ext0[2] - ext0[0], ext0[3] - ext0[1]);
    mPerUnit = span > 500 ? 0.001 : span > 100 ? 0.01 : 1; // >500 units across -> millimetres, otherwise metres
    warnings.push(`DXF has no $INSUNITS; guessed ${mPerUnit === 0.001 ? 'millimetres' : mPerUnit === 0.01 ? 'centimetres' : 'metres'} from the drawing extent`);
  }
  const canvas = makeCanvas({ minX: ext0[0], minY: ext0[1], maxX: ext0[2], maxY: ext0[3] }, 1 / mPerUnit, config.cad.targetPixelSide);
  const P = (p) => /** @type {[number,number]} */ (canvas.toPx(p[0], p[1]).map((v) => round(v, 2)));
  const ppm = 1 / canvas.metresPerPx;

  const bundle = emptyBundle({
    format: 'dxf',
    extractor: { id: 'vector_dxf', name: 'DXF extractor (built-in parser)', version: '1' },
    page: { widthPx: canvas.widthPx, heightPx: canvas.heightPx, renderDpi: null, pageIndex: 0, pageCount: 1, transforms: [canvas.transform] },
    scale: { metresPerPx: canvas.metresPerPx, source: 'vector_units', confidence: unitGuessed ? 0.5 : 1, scaleText: null },
  });

  let n = 0;
  const key = (p) => `${p}${++n}`;

  // ---- rooms / walls from closed polylines -------------------------------------------------------------------
  const minRoomM2 = 1.0;
  let assumedWalls = 0;
  for (const p of polys) {
    const ring = cleanRing(p.pts, 1e-9);
    if (p.cls === 'room' && p.closed && ring.length >= 3) {
      if (polygonArea(ring) * mPerUnit * mPerUnit < minRoomM2) continue;
      bundle.rooms.push({ layer: p.layer, key: key('room'), polygonPx: ring.map(P), rawName: null, confidence: 0.8 });
    } else if (p.cls === 'wall') {
      if (p.closed && ring.length >= 3) {
        const r = minAreaRect(ring);
        if (r && r.width * mPerUnit <= 0.6) { // thin closed ring = a wall body
          bundle.walls.push({ layer: p.layer, key: key('wall'), polygonPx: ring.map(P), centerlinePx: null, thicknessPx: round(r.width * ppm * mPerUnit, 2), confidence: 0.8 });
          continue;
        }
      }
      // outline or single line: every edge becomes a centreline wall of assumed thickness
      const pts = p.closed ? [...p.pts, p.pts[0]] : p.pts;
      for (let i = 1; i < pts.length; i++) {
        const a = pts[i - 1], c = pts[i];
        if (Math.hypot(c[0] - a[0], c[1] - a[1]) * mPerUnit < 0.2) continue;
        bundle.walls.push({ layer: p.layer, key: key('wall'), polygonPx: rectFromSegment(a, c, 0.2 / mPerUnit).map(P), centerlinePx: [P(a), P(c)], thicknessPx: null, confidence: 0.4 });
        assumedWalls++;
      }
    } else if (p.cls === 'door' || p.cls === 'window') {
      if (ring.length >= 3) bundle.openings.push({ layer: p.layer, key: key(p.cls), kind: p.cls, polygonPx: ring.map(P), widthPx: null, confidence: 0.7 });
    } else if (p.cls === 'object' || p.cls === 'stair') {
      if (ring.length >= 3) bundle.objects.push({ layer: p.layer, key: key('obj'), category: p.cls === 'stair' ? 'stair' : humanize(p.layer).toLowerCase(), polygonPx: ring.map(P), confidence: 0.6 });
    }
  }
  if (assumedWalls) warnings.push(`${assumedWalls} wall edges had no drawn thickness; an assumed 0.2 m thickness was used for their polygons (low confidence)`);

  // ---- inserts ---------------------------------------------------------------------------------------------------------
  let objCapped = 0;
  for (const ins of inserts) {
    const r = minAreaRect(ins.pts);
    if (!r) continue;
    const poly = r.corners.map(P);
    if (ins.cls === 'door' || ins.cls === 'window') {
      const w = ins.lineLens.length ? Math.max(...ins.lineLens) : r.length;
      bundle.openings.push({ layer: ins.layer, properties: { block: ins.name }, key: key(ins.cls), kind: ins.cls, subtype: null, polygonPx: poly, widthPx: round(w * mPerUnit * ppm, 2), confidence: 0.75 });
    } else if (ins.cls === 'object' || ins.cls === 'stair') {
      if (bundle.objects.length >= config.cad.maxObjects) { objCapped++; continue; }
      bundle.objects.push({ layer: ins.layer, properties: { block: ins.name }, key: key('obj'), category: ins.cls === 'stair' ? 'stair' : humanize(ins.name).toLowerCase().slice(0, 40) || 'block', name: ins.name, polygonPx: poly, confidence: 0.55 });
    }
  }
  if (objCapped) warnings.push(`${objCapped} blocks beyond the cap of ${config.cad.maxObjects} were not emitted`);

  // ---- text ----------------------------------------------------------------------------------------------------------------
  for (const t of rawTexts) {
    const raw = cleanText(t.text);
    if (!raw) continue;
    const w = raw.length * t.h * 0.6, h = t.h;
    const th = (t.rot * Math.PI) / 180, c = Math.cos(th), s = Math.sin(th);
    const corners = [[0, 0], [w, 0], [w, h], [0, h]].map(([x, y]) => P([t.at[0] + x * c - y * s, t.at[1] + x * s + y * c]));
    const bb = bboxOfPoints(corners);
    bundle.texts.push({ key: key('text'), raw, bboxPx: bb.map((v) => round(v, 2)), rotationDeg: t.rot ? round(-t.rot, 1) : 0, fontSizePx: round(h * mPerUnit * ppm, 2), confidence: 0.95 });
  }
  for (const d of rawDims) {
    if (!d.p1 || !d.p2) continue;
    const dist = d.measure ?? Math.hypot(d.p2[0] - d.p1[0], d.p2[1] - d.p1[1]);
    const metres = dist * mPerUnit;
    const label = d.text && d.text !== '<>' ? cleanText(d.text) : `${round(metres * 1000, 0)}`;
    const h = (0.1 / mPerUnit);
    const bb = bboxOfPoints([P([d.mid[0] - label.length * h * 0.3, d.mid[1]]), P([d.mid[0] + label.length * h * 0.3, d.mid[1] + h])]);
    bundle.texts.push({ key: key('text'), raw: label, bboxPx: bb.map((v) => round(v, 2)), roleHint: 'dimension', value: { number: round(metres * 1000, 1), unit: 'mm' }, confidence: 0.9 });
  }

  linkOpeningsToWalls(bundle);
  addCoverage(bundle, { dimensions: rawDims.length > 0 });
  bundle.stats = { entities: dxf.entities.length, rooms: bundle.rooms.length, walls: bundle.walls.length, openings: bundle.openings.length, objects: bundle.objects.length, texts: bundle.texts.length, metresPerUnit: mPerUnit };
  bundle.semantic = { insUnits: dxf.insUnits, layers: [...dxf.layers].slice(0, 60), unclassifiedLayers: unclassified, unsupportedEntities: dxf.skipped, pxPerMetre: round(ppm, 3) };
  bundle.warnings = warnings;
  return [bundle];
}

// =====================================================================================================
// SVG
// =====================================================================================================

/** parse an SVG transform attribute into a PDF-style matrix (apply order: this matrix is applied to child coords) */
function parseTransform(str) {
  let m = IDENTITY;
  if (!str) return m;
  const re = /(matrix|translate|scale|rotate)\s*\(([^)]*)\)/g;
  let t;
  const list = [];
  while ((t = re.exec(str))) list.push([t[1], t[2].split(/[\s,]+/).filter(Boolean).map(Number)]);
  // SVG applies the LAST listed transform first to the coordinates, so compose right-to-left
  for (const [name, a] of list.reverse()) {
    let mm = IDENTITY;
    if (name === 'matrix' && a.length === 6) mm = a;
    else if (name === 'translate') mm = [1, 0, 0, 1, a[0] ?? 0, a[1] ?? 0];
    else if (name === 'scale') mm = [a[0] ?? 1, 0, 0, a[1] ?? a[0] ?? 1, 0, 0];
    else if (name === 'rotate') {
      const th = ((a[0] ?? 0) * Math.PI) / 180, c = Math.cos(th), s = Math.sin(th), cx = a[1] ?? 0, cy = a[2] ?? 0;
      mm = [c, s, -s, c, cx - c * cx + s * cy, cy - s * cx - c * cy];
    }
    m = matMul(m, mm);
  }
  return m;
}

/** SVG path 'd' -> subpaths (arcs and curves are approximated: arcs by their end points) */
export function parsePathData(d) {
  const tokens = d.match(/[a-zA-Z]|[-+]?(?:\d+\.?\d*|\.\d+)(?:[eE][-+]?\d+)?/g) ?? [];
  const subs = [];
  let cur = null, x = 0, y = 0, sx = 0, sy = 0, cmd = '', i = 0;
  const nextNum = () => Number(tokens[i++]);
  const start = (px, py) => { cur = { pts: [[px, py]], closed: false }; subs.push(cur); sx = px; sy = py; };
  const bez = (p0, p1, p2, p3) => Array.from({ length: 8 }, (_, k) => { const t = (k + 1) / 8, u = 1 - t; return [u ** 3 * p0[0] + 3 * u * u * t * p1[0] + 3 * u * t * t * p2[0] + t ** 3 * p3[0], u ** 3 * p0[1] + 3 * u * u * t * p1[1] + 3 * u * t * t * p2[1] + t ** 3 * p3[1]]; });
  while (i < tokens.length) {
    if (/[a-zA-Z]/.test(tokens[i])) cmd = tokens[i++];
    const rel = cmd === cmd.toLowerCase();
    const C = cmd.toUpperCase();
    if (C === 'Z') { if (cur) { cur.closed = true; x = sx; y = sy; } continue; }
    if (C === 'M') { const nx = nextNum(), ny = nextNum(); x = rel ? x + nx : nx; y = rel ? y + ny : ny; start(x, y); cmd = rel ? 'l' : 'L'; }
    else if (C === 'L') { const nx = nextNum(), ny = nextNum(); x = rel ? x + nx : nx; y = rel ? y + ny : ny; if (!cur) start(x, y); else cur.pts.push([x, y]); }
    else if (C === 'H') { const nx = nextNum(); x = rel ? x + nx : nx; cur?.pts.push([x, y]); }
    else if (C === 'V') { const ny = nextNum(); y = rel ? y + ny : ny; cur?.pts.push([x, y]); }
    else if (C === 'C') {
      const a = [nextNum(), nextNum(), nextNum(), nextNum(), nextNum(), nextNum()].map((v, k) => (rel ? v + (k % 2 ? y : x) : v));
      cur?.pts.push(...bez([x, y], [a[0], a[1]], [a[2], a[3]], [a[4], a[5]])); x = a[4]; y = a[5];
    } else if (C === 'S' || C === 'Q') {
      const a = [nextNum(), nextNum(), nextNum(), nextNum()].map((v, k) => (rel ? v + (k % 2 ? y : x) : v));
      cur?.pts.push(...bez([x, y], [a[0], a[1]], [a[0], a[1]], [a[2], a[3]])); x = a[2]; y = a[3];
    } else if (C === 'T') { const nx = nextNum(), ny = nextNum(); x = rel ? x + nx : nx; y = rel ? y + ny : ny; cur?.pts.push([x, y]); }
    else if (C === 'A') { i += 5; const nx = nextNum(), ny = nextNum(); x = rel ? x + nx : nx; y = rel ? y + ny : ny; cur?.pts.push([x, y]); }
    else i++; // unknown token
  }
  return subs.filter((s) => s.pts.length >= 2);
}

const numAttr = (v, d = 0) => { const n = Number.parseFloat(v); return Number.isFinite(n) ? n : d; };

/**
 * @param {string} filePath
 * @param {{ config: import('../config.js').PipelineConfig, logger?: any }} opts
 * @returns {Promise<import('./bundle.js').Bundle[]>}
 */
export async function extractSvg(filePath, { config }) {
  const xml = await fs.readFile(filePath, 'utf8');
  const root = parseXml(xml);
  const svg = root.children.find((c) => c.name === 'svg');
  if (!svg) throw new Error('No <svg> root element');
  const warnings = [];

  let vb = (svg.attrs.viewBox ?? '').split(/[\s,]+/).map(Number);
  if (vb.length !== 4 || vb.some((v) => !Number.isFinite(v))) {
    vb = [0, 0, numAttr(svg.attrs.width, 1000), numAttr(svg.attrs.height, 1000)];
    warnings.push('SVG has no viewBox; width/height used as the coordinate frame');
  }
  const s = Math.min(1, 4000 / Math.max(vb[2], vb[3]));
  const P = (p) => /** @type {[number,number]} */ ([round((p[0] - vb[0]) * s, 2), round((p[1] - vb[1]) * s, 2)]);

  const items = []; // { cls, chain, pts, closed, groupId }
  const texts = [];
  let groupSeq = 0;

  const walk = (node, m, ctx) => {
    const own = `${node.attrs.class ?? ''} ${node.attrs.id ?? ''} ${node.attrs['data-type'] ?? ''}`.trim();
    const t = parseTransform(node.attrs.transform);
    const mm = matMul(t, m);
    const ownCls = own ? classifyName(own) : null;
    const nextCtx = { cls: ownCls ?? ctx.cls, names: own ? [...ctx.names, own] : ctx.names, group: ['g', 'svg', 'symbol'].includes(node.name) && own ? ++groupSeq : ctx.group };
    const emit = (subs) => { for (const sp of subs) items.push({ cls: nextCtx.cls, names: nextCtx.names, group: nextCtx.group, pts: sp.pts.map((p) => matApply(mm, p)), closed: sp.closed, tag: node.name }); };
    switch (node.name) {
      case 'polygon': case 'polyline': {
        const nums = (node.attrs.points ?? '').split(/[\s,]+/).filter(Boolean).map(Number);
        const pts = []; for (let i = 0; i + 1 < nums.length; i += 2) pts.push([nums[i], nums[i + 1]]);
        if (pts.length >= 2) emit([{ pts, closed: node.name === 'polygon' }]);
        break;
      }
      case 'rect': {
        const x = numAttr(node.attrs.x), y = numAttr(node.attrs.y), w = numAttr(node.attrs.width), h = numAttr(node.attrs.height);
        if (w > 0 && h > 0) emit([{ pts: [[x, y], [x + w, y], [x + w, y + h], [x, y + h]], closed: true }]);
        break;
      }
      case 'line': emit([{ pts: [[numAttr(node.attrs.x1), numAttr(node.attrs.y1)], [numAttr(node.attrs.x2), numAttr(node.attrs.y2)]], closed: false }]); break;
      case 'circle': case 'ellipse': {
        const rx = numAttr(node.attrs.r ?? node.attrs.rx), ry = numAttr(node.attrs.r ?? node.attrs.ry);
        const c = [numAttr(node.attrs.cx), numAttr(node.attrs.cy)];
        if (rx > 0) emit([{ pts: circlePolygon([0, 0], 1, 16).map(([a, b]) => [c[0] + a * rx, c[1] + b * ry]), closed: true }]);
        break;
      }
      case 'path': if (node.attrs.d) emit(parsePathData(node.attrs.d)); break;
      case 'text': {
        const raw = cleanText(node.text + node.children.map((c) => c.text).join(' '));
        if (raw) texts.push({ raw, at: matApply(mm, [numAttr(node.attrs.x), numAttr(node.attrs.y)]), fs: numAttr(node.attrs['font-size'], 12) * Math.hypot(mm[0], mm[1]) });
        break;
      }
      default:
    }
    if (!['text', 'defs', 'style', 'metadata', 'title', 'desc'].includes(node.name)) for (const c of node.children) walk(c, mm, nextCtx);
  };
  walk(svg, IDENTITY, { cls: null, names: [], group: 0 });

  // ---- scale -----------------------------------------------------------------------------------------------------------------
  const scaleTxt = texts.find((t) => classifyTextContent(t.raw).role === 'scale_note');
  const denom = scaleTxt ? parseScaleDenominator(scaleTxt.raw) : null;
  const mmPerUser = svg.attrs.width && /mm\s*$/.test(svg.attrs.width) && vb[2] ? numAttr(svg.attrs.width) / vb[2] : 25.4 / 96; // default: 1 user unit = 1 CSS px
  const metresPerUser = denom ? (mmPerUser / 1000) * denom : null;
  if (!denom) warnings.push('SVG carries no scale note: real-world scale is unknown (pixel scale left empty)');

  const bundle = emptyBundle({
    format: 'svg',
    extractor: { id: 'vector_svg', name: 'SVG extractor (built-in reader)', version: '1' },
    page: { widthPx: Math.max(1, Math.ceil(vb[2] * s)), heightPx: Math.max(1, Math.ceil(vb[3] * s)), renderDpi: null, pageIndex: 0, pageCount: 1, transforms: [{ kind: 'resize', from: 'svg_user_units', to: 'page_px', scale_x: s, scale_y: s, note: `viewBox origin (${vb[0]}, ${vb[1]}) moved to 0,0` }] },
    scale: metresPerUser ? { metresPerPx: metresPerUser / s, source: 'scale_note', confidence: 0.6, scaleText: scaleTxt.raw } : { metresPerPx: null, source: 'none', confidence: 0 },
  });
  const ppm = metresPerUser ? s / metresPerUser : null;

  // ---- group items by semantic group so a door's polygon+arc become ONE door --------------------------------------------------------
  const byGroup = new Map();
  for (const it of items) {
    if (!it.cls) continue;
    const k = ['door', 'window', 'object', 'stair'].includes(it.cls) ? `g${it.group}:${it.cls}` : `i${byGroup.size}`;
    (byGroup.get(k) ?? byGroup.set(k, []).get(k)).push(it);
  }
  let n = 0;
  const key = (p) => `${p}${++n}`;
  const roomName = (names) => {
    for (const nm of names.slice().reverse()) {
      const toks = nm.split(/\s+/).filter(Boolean);
      const idx = toks.findIndex((t) => /^(space|room|zone|huone|tila)$/i.test(t));
      if (idx >= 0 && toks[idx + 1]) return humanize(toks.slice(idx + 1).join(' '));
    }
    return null;
  };
  let wallEdges = 0;
  for (const grp of byGroup.values()) {
    const it0 = grp[0];
    const allPts = grp.flatMap((g) => g.pts);
    if (allPts.length < 2) continue;
    if (it0.cls === 'room') {
      for (const g of grp) {
        const ring = cleanRing(g.pts, 1e-9);
        if (g.closed && ring.length >= 3 && polygonArea(ring) > 100) bundle.rooms.push({ key: key('room'), polygonPx: ring.map(P), rawName: roomName(g.names), confidence: 0.8 });
      }
    } else if (it0.cls === 'wall') {
      for (const g of grp) {
        const ring = cleanRing(g.pts, 1e-9);
        if (g.closed && ring.length >= 3) {
          const r = minAreaRect(ring);
          bundle.walls.push({ key: key('wall'), polygonPx: ring.map(P), centerlinePx: null, thicknessPx: r ? round(r.width * s, 2) : null, confidence: 0.75 });
        } else if (g.pts.length >= 2) {
          wallEdges++;
          bundle.walls.push({ key: key('wall'), polygonPx: rectFromSegment(g.pts[0], g.pts.at(-1), 4).map(P), centerlinePx: [P(g.pts[0]), P(g.pts.at(-1))], thicknessPx: null, confidence: 0.4 });
        }
      }
    } else if (it0.cls === 'door' || it0.cls === 'window') {
      const r = minAreaRect(allPts);
      if (!r) continue;
      bundle.openings.push({ key: key(it0.cls), kind: it0.cls, subtype: null, polygonPx: r.corners.map(P), widthPx: round(r.length * s, 2), confidence: 0.7 });
    } else if (it0.cls === 'object' || it0.cls === 'stair') {
      const r = minAreaRect(allPts);
      if (r && bundle.objects.length < config.cad.maxObjects) bundle.objects.push({ key: key('obj'), category: it0.cls === 'stair' ? 'stair' : humanize(it0.names.at(-1) ?? 'object').toLowerCase().slice(0, 40), polygonPx: r.corners.map(P), confidence: 0.55 });
    }
  }
  if (wallEdges) warnings.push(`${wallEdges} open wall lines were given an assumed thickness (low confidence)`);

  for (const t of texts) {
    const w = t.raw.length * t.fs * 0.55;
    const bb = bboxOfPoints([P([t.at[0], t.at[1] - t.fs]), P([t.at[0] + w, t.at[1] + t.fs * 0.2])]);
    const k = key('text');
    bundle.texts.push({ key: k, raw: t.raw, bboxPx: bb.map((v) => round(v, 2)), fontSizePx: round(t.fs * s, 2), confidence: 0.95 });
    if (t === scaleTxt) bundle.scale.evidenceTextKey = k;
  }

  linkOpeningsToWalls(bundle);
  addCoverage(bundle, { dimensions: false });
  bundle.stats = { shapes: items.length, classified: items.filter((i) => i.cls).length, rooms: bundle.rooms.length, walls: bundle.walls.length, openings: bundle.openings.length, objects: bundle.objects.length, texts: bundle.texts.length };
  bundle.semantic = { pxPerMetre: ppm ? round(ppm, 3) : null, svgSize: [vb[2], vb[3]], arcsApproximated: true };
  bundle.warnings = warnings;
  return [bundle];
}

