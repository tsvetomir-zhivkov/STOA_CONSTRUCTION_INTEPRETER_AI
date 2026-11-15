/** Pure triangle sections in the unchanged model frame (Y up); also runs in a worker. */
const EPS=1e-7;
const pointKey=p=>p.map(n=>Math.round(n*1e6)).join(',');
const edgeKey=(a,b)=>[pointKey(a),pointKey(b)].sort().join('|');
export function sectionTriangle(vertices,height) {
  const points=[];
  const add=p=>{if(!points.some(q=>Math.hypot(p[0]-q[0],p[1]-q[1])<EPS))points.push(p);};
  for(let i=0;i<3;i++) {
    const a=vertices[i],b=vertices[(i+1)%3],da=a[1]-height,db=b[1]-height;
    if(Math.abs(da)<EPS)add([a[0],a[2]]);
    if(da*db<0) {const t=da/(da-db);add([a[0]+t*(b[0]-a[0]),a[2]+t*(b[2]-a[2])]);}
  }
  return points.length===2 ? points : null;
}
export function generatePlan({elements,batches,storeys,storeyId,elevation,cutHeight=1.2}) {
  const batchMap=new Map(batches.map(b=>[b.key,b]));
  const elevations=new Map(storeys.map(s=>[s.expressID,s.elevation ?? 0]));
  const cut=elevation+cutHeight, items=[];
  for(const e of elements) {
    const base=elevations.get(e.storeyId) ?? 0;
    const assigned=e.storeyId===storeyId;
    if(!assigned && !(e.bbox.min[1]+base<cut && e.bbox.max[1]+base>elevation+EPS))continue;
    const cuts=new Map(),edges=new Map(),triangles=new Set();
    for(const range of e.ranges) {
      const b=batchMap.get(range.key); if(!b)continue;
      const P=b.position,I=b.index;
      for(let i=range.start;i<range.start+range.count;i+=3) {
        const v=[0,1,2].map(j=>{const k=I[i+j]*3;return [P[k],P[k+1]+base,P[k+2]];});
        const segment=sectionTriangle(v,cut);
        if(segment)cuts.set(edgeKey(...segment),segment);
        if(v.every(p=>p[1]>cut+EPS) || v.every(p=>p[1]<elevation-EPS))continue;
        const xy=v.map(p=>[p[0],p[2]]);
        const area=(xy[1][0]-xy[0][0])*(xy[2][1]-xy[0][1])-(xy[2][0]-xy[0][0])*(xy[1][1]-xy[0][1]);
        if(Math.abs(area)<EPS)continue;
        const tkey=xy.map(pointKey).sort().join('|');if(triangles.has(tkey))continue;triangles.add(tkey);
        for(let j=0;j<3;j++) {const pair=[xy[j],xy[(j+1)%3]],key=edgeKey(...pair);const edge=edges.get(key);if(edge)edge.count++;else edges.set(key,{pair,count:1});}
      }
    }
    const native=e.nativePlan ?? [];
    const projected=[...edges.values()].filter(e=>e.count===1).map(e=>e.pair);
    items.push({key:e.globalId ?? String(e.expressID),expressID:e.expressID,name:e.name ?? e.ifcType,category:e.ifcType?.replace(/^IFC/,'').toLowerCase(),cut:[...cuts.values()],projected,native,intersects:cuts.size>0,assigned});
  }
  return {storeyId,elevation,cutHeight,items};
}

export class PlanCache {
  constructor(){this.entries=new Map();this.workers=new Set();}
  clear(){for(const w of this.workers)w.cancel();this.workers.clear();this.entries.clear();}
  get(source,storey,cutHeight=1.2){
    const key=`${source.id}:${storey.expressID}:${cutHeight}`;
    if(this.entries.has(key))return this.entries.get(key);
    const model=source.model;
    const data={elements:model.elements.map(e=>({globalId:e.globalId,expressID:e.expressID,name:e.name,ifcType:e.ifcType,storeyId:e.storeyId,bbox:e.bbox,ranges:e.ranges,nativePlan:e.nativePlan})),batches:model.batches.map(b=>({key:b.key,position:source.meshes.find(m=>m.userData.ifc.batch.key===b.key)?.userData.explodeBase ?? b.position,index:source.meshes.find(m=>m.userData.ifc.batch.key===b.key)?.userData.originalIndex ?? b.index})),storeys:model.storeys,storeyId:storey.expressID,elevation:storey.elevation ?? 0,cutHeight};
    data.elements.push(...(model.planOnly??[]));
    const promise=new Promise((resolve,reject)=>{
      const worker=new Worker(new URL('./plan.worker.js',import.meta.url),{type:'module'});
      const handle={cancel:()=>{worker.terminate();reject(new DOMException('Plan cancelled','AbortError'));}};
      this.workers.add(handle);
      const done=()=>{worker.terminate();this.workers.delete(handle);};
      worker.onmessage=event=>{done();event.data.error?reject(Error(event.data.error)):resolve(event.data);};
      worker.onerror=event=>{done();reject(Error(event.message));};
      worker.postMessage(data);
    }).catch(error=>{this.entries.delete(key);throw error;});
    this.entries.set(key,promise);return promise;
  }
}
