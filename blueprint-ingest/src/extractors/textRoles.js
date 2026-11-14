/**
 * Deterministic text-role classification. Cheap regexes settle the obvious roles (area, scale note,
 * dimension, level title, unit label) so Gemini only ever sees the texts that are genuinely ambiguous.
 */

const NUM = String.raw`\d{1,4}(?:[.,]\d{1,3})?`;

/** "25,5 m2", "12.3 m²", "8 sqm", "120 sq ft" -> { number, unit } | null */
export function parseArea(raw) {
  const s = raw.replace(/\s+/g, ' ').trim();
  let m = new RegExp(String.raw`^(${NUM})\s*(?:m\s*[2²]|m\^2|㎡|sqm|sq\.?\s*m)(?![A-Za-z0-9])`, 'i').exec(s);
  if (m) return { number: toNumber(m[1]), unit: /** @type {'m2'} */ ('m2') };
  m = new RegExp(String.raw`^(${NUM})\s*(?:ft\s*[2²]|sq\.?\s*ft|sf)(?![A-Za-z0-9])`, 'i').exec(s);
  if (m) return { number: toNumber(m[1]), unit: /** @type {'ft2'} */ ('ft2') };
  return null;
}

/** "1:100", "Scale 1 : 50", "mittakaava 1:200", "M 1/100" -> denominator | null */
export function parseScaleDenominator(raw) {
  const m = /(?:^|\b|\s)(?:scale|mittakaava|skala|[eé]chelle|ma(?:ß|ss)stab|m\.?)?\s*[:=]?\s*1\s*[:/]\s*(\d{1,5})(?!\d)/i.exec(raw.trim());
  if (!m) return null;
  const n = Number(m[1]);
  return n >= 1 && n <= 50000 ? n : null;
}

/** "3200", "3.20 m", "120 cm", "2400mm" -> { number, unit? } | null. Bare numbers need >= 3 digits. */
export function parseDimension(raw) {
  const s = raw.replace(/\s+/g, ' ').trim();
  let m = new RegExp(String.raw`^(${NUM})\s*(mm|cm|m)$`, 'i').exec(s);
  if (m) return { number: toNumber(m[1]), unit: /** @type {'mm'|'cm'|'m'} */ (m[2].toLowerCase()) };
  m = /^(\d{3,5})$/.exec(s);
  if (m) return { number: Number(m[1]) };
  m = /^(\d{1,2})['′]\s*-?\s*(\d{1,2})?(?:["″])?$/.exec(s);
  if (m) return { number: Number(m[1]) + (m[2] ? Number(m[2]) / 12 : 0), unit: /** @type {'ft'} */ ('ft') };
  return null;
}

const LEVEL_RE = /^(?:(?:level|floor|storey|story|etage|étage|kerros|niveau)\s*[-+]?\d{1,2}[a-z]?|\d{1,2}\.?\s*(?:krs|kerros|floor|etage|étage|fl\.?)|krs\.?\s*\d{1,2}|(?:ground|first|second|third|top|upper|lower|main)\s+floor|ground level|basement|cellar|attic|kellari|ullakko|vind)$/i;

/** "Whg. A", "Wohnung 3", "Apt B2", "Asunto A 12", "Unit 4" -> { raw, label } | null */
const UNIT_RE = /^(?:whg\.?|wohnung|apt\.?|apartment|appartement|unit|flat|asunto|as\.?|lgh\.?|lägenhet|korter)\s*([A-Z]{1,2}\s?\d{0,3}|\d{1,3}[A-Z]?)$/i;
/** @param {string} raw */
export function parseUnitLabel(raw) {
  const m = UNIT_RE.exec(cleanText(raw));
  return m ? { raw: cleanText(raw), label: m[1].replace(/\s+/g, '').toUpperCase() } : null;
}

/** Room numbers as drawn: "2.10", "K08", "3-1", "E.012", "1.04a". Only meaningful inside a room polygon. */
export const looksLikeRoomNumber = (raw) => /^(?:[A-Z]{1,3}[.-]?)?\d{1,3}(?:[.-]\d{1,3})?[a-z]?$/i.test(cleanText(raw)) && /\d/.test(raw) && !/^\d{3,}$/.test(cleanText(raw));

/** @param {string} raw */
export const looksLikeLevel = (raw) => LEVEL_RE.test(raw.trim()) && raw.trim().length <= 30;

/**
 * Normalise whitespace and strip control characters; keep the original otherwise.
 * @param {string} raw
 */
export function cleanText(raw) {
  // eslint-disable-next-line no-control-regex
  return raw.replace(/[\u0000-\u0008\u000B\u000C\u000E-\u001F]/g, '').replace(/\s+/g, ' ').trim();
}

/** Has at least two letters in a row (so it can be a name rather than a number/symbol). */
export const hasWord = (s) => /\p{L}{2,}/u.test(s) || /^\p{L}\p{L}?$/u.test(s.trim());

/**
 * Decide a text's role from its content alone.
 * `null` means "cannot tell from content" (caller may still make it a room label by geometry).
 * @param {string} raw
 * Roles are the schema v0.2 text.role values.
 * @returns {{ role: 'area'|'scale_note'|'dimension'|'level_title'|'unit_label'|null, value: null | { number:number, unit?: string }, unitLabel?: string }}
 */
export function classifyTextContent(raw) {
  const s = cleanText(raw);
  const area = parseArea(s);
  if (area) return { role: 'area', value: area };
  const den = parseScaleDenominator(s);
  if (den && /^(?:[A-Za-zÀ-ÿ.]+\s*)?[:=]?\s*1\s*[:/]\s*\d+$/.test(s)) return { role: 'scale_note', value: { number: den, unit: 'ratio' } };
  const dim = parseDimension(s);
  if (dim) return { role: 'dimension', value: dim };
  if (looksLikeLevel(s)) return { role: 'level_title', value: null };
  const unit = parseUnitLabel(s);
  if (unit) return { role: 'unit_label', value: null, unitLabel: unit.label };
  return { role: null, value: null };
}

function toNumber(str) {
  return Number(str.replace(',', '.'));
}

/** m² value of an area measurement (null for unknown units). */
export function areaToM2(value) {
  if (!value) return null;
  if (value.unit === 'm2') return value.number;
  if (value.unit === 'ft2') return value.number * 0.09290304;
  return null;
}
