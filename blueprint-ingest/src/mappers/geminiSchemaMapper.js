/**
 * Bundles -> validated JSON files, with Gemini adding SEMANTICS only.
 *
 *   bundle (deterministic geometry)  --buildDocument-->  schema document
 *        |                                                     |
 *        |  glossary resolves most room names for free         v
 *        '--- what is left is sent to Gemini as compact, id-keyed requests (never coordinates)
 *             -> small patches are validated and applied by CODE
 *   -> validate (Ajv 2020-12) -> one self-repair pass (errors + invalid sub-object only) -> create or non-destructive merge -> atomic write
 *
 * Zero Gemini calls happen when nothing is ambiguous (e.g. an IFC whose room names are all in the glossary).
 */

import path from 'node:path';
import { buildDocument, applyRoomType, roomLabel } from './documentBuilder.js';
import { mergeDocuments } from './merge.js';
import { ROOM_TYPES, WET_TYPES } from './glossary.js';
import { parseUnitLabel } from '../extractors/textRoles.js';
import { createValidator, readJsonIfExists, writeJsonAtomic, groupErrorsByElement, pointerForElement, getByKey, setByKey, toGeminiSchema, describeErrors } from '../storage/jsonStore.js';
import { recomputeMetres } from '../storage/metres.js';
import { understandPdfWithGemini } from '../extractors/pdfExtractor.js';
import { hasWord } from '../extractors/textRoles.js';
import { PipelineError, SchemaValidationError, FrameMismatchError } from '../util/errors.js';
import { silentLogger } from '../util/logger.js';
import { UsageTracker } from '../util/usage.js';

/** text.role values Gemini may assign to texts the deterministic pass left without a role */
const TEXT_ROLES = ['title', 'level_title', 'legend', 'unit_label', 'other'];
const LLM_ID = 'gemini_namer';

const NAMES_SCHEMA = {
  type: 'object',
  properties: {
    rooms: {
      type: 'array',
      items: {
        type: 'object',
        properties: {
          i: { type: 'integer' },
          type: { type: 'string', enum: [...ROOM_TYPES] },
          normalized: { type: ['string', 'null'] },
          expanded: { type: ['string', 'null'] },
          language: { type: ['string', 'null'] },
        },
        required: ['i', 'type', 'normalized'],
      },
    },
  },
  required: ['rooms'],
};

const TEXTS_SCHEMA = {
  type: 'object',
  properties: {
    facts: {
      type: 'object',
      properties: {
        level: { type: ['string', 'null'] },
        languages: { type: 'array', items: { type: 'string' } },
      },
      required: ['languages'],
    },
    roles: { type: 'array', items: { type: 'object', properties: { i: { type: 'integer' }, role: { type: 'string', enum: TEXT_ROLES } }, required: ['i', 'role'] } },
  },
  required: ['roles'],
};

/**
 * @typedef {Object} MapOptions
 * @property {string} inputFilePath
 * @property {string} outputJsonPath
 * @property {'auto'|'create'|'merge'} [mode='auto'] auto: merge if the file exists, else create. create: refuse to touch an existing file. merge: the file must exist.
 * @property {import('../extractors/bundle.js').Bundle[]} bundles
 * @property {import('../config.js').PipelineConfig} config
 * @property {ReturnType<import('./geminiClient.js').createGeminiClient>} [gemini]
 * @property {UsageTracker} [usage]
 * @property {any} [logger]
 * @property {string} [now]
 * @property {string} [file] drawing.file override (dataset-relative path)
 * @property {string} [source] drawing.source override (dataset folder)
 */

/**
 * @param {MapOptions} opts
 * @returns {Promise<{ outputs: Array<{ outputJsonPath:string, drawingId:string, action:'created'|'merged'|'sibling', report: any, geminiCalls:number }>, usage: ReturnType<UsageTracker['snapshot']> }>}
 */
export async function mapToSchema(opts) {
  const { inputFilePath, outputJsonPath, mode = 'auto', bundles, config, gemini } = opts;
  const logger = opts.logger ?? silentLogger;
  const usage = opts.usage ?? gemini?.usage ?? new UsageTracker(config.gemini.pricing);
  const validator = await createValidator(config.schemaPath);
  if (!bundles.length) throw new PipelineError('No bundles to map', 'NO_BUNDLES');

  const outputs = [];
  for (const bundle of bundles) {
    const callsBefore = usage.calls;
    const costBefore = usage.costUsd;
    const { document, pending } = buildDocument(bundle, { inputFilePath, now: opts.now, file: opts.file, source: opts.source, datasetRoot: config.datasetRoot, multiPage: bundles.length > 1 && bundle.format === 'pdf' });

    if (gemini?.isConfigured) await enrich({ document, pending, bundle, inputFilePath, gemini, config, logger, usage, costBefore, now: opts.now });
    else if (pending.roomNames.length) logger.info(`${pending.roomNames.length} room name(s) unresolved by the glossary and no GEMINI_API_KEY set; they stay type "unknown"`);

    const target = outputPathFor(outputJsonPath, document, bundles.length, inputFilePath);
    await finalizeOne({ document, target, mode, validator, gemini, config, logger, usage, now: opts.now, outputs, callsBefore });
  }
  return { outputs, usage: usage.snapshot() };
}

