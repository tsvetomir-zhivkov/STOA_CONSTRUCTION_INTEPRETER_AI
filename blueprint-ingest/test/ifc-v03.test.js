/**
 * Schema v0.3 from IFC: units, explicit vs calculated values, types vs instances vs schedule rows, materials and layer
 * thicknesses, systems, relationships, classes that v0.2 dropped, and products the extractor cannot read.
 * The fixture is generated here (IFC4, millimetres) so every expected value is known exactly.
 */
import { test } from 'node:test';
import assert from 'node:assert/strict';
import fs from 'node:fs/promises';
import os from 'node:os';
import path from 'node:path';
import { processBlueprint } from '../src/index.js';
import { createVersionedValidator } from '../src/schema/versions.js';
import { validateRelationships } from '../src/schema/v03.js';
import { silentLogger } from '../src/util/logger.js';

const IFC = `ISO-10303-21;
HEADER;
FILE_DESCRIPTION(('ViewDefinition [CoordinationView]'),'2;1');
FILE_NAME('house3.ifc','2026-01-01T00:00:00',('tester'),('Demo Corp'),'demo-preprocessor','Demo CAD 1.0','');
FILE_SCHEMA(('IFC4'));
ENDSEC;
DATA;
#1=IFCPROJECT('0proj',#5,'Demo',$,$,'Demo project','Design',$,#2);
#2=IFCUNITASSIGNMENT((#3,#4,#6,#7));
#3=IFCSIUNIT(*,.LENGTHUNIT.,.MILLI.,.METRE.);
#4=IFCSIUNIT(*,.AREAUNIT.,$,.SQUARE_METRE.);
#6=IFCSIUNIT(*,.VOLUMEUNIT.,$,.CUBIC_METRE.);
#7=IFCCONVERSIONBASEDUNIT(#8,.PLANEANGLEUNIT.,'DEGREE',#9);
#8=IFCDIMENSIONALEXPONENTS(0,0,0,0,0,0,0);
#9=IFCMEASUREWITHUNIT(IFCPLANEANGLEMEASURE(0.0174532925199433),#13);
#13=IFCSIUNIT(*,.PLANEANGLEUNIT.,$,.RADIAN.);
#5=IFCOWNERHISTORY($,#15,$,.ADDED.,$,$,$,1767225600);
#15=IFCAPPLICATION(#16,'1.0','Demo CAD','demo');
#16=IFCORGANIZATION($,'Demo Corp',$,$,$);
#10=IFCCARTESIANPOINT((0.,0.,0.));
#11=IFCAXIS2PLACEMENT3D(#10,$,$);
#12=IFCLOCALPLACEMENT($,#11);
#17=IFCSITE('0site',$,'Site',$,$,#12,$,$,.ELEMENT.,(60,10,30,0),(24,45,0,0),12000.,$,$);
#18=IFCBUILDING('0bldg',$,'House',$,$,#12,$,'The House',.ELEMENT.,$,$,$);
#20=IFCBUILDINGSTOREY('0storey1',$,'1.korrus',$,$,#12,$,'Ground floor',.ELEMENT.,0.);
#30=IFCDIRECTION((0.,0.,1.));
#31=IFCCARTESIANPOINT((2000.,1500.));
#32=IFCAXIS2PLACEMENT2D(#31,$);
#33=IFCRECTANGLEPROFILEDEF(.AREA.,$,#32,4000.,3000.);
#34=IFCEXTRUDEDAREASOLID(#33,#11,#30,2700.);
#35=IFCSHAPEREPRESENTATION($,'Body','SweptSolid',(#34));
#36=IFCPRODUCTDEFINITIONSHAPE($,$,(#35));
#40=IFCSPACE('0space1',$,'3-1',$,$,#12,#36,'Kitchen',.ELEMENT.,.SPACE.,$);
#50=IFCCARTESIANPOINT((2000.,-125.));
#51=IFCAXIS2PLACEMENT2D(#50,$);
#52=IFCRECTANGLEPROFILEDEF(.AREA.,$,#51,4000.,250.);
#53=IFCEXTRUDEDAREASOLID(#52,#11,#30,2700.);
#54=IFCSHAPEREPRESENTATION($,'Body','SweptSolid',(#53));
#55=IFCPRODUCTDEFINITIONSHAPE($,$,(#54));
#56=IFCWALLSTANDARDCASE('0wall1',$,'Wall A',$,$,#12,#55,'W1',.STANDARD.);
#100=IFCQUANTITYAREA('NetFloorArea',$,$,11.5,$);
#101=IFCQUANTITYVOLUME('NetVolume',$,$,31.05,$);
#102=IFCELEMENTQUANTITY('0qto1',$,'Qto_SpaceBaseQuantities',$,$,(#100,#101));
#103=IFCRELDEFINESBYPROPERTIES('0rel4',$,$,$,(#40),#102);
#104=IFCPROPERTYSINGLEVALUE('FloorCovering',$,IFCLABEL('Oak parquet'),$);
#105=IFCPROPERTYSET('0pset2',$,'Pset_SpaceCommon',$,(#104));
#106=IFCRELDEFINESBYPROPERTIES('0rel5',$,$,$,(#40),#105);
#110=IFCMATERIAL('Concrete',$,'concrete');
#111=IFCMATERIAL('Insulation',$,$);
#112=IFCMATERIALLAYER(#110,150.,$,'Core',$,$,$);
#113=IFCMATERIALLAYER(#111,50.,.F.,'Insul',$,$,$);
#114=IFCMATERIALLAYERSET((#112,#113),'Ext 200',$);
#115=IFCMATERIALLAYERSETUSAGE(#114,.AXIS2.,.POSITIVE.,-100.,$);
#116=IFCRELASSOCIATESMATERIAL('0mat1',$,$,$,(#56),#115);
#117=IFCQUANTITYLENGTH('Length',$,$,4000.,$);
#118=IFCELEMENTQUANTITY('0qto2',$,'Qto_WallBaseQuantities',$,$,(#117));
#119=IFCRELDEFINESBYPROPERTIES('0rel6',$,$,$,(#56),#118);
#120=IFCCARTESIANPOINT((1000.,-125.));
#121=IFCAXIS2PLACEMENT2D(#120,$);
#122=IFCRECTANGLEPROFILEDEF(.AREA.,$,#121,900.,250.);
#123=IFCEXTRUDEDAREASOLID(#122,#11,#30,2100.);
#124=IFCSHAPEREPRESENTATION($,'Body','SweptSolid',(#123));
#125=IFCPRODUCTDEFINITIONSHAPE($,$,(#124));
#130=IFCDOOR('0door1',$,'Door D1',$,$,#12,#125,'D01',2100.,900.,.DOOR.,.SINGLE_SWING_LEFT.,$);
#131=IFCDOORTYPE('0dtype',$,'Single 900',$,$,$,$,'DT-1',$,.DOOR.,.SINGLE_SWING_LEFT.,$,$);
#132=IFCRELDEFINESBYTYPE('0rel7',$,$,$,(#130),#131);
#133=IFCOPENINGELEMENT('0open1',$,$,$,$,#12,$,$,.OPENING.);
#134=IFCRELVOIDSELEMENT('0rel8',$,$,$,#56,#133);
#135=IFCRELFILLSELEMENT('0rel9',$,$,$,#133,#130);
#141=IFCRECTANGLEPROFILEDEF(.AREA.,$,#32,4000.,3000.);
#142=IFCEXTRUDEDAREASOLID(#141,#11,#30,200.);
#143=IFCSHAPEREPRESENTATION($,'Body','SweptSolid',(#142));
#144=IFCPRODUCTDEFINITIONSHAPE($,$,(#143));
#140=IFCSLAB('0slab1',$,'Floor slab',$,$,#12,#144,$,.FLOOR.);
#151=IFCCARTESIANPOINT((3500.,2500.));
#152=IFCAXIS2PLACEMENT2D(#151,$);
#153=IFCCIRCLEPROFILEDEF(.AREA.,$,#152,50.);
#154=IFCEXTRUDEDAREASOLID(#153,#11,#30,1000.);
#155=IFCSHAPEREPRESENTATION($,'Body','SweptSolid',(#154));
#156=IFCPRODUCTDEFINITIONSHAPE($,$,(#155));
#150=IFCPIPESEGMENT('0pipe1',$,'Riser',$,$,#12,#156,$,.RIGIDSEGMENT.);
#160=IFCDISTRIBUTIONSYSTEM('0sys1',$,'Cold water',$,$,'CW',.DOMESTICCOLDWATER.);
#161=IFCRELASSIGNSTOGROUP('0rel10',$,$,$,(#150),$,#160);
#162=IFCRELSERVICESBUILDINGS('0rel11',$,$,$,#160,(#18));
#170=IFCFLOWTERMINAL('0ft1',$,'Valve box',$,$,#12,$,$);
#180=IFCVEHICLE('0veh',$,'Cart',$,$,#12,$,$,$);
#190=IFCRELSPACEBOUNDARY('0sb1',$,$,$,#40,#56,$,.PHYSICAL.,.EXTERNAL.);
#195=IFCCLASSIFICATION('CSI',$,$,'Uniclass',$,$,$);
#196=IFCCLASSIFICATIONREFERENCE($,'Ss_25_10','Walls',#195,$,$);
#197=IFCRELASSOCIATESCLASSIFICATION('0rel12',$,$,$,(#56),#196);
#90=IFCRELCONTAINEDINSPATIALSTRUCTURE('0rel2',$,$,$,(#56,#130,#140,#150,#170,#180),#20);
#91=IFCRELAGGREGATES('0rel3',$,$,$,#20,(#40));
#92=IFCRELAGGREGATES('0agg1',$,$,$,#1,(#17));
#93=IFCRELAGGREGATES('0agg2',$,$,$,#17,(#18));
#94=IFCRELAGGREGATES('0agg3',$,$,$,#18,(#20));
ENDSEC;
END-ISO-10303-21;
`;

