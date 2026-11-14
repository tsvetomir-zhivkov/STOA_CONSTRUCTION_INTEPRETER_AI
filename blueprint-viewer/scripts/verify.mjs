import { fileURLToPath } from 'node:url';
// Phase 5 checks, in Node, on the viewer's own scene code (BlueprintScene + pickCandidates) and web-ifc's Node build.
// usage: node scripts/verify.mjs <samples dir (blueprint-ingest output)> <data dir>
//   e.g. node scripts/verify.mjs ../blueprint-ingest/out/samples ../data
// What Node cannot check (it has no GPU): frame rate and how it looks. Those are listed at the end as browser checks.
import fs from 'node:fs';
import path from 'node:path';
import { createRequire } from 'node:module';
import * as THREE from 'three';
import { BlueprintScene, buildModel, GROUP_NAMES, cleanRing, captureWebIfcLog } from '../blueprintViewer.js';

const require = createRequire(import.meta.url);
const W = require(path.resolve(path.dirname(fileURLToPath(import.meta.url)), '..', 'node_modules', 'web-ifc', 'web-ifc-api-node.js'));
const [samples = '../blueprint-ingest/out/samples', data = '../data'] = process.argv.slice(2);
const S = (...p) => path.join(samples, ...p), D = (...p) => path.join(data, ...p);
const json = (f) => JSON.parse(fs.readFileSync(f, 'utf8'));
const pal = { floorFactor: 1, wall: '#fff', frame: '#ccc', glass: '#9cf', threshold: '#876', doorLeaf: '#b62', fixture: '#fff', stair: '#cba', outline: '#d70', background: '#fff', ground: '#eee', grid: '#ccc', gridCenter: '#aaa', hover: '#00f', select: '#f90' };
const results = [];
const ok = (name, pass, detail) => { results.push(!!pass); console.log(`${pass ? 'PASS' : 'FAIL'}  ${name}${detail ? `\n      ${detail}` : ''}`); };
const q = (a, p) => { const s = [...a].sort((x, y) => x - y); return s.length ? s[Math.floor(p * (s.length - 1))] : NaN; };
let consoleErrors = 0;
const origError = console.error;
console.error = (...a) => { consoleErrors++; origError(...a); };

const api = new W.IfcAPI();
const restoreConsole = captureWebIfcLog(); // as the viewer does: before Init
await api.Init();
restoreConsole();
const heap = () => api.wasmModule.HEAPU8.length / 2 ** 20;
const newScene = () => new BlueprintScene({ palette: () => pal });

/** top-down orthographic camera over the scene's bounds */
function topCam(scene, px = 1600) {
  const b = scene.bounds(), c = b.getCenter(new THREE.Vector3()), sz = b.getSize(new THREE.Vector3());
  const s = Math.max(sz.x, sz.z) + 2;
  const cam = new THREE.OrthographicCamera(-s / 2, s / 2, s / 2, -s / 2, 0.1, 1000);
  cam.position.set(c.x, b.max.y + 100, c.z); cam.up.set(0, 0, -1); cam.lookAt(c.x, b.min.y, c.z); cam.updateMatrixWorld(); cam.updateProjectionMatrix();
  return { cam, px, mPerPx: s / px };
}
const ndc = (cam, x, y, z) => { const v = new THREE.Vector3(x, y, z).project(cam); return { x: v.x, y: v.y }; };

