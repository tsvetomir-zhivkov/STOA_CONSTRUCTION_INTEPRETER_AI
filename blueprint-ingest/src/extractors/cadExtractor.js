/**
 * CAD vector extractors: DXF (own minimal parser) and SVG (own tiny XML reader).
 * Both produce ONE Bundle (a DXF model space / an SVG is one drawing page).
 *
 * Semantics come from layer / class / id / block names via a multilingual keyword table. Geometry that cannot be
 * classified is NOT guessed at: it is counted in `semantic.unclassified` and left out.
 */

import fs from 'node:fs/promises';
import { parseDxf, INSUNITS_M } from './cad/dxfParser.js';

const INSUNITS_NAME = { 1: 'INCH', 2: 'FOOT', 4: 'MILLIMETRE', 5: 'CENTIMETRE', 6: 'METRE', 7: 'KILOMETRE', 10: 'YARD', 13: 'MICROMETRE', 14: 'DECIMETRE', 15: 'DECAMETRE', 16: 'HECTOMETRE' };
import { parseXml } from './cad/xml.js';
import { emptyBundle, makeCanvas } from './bundle.js';
import { bboxOfPoints, polygonArea, minAreaRect, cleanRing, circlePolygon, round, bboxesOverlap, matMul, matApply, IDENTITY, pointInPolygon } from '../geometry/geom.js';
import { wallsFromSegments } from './dxfWalls.js';
import { detectFlights, assembleStaircases, STAIR_LAYER_RE } from './stairs.js';
import { classifyFixture } from './fixtures.js';
import { classifyFixturePrimitives } from './dxfFixtures.js';
import { policyConfidence, FLAGS } from './confidence.js';
import { lookupRoomName, foldText } from '../mappers/glossary.js';
import { classifyTextContent, parseScaleDenominator, cleanText, looksLikeRoomNumber } from './textRoles.js';

// ---- name -> class ------------------------------------------------------------------------------------------

