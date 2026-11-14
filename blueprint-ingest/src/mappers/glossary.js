/**
 * Built-in multilingual room glossary. Resolves common room labels (and abbreviations such as the
 * Finnish "MH") deterministically, so Gemini is only asked about names that are not in here.
 * `type` values must stay within the schema's room.type enum (schema v0.2).
 */

/** @typedef {'bedroom'|'living'|'kitchen'|'dining'|'bathroom'|'wc'|'sauna'|'entry'|'corridor'|'closet'|'storage'|'utility'|'technical'|'stair'|'balcony'|'terrace'|'garage'|'office'|'outdoor'|'shaft'|'other'|'unknown'} RoomType */

export const ROOM_TYPES = /** @type {const} */ ([
  'bedroom', 'living', 'kitchen', 'dining', 'bathroom', 'wc', 'sauna', 'entry', 'corridor', 'closet', 'storage',
  'utility', 'technical', 'stair', 'balcony', 'terrace', 'garage', 'office', 'outdoor', 'shaft', 'other', 'unknown',
]);

export const WET_TYPES = new Set(['bathroom', 'wc', 'sauna', 'utility']);

/** room types that are outdoor space (schema field `is_outdoor`) */
export const OUTDOOR_TYPES = new Set(['balcony', 'terrace', 'outdoor']);

/**
 * [language, term, type, normalizedEnglish, expandedForm?]
 * @type {Array<[string, string, RoomType, string, string?]>}
 */