const dir = await fs.mkdtemp(path.join(os.tmpdir(), 'bi-v03-'));
const ifcPath = path.join(dir, 'data', 'demo_set', 'house3.ifc');
await fs.mkdir(path.dirname(ifcPath), { recursive: true });
await fs.writeFile(ifcPath, IFC);
const env = { CORRECTIONS_FILE: path.join(dir, 'none.json') };
const run = (out, extra = {}) => processBlueprint({ inputFilePath: ifcPath, outputJsonPath: path.join(dir, 'out', out), env, logger: silentLogger, ...extra });
const read = async (p) => JSON.parse(await fs.readFile(p, 'utf8'));
const v = await createVersionedValidator();

const res = await run('house3.json');
const doc = await read(res.outputs[0].outputJsonPath);
const by = (coll, pred) => doc[coll].find(pred);
const wall = by('walls', (w) => w.global_id === '0wall1');
const door = by('openings', (o) => o.global_id === '0door1');
const room = by('rooms', (r) => r.global_id === '0space1');

test('v0.3 IFC document is valid (schema + every reference resolves)', () => {
  const r = v.validate(doc);
  assert.equal(r.version, '0.3');
  assert.deepEqual(v.describe(r), []);
  assert.deepEqual(validateRelationships(doc), []);
});

