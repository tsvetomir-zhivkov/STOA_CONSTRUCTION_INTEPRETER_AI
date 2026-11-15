import { test } from 'node:test';
import assert from 'node:assert/strict';
import fs from 'node:fs/promises';
import os from 'node:os';
import path from 'node:path';
import { wallsFromSegments, joinWalls, bridgeJunctions } from '../src/extractors/dxfWalls.js';
import { detectFlights, assembleStaircases } from '../src/extractors/stairs.js';
import { classifyFixture } from '../src/extractors/fixtures.js';
import { policyConfidence } from '../src/extractors/confidence.js';
import { lookupRoomName } from '../src/mappers/glossary.js';
import { loadCorrections, applyCorrections } from '../src/mappers/corrections.js';
import { processBlueprint } from '../src/index.js';
import { silentLogger } from '../src/util/logger.js';

const seg = (a, b, extra = {}) => ({ a, b, layer: 'WALL', ring: null, thinRing: false, ...extra });
const ringSegs = (ring, extra = {}) => ring.map((p, i) => seg(p, ring[(i + 1) % ring.length], { ring, ...extra }));

test('A1: a thin closed ring becomes one measured wall; its end caps are dropped', () => {
  const ring = [[0, 0], [4, 0], [4, 0.15], [0, 0.15]];
  const { walls, stats } = wallsFromSegments(ringSegs(ring, { thinRing: true }), { mPerUnit: 1 });
  assert.equal(walls.length, 1);
  assert.equal(walls[0].assumed, false);
  assert.ok(Math.abs(walls[0].thickness - 0.15) < 1e-9);
  assert.deepEqual([stats.measured, stats.assumed, stats.capsDropped], [1, 0, 2]);
});

test('A1: two parallel lines 0.12 m apart pair; a lone line falls back to an assumed 0.2 m', () => {
  const { walls, stats } = wallsFromSegments([seg([0, 0], [3, 0]), seg([0.2, 0.12], [2.8, 0.12]), seg([10, 0], [10, 3])], { mPerUnit: 1 });
  const measured = walls.filter((w) => !w.assumed), assumed = walls.filter((w) => w.assumed);
  assert.equal(measured.length, 1);
  assert.ok(Math.abs(measured[0].thickness - 0.12) < 1e-9);
  assert.equal(assumed.length >= 1, true);
  assert.ok(assumed.every((w) => w.thickness === 0.2));
  assert.equal(stats.measured, 1);
});

test('A1: pairing rejects lines that are too far apart, crossing, or barely overlapping', () => {
  const far = wallsFromSegments([seg([0, 0], [3, 0]), seg([0, 0.8], [3, 0.8])], { mPerUnit: 1 });
  assert.equal(far.walls.filter((w) => !w.assumed).length, 0);
  const shortOverlap = wallsFromSegments([seg([0, 0], [3, 0]), seg([2.5, 0.1], [5.5, 0.1])], { mPerUnit: 1 });
  assert.equal(shortOverlap.walls.filter((w) => !w.assumed).length, 0, '0.5 m overlap of 3 m < 60 %');
});

test('A1: a gap between two walls inside one outline is not paired as wall material', () => {
  // U-shaped outline: two 0.1 m wall legs with a 0.3 m gap between them; the gap faces must not pair
  const ring = [[0, 0], [3, 0], [3, 0.5], [0, 0.5], [0, 0.4], [2.9, 0.4], [2.9, 0.1], [0, 0.1]];
  const { walls } = wallsFromSegments(ringSegs(ring), { mPerUnit: 1 });
  const measured = walls.filter((w) => !w.assumed);
  assert.ok(measured.length >= 2);
  assert.ok(measured.every((w) => Math.abs(w.thickness - 0.1) < 1e-6), `thicknesses ${measured.map((w) => w.thickness)}`);
});

test('A1: collinear walls of equal thickness whose ends meet are merged', () => {
  const { walls, stats } = wallsFromSegments([
    seg([0, 0], [2, 0]), seg([0, 0.1], [2, 0.1]),
    seg([2.02, 0], [4, 0]), seg([2.02, 0.1], [4, 0.1]),
  ], { mPerUnit: 1 });
  assert.equal(walls.length, 1);
  assert.equal(stats.merged, 1);
  const [a, b] = walls[0].centerline;
  assert.ok(Math.abs(Math.hypot(b[0] - a[0], b[1] - a[1]) - 4) < 1e-6);
});

