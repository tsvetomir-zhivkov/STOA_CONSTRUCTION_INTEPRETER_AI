import test from 'node:test';
import assert from 'node:assert/strict';
import {setRangeVisibility} from '../visibility.js';
test('component masking preserves geometry, triangle IDs and restores original indices',()=>{
  const array=new Uint32Array([0,1,2,3,4,5]);
  const mesh={geometry:{index:{array}},userData:{}};
  setRangeVisibility(mesh,[{start:3,count:3}],false);
  assert.deepEqual([...array],[0,1,2,3,3,3]);
  setRangeVisibility(mesh,[{start:3,count:3}],true);
  assert.deepEqual([...array],[0,1,2,3,4,5]);
  assert.equal(mesh.geometry.index.array,array);
});