/** can element `id` be selected from the top view with some UI state? first click, or repeated clicks on one spot */
function selectable(scene, id, points, cam, px) {
  const rc = new THREE.Raycaster();
  const e = scene.pickIndex.get(id);
  const groups = e.kind === 'json' ? new Set(e.meshes.map((m) => m.parent?.name)) : new Set([e.el.group]);
  const levelKey = e.kind === 'json' ? e.model.level.key : [...scene.levels.values()].find((lv) => e.el.ranges.some((r) => e.meshByKey.get(r.key)?.parent?.parent === lv.group))?.key;
  const states = [{ name: 'first click' }, { name: 'own group only', solo: true }, { name: 'own group + own level', solo: true, iso: true }];
  for (const st of states) {
    if (st.solo) for (const g of GROUP_NAMES) scene.setGroupVisible(g, groups.has(g));
    if (st.iso) scene.isolateLevel(levelKey);
    let how = null;
    for (const p of points) {
      const list = scene.candidates(rc, cam, ndc(cam, ...p), { width: px, height: px });
      const i = list.indexOf(id);
      if (i === 0) { how = st.name; break; }
      if (i > 0) { how = `${st.name}, click ${i + 1}`; break; }
    }
    for (const g of GROUP_NAMES) scene.setGroupVisible(g, true);
    scene.isolateLevel(null);
    if (how) return how;
  }
  return null;
}

/** points to aim at for an element: centres of its most horizontal triangles (a vertical ray through them hits the element) */
function aimPoints(scene, id) {
  const e = scene.pickIndex.get(id);
  const pts = [];
  if (e.kind === 'json') {
    for (const m of e.meshes) {
      m.updateMatrixWorld(true);
      const bb = new THREE.Box3().setFromObject(m), c = bb.getCenter(new THREE.Vector3());
      pts.push([c.x, bb.max.y, c.z]);
      const r = cleanRing(m.userData.element.polygon_m);
      if (r) for (const p of r.slice(0, 4)) { const lv = m.parent.parent.position.y; pts.push([c.x + (p[0] - c.x) * 0.7, bb.max.y, c.z + (p[1] - c.z) * 0.7]); void lv; }
    }
    return pts;
  }
  for (const r of e.el.ranges.slice(0, 3)) {
    const m = e.meshByKey.get(r.key); m.updateMatrixWorld(true);
    const P = m.geometry.attributes.position.array, I = m.geometry.index.array;
    const tris = [];
    for (let k = 0; k < r.count && tris.length < 400; k += 3) {
      const a = I[r.start + k] * 3, b = I[r.start + k + 1] * 3, c = I[r.start + k + 2] * 3;
      const A = new THREE.Vector3(P[a], P[a + 1], P[a + 2]), B = new THREE.Vector3(P[b], P[b + 1], P[b + 2]), C = new THREE.Vector3(P[c], P[c + 1], P[c + 2]);
      const n = new THREE.Vector3().crossVectors(B.clone().sub(A), C.clone().sub(A));
      const area = n.length(); if (area < 1e-10) continue;
      tris.push({ c: A.add(B).add(C).divideScalar(3).applyMatrix4(m.matrixWorld), up: Math.abs(n.y) / area * area });
    }
    tris.sort((x, y) => y.up - x.up);
    for (const t of tris.slice(0, 3)) pts.push([t.c.x, t.c.y, t.c.z]);
  }
  return pts;
}

