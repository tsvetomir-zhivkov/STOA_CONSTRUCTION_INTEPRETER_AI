import test from 'node:test';
import assert from 'node:assert/strict';
import {formatFact} from '../inspector.js';
test('unknown and zero remain distinct, measurements carry units',()=>{
  assert.equal(formatFact(null,'width_m'),'Not available');
  assert.equal(formatFact(0,'width_m'),'0 m');
  assert.equal(formatFact(false),'No');
  assert.equal(formatFact(12,'area_m2'),'12 m²');
});