test('project metadata and declared units (never assumed)', () => {
  const p = doc.project;
  assert.equal(p.name, 'Demo');
  assert.equal(p.long_name, 'Demo project');
  assert.equal(p.phase, 'Design');
  assert.equal(p.schema_identifier, 'IFC4');
  assert.deepEqual([p.application.name, p.application.version, p.application.developer], ['Demo CAD', '1.0', 'Demo Corp']);
  assert.equal(p.application.originating_system, 'Demo CAD 1.0');
  assert.equal(p.created_at, '2026-01-01T00:00:00.000Z');
  assert.deepEqual([p.units.length.unit, p.units.length.to_si, p.units.length.status], ['MILLI METRE', 0.001, 'explicit']);
  assert.deepEqual([p.units.area.unit, p.units.area.to_si], ['SQUARE_METRE', 1]);
  assert.deepEqual([p.units.angle.unit, p.units.angle.to_si, p.units.angle.status], ['DEGREE', 1, 'explicit']);
  assert.equal(p.units.mass.status, 'not_available');
  assert.equal(doc.site.ref_latitude_deg, 60.175);
  assert.equal(doc.site.ref_longitude_deg, 24.75);
  assert.equal(doc.site.ref_elevation_m, 12);
  assert.equal(doc.building.name, 'House');
  assert.equal(doc.sources[0].format, 'ifc');
  assert.match(doc.sources[0].sha256, /^[0-9a-f]{64}$/);
});

