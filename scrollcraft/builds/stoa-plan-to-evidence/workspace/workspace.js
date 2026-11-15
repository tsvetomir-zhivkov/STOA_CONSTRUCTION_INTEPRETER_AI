// /workspace: one file at a time. Before a file: the centered upload card. After: top bar (file name, format, upload),
// the AI chat on the left and the blueprint view on the right. Vector files (.ifc, .dxf, .svg) are converted to JSON by
// blueprint-ingest (npm start: scripts/start.mjs writes <repo>/out/<file>.json) and the parsed JSON is kept as the chat's
// context; other formats are shown but not converted. Questions go to answering_agent on that JSON (POST /api/chat).
// Pipes uploads (.png .jpg .dxf .dxr) go to the pipe API (pipes/web_api.py: POST /analyze, POST /ask) when
// /workspace/config.js sets window.STOA_CONFIG = { apiBase, apiKey }; the answer's cited ids and its overlay are shown.
// An IFC model is read by the existing blueprint
// viewer (mounted in the right column): it renders a plan of every level, and is itself the 3D view behind the toggle.
// File selection and image display must work even if an optional viewer dependency cannot load.
const loadSheetHelpers = () => import('/workspace/viewer/sheets.js');
const sheetFormatOf = name => name.split('.').pop().toLowerCase();

const $ = (id) => document.getElementById(id);
/** /workspace/config.js: on Modal served by the app (same origin), with npm start written by scripts/write-config.mjs */
const CFG = window.STOA_CONFIG;
const API = (CFG?.apiBase ?? '').replace(/\/$/, '');
const auth = () => (CFG?.apiKey ? { 'x-api-key': CFG.apiKey } : {});
const PIPE_API = /\.(png|jpe?g|dxf|dxr)$/i; // what the pipe API analyses
const ACCEPTED = /\.(ifc|dxf|dxr|svg|png|jpe?g|pdf)$/i;
const VIEWER_URL = '/workspace/viewer/';
const PLAN_CUT = 1.2; // a floor plan is a horizontal cut 1.2 m above the level
const PLAN_PX = 1600; // longest side of a rendered level plan

let token = 0; // bumps on every new file: late async work for an older file is dropped
let urls = []; // object URLs of the current file, revoked on reset
let levels = []; // IFC: [{ name, elevation, url }]
let levelIndex = 0;
/**
 * Chat state of the current file. `context` is the parsed JSON blueprint-ingest wrote to `outFile` (out/<file>.json):
 * the building/component structure answering_agent answers from. `history` is sent with every question, in the
 * agent's format, so follow-ups keep their context.
 * A pipes upload analysed by the pipe API has `sheetId` instead (mode 'pipes'): questions go to POST /ask.
 * @type {{ outFile: string|null, context: object|null, history: Array<{ role: 'user'|'assistant', content: string }>, busy: boolean, mode: 'building'|'pipes', sheetId: string|null, overlay: boolean }}
 */
const chat = { outFile: null, context: null, history: [], busy: false, mode: 'building', sheetId: null, overlay: false };

// ---- deterministic source lookup and pixel geometry --------------------------------------------------------
const highlights = { enabled: false, entities: new Map(), ids: [] };
const validPoint = p => Array.isArray(p) && p.length >= 2 && p.slice(0, 2).every(Number.isFinite);
const validRing = (points, minimum = 2) => Array.isArray(points) && points.length >= minimum && points.every(validPoint);

function buildEntityLookup(data) {
  const lookup = new Map();
  for (const collection of ['rooms', 'walls', 'openings', 'objects', 'units', 'texts', 'edges',
    'pipe_segments', 'pipe_fittings', 'pipe_equipment']) {
    for (const entity of Array.isArray(data?.[collection]) ? data[collection] : []) {
      if (entity && typeof entity.id === 'string' && (entity.status ?? 'active') === 'active') lookup.set(entity.id, entity);
    }
  }
  return lookup;
}

function sourceDrawing(data, entity, pageIndex) {
  const drawings = data?.drawing ? [data.drawing] : (Array.isArray(data?.drawings) ? data.drawings : []);
  const page = drawings.filter(d => (d.page_index ?? 0) === pageIndex);
  return page.find(d => d.id && d.id === entity.source_drawing_id)
    ?? page.find(d => d.id && entity.observations?.some(o => o.drawing_id === d.id))
    ?? (!entity.source_drawing_id ? page[0] : null);
}