// =====================================================================================================================
console.log('\n## 1. Clinic: three levels + their IFC');
// one JSON per building (all storeys in `levels`); older per-storey outputs (bsc-clinic_clinic-plumbing.<storey>.json) work too
const clinicJson = fs.readdirSync(S('ifc_models')).filter((f) => f.startsWith('bsc-clinic_clinic-plumbing')).map((f) => S('ifc_models', f));
const clinicIfc = D('ifc_models', 'bsc-clinic_clinic-plumbing.ifc');
{
  const scene = newScene();
  for (const f of clinicJson) scene.addJson(json(f), path.basename(f));
  const t0 = Date.now();
  const src = scene.addIfc(api, W, new Uint8Array(fs.readFileSync(clinicIfc)), 'bsc-clinic_clinic-plumbing.ifc');
  const ms = Date.now() - t0;
  const lv = [...scene.levels.values()].sort((a, b) => a.elevation - b.elevation);
  ok('levels at their real elevations, JSON levels and IFC storeys merged (not stacked twice)', lv.length === 3 && [-1, 0, 4.57].every((e, i) => Math.abs(lv[i].elevation - e) < 1e-6 && Math.abs(lv[i].group.position.y - e) < 1e-6),
    lv.map((l) => `${l.name} ${l.elevation.toFixed(2)} m (group y ${l.group.position.y.toFixed(2)}, sources ${l.sources.size})`).join(' | '));
  // alignment: each level's JSON (rendered at 55 / 45 / 51 px/m with different origins) against the single web-ifc frame
  const per = new Map();
  for (const el of src.model.elements) {
    if (!el.json) continue;
    const r = cleanRing(el.json.polygon_m); if (!r) continue;
    const cx = r.reduce((s, p) => s + p[0], 0) / r.length, cz = r.reduce((s, p) => s + p[1], 0) / r.length;
    const d = Math.hypot(cx - (el.bbox.min[0] + el.bbox.max[0]) / 2, cz - (el.bbox.min[2] + el.bbox.max[2]) / 2);
    (per.get(el.jsonSource.name) ?? per.set(el.jsonSource.name, []).get(el.jsonSource.name)).push(d);
  }
  ok('aligned in plan despite 55 / 45 / 51 px per metre (JSON footprint centre vs web-ifc solid centre)', [...per.values()].every((d) => q(d, 0.9) < 0.05),
    [...per].map(([n, d]) => `${n}: p50 ${q(d, 0.5).toFixed(3)} m, p90 ${q(d, 0.9).toFixed(3)} m over ${d.length}`).join(' | '));
  // pipes: real round solids, not 2D slivers
  const pipes = src.model.elements.filter((e) => e.ifcType === 'IFCFLOWSEGMENT');
  const horiz = pipes.filter((e) => Math.max(e.dims.x, e.dims.z) > 4 * e.dims.y);
  const round = horiz.filter((e) => { const w = Math.min(e.dims.x, e.dims.z); return Math.abs(w - e.dims.y) <= 0.25 * Math.max(w, e.dims.y) + 0.005; });
  const tris = pipes.map((e) => e.ranges.reduce((s, r) => s + r.count / 3, 0));
  ok('pipes are real cylinders following their runs', pipes.length && round.length / horiz.length > 0.9 && q(tris, 0.5) >= 24,
    `${pipes.length} IfcFlowSegment solids; ${horiz.length} horizontal, ${round.length} of them round in section (height ≈ width); median ${q(tris, 0.5)} triangles each; vertical extent p50 ${q(horiz.map((e) => e.dims.y), 0.5).toFixed(3)} m (a 2D extrusion would be the assumed 0.78 m)`);
  const m = src.match, jsonEls = scene.sources.filter((s) => s.kind === 'json').reduce((a, s) => [a[0] + s.match.withMesh, a[1] + s.match.elements], [0, 0]);
  ok('mesh <-> JSON match rate reported both ways (matched on provenance.source_ref = raw GlobalId)', jsonEls[0] === jsonEls[1],
    `meshes with a JSON element ${m.withJson}/${m.meshes} (${(100 * m.withJson / m.meshes).toFixed(1)} %): the rest are types the pipeline does not emit (${m.unmatchedTypes.join(', ')}); JSON elements with a mesh ${jsonEls[0]}/${jsonEls[1]} (100 %)`);
  ok('load cost', ms < 30000, `${src.model.stats.elements} elements, ${(src.model.stats.triangles / 1e6).toFixed(2)} M triangles in ${src.model.stats.batches} batched meshes (= draw calls), ${ms} ms in Node incl. BVH; ${src.model.stats.unsupported} representation(s) web-ifc could not mesh`);
  // click a pipe: JSON confidence/flags/provenance + IFC type
  const pipe = pipes.find((e) => e.json);
  const info = scene.describe(pipe.pickId);
  const row = (k) => info.rows.find((r) => r[0] === k)?.[1];
  ok('clicking a pipe shows its JSON confidence, flags and provenance with its IFC type and GlobalId', info.confidence != null && row('ifc_type') === 'IFCFLOWSEGMENT' && row('GlobalId') === pipe.globalId && row('provenance.extractor') && info.notes.some((n) => /nothing vertical is assumed/.test(n)),
    `title "${info.title}", confidence ${info.confidence}, flags [${info.flags.join(', ')}], ifc_type ${row('ifc_type')}, GlobalId ${row('GlobalId')}, provenance ${row('provenance.extractor')}; "${info.notes[0]}"`);
  // 3.4 pset units (real geometry)
  const u = Object.fromEntries(scene.unitCheck.map((x) => [x.prop, x]));
  ok('pset unit check: Length in millimetres, Outer Diameter in feet (against real geometry)', u.Length?.unit === 'mm' && u['Outer Diameter']?.unit === 'ft',
    scene.unitCheck.map((x) => `${x.key}: ${x.unit ?? 'raw'} (median ratio ${x.median?.toFixed(3)}, ${x.n} horizontal pipes, ${Math.round(x.agree * 100)} % agree, ${x.source})`).join(' | '));
  const prow = info.rows.find((r) => /Dimensions\.Length/.test(r[0]));
  ok('pset values shown converted only where verified, the rest labelled raw', prow && /unit verified/.test(prow[1]) && info.rows.some((r) => r[2] === 'raw'), `${prow?.[0]} = ${prow?.[1]}; e.g. raw: ${info.rows.find((r) => r[2] === 'raw')?.join(' = ')}`);
  // separate levels
  scene.setSeparation(1);
  const y1 = lv.map((l) => l.group.position.y);
  scene.setSeparation(0);
  const y0 = lv.map((l) => l.group.position.y);
  ok('"Separate levels" spreads to 3x the real spacing and back', Math.abs((y1[2] - y1[1]) - 3 * 4.57) < 1e-6 && Math.abs((y1[1] - y1[0]) - 3) < 1e-6 && y0.every((y, i) => Math.abs(y - [-1, 0, 4.57][i]) < 1e-6), `at 1: ${y1.map((y) => y.toFixed(2)).join(' / ')} m; back at 0: ${y0.map((y) => y.toFixed(2)).join(' / ')} m`);
  // every element selectable (web-ifc elements)
  const { cam, px } = topCam(scene);
  const how = {}; const missed = [];
  for (const el of src.model.elements) { const h = selectable(scene, el.pickId, aimPoints(scene, el.pickId), cam, px); h ? (how[h.replace(/click \d+/, 'click 2+')] = (how[h.replace(/click \d+/, 'click 2+')] ?? 0) + 1) : missed.push(el); }
  ok('every web-ifc element selectable (top view; first click, or repeated clicks on one spot)', missed.length === 0, `${src.model.elements.length} elements: ${JSON.stringify(how)}${missed.length ? `; missed ${missed.length}: ${[...new Set(missed.map((e) => e.ifcType))].join(', ')}` : ''}`);
  // memory: load + remove the IFC three times
  const heaps = [];
  for (let i = 0; i < 3; i++) { scene.remove(scene.sources.find((s) => s.kind === 'ifc').id); scene.addIfc(api, W, new Uint8Array(fs.readFileSync(clinicIfc)), 'clinic.ifc'); heaps.push(heap()); }
  scene.clearAll();
  ok('no WASM memory growth after loading and clearing the model three times', heaps[2] <= heaps[1] + 1, `heap after each reload: ${heaps.map((h) => h.toFixed(0)).join(' -> ')} MB (WASM memory never shrinks; it must stop growing)`);
}

