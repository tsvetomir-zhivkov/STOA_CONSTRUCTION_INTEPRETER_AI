/**
 * Test fixtures generated in code (no binary files in the repo): one small IFC, DXF, SVG and vector PDF.
 *
 *   import { writeFixtures } from './test/helpers/fixtures.js';
 *   const paths = await writeFixtures('/tmp/fx');   // { ifc, dxf, svg, pdf, unknown }
 *
 * CLI:  node test/helpers/fixtures.js <dir>
 */

import fs from 'node:fs/promises';
import path from 'node:path';
import { fileURLToPath } from 'node:url';

/** Minimal IFC4: one storey with a space (Name "3-1", LongName "Kitchen"), an external load-bearing wall, a toilet. Millimetres. */
export function ifcFixture() {
  return `ISO-10303-21;
HEADER;
FILE_DESCRIPTION(('ViewDefinition [CoordinationView]'),'2;1');
FILE_NAME('house.ifc','2026-01-01T00:00:00',(''),(''),'','','');
FILE_SCHEMA(('IFC4'));
ENDSEC;
DATA;
#1=IFCPROJECT('0proj',$,'Demo',$,$,$,$,$,#2);
#2=IFCUNITASSIGNMENT((#3));
#3=IFCSIUNIT(*,.LENGTHUNIT.,.MILLI.,.METRE.);
#10=IFCCARTESIANPOINT((0.,0.,0.));
#11=IFCAXIS2PLACEMENT3D(#10,$,$);
#12=IFCLOCALPLACEMENT($,#11);
#20=IFCBUILDINGSTOREY('0storey1',$,'1.korrus',$,$,#12,$,'Ground floor',.ELEMENT.,0.);
#21=IFCBUILDINGSTOREY('0storey2',$,'2.korrus',$,$,#12,$,$,.ELEMENT.,3000.);
#30=IFCDIRECTION((0.,0.,1.));
#31=IFCCARTESIANPOINT((2000.,1500.));
#32=IFCAXIS2PLACEMENT2D(#31,$);
#33=IFCRECTANGLEPROFILEDEF(.AREA.,$,#32,4000.,3000.);
#34=IFCEXTRUDEDAREASOLID(#33,#11,#30,2700.);
#35=IFCSHAPEREPRESENTATION($,'Body','SweptSolid',(#34));
#36=IFCPRODUCTDEFINITIONSHAPE($,$,(#35));
#40=IFCSPACE('0space1',$,'3-1',$,$,#12,#36,'Kitchen',.ELEMENT.,.SPACE.,$);
#50=IFCCARTESIANPOINT((2000.,-100.));
#51=IFCAXIS2PLACEMENT2D(#50,$);
#52=IFCRECTANGLEPROFILEDEF(.AREA.,$,#51,4000.,200.);
#53=IFCEXTRUDEDAREASOLID(#52,#11,#30,2700.);
#54=IFCSHAPEREPRESENTATION($,'Body','SweptSolid',(#53));
#55=IFCPRODUCTDEFINITIONSHAPE($,$,(#54));
#56=IFCWALL('0wall1',$,'Wall A',$,$,#12,#55,$,.STANDARD.);
#60=IFCPROPERTYSINGLEVALUE('IsExternal',$,IFCBOOLEAN(.T.),$);
#61=IFCPROPERTYSINGLEVALUE('LoadBearing',$,IFCBOOLEAN(.T.),$);
#62=IFCPROPERTYSET('0pset1',$,'Pset_WallCommon',$,(#60,#61));
#63=IFCRELDEFINESBYPROPERTIES('0rel1',$,$,$,(#56),#62);
#70=IFCCARTESIANPOINT((3000.,1000.));
#71=IFCAXIS2PLACEMENT2D(#70,$);
#72=IFCRECTANGLEPROFILEDEF(.AREA.,$,#71,700.,400.);
#73=IFCEXTRUDEDAREASOLID(#72,#11,#30,400.);
#74=IFCSHAPEREPRESENTATION($,'Body','SweptSolid',(#73));
#75=IFCPRODUCTDEFINITIONSHAPE($,$,(#74));
#76=IFCSANITARYTERMINAL('0wc1',$,'Toilet:WC-01',$,$,#12,#75,$,.TOILETPAN.);
#90=IFCRELCONTAINEDINSPATIALSTRUCTURE('0rel2',$,$,$,(#56,#76),#20);
#91=IFCRELAGGREGATES('0rel3',$,$,$,#20,(#40));
ENDSEC;
END-ISO-10303-21;
`;
}