function sourceShapes(entity, drawingId) {
  const observation = drawingId && entity.observations?.find(o => o.drawing_id === drawingId);
  if (drawingId && entity.source_drawing_id && entity.source_drawing_id !== drawingId && !observation) return [];
  // Never fill missing observation fields with pixels from a different drawing.
  const g = observation || entity;
  if (validRing(g.polygon_px, 3)) return [{ kind: 'area', rings: [g.polygon_px,
    ...(Array.isArray(g.holes_px) ? g.holes_px.filter(h => validRing(h, 3)) : [])] }];
  for (const key of ['centerline_px', 'polyline_px']) {
    if (validRing(g[key])) return [{ kind: 'line', rings: [g[key]] }];
  }
  if (Array.isArray(g.bbox_px) && g.bbox_px.length === 4 && g.bbox_px.every(Number.isFinite)) {
    const [x0, y0, x1, y1] = g.bbox_px;
    if (x1 > x0 && y1 > y0) return [{ kind: 'area', rings: [[[x0,y0], [x1,y0], [x1,y1], [x0,y1]]] }];
    if (x1 >= x0 && y1 >= y0) return [{ kind: x0 === x1 && y0 === y1 ? 'point' : 'line', rings: [[[x0,y0], [x1,y1]]] }];
  }
  if (validPoint(g.center_px)) return [{ kind: 'point', rings: [[g.center_px]] }];
  // Converted pipe objects can retain the original segment/fitting/equipment record here.
  if (g.properties?.pipe_index && typeof g.properties.pipe_index === 'object') return sourceShapes(g.properties.pipe_index, null);
  return [];
}

function inverseAffine(matrix, point) {
  if (!Array.isArray(matrix) || matrix.length < 2 || !matrix.slice(0, 2).every(row =>
    Array.isArray(row) && row.length >= 3 && row.slice(0, 3).every(Number.isFinite))) return null;
  const [[a,b,c], [d,e,f]] = matrix, determinant = a*e - b*d;
  if (Math.abs(determinant) < 1e-12) return null;
  const x = point[0]-c, y = point[1]-f;
  return [(e*x-b*y)/determinant, (a*y-d*x)/determinant];
}

function projectSourcePoint(point, drawing, view) {
  if (view.project) return view.project(point, drawing);
  const image = drawing.image;
  if (!(image?.width_px > 0 && image?.height_px > 0)) return null;
  // Vector extraction uses unrotated PDF points. The viewer's viewport includes crop and rotation.
  const pdfResize = drawing.transforms?.find(t => t.from === 'pdf_points' && t.to === 'page_px' && t.kind === 'resize');
  if (view.pdf && pdfResize) {
    const { bounds, transform } = view.pdf;
    const sx = pdfResize.scale_x, sy = pdfResize.scale_y;
    if (!(sx > 0 && sy > 0)) return null;
    const x = bounds[0]+point[0]/sx, y = bounds[3]-point[1]/sy;
    return [transform[0]*x+transform[2]*y+transform[4], transform[1]*x+transform[3]*y+transform[5]];
  }
  if (view.svgHasViewBox === false) {
    const resize = drawing.transforms?.find(t => t.from === 'svg_user_units' && t.to === 'page_px');
    return resize?.scale_x > 0 && resize?.scale_y > 0 ? [point[0]/resize.scale_x, point[1]/resize.scale_y] : null;
  }
  if (view.svgAspect && !view.svgAspect.includes('none')) {
    const scale = (view.svgAspect.includes('slice') ? Math.max : Math.min)(view.width/image.width_px, view.height/image.height_px);
    const x = view.svgAspect.includes('xMin') ? 0 : view.svgAspect.includes('xMax') ? 1 : .5;
    const y = view.svgAspect.includes('YMin') ? 0 : view.svgAspect.includes('YMax') ? 1 : .5;
    return [point[0]*scale+(view.width-image.width_px*scale)*x, point[1]*scale+(view.height-image.height_px*scale)*y];
  }
  // Raster geometry is already mapped back to original image pixels by the translator.
  // Do not apply original -> model_input preprocessing transforms again.
  return [point[0]*view.width/image.width_px, point[1]*view.height/image.height_px];
}
// ---- end source geometry helpers ---------------------------------------------------------------------------

function clearSourceHighlights() {
  highlights.ids = [];
  document.querySelectorAll('.source-overlay').forEach(svg => svg.replaceChildren());
}