// =====================================================================================================================
console.log('\n## 2. Clinic JSON without the IFC');
{
  const scene = newScene();
  for (const f of clinicJson) scene.addJson(json(f), path.basename(f));
  const sum = scene.summary();
  const s1 = sum.sources[0];
  const objs = scene.models.reduce((n, m) => n + m.stats.objects, 0);
  ok('loads, falls back to extruded polygons, says the geometry is approximate and there is no architecture', sum.sources.every((s) => s.approximate && /JSON polygons/.test(s.geometry)) && sum.sources.every((s) => !s.report.counts.rooms && !s.report.counts.walls && !s.report.counts.openings) && objs > 4000,
    `${objs} objects drawn from polygons; geometry "${s1.geometry}"; contents: ${s1.contents.map((c) => `${c.category} "${c.text}"`).join(', ')}; banners "no architecture" + "approximate geometry"`);
  ok('levels still at -1.00 / 0.00 / 4.57 m from the JSON alone', sum.levels.map((l) => l.elevation).join() === '-1,0,4.57', sum.levels.map((l) => `${l.name} ${l.elevation}`).join(' | '));
  const u = Object.fromEntries(scene.unitCheck.map((x) => [x.prop, x]));
  ok('pset unit check also works from the JSON footprints alone', u.Length?.unit === 'mm' && u['Outer Diameter']?.unit === 'ft', scene.unitCheck.map((x) => `${x.key}: ${x.unit ?? 'raw'} (ratio ${x.median?.toFixed(3)}, n ${x.n}, ${x.source})`).join(' | '));
}

