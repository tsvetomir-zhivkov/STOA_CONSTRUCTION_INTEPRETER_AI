import {svgNode} from './planView.js';

export function sanitizeSvg(text){
  const document=new DOMParser().parseFromString(text,'image/svg+xml');
  if(document.querySelector('parsererror') || document.documentElement.localName!=='svg')throw Error('Invalid SVG source');
  for(const node of document.querySelectorAll('script,foreignObject,iframe,object,embed,audio,video,animate,animateTransform,animateMotion,set'))node.remove();
  for(const node of document.querySelectorAll('*')){
    for(const attribute of [...node.attributes]){
      const name=attribute.name.toLowerCase(),value=attribute.value.trim();
      const externalUrl=[...value.matchAll(/url\(\s*(['"]?)(.*?)\1\s*\)/gi)].some(m=>!m[2].trim().startsWith('#'));
      if(name.startsWith('on') || name==='xml:base' || (name.endsWith('href') && !value.startsWith('#') && !/^data:image\/(png|jpeg|webp);/i.test(value)) || externalUrl)node.removeAttribute(attribute.name);
    }
    if(node.localName==='style')node.textContent=node.textContent.replace(/@import[^;]+;?/gi,'').replace(/url\(\s*(['"]?)(.*?)\1\s*\)/gi,(match,quote,url)=>url.trim().startsWith('#')?match:'none');
  }
  return document.documentElement;
}

export function showSource(view,text,scope,format='svg'){
  const source=sanitizeSvg(text);view.svg.replaceChildren();view.nodes.clear();
  view.content=svgNode('g');
  for(const attr of source.attributes)if(!['xmlns','width','height','viewBox','id'].includes(attr.name))view.content.setAttribute(attr.name,attr.value);
  while(source.firstChild)view.content.append(source.firstChild);
  view.svg.append(view.content);
  const records=new Map(),layers=new Map();let sequence=0;
  for(const node of [...view.content.querySelectorAll('path,line,polyline,polygon,circle,ellipse,rect,text,use,image')]){
    if(node.closest('defs,clipPath,mask,pattern,marker,symbol'))continue;
    if(format==='dxf'&&!node.dataset.sourceHandle)continue;
    const originalId=node.dataset.sourceHandle ?? node.id ?? '';
    const owner=node.closest('g[id]');
    const layer=node.dataset.layer ?? node.closest('[data-layer]')?.dataset.layer ?? owner?.getAttribute('inkscape:label') ?? owner?.id ?? 'Ungrouped';
    const key=`${scope}|source:${originalId || ++sequence}`;
    let group=view.nodes.get(key);
    if(!group){group=svgNode('g',{'data-key':key,tabindex:0,role:'button'});group.classList.add('source-element');node.parentNode.insertBefore(group,node);view.nodes.set(key,group);}
    group.append(node);
    const type=node.dataset.sourceType ?? node.localName;
    group.setAttribute('aria-label',`${type} · ${layer}`);
    records.set(key,{key,sourceId:originalId || null,name:`${type} · ${layer}`,type,layer,format,attributes:Object.fromEntries([...node.attributes].map(a=>[a.name,a.value]))});
    if(!layers.has(layer))layers.set(layer,true);
  }
  requestAnimationFrame(()=>view.fit());return {records,layers};
}