const ENTRIES = [
  // English
  ['en', 'bedroom', 'bedroom', 'bedroom'], ['en', 'bed', 'bedroom', 'bedroom'], ['en', 'master bedroom', 'bedroom', 'master bedroom'],
  ['en', 'living room', 'living', 'living room'], ['en', 'living', 'living', 'living room'], ['en', 'lounge', 'living', 'living room'],
  ['en', 'kitchen', 'kitchen', 'kitchen'], ['en', 'kitchenette', 'kitchen', 'kitchenette'], ['en', 'dining', 'dining', 'dining room'], ['en', 'dining room', 'dining', 'dining room'],
  ['en', 'bathroom', 'bathroom', 'bathroom'], ['en', 'bath', 'bathroom', 'bathroom'], ['en', 'shower', 'bathroom', 'shower room'],
  ['en', 'toilet', 'wc', 'toilet'], ['en', 'wc', 'wc', 'toilet'], ['en', 'lavatory', 'wc', 'toilet'], ['en', 'restroom', 'wc', 'toilet'],
  ['en', 'sauna', 'sauna', 'sauna'], ['en', 'laundry', 'utility', 'laundry room'], ['en', 'utility', 'utility', 'utility room'], ['en', 'utility room', 'utility', 'utility room'],
  ['en', 'hall', 'entry', 'hall'], ['en', 'entry', 'entry', 'entrance'], ['en', 'hallway', 'entry', 'hallway'], ['en', 'entrance', 'entry', 'entrance hall'], ['en', 'foyer', 'entry', 'foyer'], ['en', 'lobby', 'entry', 'lobby'],
  ['en', 'corridor', 'corridor', 'corridor'], ['en', 'closet', 'closet', 'closet'], ['en', 'wardrobe', 'closet', 'wardrobe'], ['en', 'walk-in closet', 'closet', 'walk-in closet'],
  ['en', 'storage', 'storage', 'storage'], ['en', 'store', 'storage', 'storage'], ['en', 'pantry', 'storage', 'pantry'],
  ['en', 'balcony', 'balcony', 'balcony'], ['en', 'terrace', 'terrace', 'terrace'], ['en', 'patio', 'terrace', 'patio'],
  ['en', 'office', 'office', 'office'], ['en', 'study', 'office', 'study'], ['en', 'stair', 'stair', 'staircase'], ['en', 'stairs', 'stair', 'staircase'], ['en', 'staircase', 'stair', 'staircase'],
  ['en', 'technical', 'technical', 'technical room'], ['en', 'plant room', 'technical', 'plant room'], ['en', 'boiler room', 'technical', 'boiler room'], ['en', 'garage', 'garage', 'garage'],
  ['en', 'loggia', 'balcony', 'loggia'], ['en', 'garden', 'outdoor', 'garden'], ['en', 'yard', 'outdoor', 'yard'], ['en', 'shaft', 'shaft', 'shaft'],
  // Finnish
  ['fi', 'mh', 'bedroom', 'bedroom', 'makuuhuone'], ['fi', 'makuuhuone', 'bedroom', 'bedroom'], ['fi', 'oh', 'living', 'living room', 'olohuone'], ['fi', 'olohuone', 'living', 'living room'],
  ['fi', 'kt', 'kitchen', 'kitchen', 'keittiö'], ['fi', 'keittiö', 'kitchen', 'kitchen'], ['fi', 'kk', 'kitchen', 'kitchenette', 'keittokomero'], ['fi', 'keittokomero', 'kitchen', 'kitchenette'],
  ['fi', 'ruokailutila', 'dining', 'dining room'], ['fi', 'ruokailu', 'dining', 'dining room'], ['fi', 'rt', 'dining', 'dining room', 'ruokailutila'],
  ['fi', 'kh', 'bathroom', 'bathroom', 'kylpyhuone'], ['fi', 'kylpyhuone', 'bathroom', 'bathroom'], ['fi', 'ph', 'bathroom', 'shower room', 'pesuhuone'], ['fi', 'pesuhuone', 'bathroom', 'shower room'],
  ['fi', 'wc', 'wc', 'toilet'], ['fi', 'vh', 'closet', 'closet', 'vaatehuone'], ['fi', 'vaatehuone', 'closet', 'closet'], ['fi', 'et', 'entry', 'entrance hall', 'eteinen'], ['fi', 'eteinen', 'entry', 'entrance hall'],
  ['fi', 'var', 'storage', 'storage', 'varasto'], ['fi', 'varasto', 'storage', 'storage'], ['fi', 'parveke', 'balcony', 'balcony'], ['fi', 'terassi', 'terrace', 'terrace'],
  ['fi', 'th', 'office', 'office', 'työhuone'], ['fi', 'työhuone', 'office', 'office'], ['fi', 'tk', 'technical', 'technical room', 'tekninen tila'], ['fi', 'tekninen tila', 'technical', 'technical room'],
  ['fi', 'khh', 'utility', 'utility room', 'kodinhoitohuone'], ['fi', 'kodinhoitohuone', 'utility', 'utility room'], ['fi', 'sauna', 'sauna', 'sauna'], ['fi', 'pukuhuone', 'closet', 'changing room'],
  ['fi', 'käytävä', 'corridor', 'corridor'], ['fi', 'porras', 'stair', 'staircase'], ['fi', 'porrashuone', 'stair', 'stairwell'], ['fi', 'autotalli', 'garage', 'garage'], ['fi', 'tupa', 'living', 'living room'],
  ['fi', 'kuilu', 'shaft', 'shaft'], ['fi', 'piha', 'outdoor', 'yard'],
  // Estonian
  ['et', 'elutuba', 'living', 'living room'], ['et', 'köök', 'kitchen', 'kitchen'], ['et', 'magamistuba', 'bedroom', 'bedroom'], ['et', 'vannituba', 'bathroom', 'bathroom'],
  ['et', 'esik', 'entry', 'entrance hall'], ['et', 'koridor', 'corridor', 'corridor'], ['et', 'panipaik', 'storage', 'storage'], ['et', 'rõdu', 'balcony', 'balcony'], ['et', 'saun', 'sauna', 'sauna'],
  // Swedish
  ['sv', 'sovrum', 'bedroom', 'bedroom'], ['sv', 'vardagsrum', 'living', 'living room'], ['sv', 'kök', 'kitchen', 'kitchen'], ['sv', 'badrum', 'bathroom', 'bathroom'],
  ['sv', 'tvättstuga', 'utility', 'laundry room'], ['sv', 'förråd', 'storage', 'storage'], ['sv', 'balkong', 'balcony', 'balcony'], ['sv', 'kontor', 'office', 'office'], ['sv', 'trappa', 'stair', 'staircase'], ['sv', 'hall', 'entry', 'hall'],
  // German
  ['de', 'schlafzimmer', 'bedroom', 'bedroom'], ['de', 'wohnzimmer', 'living', 'living room'], ['de', 'küche', 'kitchen', 'kitchen'], ['de', 'bad', 'bathroom', 'bathroom'], ['de', 'badezimmer', 'bathroom', 'bathroom'],
  ['de', 'flur', 'corridor', 'corridor'], ['de', 'diele', 'entry', 'hall'], ['de', 'abstellraum', 'storage', 'storage'], ['de', 'balkon', 'balcony', 'balcony'], ['de', 'büro', 'office', 'office'], ['de', 'treppe', 'stair', 'staircase'],
  ['de', 'hwr', 'utility', 'utility room', 'hauswirtschaftsraum'], ['de', 'esszimmer', 'dining', 'dining room'], ['de', 'garage', 'garage', 'garage'],
  ['de', 'zimmer', 'bedroom', 'room'], ['de', 'wohnen', 'living', 'living room'], ['de', 'essen', 'dining', 'dining room'], ['de', 'kochen', 'kitchen', 'kitchen'], ['de', 'dusche', 'bathroom', 'shower room'],
  ['de', 'reduit', 'storage', 'storage'], ['de', 'keller', 'storage', 'cellar'], ['de', 'kellerabteil', 'storage', 'cellar compartment'], ['de', 'vorplatz', 'entry', 'entrance hall'], ['de', 'eingang', 'entry', 'entrance'],
  ['de', 'loggia', 'balcony', 'loggia'], ['de', 'terrasse', 'terrace', 'terrace'], ['de', 'sitzplatz', 'outdoor', 'patio'], ['de', 'garten', 'outdoor', 'garden'], ['de', 'schacht', 'shaft', 'shaft'],
  ['de', 'waschküche', 'utility', 'laundry room'], ['de', 'technik', 'technical', 'technical room'], ['de', 'treppenhaus', 'stair', 'stairwell'], ['de', 'korridor', 'corridor', 'corridor'],
  // French
  ['fr', 'chambre', 'bedroom', 'bedroom'], ['fr', 'séjour', 'living', 'living room'], ['fr', 'salon', 'living', 'living room'], ['fr', 'cuisine', 'kitchen', 'kitchen'], ['fr', 'salle de bain', 'bathroom', 'bathroom'],
  ['fr', 'sdb', 'bathroom', 'bathroom', 'salle de bain'], ['fr', 'couloir', 'corridor', 'corridor'], ['fr', 'entrée', 'entry', 'entrance hall'], ['fr', 'placard', 'closet', 'closet'], ['fr', 'cellier', 'storage', 'storage'],
  ['fr', 'balcon', 'balcony', 'balcony'], ['fr', 'bureau', 'office', 'office'], ['fr', 'escalier', 'stair', 'staircase'], ['fr', 'wc', 'wc', 'toilet'],
];