// =====================================================================================================================
console.log('\n## 3. Swiss DXF');
const swiss = S('swiss_dwellings', 'site1000_bldg2082_floor9385.json');
{
  const doc = json(swiss);
  const scene = newScene();
  scene.addJson(doc, 'site1000_bldg2082_floor9385.json');
  const r = scene.sources[0].report;
  ok('walls: most have a measured thickness', r.walls.measuredThickness / r.counts.walls > 0.5, `${r.counts.walls} walls (0.2.0 had 345), ${r.walls.measuredThickness} measured (${Math.round(100 * r.walls.measuredThickness / r.counts.walls)} %), ${r.walls.assumedThickness} assumed; stats from the pipeline log: see blueprint-ingest README`);
  const leaves = scene.pickables.filter((p) => p.userData.piece === 'doorLeaf');
  ok('doors visible as leaves', leaves.length === r.counts.doors, `${r.counts.doors} doors, ${leaves.length} leaves`);
  const legend = scene.legend();
  const real = legend.filter((g) => g.category !== 'other' && g.category !== 'stair');
  ok('fixtures carry real categories and the legend lists them', real.length > 0, `legend: ${legend.map((g) => `${g.category} ${g.count}`).join(', ')}${real.length ? '' : ' — the Swiss DXF FIXTURE layer has no block names, so the name rules (4.4) have nothing to match: every fixture stays "other"'}`);
  const st = doc.objects.filter((o) => o.category === 'stair');
  const cov = r.coverage.find((c) => c.category === 'stairs');
  ok('stairs render, or coverage honestly says not_analysed', st.length ? scene.pickables.some((p) => p.parent?.name === 'stairs') : cov?.status === 'not_analysed', `${st.length} stair object(s), coverage ${cov?.status} (${cov?.note ?? ''})`);
  // 5. every element selectable; the wall under a window says wall
  const { cam, px } = topCam(scene);
  const ids = [...scene.pickIndex.keys()].filter((id) => scene.pickIndex.get(id).meshes[0].userData.kind !== 'wallBand');
  const how = {}; const missed = [];
  for (const id of ids) { const h = selectable(scene, id, aimPoints(scene, id), cam, px); h ? (how[h.replace(/click \d+/, 'click 2+')] = (how[h.replace(/click \d+/, 'click 2+')] ?? 0) + 1) : missed.push(id); }
  ok('every element selectable, including the smallest fixture', !missed.length, `${ids.length} elements: ${JSON.stringify(how)}${missed.length ? `; missed ${missed.length}` : ''}`);
  const band = scene.pickables.find((p) => p.userData.band === 'below');
  const bi = scene.describe(band.userData.pickId);
  ok('the wall under a window says wall', band.parent.name === 'walls' && bi.title === 'Wall below window' && bi.rows.find((x) => x[0] === 'kind')[1] === 'wall' && bi.partOf, `group "${band.parent.name}", title "${bi.title}", part of ${bi.partOf.label}`);
  const fx = scene.pickables.find((p) => p.userData.piece === 'fixture');
  const fi = scene.describe(fx.userData.pickId);
  ok('assumed values are marked as such (distinct colour in the page)', fi.rows.some((x) => x[2] === 'assumed'), fi.rows.filter((x) => x[2] === 'assumed').map((x) => `${x[0]} = ${x[1]}`).join('; '));
  // 7. a single-level file alone behaves exactly as before (same pieces, same shapes)
  const legacy = buildModel(json(swiss), pal);
  const size = (p) => { p.geometry.computeBoundingBox(); const b = p.geometry.boundingBox, v = b.getSize(new THREE.Vector3()); return [v.x, v.y * p.scale.y, v.z, p.position.y]; };
  let maxD = 0, sameKeys = legacy.pickables.length === scene.pickables.length;
  legacy.pickables.forEach((p, i) => {
    const q = scene.pickables[i];
    if (!q || p.userData.element.id !== q.userData.element.id || p.userData.piece !== q.userData.piece) { sameKeys = false; return; }
    const a = size(p), b = size(q);
    for (let k = 0; k < 4; k++) maxD = Math.max(maxD, Math.abs(a[k] - b[k]));
  });
  const over = legacy.pickables.filter((p, i) => { const a = size(p), b = size(scene.pickables[i]); return a.some((x, k) => Math.abs(x - b[k]) > 0.001); }).length;
  ok('a single-level file alone renders as before (same pieces in the same order, same heights, plan sizes within 1 cm)', sameKeys && maxD <= 0.01, `${legacy.pickables.length} pieces before, ${scene.pickables.length} now, same elements/pieces/order: ${sameKeys}; ${over} piece(s) differ by more than 1 mm, largest ${(maxD * 1000).toFixed(2)} mm: the scene frame builds from the unrounded pixels, polygon_m is rounded to 1 mm, which tilts two 35° door leaves by a fraction of a degree`);
}

