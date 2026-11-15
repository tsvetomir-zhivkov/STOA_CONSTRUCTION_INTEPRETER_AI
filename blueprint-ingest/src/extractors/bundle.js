/**
 * The Extraction Bundle: the intermediate representation every extractor (IFC / PDF / DXF / SVG) returns.
 *
 * Design rule: extractors produce GEOMETRY and RAW FACTS deterministically, already in the schema's pixel
 * space. Semantics (room types, text roles...) are added afterwards by deterministic code (glossary, regexes, geometry). `buildDocument()` turns a bundle into a schema-shaped document.
 */

/** @typedef {[number, number]} Pt */
/** @typedef {[number, number, number, number]} BBox */

/**
 * Common optional fields on every bundle element (they end up in the schema's entity base):
 *   sourceRef   DXF handle, PDF item index, IFC GlobalId... (default: the key)  -> provenance.source_ref
 *   layer       CAD layer                                                       -> provenance.layer
 *   properties  free-form extras, e.g. IFC psets                                -> properties
 *
 * @typedef {{ sourceRef?: string|null, layer?: string|null, properties?: Record<string, any>|null }} BundleCommon
 *
 * @typedef {BundleCommon & {
 *   key: string, polygonPx: Pt[], rawName?: string|null, rawNumber?: string|null,
 *   writtenAreaM2?: number|null, heightM?: number|null, floorElevationM?: number|null, confidence?: number,
 * }} BundleRoom  rawName: name carried by the source itself (IFC LongName, SVG class...); rawNumber: e.g. IFC Name "2.10"
 *
 * @typedef {BundleCommon & {
 *   key: string, polygonPx: Pt[], centerlinePx?: Pt[]|null, thicknessPx?: number|null, isExterior?: boolean|null,
 *   isLoadBearing?: boolean|null, heightM?: number|null, netSideAreaM2?: number|null, netVolumeM3?: number|null, confidence?: number,
 * }} BundleWall
 *
 * @typedef {BundleCommon & {
 *   key: string, kind: 'door'|'window'|'doorway', subtype?: string|null, polygonPx: Pt[], widthPx?: number|null, heightM?: number|null,
 *   wallKey?: string|null, sillHeightM?: number|null, headHeightM?: number|null, isEntrance?: boolean|null, confidence?: number,
 * }} BundleOpening  kind 'doorway' = opening without a door leaf (never counted as a door)
 *
 * @typedef {BundleCommon & {
 *   key: string, raw: string, bboxPx: BBox, rotationDeg?: number, fontSizePx?: number,
 *   roleHint?: 'dimension'|'scale_note'|'area'|'level_title'|null, value?: {number:number, unit?:string}|null, confidence?: number,
 * }} BundleText  roleHint forces a role (e.g. DXF DIMENSION entities)
 *
 * @typedef {BundleCommon & {
 *   key: string, category: string, name?: string|null, polygonPx: Pt[], netVolumeM3?: number|null, confidence?: number,
 * }} BundleObject  category: free lower-case noun; the builder maps it onto the schema enum and keeps the original as subtype
 *
 * @typedef {Object} BundleCoverage
 * @property {string} category rooms, walls, doors, windows, text, fixtures, stairs, symbols, dimensions, scale, legend...
 * @property {'analysed'|'partial'|'not_analysed'} status
 * @property {number} confidence
 * @property {string} [note]
 *
 * @typedef {Object} BundleLevel the storey this bundle belongs to (IFC), or a level found on the page
 * @property {string} name exactly as in the source
 * @property {string|null} [sourceRef] IFC GlobalId
 * @property {number|null} [elevationM]
 * @property {number|null} [heightM]
 * @property {number} [confidence]
 * @property {string} [key] identity within a multi-level bundle (elements point to it through `levelKey`)
 * @property {string|null} [longName]
 *
 * @typedef {{ kind:'resize'|'deskew'|'affine'|'upscale'|'invert'|'other', from?:string, to?:string, scale_x?:number, scale_y?:number, angle_deg?:number, matrix?:number[][], residual_px?:number, note?:string }} Transform
 *   schema v0.2 drawing.transforms item (already in schema shape)
 *
 * @typedef {Object} Bundle
 * @property {'ifc'|'pdf'|'dxf'|'svg'} format
 * @property {{ id:string, name:string, version:string|null }} extractor  id is the provenance.extractor value, e.g. "vector_ifc"
 * @property {{ widthPx:number, heightPx:number, renderDpi:number|null, pageIndex:number, pageCount?:number|null, title?:string|null, labelSuffix?:string|null, transforms?: Transform[] }} page
 * @property {BundleLevel|null} [level] the single level of a page / storey bundle
 * @property {BundleLevel[]} [levels] several levels (an IFC building, one JSON per building); each element then carries `levelKey`
 * @property {{ metresPerPx:number|null, source:'vector_units'|'scale_note'|'dimension_lines'|'door_width'|'none', confidence:number, scaleText?:string|null, evidenceTextKey?:string|null }} scale
 * @property {BundleRoom[]} rooms
 * @property {BundleWall[]} walls
 * @property {BundleOpening[]} openings
 * @property {BundleText[]} texts
 * @property {BundleObject[]} objects
 * @property {Array<BundleCommon & { key:string, polygonPx:number[][], confidence?:number }>} [units] apartment outlines (label from the unit-label text inside)
 * @property {BundleCoverage[]} coverage
 * @property {Record<string, any>} semantic compact extras that have no home in the schema (materials, layer names...)
 * @property {Record<string, number|string|boolean>} stats
 * @property {string[]} warnings
 */