function renderSourceHighlights() {
  if (!highlights.enabled || !chat.context) return;
  for (const sheet of document.querySelectorAll('.drawing-sheet')) {
    const view = sheet.sourceView;
    view.overlay.replaceChildren();
    for (const id of highlights.ids) {
      const entity = highlights.entities.get(id);
      if (!entity) continue;
      const drawing = sourceDrawing(chat.context, entity, view.pageIndex);
      if (!drawing) continue;
      for (const shape of sourceShapes(entity, drawing.id)) {
        const rings = shape.rings.map(ring => ring.map(p => projectSourcePoint(p, drawing, view)));
        if (rings.some(ring => ring.some(p => !validPoint(p)))) continue;
        const node = document.createElementNS('http://www.w3.org/2000/svg', shape.kind === 'point' ? 'circle' : 'path');
        if (shape.kind === 'point') {
          node.setAttribute('cx', rings[0][0][0]);
          node.setAttribute('cy', rings[0][0][1]);
          node.setAttribute('r', Math.max(view.width, view.height)*.006);
        } else {
          node.setAttribute('d', rings.map(ring => 'M'+ring.map(p => p.join(',')).join(' L')+(shape.kind === 'area' ? ' Z' : '')).join(' '));
          node.setAttribute('fill-rule', 'evenodd');
        }
        node.setAttribute('class', shape.kind === 'line' ? 'source-line' : 'source-area');
        node.setAttribute('vector-effect', 'non-scaling-stroke');
        node.dataset.sourceId = id;
        view.overlay.append(node);
      }
    }
  }
}


// ---- file selection (button, top-bar button, drop anywhere) --------------------------------------------------
const input = $('fileInput');
/** which upload button opened the file picker: 'building' (construction blueprints) or 'pipes' (MEP / piping) */
let blueprintType = 'building';
for (const b of document.querySelectorAll('[data-upload]')) b.addEventListener('click', () => { blueprintType = b.dataset.upload; input.click(); });
input.addEventListener('change', () => { const f = input.files[0]; input.value = ''; if (f) open(f, blueprintType); });
let depth = 0;
addEventListener('dragenter', (e) => { if ([...e.dataTransfer.types].includes('Files')) { depth++; document.body.classList.add('dragging'); e.preventDefault(); } });
addEventListener('dragleave', () => { if (--depth <= 0) { depth = 0; document.body.classList.remove('dragging'); } });
addEventListener('dragover', (e) => e.preventDefault());
addEventListener('drop', (e) => { e.preventDefault(); depth = 0; document.body.classList.remove('dragging'); const f = [...e.dataTransfer.files][0]; if (f) open(f, 'building'); });

function open(file, type = 'building') {
  if (!ACCEPTED.test(file.name)) {
    $('error').textContent = `${file.name}: not a supported file. Use .ifc, .dxf, .dxr, .svg, .png, .jpg or .pdf.`;
    return;
  }
  $('error').textContent = '';
  const my = reset();
  const ext = file.name.split('.').pop().toLowerCase();
  highlights.enabled = ext !== 'ifc';
  $('fileName').textContent = file.name;
  $('fileName').title = file.name;
  $('fileFormat').textContent = ext === 'jpeg' ? 'JPG' : ext.toUpperCase();
  $('picker').hidden = true;
  $('workspace').hidden = false;
  startChat(file, type, my);
  if (ext === 'ifc') showIfc(file, my);
  else showDrawing(file, my).catch((err) => { if (my === token) note(`This file could not be shown: ${err.message}`); });
}

/** drop everything tied to the previous file: chat, blueprints, levels, the 3D viewer and the toggle */
function reset() {
  clearSourceHighlights();
  highlights.enabled = false;
  highlights.entities.clear();
  for (const u of urls) URL.revokeObjectURL(u);
  urls = [];
  levels = [];
  levelIndex = 0;
  removeJson(chat.outFile); // the replaced file's out/<file>.json
  chat.outFile = null;
  chat.context = null;
  chat.history = [];
  chat.busy = false;
  chat.mode = 'building';
  chat.sheetId = null;
  chat.overlay = false;
  $('messages').replaceChildren();
  $('question').value = '';
  setChatReady(false);
  $('blueprint').replaceChildren();
  $('viewer3d').replaceChildren(); // removes the viewer iframe and its WebGL context
  $('viewer3d').classList.remove('loading');
  $('levelNav').hidden = true;
  $('toggle3d').hidden = true;
  show3D(false);
  $('viewTitle').textContent = 'Blueprint';
  return ++token;
}

