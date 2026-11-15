/**
 * sheets.js — 2D drawings (PNG, JPG, SVG, PDF, DXF) as flat sheets in the 3D scene.
 *
 * These files carry no 3D information, so each is laid flat on the ground plane, like paper on a table, at a nominal or
 * real scale:
 *   - DXF: drawn as lines in its own units ($INSUNITS; metres when the header says nothing), x east, y north.
 *   - PNG / JPG / SVG: one image, 1 px = 1 cm (nominal: an image has no scale).
 *   - PDF: every page rendered with pdf.js, read as 1:100 (nominal), pages side by side.
 * Every sheet is one pickable mesh (userData.sheet), so it can be selected and described like any element.
 */

import * as THREE from 'three';
import { parseDxf, INSUNITS_M } from './vendor/dxfParser.js';

const MAX_TEXTURE = 4096; // longest side of a rendered sheet, in texels (safe on every WebGL device)
const PT_M_AT_1_100 = (0.0254 / 72) * 100; // one PDF point at 1:100, in metres
const PDFJS = 'https://cdn.jsdelivr.net/npm/pdfjs-dist@4.10.38/build/';

export const SHEET_FORMATS = Object.freeze({
  png: 'PNG image', jpg: 'JPG image', jpeg: 'JPG image', svg: 'SVG drawing', pdf: 'PDF drawing', dxf: 'DXF drawing',
});
export const sheetFormatOf = (name) => {
  const ext = String(name).toLowerCase().match(/\.([a-z0-9]+)$/)?.[1];
  return ext && SHEET_FORMATS[ext] ? ext : null;
};

/**
 * Read a 2D drawing into a THREE.Group lying in the XZ plane (y = 0), centred on x = z = 0.
 * @param {File} file
 * @returns {Promise<{ group: THREE.Group, pieces: Array<{ mesh: THREE.Mesh, info: object }>, label: string, note: string, pages: number }>}
 */
export async function loadSheet(file) {
  const fmt = sheetFormatOf(file.name);
  if (fmt === 'dxf') return dxfSheet(await file.text(), file.name);
  if (fmt === 'pdf') return pdfSheets(new Uint8Array(await file.arrayBuffer()), file.name);
  if (fmt) return imageSheet(file, fmt);
  throw new Error(`${file.name}: not a supported drawing format`);
}

/**
 * a flat, pickable sheet of paper (w × h metres) with an optional texture. The ground sits only 12 mm below the model, which
 * a large sheet seen from afar cannot resolve in the depth buffer, so the paper is pulled towards the camera in depth.
 * `drawn: false` keeps it pickable but invisible (a DXF's lines are drawn on their own).
 */
function paper(w, h, map = null, drawn = true) {
  const geo = new THREE.PlaneGeometry(w, h);
  geo.rotateX(-Math.PI / 2); // plane +y (image top) -> -z (north)
  const mat = new THREE.MeshBasicMaterial({ color: 0xffffff, map, side: THREE.DoubleSide, polygonOffset: true, polygonOffsetFactor: -1, polygonOffsetUnits: -2, colorWrite: drawn, depthWrite: drawn });
  const mesh = new THREE.Mesh(geo, mat);
  mesh.receiveShadow = false;
  return mesh;
}

function canvasTexture(canvas) {
  const tex = new THREE.CanvasTexture(canvas);
  tex.colorSpace = THREE.SRGBColorSpace;
  tex.anisotropy = 8;
  return tex;
}

// ---- images ------------------------------------------------------------------------------------------------

async function imageSheet(file, fmt) {
  const url = URL.createObjectURL(file);
  try {
    const img = new Image();
    img.decoding = 'async';
    img.src = url;
    await img.decode().catch(() => { throw new Error(`${file.name}: the image could not be read`); });
    // an SVG without width/height has no intrinsic size: fall back to 1000 px on the long side
    let w = img.naturalWidth || 1000, h = img.naturalHeight || 1000;
    const k = Math.min(1, MAX_TEXTURE / Math.max(w, h));
    const svgUp = fmt === 'svg' ? Math.max(1, Math.min(4, MAX_TEXTURE / Math.max(w, h))) : 1; // vectors: render sharper
    const canvas = document.createElement('canvas');
    canvas.width = Math.max(1, Math.round(w * k * svgUp));
    canvas.height = Math.max(1, Math.round(h * k * svgUp));
    const ctx = canvas.getContext('2d');
    ctx.fillStyle = '#ffffff';
    ctx.fillRect(0, 0, canvas.width, canvas.height);
    ctx.drawImage(img, 0, 0, canvas.width, canvas.height);
    const wM = w * 0.01, hM = h * 0.01; // nominal 1 px = 1 cm
    const mesh = paper(wM, hM, canvasTexture(canvas));
    const group = new THREE.Group();
    group.add(mesh);
    const info = { format: SHEET_FORMATS[fmt], file: file.name, widthM: wM, heightM: hM, pixels: `${w} × ${h}`, scale: 'nominal (1 px = 1 cm)' };
    return { group, pieces: [{ mesh, info }], label: SHEET_FORMATS[fmt], pages: 1, note: 'Shown flat at a nominal scale (1 px = 1 cm): an image has no real-world scale.' };
  } finally {
    URL.revokeObjectURL(url);
  }
}

