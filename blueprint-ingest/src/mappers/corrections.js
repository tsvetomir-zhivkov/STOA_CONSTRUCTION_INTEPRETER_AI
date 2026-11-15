/**
 * Human corrections that persist across runs: a deterministic lookup table, no model.
 *
 * corrections.json (repo root by default, see config.correctionsPath):
 *   { "ref:data/x/plan.dxf#obj17": { "category": "toilet" },  one element of one drawing: drawing.file + '#' + provenance.source_ref
 *                                                              (source_ref is stable for an unchanged file, but not unique across files)
 *     "block:WC-01":     { "category": "toilet" },   objects whose properties.source_block is WC-01
 *     "room:Pièce":      { "type": "other" },        rooms whose label or name is "Pièce"
 *     "layer:WINDOW_2":  { "as": "wall" },           the whole layer is read as another class (applied inside the extractor)
 *     "layer:FIXTURE":   { "category": "sink" } }    every object on that layer
 *
 * Keys and names match case- and accent-insensitively. An element takes the MOST SPECIFIC matching rule only:
 * ref, then block, then room, then layer. A corrected element gets provenance.extractor "human", confidence 1.0, the
 * rule and the previous value in provenance.details, and the document's history gets one entry per rule that fired.
 * A missing file means no corrections; an unreadable file or an invalid rule is logged and skipped, never fatal.
 */

import fs from 'node:fs/promises';
import { ROOM_TYPES } from './glossary.js';
import { OBJECT_CATEGORIES, applyRoomType, roomLabel } from './documentBuilder.js';
import { foldText } from './glossary.js';

const LAYER_CLASSES = new Set(['wall', 'room', 'door', 'window', 'stair', 'object']);
const OPENING_KINDS = new Set(['door', 'window', 'doorway']);
const ORDER = ['ref', 'block', 'room', 'layer'];
const norm = (s) => foldText(String(s ?? '')).trim();

/** @typedef {{ key:string, kind:'ref'|'block'|'room'|'layer', name:string, value:Record<string,string> }} Rule */
/** @typedef {{ path:string|null, rules: Rule[], layerAs: Map<string, { as:string, rule:string }> }} Corrections */

/** @returns {Corrections} */
export const emptyCorrections = () => ({ path: null, rules: [], layerAs: new Map() });

/**
 * @param {string|null} file
 * @param {{ logger?: any }} [o]
 * @returns {Promise<Corrections>}
 */
export async function loadCorrections(file, o = {}) {
  const out = emptyCorrections();
  if (!file) return out;
  let raw;
  try {
    raw = await fs.readFile(file, 'utf8');
  } catch (err) {
    if (err.code !== 'ENOENT') o.logger?.warn?.(`corrections file ${file} not readable (${err.message}); no corrections applied`);
    return out;
  }
  let json;
  try { json = JSON.parse(raw); } catch (err) { o.logger?.warn?.(`corrections file ${file} is not valid JSON (${err.message}); no corrections applied`); return out; }
  if (!json || typeof json !== 'object' || Array.isArray(json)) { o.logger?.warn?.(`corrections file ${file} must be an object of "kind:name": {...}; ignored`); return out; }
  out.path = file;
  for (const [key, value] of Object.entries(json)) {
    const m = /^(ref|block|room|layer):(.+)$/i.exec(key);
    const bad = (why) => o.logger?.warn?.(`corrections: skipping "${key}": ${why}`);
    if (!m) { bad('key must start with ref:, block:, room: or layer:'); continue; }
    if (!value || typeof value !== 'object') { bad('value must be an object'); continue; }
    const kind = /** @type {Rule['kind']} */ (m[1].toLowerCase()), name = m[2];
    const v = {};
    if (value.as != null) { if (kind !== 'layer' || !LAYER_CLASSES.has(value.as)) { bad(`"as" must be one of ${[...LAYER_CLASSES].join('|')} on a layer: rule`); continue; } v.as = value.as; }
    if (value.category != null) { if (!OBJECT_CATEGORIES.has(value.category)) { bad(`unknown category "${value.category}"`); continue; } v.category = value.category; }
    if (value.type != null) { if (!ROOM_TYPES.includes(value.type)) { bad(`unknown room type "${value.type}"`); continue; } v.type = value.type; }
    if (value.kind != null) { if (!OPENING_KINDS.has(value.kind)) { bad(`unknown opening kind "${value.kind}"`); continue; } v.kind = value.kind; }
    if (!Object.keys(v).length) { bad('no usable field (as, category, type or kind)'); continue; }
    out.rules.push({ key, kind, name, value: v });
    if (v.as) out.layerAs.set(norm(name), { as: v.as, rule: key });
  }
  return out;
}