// ---- AI chat: converted JSON as context, answered by answering_agent ----------------------------------------------
function addMessage(who, text) {
  const li = Object.assign(document.createElement('li'), { className: who, textContent: text });
  $('messages').append(li);
  li.scrollIntoView({ block: 'end' });
  return li;
}
function setChatReady(on, placeholder) {
  $('question').disabled = !on;
  $('chatForm').querySelector('button').disabled = !on;
  $('question').placeholder = placeholder ?? (on ? 'Ask about this drawing' : 'The assistant is not available for this file');
}
async function errorOf(res) {
  try { return (await res.json()).error || res.statusText; } catch { return `${res.status} ${res.statusText}`; }
}
/** delete a converted JSON from out/ (the file was replaced, or the page is closed) */
function removeJson(outFile) {
  if (outFile) fetch(`/api/convert?${new URLSearchParams({ file: outFile.replace(/^out\//, '') })}`, { method: 'DELETE', keepalive: true, headers: auth() }).catch(() => {});
}
addEventListener('pagehide', () => { removeJson(chat.outFile); chat.outFile = null; });

/** convert the upload to JSON (the server routes it: vector -> blueprint-ingest, raster -> translator); its JSON becomes the chat context */
async function startChat(file, type, my) {
  if (type === 'pipes' && CFG && PIPE_API.test(file.name)) return startPipes(file, my);
  setChatReady(false, 'Converting the file …');
  const status = addMessage('status', `Converting ${file.name} to JSON …`);
  try {
    const res = await fetch(`/api/convert?${new URLSearchParams({ name: file.name, type })}`, { method: 'POST', body: file, headers: auth() });
    if (my !== token) return;
    if (res.status === 415) {
      const body = await res.json().catch(() => ({}));
      status.textContent = `${file.name} was not converted: ${body.error || 'this format is not supported'}. The AI assistant is not available for this file.`;
      setChatReady(false);
      return;
    }
    if (!res.ok) throw new Error(res.status === 404 || res.status === 405 ? 'the site was not started with npm start (in scrollcraft/builds/stoa-plan-to-evidence)' : await errorOf(res));
    const out = await res.json();
    if (my !== token) { removeJson(out.file); return; }
    chat.outFile = out.file;
    chat.context = out.json;
    if (highlights.enabled) highlights.entities = buildEntityLookup(out.json);
    renderSourceHighlights();
    status.remove();
    const count = (k) => (out.json[k] ?? []).filter((e) => (e.status ?? 'active') === 'active').length;
    const found = [['levels', 'level'], ['rooms', 'room'], ['walls', 'wall'], ['openings', 'opening'], ['objects', 'object']]
      .map(([k, w]) => [count(k), w]).filter(([n]) => n).map(([n, w]) => `${n} ${w}${n === 1 ? '' : 's'}`).join(', ');
    addMessage('assistant', `I converted ${file.name} to JSON (${out.file}${found ? `: ${found}` : ''}) and I'm ready to answer questions about it.`);
    setChatReady(true);
  } catch (err) {
    if (my !== token) return;
    status.className = 'error';
    status.textContent = `${file.name} could not be converted to JSON: ${err.message}`;
    setChatReady(false);
  }
}

/** a pipes sheet (scan or DXF) analysed by the pipe API: POST /analyze -> sheet id, summary, overlay */
async function startPipes(file, my) {
  setChatReady(false, 'Analysing the pipe sheet …');
  const status = addMessage('status', `Analysing ${file.name}: symbols, pipes and text (a new sheet takes about a minute) …`);
  try {
    const form = new FormData();
    form.append('file', file, file.name.replace(/\.dxr$/i, '.dxf')); // .dxr is DXF content
    const res = await fetch(`${API}/analyze`, { method: 'POST', headers: auth(), body: form });
    if (my !== token) return;
    if (!res.ok) throw new Error(await errorOf(res));
    const out = await res.json();
    if (my !== token) return;
    chat.mode = 'pipes';
    chat.sheetId = out.sheet_id;
    status.remove();
    const s = out.summary || {};
    const found = [[s.symbols, 'symbol'], [s.edges, 'pipe'], [s.equipment_nodes, 'equipment node']]
      .filter(([n]) => n).map(([n, w]) => `${n} ${w}${n === 1 ? '' : 's'}`).join(', ');
    addMessage('assistant', `I analysed ${file.name} as a pipe sheet${found ? ` (${found})` : ''}${out.cached ? ', from the cache' : ''}. Ask me about symbols, connections, routes or pipe lengths.`);
    for (const w of out.warnings || []) addMessage('status', `Warning: ${w}`);
    if (s.unsnapped_ends) addMessage('status', `${s.unsnapped_ends} pipe end(s) reach nothing: connections near them may be incomplete.`);
    setChatReady(true);
    showOverlay(await imageUrl(`${API}${out.overlay_url}`), `${file.name}: detected symbols (green) and traced pipes (blue)`, my);
  } catch (err) {
    if (my !== token) return;
    status.className = 'error';
    status.textContent = `${file.name} could not be analysed: ${err.message}`;
    setChatReady(false);
  }
}

