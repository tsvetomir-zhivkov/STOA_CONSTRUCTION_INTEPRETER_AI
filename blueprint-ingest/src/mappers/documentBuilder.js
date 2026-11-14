/**
 * Bundle -> schema v0.2 document (deterministic): this step turns geometry + raw facts into the schema shape,
 * resolves names through the glossary, links texts to rooms by geometry and writes the code-owned fields.
 */

import path from 'node:path';
import { bboxOfPoints, pointInPolygon, polygonArea, polygonCenterline, minAreaRect, round } from '../geometry/geom.js';
import { classifyTextContent, areaToM2, hasWord, looksLikeRoomNumber, parseUnitLabel } from '../extractors/textRoles.js';
import { lookupRoomName, OUTDOOR_TYPES } from './glossary.js';
import { recomputeMetres } from '../storage/metres.js';

export const SCHEMA_VERSION = '0.2';
export const TRANSLATOR_VERSION = 'blueprint-ingest@0.2.2';

/** bundle format -> drawing.route */
const ROUTE = { ifc: 'ifc', dxf: 'dxf', svg: 'svg', pdf: 'vector_pdf' };
/** legacy / extractor scale sources -> drawing.scale.source */
const SCALE_SOURCE = { vector_units: 'vector_units', scale_note: 'scale_note', dimension_lines: 'dimension_lines', dimension_line: 'dimension_lines', door_width: 'door_width', door_width_estimate: 'door_width', none: 'none' };
/** legacy / extractor text roles -> text.role (undefined = role unknown, the field is omitted) */
const TEXT_ROLE = { room_label: 'room_label', room_number: 'room_number', area: 'area', dimension: 'dimension', scale_note: 'scale_note', title: 'title', level_title: 'level_title', level: 'level_title', unit_label: 'unit_label', legend: 'legend', legend_entry: 'legend', other: 'other', note: 'other' };

/** schema object.category enum (stove..radiator added in 0.2.1) */
export const OBJECT_CATEGORIES = new Set(['toilet', 'sink', 'shower', 'bathtub', 'sauna_bench', 'stair', 'stove', 'fridge', 'washing_machine', 'elevator', 'radiator', 'closet', 'other']);

/** free-form object category (+ name) -> object.category enum (IFC / SVG / PDF; DXF classifies itself) */
const OBJECT_RULES = [
  ['toilet', /toilet|\bwc\b|klosett|vessa|water.?closet|\bwc[-_ ]/i],
  ['bathtub', /bath.?tub|badewanne|kylpyamme|baignoire|vann\b|\btub\b/i],
  ['shower', /shower|dusche|suihku|douche|dush/i],
  ['sink', /sink|basin|lavabo|lavatory|waschtisch|lavandino|allas|évier|kraanikauss/i],
  ['sauna_bench', /sauna|lauteet|laude|lava\b|saunabank/i],
  ['stair', /stair|treppe|porras|escalier|trepp/i],
];
export function mapObjectCategory(category, name) {
  const hay = `${category ?? ''} ${name ?? ''}`;
  for (const [cat, re] of OBJECT_RULES) if (re.test(hay)) return cat;
  return 'other';
}

const safe = (s) => String(s).replace(/[^A-Za-z0-9_.-]/g, '_');
const bboxOf = (poly) => bboxOfPoints(poly).map((v) => round(v, 2));
const center = (b) => [(b[0] + b[2]) / 2, (b[1] + b[3]) / 2];
const clamp01 = (c, d) => Math.max(0, Math.min(1, c ?? d));
const nonEmpty = (o) => (o && Object.keys(o).length ? o : undefined);

/** Unique id generator with a fixed prefix. */
function idMaker() {
  const used = new Set();
  return (prefix, key) => {
    const stripped = String(key).replace(/^(room|wall|door|window|opening|doorway|text|obj|level|unit)(?=\d)/, '');
    let id = `${prefix}${safe(stripped)}`;
    for (let i = 2; used.has(id); i++) id = `${prefix}${safe(stripped)}_${i}`;
    used.add(id);
    return id;
  };
}

/**
 * Dataset-relative path + source folder for drawing.file / drawing.source.
 * `data/<source>/...` anywhere in the path wins; otherwise the path relative to `datasetRoot` (default cwd) is used.
 * @param {string} inputFilePath @param {{ datasetRoot?: string|null }} [o]
 */
