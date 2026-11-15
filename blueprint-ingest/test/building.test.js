import test from 'node:test';
import assert from 'node:assert/strict';
import fs from 'node:fs/promises';
import os from 'node:os';
import path from 'node:path';
import { fileURLToPath } from 'node:url';
import { migrate, mergeBuildings } from '../src/storage/building.js';
import { createValidator, writeJsonAtomic } from '../src/storage/jsonStore.js';
import { SCHEMA_PATH, loadConfig } from '../src/config.js';
import { addSchedules, readScheduleTables } from '../src/extractors/schedules.js';
import { processBlueprint } from '../src/index.js';
import { routeToVisionAgent } from '../src/agents/visionAgentAdapter.js';
import { mapToSchema } from '../src/mappers/geminiSchemaMapper.js';
import { emptyBundle } from '../src/extractors/bundle.js';

const validator = await createValidator(SCHEMA_PATH);
const legacy = JSON.parse(await fs.readFile(new URL('../out/plan.level-1.json', import.meta.url), 'utf8'));
async function temporary(t) {
  const dir = await fs.mkdtemp(path.join(os.tmpdir(), 'stoa-building-'));
  t.after(() => fs.rm(dir, { recursive: true, force: true })); return dir;
}
test('v0.2 migration, stable identities, all-of strictness and references', () => {
  const doc = migrate(legacy); validator.assertValid(doc);
  assert.deepEqual(migrate(doc), doc);
  const bad = structuredClone(doc); bad.rooms[0].level_id = 'missing'; assert.equal(validator.validate(bad).valid, false);
  const unknown = structuredClone(doc); unknown.rooms[0].unexpected = 1; assert.equal(validator.validate(unknown).valid, false);
  const nullable = structuredClone(doc); nullable.rooms[0].material_layers = [{ material: { name: null }, thickness_m: null }]; validator.assertValid(nullable);
  nullable.rooms[0].material_layers[0].thickness_m = -1; assert.equal(validator.validate(nullable).valid, false);
});
test('multiple pages keep separate pixel frames and do not duplicate identical IFC instances', () => {
  const a = migrate(legacy), b = structuredClone(legacy); b.drawing.part = 'another-observation';
  const result = mergeBuildings([a, migrate(b)]); validator.assertValid(result);
  assert.equal(result.drawings.length, 2); assert.equal(result.rooms.length, a.rooms.length);
  assert.equal(result.rooms[0].observations.length, 2);
});
test('invalid document is never atomically written', async t => {
  const dir = await temporary(t), target = path.join(dir, 'invalid.json');
  const doc = migrate(legacy); doc.openings[0].wall_id = 'missing';
  await assert.rejects(writeJsonAtomic(target, doc));
  await assert.rejects(fs.stat(target), { code: 'ENOENT' });
});
test('printed schedule rows remain specifications and normalize explicitly labelled units', () => {
  const text = (s, x, y) => ({ text: s, bbox_px: [x, y, x + 70, y + 10], provenance: { extractor: 'pdf', source_ref: `${x}:${y}` } });
  const texts = [text('Door schedule', 0, 0), text('Mark', 0, 20), text('Width (mm)', 100, 20), text('Quantity', 200, 20), text('D01', 0, 40), text('900', 100, 40), text('4', 200, 40)];
  const tables = readScheduleTables(texts); assert.equal(tables.length, 1);
  const doc = migrate(legacy), before = doc.openings.length; addSchedules(doc, tables); validator.assertValid(doc);
  assert.equal(doc.schedules[0].rows[0].dimensions.width_m, .9);
  assert.equal(doc.schedules[0].rows[0].quantity, 4);
  assert.deepEqual(doc.schedules[0].rows[0].entity_ids, []); assert.equal(doc.openings.length, before);
  doc.schedules[0].rows[0].entity_ids = ['missing']; assert.equal(validator.validate(doc).valid, false);
});
test('SVG parser through mapper writes one v0.3 output without an AI key', async t => {
  const dir = await temporary(t), input = path.join(dir, 'plan.svg'), output = path.join(dir, 'building.json');
  await fs.writeFile(input, '<svg xmlns="http://www.w3.org/2000/svg" width="500" height="500" viewBox="0 0 500 500"><g id="rooms"><rect x="40" y="40" width="200" height="180" fill="none" stroke="black"/><text x="80" y="100">Office</text></g></svg>');
  const result = await processBlueprint({ inputFilePath: input, outputJsonPath: output, env: {}, gemini: { isConfigured: false } });
  assert.equal(result.outputs.length, 1); const doc = JSON.parse(await fs.readFile(output, 'utf8')); validator.assertValid(doc);
  assert.equal(doc.drawings[0].route, 'svg');
});
test('multi-page PDF bundles save one building with all source frames', async t => {
  const dir = await temporary(t), output = path.join(dir, 'building.json');
  const bundles = [0, 1].map(pageIndex => emptyBundle({ format: 'pdf', extractor: { id: 'pdf', name: 'PDF', version: 'test' }, page: { widthPx: 800, heightPx: 1000, pageIndex, pageCount: 2, renderDpi: 150, transforms: [] }, level: null, scale: { source: 'none', confidence: 0, metresPerPx: null } }));
  const result = await mapToSchema({ inputFilePath: path.join(dir, 'plan.pdf'), outputJsonPath: output, bundles, config: loadConfig({}), gemini: { isConfigured: false } });
  assert.equal(result.outputs.length, 1); const doc = JSON.parse(await fs.readFile(output, 'utf8')); validator.assertValid(doc); assert.equal(doc.drawings.length, 2);
});
test('external vision module writes through a validated staging boundary', async t => {
  const dir = await temporary(t), module = path.join(dir, 'vision.mjs'), output = path.join(dir, 'building.json');
  await fs.writeFile(module, `import fs from 'node:fs/promises'; export default async args => { await fs.writeFile(args.outputJsonPath, JSON.stringify(${JSON.stringify(legacy)})); return {ok:true}; };`);
  await routeToVisionAgent({ filePath: path.join(dir, 'plan.png'), outputJsonPath: output, schemaPath: SCHEMA_PATH, detectionDetails: { reason: 'test' } }, { agentModule: module });
  validator.assertValid(JSON.parse(await fs.readFile(output, 'utf8')));
  const before = await fs.readFile(output, 'utf8');
  const invalidModule = path.join(dir, 'invalid.mjs');
  await fs.writeFile(invalidModule, `import fs from 'node:fs/promises'; export default async args => { await fs.writeFile(args.outputJsonPath, '{}'); };`);
  await assert.rejects(routeToVisionAgent({ filePath: 'plan.png', outputJsonPath: output, schemaPath: SCHEMA_PATH, detectionDetails: { reason: 'test' } }, { agentModule: invalidModule }));
  assert.equal(await fs.readFile(output, 'utf8'), before);
});