const CLASS_RULES = [
  ['window', /window|ikkuna|fenster|fen[eê]tre|ventana|glaz/i],
  ['door', /door|ovi\b|\bovet\b|t[uü]r\b|porte|puerta|dr\b|\bdoor/i],
  ['wall', /wall|sein[aä]|mur\b|wand|muro|a-wall|partition|v[aä]ggar?/i],
  // apartment outlines (before 'room': an apartment is not a room). Swiss Dwellings: UNIT_OUTLINE
  ['unit', /unit[ _-]?outline|^units?$|apartment|wohnung|logement|appartamento|huoneisto/i],
  ['room', /\broom|space|zone|huone|tila|raum|pi[eè]ce|habitaci|a-area|a-room|\barea\b/i],
  ['stair', STAIR_LAYER_RE],
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
  const stairs = flags.stairs ?? { status: 'not_analysed', confidence: 0.3 };
  const cov = (category, status, confidence, note) => b.coverage.push({ category, status, confidence, ...(note ? { note } : {}) });
  const classified = (n) => (n ? 'analysed' : 'partial');
  cov('walls', classified(b.walls.length), 0.8, b.walls.length ? undefined : 'no wall layer/class found');
  cov('rooms', classified(b.rooms.length), 0.8, b.rooms.length ? undefined : 'no room/space layer or class found');
  cov('doors', classified(b.openings.some((o) => o.kind === 'door')), 0.8);
  cov('windows', classified(b.openings.some((o) => o.kind === 'window')), 0.8);
  cov('text', b.texts.length ? 'analysed' : 'partial', 0.9);
  cov('dimensions', flags.dimensions ? 'analysed' : 'partial', 0.8);
  // fixtures: analysed where every primitive of a fixture layer went through the shape rules (a zero count is then a finding)
  if (flags.fixtures) cov('fixtures', 'analysed', 0.7, `shape rules on layer ${flags.fixtures.layers.join(', ')}: ${flags.fixtures.primitives} primitive(s) -> ${Object.entries(flags.fixtures.counts).map(([k, v]) => `${v} ${k}`).join(', ') || 'no fixtures'}`);
  else cov('fixtures', b.objects.length ? 'partial' : 'not_analysed', 0.5);
  cov('scale', b.scale.source === 'vector_units' ? 'analysed' : b.scale.source === 'none' ? 'not_analysed' : 'partial', b.scale.confidence);
  cov('stairs', stairs.status, stairs.confidence, stairs.note);
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
export async function extractDxf(filePath, { config, logger, corrections }) {
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
  /** every LINE / LWPOLYLINE segment of the drawing, any layer (stair treads are looked for in all of them) */
  const allSegs = [];
  // human corrections "layer:NAME": { as: "<class>" } replace the keyword classification of that layer
  const layerAs = corrections?.layerAs ?? new Map();
  const correctedLayers = new Map(); // folded layer -> rule key
  const layerClass = (layer) => {
    const k = foldText(layer ?? '');
    if (layerAs.has(k)) { correctedLayers.set(k, layerAs.get(k).rule); return layerAs.get(k).as; }
    return classifyName(layer ?? '');
  };
  const correction = (layer) => correctedLayers.get(foldText(layer ?? '')) ?? null;

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
    const cls = layerClass(e.layer);
    switch (e.type) {
      case 'LINE':
      case 'LWPOLYLINE':
        for (let i = 1; i < e.pts.length + (e.closed ? 1 : 0); i++) allSegs.push({ a: e.pts[i - 1], b: e.pts[i % e.pts.length], layer: e.layer, cls });
        if (cls) polys.push({ kind: 'poly', pts: e.pts, closed: e.closed, layer: e.layer, cls, handle: e.handle ?? null, type: e.type });
        else unclassified[e.layer] = (unclassified[e.layer] ?? 0) + 1;
        break;
      case 'CIRCLE':
        if (cls) polys.push({ kind: 'poly', pts: circlePolygon(e.c, e.r, 16), closed: true, layer: e.layer, cls, handle: e.handle ?? null, type: e.type, circle: { c: e.c, r: e.r } });
        break;
      case 'TEXT': case 'MTEXT':
        if (e.text) rawTexts.push(e);
        break;
      case 'DIMENSION':
        rawDims.push(e);
        break;
      case 'INSERT': {
        const nameCls = layerAs.has(foldText(e.layer ?? '')) ? null : classifyName(e.name);
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
    // schema v0.3 project.units: DXF declares a length unit only ($INSUNITS); areas and volumes are derived in m2 / m3
    declaredUnits: {
      length: unitGuessed
        ? { unit: mPerUnit === 0.001 ? 'MILLIMETRE' : mPerUnit === 0.01 ? 'CENTIMETRE' : 'METRE', to_si: mPerUnit, status: 'inferred', source: 'no $INSUNITS in the DXF header: guessed from the drawing extent' }
        : { unit: INSUNITS_NAME[dxf.insUnits] ?? `INSUNITS ${dxf.insUnits}`, to_si: mPerUnit, status: 'explicit', source: `DXF header $INSUNITS=${dxf.insUnits}` },
    },
  });

  let n = 0;
  const key = (p) => `${p}${++n}`;

  // ---- rooms, openings, objects from polylines; wall segments are collected for line pairing ----------------------
  // confidences follow ./confidence.js (policy values are caps: nothing rises without new evidence)
  // a closed outline on a room layer is a room however small (a 0.95 m2 "Reduit" is a numbered room); only degenerate slivers go
  const minRoomM2 = 0.01;
  const roomRings = []; // world rings of emitted rooms, for the stair search
  const wallSegs = [];
  const stairLayerSegs = [];
  /** fixture-layer primitives whose layer name names no fixture: classified by shape after the stairs (./dxfFixtures.js) */
  const fixturePrims = new Map();
  const roomCandidates = [], roomSegs = [];
  for (const p of polys) {
    const ring = cleanRing(p.pts, 1e-9);
    const corr = correction(p.layer);
    const shut = p.closed || (p.pts.length >= 3 && Math.hypot(p.pts[0][0] - p.pts.at(-1)[0], p.pts[0][1] - p.pts.at(-1)[1]) < 1e-6); // closed flag, or first = last vertex
    if (p.cls === 'room' && shut && ring.length >= 3) {
      roomCandidates.push({ p, ring, corr });
    } else if (p.cls === 'room' && p.pts.length >= 2) {
      roomSegs.push(p.pts); // open lines on a room layer: an X of two crossing lines inside an outline marks a shaft / void
    } else if (p.cls === 'wall') {
      const closed = p.closed && ring.length >= 3;
      const r = closed ? minAreaRect(ring) : null;
      const thinRing = !!(r && r.width * mPerUnit <= 0.6); // was already measured (0.8) before line pairing
      const pts = closed ? [...ring, ring[0]] : p.pts;
      for (let i = 1; i < pts.length; i++) wallSegs.push({ a: pts[i - 1], b: pts[i], layer: p.layer, ring: closed ? ring : null, thinRing });
    } else if (p.cls === 'unit') {
      // apartment outline: labelled from the unit-label text inside it (documentBuilder); rooms are linked by containment
      if (shut && ring.length >= 3) bundle.units.push({ layer: p.layer, key: key('unit'), sourceRef: p.handle, polygonPx: ring.map(P), confidence: policyConfidence({ classRead: true, measured: true }), correctedBy: corr });
    } else if (p.cls === 'door' || p.cls === 'window') {
      if (ring.length >= 3) bundle.openings.push({ layer: p.layer, key: key(p.cls), kind: p.cls, polygonPx: ring.map(P), widthPx: null, confidence: policyConfidence({ classRead: true, measured: true }, 0.7), correctedBy: corr });
    } else if (p.cls === 'stair') {
      const pts = p.closed ? [...p.pts, p.pts[0]] : p.pts;
      for (let i = 1; i < pts.length; i++) stairLayerSegs.push({ a: pts[i - 1], b: pts[i], layer: p.layer, ring: ring.length >= 3 && p.closed ? ring : null });
    } else if (p.cls === 'object') {
      const fx = classifyFixture({ block: null, layer: p.layer });
      if (!fx.category) {
        // the layer name does not say which fixture: every primitive (closed or open, circles too) goes to the shape rules
        if (!fixturePrims.has(p.layer)) fixturePrims.set(p.layer, []);
        fixturePrims.get(p.layer).push(p.circle ? { handle: p.handle, type: 'CIRCLE', c: p.circle.c, r: p.circle.r } : { handle: p.handle, type: p.type, pts: p.pts, closed: p.closed });
        continue;
      }
      if (ring.length < 3) continue;
      bundle.objects.push({
        layer: p.layer, key: key('obj'), category: fx.category ?? 'other', subtype: humanize(p.layer).toLowerCase(), polygonPx: ring.map(P),
        confidence: policyConfidence({ classRead: !!fx.category, measured: true }, 0.6), correctedBy: corr,
      });
    }
  }

  // ---- rooms: every closed outline on a room layer (X-marked ones are shafts) ------------------------------------------------
  const tol = 0.05 / mPerUnit;
  for (const { p, ring, corr } of roomCandidates) {
    const shaft = hasCrossInside(ring, roomSegs, tol);
    if (!shaft && polygonArea(ring) * mPerUnit * mPerUnit < minRoomM2) continue;
    bundle.rooms.push({
      layer: p.layer, key: key('room'), sourceRef: p.handle, polygonPx: ring.map(P), rawName: null, confidence: policyConfidence({ classRead: true, measured: true }, 0.8), correctedBy: corr,
      ...(shaft ? { typeHint: 'shaft', properties: { void_marker: 'two crossing lines inside the outline' } } : {}),
    });
    roomRings.push(ring);
  }

  // ---- walls: line pairing (measured), unpaired lines fall back to an assumed 0.2 m ------------------------------------
  const wr = wallsFromSegments(wallSegs, { mPerUnit });
  for (const w of wr.walls) {
    bundle.walls.push({
      layer: w.layer, key: key('wall'), polygonPx: w.polygon.map(P), centerlinePx: w.centerline.map(P),
      thicknessPx: round(w.thickness * mPerUnit * ppm, 2), // measured, or the assumed 0.2 m (then flagged)
      confidence: w.assumed ? policyConfidence({ classRead: true, measured: false, assumed: true }) : policyConfidence({ classRead: true, measured: true }, w.thinRing ? 0.8 : 1),
      flags: w.assumed ? [FLAGS.assumedThickness] : [], correctedBy: correction(w.layer),
      ...(w.merged || w.joined ? { properties: { ...(w.merged ? { merged_from: w.merged + 1 } : {}), ...(w.joined ? { joined_ends: w.joined } : {}) } } : {}),
    });
  }
  if (wr.stats.assumed) warnings.push(`${wr.stats.assumed} wall lines had no parallel partner; an assumed 0.2 m thickness was used for them (confidence 0.4, flag assumed_thickness)`);
  logger?.info?.(`walls: ${wr.stats.measured} measured, ${wr.stats.assumed} assumed, ${wr.stats.merged} merged, ${wr.stats.bridged} bridged across T/X junctions, ${wr.stats.junctions} junctions closed (${wr.stats.corners} corners, ${wr.stats.tees} tees; ${wr.stats.covered} assumed pieces inside measured walls dropped, ${wr.stats.solidRings ?? 0} small closed outlines kept as solid walls, ${wr.stats.pairs} line pairs, ${wr.stats.capsDropped} end caps dropped)`);

  // ---- inserts ---------------------------------------------------------------------------------------------------------
  let objCapped = 0;
  for (const ins of inserts) {
    const r = minAreaRect(ins.pts);
    if (!r) continue;
    const poly = r.corners.map(P);
    if (ins.cls === 'door' || ins.cls === 'window') {
      const w = ins.lineLens.length ? Math.max(...ins.lineLens) : r.length;
      bundle.openings.push({ layer: ins.layer, properties: { source_block: ins.name }, key: key(ins.cls), kind: ins.cls, subtype: null, polygonPx: poly, widthPx: round(w * mPerUnit * ppm, 2), confidence: policyConfidence({ classRead: true, measured: true }, 0.75), correctedBy: correction(ins.layer) });
    } else if (ins.cls === 'object' || ins.cls === 'stair') {
      if (bundle.objects.length >= config.cad.maxObjects) { objCapped++; continue; }
      const fx = ins.cls === 'stair' ? { category: 'stair' } : classifyFixture({ block: ins.name, layer: ins.layer });
      bundle.objects.push({
        layer: ins.layer, properties: { source_block: ins.name }, key: key('obj'), category: fx.category ?? 'other', subtype: humanize(ins.name).toLowerCase().slice(0, 40) || 'block',
        name: ins.name, polygonPx: poly, confidence: policyConfidence({ classRead: !!fx.category, measured: true }, 0.55), correctedBy: correction(ins.layer),
      });
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

  // ---- stairs ---------------------------------------------------------------------------------------------------------------
  const stairs = findStairs({ stairLayerSegs, allSegs, roomRings, rawTexts, mPerUnit, ppm, P, key, correction });
  bundle.objects.push(...stairs.objects);
  if (stairs.objects.length || stairs.note) logger?.info?.(`stairs: ${stairs.objects.length} staircase(s), ${stairs.flights} flight(s), ${stairs.landings} landing(s) (${stairs.byLayer} by layer, ${stairs.byRoom} by tread lines in stair rooms)`);

  // ---- fixtures by shape (fixture layers whose name names no fixture, e.g. Swiss Dwellings "FIXTURE") -------------------------
  const fixtureStats = emitShapeFixtures({ fixturePrims, bundle, mPerUnit, P, key, correction });
  if (fixtureStats.layers.length) logger?.info?.(`fixtures by shape on ${fixtureStats.layers.join(', ')}: ${Object.entries(fixtureStats.counts).map(([k, v]) => `${v} ${k}`).join(', ')}${fixtureStats.mergedStairs ? ` (${fixtureStats.mergedStairs} stair group(s) joined to a stair already found)` : ''}`);

  linkOpeningsToWalls(bundle);
  addCoverage(bundle, { dimensions: rawDims.length > 0, stairs: stairs.coverage, fixtures: fixtureStats.layers.length ? fixtureStats : null });
  bundle.stats = { wallsMeasured: wr.stats.measured, wallsAssumed: wr.stats.assumed, wallsMerged: wr.stats.merged, wallJunctions: wr.stats.junctions, stairs: stairs.objects.length, entities: dxf.entities.length, rooms: bundle.rooms.length, walls: bundle.walls.length, openings: bundle.openings.length, objects: bundle.objects.length, texts: bundle.texts.length, metresPerUnit: mPerUnit };
  bundle.semantic = { insUnits: dxf.insUnits, layers: [...dxf.layers].slice(0, 60), unclassifiedLayers: unclassified, unsupportedEntities: dxf.skipped, pxPerMetre: round(ppm, 3) };
  bundle.warnings = warnings;
  return [bundle];
}

/** a point list that ends where it starts (a path closed without Z, a polyline drawn back to its start) */
const isShut = (pts) => pts.length >= 4 && Math.hypot(pts[0][0] - pts.at(-1)[0], pts[0][1] - pts.at(-1)[1]) < 1e-6;

/** two of the open lines lying inside `ring` (within tol of it) cross each other: the X that marks a shaft / void */
function hasCrossInside(ring, lines, tol) {
  const inside = lines.filter((pts) => pts.every((q) => pointInPolygon(q, ring) || ring.some((a, i) => distToSeg(q, a, ring[(i + 1) % ring.length]) <= tol)));
  for (let i = 0; i < inside.length; i++) for (let j = i + 1; j < inside.length; j++) {
    const A = inside[i], B = inside[j];
    for (let a = 1; a < A.length; a++) for (let b = 1; b < B.length; b++) if (properCross(A[a - 1], A[a], B[b - 1], B[b])) return true;
  }
  return false;
}
function distToSeg(p, a, b) {
  const dx = b[0] - a[0], dy = b[1] - a[1], l2 = dx * dx + dy * dy;
  const t = l2 ? Math.max(0, Math.min(1, ((p[0] - a[0]) * dx + (p[1] - a[1]) * dy) / l2)) : 0;
  return Math.hypot(p[0] - a[0] - t * dx, p[1] - a[1] - t * dy);
}
function properCross(a, b, c, d) {
  const x = (o, p, q) => (p[0] - o[0]) * (q[1] - o[1]) - (p[1] - o[1]) * (q[0] - o[0]);
  const d1 = x(c, d, a), d2 = x(c, d, b), d3 = x(a, b, c), d4 = x(a, b, d);
  return d1 * d2 < 0 && d3 * d4 < 0;
}

/**
 * Fixture-layer primitives -> fixture objects (./dxfFixtures.js). Each object's source_ref is the DXF handle of its anchor
 * primitive; properties.part_handles lists every primitive it was built from and properties.fixture_rule the rule used.
 * A stair group that overlaps a stair already found (stair layer / stair room) is joined to that stair, not emitted twice.
 */
function emitShapeFixtures({ fixturePrims, bundle, mPerUnit, P, key, correction }) {
  const counts = {}, layers = [];
  let primitives = 0, mergedStairs = 0;
  const centre = (ring) => ring.reduce((a, p) => [a[0] + p[0] / ring.length, a[1] + p[1] / ring.length], [0, 0]);
  for (const [layer, prims] of fixturePrims) {
    layers.push(layer); primitives += prims.length;
    for (const f of classifyFixturePrimitives(prims, mPerUnit)) {
      const polygonPx = f.ring.map(P);
      if (f.category === 'stair') {
        const c = centre(polygonPx);
        const twin = bundle.objects.find((o) => o.category === 'stair' && o.polygonPx?.length >= 3 && (pointInPolygon(c, o.polygonPx) || pointInPolygon(centre(o.polygonPx), polygonPx)));
        if (twin) {
          twin.properties = { ...(twin.properties ?? {}), part_handles: [...new Set([...(twin.properties?.part_handles ?? []), ...f.parts])] };
          mergedStairs++;
          counts.stair = (counts.stair ?? 0) + 1;
          continue;
        }
      }
      const label = f.subtype ? `${f.category} (${f.subtype})` : f.category;
      counts[label] = (counts[label] ?? 0) + 1;
      bundle.objects.push({
        layer, key: key('obj'), sourceRef: f.handle, category: f.category, subtype: f.subtype, polygonPx,
        confidence: policyConfidence({ classRead: false, measured: true }, f.confidence), correctedBy: correction(layer),
        properties: { part_handles: f.parts, fixture_rule: f.rule, detected_by: 'shape' },
      });
    }
  }
  return { layers, counts, primitives, mergedStairs };
}

/**
 * Stairs, two paths:
 *   1. stair-named layers: tread runs among their segments (class read + measured: 0.9). A stair layer without tread runs
 *      keeps the old behaviour (one stair object per closed ring, 0.6).
 *   2. rooms whose label is a stair word ("Treppenhaus", "Cage d'escalier", "Vano scala"...): tread runs among the
 *      segments inside the room, any layer (class inferred from the room: 0.7, flag inferred_from_room).
 * Coverage: analysed when a stair layer exists, partial when only path 2 found flights, otherwise not_analysed
 * (nothing found is NOT reported as "no stairs").
 */
function findStairs({ stairLayerSegs, allSegs, roomRings, rawTexts, mPerUnit, ppm, P, key, correction }) {
  const objects = [];
  let byLayer = 0, byRoom = 0, nFlights = 0, nLandings = 0;
  const px = (len) => round(len * mPerUnit * ppm, 2); // drawing units -> page px (metres are derived by recomputeMetres)
  const pxPt = (p) => P(p).map((v) => round(v, 2));
  /** one object per staircase: flights[] and inferred landings[]; *_m fields are filled from the *_px ones by recomputeMetres */
  const emit = (st, layer, conf, flags, detectedBy) => {
    const flights = st.flights.map((f) => {
      const a = P(f.start), b = P([f.start[0] + f.dir[0], f.start[1] + f.dir[1]]); // plan frame (px: y down)
      const deg = ((Math.atan2(b[1] - a[1], b[0] - a[0]) * 180) / Math.PI + 360) % 360;
      return { tread_count: f.n, direction_deg: round(deg, 1), start_px: pxPt(f.start), width_px: px(f.width), going_px: px(f.going), start_m: null, width_m: null, going_m: null };
    });
    const landings = st.landings.map((l) => ({ polygon_px: l.polygon.map(pxPt), polygon_m: null, at_height_m: null, after_tread: l.afterTread, side: l.side }));
    objects.push({
      layer, key: key('stair'), category: 'stair', subtype: null, polygonPx: st.polygon.map(P), confidence: conf,
      flags: [...flags, ...(st.landings.some((l) => l.side === 'assumed') ? ['landing_side_assumed'] : [])],
      properties: { tread_count: st.treads, flights, landings, rise_direction: 'unknown', detected_by: detectedBy }, correctedBy: correction(layer),
    });
    nFlights += flights.length; nLandings += landings.length;
  };

  // path 1: stair layers
  const layers = [...new Set(stairLayerSegs.map((s) => s.layer))];
  for (const layer of layers) {
    const segs = stairLayerSegs.filter((s) => s.layer === layer);
    const flights = detectFlights(segs, { mPerUnit });
    if (flights.length) {
      for (const st of assembleStaircases(flights, { mPerUnit })) { emit(st, layer, policyConfidence({ classRead: true, measured: true }), [], 'layer'); byLayer++; }
      continue;
    }
    const rings = [...new Map(segs.filter((s) => s.ring).map((s) => [s.ring, s.ring])).values()];
    for (const ring of rings) {
      objects.push({ layer, key: key('obj'), category: 'stair', subtype: null, polygonPx: ring.map(P), confidence: policyConfidence({ classRead: true, measured: true }, 0.6), correctedBy: correction(layer), properties: { detected_by: 'layer' } });
      byLayer++;
    }
  }

  // path 2: tread lines inside rooms named as stairs
  const isStairName = (raw) => {
    const t = cleanText(raw);
    if (!t || classifyTextContent(t).role) return false; // "Scala 1:100" is a scale note, not a stair
    return lookupRoomName(t)?.type === 'stair' || STAIR_LAYER_RE.test(t);
  };
  const stairRooms = roomRings.filter((ring) => rawTexts.some((t) => isStairName(t.text) && pointInPolygon(t.at, ring)));
  for (const ring of stairRooms) {
    const bb = bboxOfPoints(ring);
    const inside = allSegs.filter((s) => s.cls !== 'room' && s.cls !== 'text' && s.cls !== 'dimension'
      && pointInPolygon([(s.a[0] + s.b[0]) / 2, (s.a[1] + s.b[1]) / 2], ring)
      && [s.a, s.b].every((p) => p[0] >= bb[0] && p[0] <= bb[2] && p[1] >= bb[1] && p[1] <= bb[3]));
    const flights = detectFlights(inside, { mPerUnit });
    if (!flights.length) continue;
    const counts = {};
    for (const s of inside) counts[s.layer] = (counts[s.layer] ?? 0) + 1;
    const layer = Object.entries(counts).sort((x, y) => y[1] - x[1])[0][0];
    for (const st of assembleStaircases(flights, { mPerUnit, room: ring })) { emit(st, layer, policyConfidence({ classRead: false, measured: true }), [FLAGS.inferredFromRoom], 'room_geometry'); byRoom++; }
  }

  let coverage;
  if (layers.length) coverage = { status: 'analysed', confidence: byLayer ? 0.8 : 0.6, note: byLayer ? `stair layer(s): ${layers.join(', ')}` : `stair layer(s) ${layers.join(', ')} present but no stair geometry found` };
  else if (byRoom) coverage = { status: 'partial', confidence: 0.6, note: `${byRoom} staircase(s), ${nFlights} flight(s) from tread lines inside ${stairRooms.length} room(s) named as stairs; stairs drawn elsewhere are not detected` };
  else coverage = { status: 'not_analysed', confidence: 0.3, note: stairRooms.length ? `${stairRooms.length} room(s) named as stairs, but no tread lines found in them` : undefined };
  return { objects, byLayer, byRoom, flights: nFlights, landings: nLandings, coverage, note: coverage.note };
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

  // <use href="#id"> draws the referenced <symbol>/<g>/shape; symbols are only drawn through a <use>
  const byId = new Map();
  const index = (node) => { if (node.attrs?.id) byId.set(node.attrs.id, node); for (const c of node.children ?? []) index(c); };
  index(svg);
  const walk = (node, m, ctx, depth = 0) => {
    const own = `${node.attrs.class ?? ''} ${node.attrs.id ?? ''} ${node.attrs['data-type'] ?? ''} ${node.attrs['data-name'] ?? ''}`.trim();
    const t = parseTransform(node.attrs.transform);
    const mm = matMul(t, m);
    const ownCls = own ? classifyName(own) : null;
    // the nearest element that names itself: its class (an IFC class in IFC-rendered plans), data-name, data-guid
    const named = node.attrs['data-guid'] || node.attrs['data-name'] || (node.name === 'use' && node.attrs.id);
    const meta = named ? { svgClass: node.attrs.class ?? null, name: node.attrs['data-name'] ?? null, guid: node.attrs['data-guid'] ?? null, elementId: node.attrs.id ?? null } : ctx.meta;
    const nextCtx = { cls: ownCls ?? ctx.cls, names: own ? [...ctx.names, own] : ctx.names, group: ['g', 'svg', 'symbol', 'use'].includes(node.name) && own ? ++groupSeq : ctx.group, meta };
    const emit = (subs) => { for (const sp of subs) items.push({ cls: nextCtx.cls, names: nextCtx.names, group: nextCtx.group, meta: nextCtx.meta, pts: sp.pts.map((p) => matApply(mm, p)), closed: sp.closed || isShut(sp.pts), tag: node.name }); };
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
      case 'use': {
        const ref = String(node.attrs.href ?? node.attrs['xlink:href'] ?? '').replace(/^#/, '');
        const target = byId.get(ref);
        if (target && depth < 8) {
          const at = matMul([1, 0, 0, 1, numAttr(node.attrs.x), numAttr(node.attrs.y)], mm);
          // the referenced name (symbol id, its class) classifies the use too: <use href="#wc-bowl"> is a toilet
          const tOwn = `${target.attrs.class ?? ''} ${target.attrs.id ?? ''} ${target.attrs['data-name'] ?? ''}`.trim();
          const useCtx = { ...nextCtx, cls: nextCtx.cls ?? classifyName(tOwn), names: [...nextCtx.names, tOwn], meta: { ...(nextCtx.meta ?? {}), name: nextCtx.meta?.name ?? target.attrs['data-name'] ?? target.attrs.id ?? null, symbol: ref } };
          for (const c of target.name === 'symbol' || target.name === 'g' ? target.children : [target]) walk(c, at, useCtx, depth + 1);
        }
        break;
      }
      case 'text': {
        const raw = cleanText(node.text + node.children.map((c) => c.text).join(' '));
        if (raw) texts.push({ raw, at: matApply(mm, [numAttr(node.attrs.x), numAttr(node.attrs.y)]), fs: numAttr(node.attrs['font-size'], 12) * Math.hypot(mm[0], mm[1]) });
        break;
      }
      default:
    }
    if (!['text', 'defs', 'style', 'metadata', 'title', 'desc', 'symbol', 'use'].includes(node.name)) for (const c of node.children) walk(c, mm, nextCtx, depth);
  };
  walk(svg, IDENTITY, { cls: null, names: [], group: 0, meta: null });

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
    // doors/windows/objects/stairs: one per semantic group; rooms: one per named element (its subpaths are outline + holes)
    const k = ['door', 'window', 'object', 'stair'].includes(it.cls) ? `g${it.group}:${it.cls}`
      : it.cls === 'room' && (it.meta?.guid || it.meta?.elementId) ? `room:${it.meta.guid ?? ''}|${it.meta.elementId ?? ''}` : `i${byGroup.size}`;
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
  /** source fields of an SVG element: GlobalId / IFC class when the plan was rendered from IFC, its name, its element id */
  const GUID = /^[0-9A-Za-z_$]{22}$/;
  const src = (meta) => {
    if (!meta) return {};
    const ifcClass = /^Ifc[A-Za-z]+$/.test(meta.svgClass ?? '') ? meta.svgClass.toUpperCase() : null;
    const guid = meta.guid && GUID.test(meta.guid) ? meta.guid : null;
    const properties = Object.fromEntries(Object.entries({ svg_class: meta.svgClass, svg_name: meta.name, svg_symbol: meta.symbol ?? null, svg_element_id: meta.elementId }).filter(([, v]) => v != null && v !== ''));
    return {
      sourceRef: guid ?? meta.elementId ?? null,
      ...(Object.keys(properties).length ? { properties } : {}),
      ...(ifcClass || guid ? { v03: { ...(guid ? { globalId: guid } : {}), ...(ifcClass ? { ifcClass } : {}) } } : {}),
    };
  };
  /** "Rum nr. K08" -> "K08": the last token of an element name that reads as a room number */
  const numberFrom = (name) => (name ? (String(name).split(/\s+/).reverse().find((t) => looksLikeRoomNumber(t)) ?? null) : null);
  let wallEdges = 0, objCapped = 0;
  for (const grp of byGroup.values()) {
    const it0 = grp[0];
    const allPts = grp.flatMap((g) => g.pts);
    if (allPts.length < 2) continue;
    if (it0.cls === 'room') {
      // the rings of ONE named element (an IfcSpace drawn as one path with several subpaths) are one room: the largest ring is
      // its outline, rings inside it are holes, a disjoint ring is kept in properties.extra_parts_px (never a second room
      // with the same GlobalId). Unnamed shapes stay one room per ring.
      const rings = grp.map((g) => ({ g, ring: cleanRing(g.pts, 1e-9) })).filter(({ g, ring }) => g.closed && ring.length >= 3 && Math.abs(polygonArea(ring)) > 100);
      const owner = (g) => (g.meta && (g.meta.guid || g.meta.elementId)) ? `${g.meta.guid ?? ''}|${g.meta.elementId ?? ''}` : null;
      const parts = new Map();
      rings.forEach((x, i) => { const k = owner(x.g) ?? `ring${i}`; (parts.get(k) ?? parts.set(k, []).get(k)).push(x); });
      for (const list of parts.values()) {
        list.sort((a, b) => Math.abs(polygonArea(b.ring)) - Math.abs(polygonArea(a.ring)));
        const [outer, ...rest] = list;
        const holes = rest.filter((x) => x.ring.every((q) => pointInPolygon(q, outer.ring)));
        const extra = rest.filter((x) => !holes.includes(x));
        const sr = src(outer.g.meta);
        if (extra.length) sr.properties = { ...(sr.properties ?? {}), extra_parts_px: extra.map((x) => x.ring.map(P)) };
        bundle.rooms.push({
          key: key('room'), ...sr, polygonPx: outer.ring.map(P), ...(holes.length ? { holesPx: holes.map((x) => x.ring.map(P)) } : {}),
          rawName: roomName(outer.g.names), rawNumber: numberFrom(outer.g.meta?.name), confidence: 0.8,
        });
      }
    } else if (it0.cls === 'wall') {
      for (const g of grp) {
        const ring = cleanRing(g.pts, 1e-9);
        if (g.closed && ring.length >= 3) {
          const r = minAreaRect(ring);
          bundle.walls.push({ key: key('wall'), ...src(g.meta), polygonPx: ring.map(P), centerlinePx: null, thicknessPx: r ? round(r.width * s, 2) : null, confidence: 0.75 });
        } else if (g.pts.length >= 2) {
          wallEdges++;
          bundle.walls.push({ key: key('wall'), ...src(g.meta), polygonPx: rectFromSegment(g.pts[0], g.pts.at(-1), 4).map(P), centerlinePx: [P(g.pts[0]), P(g.pts.at(-1))], thicknessPx: null, confidence: 0.4 });
        }
      }
    } else if (it0.cls === 'door' || it0.cls === 'window') {
      const r = minAreaRect(allPts);
      if (!r) continue;
      bundle.openings.push({ key: key(it0.cls), ...src(it0.meta), kind: it0.cls, subtype: null, polygonPx: r.corners.map(P), widthPx: round(r.length * s, 2), confidence: 0.7 });
    } else if (it0.cls === 'object' || it0.cls === 'stair') {
      const r = minAreaRect(allPts);
      if (!r) continue;
      if (bundle.objects.length >= config.cad.maxObjects) { objCapped++; continue; }
      // category: the element's own name / symbol, then its class ("IfcSanitaryTerminal", "toilet-bowl", data-name "WC");
      // anything unmatched stays "other" with the source name kept as subtype and in properties
      const nm = it0.meta?.name ?? it0.meta?.symbol ?? null;
      const fx = it0.cls === 'stair' ? { category: 'stair' } : classifyFixture({ block: nm ?? it0.meta?.symbol, layer: it0.meta?.svgClass ?? it0.names.at(-1) });
      const fallback = humanize(it0.meta?.svgClass ?? it0.names.at(-1)?.split(/\s+/)[0] ?? 'object').toLowerCase().slice(0, 40);
      bundle.objects.push({
        key: key('obj'), ...src(it0.meta), category: fx.category ?? fallback, subtype: nm ? String(nm).slice(0, 60) : null, ...(nm ? { name: String(nm) } : {}),
        polygonPx: r.corners.map(P), confidence: fx.category ? 0.7 : 0.55,
      });
    }
  }
  if (wallEdges) warnings.push(`${wallEdges} open wall lines were given an assumed thickness (low confidence)`);
  if (objCapped) warnings.push(`${objCapped} objects beyond the cap of ${config.cad.maxObjects} were not emitted`);

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

