/**
 * Fixture class from a DXF block name and/or layer name (case- and accent-insensitive substring match).
 * Block name wins over layer name. No match -> null (the object stays category "other").
 * The original block name is always kept in properties.source_block by the extractor.
 */

import { foldText } from '../mappers/glossary.js';

const RULES = [
  ['toilet', ['wc', 'toilette', 'toilet', 'klo']],
  ['sink', ['lavabo', 'waschbecken', 'washbasin', 'basin', 'washstand', 'sink', 'evier', 'lavandino']],
  ['shower', ['douche', 'dusche', 'shower']],
  ['bathtub', ['baignoire', 'wanne', 'bathtub', 'bath']],
  ['stove', ['cuisiniere', 'herd', 'stove', 'cooker']],
  ['fridge', ['frigo', 'kuhlschrank', 'fridge']],
  ['washing_machine', ['lave-linge', 'waschmaschine', 'washer']],
  ['elevator', ['ascenseur', 'aufzug', 'lift', 'elevator']],
  ['radiator', ['radiateur', 'heizkorper', 'radiator']],
  // last: 'kuhlschrank' (fridge) contains 'schrank'
  ['closet', ['placard', 'schrank', 'closet', 'wardrobe']],
];

/** @param {string|null|undefined} name @returns {string|null} */
export function fixtureCategory(name) {
  if (!name) return null;
  const s = foldText(name);
  for (const [cat, words] of RULES) if (words.some((w) => s.includes(w))) return cat;
  return null;
}

/** @param {{ block?: string|null, layer?: string|null }} src @returns {{ category: string|null, from: 'block'|'layer'|null }} */
export function classifyFixture({ block, layer }) {
  const b = fixtureCategory(block);
  if (b) return { category: b, from: 'block' };
  const l = fixtureCategory(layer);
  return l ? { category: l, from: 'layer' } : { category: null, from: null };
}
