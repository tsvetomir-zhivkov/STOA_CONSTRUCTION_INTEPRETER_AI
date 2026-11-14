import {identityOfRecord} from './visibility.js';
const $=id=>document.getElementById(id);
const label=value=>String(value).replace(/_/g,' ').replace(/([a-z])([A-Z])/g,'$1 $2').replace(/^ifc /i,'');
const number=new Intl.NumberFormat(undefined,{maximumFractionDigits:3});
export function formatFact(value,key=''){
  if(value==null)return 'Not available';
  if(typeof value==='boolean')return value?'Yes':'No';
  if(typeof value==='number')return `${number.format(value)}${key.endsWith('_m3')?' m³':key.endsWith('_m2')?' m²':key.endsWith('_m')?' m':''}`;
  return String(value);
}
function row(dl,name,value){const dt=document.createElement('dt'),dd=document.createElement('dd');dt.textContent=name;dd.textContent=value;dl.append(dt,dd);}
function section(title){const details=document.createElement('details'),summary=document.createElement('summary');summary.textContent=title;details.append(summary);return details;}
function tableProperties(parent,object,depth=0){
  if(!object||depth>5)return;
  const dl=document.createElement('dl');
  for(const [key,value]of Object.entries(object)){
    if(value==null||key==='imported_v03')continue;
    if(typeof value==='object'){const details=section(label(key));tableProperties(details,value,depth+1);parent.append(details);}
    else row(dl,label(key),formatFact(value,key));
  }
  if(dl.children.length)parent.append(dl);
}
export function installInspector(workspace){
  const extra=document.createElement('div');extra.id='componentDetails';$('panelRows').after(extra);
  const actions=document.createElement('div');actions.className='inspector-actions';
  const remove=document.createElement('button');remove.id='removeComponent';remove.textContent='Remove component';remove.className='small ghost';
  const restore=document.createElement('button');restore.id='restoreComponents';restore.textContent='Restore removed';restore.className='small ghost';
  actions.append(remove,restore);$('panel').append(actions);
  workspace.removedSource=new Set();
  workspace.entityForPick=pick=>{
    const record=workspace.viewer?.bp.pickIndex.get(pick);if(!record)return null;
    const source=record.kind==='ifc'?record.source:record.model.source;
    const projected=record.kind==='ifc'?record.el.json:record.meshes[0]?.userData.element;
    const indexes=workspace.indexes.get(source.scope)??[];
    for(const index of indexes){
      const id=projected?.id ?? index.byGuid.get(record.el?.globalId);
      const match=index.entities.get(id);if(match)return {...match,index,source,record};
    }
    return null;
  };
  const render=(entity,collection,index,title)=>{
    $('panel').classList.add('open');document.body.classList.add('inspector-open');requestAnimationFrame(()=>workspace.viewer?.resize());$('panelTitle').textContent=entity?.name??entity?.label??title??'Component';
    const dl=$('panelRows');dl.replaceChildren();extra.replaceChildren();
    if(!entity){row(dl,'Component information','No validated semantic record is linked to this source element.');return;}
    const type=index.entities.get(entity.type_id)?.entity;
    const fields=['ifc_class','type_name','description','kind','category','predefined_type','fire_rating','acoustic_rating','is_external','is_load_bearing','operation','hardware_group','length_m','width_m','depth_m','height_m','thickness_m','area_m2','net_side_area_m2','gross_area_m2','net_area_m2','volume_m3','net_volume_m3'];
    row(dl,'Classification',label(entity.ifc_class??entity.category??entity.kind??collection));
    if(type?.name)row(dl,'Type',type.name);
    for(const key of fields)if(entity[key]!=null && !['ifc_class','category','kind'].includes(key))row(dl,label(key).replace(/ m[23]?$/,''),formatFact(entity[key],key));
    for(const[key,value]of Object.entries(entity.dimensions??{}))if(value!=null && entity[key]==null)row(dl,label(key).replace(/ m$/,''),formatFact(value,key));
    const level=index.entities.get(entity.level_id)?.entity;row(dl,'Floor',level?.name??'Unassigned');
    const rooms=[...(entity.room_ids??[]),...(entity.room_id?[entity.room_id]:[])].map(id=>index.entities.get(id)?.entity).filter(Boolean);
    if(rooms.length)row(dl,'Connected rooms',rooms.map(r=>[r.number,r.name].filter(Boolean).join(' ')).join(', '));
    const host=index.entities.get(entity.wall_id??entity.properties?.host_id)?.entity;if(host)row(dl,'Host',host.name??host.ifc_class??'Host component');
    const materials=entity.materials??[];if(materials.length)row(dl,'Materials',materials.map(m=>m.name??'Material name unavailable').join(', '));
    if(entity.material_layers?.length){const details=section('Material layers · source order');entity.material_layers.forEach((layer,i)=>{const p=document.createElement('p');p.textContent=`${i+1}. ${layer.material?.name??'Unnamed material'} · ${formatFact(layer.thickness_m,'thickness_m')}`;details.append(p);});extra.append(details);}
    if(entity.quantities?.length){const details=section('Measurements and provenance'),values=document.createElement('dl');for(const q of entity.quantities)row(values,label(q.name),`${q.value==null?'Not available':number.format(q.value)+' '+q.unit} · ${q.origin}${q.method?' · '+q.method:''}`);details.append(values);extra.append(details);}
    if(entity.classifications?.length){const details=section('Classification systems');tableProperties(details,entity.classifications);extra.append(details);}
    for(const [name,value]of Object.entries({finishes:entity.finishes,glazing:entity.glazing,structural_properties:entity.structural_properties,technical_properties:entity.properties?.psets})){
      if(value&&Object.keys(value).length){const details=section(label(name));tableProperties(details,value);extra.append(details);}
    }
    const technical=section('Source identifiers and technical trace');const pre=document.createElement('pre');pre.textContent=JSON.stringify({id:entity.id,ifc_global_id:entity.ifc_global_id,source_refs:entity.source_refs,properties:entity.properties},null,2);technical.append(pre);extra.append(technical);
  };
  workspace.inspectSelection=pick=>{
    workspace.selectedSource=null;if(!pick)return;
    const match=workspace.entityForPick(pick);
    render(match?.entity,match?.collection,match?.index,match?.record?.el?.name??'Selected component');
  };
  workspace.inspectSource=record=>{
    if(!record)return;
    workspace.viewer?.select(null);workspace.selectedSource=record.key;$('panelCorrect').hidden=true;$('panelConf').hidden=true;
    $('panelSource').textContent=workspace.activeDocument?.name??'';
    let match=null;
    for(const index of workspace.indexes.get(workspace.activeDocument?.id)??[])for(const value of index.entities.values()){
      const sourceId=record.format==='dxf'?value.entity.properties?.dxf_handle:value.entity.properties?.svg_element_id;
      if(sourceId && sourceId===record.sourceId)match={...value,index};
    }
    render(match?.entity,match?.collection,match?.index,record.name);
    if(!match){row($('panelRows'),'Source type',record.type);row($('panelRows'),'Layer / group',record.layer);row($('panelRows'),'Classification','Unclassified source geometry');}
    const technical=section('Source geometry and identifiers'),pre=document.createElement('pre');pre.textContent=JSON.stringify(workspace.sourceInfo?.entities?.[record.sourceId]??record,null,2);technical.append(pre);extra.append(technical);
  };
  remove.onclick=()=>{
    const key=workspace.selectedSource;
    if(key){workspace.removedSource.add(key);workspace.filterSource();workspace.selectedSource=null;}
    else {const record=workspace.viewer?.bp.pickIndex.get(workspace.viewer.selected);if(record){workspace.viewer.bp.removedEntities.add(identityOfRecord(record));workspace.viewer.bp.applyEntityVisibility();workspace.viewer.select(null);workspace.syncPlanVisibility();}}
    $('panel').classList.remove('open');document.body.classList.remove('inspector-open');workspace.viewer?.resize();workspace.message('Component removed from this workspace. Original source retained.');
  };
  restore.onclick=()=>{workspace.removedSource.clear();workspace.filterSource();workspace.viewer?.bp.removedEntities.clear();workspace.viewer?.bp.applyEntityVisibility();workspace.syncPlanVisibility();};
  const globalRestore=restore.cloneNode(true);globalRestore.id='restoreAllComponents';globalRestore.onclick=restore.onclick;$('controls').append(globalRestore);
}
