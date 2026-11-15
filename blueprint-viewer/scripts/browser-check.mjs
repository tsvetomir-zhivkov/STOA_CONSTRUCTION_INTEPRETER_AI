/** Real Chrome + running workspace API. No mocks for extraction or rendering.
 * python blueprint-viewer/scripts/workspace-fixtures.py
 * npm --prefix blueprint-viewer start -- --port 4601
 * npm --prefix blueprint-viewer run test:browser -- http://127.0.0.1:4601
 */
import assert from 'node:assert/strict';
import fs from 'node:fs/promises';
import path from 'node:path';
import {fileURLToPath} from 'node:url';
import {chromium} from 'playwright-core';
import {writeFixtures} from '../../blueprint-ingest/test/helpers/fixtures.js';
const root=path.resolve(path.dirname(fileURLToPath(import.meta.url)),'../..');
const qa=path.join(root,'.workspace-data/qa'),folder=path.join(qa,'fixtures');
await fs.mkdir(folder,{recursive:true});
const fixtures=await writeFixtures(path.join(folder,'vectors'));
const files={ifc:path.join(folder,'building.ifc'),dxf:path.join(folder,'plan.dxf'),svg:fixtures.svg};
const base=process.argv[2]??'http://127.0.0.1:4601';
const browser=await chromium.launch({channel:process.env.BROWSER_CHANNEL??'chrome',headless:true});
const page=await browser.newPage({viewport:{width:1500,height:950}});
const errors=[],owned=new Set(),results=[];
page.on('pageerror',e=>errors.push(e.message));
page.on('response',async response=>{if(response.url()===`${base}/api/documents`&&response.request().method()==='POST'&&response.status()===202){try{owned.add((await response.json()).id);}catch{}}});
const nativeOnly=process.argv.includes('--native-only');
async function check(name,run){if(nativeOnly&&!name.startsWith('native IFC'))return;const start=Date.now();await run();results.push({name,ms:Date.now()-start});console.log(`PASS ${name}`);}
async function clear(){await page.evaluate(()=>workspace.clear());await page.waitForFunction(()=>!window.viewer||!viewer.bp.sources.length&&viewer.session.state==='EMPTY');}
async function upload(kind){
  const before=await page.evaluate(()=>workspace.activeDocument?.id);
  await page.locator('#fileInput').setInputFiles(files[kind]);
  await page.waitForFunction(({before,kind})=>workspace.activeDocument?.id!==before&&workspace.activeDocument?.type===kind&&(['ifc','json'].includes(kind)?viewer.bp.sources.filter(s=>s.scope===workspace.activeDocument.id).length>=(kind==='ifc'?2:1)&&viewer.session.state==='READY':workspace.sourceRecords.size>0),{before,kind},{timeout:90000});
  await page.waitForFunction(()=>!workspace.imports.size);
  owned.add(await page.evaluate(()=>workspace.activeDocument.id));
  assert.equal(await page.evaluate(()=>document.body.classList.contains('viewing')),true);
}
try{
  await page.goto(base);
  await check('IFC import, real meshes and every storey plan',async()=>{
    await upload('ifc');
    assert.equal(await page.evaluate(()=>workspace.floors.length),2);
    await page.locator('[data-mode=parallel]').click();
    await page.evaluate(async()=>{for(const floor of workspace.floors)await workspace.setFloor(floor.key);await workspace.setFloor(workspace.floors[0].key);});
    assert.equal(await page.evaluate(()=>workspace.planCache.entries.size),2);
    assert.ok(await page.evaluate(()=>viewer.bp.sources[0].model.stats.triangles>0));
    await page.screenshot({path:path.join(qa,'parallel-final.png')});
  });
  await check('floor/category combinations preserve geometry and plan cache',async()=>{
    assert.ok(await page.evaluate(()=>{
      const bp=viewer.bp,source=bp.sources.find(s=>s.kind==='ifc'),geometries=source.meshes.map(m=>m.geometry),keys=[...workspace.planCache.entries.values()];
      const record=[...bp.pickIndex].find(([,r])=>r.kind==='ifc'&&r.el.ifcType.toUpperCase().includes('WALL'));if(!record)throw Error('Fixture wall missing');
      viewer.setCategoryVisible('wall',false);const hidden=!bp.isShown(record[0]);viewer.setCategoryVisible('wall',true);
      viewer.setLevelVisible(workspace.floors[0].levelKey,false);bp.view.isolateLevel=null;bp.applyView();const floorHidden=!bp.isShown(record[0]);viewer.setLevelVisible(workspace.floors[0].levelKey,true);
      return hidden&&floorHidden&&bp.isShown(record[0])&&geometries.every((g,i)=>g===source.meshes[i].geometry)&&keys.every(p=>[...workspace.planCache.entries.values()].includes(p));
    }));
  });
  await check('selection in both views, inspection, explode and component removal',async()=>{
    const key=await page.evaluate(()=>[...workspace.planView.nodes.keys()].find(k=>workspace.pickByKey.has(k)));
    await page.evaluate(key=>workspace.selectPlan(key),key);
    assert.ok(await page.evaluate(()=>viewer.selected));
    assert.ok(await page.locator('#panelRows').textContent());
    await page.evaluate(()=>{const pick=viewer.selected;viewer.select(null);viewer.select(pick);viewer.setExplode(.6);viewer.setLevelSeparation(.4);});
    assert.equal(await page.evaluate(()=>workspace.planView.nodes.get(workspace.keyByPick.get(viewer.selected)).classList.contains('selected')),true);
    await page.locator('#removeComponent').click();
    assert.equal(await page.evaluate(()=>viewer.bp.removedEntities.size),1);
    await page.locator('#restoreAllComponents').click();
    assert.equal(await page.evaluate(()=>viewer.bp.removedEntities.size),0);
    await page.evaluate(()=>{viewer.setExplode(0);viewer.setLevelSeparation(0);viewer.frame();});
  });
  await check('3D orbit, pan and zoom',async()=>{
    await page.locator('[data-mode="3d"]').click();
    await page.evaluate(()=>new Promise(resolve=>requestAnimationFrame(()=>requestAnimationFrame(resolve))));
    const before=await page.evaluate(()=>viewer.camera.position.toArray());
    const box=await page.locator('#stage').boundingBox();
    assert.ok(box.height<950,'Renderer must stay within its visible viewport');
    await page.mouse.move(box.x+box.width/2,box.y+box.height/2);await page.mouse.down();await page.mouse.move(box.x+box.width/2+70,box.y+box.height/2+35,{steps:8});await page.mouse.up();await page.mouse.wheel(0,160);
    await page.waitForTimeout(150);assert.notDeepEqual(await page.evaluate(()=>viewer.camera.position.toArray()),before);
  });
  for(const [from,to]of [['ifc','ifc'],['ifc','dxf'],['dxf','svg'],['svg','ifc']])await check(`${from} remove then ${to} without refresh`,async()=>{await clear();await upload(to);});
  await check('same IFC twice keeps document scopes distinct',async()=>{
    const old=await page.evaluate(()=>workspace.activeDocument.id);await upload('ifc');
    assert.notEqual(await page.evaluate(()=>workspace.activeDocument.id),old);
    assert.equal(await page.evaluate(()=>workspace.floors.length),4);
    await page.evaluate(()=>workspace.removeSource(viewer.bp.sources[0].id));
    assert.equal(await page.evaluate(()=>workspace.floors.length),2);
  });
  for(const kind of ['dxf','svg'])await check(`${kind}: native vectors, layers, picking, pan and zoom`,async()=>{
    await clear();await upload(kind);
    assert.equal(await page.evaluate(()=>workspace.mode),'2d');
    assert.ok(await page.evaluate(()=>workspace.layerVisibility.size>0));
    await page.evaluate(()=>{const [key,record]=workspace.sourceRecords.entries().next().value;workspace.selectPlan(key);workspace.layerVisibility.set(record.layer,false);workspace.filterSource();if(workspace.planView.nodes.get(key).style.display!=='none')throw Error('Layer was not hidden');workspace.layerVisibility.set(record.layer,true);workspace.filterSource();});
    assert.match(await page.locator('#panelRows').textContent(),/Classification/);
    await page.evaluate(()=>new Promise(resolve=>requestAnimationFrame(()=>requestAnimationFrame(resolve))));
    const before=await page.evaluate(()=>workspace.planView.box);
    await page.locator('.plan-canvas').hover({position:{x:180,y:180}});
    await page.mouse.wheel(0,100);
    await page.waitForFunction(before=>JSON.stringify(workspace.planView.box)!==JSON.stringify(before),before);
    const box=await page.locator('.plan-canvas').boundingBox();
    await page.mouse.move(box.x+180,box.y+180);await page.mouse.down();await page.mouse.move(box.x+220,box.y+210,{steps:8});await page.mouse.up();
    assert.notDeepEqual(await page.evaluate(()=>workspace.planView.box),before);
    await page.locator('#removeComponent').click();assert.equal(await page.evaluate(()=>workspace.removedSource.size),1);await page.locator('#restoreAllComponents').click();
  });
  await check('source SVG isolation preserves local definitions and strips active content',async()=>{
    assert.ok(await page.evaluate(async()=>{const {sanitizeSvg}=await import('./sourceView.js');const svg=sanitizeSvg(`<svg xmlns="http://www.w3.org/2000/svg"><script>alert(1)</script><defs><pattern id="p"/></defs><path fill="url('#p')" onclick="alert(1)"/><image href="https://example.com/x"/></svg>`);return !svg.querySelector('script')&&!svg.querySelector('[onclick]')&&svg.querySelector('path').getAttribute('fill')==="url('#p')"&&!svg.querySelector('image').hasAttribute('href');}));
  });
  await check('clear during extraction cancels backend and rejects late publication',async()=>{
    await clear();await page.locator('#fileInput').setInputFiles(files.ifc);
    await page.waitForFunction(()=>workspace.imports.size>0);
    const id=await page.evaluate(()=>[...workspace.imports][0]);owned.add(id);
    await clear();await page.waitForTimeout(700);
    assert.equal((await page.request.get(`${base}/api/documents/${id}`)).status(),404);
    assert.equal(await page.evaluate(()=>viewer.bp.sources.length),0);
    await upload('ifc');
  });
  await check('extraction error followed by a valid upload recovers',async()=>{
    await clear();await page.locator('#fileInput').setInputFiles({name:'invalid.json',mimeType:'application/json',buffer:Buffer.from('{invalid')});
    await page.waitForFunction(()=>workspace.activeDocument?.state==='ERROR',{},{timeout:90000});await upload('ifc');
  });
  await check('gallery opens stored documents; analysis facts navigate to components',async()=>{
    await page.locator('#drawingsOpen').click();assert.ok(await page.locator('.document-card').count());await page.locator('#libraryClose').click();
    const meta=await page.evaluate(()=>workspace.activeDocument);await clear();await page.evaluate(meta=>workspace.openDocument(meta),meta);
    await page.locator('#analysisOpen').click();assert.match(await page.locator('#analysisFacts').textContent(),/Extracted information/);
    assert.ok(await page.evaluate(()=>{const index=workspace.indexes.get(workspace.activeDocument.id)[0];const wall=[...index.entities.values()].find(x=>x.collection==='walls');workspace.locateEntity(wall.entity.id,index);return !!viewer.selected;}));
    await page.locator('#analysisClose').click();
  });
  await check('resource counts return to baseline after repeated removal',async()=>{
    const counts=[];for(let i=0;i<3;i++){await clear();await upload('ifc');await clear();counts.push(await page.evaluate(()=>{viewer.renderer.render(viewer.scene,viewer.camera);return {geometries:viewer.renderer.info.memory.geometries,textures:viewer.renderer.info.memory.textures,picks:viewer.bp.pickIndex.size,plans:workspace.planCache.entries.size,workers:workspace.planCache.workers.size};}));}
    assert.deepEqual(counts[2],counts[0]);assert.equal(counts[2].picks,0);assert.equal(counts[2].workers,0);results.push({resources:counts});
  });
  await check('large existing normalized sample: in-place visibility',async()=>{
    files.json=path.join(root,'blueprint-ingest/out/samples-v0.2/ifc_models/bsc-esplanades_1807-ep-ar-v18.json');await upload('json');
    const stats=await page.evaluate(()=>{const bp=viewer.bp,before=bp.pickables.map(m=>m.geometry),t=performance.now();viewer.setGroupVisible('walls',false);viewer.setGroupVisible('walls',true);return {entities:bp.pickIndex.size,meshes:bp.pickables.length,toggleMs:performance.now()-t,preserved:before.every((g,i)=>g===bp.pickables[i].geometry)};});
    assert.ok(stats.preserved);assert.ok(stats.entities>1000);results.push({performance:stats});
    await page.screenshot({path:path.join(qa,'large-model-final.png')});
  });
  await check('mobile mode switches and fit without horizontal page overflow',async()=>{
    await page.setViewportSize({width:390,height:844});await page.evaluate(()=>document.body.classList.add('sidebar-collapsed'));await page.locator('[data-mode="2d"]').click();await page.locator('#fitView').click();await page.waitForTimeout(100);
    assert.ok(await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth));await page.screenshot({path:path.join(qa,'mobile-final.png')});
  });
  await check('native IFC door plan without a 3D solid remains selectable and inspectable',async()=>{
    await clear();const original=files.ifc;files.ifc=path.join(folder,'native-plan.ifc');await upload('ifc');files.ifc=original;
    const result=await page.evaluate(async()=>{
      await workspace.setFloor(workspace.floors[0].key);workspace.setMode('parallel');
      const source=viewer.bp.sources.find(s=>s.kind==='ifc'),door=source.model.planOnly.find(e=>e.ifcType==='IFCDOOR');
      if(!door)throw Error('Native-only product was lost');
      const key=source.scope+'|'+door.globalId;
      if(!workspace.planView.nodes.has(key))throw Error('Native-only product absent from plan');
      workspace.selectPlan(key);return {segments:door.nativePlan.length,title:document.getElementById('panelTitle').textContent};
    });
    assert.ok(result.segments>20);assert.equal(result.title,'D1');
  });
  assert.deepEqual(errors,[]);console.log('All browser checks passed.');
}finally{
  await page.screenshot({path:path.join(qa,'browser-last.png')}).catch(()=>{});
  await fs.writeFile(path.join(qa,nativeOnly?'browser-native-results.json':'browser-results.json'),JSON.stringify({results,errors},null,2));
  for(const id of owned)await page.request.delete(`${base}/api/documents/${id}`).catch(()=>{});
  await browser.close();
}