test('A1: thresholds are in metres, independent of drawing units (mm drawing)', () => {
  const { walls } = wallsFromSegments([seg([0, 0], [3000, 0]), seg([0, 120], [3000, 120])], { mPerUnit: 0.001 });
  assert.equal(walls.filter((w) => !w.assumed).length, 1);
});

test('A2: evenly spaced parallel tread lines form one flight with measured properties', () => {
  const treads = Array.from({ length: 8 }, (_, i) => ({ a: [0, i * 0.28], b: [1.2, i * 0.28] }));
  const flights = detectFlights([...treads, { a: [5, 0], b: [5, 3] }], { mPerUnit: 1 });
  assert.equal(flights.length, 1);
  const p = flights[0].props;
  assert.equal(p.tread_count, 8);
  assert.equal(p.width_m, 1.2);
  assert.ok(Math.abs(p.run_length_m - 8 * 0.28) < 1e-3);
  assert.equal(p.run_direction_deg, 270, 'drawing +y is plan -y (up the page)');
  assert.equal(p.rise_direction, 'unknown');
});

test('A2: uneven spacing or fewer than four lines is not a stair', () => {
  assert.equal(detectFlights([0, 0.28, 0.56].map((y) => ({ a: [0, y], b: [1, y] })), { mPerUnit: 1 }).length, 0);
  assert.equal(detectFlights([0, 0.28, 0.9, 1.0, 1.7].map((y) => ({ a: [0, y], b: [1, y] })), { mPerUnit: 1 }).length, 0);
});

test('A3: glossary is case- and accent-insensitive, strips numbering, maps generic words to "other"', () => {
  assert.equal(lookupRoomName('Chambre 2').type, 'bedroom');
  assert.equal(lookupRoomName('PIECE').type, 'other');
  assert.equal(lookupRoomName('Pièce').type, 'other');
  assert.equal(lookupRoomName('séjour/repas').type, 'living');
  assert.equal(lookupRoomName('Cage d’escalier').type, 'stair');
  assert.equal(lookupRoomName('Bagno').type, 'bathroom');
  assert.equal(lookupRoomName('WC').type, 'wc');
  assert.equal(lookupRoomName('Atelier'), null);
});

test('A4: fixtures from block name first, then layer; unmatched stays null', () => {
  assert.deepEqual(classifyFixture({ block: 'WC-01', layer: 'FIXTURE' }), { category: 'toilet', from: 'block' });
  assert.deepEqual(classifyFixture({ block: 'Kühlschrank 60', layer: 'X' }), { category: 'fridge', from: 'block' });
  assert.deepEqual(classifyFixture({ block: 'BLK7', layer: 'A-RADIATEUR' }), { category: 'radiator', from: 'layer' });
  assert.equal(classifyFixture({ block: null, layer: 'FIXTURE' }).category, null);
});

test('A5: the policy is a cap and never raises a confidence', () => {
  assert.equal(policyConfidence({ classRead: true, measured: true }), 0.9);
  assert.equal(policyConfidence({ classRead: true, measured: true }, 0.8), 0.8);
  assert.equal(policyConfidence({ classRead: false, measured: true }), 0.7);
  assert.equal(policyConfidence({ classRead: true, measured: false, assumed: true }, 1), 0.4);
});

test('A6: corrections: missing file, bad JSON and bad rules never crash', async () => {
  const dir = await fs.mkdtemp(path.join(os.tmpdir(), 'bi-corr-'));
  const lines = [];
  const logger = { ...silentLogger, warn: (m) => lines.push(m) };
  assert.equal((await loadCorrections(path.join(dir, 'nope.json'), { logger })).rules.length, 0);
  await fs.writeFile(path.join(dir, 'bad.json'), '{ not json');
  assert.equal((await loadCorrections(path.join(dir, 'bad.json'), { logger })).rules.length, 0);
  await fs.writeFile(path.join(dir, 'c.json'), JSON.stringify({ 'block:X': { category: 'spaceship' }, 'nonsense': {}, 'room:Pièce': { type: 'other' } }));
  const c = await loadCorrections(path.join(dir, 'c.json'), { logger });
  assert.deepEqual(c.rules.map((r) => r.key), ['room:Pièce']);
  assert.ok(lines.length >= 3);
});

