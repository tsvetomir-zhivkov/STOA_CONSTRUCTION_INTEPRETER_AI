/**
 * Minimal, dependency-free ASCII DXF reader (enough for floor plans).
 * Reads: HEADER($INSUNITS), BLOCKS, ENTITIES with LINE, LWPOLYLINE, POLYLINE/VERTEX, CIRCLE, ARC, TEXT, MTEXT, INSERT, DIMENSION.
 * Unsupported entities (HATCH, SPLINE, 3DFACE, ...) are counted and ignored. Bulges in polylines are ignored (counted).
 * If the optional `dxf-parser` package is installed it is NOT required: this parser has no dependencies by design.
 */

/** @typedef {{ type:string, layer:string, [k:string]: any }} DxfEntity */

/** DXF $INSUNITS code -> metres per unit */
export const INSUNITS_M = { 1: 0.0254, 2: 0.3048, 4: 0.001, 5: 0.01, 6: 1, 7: 1000, 8: 2.54e-8, 9: 2.54e-5, 10: 0.9144, 13: 1e-6, 14: 0.1, 15: 10, 16: 100 };

/**
 * @param {string} text
 * @returns {{ insUnits:number|null, entities:DxfEntity[], blocks:Map<string,{ base:[number,number], entities:DxfEntity[] }>, layers:Set<string>, skipped:Record<string,number> }}
 */
export function parseDxf(text) {
  const lines = text.split(/\r\n|\n|\r/);
  const pairs = [];
  for (let i = 0; i + 1 < lines.length; i += 2) {
    const code = Number.parseInt(lines[i].trim(), 10);
    if (Number.isNaN(code)) continue;
    pairs.push([code, lines[i + 1].trim()]);
  }
  const skipped = {};
  const layers = new Set();
  let insUnits = null;
  const entities = [];
  const blocks = new Map();
  let section = null, curBlock = null, pendingPoly = null, hdrVar = null;
  /** @type {Array<[number,string]>|null} */
  let cur = null;

  const flush = () => {
    if (!cur) return;
    const type = cur[0][1].toUpperCase();
    const ent = decodeEntity(type, cur, skipped);
    cur = null;
    if (!ent) return;
    if (ent.type === 'POLYLINE') { pendingPoly = ent; return; }
    if (ent.type === 'VERTEX') { if (pendingPoly) pendingPoly.pts.push(ent.pt); return; }
    if (ent.type === 'SEQEND') { if (pendingPoly) { (curBlock ? curBlock.entities : entities).push({ ...pendingPoly, type: 'LWPOLYLINE' }); pendingPoly = null; } return; }
    layers.add(ent.layer);
    (curBlock ? curBlock.entities : entities).push(ent);
  };

  for (const pr of pairs) {
    const [code, val] = pr;
    if (section === 'HEADER' && code === 9) { hdrVar = val; continue; }
    if (section === 'HEADER' && hdrVar === '$INSUNITS' && code === 70) { insUnits = Number(val); hdrVar = null; continue; }
    if (code === 0) {
      const up = val.toUpperCase();
      if (up === 'SECTION') { flush(); section = '?'; cur = null; continue; }
      if (up === 'ENDSEC') { flush(); section = null; continue; }
      if (up === 'EOF') { flush(); break; }
      if (section === 'BLOCKS' && up === 'BLOCK') { flush(); curBlock = { name: '', base: [0, 0], entities: [], header: [] }; cur = null; curBlock.collecting = true; continue; }
      if (section === 'BLOCKS' && up === 'ENDBLK') { flush(); if (curBlock) { blocks.set(curBlock.name, { base: curBlock.base, entities: curBlock.entities }); } curBlock = null; continue; }
      if (section === 'ENTITIES' || (section === 'BLOCKS' && curBlock)) { flush(); if (curBlock) curBlock.collecting = false; cur = [pr]; continue; }
      cur = null;
      continue;
    }
    if (section === '?' && code === 2) { section = val.toUpperCase(); continue; }
    if (section === 'BLOCKS' && curBlock?.collecting) {
      if (code === 2 && !curBlock.name) curBlock.name = val;
      else if (code === 10) curBlock.base[0] = Number(val);
      else if (code === 20) curBlock.base[1] = Number(val);
      continue;
    }
    if (cur) cur.push(pr);
  }
  flush();
  return { insUnits, entities, blocks, layers, skipped };
}