/** an image the API serves (it needs the key header, so not a plain <img src>): fetched as a blob */
async function imageUrl(url) {
  const res = await fetch(url, { headers: auth() });
  if (!res.ok) throw new Error(`overlay: ${await errorOf(res)}`);
  const u = URL.createObjectURL(await res.blob());
  urls.push(u);
  return u;
}

function showOverlay(src, caption, my) {
  if (my !== token) return;
  chat.overlay = true; // the plain drawing must not replace it when it finishes later
  const img = Object.assign(document.createElement('img'), { src, alt: caption });
  $('blueprint').replaceChildren(figure(img, caption));
}

/** the agent writes **bold**: rendered as text nodes and <strong>, never as HTML */
const answerNodes = (answer) => String(answer).split(/\*\*(.+?)\*\*/g)
  .map((part, k) => (k % 2 ? Object.assign(document.createElement('strong'), { textContent: part }) : document.createTextNode(part)));

/** POST /ask: answer, the ids it cites (chips), reliability and cost; the overlay goes to the right column */
async function askPipes(question, pending, my) {
  const res = await fetch(`${API}/ask`, { method: 'POST', headers: { 'content-type': 'application/json', ...auth() }, body: JSON.stringify({ sheet_id: chat.sheetId, question }) });
  if (my !== token) return null;
  if (!res.ok) throw new Error(await errorOf(res));
  const out = await res.json();
  if (my !== token) return null;
  pending.className = 'assistant';
  const nodes = answerNodes(out.answer);
  if (out.cited_ids?.length) {
    const ul = Object.assign(document.createElement('ul'), { className: 'cites', title: 'Evidence: highlighted on the drawing' });
    ul.append(...out.cited_ids.map((id) => Object.assign(document.createElement('li'), { textContent: id })));
    nodes.push(ul);
  }
  const meta = [out.reliable === false ? 'not exhaustive: see the warning in the answer' : null,
    out.ungrounded_ids?.length ? `ids not from a tool: ${out.ungrounded_ids.join(', ')}` : null,
    out.cost_usd != null ? `cost $${out.cost_usd.toFixed(4)}` : null].filter(Boolean).join(' · ');
  if (meta) nodes.push(Object.assign(document.createElement('small'), { className: 'meta', textContent: meta }));
  pending.replaceChildren(...nodes);
  if (out.overlay_png_base64) showOverlay(`data:image/png;base64,${out.overlay_png_base64}`, `Evidence for: ${question}`, my);
  return out.answer;
}

$('chatForm').addEventListener('submit', async (e) => {
  e.preventDefault();
  const question = $('question').value.trim();
  if (!question || !(chat.outFile || chat.sheetId) || chat.busy) return;
  const my = token;
  clearSourceHighlights();
  $('question').value = '';
  addMessage('user', question);
  const pending = addMessage('assistant pending', '…');
  chat.busy = true;
  setChatReady(false, 'Waiting for the answer …');
  try {
    if (chat.mode === 'pipes') {
      const answer = await askPipes(question, pending, my);
      if (answer != null) chat.history.push({ role: 'user', content: question }, { role: 'assistant', content: answer });
      return;
    }
    const res = await fetch('/api/chat', { method: 'POST', headers: { 'content-type': 'application/json', ...auth() }, body: JSON.stringify({ file: chat.outFile, question, history: chat.history }) });
    if (my !== token) return;
    if (!res.ok) throw new Error(res.status === 405 ? 'the site was not started with npm start (in scrollcraft/builds/stoa-plan-to-evidence)' : await errorOf(res));
    const result = await res.json();
    if (my !== token) return;
    const answer = String(result.answer ?? '').replace(/^[ \t]*sources?\s*:.*$/gim, '').trim();
    if (highlights.enabled) {
      highlights.ids = Array.isArray(result.source_ids) ? [...new Set(result.source_ids.filter(id => typeof id === 'string'))] : [];
      renderSourceHighlights();
    }
    pending.className = 'assistant';
    // the agent writes **bold**: rendered as text nodes and <strong>, never as HTML
    pending.replaceChildren(...answerNodes(answer));
    chat.history.push({ role: 'user', content: question }, { role: 'assistant', content: answer });
  } catch (err) {
    if (my !== token) return;
    pending.className = 'error';
    pending.textContent = `The assistant could not answer: ${err.message}`;
  } finally {
    if (my === token) { chat.busy = false; setChatReady(!!(chat.outFile || chat.sheetId)); $('question').focus(); }
  }
});

