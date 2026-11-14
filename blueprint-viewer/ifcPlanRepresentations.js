import * as THREE from './vendor/three/build/three.module.js';
const value=x=>x && typeof x==='object' && 'value' in x ? x.value : x;
const coords=x=>(x ?? []).map(v=>Number(value(v)));
function axis(api,id,ref){
  const a=api.GetLine(id,value(ref));
  if(!a)return new THREE.Matrix4();
  const loc=api.GetLine(id,value(a.Location));
  const p=coords(loc?.Coordinates);while(p.length<3)p.push(0);
  const z=a.Axis ? coords(api.GetLine(id,value(a.Axis))?.DirectionRatios) : [0,0,1];
  const x=a.RefDirection ? coords(api.GetLine(id,value(a.RefDirection))?.DirectionRatios) : [1,0,0];
  while(x.length<3)x.push(0);
  const Z=new THREE.Vector3(...z).normalize(),X=new THREE.Vector3(...x).normalize();
  const Y=new THREE.Vector3().crossVectors(Z,X).normalize();X.crossVectors(Y,Z).normalize();
  return new THREE.Matrix4().makeBasis(X,Y,Z).setPosition(...p);
}
export function placementMatrix(api,id,ref,seen=new Set()){
  if(!ref)return new THREE.Matrix4();
  const key=value(ref);if(seen.has(key))throw Error('Cyclic IFC placement');seen.add(key);
  const p=api.GetLine(id,key);
  if(!p?.RelativePlacement)return new THREE.Matrix4();
  return placementMatrix(api,id,p.PlacementRelTo,seen).multiply(axis(api,id,p.RelativePlacement));
}

/** Read native FootPrint/Plan curves only. Unsupported representations stay unclaimed. */
export function nativePlan(api,id,product,lenM,origin){
  if(!product.Representation || !origin)return [];
  const shape=api.GetLine(id,value(product.Representation));
  const matrix=placementMatrix(api,id,product.ObjectPlacement);
  const output=[];
  const world=point=>{
    const p=new THREE.Vector3(point[0]??0,point[1]??0,point[2]??0).applyMatrix4(matrix).multiplyScalar(lenM);
    return [p.x-origin.x,-p.y+origin.y];
  };
  const line=points=>{for(let i=1;i<points.length;i++)output.push([world(points[i-1]),world(points[i])]);};
  const point=ref=>coords(api.GetLine(id,value(ref))?.Coordinates);
  const walk=(ref,depth=0)=>{
    if(depth>32)return;
    const item=api.GetLine(id,value(ref));if(!item)return;
    const type=api.GetNameFromTypeCode(item.type);
    if(type==='IFCPOLYLINE')return line(item.Points.map(point));
    if(type==='IFCINDEXEDPOLYCURVE'){
      const pts=api.GetLine(id,value(item.Points))?.CoordList?.map(coords) ?? [];
      if(!item.Segments?.length)return line(pts);
      for(const seg of item.Segments){
        const indices=coords(value(seg));
        // Three-point arcs need their exact arc representation, never a straight chord.
        if(String(seg.name ?? '').toUpperCase().includes('ARC'))continue;
        line(indices.map(i=>pts[i-1]).filter(Boolean));
      }
      return;
    }
    if(type==='IFCCOMPOSITECURVE'){for(const segment of item.Segments ?? []){const s=api.GetLine(id,value(segment));if(s?.ParentCurve)walk(s.ParentCurve,depth+1);}return;}
    if(type==='IFCGEOMETRICCURVESET' || type==='IFCGEOMETRICSET'){for(const e of item.Elements ?? [])walk(e,depth+1);return;}
    if(type==='IFCTRIMMEDCURVE' || type==='IFCCIRCLE'){
      const circle=type==='IFCCIRCLE'?item:api.GetLine(id,value(item.BasisCurve));
      if(api.GetNameFromTypeCode(circle?.type)!=='IFCCIRCLE')return;
      const start=type==='IFCCIRCLE'?0:Number(value(item.Trim1?.[0]));
      const end=type==='IFCCIRCLE'?2*Math.PI:Number(value(item.Trim2?.[0]));
      if(!Number.isFinite(start)||!Number.isFinite(end))return;
      const local=axis(api,id,circle.Position),r=Number(value(circle.Radius));
      let sweep=end-start;const sense=value(item.SenseAgreement)!==false;
      if(type!=='IFCCIRCLE' && sense && sweep<0)sweep+=2*Math.PI;
      if(type!=='IFCCIRCLE' && !sense && sweep>0)sweep-=2*Math.PI;
      const n=Math.max(8,Math.ceil(Math.abs(sweep)*24));
      line(Array.from({length:n+1},(_,i)=>new THREE.Vector3(r*Math.cos(start+sweep*i/n),r*Math.sin(start+sweep*i/n),0).applyMatrix4(local).toArray()));
    }
  };
  for(const ref of shape.Representations ?? []){
    const representation=api.GetLine(id,value(ref));
    if(!/^(footprint|plan)$/i.test(String(value(representation.RepresentationIdentifier))))continue;
    for(const item of representation.Items ?? [])walk(item);
  }
  return output;
}