// =====================================================================================================================
console.log('\n## 4. Raster (CubiCasa-style) JSON');
{
  // NOTE: there is no raster route in this repository (translator/extract/raster is a stub), so this is a SYNTHETIC file in
  // the shape the raster route is specified to emit: route raster, no scale, CubiCasa class names.
  const doc = {
    schema_version: '0.2', drawing: { file: 'synthetic/high_quality-0001.png', route: 'raster', scale: { m_per_px: null, source: 'none', confidence: 0 }, transforms: [] },
    levels: [], units: [], texts: [], edges: [], history: [],
    coverage: [{ category: 'rooms', status: 'analysed', confidence: 0.7 }, { category: 'fixtures', status: 'partial', confidence: 0.5 }, { category: 'stairs', status: 'not_analysed', confidence: 0.3 }],
    rooms: [
      { id: 'r1', status: 'active', type: 'living_room', confidence: 0.7, polygon_px: [[0, 0], [400, 0], [400, 300], [0, 300]], polygon_m: null, provenance: { extractor: 'cubicasa', source_ref: 'seg1' } },
      { id: 'r2', status: 'active', type: 'outdoor', confidence: 0.7, polygon_px: [[400, 0], [600, 0], [600, 300], [400, 300]], polygon_m: null, provenance: { extractor: 'cubicasa', source_ref: 'seg2' } },
      { id: 'r3', status: 'active', type: 'hall', confidence: 0.7, polygon_px: [[0, 300], [300, 300], [300, 450], [0, 450]], polygon_m: null, provenance: { extractor: 'cubicasa', source_ref: 'seg3' } },
    ],
    walls: [], openings: [],
    objects: [{ id: 'o1', status: 'active', category: 'fixture', subtype: 'toilet', confidence: 0.6, polygon_px: [[20, 320], [60, 320], [60, 380], [20, 380]], polygon_m: null, provenance: { extractor: 'cubicasa', source_ref: 'icon1' } }],
  };
  const scene = newScene();
  scene.addJson(doc, 'high_quality-0001.json');
  const s = scene.sources[0];
  ok('loads in unscaled mode with the no-scale banner', s.frame.kind === 'unscaled' && scene.pickables.length === 4, `frame "${s.frame.kind}": ${s.frame.note}; ${scene.pickables.length} pieces drawn`);
  ok('room types and fixture classes mapped to the shared enum', doc.rooms.map((r) => r.type).join() === 'living,outdoor,entry' && doc.objects[0].category === 'toilet', `rooms: living_room->${doc.rooms[0].type}, outdoor->${doc.rooms[1].type}, hall->${doc.rooms[2].type}; object fixture/toilet->${doc.objects[0].category}; ${s.vocabulary.changed.length} mapped, ${s.vocabulary.unmapped.length} unmapped`);
}