export function datasetPath(inputFilePath, o = {}) {
  const abs = path.resolve(inputFilePath).split(path.sep).join('/');
  const m = /(?:^|\/)(data\/([^/]+)\/.+)$/.exec(abs);
  if (m) return { file: m[1], source: m[2] };
  const rel = path.relative(path.resolve(o.datasetRoot ?? process.cwd()), path.resolve(inputFilePath)).split(path.sep).join('/');
  const file = rel && !rel.startsWith('..') ? rel : path.basename(inputFilePath);
  const segs = file.split('/');
  return { file, source: segs.length > 1 ? (segs[0] === 'data' && segs.length > 2 ? segs[1] : segs[0]) : path.basename(path.dirname(path.resolve(inputFilePath))) };
}

/**
 * @typedef {Object} BuildOptions
 * @property {string} inputFilePath
 * @property {string} [file] value for drawing.file (default: derived by datasetPath)
 * @property {string} [source] value for drawing.source (default: derived by datasetPath)
 * @property {string|null} [datasetRoot]
 * @property {string} [now] ISO timestamp
 * @property {boolean} [multiPage] set drawing.part to p{n}
 */

/**
 * @param {import('../extractors/bundle.js').Bundle} bundle
 * @param {BuildOptions} opts
 * @returns {{ document: any, pending: { roomNames: Array<{id:string, raw:string}> } }}  roomNames: labels not in the glossary (type stays "unknown")
 */
