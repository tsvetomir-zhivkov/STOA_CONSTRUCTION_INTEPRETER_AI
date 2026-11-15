import { test } from 'node:test';
import assert from 'node:assert/strict';
import fs from 'node:fs/promises';
import os from 'node:os';
import path from 'node:path';
import { processBlueprint } from '../src/index.js';
import { loadConfig } from '../src/config.js';
import { createVersionedValidator } from '../src/schema/versions.js';
import { UnsupportedFormatError } from '../src/util/errors.js';
import { silentLogger } from '../src/util/logger.js';
import { writeFixtures } from './helpers/fixtures.js';

const dir = await fs.mkdtemp(path.join(os.tmpdir(), 'bi-e2e-'));
const fx = await writeFixtures(dir);
const versioned = await createVersionedValidator();
const validate = (d) => { const r = versioned.validate(d); if (!r.valid) console.error(versioned.describe(r).slice(0, 10)); return r; };
const run = (input, out) => processBlueprint({ inputFilePath: input, outputJsonPath: path.join(dir, 'out', out), env: { CORRECTIONS_FILE: path.join(dir, 'no-corrections.json') }, logger: silentLogger });
const read = async (p) => JSON.parse(await fs.readFile(p, 'utf8'));

test('IFC: one valid document per building with levels, number/name split and psets', async () => {
  const res = await run(fx.ifc, 'house.json');
  assert.equal(res.status, 'processed');
  assert.equal('usage' in res, false);
  assert.equal(res.outputs.length, 1);
  const doc = await read(res.outputs[0].outputJsonPath);
  assert.ok(validate(doc).valid);
  assert.equal(doc.drawings[0].route, 'ifc');
  assert.equal(doc.levels[0].name, '1.korrus');
  const room = doc.rooms[0];
  assert.deepEqual([room.name, room.number, room.label, room.type], ['Kitchen', '3-1', 'Kitchen (3-1)', 'kitchen']);
  assert.equal(room.area_m2, 12);
  assert.equal(room.provenance.source_ref, '0space1');
  assert.equal(doc.walls[0].is_load_bearing, true);
  assert.equal(doc.objects[0].category, 'toilet');
});

test('DXF: rooms, area, room number, unit and fixture', async () => {
  const res = await run(fx.dxf, 'plan.json');
  const doc = await read(res.outputs[0].outputJsonPath);
  assert.ok(validate(doc).valid);
  const kitchen = doc.rooms.find((r) => r.name === 'Kitchen');
  assert.deepEqual([kitchen.type, kitchen.number, kitchen.area_written_m2], ['kitchen', '2.10', 12]);
  assert.equal(doc.rooms.find((r) => r.name === 'MH').type, 'bedroom');
  assert.equal(doc.units[0].label, 'A');
  assert.equal(doc.openings[0].width_m, 0.9);
});

test('SVG: valid document with scale from the 1:100 note', async () => {
  const res = await run(fx.svg, 'svg.json');
  const doc = await read(res.outputs[0].outputJsonPath);
  assert.ok(validate(doc).valid);
  assert.equal(doc.drawings[0].route, 'svg');
  assert.equal(doc.drawings[0].scale.source, 'scale_note');
  assert.ok(doc.rooms.length >= 2);
});

test('vector PDF: one building containing every page', async () => {
  const res = await run(fx.pdf, 'sheet.json');
  assert.equal(res.outputs.length, 1);
  for (const o of res.outputs) {
    const doc = await read(o.outputJsonPath);
    assert.ok(validate(doc).valid);
    assert.equal(doc.drawings[0].route, 'vector_pdf');
    assert.equal(doc.drawings.length, 2);
    assert.deepEqual(doc.drawings.map(d=>d.page_index).sort(), [0,1]);
    assert.equal(doc.rooms.find((r) => r.name === 'Kitchen')?.type, 'kitchen');
  }
});

test('unknown format is unsupported with reason unknown_format', async () => {
  await assert.rejects(run(fx.unknown, 'x.json'), (err) => err instanceof UnsupportedFormatError && /unknown_format/.test(err.message));
});