/** one file per drawing page: if a source yields several pages/levels, suffix the drawing id before the extension */
export function outputPathFor(outputJsonPath, document, total, inputFilePath) {
  if (total <= 1) return outputJsonPath;
  const ext = path.extname(outputJsonPath);
  const base = outputJsonPath.slice(0, outputJsonPath.length - ext.length);
  const part = document.drawing.part ?? `p${(document.drawing.page_index ?? 0) + 1}`;
  return `${base}.${String(part).replace(/[^A-Za-z0-9_.-]/g, '_')}${ext || '.json'}`;
}

/** human-readable identity of a document: file plus part, e.g. "data/x/plan.ifc#level-1" */
export const drawingKey = (d) => `${d.file}${d.part ? `#${d.part}` : ''}`;

// =====================================================================================================
// persistence
// =====================================================================================================

async function finalizeOne({ document, target, mode, validator, gemini, config, logger, usage, now, outputs, callsBefore }) {
  let existing = await readJsonIfExists(target);
  if (mode === 'create' && existing) throw new PipelineError(`Refusing to overwrite existing ${target} in mode "create" (use "auto" or "merge")`, 'OUTPUT_EXISTS');
  if (mode === 'merge' && !existing) throw new PipelineError(`mode "merge" needs an existing ${target}`, 'OUTPUT_MISSING');

  let doc = await validateOrRepair(document, { validator, gemini, logger, label: 'new document' });
  let action = 'created';
  let report = { added: [...doc.levels, ...doc.units, ...doc.rooms, ...doc.walls, ...doc.openings, ...doc.texts, ...doc.objects].length };
  let outPath = target;

  if (existing) {
    existing = await validateOrRepair(existing, { validator, gemini, logger, label: `existing ${path.basename(target)}`, existingFile: true });
    try {
      const m = mergeDocuments(existing, doc, config, { now });
      doc = m.document; report = m.report; action = 'merged';
    } catch (err) {
      if (err instanceof FrameMismatchError && config.merge.onFrameMismatch === 'sibling') {
        const ext = path.extname(target);
        outPath = `${target.slice(0, target.length - ext.length)}.alt-${doc.drawing.image.width_px}x${doc.drawing.image.height_px}${ext}`;
        action = 'sibling';
        logger.warn(`pixel frame differs from ${target}; writing ${outPath} instead`);
        const prior = await readJsonIfExists(outPath);
        if (prior) { const m = mergeDocuments(prior, doc, config, { now }); doc = m.document; report = m.report; } else report = { note: 'new sibling file' };
      } else throw err;
    }
    // merged result must be valid too (a bug or a stale file must never be persisted silently)
    doc = await validateOrRepair(doc, { validator, gemini, logger, label: 'merged document' });
  }
  await writeJsonAtomic(outPath, doc);
  outputs.push({ outputJsonPath: outPath, drawingId: drawingKey(doc.drawing), action, report, geminiCalls: usage.calls - callsBefore });
}

/**
 * Validate; on failure do exactly ONE repair pass that sends Gemini only the errors and the invalid sub-object.
 * Without a configured Gemini (or when the repair does not fix it) a SchemaValidationError is thrown.
 */
async function validateOrRepair(doc, { validator, gemini, logger, label, existingFile = false }) {
  let r = validator.validate(doc);
  if (r.valid) return doc;
  logger.warn(`${label} fails the schema (${r.errors.length} errors); attempting one repair pass`);
  if (!gemini?.isConfigured) throw new SchemaValidationError(r.errors, `${label} does not conform to the schema and no Gemini key is available for repair: ${describeErrors(r.errors).slice(0, 3).join('; ')}`);

  const fixed = structuredClone(doc);
  const groups = groupErrorsByElement(r.errors);
  for (const [key, errs] of groups) {
    if (key === '' || key === '/schema_version') continue; // structural problems are not for the model to guess
    const el = getByKey(fixed, key);
    const schema = toGeminiSchema(validator.schema, pointerForElement(key));
    const prompt = `Fix this JSON object so it satisfies the schema. Change only what the errors require; keep all other values exactly. Never invent geometry.\nERRORS:\n${describeErrors(errs).join('\n')}\nOBJECT:\n${JSON.stringify(el)}`;
    const { json } = await gemini.generateJson({ tier: 'primary', label: `repair${key}`, prompt, schema });
    setByKey(fixed, key, json);
  }
  recomputeMetres(fixed);
  r = validator.validate(fixed);
  if (!r.valid) throw new SchemaValidationError(r.errors, `${label} still invalid after the repair pass${existingFile ? ' (the existing file was left untouched)' : ''}: ${describeErrors(r.errors).slice(0, 3).join('; ')}`);
  return fixed;
}