// ---- PDF ---------------------------------------------------------------------------------------------------

let pdfjsPromise = null;
function pdfjs() {
  pdfjsPromise ??= import(/* @vite-ignore */ `${PDFJS}pdf.min.mjs`).then((m) => {
    m.GlobalWorkerOptions.workerSrc = `${PDFJS}pdf.worker.min.mjs`;
    return m;
  }).catch((err) => { pdfjsPromise = null; throw new Error(`the PDF reader (pdf.js) could not be loaded: ${err.message}`); });
  return pdfjsPromise;
}

/** every page of a PDF rendered to a canvas (white background), with its size in PDF points; also used by /workspace */
export async function renderPdfPages(bytes) {
  const lib = await pdfjs();
  const doc = await lib.getDocument({ data: bytes }).promise;
  try {
    const pages = [];
    for (let n = 1; n <= doc.numPages; n++) {
      const page = await doc.getPage(n);
      const base = page.getViewport({ scale: 1 });
      const scale = Math.min(3, MAX_TEXTURE / Math.max(base.width, base.height));
      const vp = page.getViewport({ scale });
      const canvas = document.createElement('canvas');
      canvas.width = Math.max(1, Math.round(vp.width));
      canvas.height = Math.max(1, Math.round(vp.height));
      const ctx = canvas.getContext('2d');
      ctx.fillStyle = '#ffffff';
      ctx.fillRect(0, 0, canvas.width, canvas.height);
      await page.render({ canvasContext: ctx, viewport: vp }).promise;
      page.cleanup();
      pages.push({ n, canvas, wPt: base.width, hPt: base.height });
    }
    return pages;
  } finally {
    doc.destroy();
  }
}

async function pdfSheets(bytes, name) {
  const pages = (await renderPdfPages(bytes)).map((p) => ({ ...p, wM: p.wPt * PT_M_AT_1_100, hM: p.hPt * PT_M_AT_1_100 }));
  const group = new THREE.Group();
  const pieces = [];
  // pages side by side, left to right, with a small gap; the row is centred on x = 0
  const gap = Math.max(...pages.map((p) => p.wM)) * 0.06;
  const total = pages.reduce((s, p) => s + p.wM, 0) + gap * (pages.length - 1);
  let x = -total / 2;
  for (const p of pages) {
    const mesh = paper(p.wM, p.hM, canvasTexture(p.canvas));
    mesh.position.x = x + p.wM / 2;
    x += p.wM + gap;
    group.add(mesh);
    pieces.push({ mesh, info: { format: 'PDF drawing', file: name, page: `${p.n} of ${pages.length}`, widthM: p.wM, heightM: p.hM, scale: 'nominal (read as 1:100)' } });
  }
  return { group, pieces, label: 'PDF drawing', pages: pages.length, note: 'Every page is shown flat, side by side, read as 1:100 (nominal: the scale printed on the drawing is not read).' };
}

// ---- DXF ---------------------------------------------------------------------------------------------------

const ARC_STEPS = 48;