const num = (v) => Number(v);

/** @param {string} type @param {Array<[number,string]>} pr @param {Record<string,number>} skipped */
function decodeEntity(type, pr, skipped) {
  const layer = pr.find((p) => p[0] === 8)?.[1] ?? '0';
  const get = (c) => { const f = pr.find((p) => p[0] === c); return f ? num(f[1]) : undefined; };
  switch (type) {
    case 'LINE':
      return { type, layer, pts: [[get(10) ?? 0, get(20) ?? 0], [get(11) ?? 0, get(21) ?? 0]], closed: false };
    case 'LWPOLYLINE': {
      const pts = [];
      let bulge = false;
      for (const [c, v] of pr) {
        if (c === 10) pts.push([num(v), 0]);
        else if (c === 20 && pts.length) pts[pts.length - 1][1] = num(v);
        else if (c === 42 && num(v) !== 0) bulge = true;
      }
      if (bulge) skipped.bulges = (skipped.bulges ?? 0) + 1;
      return { type, layer, pts, closed: ((get(70) ?? 0) & 1) === 1 };
    }
    case 'POLYLINE':
      return { type, layer, pts: [], closed: ((get(70) ?? 0) & 1) === 1 };
    case 'VERTEX':
      return { type, layer, pt: [get(10) ?? 0, get(20) ?? 0] };
    case 'SEQEND':
      return { type, layer };
    case 'CIRCLE':
      return { type, layer, c: [get(10) ?? 0, get(20) ?? 0], r: get(40) ?? 0 };
    case 'ARC':
      return { type, layer, c: [get(10) ?? 0, get(20) ?? 0], r: get(40) ?? 0, a0: get(50) ?? 0, a1: get(51) ?? 360 };
    case 'TEXT':
      return { type, layer, at: [get(10) ?? 0, get(20) ?? 0], h: get(40) ?? 1, rot: get(50) ?? 0, text: pr.find((p) => p[0] === 1)?.[1] ?? '' };
    case 'MTEXT': {
      const chunks = pr.filter((p) => p[0] === 3).map((p) => p[1]).join('');
      const last = pr.find((p) => p[0] === 1)?.[1] ?? '';
      return { type, layer, at: [get(10) ?? 0, get(20) ?? 0], h: get(40) ?? 1, rot: get(50) ?? 0, text: stripMtext(chunks + last) };
    }
    case 'INSERT':
      return { type, layer, name: pr.find((p) => p[0] === 2)?.[1] ?? '', at: [get(10) ?? 0, get(20) ?? 0], sx: get(41) ?? 1, sy: get(42) ?? 1, rot: get(50) ?? 0 };
    case 'DIMENSION':
      return {
        type, layer, text: pr.find((p) => p[0] === 1)?.[1] ?? '', measure: get(42),
        p1: get(13) != null ? [get(13), get(23) ?? 0] : null, p2: get(14) != null ? [get(14), get(24) ?? 0] : null, mid: [get(11) ?? get(10) ?? 0, get(21) ?? get(20) ?? 0],
      };
    default:
      skipped[type] = (skipped[type] ?? 0) + 1;
      return null;
  }
}

/** Strip MTEXT inline formatting: {\fArial|b0;Text}, \P line breaks, %%c symbols. @param {string} s */
export function stripMtext(s) {
  return s
    .replace(/\\[fFcCHhQqWwTtAa][^;]*;/g, '')
    .replace(/\\[Pp]/g, ' ')
    .replace(/\\[LlOoKk]/g, '')
    .replace(/\\([\\{}])/g, '$1')
    .replace(/[{}]/g, '')
    .replace(/%%[cC]/g, '⌀').replace(/%%[dD]/g, '°').replace(/%%[pP]/g, '±')
    .replace(/\s+/g, ' ').trim();
}
