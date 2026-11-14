import {IfcAPI} from './vendor/web-ifc/web-ifc-api.js';
import * as W from './vendor/web-ifc/web-ifc-api.js';
import * as THREE from './vendor/three/build/three.module.js';
import {MeshBVH} from './vendor/three-mesh-bvh/build/index.module.js';
import {loadIfcModel,captureWebIfcLog} from './ifcGeometry.js';
self.onmessage=async({data})=>{
  const api=new IfcAPI();let model=null;
  try{
    api.SetWasmPath(new URL('./vendor/web-ifc/',import.meta.url).href,true);
    const restore=captureWebIfcLog();try{await api.Init(undefined,true);}finally{restore();}
    model=loadIfcModel(api,W,new Uint8Array(data.bytes),{origin:data.origin});
    for(const batch of model.batches){
      const geometry=new THREE.BufferGeometry();
      geometry.setAttribute('position',new THREE.BufferAttribute(batch.position,3));
      geometry.setIndex(new THREE.BufferAttribute(batch.index,1));
      batch.bvh=MeshBVH.serialize(new MeshBVH(geometry,{indirect:true}));
      geometry.dispose();
    }
    const buffers=new Set();
    function collect(v){if(ArrayBuffer.isView(v))buffers.add(v.buffer);else if(v instanceof ArrayBuffer)buffers.add(v);else if(v&&typeof v==='object')Object.values(v).forEach(collect);}
    collect(model);self.postMessage({model},[...buffers]);
  }catch(error){self.postMessage({error:error.message});}
  finally{if(model)api.CloseModel(model.modelID);api.Dispose?.();}
};
