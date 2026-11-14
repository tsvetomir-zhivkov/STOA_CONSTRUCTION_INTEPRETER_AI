/**
 * One vocabulary for every route. Schema v0.2 defines the enums for room types and object categories; any other name a
 * route (or an older translator, or a raster model) produces is mapped here, at the END of extraction, and anything that
 * does not map is REPORTED, never passed through silently.
 *
 * Pure ES module with no Node imports: blueprint-viewer copies this file (scripts/vendor.mjs) and applies the same tables
 * to every file it opens.
 */

export const ROOM_TYPES = Object.freeze(['bedroom', 'living', 'kitchen', 'dining', 'bathroom', 'wc', 'sauna', 'entry', 'corridor', 'closet', 'storage', 'utility', 'technical', 'stair', 'balcony', 'terrace', 'garage', 'office', 'outdoor', 'shaft', 'other', 'unknown']);
export const OBJECT_CATEGORIES = Object.freeze(['toilet', 'sink', 'shower', 'bathtub', 'sauna_bench', 'stair', 'stove', 'fridge', 'washing_machine', 'elevator', 'radiator', 'closet', 'other']);

/** alias -> schema room type (keys are folded: lower case, accents and separators normalised to "_") */
export const ROOM_TYPE_ALIASES = Object.freeze({
  living_room: 'living', livingroom: 'living', lounge: 'living', sitting_room: 'living', family_room: 'living',
  hall: 'entry', hallway: 'corridor', entrance: 'entry', lobby: 'entry', foyer: 'entry', vestibule: 'entry', draught_lobby: 'entry',
  toilet: 'wc', wc_room: 'wc', water_closet: 'wc', lavatory: 'wc', restroom: 'wc',
  bath: 'bathroom', washroom: 'bathroom', shower_room: 'bathroom', bath_room: 'bathroom',
  bed_room: 'bedroom', master_bedroom: 'bedroom', kitchenette: 'kitchen', dining_room: 'dining',
  walk_in_closet: 'closet', wardrobe: 'closet', dressing_room: 'closet',
  storeroom: 'storage', store: 'storage', pantry: 'storage', cellar: 'storage', basement_storage: 'storage',
  laundry: 'utility', laundry_room: 'utility', boiler_room: 'technical', plant_room: 'technical', mechanical: 'technical', electrical: 'technical',
  stairs: 'stair', staircase: 'stair', stairwell: 'stair', stair_hall: 'stair', elevator: 'technical', lift: 'technical',
  loggia: 'balcony', patio: 'terrace', deck: 'terrace', roof_terrace: 'terrace', garden: 'outdoor', yard: 'outdoor', porch: 'outdoor',
  carport: 'garage', study: 'office', workroom: 'office', duct: 'shaft', riser: 'shaft',
  room: 'other', undefined: 'unknown', background: 'unknown', none: 'unknown', '': 'unknown',
});

/** alias -> schema object category */
export const OBJECT_CATEGORY_ALIASES = Object.freeze({
  wc: 'toilet', toilet_seat: 'toilet', water_closet: 'toilet', closet_bowl: 'toilet', urinal: 'toilet',
  washbasin: 'sink', wash_basin: 'sink', basin: 'sink', lavatory: 'sink', handbasin: 'sink', kitchen_sink: 'sink',
  bath: 'bathtub', tub: 'bathtub', bath_tub: 'bathtub', showers_and_baths: 'bathtub',
  shower_tray: 'shower', cooker: 'stove', oven: 'stove', range: 'stove', hob: 'stove', refrigerator: 'fridge', freezer: 'fridge',
  washer: 'washing_machine', washingmachine: 'washing_machine', lift: 'elevator', heater: 'radiator',
  wardrobe: 'closet', cupboard: 'closet', cabinet: 'closet', stairs: 'stair', staircase: 'stair', stair_flight: 'stair',
  sauna: 'sauna_bench', sauna_bench_: 'sauna_bench', bench: 'sauna_bench',
});

/** generic category words a route may put in `category` while the real class sits in `subtype` */
const GENERIC = new Set(['', 'fixture', 'fixtures', 'object', 'objects', 'icon', 'symbol', 'item', 'other', 'unknown', 'element', 'equipment']);

export const fold = (s) => String(s ?? '').normalize('NFD').replace(/\p{M}+/gu, '').toLowerCase().trim().replace(/[\s\-/]+/g, '_');

/** @returns {{ value:string, mapped:boolean, known:boolean }} known = already a schema value; mapped = an alias was applied */
export function mapRoomType(t) {
  const f = fold(t);
  if (ROOM_TYPES.includes(f)) return { value: f, mapped: f !== t, known: true };
  if (f in ROOM_TYPE_ALIASES) return { value: ROOM_TYPE_ALIASES[f], mapped: true, known: true };
  return { value: 'unknown', mapped: false, known: false };
}

/** category + subtype -> schema category. A generic category with a meaningful subtype takes the subtype. */
export function mapObjectCategory(category, subtype = null) {
  const look = (x) => { const f = fold(x); return OBJECT_CATEGORIES.includes(f) && f !== 'other' ? f : OBJECT_CATEGORY_ALIASES[f] ?? null; };
  const c = look(category);
  if (c) return { value: c, mapped: c !== category, known: true, from: 'category' };
  if (GENERIC.has(fold(category))) {
    const s = look(subtype);
    if (s) return { value: s, mapped: true, known: true, from: 'subtype' };
    return { value: 'other', mapped: fold(category) !== 'other', known: true, from: 'generic' };
  }
  return { value: 'other', mapped: false, known: false, from: 'unmapped' };
}

/**
 * Map every room type and object category in a schema document onto the shared enums (in place).
 * Unmapped values become unknown / other, keep the raw value in properties.raw_type / raw_category and get the flag
 * `unmapped_vocabulary`; they are listed in the result so the caller can log them. Nothing is dropped.
 * @returns {{ changed: Array<{id:string, field:string, from:any, to:string}>, unmapped: Array<{id:string, field:string, value:any}> }}
 */
export function normalizeVocabulary(doc) {
  const changed = [], unmapped = [];
  const flag = (e) => { e.flags = [...new Set([...(e.flags ?? []), 'unmapped_vocabulary'])]; };
  for (const r of doc.rooms ?? []) {
    if (r.type == null) continue;
    const m = mapRoomType(r.type);
    if (!m.known) { unmapped.push({ id: r.id, field: 'type', value: r.type }); r.properties = { ...(r.properties ?? {}), raw_type: r.type }; flag(r); }
    if (m.value !== r.type) { changed.push({ id: r.id, field: 'type', from: r.type, to: m.value }); r.type = m.value; }
  }
  for (const o of doc.objects ?? []) {
    if (o.category == null) continue;
    const m = mapObjectCategory(o.category, o.subtype);
    if (!m.known) { unmapped.push({ id: o.id, field: 'category', value: o.category }); o.properties = { ...(o.properties ?? {}), raw_category: o.category }; flag(o); }
    if (m.value !== o.category) {
      changed.push({ id: o.id, field: 'category', from: o.category, to: m.value });
      if (m.from === 'subtype') o.subtype = null; // the subtype WAS the category
      o.category = m.value;
    }
  }
  return { changed, unmapped };
}
