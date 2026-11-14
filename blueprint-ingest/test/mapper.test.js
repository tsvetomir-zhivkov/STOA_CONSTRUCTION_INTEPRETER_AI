import { test } from 'node:test';
import assert from 'node:assert/strict';
import fs from 'node:fs/promises';
import os from 'node:os';
import path from 'node:path';
import { mapToSchema } from '../src/mappers/schemaMapper.js';
import { extractDxf } from '../src/extractors/cadExtractor.js';
import { loadConfig } from '../src/config.js';
import { createValidator } from '../src/storage/jsonStore.js';
import { SchemaValidationError, PipelineError } from '../src/util/errors.js';
import { silentLogger } from '../src/util/logger.js';
import { writeFixtures } from './helpers/fixtures.js';

const config = loadConfig({});
const tmp = () => fs.mkdtemp(path.join(os.tmpdir(), 'bi-mapper-'));

async function setup() {
  const dir = await tmp();
  const fx = await writeFixtures(dir);
  const bundles = await extractDxf(fx.dxf, { config, logger: silentLogger });
  return { dir, fx, bundles, out: path.join(dir, 'out', 'plan.json') };
}

test('create, then merge into the same file', async () => {
  const { fx, bundles, out } = await setup();
  const { validate } = await createValidator(config.schemaPath);

  const first = await mapToSchema({ inputFilePath: fx.dxf, outputJsonPath: out, mode: 'auto', bundles, config, logger: silentLogger });
  assert.equal(first.outputs.length, 1);
  assert.equal(first.outputs[0].action, 'created');
  assert.equal('usage' in first, false);
  assert.deepEqual(Object.keys(first.outputs[0]).sort(), ['action', 'drawingId', 'outputJsonPath', 'report']);
  const created = JSON.parse(await fs.readFile(out, 'utf8'));
  assert.ok(validate(created).valid);
  assert.equal(created.schema_version, '0.2');
  assert.equal(created.drawing.file, 'data/demo_set/plan.dxf');
  assert.equal(created.drawing.route, 'dxf');

  const second = await mapToSchema({ inputFilePath: fx.dxf, outputJsonPath: out, mode: 'auto', bundles: await extractDxf(fx.dxf, { config, logger: silentLogger }), config, logger: silentLogger });
  assert.equal(second.outputs[0].action, 'merged');
  assert.equal(second.outputs[0].report.added.length, 0, 'identical input adds nothing');
  const merged = JSON.parse(await fs.readFile(out, 'utf8'));
  assert.ok(validate(merged).valid);
  assert.equal(merged.rooms.length, created.rooms.length);
});

test('mode "create" refuses to overwrite an existing file', async () => {
  const { fx, bundles, out } = await setup();
  await mapToSchema({ inputFilePath: fx.dxf, outputJsonPath: out, mode: 'create', bundles, config, logger: silentLogger });
  const before = await fs.readFile(out, 'utf8');
  await assert.rejects(
    mapToSchema({ inputFilePath: fx.dxf, outputJsonPath: out, mode: 'create', bundles, config, logger: silentLogger }),
    (err) => err instanceof PipelineError && err.code === 'OUTPUT_EXISTS',
  );
  assert.equal(await fs.readFile(out, 'utf8'), before);
});

test('an invalid existing file throws SchemaValidationError and is left untouched', async () => {
  const { fx, bundles, out } = await setup();
  await fs.mkdir(path.dirname(out), { recursive: true });
  const invalid = `${JSON.stringify({ schema_version: '0.2', drawing: { file: 'x' }, rooms: 'not-an-array' }, null, 2)}\n`;
  await fs.writeFile(out, invalid);
  await assert.rejects(
    mapToSchema({ inputFilePath: fx.dxf, outputJsonPath: out, mode: 'auto', bundles, config, logger: silentLogger }),
    (err) => err instanceof SchemaValidationError && /left untouched/.test(err.message) && err.errors.length > 0,
  );
  assert.equal(await fs.readFile(out, 'utf8'), invalid);
  const leftovers = (await fs.readdir(path.dirname(out))).filter((f) => f.endsWith('.tmp'));
  assert.deepEqual(leftovers, []);
});

test('room names not in the glossary stay type "unknown" and are logged once', async () => {
  const { dir, out } = await setup();
  const src = path.join(dir, 'data', 'demo_set', 'atelier.dxf');
  await fs.writeFile(src, (await fs.readFile(path.join(dir, 'data', 'demo_set', 'plan.dxf'), 'utf8')).replace(/^Kitchen$/m, 'Atelier'));
  const lines = [];
  const logger = { ...silentLogger, info: (m) => lines.push(m) };
  await mapToSchema({ inputFilePath: src, outputJsonPath: out, bundles: await extractDxf(src, { config, logger: silentLogger }), config, logger });
  const doc = JSON.parse(await fs.readFile(out, 'utf8'));
  const atelier = doc.rooms.find((r) => r.name === 'Atelier');
  assert.equal(atelier.type, 'unknown');
  assert.equal(lines.filter((l) => /not in the glossary/.test(l)).length, 1);
});