// ---- blueprints of non-IFC files ------------------------------------------------------------------------------
const note = (text) => $('blueprint').replaceChildren(Object.assign(document.createElement('p'), { className: 'note', textContent: text }));

async function showDrawing(file, my) {
  const fmt = /\.dxr$/i.test(file.name) ? 'dxf' : sheetFormatOf(file.name); // .dxr is DXF content
  note(`Reading ${file.name} …`);
  if (fmt === 'pdf') {
    const { renderPdfPages } = await loadSheetHelpers();
    const pages = await renderPdfPages(new Uint8Array(await file.arrayBuffer()));
    if (my !== token || chat.overlay) return;
    $('blueprint').replaceChildren(...pages.map((p) => figure(p.canvas, pages.length > 1 ? `Page ${p.n} of ${pages.length}` : '', { pageIndex: p.n-1, pdf: p.pdf })));
  } else if (fmt === 'dxf') {
    const canvas = await drawDxf(await file.text());
    if (my !== token || chat.overlay) return;
    $('blueprint').replaceChildren(figure(canvas, '', { project: canvas.sourceProjection }));
  } else {
    const url = URL.createObjectURL(file);
    urls.push(url);
    const img = Object.assign(document.createElement('img'), { src: url, alt: `Blueprint of ${file.name}` });
    await img.decode().catch(() => { throw new Error('the image could not be read'); });
    if (my !== token) return;
    const svgRoot = fmt === 'svg' ? new DOMParser().parseFromString(await file.text(), 'image/svg+xml').documentElement : null;
    if (my !== token || chat.overlay) return;
    $('blueprint').replaceChildren(figure(img, '', {
      svgAspect: svgRoot ? svgRoot.getAttribute('preserveAspectRatio') || 'xMidYMid meet' : null,
      svgHasViewBox: svgRoot ? svgRoot.hasAttribute('viewBox') : null,
    }));
  }
}

function figure(el, caption, options = null) {
  const fig = document.createElement('figure');
  if (!options) {
    // IFC level snapshots use the original figure path and never get source overlays.
    fig.append(el);
  } else {
    const sheet = document.createElement('div');
    sheet.className = 'drawing-sheet';
    sheet.title = 'Ctrl + wheel to zoom; Shift + drag to pan; double-click to reset';
    const overlay = document.createElementNS('http://www.w3.org/2000/svg', 'svg');
    overlay.classList.add('source-overlay');
    overlay.setAttribute('aria-label', 'Answer sources');
    const width = el.naturalWidth || el.width, height = el.naturalHeight || el.height;
    overlay.setAttribute('viewBox', `0 0 ${width} ${height}`);
    overlay.setAttribute('preserveAspectRatio', 'none');
    sheet.sourceView = { width, height, overlay, pageIndex: 0, ...options };
    el.draggable = false;
    sheet.append(el, overlay);
    fig.append(sheet);
    // A single container transform keeps the image and SVG aligned through zoom and pan.
    let zoom = 1, x = 0, y = 0, drag = null;
    const move = () => { sheet.style.transform = `translate(${x}px,${y}px) scale(${zoom})`; };
    fig.addEventListener('wheel', e => {
      if (!e.ctrlKey) return;
      e.preventDefault();
      zoom = Math.max(.5, Math.min(8, zoom*Math.exp(-e.deltaY*.002)));
      move();
    }, { passive: false });
    sheet.addEventListener('pointerdown', e => {
      if (!e.shiftKey || e.button !== 0) return;
      e.preventDefault();
      drag = [e.clientX-x, e.clientY-y];
      sheet.setPointerCapture(e.pointerId);
    });
    sheet.addEventListener('pointermove', e => { if (drag) { x = e.clientX-drag[0]; y = e.clientY-drag[1]; move(); } });
    for (const event of ['pointerup', 'pointercancel', 'lostpointercapture']) sheet.addEventListener(event, () => { drag = null; });
    fig.addEventListener('dblclick', () => { zoom = 1; x = y = 0; move(); });
    queueMicrotask(renderSourceHighlights);
  }
  if (caption) fig.append(Object.assign(document.createElement('figcaption'), { textContent: caption }));
  return fig;
}