/** line segments of a parsed DXF (blocks expanded), in drawing units, as consecutive point pairs; also used by /workspace */
export function dxfSegments(dxf) {
  const segs = [];
  const walk = (entities, T, depth) => {
    const P = ([x, y]) => [T.a * x + T.b * y + T.e, T.c * x + T.d * y + T.f];
    const poly = (pts, closed) => { for (let i = 0; i + 1 < pts.length; i++) segs.push(P(pts[i]), P(pts[i + 1])); if (closed && pts.length > 2) segs.push(P(pts[pts.length - 1]), P(pts[0])); };
    for (const e of entities) {
      if ((e.type === 'LINE' || e.type === 'LWPOLYLINE') && e.pts?.length) poly(e.pts, e.closed);
      else if (e.type === 'CIRCLE' || e.type === 'ARC') {
        const a0 = e.type === 'ARC' ? (e.a0 * Math.PI) / 180 : 0;
        let a1 = e.type === 'ARC' ? (e.a1 * Math.PI) / 180 : Math.PI * 2;
        if (a1 <= a0) a1 += Math.PI * 2;
        const n = Math.max(6, Math.ceil((ARC_STEPS * (a1 - a0)) / (Math.PI * 2)));
        const pts = [];
        for (let i = 0; i <= n; i++) { const t = a0 + ((a1 - a0) * i) / n; pts.push([e.c[0] + e.r * Math.cos(t), e.c[1] + e.r * Math.sin(t)]); }
        poly(pts, false);
      } else if (e.type === 'INSERT' && depth < 8) {
        const blk = dxf.blocks.get(e.name);
        if (!blk) continue;
        const r = (e.rot * Math.PI) / 180, cos = Math.cos(r), sin = Math.sin(r);
        // insert: translate(-base) -> scale -> rotate -> translate(at), then the parent transform
        const L = { a: cos * e.sx, b: -sin * e.sy, c: sin * e.sx, d: cos * e.sy };
        const lx = e.at[0] - (L.a * blk.base[0] + L.b * blk.base[1]), ly = e.at[1] - (L.c * blk.base[0] + L.d * blk.base[1]);
        walk(blk.entities, {
          a: T.a * L.a + T.b * L.c, b: T.a * L.b + T.b * L.d, c: T.c * L.a + T.d * L.c, d: T.c * L.b + T.d * L.d,
          e: T.a * lx + T.b * ly + T.e, f: T.c * lx + T.d * ly + T.f,
        }, depth + 1);
      }
    }
  };
  walk(dxf.entities, { a: 1, b: 0, c: 0, d: 1, e: 0, f: 0 }, 0);
  // keep whole segments only (pairs of points), dropping any with a non-finite end
  const out = [];
  for (let i = 0; i + 1 < segs.length; i += 2) if (segs[i].every(Number.isFinite) && segs[i + 1].every(Number.isFinite)) out.push(segs[i], segs[i + 1]);
  return out;
}

function dxfSheet(text, name) {
  const dxf = parseDxf(text);
  const unit = INSUNITS_M[dxf.insUnits] ?? 1;
  const segs = dxfSegments(dxf);
  if (segs.length < 2) throw new Error(`${name}: no drawable lines in this DXF`);
  let x0 = Infinity, y0 = Infinity, x1 = -Infinity, y1 = -Infinity;
  for (const [x, y] of segs) { x0 = Math.min(x0, x); x1 = Math.max(x1, x); y0 = Math.min(y0, y); y1 = Math.max(y1, y); }
  const cx = (x0 + x1) / 2, cy = (y0 + y1) / 2;
  const pos = new Float32Array(segs.length * 3);
  // lines just above the ground, proportional to the drawing so they stay resolvable in depth from afar
  const lift = Math.max(0.004, Math.max(x1 - x0, y1 - y0) * unit * 0.0005);
  // centre in double precision first (survey coordinates can be large), then metres; plan y (north) -> scene -z
  segs.forEach(([x, y], i) => { pos[i * 3] = (x - cx) * unit; pos[i * 3 + 1] = lift; pos[i * 3 + 2] = -(y - cy) * unit; });
  const geo = new THREE.BufferGeometry();
  geo.setAttribute('position', new THREE.BufferAttribute(pos, 3));
  const lines = new THREE.LineSegments(geo, new THREE.LineBasicMaterial({ color: 0x1d232b }));
  lines.raycast = () => {}; // the paper under the lines is what gets picked
  const wM = (x1 - x0) * unit, hM = (y1 - y0) * unit, pad = Math.max(wM, hM) * 0.03;
  const mesh = paper(wM + 2 * pad, hM + 2 * pad, null, false);
  const group = new THREE.Group();
  group.add(mesh, lines);
  const units = dxf.insUnits in INSUNITS_M ? 'from the file' : 'not declared in the file: read as metres';
  const info = { format: 'DXF drawing', file: name, widthM: wM, heightM: hM, layers: dxf.layers.size, scale: `real (units ${units})` };
  return { group, pieces: [{ mesh, info }], label: 'DXF drawing', pages: 1, note: `Lines drawn flat at real size (units ${units}). Text, hatches and splines are not drawn.` };
}

/** free GPU memory of a sheet group */
export function disposeSheet(group) {
  group.traverse((o) => {
    o.geometry?.dispose();
    if (o.material) { o.material.map?.dispose(); o.material.dispose(); }
  });
  group.parent?.remove(group);
}