test('space: explicit quantities win over the footprint; finish and boundary are read', () => {
  assert.equal(room.area_computed_m2, 12); // 4 x 3 m footprint
  assert.equal(room.area_m2, 11.5); // NetFloorArea
  assert.equal(room.quantities.area_m2.status, 'explicit');
  assert.equal(room.quantities.area_computed_m2.status, 'calculated');
  assert.equal(room.volume_m3, 31.05); // NetVolume, not area x height
  assert.equal(room.quantities.volume_m3.status, 'explicit');
  assert.equal(room.height_m, 2.7);
  assert.equal(room.quantities.height_m.status, 'calculated');
  assert.equal(room.quantities['Qto_SpaceBaseQuantities.NetFloorArea'].raw.unit, 'SQUARE_METRE');
  assert.deepEqual(room.finishes.floor, { value: 'Oak parquet', status: 'explicit', source: 'Pset_SpaceCommon.FloorCovering', covering_ids: [], material_ids: [] });
  assert.equal(room.finishes.ceiling.status, 'not_available');
  assert.deepEqual(room.boundary_element_ids, [wall.id]);
  assert.deepEqual(wall.room_ids, [room.id]);
});

test('wall: layer thicknesses converted from mm; explicit length; classification', () => {
  const set = by('material_layer_sets', (s) => s.id === wall.material_layer_set_id);
  assert.ok(set, 'wall has a layer set');
  assert.equal(set.kind, 'layer_set');
  assert.deepEqual(set.layers.map((l) => [doc.materials.find((m) => m.id === l.material_id).name, l.thickness_m]), [['Concrete', 0.15], ['Insulation', 0.05]]);
  assert.equal(set.total_thickness_m, 0.2);
  assert.equal(set.layers[1].is_ventilated, false);
  // the drawn footprint is 250 mm; the layer set says 200 mm and wins (calculated from explicit layers)
  assert.equal(wall.thickness_m, 0.2);
  assert.equal(wall.quantities.thickness_m.method, 'sum of IfcMaterialLayer.LayerThickness');
  assert.equal(wall.length_m, 4);
  assert.equal(wall.quantities.length_m.status, 'explicit');
  assert.deepEqual(wall.classifications, [{ system: 'Uniclass', code: 'Ss_25_10', name: 'Walls' }]);
  assert.equal(doc.materials.find((m) => m.name === 'Concrete').category, 'concrete');
});

