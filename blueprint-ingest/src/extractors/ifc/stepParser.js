/**
 * Streaming STEP (ISO 10303-21) reader for IFC files - no WASM, no full-file string.
 *
 * Memory model: the file is read line by line in TWO passes.
 *   pass A  keeps only entity types we ask for (never raw IFCCARTESIANPOINT soup) and records which point ids they reference;
 *   pass B  loads just those referenced points.
 * Huge tessellated models therefore cost one cheap scan instead of millions of retained objects.
 */

import fs from 'node:fs';
import readline from 'node:readline';

/** @typedef {{ ref: number }} Ref */
/** @typedef {{ enum: string }} Enum */
/** @typedef {{ type: string, value: any }} Typed */

/** Decode STEP string escapes: '' -> ', \X2\00E4\X0\ -> ä, \X\E4 -> ä. @param {string} s */
export function decodeStepString(s) {
  return s
    .replace(/''/g, "'")
    .replace(/\\X2\\([0-9A-Fa-f]+)\\X0\\/g, (_, hex) => (hex.match(/.{4}/g) ?? []).map((h) => String.fromCharCode(parseInt(h, 16))).join(''))
    .replace(/\\X\\([0-9A-Fa-f]{2})/g, (_, h) => String.fromCharCode(parseInt(h, 16)))
    .replace(/\\\\/g, '\\');
}

/**
 * Parse a STEP parameter list (the text between the outer parentheses of an entity).
 * $ -> null, * -> '*', #12 -> {ref:12}, .T. -> {enum:'T'}, 'abc' -> 'abc', IFCLABEL('x') -> {type,value}, (..) -> array.
 * @param {string} s
 * @returns {any[]}
 */
export function parseParams(s) {
  let i = 0;
  const n = s.length;
  const ws = () => { while (i < n && /\s/.test(s[i])) i++; };

  function value() {
    ws();
    const c = s[i];
    if (c === '$') { i++; return null; }
    if (c === '*') { i++; return '*'; }
    if (c === '#') {
      let j = i + 1;
      while (j < n && s.charCodeAt(j) >= 48 && s.charCodeAt(j) <= 57) j++;
      const r = { ref: Number(s.slice(i + 1, j)) };
      i = j;
      return r;
    }
    if (c === "'") {
      let j = i + 1;
      for (; j < n; j++) {
        if (s[j] === "'") { if (s[j + 1] === "'") { j++; continue; } break; }
      }
      const raw = s.slice(i + 1, j);
      i = j + 1;
      return decodeStepString(raw);
    }
    if (c === '"') { // IFC4 binary strings: keep raw
      const j = s.indexOf('"', i + 1);
      const raw = s.slice(i + 1, j);
      i = j + 1;
      return raw;
    }
    if (c === '.') {
      const j = s.indexOf('.', i + 1);
      const e = s.slice(i + 1, j);
      i = j + 1;
      return { enum: e };
    }
    if (c === '(') {
      i++;
      const arr = [];
      ws();
      if (s[i] === ')') { i++; return arr; }
      for (;;) {
        arr.push(value());
        ws();
        if (s[i] === ',') { i++; continue; }
        if (s[i] === ')') { i++; break; }
        break; // malformed: bail out of this list
      }
      return arr;
    }
    // number or typed value
    const m = /^[-+]?(?:\d+\.?\d*(?:[eE][-+]?\d+)?|\.\d+(?:[eE][-+]?\d+)?)/.exec(s.slice(i, i + 40));
    if (m) { i += m[0].length; return Number(m[0]); }
    const t = /^[A-Za-z_][A-Za-z0-9_]*/.exec(s.slice(i, i + 80));
    if (t) {
      i += t[0].length;
      ws();
      if (s[i] === '(') {
        const inner = value(); // parses the parenthesised list
        return { type: t[0].toUpperCase(), value: Array.isArray(inner) && inner.length === 1 ? inner[0] : inner };
      }
      return t[0];
    }
    i++; // unknown character: skip
    return null;
  }

  const out = [];
  ws();
  while (i < n) {
    out.push(value());
    ws();
    if (s[i] === ',') i++;
    else if (i < n) i++;
    ws();
  }
  return out;
}

/** Strip a typed wrapper: {type:'IFCLABEL', value:'x'} -> 'x'; {enum:'T'} -> true; other values unchanged. @param {any} v */
export function unwrap(v) {
  if (v && typeof v === 'object' && !Array.isArray(v)) {
    if ('type' in v && 'value' in v) return unwrap(v.value);
    if ('enum' in v) return v.enum === 'T' ? true : v.enum === 'F' ? false : v.enum;
  }
  return v;
}

