import {installLibrary} from './library.js';
import {installAnalysis} from './analysis.js';
import {installInspector} from './inspector.js';
import {showSource} from './sourceView.js';
import {PlanCache} from './plans.js';
import {PlanView} from './planView.js';
import {indexBuilding,renderProjection} from './buildingModel.js';
import {api} from './api.js';
export {api} from './api.js';

const $=id=>document.getElementById(id);
const pause=ms=>new Promise(resolve=>setTimeout(resolve,ms));
export class Workspace {
  constructor(app){
    this.app=app;this.documents=new Map();this.indexes=new Map();this.planCache=new PlanCache();
    this.epoch=0;this.planGeneration=0;this.imports=new Set();this.mode='3d';this.pickByKey=new Map();this.keyByPick=new Map();this.floor=null;this.sourceStamp='';
    this.sourceRecords=new Map();this.layerVisibility=new Map();this.typeVisibility=new Map();
    this.planView=new PlanView($('planSurface'),{onSelect:key=>this.selectPlan(key),onHover:key=>{this.planView.hover(key);this.viewer?.setHovered(this.pickByKey.get(key) ?? null);}});
    for(const button of document.querySelectorAll('[data-mode]'))button.addEventListener('click',()=>this.setMode(button.dataset.mode));
    installInspector(this);installLibrary(this);installAnalysis(this);
    $('cancelImport').onclick=()=>this.clear();
    $('dismissWorkspaceError').onclick=()=>{$('workspaceError').hidden=true;};
    if(matchMedia('(max-width:900px)').matches)document.body.classList.add('sidebar-collapsed');
    $('toggleSidebar').onclick=()=>{document.body.classList.toggle('sidebar-collapsed');requestAnimationFrame(()=>this.viewer?.resize());};
    $('inspectorClose').onclick=()=>{this.viewer?.select(null);$('panel').classList.remove('open');document.body.classList.remove('inspector-open');this.viewer?.resize();};
    const divider=$('viewDivider');
    divider.onpointerdown=e=>{divider.setPointerCapture(e.pointerId);divider.dataset.dragging='true';};
    divider.onpointermove=e=>{if(divider.dataset.dragging!=='true')return;const box=$('viewport').getBoundingClientRect();$('viewport').style.setProperty('--plan-width',`${Math.max(25,Math.min(75,(e.clientX-box.left)/box.width*100))}%`);this.viewer?.resize();};
    divider.onpointerup=()=>{delete divider.dataset.dragging;};
    divider.onkeydown=e=>{if(['ArrowLeft','ArrowRight'].includes(e.key)){e.preventDefault();const value=parseFloat($('viewport').style.getPropertyValue('--plan-width')||'50');$('viewport').style.setProperty('--plan-width',`${Math.max(25,Math.min(75,value+(e.key==='ArrowRight'?5:-5)))}%`);this.viewer?.resize();}};
    $('floorSelect').addEventListener('change',()=>this.setFloor($('floorSelect').value));
    $('planCut').addEventListener('change',()=>this.showFloor());
    $('fitView').addEventListener('click',()=>{this.viewer?.frame();this.planView.fit();});
    $('resetView').addEventListener('click',()=>{if(this.viewer){this.viewer.resetFilters();this.viewer.bp.applyView();this.viewer.frame();this.app.render(this.viewer.summary());}this.planView.fit();});
  }
  get viewer(){return window.viewer;}
  message(text){$('workspaceMessage').textContent=text;}
  reportError(error){
    this.message(error.message);$('workspaceErrorText').textContent=error.message;$('workspaceError').hidden=false;
    if(!this.viewer?.bp.sources.length&&!this.sourceRecords.size&&!this.activeDocument)document.body.classList.remove('viewing');
  }
  setMode(mode){
    this.mode=mode;document.body.dataset.mode=mode;
    for(const b of document.querySelectorAll('[data-mode]'))b.setAttribute('aria-pressed',String(b.dataset.mode===mode));
    requestAnimationFrame(()=>{this.viewer?.resize();this.viewer?.frame();if(this.planView.content)this.planView.fit();});
    if(mode!=='3d' && this.floor)this.showFloor();
  }
  loading(name,phase,previewReady=false){
    $('importProgress').hidden=false;$('importProgress').dataset.previewReady=String(previewReady);
    $('importName').textContent=name;$('importPhase').textContent=phase;
  }
  async waitForExtraction(meta,epoch){
    while(['UPLOADING','PROCESSING'].includes(meta.state)&&epoch===this.epoch){
      await pause(500);if(epoch!==this.epoch)return meta;
      meta=await api(`/api/documents/${meta.id}`);
      if(epoch!==this.epoch)return meta;
      this.documents.set(meta.id,meta);
      if(this.activeDocument?.id===meta.id)this.activeDocument=meta;
    }
    return meta;
  }
  async attachBuildingData(meta,epoch){
    if(meta.state!=='READY'||this.indexes.get(meta.id)?.length)return;
    const buildings=await api(`/api/documents/${meta.id}/buildings`);
    if(epoch!==this.epoch)return;
    const viewer=this.viewer;
    const hasGeometry=viewer.bp.sources.some(s=>s.scope===meta.id&&s.kind==='ifc');
    if(meta.type==='ifc'&&!hasGeometry)return;
    this.indexes.set(meta.id,buildings.map(indexBuilding));
    for(const building of buildings){
      const results=await viewer.addFiles([{name:`${meta.name} · building.json`,documentId:meta.id,text:async()=>JSON.stringify(building)}],{reframe:!hasGeometry});
      if(epoch!==this.epoch)return;
      if(results.some(r=>!r.ok))throw Error(results.filter(r=>!r.ok).map(r=>r.error).join('\n'));
    }
    if(this.activeDocument?.id===meta.id)this.refreshAnalysis?.();
  }
  async finishIfc(meta,epoch){
    this.loading(meta.name,'3D ready. Building data is still processing.',true);
    meta=await this.waitForExtraction(meta,epoch);
    if(epoch!==this.epoch)return;
    await this.attachBuildingData(meta,epoch);
    if(epoch!==this.epoch)return;
    if(this.activeDocument?.id===meta.id){
      this.activeDocument=meta;this.refreshAnalysis?.();
      this.message(meta.state==='ERROR'?`3D source available. Extraction unavailable: ${meta.error}`:`${meta.name} · validated building data`);
    }
  }
  async addFiles(files){
    $('workspaceError').hidden=true;
    const epoch=this.epoch;
    for(const file of files){
      if(epoch!==this.epoch)break;
      this.app.ensureViewer();document.body.classList.add('viewing');
      this.loading(file.name,'Saving drawing…');this.message(`Importing ${file.name}…`);
      let meta;
      try{
        meta=await api('/api/documents',{method:'POST',headers:{'X-Filename':encodeURIComponent(file.name)},body:file});
        if(epoch!==this.epoch){await api(`/api/documents/${meta.id}`,{method:'DELETE'});break;}
        this.imports.add(meta.id);this.documents.set(meta.id,meta);
        if(meta.type==='ifc'){
          // Render the original geometry while the independent extractor prepares facts.
          await this.openDocument(meta,file);
          if(epoch===this.epoch)await this.finishIfc(meta,epoch);
        }else{
          this.loading(file.name,'Preparing drawing data…');
          meta=await this.waitForExtraction(meta,epoch);
          if(epoch!==this.epoch)break;
          await this.openDocument(meta,file);
          if(meta.state==='ERROR')this.message(`Source retained. Extraction unavailable: ${meta.error}`);
        }
      }catch(error){if(epoch===this.epoch)this.reportError(error);}
      finally{if(meta)this.imports.delete(meta.id);if(epoch===this.epoch)$('importProgress').hidden=true;}
    }
    if(epoch===this.epoch)this.refreshLibrary?.().catch(error=>{if(epoch===this.epoch)this.reportError(error);});
  }
  async openDocument(meta,file=null){
    const epoch=this.epoch;this.analysisController?.abort();this.activeDocument=meta;this.documents.set(meta.id,meta);
    $('workspaceError').hidden=true;
    this.sourceInfo=null;this.sourceRecords.clear();this.planView.clear();$('sourcePanel').hidden=true;
    $('chatMessages').replaceChildren();$('planTools').hidden=false;document.querySelector('#planPane iframe')?.remove();$('planSurface').hidden=false;
    const viewer=this.app.ensureViewer();
    const loaded=viewer.bp.sources.some(s=>s.scope===meta.id);
    $('workspaceTitle').textContent=meta.name;
    try{
      if(meta.type==='ifc'&&!loaded){
        this.setMode('3d');this.loading(meta.name,'Building 3D geometry. Large drawings may take a moment.');
        let bytes;
        if(file)bytes=await file.arrayBuffer();
        else{
          const response=await fetch(`/api/documents/${meta.id}/source`);
          if(!response.ok)throw Error('The stored IFC source could not be loaded.');
          bytes=await response.arrayBuffer();
        }
        if(epoch!==this.epoch)return;
        const results=await viewer.addFiles([{name:meta.name,documentId:meta.id,arrayBuffer:async()=>bytes}]);
        if(epoch!==this.epoch)return;
        if(results.some(r=>!r.ok))throw Error(results.filter(r=>!r.ok).map(r=>r.error).join('\n'));
        this.loading(meta.name,'3D ready. Preparing building data…',true);
      }
      if(epoch!==this.epoch)return;
      if(['ifc','json'].includes(meta.type)){
        await this.attachBuildingData(meta,epoch);
        if(epoch!==this.epoch)return;
        this.sceneChanged(viewer.summary());this.setMode(meta.type==='ifc'?'3d':'parallel');
      }else await this.openSource(meta);
      if(epoch!==this.epoch||this.activeDocument?.id!==meta.id)return;
      this.refreshAnalysis?.();document.body.classList.add('viewing');
      this.message(`${meta.name} · ${meta.state==='READY'?'validated building data':'source view'}`);
    }finally{if(epoch===this.epoch)$('importProgress').hidden=true;}
    // Reopening a stored, processing IFC also renders immediately, then attaches facts.
    if(!file&&meta.type==='ifc'&&['UPLOADING','PROCESSING'].includes(meta.state)){
      this.finishIfc(meta,epoch).catch(error=>{if(epoch===this.epoch)this.reportError(error);})
        .finally(()=>{if(epoch===this.epoch)$('importProgress').hidden=true;});
    }
  }
  clear(){
    $('importProgress').hidden=true;
    this.analysisController?.abort();this.epoch++;this.planGeneration++;this.planCache.clear();this.planView.clear();this.floor=null;this.activeDocument=null;
    for(const id of this.imports)api(`/api/documents/${id}`,{method:'DELETE'}).catch(()=>{});
    this.imports.clear();this.indexes.clear();this.sourceInfo=null;this.sourceRecords.clear();this.layerVisibility.clear();this.typeVisibility.clear();this.removedSource?.clear();$('sourcePanel').hidden=true;document.querySelector('#planPane iframe')?.remove();$('planSurface').hidden=false;this.viewer?.clear();this.pickByKey.clear();this.keyByPick.clear();
    $('floorSelect').replaceChildren();$('chatMessages').replaceChildren();$('analysisFacts').replaceChildren();$('analysisPanel').hidden=true;document.body.classList.remove('analysis-open','inspector-open');
    this.message('Ready to open a drawing');
  }
  removeSource(sourceId){
    $('importProgress').hidden=true;
    const source=this.viewer?.bp.sources.find(s=>s.id===sourceId);if(!source)return;
    this.epoch++;this.planGeneration++;this.planCache.clear();
    this.analysisController?.abort();
    for(const id of this.imports)api(`/api/documents/${id}`,{method:'DELETE'}).catch(()=>{});
    this.imports.clear();this.indexes.delete(source.scope);
    if(this.activeDocument?.id===source.scope){this.activeDocument=null;this.refreshAnalysis?.();}
    this.viewer.session.invalidate();
    for(const s of [...this.viewer.bp.sources])if(s.scope===source.scope)this.viewer.bp.remove(s.id,false);
    this.viewer.bp.assemble();
    if(!this.viewer.bp.sources.length)this.viewer.resetFilters();
    this.viewer.afterChange(!this.viewer.bp.sources.length);this.viewer.session.set(this.viewer.bp.sources.length?'READY':'EMPTY');
    this.sceneChanged(this.viewer.summary());
  }
  sceneChanged(summary){
    const viewer=this.viewer;if(!viewer)return;
    viewer.onVisibilityChange=()=>this.syncPlanVisibility();
    viewer.onHover=pick=>this.planView.hover(this.keyByPick.get(pick));
    const stamp=viewer.bp.sources.map(s=>s.id).join(',');
    const changed=stamp!==this.sourceStamp;
    if(changed){this.sourceStamp=stamp;this.planCache.clear();this.planGeneration++;}
    this.pickByKey.clear();this.keyByPick.clear();
    for(const [pick,record] of viewer.bp.pickIndex){
      const source=record.kind==='ifc'?record.source:record.model.source;
      const entity=record.kind==='ifc'?record.el.json:record.meshes[0]?.userData.element;
      const id=entity?.ifc_global_id ?? entity?.global_id ?? record.el?.globalId ?? entity?.id ?? record.el?.expressID;
      const key=`${source.scope ?? source.id}|${id}`;this.pickByKey.set(key,pick);this.keyByPick.set(pick,key);
    }
    this.floors=[];
    for(const source of viewer.bp.sources.filter(s=>s.kind==='ifc'))for(const storey of source.model.storeys){
      const level=[...viewer.bp.levels.values()].find(l=>l.sources.has(source.id)&&l.key.endsWith(`:${storey.globalId ?? storey.expressID}`));
      this.floors.push({key:`${source.id}:${storey.expressID}`,source,storey,levelKey:level?.key,name:storey.name});
    }
    if(!this.floors.length)for(const source of viewer.bp.sources.filter(s=>s.kind==='json'))for(const level of source.doc.levels??[]){
      const sceneLevel=[...viewer.bp.levels.values()].find(l=>l.entityId===level.id && l.sources.has(source.id));
      this.floors.push({key:`${source.id}:${level.id}`,source,level,levelKey:sceneLevel?.key,name:level.name??'Unnamed storey'});
    }
    $('floorSelect').replaceChildren(...this.floors.map(f=>new Option(f.name,f.key)));
    if(!this.floors.some(f=>f.key===this.floor))this.floor=this.floors[0]?.key ?? null;
    $('floorSelect').value=this.floor??'';
    if(changed&&this.floor)this.showFloor();
    else this.syncPlanVisibility();
  }
  async openSource(meta){
    const epoch=this.epoch,current=()=>epoch===this.epoch&&this.activeDocument?.id===meta.id;
    this.sourceRecords.clear();this.layerVisibility.clear();this.typeVisibility.clear();
    this.floor=null;this.planGeneration++;
    const url=`/api/documents/${meta.id}/source`;
    $('planTools').hidden=true;
    document.querySelector('#planPane iframe')?.remove();
    $('planSurface').hidden=false;
    if(meta.type==='pdf'){
      const iframe=document.createElement('iframe');iframe.title=meta.name;iframe.src=url;iframe.style.cssText='width:100%;height:100%;border:0';
      $('planPane').append(iframe);$('planSurface').hidden=true;
    }else if(['png','jpg','jpeg','webp'].includes(meta.type)){
      const image=new Image();image.src=url;await image.decode();if(!current())return;
      const xml=`<svg xmlns="http://www.w3.org/2000/svg"><image width="${image.naturalWidth}" height="${image.naturalHeight}"/></svg>`;
      showSource(this.planView,xml,meta.id);this.planView.svg.querySelector('image').setAttribute('href',url);this.planView.fit();
    }else{
      const response=await fetch(meta.type==='dxf'?`/api/documents/${meta.id}/preview`:url);
      if(!response.ok)throw Error('Source preview unavailable; see extraction error');
      const xml=await response.text();if(!current())return;
      const info=meta.type==='dxf'?await api(`/api/documents/${meta.id}/source-info`):null;if(!current())return;
      const source=showSource(this.planView,xml,meta.id,meta.type);
      this.sourceRecords=source.records;this.layerVisibility=source.layers;
      for(const record of this.sourceRecords.values())this.typeVisibility.set(record.type,true);
      this.sourceInfo=info;
      this.renderSourceFilters();
    }
    this.setMode('2d');
  }
  renderSourceFilters(){
    const draw=(target,map)=>{
      $(target).replaceChildren(...[...map].map(([name,on])=>{
        const label=document.createElement('label');label.className='check';
        const checkbox=Object.assign(document.createElement('input'),{type:'checkbox',checked:on});
        checkbox.onchange=()=>{map.set(name,checkbox.checked);this.filterSource();};label.append(checkbox,name);return label;
      }));
    };
    draw('sourceLayers',this.layerVisibility);draw('sourceTypes',this.typeVisibility);
    $('sourcePanel').hidden=!this.sourceRecords.size;
  }
  filterSource(){for(const [key,record]of this.sourceRecords)this.planView.visible(key,this.layerVisibility.get(record.layer)&&this.typeVisibility.get(record.type)&&!this.removedSource?.has(key));}
  async setFloor(key){
    $('planTools').hidden=false;
    this.floor=key;$('floorSelect').value=key;const floor=this.floors.find(f=>f.key===key);
    if(floor?.levelKey){this.viewer.isolateLevel(floor.levelKey);this.app.render(this.viewer.summary());}
    await this.showFloor();
  }
  async showFloor(){
    const floor=this.floors?.find(f=>f.key===this.floor);if(!floor)return;
    const generation=++this.planGeneration;
    try{
      this.message(`Preparing ${floor.name}…`);
      const scope=floor.source.scope??floor.source.id;
      let plan;
      if(floor.storey){
        plan=await this.planCache.get(floor.source,floor.storey,Number($('planCut').value)||1.2);
        plan={...plan,items:plan.items.map(i=>({...i,key:`${scope}|${i.key}`}))};
      }else plan={items:[]};
      // Normalized room boundaries/labels and source footprints supplement real sections.
      const sources=this.viewer.bp.sources.filter(s=>s.kind==='json'&&s.scope===floor.source.scope);
      for(const source of sources){
        const doc=renderProjection(source.doc._canonical ?? source.doc);
        for(const collection of floor.storey?['rooms']:['rooms','walls','openings','objects'])for(const entity of doc[collection]??[]){
          const level=doc.levels?.find(l=>l.id===entity.level_id);
          const match=floor.storey?(level?.ifc_global_id??level?.global_id)===floor.storey.globalId:entity.level_id===floor.level.id;
          if(!match||!entity.polygon_px?.length)continue;
          const pts=entity.polygon_px.map(p=>[p[0]-(this.viewer.bp.origin?.x??0),-p[1]+(this.viewer.bp.origin?.y??0)]);
          const center=pts.reduce((a,p)=>[a[0]+p[0]/pts.length,a[1]+p[1]/pts.length],[0,0]);
          plan.items.push({key:`${scope}|${entity.ifc_global_id??entity.global_id??entity.id}`,name:entity.name??entity.category??collection,projected:pts.map((p,i)=>[p,pts[(i+1)%pts.length]]),cut:[],native:[],center,label:collection==='rooms'?[entity.number,entity.name].filter(Boolean).join(' · '):null});
        }
      }
      if(generation!==this.planGeneration)return;
      this.planView.show(plan);this.syncPlanVisibility();this.selectionChanged();
      this.message(`${floor.name} · ${floor.storey?'section at '+$('planCut').value+' m':'source footprints'} · ${plan.items.length} represented elements`);
    }catch(error){if(error.name!=='AbortError'&&generation===this.planGeneration)this.message(error.message);}
  }
  syncPlanVisibility(){for(const key of this.planView.nodes.keys()){
    if(this.sourceRecords.has(key))continue;
    const pick=this.pickByKey.get(key);
    this.planView.visible(key,(!pick||this.viewer.bp.isShown(pick))&&!this.removedSource?.has(key));
  }}
  selectPlan(key){const pick=this.pickByKey.get(key);if(pick)this.viewer.select(pick);else{this.planView.select(key);if(this.inspectPlanEntity?.(key)){this.message('Selected component has no matching 3D geometry.');return;}this.inspectSource?.(this.sourceRecords.get(key));this.message(key?'Source geometry selected; semantic classification is not established unless a source identifier matches.':'');}}
  selectionChanged(){
    const pick=this.viewer?.selected,key=this.keyByPick.get(pick);
    const represented=this.planView.select(key);
    if(pick&&!represented&&this.mode!=='3d'){
      const record=this.viewer.bp.pickIndex.get(pick);
      const floor=this.floors?.find(f=>record?.kind==='ifc'?f.source===record.source&&f.storey?.expressID===record.el.storeyId:f.levelKey===record?.model?.level?.key);
      if(floor&&floor.key!==this.floor){this.floor=floor.key;$('floorSelect').value=floor.key;this.showFloor();}
      else this.message('Selected component is not represented on this floor plan.');
    }
    this.inspectSelection?.(pick);
  }
}
