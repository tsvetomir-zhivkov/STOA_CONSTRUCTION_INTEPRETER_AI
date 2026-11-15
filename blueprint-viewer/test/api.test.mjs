import {test} from 'node:test';
import assert from 'node:assert/strict';
import {api} from '../api.js';

test('static preview HTML produces actionable service error for failed uploads',async t=>{
  for(const status of [200,404,501]){
    t.mock.method(globalThis,'fetch',async()=>new Response('<html>Not found</html>',{status,headers:{'Content-Type':'text/html'}}));
    await assert.rejects(api('/api/documents',{method:'POST'}),/Stop the static preview server.*npm start/);
    t.mock.restoreAll();
  }
});
test('JSON service errors retain their actual cause',async t=>{
  t.mock.method(globalThis,'fetch',async()=>Response.json({error:'Unsupported document format'},{status:400}));
  await assert.rejects(api('/api/documents'),/Unsupported document format/);
});
test('valid document list and upload options reach the caller unchanged',async t=>{
  const options={method:'POST',body:'IFC payload'};
  t.mock.method(globalThis,'fetch',async(path,received)=>{
    assert.equal(path,'/api/documents');assert.equal(received,options);
    return Response.json({id:'document',state:'UPLOADING'},{status:202});
  });
  assert.deepEqual(await api('/api/documents',options),{id:'document',state:'UPLOADING'});
});
test('stopped service is explained and intentional aborts remain aborts',async t=>{
  t.mock.method(globalThis,'fetch',async()=>{throw new TypeError('Failed to fetch');});
  await assert.rejects(api('/api/documents'),/Check that npm start is still running/);
  t.mock.restoreAll();
  t.mock.method(globalThis,'fetch',async()=>{throw new DOMException('Cancelled','AbortError');});
  await assert.rejects(api('/api/documents'),{name:'AbortError'});
});
