import test from 'node:test';
import assert from 'node:assert/strict';
import { FileSession } from '../session.js';

test('removal invalidates late extraction and does not block new imports', async () => {
  const session = new FileSession();
  const sources = [];
  let finish;
  const pending = session.run(async ({ current }) => {
    await new Promise(resolve => { finish = resolve; });
    if (current()) sources.push('old');
    return [{ ok: true }];
  }, () => sources.length);
  await new Promise(resolve => setImmediate(resolve));
  session.invalidate();
  await session.run(async () => { sources.push('new'); return [{ ok: true }]; }, () => sources.length);
  finish();
  assert.deepEqual(await pending, []);
  assert.deepEqual(sources, ['new']);
  assert.equal(session.state, 'READY');
});

test('failed upload can recover; concurrent uploads are serialized', async () => {
  const session = new FileSession();
  await assert.rejects(session.run(async () => { throw Error('bad file'); }, () => false));
  assert.equal(session.state, 'ERROR');
  const order = [];
  await Promise.all([1, 2, 3].map(i => session.run(async ({ processing }) => {
    processing(); order.push(i); return [{ ok: true }];
  }, () => true)));
  assert.deepEqual(order, [1, 2, 3]);
  assert.equal(session.state, 'READY');
});