/** a DXF drawn as black lines on white, with the viewer's DXF reader (blocks expanded) */
async function drawDxf(text) {
  const [{ dxfSegments }, { parseDxf }] = await Promise.all([
    loadSheetHelpers(), import('/workspace/viewer/vendor/dxfParser.js'),
  ]);
  const segs = dxfSegments(parseDxf(text));
  if (segs.length < 2) throw new Error('no drawable lines in this DXF');
  let x0 = Infinity, y0 = Infinity, x1 = -Infinity, y1 = -Infinity;
  for (const [x, y] of segs) { x0 = Math.min(x0, x); x1 = Math.max(x1, x); y0 = Math.min(y0, y); y1 = Math.max(y1, y); }
  const w = x1 - x0 || 1, h = y1 - y0 || 1, pad = 24;
  const k = (PLAN_PX * 1.5 - 2 * pad) / Math.max(w, h);
  const canvas = document.createElement('canvas');
  canvas.width = Math.ceil(w * k + 2 * pad);
  canvas.height = Math.ceil(h * k + 2 * pad);
  const ctx = canvas.getContext('2d');
  ctx.fillStyle = '#ffffff';
  ctx.fillRect(0, 0, canvas.width, canvas.height);
  ctx.strokeStyle = '#1d232b';
  ctx.lineWidth = 1.2;
  ctx.beginPath();
  for (let i = 0; i + 1 < segs.length; i += 2) {
    ctx.moveTo(pad + (segs[i][0] - x0) * k, pad + (y1 - segs[i][1]) * k); // plan y up -> canvas y down
    ctx.lineTo(pad + (segs[i + 1][0] - x0) * k, pad + (y1 - segs[i + 1][1]) * k);
  }
  ctx.stroke();
  canvas.sourceProjection = (point, drawing) => {
    const matrix = drawing.transforms?.find(t => t.kind === 'affine' && t.from === 'model_units' && t.to === 'page_px')?.matrix;
    const model = inverseAffine(matrix, point);
    return model ? [pad+(model[0]-x0)*k, pad+(y1-model[1])*k] : null;
  };
  return canvas;
}

// ---- IFC: level plans from the existing viewer, and the 3D toggle ---------------------------------------------
function showIfc(file, my) {
  note(`Reading ${file.name} and drawing a plan of every level …`);
  $('toggle3d').hidden = false;
  $('viewer3d').classList.add('loading');
  const frame = Object.assign(document.createElement('iframe'), { src: VIEWER_URL, title: `3D model of ${file.name}` });
  frame.addEventListener('load', async () => {
    try {
      // hand the file to the viewer through its own file input, exactly like its "Open files" button
      const fileInput = frame.contentDocument.getElementById('fileInput');
      const dt = new DataTransfer();
      dt.items.add(file);
      fileInput.files = dt.files;
      fileInput.dispatchEvent(new Event('change'));
      const viewer = await loaded(frame, my);
      if (!viewer || my !== token) return;
      levels = renderLevelPlans(viewer);
      if (my !== token) return;
      if (!levels.length) { note('This model has no levels to draw.'); return; }
      const sel = $('levelSelect');
      sel.replaceChildren(...levels.map((lv, i) => new Option(`${lv.name} (${lv.elevation >= 0 ? '+' : ''}${lv.elevation.toFixed(2)} m)`, String(i))));
      $('levelNav').hidden = false;
      showLevel(0);
    } catch (err) {
      if (my === token) note(`The level plans could not be drawn: ${err.message}`);
    } finally {
      if (my === token) $('viewer3d').classList.remove('loading');
    }
  }, { once: true });
  $('viewer3d').replaceChildren(frame);
}