test('A6: most specific rule wins; corrected element is human, confidence 1.0, with a history entry', async () => {
  const dir = await fs.mkdtemp(path.join(os.tmpdir(), 'bi-corr-'));
  await fs.writeFile(path.join(dir, 'c.json'), JSON.stringify({
    'layer:FIXTURE': { category: 'sink' }, 'block:wc-01': { category: 'toilet' }, 'ref:data/demo/plan.dxf#obj9': { category: 'bathtub' }, 'ref:obj10': { category: 'shower' }, 'room:piece': { type: 'storage' },
  }));
  const corr = await loadCorrections(path.join(dir, 'c.json'));
  const obj = (id, ref, block) => ({ id, status: 'active', confidence: 0.6, category: 'other', provenance: { extractor: 'vector_dxf', source_ref: ref, layer: 'FIXTURE' }, ...(block ? { properties: { source_block: block } } : {}) });
  const doc = {
    drawing: { file: 'data/demo/plan.dxf' },
    rooms: [{ id: 'room_1', status: 'active', confidence: 0.8, name: 'Pièce', label: 'Pièce', number: null, type: 'other', is_outdoor: false, provenance: { extractor: 'vector_dxf', source_ref: 'room1' } }],
    openings: [], walls: [], history: [],
    objects: [obj('obj_a', 'obj9', 'WC-01'), obj('obj_b', 'obj10', 'WC-01'), obj('obj_c', 'obj11', null)],
  };
  applyCorrections(doc, corr, { now: '2026-11-14T00:00:00Z' });
  assert.deepEqual(doc.objects.map((o) => o.category), ['bathtub', 'toilet', 'sink']);
  assert.ok(doc.objects.every((o) => o.confidence === 1 && o.provenance.extractor === 'human'));
  assert.equal(doc.objects[1].provenance.details.rule, 'block:wc-01');
  assert.equal(doc.objects[1].provenance.details.previous.category, 'other');
  assert.equal(doc.rooms[0].type, 'storage');
  // 'ref:obj10' has no file part: ambiguous across drawings, never matches (obj_b keeps the block rule)
  assert.deepEqual(doc.history.map((h) => h.note.match(/"([^"]+)"/)[1]).sort(), ['block:wc-01', 'layer:FIXTURE', 'ref:data/demo/plan.dxf#obj9', 'room:piece'].sort());
  assert.ok(doc.history.every((h) => h.extractor === 'human' && h.action === 'correction'));
});

test('A6 + A7 end to end: a DXF re-run with a correction file; rooms keep a null height', async () => {
  const dir = await fs.mkdtemp(path.join(os.tmpdir(), 'bi-corr-e2e-'));
  const { writeFixtures } = await import('./helpers/fixtures.js');
  const fx = await writeFixtures(dir);
  const out = path.join(dir, 'out', 'plan.json');
  const run = (env) => processBlueprint({ inputFilePath: fx.dxf, outputJsonPath: out, env, logger: silentLogger });
  await run({ CORRECTIONS_FILE: path.join(dir, 'missing.json') });
  const before = JSON.parse(await fs.readFile(out, 'utf8'));
  const fixture = before.objects.find((o) => o.category === 'toilet' || o.category === 'other');
  assert.ok(before.rooms.every((r) => r.height_m === null));
  await fs.writeFile(path.join(dir, 'corr.json'), JSON.stringify({ [`ref:${before.drawings[0].file}#${fixture.provenance.source_ref}`]: { category: 'bathtub' } }));
  await run({ CORRECTIONS_FILE: path.join(dir, 'corr.json') });
  const after = JSON.parse(await fs.readFile(out, 'utf8'));
  const active = after.objects.filter((o) => o.status === 'active');
  const corrected = active.find((o) => o.provenance.source_ref === fixture.provenance.source_ref);
  assert.equal(corrected.category, 'bathtub');
  assert.equal(corrected.confidence, 1);
  assert.equal(after.objects.find((o) => o.id === fixture.id).status, 'superseded', 'the uncorrected twin is superseded, not left active');
  assert.ok(after.history.some((h) => h.extractor === 'human' && h.action === 'correction'));
});

// ---- wall junctions ---------------------------------------------------------------------------------------------------
const wall = (a, b, t = 0.2, extra = {}) => ({ centerline: [a, b], thickness: t, polygon: null, assumed: false, thinRing: false, layer: 'WALL', merged: 0, ...extra });
const near = (p, q, eps = 1e-6) => Math.hypot(p[0] - q[0], p[1] - q[1]) < eps;
const hasPt = (poly, q) => poly.some((p) => near(p, q));

test('3.3: an L corner is mitred: both walls end on the same diagonal, no notch, no overlap', () => {
  // strips that stop short of the corner square (as line pairing leaves them)
  const A = wall([0, 0], [3.9, 0]), B = wall([4, 0.1], [4, 3]);
  const r = joinWalls([A, B]);
  assert.deepEqual(r, { corners: 1, tees: 0 });
  for (const w of [A, B]) {
    assert.ok(hasPt(w.polygon, [3.9, 0.1]), 'inner face intersection');
    assert.ok(hasPt(w.polygon, [4.1, -0.1]), 'outer face intersection');
  }
  assert.ok(near(A.centerline[1], [4, 0]) && near(B.centerline[0], [4, 0]), 'both centrelines run to the corner point');
});

test('3.3: a T junction cuts the stem at the near face of the through-wall, which stays as it is', () => {
  const C = wall([0, 0], [6, 0]), D = wall([3, 0.15], [3, 3]);
  const before = JSON.stringify(C.centerline);
  assert.deepEqual(joinWalls([C, D]), { corners: 0, tees: 1 });
  assert.equal(C.polygon, null, 'through-wall untouched');
  assert.equal(JSON.stringify(C.centerline), before);
  assert.ok(D.polygon.slice(0, 1).concat(D.polygon.slice(3)).every((p) => Math.abs(p[1] - 0.1) < 1e-9), 'stem starts exactly on the face y = 0.1');
});

test('3.3: collinear halves are bridged across a T hole only when a third wall ends in the gap', () => {
  const opts = { cosTol: Math.cos(Math.PI / 60), dt: 0.02, lateral: 0.03, maxGap: 0.6, tol: 0.05 };
  const withStem = bridgeJunctions([wall([0, 0], [2.9, 0]), wall([3.1, 0], [6, 0]), wall([3, 0.1], [3, 3])], opts);
  assert.equal(withStem.bridged, 1);
  assert.equal(withStem.walls.length, 2);
  const doorway = bridgeJunctions([wall([0, 0], [2.8, 0]), wall([3.2, 0], [6, 0])], opts);
  assert.equal(doorway.bridged, 0, 'a gap with no wall ending in it (a doorway) stays open');
});

test('3.4: end to end, wall corners close and unpaired lines keep the 0.2 m assumption', () => {
  // two L-shaped double-line walls meeting at a corner, plus one lone line
  const segs = [seg([0, 0], [4, 0]), seg([0, 0.2], [3.8, 0.2]), seg([4, 0], [4, 4]), seg([3.8, 0.2], [3.8, 4]), seg([10, 0], [10, 3])];
  const { walls, stats } = wallsFromSegments(segs, { mPerUnit: 1 });
  assert.equal(stats.assumed, 1);
  assert.ok(stats.junctions >= 1, 'the corner was closed');
  assert.ok(stats.covered >= 1, 'corner-square face pieces inside the mitred walls were dropped');
  const lone = walls.find((w) => w.assumed);
  assert.ok(Math.abs(lone.thickness - 0.2) < 1e-9);
});

// ---- stairs: flights and landings ----------------------------------------------------------------------------------------
test('5: a U stair is two flights 180 degrees apart with one landing between them, on the side the room has space for', () => {
  const f1 = Array.from({ length: 8 }, (_, i) => ({ a: [0, i * 0.28], b: [1, i * 0.28] }));
  const f2 = Array.from({ length: 8 }, (_, i) => ({ a: [1.1, i * 0.28], b: [2.1, i * 0.28] }));
  const flights = detectFlights([...f1, ...f2], { mPerUnit: 1 });
  assert.equal(flights.length, 2);
  const room = [[-0.1, -0.1], [2.2, -0.1], [2.2, 3.5], [-0.1, 3.5]];
  const [st, ...more] = assembleStaircases(flights, { mPerUnit: 1, room });
  assert.equal(more.length, 0, 'one staircase');
  assert.equal(st.flights.length, 2);
  assert.equal(st.treads, 16);
  const [a, b] = st.flights;
  assert.ok(Math.abs(a.dir[0] * b.dir[0] + a.dir[1] * b.dir[1] + 1) < 1e-6, 'flights run in opposite directions');
  assert.equal(st.landings.length, 1);
  const L = st.landings[0];
  assert.equal(L.afterTread, 8);
  assert.equal(L.side, 'room');
  const ys = L.polygon.map((p) => p[1]), xs = L.polygon.map((p) => p[0]);
  assert.ok(Math.abs(Math.min(...ys) - 2.24) < 1e-6 && Math.abs(Math.max(...ys) - 3.24) < 1e-6, 'landing beyond the flights, as deep as a flight is wide');
  assert.ok(Math.abs(Math.min(...xs)) < 1e-6 && Math.abs(Math.max(...xs) - 2.1) < 1e-6, 'landing spans both flights');
  assert.ok(near(a.end, [0.5, 2.24]) && near(b.start, [1.6, 2.24]), 'flight 2 starts where the landing begins');
});

test('5: without a room both landing sides fit, so the side is marked assumed', () => {
  const f1 = Array.from({ length: 6 }, (_, i) => ({ a: [0, i * 0.3], b: [1, i * 0.3] }));
  const f2 = Array.from({ length: 6 }, (_, i) => ({ a: [1.1, i * 0.3], b: [2.1, i * 0.3] }));
  const [st] = assembleStaircases(detectFlights([...f1, ...f2], { mPerUnit: 1 }), { mPerUnit: 1 });
  assert.equal(st.landings[0].side, 'assumed');
});

test('3.1: a pier whose face is split by a T junction still pairs (combined overlap), and its split end cap is dropped', () => {
  // 0.37 x 0.76 pier; a 0.2 m wall leaves its left face between y 0.3 and 0.5; the top cap has an extra vertex at x 0.33
  const ring = [[0, 0], [0.37, 0], [0.37, 0.76], [0.33, 0.76], [0, 0.76], [0, 0.5], [-2, 0.5], [-2, 0.3], [0, 0.3]];
  const { walls, stats } = wallsFromSegments(ringSegs(ring), { mPerUnit: 1 });
  assert.equal(stats.assumed, 0, 'nothing falls back to the 0.2 m assumption');
  const pier = walls.find((w) => Math.abs(w.thickness - 0.37) < 1e-6);
  assert.ok(pier, 'the pier is one measured wall 0.37 m thick');
  const [a, b] = pier.centerline;
  assert.ok(Math.abs(Math.hypot(b[0] - a[0], b[1] - a[1]) - 0.76) < 1e-6, 'bridged across the stem: full 0.76 m');
  assert.ok(walls.some((w) => Math.abs(w.thickness - 0.2) < 1e-6), 'the stem is measured too');
});

test('ids: IFC GlobalIds that differ only in "$" vs "_" get distinct ids', async () => {
  const { encodeGuid } = await import('../src/extractors/ifcExtractor.js');
  const a = encodeGuid('2F86O5QEHDQg2jQM9OV52$'), b = encodeGuid('2F86O5QEHDQg2jQM9OV52_');
  assert.notEqual(a, b);
  assert.match(a, /^[A-Za-z0-9_.-]+$/);
  assert.equal(encodeGuid('0abcXYZ'), '0abcXYZ', 'plain GUIDs are unchanged');
});

test('4.4: fixture names map case- and accent-insensitively; Kühlschrank is a fridge, Schrank a closet', () => {
  for (const [name, cat] of [['WC-01', 'toilet'], ['Lavabo', 'sink'], ['Évier', 'sink'], ['Douche', 'shower'], ['Baignoire', 'bathtub'], ['Cuisinière', 'stove'],
    ['Kühlschrank', 'fridge'], ['Lave-linge', 'washing_machine'], ['Aufzug', 'elevator'], ['Radiateur', 'radiator'], ['Placard', 'closet'], ['Schrank 60', 'closet'], ['Wardrobe', 'closet'], ['XYZ-12', null]]) {
    assert.equal(classifyFixture({ block: name }).category, cat, name);
  }
});

test('4.5: every route maps onto one vocabulary; unmapped values are reported and flagged, never passed through', async () => {
  const { normalizeVocabulary } = await import('../src/mappers/vocabulary.js');
  const doc = {
    rooms: [{ id: 'r1', type: 'living_room' }, { id: 'r2', type: 'hall' }, { id: 'r3', type: 'outdoor' }, { id: 'r4', type: 'Living' }, { id: 'r5', type: 'spaceship' }],
    objects: [{ id: 'o1', category: 'fixture', subtype: 'toilet' }, { id: 'o2', category: 'washbasin' }, { id: 'o3', category: 'sink' }, { id: 'o4', category: 'teleporter' }],
  };
  const r = normalizeVocabulary(doc);
  assert.deepEqual(doc.rooms.map((x) => x.type), ['living', 'entry', 'outdoor', 'living', 'unknown']);
  assert.deepEqual(doc.objects.map((x) => x.category), ['toilet', 'sink', 'sink', 'other']);
  assert.equal(doc.objects[0].subtype, null, 'the subtype that carried the class is folded into category');
  assert.deepEqual(r.unmapped.map((u) => u.value), ['spaceship', 'teleporter']);
  assert.equal(doc.rooms[4].properties.raw_type, 'spaceship');
  assert.ok(doc.objects[3].flags.includes('unmapped_vocabulary'));
});
