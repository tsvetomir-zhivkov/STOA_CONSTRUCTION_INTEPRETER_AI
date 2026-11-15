/**
 * Every id reference resolves, and nothing is silently lost:
 *  - merge identity: elements with different GlobalIds are never merged, however well their boxes overlap (an IFC
 *    building shares one pixel frame, so stacked slabs have IoU 1), and references copied into an existing twin are
 *    remapped (regression: rooms[].boundary_ids pointed at a level-5 ceiling slab folded into its level-2 twin);
 *  - space boundaries to elements that are deliberately not emitted (IfcVirtualElement) or missing are recorded in
 *    rooms[].properties.unresolved_references with GlobalId, IFC class and reason, never invented as entities.
 */
import { test } from 'node:test';
import assert from 'node:assert/strict';
import fs from 'node:fs/promises';
import { existsSync } from 'node:fs';
import os from 'node:os';
import path from 'node:path';
import { fileURLToPath } from 'node:url';
import { processBlueprint } from '../src/index.js';
import { createVersionedValidator } from '../src/schema/versions.js';
import { referenceErrors as validateRelationships } from '../src/storage/building.js';
import { elementRef } from '../src/extractors/ifcExtractor.js';
import { unresolvedReference } from '../src/schema/lift.js';
import { ifcFixture } from './helpers/fixtures.js';
import { silentLogger } from '../src/util/logger.js';

const HERE = path.dirname(fileURLToPath(import.meta.url));
const v = await createVersionedValidator();
const read = async (p) => JSON.parse(await fs.readFile(p, 'utf8'));
const env = (dir) => ({ CORRECTIONS_FILE: path.join(dir, 'none.json') });
const allEntities = (d) => ['rooms', 'walls', 'openings', 'objects'].flatMap((k) => d[k]);

// two storeys; identical ceiling slabs stacked on both (same footprint); a space on each storey bounded by its own slab;
// space 2 is also bounded by a virtual element (not emitted by design) and by an id that is not in the file
const STACKED = ifcFixture().replace('ENDSEC;\nEND-ISO', `#13=IFCCARTESIANPOINT((0.,0.,3000.));
#14=IFCAXIS2PLACEMENT3D(#13,$,$);
#15=IFCLOCALPLACEMENT($,#14);
#141=IFCRECTANGLEPROFILEDEF(.AREA.,$,#32,4000.,3000.);
#142=IFCEXTRUDEDAREASOLID(#141,#11,#30,200.);
#143=IFCSHAPEREPRESENTATION($,'Body','SweptSolid',(#142));
#144=IFCPRODUCTDEFINITIONSHAPE($,$,(#143));
#140=IFCSLAB('0slabA',$,'[L]BG',$,'Ceiling',#12,#144,$,.USERDEFINED.);
#145=IFCSLAB('0slabB',$,'[L]BG',$,'Ceiling',#15,#144,$,.USERDEFINED.);
#46=IFCSPACE('0space2',$,'4-1',$,$,#15,#36,'Kontor',.ELEMENT.,.SPACE.,$);
#185=IFCVIRTUALELEMENT('0virt1',$,'Virtual boundary',$,$,#15,$,$);
#92=IFCRELAGGREGATES('0rel4',$,$,$,#21,(#46));
#93=IFCRELCONTAINEDINSPATIALSTRUCTURE('0rel5',$,$,$,(#140),#20);
#94=IFCRELCONTAINEDINSPATIALSTRUCTURE('0rel6',$,$,$,(#145,#185),#21);
#190=IFCRELSPACEBOUNDARY('0sb1',$,$,$,#40,#56,$,.PHYSICAL.,.EXTERNAL.);
#191=IFCRELSPACEBOUNDARY('0sb2',$,$,$,#40,#140,$,.PHYSICAL.,.INTERNAL.);
#192=IFCRELSPACEBOUNDARY('0sb3',$,$,$,#46,#145,$,.PHYSICAL.,.INTERNAL.);
#193=IFCRELSPACEBOUNDARY('0sb4',$,$,$,#46,#185,$,.VIRTUAL.,.INTERNAL.);
#194=IFCRELSPACEBOUNDARY('0sb5',$,$,$,#46,#999,$,.PHYSICAL.,.INTERNAL.);
ENDSEC;
END-ISO`);

