/**
 * Code-written metre fields (schema v0.2). Pixels are the source of truth; every `*_m` / `*_m2` field derived from geometry
 * is written here from `drawing.scale.m_per_px`. Never edit these by hand or let a model write them.
 *
 * Pixel-only inputs that the schema has no field for live in `properties` (`thickness_px`, `width_px`), so a scale change
 * can recompute their metre values.
 *
 * Room area flags: when a written and a computed area differ by more than 10 % the room gets the flags
 * `area_mismatch` + `check_this` (and `properties.area_difference_pct`). Code removes them again when the mismatch is gone,
 * but leaves a `check_this` set by someone else alone.
 */

import { bboxOfPoints, polygonArea, polylineLength, round } from '../geometry/geom.js';

const r3 = (n) => round(n, 3);
const median = (a) => { const s = [...a].sort((x, y) => x - y); const m = Math.floor(s.length / 2); return s.length % 2 ? s[m] : (s[m - 1] + s[m]) / 2; };
const CROSS_CHECK_SEP = ' | cross-check: ';
export const AREA_MISMATCH_PCT = 10;

/**
 * Recompute all metre-derived fields in place.
 * @param {any} doc schema document
 * @returns {any} the same document
 */
export function recomputeMetres(doc) {
  const k = doc.drawing?.scale?.m_per_px ?? null;
  const has = typeof k === 'number' && k > 0;
  const mpt = (p) => (p.length > 2 ? [r3(p[0] * k), r3(p[1] * k), p[2]] : [r3(p[0] * k), r3(p[1] * k)]);
  const mpoly = (poly) => (has && Array.isArray(poly) ? poly.map(mpt) : null);
  const areaPx = (poly) => (Array.isArray(poly) && poly.length >= 3 ? polygonArea(poly) : 0);

  const ratios = [];
  for (const r of doc.rooms ?? []) {
    r.polygon_m = mpoly(r.polygon_px);
    r.holes_m = has ? (r.holes_px ?? []).map((h) => h.map(mpt)) : [];
    const net = areaPx(r.polygon_px) - (r.holes_px ?? []).reduce((s, h) => s + areaPx(h), 0);
    r.area_computed_m2 = has && r.polygon_px ? round(net * k * k, 2) : null;
    r.area_m2 = r.area_written_m2 ?? r.area_computed_m2 ?? null;
    r.volume_m3 = r.area_m2 != null && r.height_m != null ? round(r.area_m2 * r.height_m, 2) : (r.volume_m3 ?? null);
    areaFlags(r, ratios);
  }
  for (const w of doc.walls ?? []) {
    w.polygon_m = mpoly(w.polygon_px);
    // a length given by the source (no axis line) is kept
    w.length_m = has && w.polyline_px?.length >= 2 ? r3(polylineLength(w.polyline_px) * k) : (w.length_m ?? null);
    const tpx = w.properties?.thickness_px;
    if (tpx != null) w.thickness_m = has ? r3(tpx * k) : null;
  }
  for (const o of doc.openings ?? []) {
    o.polygon_m = mpoly(o.polygon_px);
    const wpx = o.properties?.width_px;
    if (wpx != null) o.width_m = has ? r3(wpx * k) : null;
  }
  for (const o of doc.objects ?? []) {
    o.polygon_m = mpoly(o.polygon_px);
    o.area_m2 = has && o.polygon_px ? round(areaPx(o.polygon_px) * k * k, 3) : null;
    // stair flights / landings keep pixels as the source too (properties.flights[].*_px, properties.landings[].polygon_px)
    for (const f of Array.isArray(o.properties?.flights) ? o.properties.flights : []) {
      if (f.start_px) f.start_m = has ? mpt(f.start_px) : null;
      for (const q of ['width', 'going']) if (f[`${q}_px`] != null) f[`${q}_m`] = has ? r3(f[`${q}_px`] * k) : null;
    }
    for (const l of Array.isArray(o.properties?.landings) ? o.properties.landings : []) if (l.polygon_px) l.polygon_m = mpoly(l.polygon_px);
  }
  for (const u of doc.units ?? []) {
    u.polygon_m = mpoly(u.polygon_px);
    if (u.polygon_px) u.area_m2 = has ? round(areaPx(u.polygon_px) * k * k, 2) : null;
  }

  const sc = doc.drawing?.scale;
  if (sc) {
    const baseNote = (sc.note ?? '').split(CROSS_CHECK_SEP)[0].replace(/^cross-check: .*$/, '');
    const check = ratios.length ? `${ratios.length} room(s), median computed/written area = ${round(median(ratios), 3)} (written areas only cross-check the scale, never set it)` : '';
    const note = check ? (baseNote ? `${baseNote}${CROSS_CHECK_SEP}${check}` : `cross-check: ${check}`) : baseNote;
    if (note) sc.note = note; else delete sc.note;
  }
  return doc;
}

function areaFlags(r, ratios) {
  const flags = new Set(r.flags ?? []);
  const hadMismatch = flags.has('area_mismatch');
  flags.delete('area_mismatch');
  if (hadMismatch) flags.delete('check_this');
  const props = r.properties ?? {};
  delete props.area_difference_pct;
  if (r.area_computed_m2 != null && r.area_written_m2 != null && r.area_written_m2 > 0) {
    const diff = round(((r.area_computed_m2 - r.area_written_m2) / r.area_written_m2) * 100, 1);
    props.area_difference_pct = diff;
    if (Math.abs(diff) > AREA_MISMATCH_PCT) { flags.add('area_mismatch'); flags.add('check_this'); }
    if (r.status !== 'superseded') ratios.push(r.area_computed_m2 / r.area_written_m2);
  }
  r.flags = [...flags];
  if (Object.keys(props).length) r.properties = props; else delete r.properties;
}

/** @param {number[][]} poly */
export const bboxOfPolygon = (poly) => bboxOfPoints(poly).map((v) => round(v, 2));
