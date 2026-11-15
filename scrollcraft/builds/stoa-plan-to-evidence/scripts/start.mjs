// npm start: serves this site (/, /workspace, /workspace/viewer/) and converts /workspace uploads to JSON, writing
// <repo>/out/<file>.json in blueprint-ingest's schema v0.3:
//   vector (.ifc .dxf .dxr .svg, vector PDF)  -> blueprint-ingest processBlueprint() (what `node src/index.js` runs)
//   raster (.png .jpg .jpeg, scanned PDF)     -> the raster translator, `python -m translator.upload` (CubiCasa on Modal)
//   building blueprints use both; pipes blueprints use the vector path only (raster pipes are skipped, as before)
//
//   POST   /api/convert?name=<file>&type=building|pipes   body: the file  -> { file, json, route, blueprintType }
//   DELETE /api/convert?file=<name>   delete a JSON this process wrote to out/ (file removed or replaced)
//   POST   /api/chat                  { file, question, history } -> answering_agent.answer_question() on out/<file>
//                                     (Python: <repo>/.venv/bin/python if present, else $PYTHON or python3)
import './node-compat.mjs'; // first: PDF support on Node 18 (see the file)
import { createServer } from 'node:http';
import { createWriteStream, existsSync } from 'node:fs';
import { spawn } from 'node:child_process';
import { mkdir, mkdtemp, readFile, rm, stat } from 'node:fs/promises';
import { tmpdir } from 'node:os';
import { basename, dirname, extname, join, normalize, resolve, sep } from 'node:path';
import { fileURLToPath } from 'node:url';
import { processBlueprint } from '../../../../blueprint-ingest/src/index.js';
import { createLogger } from '../../../../blueprint-ingest/src/util/logger.js';
import { classifyBlueprint } from '../../../../blueprint-ingest/src/classifier/blueprintClassifier.js';

const SITE = resolve(dirname(fileURLToPath(import.meta.url)), '..');
const REPO = resolve(SITE, '../../..');
const OUT = join(REPO, 'out');
const PYTHON = process.env.PYTHON || (existsSync(join(REPO, '.venv/bin/python')) ? join(REPO, '.venv/bin/python') : 'python3');
const PORT = Number(process.env.PORT || 3000);
const VECTOR = new Set(['ifc', 'dxf', 'dxr', 'svg']); // .dxr is DXF content: converted as .dxf
const RASTER = new Set(['png', 'jpg', 'jpeg']);
const BLUEPRINT_TYPES = new Set(['building', 'pipes']);
/** out/ files this process created (the only ones it may delete or chat about) -> their blueprint type */
const written = new Map();

const TYPES = {
  '.html': 'text/html; charset=utf-8', '.js': 'text/javascript; charset=utf-8', '.mjs': 'text/javascript; charset=utf-8',
  '.css': 'text/css; charset=utf-8', '.json': 'application/json; charset=utf-8', '.svg': 'image/svg+xml',
  '.png': 'image/png', '.jpg': 'image/jpeg', '.jpeg': 'image/jpeg', '.wasm': 'application/wasm', '.ico': 'image/x-icon',
};
const send = (res, code, body) => {
  res.writeHead(code, { 'content-type': TYPES['.json'], 'cache-control': 'no-store' });
  res.end(typeof body === 'string' ? body : JSON.stringify(body));
};

/** out/<file>.json, never overwriting a file that is already there (<file>-2.json, -3, …) */
function outPath(name) {
  let p = join(OUT, `${name}.json`);
  for (let n = 2; existsSync(p); n++) p = join(OUT, `${name}-${n}.json`);
  return p;
}

/** run the raster translator (translator/upload.py) on one file; exit code 3 = the Modal side is not set up */
function runTranslator(input, output, name) {
  return new Promise((ok) => {
    const p = spawn(PYTHON, ['-m', 'translator.upload', input, output, '--name', name], { cwd: REPO });
    let err = '';
    p.stderr.on('data', (d) => (err += d));
    p.on('error', (e) => ok({ code: -1, err: `cannot start ${PYTHON}: ${e.message}` }));
    p.on('close', (code) => ok({ code, err }));
  });
}

