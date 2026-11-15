// Node 18 compatibility for blueprint-ingest's PDF reader (pdfjs-dist 6 expects Node 20+; blueprint-ingest declares >= 22).
// Imported first by start.mjs; on a current Node both lines are no-ops.
import { createRequire } from 'node:module';

const require = createRequire(import.meta.url);

Promise.withResolvers ??= function withResolvers() {
  let resolve, reject;
  const promise = new this((res, rej) => { resolve = res; reject = rej; });
  return { promise, resolve, reject };
};
process.getBuiltinModule ??= (id) => require(id);
