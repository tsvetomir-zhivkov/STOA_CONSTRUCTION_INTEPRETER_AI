import {api} from './workspace.js';
const $=id=>document.getElementById(id);
export function installLibrary(workspace){
  workspace.refreshLibrary=async()=>{
    const docs=await api('/api/documents');$('documentCount').textContent=`${docs.length} stored document${docs.length===1?'':'s'}`;
    $('documentGallery').replaceChildren();
    $('libraryEmpty').hidden=docs.length>0;
    for(const meta of docs){
      workspace.documents.set(meta.id,meta);
      const card=document.createElement('article');card.className='document-card';
      const open=document.createElement('button');open.className='document-open';
      const preview=document.createElement('div');preview.className='document-preview';
      if(meta.has_preview || ['svg','png','jpg','jpeg','webp'].includes(meta.type)){
        const img=new Image();img.loading='lazy';img.alt=`Preview of ${meta.name}`;img.src=`/api/documents/${meta.id}/${['svg','png','jpg','jpeg','webp'].includes(meta.type)?'source':'thumbnail'}`;
        img.onerror=()=>{img.remove();preview.textContent='Preview unavailable';};preview.append(img);
      }else preview.textContent=`${meta.type.toUpperCase()} · ${meta.state==='ERROR'?'Extraction unavailable':'Preview not generated'}`;
      const title=document.createElement('strong');title.textContent=meta.name;
      const detail=document.createElement('span');detail.textContent=[meta.type.toUpperCase(),...(meta.building_names??[]),...(meta.floor_names??[])].filter(Boolean).join(' · ');
      open.append(preview,title,detail);open.onclick=async()=>{
        $('library').hidden=true;
        try{await workspace.openDocument(meta);document.body.classList.add('viewing');}catch(error){workspace.reportError(error);}
      };
      const footer=document.createElement('div');footer.className='document-footer';
      const state=document.createElement('span');state.textContent=meta.state==='READY'?'Ready':meta.state==='ERROR'?'Source saved · analysis unavailable':meta.state.toLowerCase();
      const remove=document.createElement('button');remove.className='ghost small';remove.textContent='Delete';remove.setAttribute('aria-label',`Delete ${meta.name}`);
      remove.onclick=async()=>{
        const source=workspace.viewer?.bp.sources.find(s=>s.scope===meta.id);if(source)workspace.removeSource(source.id);
        if(workspace.activeDocument?.id===meta.id)workspace.clear();
        await api(`/api/documents/${meta.id}`,{method:'DELETE'});workspace.documents.delete(meta.id);await workspace.refreshLibrary();
      };
      footer.append(state,remove);card.append(open,footer);$('documentGallery').append(card);
    }
  };
  for(const id of ['drawingsOpen','startLibrary'])$(id).onclick=async()=>{try{await workspace.refreshLibrary();$('library').hidden=false;}catch(error){workspace.reportError(error);}};
  $('libraryClose').onclick=()=>{$('library').hidden=true;workspace.viewer?.resize();};
  $('libraryUpload').onclick=()=>{$('library').hidden=true;$('fileInput').click();};
}
