const NS='http://www.w3.org/2000/svg';
export const svgNode=(name,attrs={})=>{const node=document.createElementNS(NS,name);for(const [k,v] of Object.entries(attrs))node.setAttribute(k,String(v));return node;};
export class PlanView {
  constructor(container,{onSelect=()=>{},onHover=()=>{}}={}){
    this.container=container;this.onSelect=onSelect;this.onHover=onHover;
    this.svg=svgNode('svg',{role:'img','aria-label':'Interactive floor plan',tabindex:0});
    this.svg.classList.add('plan-canvas');
    const host=document.createElement('div');host.style.cssText='width:100%;height:100%';container.append(host);
    const shadow=host.attachShadow({mode:'open'});const css=document.createElement('link');css.rel='stylesheet';css.href=new URL('./workspace.css',import.meta.url).href;shadow.append(css,this.svg);
    this.nodes=new Map();
    this.svg.addEventListener('wheel',e=>{e.preventDefault();if(!this.box)return;const r=this.svg.getBoundingClientRect(),factor=Math.exp(Math.max(-1,Math.min(1,e.deltaY*.001)));const px=(e.clientX-r.left)/r.width,py=(e.clientY-r.top)/r.height;const [x,y,w,h]=this.box;this.setBox([x+w*px*(1-factor),y+h*py*(1-factor),w*factor,h*factor]);},{passive:false});
    this.svg.addEventListener('pointerdown',e=>{this.drag={x:e.clientX,y:e.clientY,box:this.box?.slice(),moved:false,key:e.target.closest('[data-key]')?.dataset.key ?? null};this.svg.setPointerCapture(e.pointerId);});
    this.svg.addEventListener('pointermove',e=>{if(this.drag?.box){const dx=e.clientX-this.drag.x,dy=e.clientY-this.drag.y;this.drag.moved ||= Math.hypot(dx,dy)>4;if(this.drag.moved){const r=this.svg.getBoundingClientRect();const [x,y,w,h]=this.drag.box;this.setBox([x-dx*w/r.width,y-dy*h/r.height,w,h]);}}else this.onHover(e.target.closest('[data-key]')?.dataset.key ?? null);});
    this.svg.addEventListener('pointerup',e=>{if(this.drag&&!this.drag.moved)this.onSelect(this.drag.key);this.drag=null;});
    this.svg.addEventListener('keydown',e=>{if(e.key==='Home'){this.fit();e.preventDefault();}if(e.key==='Enter')this.onSelect(e.target.closest('[data-key]')?.dataset.key ?? null);});
  }
  setBox(box){this.box=box;this.svg.setAttribute('viewBox',box.join(' '));}
  fit(){if(!this.content)return;const b=this.content.getBBox();if(!b.width&&!b.height)return;const pad=Math.max(b.width,b.height)*.06 || 1;this.setBox([b.x-pad,b.y-pad,b.width+pad*2,b.height+pad*2]);}
  show(plan){
    this.svg.replaceChildren();this.nodes.clear();this.content=svgNode('g');this.svg.append(this.content);
    for(const item of plan.items){
      const g=svgNode('g',{'data-key':item.key,tabindex:0,role:'button','aria-label':item.name ?? 'Drawing element'});
      const title=svgNode('title');title.textContent=item.name ?? item.key;g.append(title);
      for(const [kind,segments] of [['projected',item.projected],['cut',item.cut],['native',item.native]]){
        if(!segments?.length)continue;
        const d=segments.map(([a,b])=>`M${a[0]},${a[1]}L${b[0]},${b[1]}`).join('');
        g.append(svgNode('path',{d,class:`plan-line ${kind}`,fill:'none','vector-effect':'non-scaling-stroke'}));
        g.append(svgNode('path',{d,class:'plan-hit',fill:'none',stroke:'transparent','stroke-width':10,'vector-effect':'non-scaling-stroke'}));
      }
      if(item.label && item.center){const t=svgNode('text',{x:item.center[0],y:item.center[1],class:'room-label','font-size':item.labelSize ?? .25,'text-anchor':'middle'});t.textContent=item.label;g.append(t);}
      this.content.append(g);this.nodes.set(item.key,g);
    }
    requestAnimationFrame(()=>this.fit());
  }
  select(key){for(const [id,node]of this.nodes)node.classList.toggle('selected',id===key);return this.nodes.has(key);}
  hover(key){for(const[id,node]of this.nodes)node.classList.toggle('hovered',id===key);}
  visible(key,on){const node=this.nodes.get(key);if(node)node.style.display=on?'':'none';}
  clear(){this.svg.replaceChildren();this.nodes.clear();this.content=null;this.box=null;}
}