const dir = await fs.mkdtemp(path.join(os.tmpdir(), 'bi-refs-'));
const ifcPath = path.join(dir, 'data', 'demo_set', 'stacked.ifc');
await fs.mkdir(path.dirname(ifcPath), { recursive: true });
await fs.writeFile(ifcPath, STACKED);
const run = (out, extra = {}) => processBlueprint({ inputFilePath: ifcPath, outputJsonPath: path.join(dir, 'out', out), env: env(dir), logger: silentLogger, ...extra });

test('unit: elementRef resolves with the entity key generator and names why a target is not emitted', () => {
  const model = { e: new Map([
    [1, { type: 'IfcSlab', args: ['0slab$_x'] }],
    [2, { type: 'IFCVIRTUALELEMENT', args: ['0virt1'] }],
    [3, { type: 'IFCVEHICLE', args: ['0veh'] }],
    [4, { type: 'IFCWALL', args: ['0wallOther'] }],
  ]) };
  const keyAll = new Set(['ifc_0slab_d_ux']); // the key the slab entity is emitted under ('$' -> '_d', '_' -> '_u')
  assert.deepEqual(elementRef(model, 1, keyAll), { key: 'ifc_0slab_d_ux', expressId: 1, globalId: '0slab$_x', ifcClass: 'IfcSlab', reason: null });
  const virt = elementRef(model, 2, keyAll);
  assert.deepEqual([virt.globalId, virt.ifcClass, virt.reason], ['0virt1', 'IFCVIRTUALELEMENT', 'not_emitted_class']);
  assert.match(virt.detail, /virtual/);
  assert.equal(elementRef(model, 3, keyAll).reason, 'class_not_read');
  assert.equal(elementRef(model, 4, keyAll).reason, 'outside_document');
  assert.deepEqual(elementRef(model, 99, keyAll), { key: 'ifc_99', expressId: 99, globalId: null, ifcClass: null, reason: 'entity_not_in_file' });
  assert.deepEqual(unresolvedReference('boundary_element_ids', virt), {
    field: 'boundary_element_ids', global_id: '0virt1', ifc_class: 'IFCVIRTUALELEMENT', express_id: 2, reason: 'not_emitted_class', detail: virt.detail,
  });
});

test('unresolved boundary references are recorded, not invented; the id list stays strictly valid', async () => {
  const res = await run('stacked.json');
  const doc = await read(res.outputs[0].outputJsonPath);
  assert.deepEqual(v.describe(v.validate(doc)), []);
  assert.deepEqual(validateRelationships(doc), []);
  const room2 = doc.rooms.find((r) => r.ifc_global_id === '0space2');
  const slabB = doc.objects.find((o) => o.ifc_global_id === '0slabB');
  assert.equal(slabB.ifc_class, 'IfcSlab', 'the slab keeps its IFC class');
  assert.deepEqual(room2.boundary_ids, [slabB.id]);
  assert.equal(allEntities(doc).some((e) => e.ifc_global_id === '0virt1'), false, 'no entity is invented for the virtual element');
  const un = room2.properties.unresolved_references;
  assert.deepEqual(un.map((u) => [u.field, u.global_id, u.ifc_class, u.reason]), [
    ['boundary_ids', '0virt1', 'IFCVIRTUALELEMENT', 'not_emitted_class'],
    ['boundary_ids', null, null, 'entity_not_in_file'],
  ]);
  const rel = doc.coverage.find((c) => c.category === 'relationships');
  assert.match(rel.note, /2 space-boundary reference\(s\).*IFCVIRTUALELEMENT 1, unknown 1/);
});