/**
 * @param {Partial<Bundle> & Pick<Bundle,'format'>} partial
 * @returns {Bundle}
 */
export function emptyBundle(partial) {
  return {
    extractor: { id: `vector_${partial.format}`, name: `${partial.format} vector extractor`, version: null },
    page: { widthPx: 1, heightPx: 1, renderDpi: null, pageIndex: 0, pageCount: 1, transforms: [] },
    level: null,
    scale: { metresPerPx: null, source: 'none', confidence: 0 },
    rooms: [], walls: [], openings: [], texts: [], objects: [], units: [], coverage: [],
    semantic: {}, stats: {}, warnings: [],
    ...partial,
  };
}

/**
 * Pixel canvas for vector sources that have no natural raster: world extents -> pixels with a margin.
 * Y is flipped because the schema has origin top-left, y down.
 *
 * @param {{minX:number,minY:number,maxX:number,maxY:number}} ext extents in world units
 * @param {number} unitsPerMetre world units per metre (e.g. 1000 for millimetres)
 * @param {number} targetSide desired longest side in pixels
 * @param {number} [marginFrac]
 */
export function makeCanvas(ext, unitsPerMetre, targetSide, marginFrac = 0.02) {
  const w = Math.max(ext.maxX - ext.minX, 1e-9), h = Math.max(ext.maxY - ext.minY, 1e-9);
  const margin = Math.max(w, h) * marginFrac;
  const ox = ext.minX - margin, oy = ext.maxY + margin;
  const spanX = w + 2 * margin, spanY = h + 2 * margin;
  // choose a "nice" pixels-per-unit (2 significant digits) so re-runs and neighbouring files agree
  const raw = targetSide / Math.max(spanX, spanY);
  const ppu = Number(raw.toPrecision(2));
  return {
    pxPerUnit: ppu,
    widthPx: Math.max(1, Math.ceil(spanX * ppu)),
    heightPx: Math.max(1, Math.ceil(spanY * ppu)),
    /** @param {number} x @param {number} y @returns {Pt} */
    toPx: (x, y) => [(x - ox) * ppu, (oy - y) * ppu],
    metresPerPx: 1 / unitsPerMetre / ppu,
    /** schema drawing.transforms entry: px = M * [x, y, 1] */
    transform: /** @type {Transform} */ ({
      kind: 'affine', from: 'model_units', to: 'page_px',
      matrix: [[ppu, 0, -ox * ppu], [0, -ppu, oy * ppu]].map((row) => row.map((v) => Number(v.toFixed(6)))),
      note: `vector source rendered at ${ppu} px per model unit (${unitsPerMetre} units per metre), y flipped`,
    }),
  };
}