test('door: instance, type and schedule entry stay distinct', () => {
  assert.equal(door.width_m, 0.9);
  assert.equal(door.height_m, 2.1);
  assert.equal(door.quantities.width_m.status, 'explicit');
  assert.deepEqual(door.quantities.width_m.raw, { value: 900, unit: 'MILLI METRE' });
  assert.equal(door.mark, 'D01');
  assert.equal(door.operation, 'SINGLE_SWING_LEFT');
  assert.equal(door.wall_id, wall.id);
  const type = by('element_types', (t) => t.id === door.type_id);
  assert.deepEqual([type.kind, type.name, type.tag, type.operation, type.predefined_type, type.ifc_class], ['door', 'Single 900', 'DT-1', 'SINGLE_SWING_LEFT', 'DOOR', 'IFCDOORTYPE']);
  assert.deepEqual(type.instance_ids, [door.id]);
  const sch = doc.schedules.find((s) => s.kind === 'door');
  assert.equal(sch.status, 'calculated');
  assert.deepEqual(sch.entries, [{ mark: 'DT-1', type_id: type.id, name: 'Single 900', instance_ids: [door.id], count: 1, width_m: 0.9, height_m: 2.1 }]);
});

test('MEP system membership and the building it serves', () => {
  const pipe = by('objects', (o) => o.global_id === '0pipe1');
  assert.equal(pipe.discipline, 'mep');
  assert.equal(pipe.ifc_class, 'IFCPIPESEGMENT');
  const sys = by('systems', (s) => s.id === pipe.system_ids[0]);
  assert.deepEqual([sys.name, sys.kind, sys.predefined_type, sys.long_name], ['Cold water', 'distribution_system', 'DOMESTICCOLDWATER', 'CW']);
  assert.deepEqual(sys.member_ids, [pipe.id]);
  assert.ok(doc.relationships.some((r) => r.kind === 'serves' && r.from_id === sys.id && r.to_id === doc.building.id));
});

test('no IFC class is dropped: v0.2 count-only and footprint-less elements kept; unreadable classes named', () => {
  const slab = by('objects', (o) => o.global_id === '0slab1');
  assert.ok(slab, 'IfcSlab emitted');
  assert.equal(slab.discipline, 'structural');
  const ft = by('objects', (o) => o.global_id === '0ft1');
  assert.ok(ft, 'element without geometry kept');
  assert.equal(ft.polygon_px, null);
  assert.ok(ft.flags.includes('no_plan_geometry'));
  const cls = doc.coverage.find((c) => c.category === 'ifc_classes');
  assert.equal(cls.status, 'partial');
  assert.match(cls.note, /NOT READ \(class unknown to the extractor\): IFCVEHICLE 1/);
  assert.match(cls.note, /IFCOPENINGELEMENT 1/);
  const stat = doc.statistics.find((s) => s.name === 'count.openings.door');
  assert.deepEqual([stat.value, stat.exact], [1, true]);
});

test('--schema 0.2 still writes v0.2 without the v0.3-only elements; a v0.3 run merges into that file', async () => {
  const r2 = await run('house3.v02.json', { configOverrides: { schemaVersion: '0.2' } });
  const d2 = await read(r2.outputs[0].outputJsonPath);
  assert.equal(d2.schema_version, '0.2');
  assert.ok(v.validate(d2).valid);
  assert.equal(d2.objects.some((o) => o.provenance.source_ref === '0slab1' || o.provenance.source_ref === '0ft1'), false);
  assert.equal('project' in d2, false);
  // same pixel frame, so the v0.3 extraction merges into the existing v0.2 file (migrated in memory first)
  const r3 = await run('house3.v02.json');
  assert.equal(r3.outputs[0].action, 'merged');
  const d3 = await read(r3.outputs[0].outputJsonPath);
  assert.equal(d3.schema_version, '0.3');
  assert.deepEqual(v.describe(v.validate(d3)), []);
  assert.ok(d3.objects.some((o) => o.global_id === '0slab1'));
  assert.equal(d3.walls.length, 1, 'the wall is not duplicated');
  // a v0.2 run into a v0.3 file is refused
  await assert.rejects(run('house3.v02.json', { configOverrides: { schemaVersion: '0.2' } }), /refusing to merge a schema 0.2 extraction/);
});