test('merge into a migrated v0.2 file: stacked slabs with different GlobalIds stay distinct, boundary ids resolve', async () => {
  const out = 'stacked.v02.json';
  const r2 = await run(out, { configOverrides: { schemaVersion: '0.2' } });
  const d2 = await read(r2.outputs[0].outputJsonPath);
  assert.equal(d2.objects.some((o) => o.provenance.source_ref === '0slabA'), false, 'v0.2 has no slabs');
  const r3 = await run(out);
  assert.equal(r3.outputs[0].action, 'merged');
  const d3 = await read(r3.outputs[0].outputJsonPath);
  assert.deepEqual(v.describe(v.validate(d3)), []);
  assert.deepEqual(validateRelationships(d3), []);
  const slabs = d3.objects.filter((o) => o.ifc_class === 'IfcSlab');
  assert.deepEqual(slabs.map((s) => s.ifc_global_id).sort(), ['0slabA', '0slabB'], 'both stacked slabs are kept');
  assert.notEqual(slabs[0].level_id, slabs[1].level_id);
  for (const [space, slab] of [['0space1', '0slabA'], ['0space2', '0slabB']]) {
    const room = d3.rooms.find((r) => r.ifc_global_id === space && r.status === 'active');
    assert.ok(room.boundary_ids.includes(slabs.find((s) => s.ifc_global_id === slab).id), `${space} bounded by ${slab}`);
  }
  assert.equal(d3.rooms.filter((r) => r.status === 'active').length, 2, 'the two stacked spaces stay two rooms');
  const room2 = d3.rooms.find((r) => r.ifc_global_id === '0space2');
  assert.equal(room2.properties.unresolved_references.length, 2, 'the record survives the merge');
});

// ---- regression on the real file (not in git: skipped when the dataset is absent) ----------------------------------------------
const MOLIO = path.resolve(HERE, '..', '..', 'data', 'ifc_models', 'bsc-molio_molio-with-uris.ifc');
const BLOCK = path.resolve(HERE, '..', 'big', 'block.json');
const haveMolio = existsSync(MOLIO) && existsSync(BLOCK);

test('regression: molio IFC merged into the migrated v0.2 block.json validates', { skip: !haveMolio && 'dataset file or big/block.json not present', timeout: 300_000 }, async () => {
  const d = await fs.mkdtemp(path.join(os.tmpdir(), 'bi-molio-'));
  const outPath = path.join(d, 'block.json');
  await fs.copyFile(BLOCK, outPath);
  const before = await read(outPath);
  const res = await processBlueprint({ inputFilePath: MOLIO, outputJsonPath: outPath, env: env(d), logger: silentLogger });
  assert.equal(res.outputs[0].action, 'merged');
  const doc = await read(outPath);
  assert.deepEqual(v.describe(v.validate(doc)), []);
  assert.deepEqual(validateRelationships(doc), []);
  // the two references from the original failure resolve to the slabs themselves (IFCSLAB, objects collection)
  for (const [gid, id] of [['3XAHiMg$O5LveU6DMiuzgd', 'obj_ifc_3XAHiMg_dO5LveU6DMiuzgd'], ['1iYEG2WWm0ULS4mtIqybwZ', 'obj_ifc_1iYEG2WWm0ULS4mtIqybwZ']]) {
    const slab = doc.objects.find((o) => o.ifc_global_id === gid);
    assert.ok(slab, `${id} is emitted`);
    assert.deepEqual([slab.ifc_global_id, slab.ifc_class], [gid, 'IfcSlab']);
    assert.ok(doc.rooms.some((r) => r.boundary_ids.includes(slab.id)));
  }
  // no entity of the existing v0.2 document is lost, and every incoming GlobalId is present exactly once (active)
  const refs = new Set(allEntities(doc).map(e=>e.provenance?.source_ref));
  assert.ok(allEntities(before).every(e=>refs.has(e.provenance?.source_ref)));
  const slabGuids = doc.objects.filter((o) => o.ifc_class === 'IfcSlab' && o.status === 'active').map((o) => o.ifc_global_id);
  assert.equal(new Set(slabGuids).size, slabGuids.length);
  assert.equal(slabGuids.length, 53);
  // the 56 IfcVirtualElement boundaries are recorded, not dropped
  const un = doc.rooms.flatMap((r) => r.properties?.unresolved_references ?? []);
  assert.equal(un.filter((u) => u.ifc_class === 'IFCVIRTUALELEMENT' && u.reason === 'not_emitted_class').length, 56);
});
