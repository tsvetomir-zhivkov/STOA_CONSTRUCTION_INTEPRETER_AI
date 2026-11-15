// Copy blueprint-viewer into workspace/viewer/ so a static server rooted at this build serves it at /workspace.
// Re-run after changing blueprint-viewer: npm run sync-viewer
import fs from 'node:fs';
import path from 'node:path';
import { fileURLToPath } from 'node:url';

const here = path.dirname(fileURLToPath(import.meta.url));
const src = path.resolve(here, '../../../../blueprint-viewer');
const dst = path.resolve(here, '../workspace/viewer');
const files = ['index.html', 'blueprintViewer.js', 'ifcGeometry.js', 'psetUnits.js', 'sheets.js', 'vendor'];

if (!fs.existsSync(path.join(src, 'vendor/web-ifc/web-ifc.wasm'))) {
  console.error(`missing ${path.join(src, 'vendor/web-ifc/web-ifc.wasm')}: run npm install in blueprint-viewer first`);
  process.exit(1);
}
fs.rmSync(dst, { recursive: true, force: true });
fs.mkdirSync(dst, { recursive: true });
for (const f of files) fs.cpSync(path.join(src, f), path.join(dst, f), { recursive: true });
console.log(`copied blueprint-viewer (${files.join(', ')}) -> ${path.relative(process.cwd(), dst) || dst}`);
