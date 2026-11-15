// npm run config: write workspace/config.js for local use (npm start), from env vars. Not committed (.gitignore).
//   STOA_PIPES_API_BASE  the pipe API, e.g. https://elreverso07--stoa-pipes-api.us-east.modal.run
//   STOA_PIPES_API_KEY   its X-API-Key (whatever is written here is readable by anyone who opens the page)
// On Modal the app serves /workspace/config.js itself (same origin, key from the Modal Secret stoa-pipes-api).
import fs from 'node:fs';
import path from 'node:path';
import { fileURLToPath } from 'node:url';

const base = process.env.STOA_PIPES_API_BASE || '';
const key = process.env.STOA_PIPES_API_KEY || '';
if (!base || !key) {
  console.error('set STOA_PIPES_API_BASE and STOA_PIPES_API_KEY first');
  process.exit(1);
}
const out = path.resolve(path.dirname(fileURLToPath(import.meta.url)), '../workspace/config.js');
fs.writeFileSync(out, `window.STOA_CONFIG = ${JSON.stringify({ apiBase: base, apiKey: key })};
`);
console.log(`wrote ${out}`);
