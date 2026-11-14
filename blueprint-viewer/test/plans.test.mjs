import test from 'node:test';
import assert from 'node:assert/strict';
import {sectionTriangle,generatePlan} from '../plans.js';
test('section uses actual triangle intersections, not a footprint',()=>{
  assert.deepEqual(sectionTriangle([[0,0,0],[4,0,0],[0,4,2]],2),[[2,1],[0,1]]);
  assert.equal(sectionTriangle([[0,0,0],[4,0,0],[0,1,2]],2),null);
});
test('each storey uses its own elevation; non-intersected objects are distinguished',()=>{
  const data={storeys:[{expressID:2,elevation:3}],storeyId:2,elevation:3,cutHeight:1,
    elements:[{expressID:9,storeyId:2,bbox:{min:[0,0,0],max:[4,4,2]},ranges:[{key:'a',start:0,count:3}]}],
    batches:[{key:'a',position:new Float32Array([0,0,0,4,0,0,0,4,2]),index:new Uint32Array([0,1,2])}]};
  const plan=generatePlan(data);assert.equal(plan.items[0].intersects,true);
  assert.equal(generatePlan({...data,cutHeight:5}).items[0].intersects,false);
});