/** @type {Map<string, {language:string, term:string, type:RoomType, normalized:string, expanded:string|null}>} */
const INDEX = new Map();
for (const [language, term, type, normalized, expanded] of ENTRIES) {
  // first entry wins, so English "wc" beats the later fi/fr duplicates
  if (!INDEX.has(term)) INDEX.set(term, { language, term, type, normalized, expanded: expanded ?? null });
}

/** Lower-case, trim punctuation and trailing numbering ("MH 2", "Bedroom 3." -> "mh", "bedroom"). @param {string} raw */
export function normalizeKey(raw) {
  return raw
    .toLowerCase()
    .replace(/[\s_]+/g, ' ')
    .replace(/[.,:;()]+/g, '')
    .replace(/\s*[-/#]?\s*\d+[a-z]?$/i, '')
    .trim();
}

/**
 * @param {string|null|undefined} raw
 * @returns {{ type: RoomType, normalized: string, expanded: string|null, language: string, is_wet_room: boolean, glossary_term_id: string } | null}
 */
export function lookupRoomName(raw) {
  if (!raw) return null;
  const key = normalizeKey(raw);
  const hit = INDEX.get(key);
  if (!hit) return null;
  return {
    type: hit.type,
    normalized: hit.normalized,
    expanded: hit.expanded,
    language: hit.language,
    is_wet_room: WET_TYPES.has(hit.type),
    glossary_term_id: `${hit.language}:${raw.trim().toUpperCase().replace(/\s+/g, ' ').slice(0, 24)}`,
  };
}