// =====================================================================================================
// Gemini enrichment (semantics only)
// =====================================================================================================

/** simple concurrency-limited map */
async function pool(items, limit, fn) {
  const out = new Array(items.length);
  let next = 0;
  await Promise.all(Array.from({ length: Math.min(limit, items.length) }, async () => {
    for (;;) { const i = next++; if (i >= items.length) return; out[i] = await fn(items[i], i); }
  }));
  return out;
}

const chunk = (arr, n) => Array.from({ length: Math.ceil(arr.length / n) }, (_, i) => arr.slice(i * n, i * n + n));

async function enrich({ document, pending, bundle, inputFilePath, gemini, config, logger, usage, costBefore, now }) {
  const touched = new Set();
  const at = now ?? new Date().toISOString();
  const roomsById = new Map(document.rooms.map((r) => [r.id, r]));
  const languages = new Set(pending.languages ?? []);
  const prov = (ref) => ({ extractor: LLM_ID, source_ref: ref });

  // 1) unresolved room names: unique raw strings only, ids replaced by small integers
  if (pending.roomNames.length) {
    const unique = [...new Set(pending.roomNames.map((n) => n.raw))];
    const langs = [...languages].join(',') || 'unknown';
    await pool(chunk(unique, config.mapper.batchSize), 2, async (batch) => {
      const prompt = `Classify room labels from an architectural floor plan. Known drawing languages: ${langs}. For each label give: type (fixed list; "wc" = toilet only, "entry" = entrance hall, "outdoor" = garden/yard/patio, "living" = living room), English name (normalized), the full word in the drawing's language (expanded, if the label is an abbreviation), ISO 639-1 language. Use type "unknown" when you cannot tell. Labels:\n${JSON.stringify(batch.map((raw, i) => ({ i, raw })))}`;
      try {
        const { json } = await gemini.generateJson({ tier: 'primary', label: 'room_names', prompt, schema: NAMES_SCHEMA });
        const byRaw = new Map();
        for (const it of json.rooms ?? []) {
          const raw = batch[it.i];
          if (raw == null || !ROOM_TYPES.includes(it.type)) continue;
          byRaw.set(raw, it);
        }
        for (const n of pending.roomNames) {
          const it = byRaw.get(n.raw);
          const room = roomsById.get(n.id);
          if (!it || !room) continue;
          room.name = n.raw;
          applyRoomType(room, it.type, { normalized: it.normalized ?? null, expanded: it.expanded ?? null, language: it.language ?? null, is_wet_room: WET_TYPES.has(it.type), resolved_by: LLM_ID });
          room.label = roomLabel(room);
          if (it.language) languages.add(it.language);
          touched.add(room.id);
        }
      } catch (err) { logger.warn(`room-name call failed: ${err.message}; names stay "unknown"`); }
    });
  }

  // 2) text roles + page level, only for formats that do not carry them
  const needFacts = bundle.format !== 'ifc' && !document.levels.length;
  const cand = pending.texts.filter((t) => hasWord(t.raw) && t.raw.length >= 2).sort((a, b) => b.fontPx - a.fontPx);
  let levelName = null;
  if (cand.length) {
    const W = document.drawing.image.width_px, H = document.drawing.image.height_px;
    const textsById = new Map(document.texts.map((t) => [t.id, t]));
    const chunks = chunk(cand, config.mapper.batchSize).slice(0, 4); // hard cap on cost: 4 calls per page
    await pool(chunks, 2, async (batch, ci) => {
      const compact = batch.map((t, i) => ({ i, t: t.raw.slice(0, config.mapper.maxTextChars), x: Math.round(((t.bbox[0] + t.bbox[2]) / 2 / W) * 100), y: Math.round(((t.bbox[1] + t.bbox[3]) / 2 / H) * 100), f: Math.round(t.fontPx) }));
      const prompt = `You see text snippets from one architectural drawing page (x,y = position in % of the page from top-left, f = font size px). ${ci === 0 && needFacts ? 'Fill "facts" with the storey/level name exactly as printed (e.g. "1.korrus", "Kælder", "2. OG") and the languages when the snippets show them; otherwise null.' : 'Leave facts empty.'} For "roles" give a role only for snippets that clearly are a drawing title, level title, legend entry, apartment/unit label (e.g. "Whg. A") or another note ("other"); omit the rest.\n${JSON.stringify(compact)}`;
      try {
        const { json } = await gemini.generateJson({ tier: 'primary', label: 'text_roles', prompt, schema: TEXTS_SCHEMA });
        for (const r of json.roles ?? []) {
          const t = batch[r.i] && textsById.get(batch[r.i].id);
          if (t && TEXT_ROLES.includes(r.role) && !t.role) { t.role = r.role; touched.add(t.id); }
        }
        const f = json.facts;
        if (f && ci === 0) {
          if (f.level && typeof f.level === 'string') levelName = f.level.trim();
          for (const l of f.languages ?? []) if (/^[a-z]{2}$/.test(l)) languages.add(l);
        }
      } catch (err) { logger.warn(`text-role call failed: ${err.message}`); }
    });
  }

  // 3) legend / schedule pages of vector PDFs: Files API (upload is deleted in a finally block inside understandPdfWithGemini)
  if (bundle.format === 'pdf' && pending.looksLikeLegendOrSchedule && config.pdf.filesApi !== 'off') {
    const u = await understandPdfWithGemini(inputFilePath, { gemini, pageIndex: bundle.page.pageIndex, logger });
    if (u) {
      const byRaw = new Map(document.texts.map((t) => [t.text.trim().toLowerCase(), t]));
      if (u.title) { const t = byRaw.get(String(u.title).trim().toLowerCase()); if (t && !t.role) { t.role = 'title'; touched.add(t.id); } }
      if (!levelName && !document.levels.length && u.level) levelName = String(u.level).trim();
      let n = 0;
      for (const e of u.legend ?? []) {
        const a = byRaw.get(String(e.symbol).trim().toLowerCase()), b = byRaw.get(String(e.meaning).trim().toLowerCase());
        if (!a || !b || a === b) continue;
        a.role = 'legend'; b.role = 'legend';
        const id = `edge_legend_${++n}`;
        document.edges.push({ id, confidence: 0.7, provenance: prov(`files_api:page${bundle.page.pageIndex}:legend:${n}`), status: 'active', superseded_by: null, level_id: null, flags: [], kind: 'legend', from_id: a.id, to: b.id, meaning: e.meaning });
        touched.add(id); touched.add(a.id); touched.add(b.id);
      }
      if (n) { const c = document.coverage.find((x) => x.category === 'legend'); if (c) Object.assign(c, { status: 'partial', confidence: 0.6, extractor: LLM_ID }); }
    }
  }

  // ---- level found on the page -> level entity; entities without a level belong to it ---------------------------------------
  if (levelName && !document.levels.length) {
    const lt = document.texts.find((t) => t.text.trim() === levelName);
    const id = `level_${levelName.replace(/[^A-Za-z0-9_.-]/g, '_')}`;
    document.levels.push({ id, confidence: 0.6, provenance: prov(lt ? lt.id : 'text_roles:facts.level'), status: 'active', superseded_by: null, level_id: null, flags: [], name: levelName, elevation_m: null, height_m: null, bbox_px: null });
    if (lt && !lt.role) lt.role = 'level_title';
    for (const k of ['units', 'rooms', 'walls', 'openings', 'objects', 'texts']) for (const e of document[k]) e.level_id ??= id;
    touched.add(id);
  }

  // ---- unit labels found by Gemini -> unit entities ---------------------------------------------------------------------------------------
  for (const t of document.texts) {
    if (t.role !== 'unit_label' || !touched.has(t.id)) continue;
    const label = parseUnitLabel(t.text)?.label ?? t.text.trim();
    if (document.units.some((u) => u.label === label)) continue;
    const id = `unit_${label.replace(/[^A-Za-z0-9_.-]/g, '_')}`;
    document.units.push({ id, confidence: 0.5, provenance: prov(t.id), status: 'active', superseded_by: null, level_id: t.level_id ?? null, flags: [], label, label_raw: t.text, polygon_px: null, polygon_m: null, area_m2: null });
    touched.add(id);
  }

  if (touched.size) {
    const model = config.gemini.models.primary;
    const cost = usage.costUsd != null && costBefore != null ? Number((usage.costUsd - costBefore).toFixed(6)) : null;
    const ids = [...touched];
    document.history.push({ at, extractor: LLM_ID, action: 'semantic_enrichment', entity_ids: ids.slice(0, 500), cost_usd: cost, note: `Gemini (${model}): room types, text roles, level, legend${languages.size ? `; languages ${[...languages].join(',')}` : ''}` });
  }
}