/** 'vector' (blueprint-ingest), 'raster' (translator) or null (not converted), from the format and, for PDFs, its pages */
async function routeOf(format, input, blueprintType) {
  if (VECTOR.has(format)) return 'vector';
  const raster = blueprintType === 'building' ? 'raster' : null; // pipes: raster is not converted (existing pipeline)
  if (RASTER.has(format)) return raster;
  if (format === 'pdf') {
    // blueprint-ingest's own check: pages with text and vector paths -> vector_extraction; scanned only -> vision_agent
    const c = await classifyBlueprint(input, { logger: createLogger('error') });
    if (c.route === 'vector_extraction' || c.route === 'mixed') return 'vector';
    if (c.route === 'vision_agent') return raster;
    throw new Error(`the PDF could not be read (${c.reason}${c.warnings?.length ? `: ${c.warnings[0]}` : ''})`);
  }
  return null;
}

async function convert(req, res, url) {
  const name = basename(url.searchParams.get('name') || '').replace(/[^\w.\- ()]+/g, '_');
  const format = extname(name).slice(1).toLowerCase();
  const blueprintType = url.searchParams.get('type') || 'building';
  if (!BLUEPRINT_TYPES.has(blueprintType)) { req.resume(); return send(res, 400, { error: 'type must be building or pipes' }); }
  if (!VECTOR.has(format) && !RASTER.has(format) && format !== 'pdf') {
    req.resume();
    return send(res, 415, { error: `.${format || '?'} files are not supported: use .ifc, .dxf, .dxr, .svg, .png, .jpg or .pdf` });
  }
  await mkdir(OUT, { recursive: true });
  const tmp = await mkdtemp(join(tmpdir(), 'stoa-upload-'));
  const input = join(tmp, format === 'dxr' ? `${name.slice(0, -4)}.dxf` : name);
  const output = outPath(name);
  try {
    await new Promise((ok, fail) => req.pipe(createWriteStream(input)).on('finish', ok).on('error', fail));
    const route = await routeOf(format, input, blueprintType);
    if (!route) return send(res, 415, { skipped: true, error: `${format === 'pdf' ? 'scanned (non-vector) PDFs' : `.${format} files`} are not converted for pipes blueprints yet` });
    if (route === 'vector') {
      const result = await processBlueprint({ inputFilePath: input, outputJsonPath: output, mode: 'create', logger: createLogger('error'), file: name, source: 'workspace' });
      if (!result.outputs?.length || !existsSync(output)) throw new Error('the converter produced no drawing for this file');
    } else {
      const r = await runTranslator(input, output, name);
      if (r.code !== 0) {
        const line = r.err.trim().split('\n').filter(Boolean).pop() || 'the raster translator failed';
        if (r.code !== 3) console.error(`[translator] ${r.err.trim().split('\n').slice(-5).join('\n')}`);
        return send(res, r.code === 3 ? 503 : 422, { error: line.replace(/^\w+: /, '') });
      }
    }
    written.set(output, blueprintType);
    const text = await readFile(output, 'utf8');
    return send(res, 201, `{"file":${JSON.stringify(`out/${basename(output)}`)},"route":"${route}","blueprintType":"${blueprintType}","json":${text}}`);
  } catch (err) {
    return send(res, 422, { error: err?.message || String(err) });
  } finally {
    if (existsSync(output) && !written.has(output)) await rm(output, { force: true });
    await rm(tmp, { recursive: true, force: true });
  }
}

async function remove(res, url) {
  const p = join(OUT, basename(url.searchParams.get('file') || ''));
  if (!written.has(p)) return send(res, 404, { error: 'not a file converted by this session' });
  await rm(p, { force: true });
  written.delete(p);
  return send(res, 200, { removed: `out/${basename(p)}` });
}