/** wait until the viewer in the frame has the model (or reports why it could not load it) */
async function loaded(frame, my) {
  for (;;) {
    if (my !== token) return null;
    const win = frame.contentWindow, doc = frame.contentDocument;
    const v = win?.viewer;
    if (v && v.bp.sources.length) return v;
    const err = doc?.getElementById('error')?.textContent;
    if (err) throw new Error(err);
    await new Promise((r) => setTimeout(r, 200));
  }
}

/**
 * One plan per level, drawn by the viewer's own renderer: the level isolated, everything above PLAN_CUT clipped, seen
 * straight from above. The viewer's view state (isolated level, cut, separation, pull apart, size) is restored after.
 */
function renderLevelPlans(viewer) {
  const bp = viewer.bp, renderer = viewer.renderer;
  const keep = { isolate: bp.view.isolateLevel, cut: bp.view.cut, sep: bp.view.separation, explode: bp.view.explode, grid: viewer.grid?.visible };
  viewer.select(null);
  viewer.setLevelSeparation(0);
  viewer.setExplode(0);
  if (viewer.grid) viewer.grid.visible = false;
  const Box = bp.bounds().constructor; // the viewer's own three.js classes (it runs in the frame's realm)
  const out = [];
  try {
    for (const lv of [...bp.levels.values()].sort((a, b) => a.rank - b.rank)) {
      viewer.isolateLevel(lv.key);
      const y = bp.levelY(lv);
      viewer.setCut(y + PLAN_CUT);
      lv.group.updateMatrixWorld(true);
      const box = new Box().setFromObject(lv.group);
      if (box.isEmpty()) continue;
      const sx = box.max.x - box.min.x || 1, sz = box.max.z - box.min.z || 1;
      const w = Math.round(sx >= sz ? PLAN_PX : (PLAN_PX * sx) / sz), h = Math.round(sz >= sx ? PLAN_PX : (PLAN_PX * sz) / sx);
      // a narrow perspective camera far above is, for a plan, indistinguishable from an orthographic one
      const cam = viewer.camera.clone();
      cam.fov = 2;
      cam.aspect = w / h;
      const half = (Math.max(sz, sx / cam.aspect) / 2) * 1.06;
      const dist = half / Math.tan((cam.fov * Math.PI) / 360);
      const cx = (box.min.x + box.max.x) / 2, cz = (box.min.z + box.max.z) / 2;
      cam.position.set(cx, y + PLAN_CUT + dist, cz);
      cam.up.set(0, 0, -1); // north up
      cam.lookAt(cx, y, cz);
      cam.near = Math.max(0.1, dist - 10);
      cam.far = dist + PLAN_CUT + (y - box.min.y) + 10;
      cam.updateProjectionMatrix();
      renderer.setSize(w, h, false);
      renderer.render(viewer.scene, cam);
      out.push({ name: lv.name, elevation: lv.elevation, url: renderer.domElement.toDataURL('image/png') });
    }
  } finally {
    viewer.isolateLevel(keep.isolate);
    viewer.setCut(keep.cut);
    viewer.setLevelSeparation(keep.sep);
    viewer.setExplode(keep.explode);
    if (viewer.grid) viewer.grid.visible = keep.grid;
    viewer.resize();
  }
  return out;
}

function showLevel(i) {
  if (!levels.length) return;
  levelIndex = (i + levels.length) % levels.length;
  const lv = levels[levelIndex];
  $('levelSelect').value = String(levelIndex);
  $('viewTitle').textContent = `Level ${levelIndex + 1} of ${levels.length}`;
  const img = Object.assign(document.createElement('img'), { src: lv.url, alt: `Plan of ${lv.name}` });
  $('blueprint').replaceChildren(figure(img, `${lv.name} · plan cut ${PLAN_CUT} m above the floor`));
}
$('prevLevel').addEventListener('click', () => showLevel(levelIndex - 1));
$('nextLevel').addEventListener('click', () => showLevel(levelIndex + 1));
$('levelSelect').addEventListener('change', () => showLevel(Number($('levelSelect').value)));

$('toggle3d').addEventListener('click', () => show3D($('toggle3d').getAttribute('aria-pressed') !== 'true'));
function show3D(on) {
  $('toggle3d').setAttribute('aria-pressed', String(on));
  $('toggle3d').textContent = on ? 'Blueprints' : '3D View';
  $('viewer3d').classList.toggle('shown', on);
  $('blueprint').hidden = on;
  $('levelNav').hidden = on || !levels.length;
  $('viewTitle').textContent = on ? '3D View' : levels.length ? `Level ${levelIndex + 1} of ${levels.length}` : 'Blueprint';
}