export const isRef = (v) => v && typeof v === 'object' && typeof v.ref === 'number';

const STMT_RE = /^#(\d+)\s*=\s*([A-Za-z0-9_]+)\s*\(([\s\S]*)\)\s*;?\s*$/;

/**
 * Async generator over the statements of the DATA section.
 * @param {string} filePath
 * @returns {AsyncGenerator<{ header?: string, id?: number, type?: string, body?: string }>}
 */
async function* statements(filePath) {
  const rl = readline.createInterface({ input: fs.createReadStream(filePath, { encoding: 'latin1' }), crlfDelay: Infinity });
  let inData = false;
  let buf = '';
  let quotes = 0;
  for await (const line of rl) {
    if (!inData) {
      if (/^\s*FILE_SCHEMA/i.test(line) || /^\s*FILE_NAME/i.test(line)) yield { header: line };
      if (/^\s*DATA\s*;/i.test(line)) inData = true;
      continue;
    }
    if (/^\s*ENDSEC\s*;/i.test(line)) break;
    const trimmed = line.trim();
    if (!trimmed || trimmed.startsWith('/*')) continue;
    buf += (buf ? ' ' : '') + trimmed;
    for (let k = 0; k < trimmed.length; k++) if (trimmed.charCodeAt(k) === 39) quotes++;
    // statement complete when it ends with ';' and quotes are balanced (a '' escape adds two)
    if (trimmed.endsWith(';') && quotes % 2 === 0) {
      const m = STMT_RE.exec(buf);
      if (m) yield { id: Number(m[1]), type: m[2].toUpperCase(), body: m[3] };
      buf = '';
      quotes = 0;
    }
  }
}

/** Collect every #ref inside parsed params. @param {any} v @param {Set<number>} out */
function collectRefs(v, out) {
  if (Array.isArray(v)) for (const x of v) collectRefs(x, out);
  else if (isRef(v)) out.add(v.ref);
}

/**
 * Parse an IFC file into an entity map.
 *
 * @param {string} filePath
 * @param {{ wanted: { has(type:string): boolean }, pointHolders?: { has(type:string): boolean } }} opts
 *   wanted       entity types to keep (parsed): a Set, or any object with has(type)
 *   pointHolders entity types whose ref'd IFCCARTESIANPOINTs must be loaded (default: any kept entity)
 * @returns {Promise<{ schema: string|null, fileName: string[]|null, typeCounts: Map<string, number>, entities: Map<number, {type:string, args:any[]}> }>}
 *   fileName: the quoted strings of the FILE_NAME header (name, time stamp, author..., originating system...)
 *   typeCounts: number of instances of EVERY entity type in the file, kept or not
 */
export async function parseStepFile(filePath, { wanted, pointHolders }) {
  const entities = new Map();
  const neededPoints = new Set();
  const typeCounts = new Map();
  let schema = null, fileName = null;
  const holders = pointHolders ?? wanted;

  // pass A ---------------------------------------------------------------
  for await (const st of statements(filePath)) {
    if (st.header) {
      const m = /FILE_SCHEMA\s*\(\s*\(\s*'([^']+)'/i.exec(st.header);
      if (m) schema = m[1];
      const fn = /FILE_NAME\s*\(([\s\S]*)\)\s*;?\s*$/i.exec(st.header);
      if (fn) fileName = [...fn[1].matchAll(/'((?:[^']|'')*)'/g)].map((x) => x[1].replace(/''/g, "'"));
      continue;
    }
    typeCounts.set(st.type, (typeCounts.get(st.type) ?? 0) + 1);
    if (!wanted.has(st.type)) continue;
    const args = parseParams(st.body);
    entities.set(st.id, { type: st.type, args });
    if (holders.has(st.type)) collectRefs(args, neededPoints);
  }

  // pass B ---------------------------------------------------------------
  if (neededPoints.size) {
    for await (const st of statements(filePath)) {
      if (st.header || st.type !== 'IFCCARTESIANPOINT' || !neededPoints.has(st.id)) continue;
      entities.set(st.id, { type: st.type, args: parseParams(st.body) });
    }
  }
  return { schema, fileName, typeCounts, entities };
}

/**
 * Entity type of selected ids (one extra streaming pass; used only to NAME products the extractor does not read).
 * @param {string} filePath @param {Set<number>} ids @returns {Promise<Map<number,string>>}
 */
export async function typesOfIds(filePath, ids) {
  const out = new Map();
  if (!ids.size) return out;
  for await (const st of statements(filePath)) {
    if (!st.header && ids.has(st.id)) { out.set(st.id, st.type); if (out.size === ids.size) break; }
  }
  return out;
}
