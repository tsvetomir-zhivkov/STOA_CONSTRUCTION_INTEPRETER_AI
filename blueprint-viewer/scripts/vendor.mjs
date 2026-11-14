import { fileURLToPath } from 'node:url';
// Copy the browser build of web-ifc and its .wasm next to the page: the wasm must be SERVED, not bundled.
import fs from 'node:fs';
import path from 'node:path';
const here = path.dirname(fileURLToPath(import.meta.url));
const src = path.join(here, '..', 'node_modules', 'web-ifc');
const dst = path.join(here, '..', 'vendor', 'web-ifc');
fs.mkdirSync(dst, { recursive: true });
for (const f of ['web-ifc-api.js', 'web-ifc.wasm']) fs.copyFileSync(path.join(src, f), path.join(dst, f));
const v = JSON.parse(fs.readFileSync(path.join(src, 'package.json'), 'utf8')).version;
fs.writeFileSync(path.join(dst, 'VERSION'), `${v}\n`);
console.log(`vendored web-ifc ${v} -> vendor/web-ifc/ (web-ifc-api.js, web-ifc.wasm)`);
// the shared vocabulary (one source of truth with blueprint-ingest)
const voc = path.join(here, '..', '..', 'blueprint-ingest', 'src', 'mappers', 'vocabulary.js');
fs.copyFileSync(voc, path.join(here, '..', 'vendor', 'vocabulary.js'));
console.log('copied blueprint-ingest vocabulary.js -> vendor/vocabulary.js');

for (const name of ['three', 'three-mesh-bvh']) {
  fs.cpSync(path.join(here, '..', 'node_modules', name), path.join(here, '..', 'vendor', name), { recursive: true });
}