/**
 * Apply the post-classification rules (everything except layer "as", which the extractor already used) to a document,
 * and write history entries for every rule that fired, including layer "as".
 * @param {any} doc schema document (changed in place)
 * @param {Corrections} corr
 * @param {{ now?: string }} [o]
 * @returns {Array<{ rule:string, ids:string[] }>} what fired
 */
export function applyCorrections(doc, corr, o = {}) {
  if (!corr?.rules.length) return [];
  const now = o.now ?? new Date().toISOString();
  const fired = new Map();
  const hit = (rule, id, note) => { const f = fired.get(rule) ?? fired.set(rule, { ids: [], notes: new Set() }).get(rule); f.ids.push(id); f.notes.add(note); };

  // layer "as" was applied while extracting: just record it
  for (const k of ['rooms', 'walls', 'openings', 'objects']) {
    for (const e of doc[k] ?? []) if (e.provenance?.extractor === 'human' && e.provenance.details?.rule?.startsWith('layer:')) hit(e.provenance.details.rule, e.id, `read as ${k.slice(0, -1)}`);
  }

  const rules = corr.rules.filter((r) => !r.value.as);
  const matchRule = (e, kindKey) => {
    for (const kind of ORDER) {
      for (const r of rules) {
        if (r.kind !== kind) continue;
        const n = norm(r.name);
        const ok = (kind === 'ref' && refMatches(n, doc.drawing?.file, e.provenance?.source_ref))
          || (kind === 'block' && e.properties?.source_block != null && norm(e.properties.source_block) === n)
          || (kind === 'room' && kindKey === 'rooms' && (norm(e.label) === n || norm(e.name) === n))
          || (kind === 'layer' && e.provenance?.layer != null && norm(e.provenance.layer) === n);
        if (ok && fieldFor(kindKey, r.value)) return r;
      }
    }
    return null;
  };

  for (const kindKey of ['rooms', 'openings', 'objects']) {
    for (const e of doc[kindKey] ?? []) {
      if (e.status === 'superseded') continue;
      const r = matchRule(e, kindKey);
      if (!r) continue;
      const field = fieldFor(kindKey, r.value);
      const previous = { [field]: e[field], extractor: e.provenance?.extractor, confidence: e.confidence };
      if (kindKey === 'rooms') {
        applyRoomType(e, r.value.type, { ...(e.properties?.name_resolution ?? {}), resolved_by: 'human' });
        e.label = roomLabel(e);
      } else e[field] = r.value[field];
      e.confidence = 1;
      e.provenance = { ...e.provenance, extractor: 'human', details: { ...(e.provenance?.details ?? {}), rule: r.key, previous } };
      hit(r.key, e.id, `${field} ${previous[field]} -> ${r.value[field]}`);
    }
  }

  for (const [rule, f] of fired) {
    doc.history.push({
      at: now, extractor: 'human', action: 'correction', entity_ids: f.ids.slice(0, 500),
      note: `corrections.json rule "${rule}"${corr.path ? ` (${corr.path})` : ''}: ${[...f.notes].slice(0, 5).join('; ')} on ${f.ids.length} element(s)`,
    });
  }
  return [...fired].map(([rule, f]) => ({ rule, ids: f.ids }));
}

/** "file#source_ref": both must match; a bare "source_ref" (no '#') is ambiguous across drawings and never matches */
function refMatches(n, file, sourceRef) {
  const i = n.lastIndexOf('#');
  if (i < 0) return false;
  return n.slice(0, i) === norm(file) && n.slice(i + 1) === norm(sourceRef);
}

/** which entity field a rule value changes for this collection, or null when it does not apply */
function fieldFor(kindKey, v) {
  if (kindKey === 'rooms') return v.type ? 'type' : null;
  if (kindKey === 'openings') return v.kind ? 'kind' : null;
  if (kindKey === 'objects') return v.category ? 'category' : null;
  return null;
}