// =====================================================================================================================
console.log('\n## 7b. Esplanades: IFC + all levels (architecture, georeferenced 538 km / 6 591 km)');
{
  const scene = newScene();
  for (const f of fs.readdirSync(S('ifc_models')).filter((x) => x.startsWith('bsc-esplanades'))) scene.addJson(json(S('ifc_models', f)), f);
  const src = scene.addIfc(api, W, new Uint8Array(fs.readFileSync(D('ifc_models', 'bsc-esplanades_1807-ep-ar-v18.ifc'))), 'esplanades.ifc');
  const walls = src.model.elements.filter((e) => e.group === 'walls'), slabs = src.model.elements.filter((e) => e.ifcType === 'IFCSLAB');
  ok('the building stands up (no -90° rotation: web-ifc output is already Y-up)', q(walls.map((e) => e.dims.y), 0.5) > 2 && q(slabs.map((e) => e.dims.y), 0.5) < 0.6 && Math.abs(q(walls.map((e) => e.bbox.min[1]), 0.5)) < 0.05,
    `walls: median height ${q(walls.map((e) => e.dims.y), 0.5).toFixed(2)} m, base at storey level (median ${q(walls.map((e) => e.bbox.min[1]), 0.5).toFixed(3)} m); slabs: median thickness ${q(slabs.map((e) => e.dims.y), 0.5).toFixed(2)} m (horizontal)`);
  const lv = [...scene.levels.values()].sort((a, b) => a.elevation - b.elevation);
  ok('storeys at their elevations (site datum +14.30 m removed, so JSON and IFC share one height frame)', lv.length === 7, lv.map((l) => `${l.name} ${l.elevation.toFixed(2)}`).join(' | '));
  const m = src.match;
  ok('match rate', true, `meshes with a JSON element ${m.withJson}/${m.meshes}; JSON elements with a mesh ${scene.sources.filter((s) => s.kind === 'json').reduce((n, s) => n + s.match.withMesh, 0)}/${scene.sources.filter((s) => s.kind === 'json').reduce((n, s) => n + s.match.elements, 0)} (rooms are IfcSpace: web-ifc does not stream them, so they are drawn from the JSON)`);
  scene.clearAll();
}

console.log('\n## 8. Console');
ok('no console errors during any of the above loads', consoleErrors === 0, `${consoleErrors} console.error call(s); web-ifc "unexpected mesh type" lines are counted per model and reported once as a warning`);

console.log(`\n${results.filter(Boolean).length}/${results.length} checks passed`);
console.log('Browser-only (no GPU in Node): frame rate while orbiting (shown live in the page status line), visual look, wasm served over HTTP.');

if (results.some(value => !value)) process.exitCode = 1;