export function buildDocument(bundle, opts) {
  const now = opts.now ?? new Date().toISOString();
  const extId = bundle.extractor.id;
  const mk = idMaker();
  const ds = datasetPath(opts.inputFilePath, { datasetRoot: opts.datasetRoot });

  // ---- level ---------------------------------------------------------------------------------------------------------------
  // a bundle has ONE level (a page / a storey) or SEVERAL (an IFC building: bundle.levels, elements carry levelKey)
  const levels = [];
  let levelId = null;
  const levelIdByKey = new Map();
  const addLevel = (lv, longName) => {
    const id = mk('level_', lv.name);
    levels.push({
      id, confidence: clamp01(lv.confidence, 0.9),
      provenance: { extractor: extId, source_ref: lv.sourceRef || lv.name },
      status: 'active', superseded_by: null, level_id: null, flags: [],
      ...(longName ? { properties: { long_name: longName } } : {}),
      name: lv.name, elevation_m: lv.elevationM ?? null, height_m: lv.heightM ?? null, bbox_px: null,
    });
    return id;
  };
  if (Array.isArray(bundle.levels) && bundle.levels.length) {
    for (const lv of bundle.levels) if (lv?.name) levelIdByKey.set(lv.key ?? lv.name, addLevel(lv, lv.longName));
  } else if (bundle.level?.name) {
    levelId = addLevel(bundle.level, bundle.page.title);
  }
  /** level of a bundle element: its own storey in a multi-level bundle, else the bundle's single level */
  const levelOf = (el) => (el?.levelKey != null && levelIdByKey.has(el.levelKey) ? levelIdByKey.get(el.levelKey) : levelId);

  /** entity base fields */
  const base = (id, el, defConf, extra = {}) => {
    const prov = { extractor: extId, source_ref: String(el.sourceRef ?? el.key ?? id) || id };
    if (el.layer) prov.layer = String(el.layer);
    let confidence = clamp01(el.confidence, defConf);
    if (el.correctedBy) { // layer reclassified by corrections.json ("layer:NAME": { as })
      prov.extractor = 'human';
      prov.details = { rule: el.correctedBy, geometry_extractor: extId };
      confidence = 1;
    }
    const properties = nonEmpty({ ...(el.properties ?? {}), ...extra });
    return { id, confidence, provenance: prov, status: 'active', superseded_by: null, level_id: levelOf(el), flags: [...(el.flags ?? [])], ...(properties ? { properties } : {}) };
  };

  // ---- texts ---------------------------------------------------------------------------------------------------------------
  const fontOf = new Map();
  const unitLabels = new Map(); // text id -> label
  const texts = bundle.texts.map((t) => {
    const cls = classifyTextContent(t.raw);
    const role = TEXT_ROLE[t.roleHint ?? cls.role ?? ''];
    const value = t.value ?? cls.value ?? null;
    const id = mk('text_', t.key);
    fontOf.set(id, t.fontSizePx ?? 0);
    if (role === 'unit_label') unitLabels.set(id, cls.unitLabel ?? parseUnitLabel(t.raw)?.label ?? t.raw);
    const bb = t.bboxPx.map((v) => round(v, 2));
    return {
      ...base(id, t, 0.9, { ...(value && role && role !== 'level_title' ? { value: { number: value.number, ...(value.unit ? { unit: value.unit } : {}) } } : {}), ...(t.fontSizePx ? { font_size_px: round(t.fontSizePx, 2) } : {}) }),
      text: t.raw, bbox_px: bb, angle_deg: t.rotationDeg ?? 0, ...(role ? { role } : {}), room_id: null,
    };
  });
  const textIdByKey = new Map(bundle.texts.map((t, i) => [t.key, texts[i].id]));

  // ---- walls ---------------------------------------------------------------------------------------------------------------
  const wallIds = new Map();
  const walls = bundle.walls.map((w) => {
    const id = mk('wall_', w.key);
    wallIds.set(w.key, id);
    // outline-only walls: axis + thickness from the minimum-area rectangle (gives length_m / thickness_m)
    const cl = !w.centerlinePx || w.thicknessPx == null ? polygonCenterline(w.polygonPx) : null;
    const axis = w.centerlinePx ?? (cl ? [cl.a, cl.b].map((p) => p.map((v) => round(v, 2))) : null);
    const tpx = w.thicknessPx ?? (cl ? round(cl.thickness, 2) : null);
    return {
      ...base(id, w, 0.6, tpx != null ? { thickness_px: tpx } : {}),
      polyline_px: axis, polygon_px: w.polygonPx, polygon_m: null, length_m: null, thickness_m: null,
      height_m: w.heightM ?? null, net_side_area_m2: w.netSideAreaM2 ?? null, net_volume_m3: w.netVolumeM3 ?? null,
      is_external: w.isExterior ?? null, is_load_bearing: w.isLoadBearing ?? null, room_ids: [],
    };
  });

  // ---- rooms ---------------------------------------------------------------------------------------------------------------
  const internal = new Map(); // room id -> { rawName, poly, area }
  const rooms = bundle.rooms.map((r) => {
    const id = mk('room_', r.key);
    internal.set(id, { rawName: r.rawName ?? null, poly: r.polygonPx, area: polygonArea(r.polygonPx) });
    return {
      ...base(id, r, 0.6, r.floorElevationM != null ? { floor_elevation_m: r.floorElevationM } : {}),
      name: null, number: r.rawNumber ?? null, label: null, type: 'unknown', subtype: null, unit_id: null, parent_room_id: null, is_outdoor: false,
      polygon_px: r.polygonPx, polygon_m: null, holes_px: [], holes_m: [],
      area_m2: null, area_written_m2: r.writtenAreaM2 ?? null, area_computed_m2: null, height_m: r.heightM ?? null, volume_m3: null, text_ids: [],
    };
  });

  // ---- link texts to rooms (geometry only) ----------------------------------------------------------------------------------
  // lid: only rooms of that level (a multi-level document stacks floors in one plan frame)
  const roomAt = (pt, lid = null) => {
    let best = null;
    for (const r of rooms) { if (lid != null && r.level_id !== lid) continue; const x = internal.get(r.id); if (pointInPolygon(pt, x.poly) && (!best || x.area < internal.get(best.id).area)) best = r; }
    return best;
  };
  const byRoom = new Map(rooms.map((r) => [r.id, { names: [], numbers: [], areas: [] }]));
  for (const t of texts) {
    const r = roomAt(center(t.bbox_px));
    if (!r) continue;
    t.room_id = r.id;
    const slot = byRoom.get(r.id);
    if (t.role === 'area') slot.areas.push(t);
    else if (!t.role && looksLikeRoomNumber(t.text)) slot.numbers.push(t);
    else if (!t.role && hasWord(t.text) && t.text.length <= 40) slot.names.push(t);
  }

  const pendingNames = [];
  for (const r of rooms) {
    const { names, numbers, areas } = byRoom.get(r.id);
    let raw = internal.get(r.id).rawName;
    const rc = center(bboxOf(r.polygon_px));
    const dist = (t) => Math.hypot(...center(t.bbox_px).map((v, i) => v - rc[i]));
    if (names.length) {
      // prefer a glossary hit, then the largest print, then the text nearest the room's centre
      const score = (t) => (lookupRoomName(t.text) ? 1e6 : 0) + (fontOf.get(t.id) ?? 0) * 10 - dist(t) * 0.01;
      const pick = [...names].sort((a, b) => score(b) - score(a))[0];
      pick.role = 'room_label';
      r.text_ids.push(pick.id);
      raw = pick.text;
    }
    if (numbers.length && r.number == null) {
      const pick = [...numbers].sort((a, b) => dist(a) - dist(b))[0];
      pick.role = 'room_number';
      r.number = pick.text.trim();
      r.text_ids.push(pick.id);
    }
    if (areas.length) {
      const a = areas[0];
      const m2 = areaToM2(a.properties?.value);
      if (m2 != null) { r.area_written_m2 = round(m2, 2); (r.properties ??= {}).area_written_text_id = a.id; r.text_ids.push(a.id); }
    }
    if (raw) {
      r.name = raw;
      const hit = lookupRoomName(raw);
      if (hit) {
        applyRoomType(r, hit.type, { normalized: hit.normalized, expanded: hit.expanded, language: hit.language, glossary_term_id: hit.glossary_term_id, resolved_by: 'glossary' });
        const lt = texts.find((t) => t.id === r.text_ids[0] && t.role === 'room_label');
        if (lt) (lt.properties ??= {}).language = hit.language;
      } else {
        pendingNames.push({ id: r.id, raw });
      }
    }
    r.label = roomLabel(r);
  }

  // ---- openings / objects ------------------------------------------------------------------------------------------------------
  const openings = bundle.openings.map((o) => {
    const kind = o.kind === 'door' || o.kind === 'window' ? o.kind : 'doorway';
    const id = mk(`${kind}_`, o.key);
    const h = o.heightM ?? (o.headHeightM != null && o.sillHeightM != null ? round(o.headHeightM - o.sillHeightM, 3) : null);
    const extra = {};
    if (o.subtype) extra.subtype = o.subtype;
    const wpx = o.widthPx ?? (minAreaRect(o.polygonPx)?.length ?? null); // outline-only openings: long side of the outline
    if (wpx != null) extra.width_px = round(wpx, 2);
    if (o.sillHeightM != null) extra.sill_height_m = o.sillHeightM;
    if (o.headHeightM != null) extra.head_height_m = o.headHeightM;
    return {
      ...base(id, o, 0.6, extra), kind, bbox_px: bboxOf(o.polygonPx), polygon_px: o.polygonPx, polygon_m: null,
      width_m: null, height_m: h, is_entrance: o.isEntrance ?? null, wall_id: o.wallKey ? (wallIds.get(o.wallKey) ?? null) : null, room_ids: [],
    };
  });
  const objects = bundle.objects.map((o) => {
    const id = mk('obj_', o.key);
    const bb = bboxOf(o.polygonPx);
    const category = bundle.format === 'dxf' && OBJECT_CATEGORIES.has(o.category) ? o.category : mapObjectCategory(o.category, o.name);
    return {
      ...base(id, o, 0.5, o.name ? { name: o.name } : {}), category, subtype: o.subtype !== undefined ? o.subtype : (o.category && o.category !== category ? o.category : null),
      bbox_px: bb, polygon_px: o.polygonPx, polygon_m: null, room_id: roomAt(center(bb), levelOf(o))?.id ?? null, net_volume_m3: o.netVolumeM3 ?? null, area_m2: null,
    };
  });

  // ---- a room that contains a detected stair is a stair room (DXF only) ---------------------------------------------------------
  // Only rooms whose own label did not resolve (unknown / other) are changed: a type read from the label is stronger evidence
  // than "a stair's centre falls inside". Not applied to IFC, whose stairs and spaces overlap freely (gross-area spaces).
  for (const o of bundle.format === 'dxf' ? objects : []) {
    if (o.category !== 'stair' || !o.room_id) continue;
    const r = rooms.find((x) => x.id === o.room_id);
    if (!r || (r.type !== 'unknown' && r.type !== 'other')) continue;
    const prev = r.type;
    applyRoomType(r, 'stair', { ...(r.properties?.name_resolution ?? {}), resolved_by: 'contains_stair', type_before: prev });
    if (!r.flags.includes('inferred_from_contents')) r.flags.push('inferred_from_contents');
  }

  // ---- units (apartments) from unit-label texts -----------------------------------------------------------------------------------
  const units = [];
  for (const [tid, label] of unitLabels) {
    if (units.some((u) => u.label === label)) continue;
    const t = texts.find((x) => x.id === tid);
    units.push({
      id: mk('unit_', label), confidence: 0.6, provenance: { extractor: extId, source_ref: tid, details: { from_text: tid } },
      status: 'active', superseded_by: null, level_id: levelId, flags: [],
      label, label_raw: t.text, polygon_px: null, polygon_m: null, area_m2: null,
    });
  }

  // ---- scale ------------------------------------------------------------------------------------------------------------------------
  const sc = bundle.scale;
  const hasScale = sc.metresPerPx != null && sc.metresPerPx > 0;
  const scaleTextId = sc.evidenceTextKey ? textIdByKey.get(sc.evidenceTextKey) : null;
  const scale = {
    m_per_px: hasScale ? sc.metresPerPx : null, source: hasScale ? (SCALE_SOURCE[sc.source] ?? 'none') : 'none', confidence: hasScale ? clamp01(sc.confidence, 0) : 0,
    ...(sc.scaleText || scaleTextId ? { note: [sc.scaleText ? `"${sc.scaleText}"` : null, scaleTextId ? `text ${scaleTextId}` : null].filter(Boolean).join(' from ') } : {}),
  };

  // ---- level extent ----------------------------------------------------------------------------------------------------------------
  for (const lv of levels) {
    const geom = [...rooms, ...walls].filter((e) => e.level_id === lv.id || (levels.length === 1 && e.level_id == null)).flatMap((e) => e.polygon_px);
    if (geom.length) lv.bbox_px = bboxOf(geom);
  }

  const coverage = bundle.coverage.map((c) => ({
    category: c.category, region: 'page', status: c.status, extractor: extId, confidence: clamp01(c.confidence, 0), ...(c.note ? { note: c.note } : {}),
  }));

  const counts = { rooms: rooms.length, walls: walls.length, openings: openings.length, objects: objects.length, texts: texts.length, units: units.length, levels: levels.length };
  const document = {
    schema_version: SCHEMA_VERSION,
    drawing: {
      file: opts.file ?? ds.file,
      source: opts.source ?? ds.source,
      page_index: bundle.page.pageIndex ?? 0,
      page_count: bundle.page.pageCount ?? null,
      part: bundle.page.labelSuffix ?? (opts.multiPage ? `p${(bundle.page.pageIndex ?? 0) + 1}` : null),
      image: { width_px: Math.max(1, Math.round(bundle.page.widthPx)), height_px: Math.max(1, Math.round(bundle.page.heightPx)), dpi: bundle.page.renderDpi ?? null },
      route: ROUTE[bundle.format] ?? 'none',
      read_from: path.basename(opts.inputFilePath),
      transforms: bundle.page.transforms ?? [],
      scale,
      translator_version: TRANSLATOR_VERSION,
      created_at: now,
    },
    levels, units, rooms, walls, openings, objects, texts, edges: [], coverage,
    history: [{
      at: now, extractor: extId, action: 'extract',
      note: `${bundle.extractor.name}${bundle.extractor.version ? ` ${bundle.extractor.version}` : ''}: ${Object.entries(counts).map(([k, v]) => `${v} ${k}`).join(', ')}; ${rooms.length - pendingNames.length} room name(s) via glossary/geometry`,
      cost_usd: 0,
    }],
  };
  recomputeMetres(document);
  return {
    document,
    pending: { roomNames: pendingNames },
  };
}

/**
 * Set a room's type (+ is_outdoor) and record how the name was resolved in properties.name_resolution.
 * @param {any} room @param {string} type @param {Record<string, any>} resolution
 */
export function applyRoomType(room, type, resolution) {
  room.type = type;
  room.is_outdoor = OUTDOOR_TYPES.has(type);
  room.properties = { ...(room.properties ?? {}), name_resolution: Object.fromEntries(Object.entries(resolution).filter(([, v]) => v != null)) };
}

/** "ELUTUBA+KÖÖK (3-1)" style combined label as questions quote it. */
export function roomLabel(room) {
  if (room.name && room.number) return `${room.name} (${room.number})`;
  return room.name ?? room.number ?? null;
}