/** ASCII DXF in metres: two rooms, a wall, a door, a WC block outline and room / area / number / unit / scale texts. */
export function dxfFixture() {
  const L = ['0', 'SECTION', '2', 'HEADER', '9', '$INSUNITS', '70', '6', '0', 'ENDSEC', '0', 'SECTION', '2', 'ENTITIES'];
  const lw = (layer, pts) => { L.push('0', 'LWPOLYLINE', '8', layer, '90', String(pts.length), '70', '1'); for (const [x, y] of pts) L.push('10', String(x), '20', String(y)); };
  const txt = (layer, x, y, t) => L.push('0', 'TEXT', '8', layer, '10', String(x), '20', String(y), '40', '0.3', '1', t);
  lw('A-ROOM', [[0, 0], [4, 0], [4, 3], [0, 3]]);
  lw('A-ROOM', [[4, 0], [7, 0], [7, 3], [4, 3]]);
  lw('A-WALL', [[0, -0.2], [7, -0.2], [7, 0], [0, 0]]);
  lw('A-DOOR', [[1, -0.2], [1.9, -0.2], [1.9, 0], [1, 0]]);
  lw('SANITARY-WC', [[5, 1], [5.4, 1], [5.4, 1.6], [5, 1.6]]);
  txt('A-TEXT', 1.5, 1.5, 'Kitchen'); txt('A-TEXT', 1.5, 1.0, '12,0 m2'); txt('A-TEXT', 1.5, 2.2, '2.10');
  txt('A-TEXT', 5.2, 2.0, 'MH'); txt('A-TEXT', 8, 4, 'Whg. A'); txt('A-TEXT', 8, 5, '1:100');
  L.push('0', 'ENDSEC', '0', 'EOF');
  return `${L.join('\n')}\n`;
}

/** SVG at 1:100 (mm user units): two spaces, a wall, a door and labels. */
export function svgFixture() {
  return `<svg xmlns="http://www.w3.org/2000/svg" width="200mm" height="100mm" viewBox="0 0 200 100">
<polygon class="Space Kitchen" points="10,10 90,10 90,60 10,60"/><polygon class="Space Bedroom" points="90,10 150,10 150,60 90,60"/>
<rect class="Wall" x="10" y="8" width="140" height="2"/><rect class="Door" x="30" y="8" width="9" height="2"/>
<text x="40" y="35">Küche</text><text x="40" y="45">15 m²</text><text x="160" y="90">1:100</text></svg>
`;
}

/** Two-page vector PDF: two stroked room rectangles, a hatch of short lines, labels, a written area and a 1:100 note. */
export function pdfFixture() {
  const hatch = Array.from({ length: 45 }, (_, i) => `${50 + i * 10} 40 m ${52 + i * 10} 45 l S`).join(' ');
  const content = `0.5 w 50 50 m 300 50 l 300 250 l 50 250 l h S 300 50 m 500 50 l 500 250 l 300 250 l h S ${hatch} `
    + 'BT /F1 12 Tf 120 150 Td (Kitchen) Tj ET BT /F1 10 Tf 120 130 Td (12.5 m2) Tj ET BT /F1 12 Tf 380 150 Td (MH) Tj ET '
    + 'BT /F1 10 Tf 450 20 Td (1:100) Tj ET BT /F1 14 Tf 60 300 Td (Pohjapiirros 1. kerros) Tj ET';
  const stream = `<< /Length ${Buffer.byteLength(content, 'latin1')} >>\nstream\n${content}\nendstream`;
  const pageObj = (contents) => `<< /Type /Page /Parent 2 0 R /MediaBox [0 0 595 420] /Contents ${contents} 0 R /Resources << /Font << /F1 7 0 R >> >> >>`;
  const objs = ['<< /Type /Catalog /Pages 2 0 R >>', '<< /Type /Pages /Kids [3 0 R 5 0 R] /Count 2 >>', pageObj(4), stream, pageObj(6), stream, '<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>'];
  let out = '%PDF-1.4\n';
  const offsets = [];
  objs.forEach((o, i) => { offsets.push(Buffer.byteLength(out, 'latin1')); out += `${i + 1} 0 obj\n${o}\nendobj\n`; });
  const xref = Buffer.byteLength(out, 'latin1');
  out += `xref\n0 ${objs.length + 1}\n0000000000 65535 f \n${offsets.map((o) => `${String(o).padStart(10, '0')} 00000 n \n`).join('')}`;
  out += `trailer\n<< /Size ${objs.length + 1} /Root 1 0 R >>\nstartxref\n${xref}\n%%EOF\n`;
  return Buffer.from(out, 'latin1');
}

/**
 * Write all fixtures into `<dir>/data/demo_set/` (so drawing.file / drawing.source are derived as in the dataset).
 * @param {string} dir
 * @returns {Promise<{ ifc:string, dxf:string, svg:string, pdf:string, unknown:string }>}
 */
export async function writeFixtures(dir) {
  const d = path.join(dir, 'data', 'demo_set');
  await fs.mkdir(d, { recursive: true });
  const p = { ifc: path.join(d, 'house.ifc'), dxf: path.join(d, 'plan.dxf'), svg: path.join(d, 'plan.svg'), pdf: path.join(d, 'sheet.pdf'), unknown: path.join(d, 'mystery.bin') };
  await fs.writeFile(p.ifc, ifcFixture());
  await fs.writeFile(p.dxf, dxfFixture());
  await fs.writeFile(p.svg, svgFixture());
  await fs.writeFile(p.pdf, pdfFixture());
  await fs.writeFile(p.unknown, Buffer.from([0x13, 0x37, 0x00, 0xfe, 0x42, 0x99, 0x01, 0x02, 0x03]));
  return p;
}

if (process.argv[1] && path.resolve(process.argv[1]) === fileURLToPath(import.meta.url)) {
  const dir = process.argv[2] ?? 'fixtures';
  writeFixtures(dir).then((p) => process.stdout.write(`${JSON.stringify(p, null, 2)}\n`));
}
