/**
 * psetUnits.js — IFC property sets are copied raw and their units are NOT consistent (the clinic model has
 * Dimensions.Length in millimetres and Dimensions.Outer Diameter in feet, in the same pset). Before any pset length is
 * shown as a length, its unit is checked against geometry measured from the same elements:
 *   pipes lying along a plan axis: plan length of the geometry vs the pset "Length", plan width vs "Outer Diameter".
 * The median ratio pset / measured picks the unit (m 1, cm 100, mm 1000, ft 3.281, in 39.37); it is accepted only when
 * it is within 10 % of that unit and at least 60 % of the samples agree. Everything else stays "raw".
 */

export const UNITS = Object.freeze([['m', 1], ['cm', 100], ['mm', 1000], ['ft', 1 / 0.3048], ['in', 1 / 0.0254]]);

/** pset entries whose names say what they measure */
export const CHECKS = Object.freeze([
  { prop: 'Length', re: /^length$/i, measure: 'length' },
  { prop: 'Outer Diameter', re: /^(outer[\s_]*diameter|outside[\s_]*diameter)$/i, measure: 'width' },
]);

/** first numeric pset value whose key matches re: { pset, key, value } */
export function findPset(psets, re) {
  for (const [pset, props] of Object.entries(psets ?? {})) {
    for (const [key, v] of Object.entries(props ?? {})) if (re.test(key) && typeof v === 'number' && Number.isFinite(v) && v > 0) return { pset, key, value: v };
  }
  return null;
}

/**
 * @param {Array<{ psets: any, measured: { length:number, width:number, horizontal:boolean } | null }>} samples
 * @returns {Array<{ prop:string, key:string|null, unit:string|null, toMetres:number|null, median:number|null, n:number, agree:number, verdict:'verified'|'raw' }>}
 */
export function checkPsetUnits(samples) {
  const out = [];
  for (const c of CHECKS) {
    // one property only: the most common "pset.key" matching the name (different psets may hold differently scaled values)
    const freq = new Map();
    for (const s of samples) for (const [pset, props] of Object.entries(s.psets ?? {})) for (const [key, v] of Object.entries(props ?? {})) {
      if (c.re.test(key) && typeof v === 'number' && v > 0) freq.set(`${pset}\u0000${key}`, (freq.get(`${pset}\u0000${key}`) ?? 0) + 1);
    }
    const top = [...freq].sort((a, b) => b[1] - a[1])[0]?.[0];
    if (!top) { out.push({ prop: c.prop, key: null, unit: null, toMetres: null, median: null, n: 0, agree: 0, verdict: 'raw' }); continue; }
    const [pset, key] = top.split('\u0000');
    const ratios = [];
    for (const s of samples) {
      const v = s.psets?.[pset]?.[key], m = s.measured?.[c.measure];
      if (!s.measured?.horizontal || typeof v !== 'number' || !(v > 0) || !(m > 1e-3)) continue;
      ratios.push(v / m);
    }
    const label = `${pset}.${key}`;
    if (!ratios.length) { out.push({ prop: c.prop, key: label, unit: null, toMetres: null, median: null, n: 0, agree: 0, verdict: 'raw' }); continue; }
    const sorted = [...ratios].sort((a, b) => a - b), median = sorted[sorted.length >> 1];
    const [unit, f] = UNITS.reduce((best, u) => (Math.abs(Math.log(median / u[1])) < Math.abs(Math.log(median / best[1])) ? u : best), UNITS[0]);
    const agree = ratios.filter((r) => Math.abs(Math.log(r / f)) < Math.log(1.1)).length / ratios.length;
    const ok = Math.abs(Math.log(median / f)) < Math.log(1.1) && agree >= 0.6;
    out.push({ prop: c.prop, key: label, pset, psetKey: key, unit: ok ? unit : null, toMetres: ok ? 1 / f : null, median, n: ratios.length, agree, verdict: ok ? 'verified' : 'raw' });
  }
  return out;
}

/**
 * Plan measurements of a pipe-like element. From real geometry (an IFC element box, metres) when available: horizontal
 * and lying along a plan axis when the vertical extent and the shorter plan extent are both small and similar.
 */
export function measureFromBox(dims) {
  if (!dims) return null;
  const L = Math.max(dims.x, dims.z), w = Math.min(dims.x, dims.z), h = dims.y;
  const horizontal = L > 4 * Math.max(w, h) && Math.abs(w - h) <= 0.25 * Math.max(w, h) + 0.005;
  return { length: L, width: (w + h) / 2, horizontal };
}

/** same from a 2D footprint's minimum-area rectangle ({ length, width } in metres): no vertical information */
export function measureFromFootprint(rect) {
  if (!rect) return null;
  return { length: rect.length, width: rect.width, horizontal: rect.length > 4 * rect.width };
}
