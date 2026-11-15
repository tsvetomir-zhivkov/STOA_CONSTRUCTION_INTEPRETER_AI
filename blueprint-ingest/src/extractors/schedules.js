/** Conservative printed-table extraction. A title plus aligned column headers is required. */
const KINDS = [['door', /door\s+schedule/i], ['window', /window\s+schedule/i], ['room_finish', /room\s+finish\s+schedule/i], ['material', /material\s+schedule/i], ['sanitary_fixture', /sanitary\s+(fixture\s+)?schedule/i], ['equipment', /equipment\s+schedule/i]];
const HEADER = /^(mark|type|room|number|description|width(?:\s*\((?:mm|cm|m)\))?|height(?:\s*\((?:mm|cm|m)\))?|material|finish|quantity|qty|notes?)$/i;

export function readScheduleTables(texts) {
  const title = texts.find(t => KINDS.some(([, re]) => re.test(t.text)));
  if (!title || !title.bbox_px) return [];
  const kind = KINDS.find(([, re]) => re.test(title.text))[0];
  const lines = [];
  for (const t of texts.filter(t => t.bbox_px && t.bbox_px[1] > title.bbox_px[1]).sort((a, b) => a.bbox_px[1] - b.bbox_px[1] || a.bbox_px[0] - b.bbox_px[0])) {
    const y = t.bbox_px[1], tolerance = Math.max(2, (t.bbox_px[3] - y) * .5);
    let line = lines.find(l => Math.abs(l.y - y) <= tolerance);
    if (!line) { line = { y, items: [] }; lines.push(line); }
    line.items.push(t);
  }
  const headerIndex = lines.findIndex(l => l.items.length >= 2 && l.items.every(t => HEADER.test(t.text.trim())));
  if (headerIndex < 0) return [];
  const header = lines[headerIndex].items.sort((a, b) => a.bbox_px[0] - b.bbox_px[0]);
  const rows = [];
  for (const line of lines.slice(headerIndex + 1)) {
    if (line.items.length !== header.length) break;
    const cells = line.items.sort((a, b) => a.bbox_px[0] - b.bbox_px[0]);
    if (cells.some((t, i) => Math.abs(t.bbox_px[0] - header[i].bbox_px[0]) > Math.max(10, (header[i].bbox_px[3] - header[i].bbox_px[1]) * 2))) break;
    rows.push({ cells: cells.map(t => t.text), source_ref: cells.map(t => t.provenance.source_ref).join(',') });
  }
  return rows.length ? [{ name: title.text, schedule_type: kind, headers: header.map(t => t.text), rows, source_ref: title.provenance.source_ref }] : [];
}

export function addSchedules(building, tables = []) {
  const drawing = building.drawings[0];
  const ref = source_ref => ({ source_document_id: drawing.source_document_id, drawing_id: drawing.id, source_ref, extractor: 'schedule_table' });
  for (const [i, table] of tables.entries()) {
    const id = `${drawing.id}:schedule:${i}`;
    const rows = table.rows.map((row, n) => {
      const item = { id: `${id}:row:${n}`, cells: row.cells, entity_ids: [], source_refs: [ref(row.source_ref ?? `table:${i}:row:${n}`)], type_mark: null, dimensions: {}, quantity: null, description: null };
      table.headers.forEach((header, c) => {
        const value = row.cells[c];
        if (/^(mark|type)$/i.test(header)) item.type_mark = value == null ? null : String(value);
        if (/^description$/i.test(header)) item.description = value == null ? null : String(value);
        if (/^(qty|quantity)$/i.test(header) && /^\d+(\.\d+)?$/.test(String(value))) item.quantity = Number(value);
        const dim = /^(width|height)\s*\((mm|cm|m)\)$/i.exec(header);
        if (dim && /^\d+(\.\d+)?$/.test(String(value))) item.dimensions[`${dim[1].toLowerCase()}_m`] = Number(value) * ({ mm: .001, cm: .01, m: 1 }[dim[2].toLowerCase()]);
      });
      // No instance links from a repeated mark alone. A schedule row is never an instance.
      return item;
    });
    building.schedules.push({ id, name: table.name ?? null, schedule_type: table.schedule_type, source_document_id: drawing.source_document_id, source_sheet_id: drawing.id, headers: table.headers, rows, confidence: .7, provenance: { extractor: 'schedule_table', source_ref: table.source_ref ?? `table:${i}` }, source_refs: [ref(table.source_ref ?? `table:${i}`)] });
  }
}