/** run answering_agent on a converted JSON (scripts/agent_bridge.py); one process per question */
function runAgent(document, payload) {
  return new Promise((ok) => {
    const p = spawn(PYTHON, [join(SITE, 'scripts/agent_bridge.py'), document], { cwd: REPO });
    let out = '', err = '';
    p.stdout.on('data', (d) => (out += d));
    p.stderr.on('data', (d) => (err += d));
    p.on('error', (e) => ok({ code: -1, out, err: `cannot start ${PYTHON}: ${e.message}` }));
    p.on('close', (code) => ok({ code, out, err }));
    p.stdin.end(JSON.stringify(payload));
  });
}

async function chat(req, res) {
  let body = '';
  for await (const chunk of req) body += chunk;
  let msg;
  try { msg = JSON.parse(body || '{}'); } catch { return send(res, 400, { error: 'invalid JSON body' }); }
  const question = String(msg.question ?? '').trim();
  if (!question) return send(res, 400, { error: 'question is required' });
  const document = join(OUT, basename(String(msg.file ?? '')));
  if (!written.has(document)) return send(res, 404, { error: 'no converted JSON for this chat: upload the file again' });
  const history = (Array.isArray(msg.history) ? msg.history : [])
    .filter((t) => t && (t.role === 'user' || t.role === 'assistant') && typeof t.content === 'string').slice(-20);
  const r = await runAgent(document, { question, history, blueprint_type: written.get(document) });
  if (r.code === 0) {
    try { return send(res, 200, JSON.parse(r.out)); } catch { return send(res, 502, { error: 'the agent returned no answer' }); }
  }
  const lines = r.err.trim().split('\n').filter(Boolean);
  const missing = lines.map((l) => l.match(/ModuleNotFoundError: No module named '([^']+)'/)?.[1]).find(Boolean);
  const error = missing
    ? `Python dependency "${missing}" is not installed for ${PYTHON}. From the repo root: python3 -m venv .venv && .venv/bin/pip install google-genai python-dotenv shapely`
    : lines.pop() || 'the agent failed';
  console.error(`[agent] ${r.err.trim().split('\n').slice(-5).join('\n')}`);
  return send(res, 502, { error });
}

async function serveStatic(req, res, url) {
  const rel = normalize(decodeURIComponent(url.pathname)).replace(/^([/\\]|\.\.)+/, '');
  let file = join(SITE, rel);
  if (!file.startsWith(SITE) || file.split(sep).includes('node_modules')) return send(res, 404, { error: 'not found' });
  try {
    if ((await stat(file)).isDirectory()) file = join(file, 'index.html');
  } catch {
    file = `${file}.html`; // clean URLs, as `serve` does
  }
  try {
    const data = await readFile(file);
    res.writeHead(200, { 'content-type': TYPES[extname(file).toLowerCase()] || 'application/octet-stream' });
    res.end(req.method === 'HEAD' ? undefined : data);
  } catch { send(res, 404, { error: 'not found' }); }
}

createServer(async (req, res) => {
  const url = new URL(req.url, 'http://localhost');
  try {
    if (url.pathname === '/api/convert' && req.method === 'POST') return await convert(req, res, url);
    if (url.pathname === '/api/convert' && req.method === 'DELETE') return await remove(res, url);
    if (url.pathname === '/api/chat' && req.method === 'POST') return await chat(req, res);
    if (url.pathname.startsWith('/api/')) return send(res, 404, { error: 'unknown endpoint' });
    if (req.method !== 'GET' && req.method !== 'HEAD') return send(res, 405, { error: 'method not allowed' });
    return await serveStatic(req, res, url);
  } catch (err) {
    console.error(err);
    if (!res.headersSent) send(res, 500, { error: String(err?.message || err) });
  }
}).listen(PORT, () => console.log(`Stoa site on http://localhost:${PORT}  (/workspace converts .ifc/.dxf/.svg into ${OUT})`));
