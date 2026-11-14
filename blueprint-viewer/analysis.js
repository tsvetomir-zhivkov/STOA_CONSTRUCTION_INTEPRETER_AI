import {api} from './workspace.js';
const $=id=>document.getElementById(id);
function text(tag,value,cls=''){const node=document.createElement(tag);node.textContent=value;node.className=cls;return node;}
export function installAnalysis(workspace){
  workspace.locateEntity=(id,index)=>{
    const entity=index.entities.get(id)?.entity;if(!entity)return;
    const key=`${workspace.activeDocument.id}|${entity.ifc_global_id??id}`;
    const pick=workspace.pickByKey.get(key);
    if(pick){workspace.viewer.select(pick);workspace.planView.select(key);}
    else workspace.message('This entity has no reliably linked visible representation.');
  };
  workspace.refreshAnalysis=()=>{
    const indexes=workspace.indexes.get(workspace.activeDocument?.id)??[];
    const select=$('analysisBuilding'),previous=select.value;
    select.replaceChildren(...indexes.map((index,i)=>new Option(index.building.project.building_name??index.building.project.name??`Building ${i+1}`,String(i))));
    if([...select.options].some(o=>o.value===previous))select.value=previous;
    workspace.renderAnalysisFacts();
  };
  workspace.renderAnalysisFacts=()=>{
    const index=(workspace.indexes.get(workspace.activeDocument?.id)??[])[Number($('analysisBuilding').value)||0];
    const target=$('analysisFacts');target.replaceChildren();
    if(!index){target.append(text('p','No validated building data is available for this document. You can still review the original source.'));return;}
    const summary=document.createElement('dl');
    for(const collection of ['levels','rooms','walls','openings','objects']){summary.append(text('dt',collection),text('dd',String(index.building[collection].filter(e=>e.status!=='superseded').length)));}
    target.append(text('h3','Extracted information'),summary,text('h3','Coverage and uncertainty'));
    for(const item of index.building.coverage){
      const card=text('article','',`finding ${item.status==='analysed'?'covered':'uncertain'}`);
      card.append(text('strong',item.category),text('span',`${item.status.replaceAll('_',' ')} · ${Math.round(item.confidence*100)}% confidence`));
      if(item.note)card.append(text('p',item.note));target.append(card);
    }
    const flagged=[...index.entities.values()].filter(v=>v.entity.flags?.length&&v.entity.status!=='superseded');
    if(flagged.length)target.append(text('h3','Components to review'));
    for(const {entity}of flagged){
      const button=text('button',`${entity.name??entity.category??entity.ifc_class??'Component'} · ${entity.flags.map(f=>f.replaceAll('_',' ')).join(', ')}`,'finding uncertain');
      button.onclick=()=>workspace.locateEntity(entity.id,index);target.append(button);
    }
  };
  $('analysisBuilding').onchange=()=>workspace.renderAnalysisFacts();
  $('analysisOpen').onclick=()=>{
    const on=document.body.classList.toggle('analysis-open');$('analysisPanel').hidden=!on;
    if(on){workspace.refreshAnalysis();workspace.setMode(workspace.activeDocument?.type==='ifc'?'parallel':'2d');}
    requestAnimationFrame(()=>workspace.viewer?.resize());
  };
  $('analysisClose').onclick=()=>{document.body.classList.remove('analysis-open');$('analysisPanel').hidden=true;workspace.viewer?.resize();};
  $('askForm').onsubmit=async event=>{
    event.preventDefault();const question=$('question').value.trim(),meta=workspace.activeDocument;if(!question||!meta)return;
    const epoch=workspace.epoch,building=Number($('analysisBuilding').value)||0;
    workspace.analysisController?.abort();workspace.analysisController=new AbortController();$('askButton').disabled=true;
    const chat=$('chatMessages');chat.append(text('p',question,'chat-question'));const response=text('article','Analysing the selected building…','chat-answer');chat.append(response);$('question').value='';
    try{
      const result=await api(`/api/documents/${meta.id}/analysis`,{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({question,building}),signal:workspace.analysisController.signal});
      if(epoch!==workspace.epoch||workspace.activeDocument?.id!==meta.id)return;
      response.textContent=result.answer;
      const index=workspace.indexes.get(meta.id)?.[building];
      if(index)for(const [id,{entity}]of index.entities)if(result.answer.includes(id)){
        const button=text('button',`Locate ${entity.name??entity.category??entity.ifc_class??'component'}`,'ghost small');button.onclick=()=>workspace.locateEntity(id,index);response.append(button);
      }
    }catch(error){if(error.name!=='AbortError')response.textContent=`Analysis unavailable: ${error.message}`;}
    finally{$('askButton').disabled=false;}
  };
}
