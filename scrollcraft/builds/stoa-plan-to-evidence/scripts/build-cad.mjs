import { writeFileSync } from 'node:fs';
import { fileURLToPath } from 'node:url';

// Authored illustrative geometry. Units: 60 drawing units per metre.
// This is native SVG authoring, never a claim of extracted customer geometry.
const out = fileURLToPath(new URL('../assets/', import.meta.url));
// Blueprint paper and white drafting ink share the site's two accent tokens.
const colors = { paper: '#164E91', ink: '#FFFFFF', soft: 'color-mix(in srgb, #FFFFFF 72%, #164E91)', accent: '#FFFFFF' };
const path = (d, cls='fine') => `<path class="${cls}" d="${d}"/>`;
const rect = (x,y,w,h,cls='fine') => `<rect class="${cls}" x="${x}" y="${y}" width="${w}" height="${h}"/>`;
const text = (x,y,value,cls='note',anchor='start') => `<text class="${cls}" x="${x}" y="${y}" text-anchor="${anchor}">${value}</text>`;
const defs = `<defs>
  <pattern id="cut-hatch" width="7" height="7" patternUnits="userSpaceOnUse" patternTransform="rotate(45)"><path d="M0 0V7" stroke="${colors.soft}" stroke-width=".7"/></pattern>
  <pattern id="concrete" width="21" height="19" patternUnits="userSpaceOnUse"><path d="m2 3 4 1-2 4Zm13 9 3-2 1 5ZM6 16h2" fill="none" stroke="${colors.soft}" stroke-width=".6"/></pattern>
  <pattern id="insulation" width="14" height="12" patternUnits="userSpaceOnUse"><path d="M0 6Q3-4 7 6T14 6" fill="none" stroke="${colors.soft}" stroke-width=".6"/></pattern>
</defs>`;
const style = `<style>
  svg{background:${colors.paper};color:${colors.ink};font-family:Bahnschrift,'Segoe UI',sans-serif}
  path,rect,line,polyline{fill:none;stroke-linejoin:miter;stroke-linecap:square}
  .fine{stroke:${colors.ink};stroke-width:1.1}.thin{stroke:${colors.soft};stroke-width:.6}
  .cut{fill:url(#cut-hatch);stroke:${colors.ink};stroke-width:2.5}
  .concrete{fill:url(#concrete);stroke:${colors.ink};stroke-width:2}
  .heavy{stroke:${colors.ink};stroke-width:3}.dash{stroke:${colors.soft};stroke-width:.7;stroke-dasharray:12 4 2 4}
  .hidden{stroke:${colors.soft};stroke-width:.8;stroke-dasharray:5 4}
  .note{fill:${colors.soft};font-size:10px;letter-spacing:.6px}
  .room{fill:${colors.ink};font-size:14px;letter-spacing:1.2px}
  .title{fill:${colors.ink};font-size:19px;font-weight:600;letter-spacing:.5px}
  .dim{fill:${colors.ink};font-size:12px;letter-spacing:.6px}
  .tag{fill:${colors.accent};font-size:16px;font-weight:600}
  .tag-ref{fill:${colors.soft};font-size:10px;letter-spacing:.3px}
  .source circle{fill:${colors.paper};stroke:${colors.accent};stroke-width:1.7}
  .source path{stroke:${colors.accent};stroke-width:1}
  .source rect{fill:${colors.paper};stroke:none}
</style>`;

function dimension(x1,x2,y,label,ext=180) {
  return path(`M${x1} ${ext}V${y-8}M${x2} ${ext}V${y-8}M${x1-8} ${y}H${x2+8}`,'thin') +
    path(`M${x1-4} ${y+4}l8-8M${x2-4} ${y+4}l8-8`,'fine') + text((x1+x2)/2,y-6,label,'dim','middle');
}
function grid() {
  let svg='';
  for (const [x,name] of [[240,'A'],[506,'B'],[756,'C'],[960,'D']]) {
    svg += path(`M${x} 134V652`,'dash');
    svg += `<g aria-label="Grid axis ${name}"><circle cx="${x}" cy="140" r="12" fill="${colors.paper}" stroke="${colors.soft}" stroke-width=".8"/>${text(x,144,name,'dim','middle')}</g>`;
  }
  for (const [y,name] of [[180,'1'],[400,'2'],[600,'3']]) {
    svg += path(`M178 ${y}H1008`,'dash');
    svg += `<g aria-label="Grid axis ${name}"><circle cx="190" cy="${y}" r="12" fill="${colors.paper}" stroke="${colors.soft}" stroke-width=".8"/>${text(190,y+4,name,'dim','middle')}</g>`;
  }
  return svg;
}
function dimensions() {
  return dimension(240,960,83,'12 000') + dimension(240,506,115,'4 433') + dimension(506,756,115,'4 167') + dimension(756,960,115,'3 400') +
    path('M240 180H151M240 600H151M161 172V608M157 184l8-8M157 604l8-8','thin') +
    `<text class="dim" x="151" y="390" text-anchor="middle" transform="rotate(-90 151 390)">7 000</text>` +
    dimension(240,960,645,'12 000',600);
}
function north() {
  return `<g aria-label="North arrow">${text(1068,108,'N','room','middle')}${path('M1068 171V120M1059 143l9-23 9 23M1068 120l-9 23h9Z','fine')}</g>`;
}
function scale() {
  return `<g aria-label="Graphic scale, 0 to 3 metres">${text(985,564,'METRES','note')}${rect(985,578,180,8)}<path d="M985 578h60v8h-60ZM1105 578h60v8h-60Z" style="fill:${colors.ink}"/>${[0,1,2,3].map(n=>text(985+n*60,603,n,'note','middle')).join('')}</g>`;
}
function titleBlock(name,number) {
  return rect(24,24,1152,752,'fine') + path('M24 694H1176M764 694V776M968 694V776M764 735H1176','fine') +
    text(44,716,'STOA / ILLUSTRATIVE DRAWING SET','note') + text(44,745,name,'title') + text(44,766,'DIMENSIONS IN MILLIMETRES. SCHEMATIC STUDY, NOT FOR CONSTRUCTION.','note') +
    text(779,711,'DRAWING','note') + text(779,728,number,'room') + text(983,711,'DISCIPLINE','note') + text(983,728,'ARCHITECTURE','dim') +
    text(779,751,'SCALE AT ORIGINAL SIZE','note') + text(779,768,'1:100 / USE SCALE BAR','dim') + text(983,751,'SOURCE','note') + text(983,768,'AUTHORED SAMPLE','dim');
}
function windowH(x,y,w) {
  return rect(x,y,w,18,'fine') + path(`M${x} ${y+5}h${w}M${x} ${y+13}h${w}M${x+w/3} ${y}v18M${x+w*2/3} ${y}v18`);
}
function windowV(x,y,h) {
  return rect(x,y,18,h,'fine') + path(`M${x+5} ${y}v${h}M${x+13} ${y}v${h}M${x} ${y+h/2}h18`);
}
function floor() {
  let s = [[240,180,370,18],[770,180,190,18],[240,180,18,65],[240,335,18,265],[942,180,18,50],[942,360,18,240],[240,582,530,18],[830,582,130,18],[500,198,12,124],[500,376,12,206],[258,394,102,12],[414,394,86,12],[512,430,100,12],[666,430,276,12],[750,442,12,28],[750,524,12,58]].map(v=>rect(...v,'cut')).join('');
  s += windowH(610,180,160)+windowV(240,245,90)+windowV(942,230,130);
  // Door leaves and arcs share a hinge and radius, inside true wall gaps.
  s += `<g data-cad-detail="door-swings">${path('M512 322h54M512 376A54 54 0 0 0 566 322M360 394v-54M414 394A54 54 0 0 0 360 340M612 430v-54M666 430A54 54 0 0 0 612 376M762 470h54M762 524A54 54 0 0 0 816 470M770 582v-60M830 582A60 60 0 0 0 770 522','fine')}</g>`;
  // Bed and wardrobes, drawn with thin furniture lines.
  s += rect(282,216,126,143,'thin')+rect(286,220,118,28,'thin')+rect(289,223,51,21,'thin')+rect(349,223,51,21,'thin')+path('M282 255H408M286 258V354H404V258','thin');
  s += rect(434,214,44,92,'thin')+path('M434 245H478M434 276H478M456 214V306','thin');
  // Living / dining, with wall clearance and furniture scale.
  s += rect(654,217,190,51,'thin')+rect(660,223,178,39,'thin')+path('M716 223V262M780 223V262','thin');
  s += rect(712,289,92,46,'thin')+rect(861,295,48,79,'thin')+rect(520,205,40,104,'thin');
  s += `<ellipse cx="661" cy="354" rx="35" ry="24" fill="none" stroke="${colors.soft}" stroke-width=".7"/>`;
  s += [[614,343,13,22],[695,343,13,22],[650,318,22,11],[650,379,22,11]].map(v=>rect(...v,'thin')).join('');
  // Kitchen counters, sink, hob; bathroom sanitary fixtures.
  s += path('M278 425V561H467V525H314V425Z','thin')+path('M278 461H314M278 501H314M330 525V561M377 525V561M422 525V561','thin');
  s += rect(282,435,27,47,'thin')+rect(285,439,21,17,'thin')+rect(285,461,21,17,'thin')+rect(388,530,31,27,'thin');
  s += path('M393 534h7v7h-7ZM407 534h7v7h-7ZM393 546h7v7h-7ZM407 546h7v7h-7Z','thin');
  s += rect(866,449,66,66,'thin')+path('M866 449l66 66M866 515l66-66','thin');
  s += rect(791,451,43,23,'thin')+`<ellipse cx="813" cy="462" rx="15" ry="7" fill="none" stroke="${colors.soft}" stroke-width=".8"/>`;
  s += rect(903,537,27,12,'thin')+path('M905 549v15a12 12 0 0 0 23 0v-15Z','thin');
  s += rect(526,463,39,98,'thin')+path('M526 487H565M526 511H565M526 535H565','thin');
  for (const [x,y,name,num] of [[393,377,'BEDROOM','101'],[754,397,'LIVING','102'],[397,489,'KITCHEN','103'],[655,501,'CORRIDOR','104'],[847,548,'BATHROOM','105']]) {
    s += text(x,y,name,'room','middle')+text(x,y+15,num,'note','middle');
  }
  return s;
}
function foundation() {
  let s = rect(222,162,756,456,'concrete')+`<rect x="278" y="218" width="644" height="344" fill="${colors.paper}" stroke="${colors.ink}" stroke-width="1.5"/>`;
  s += rect(480,198,52,384,'concrete')+rect(258,380,684,40,'concrete');
  for (const x of [250,506,756,950]) for (const y of [190,400,590]) s += rect(x-27,y-27,54,54,'concrete')+rect(x-8,y-8,16,16,'heavy');
  for(let x=294;x<920;x+=34) s += path(`M${x} 226V554`,'thin');
  for(let y=235;y<550;y+=34) s += path(`M286 ${y}H914`,'thin');
  return s + text(665,300,'SLAB / REINFORCEMENT PLAN','room','middle') + text(665,320,'SCHEMATIC STRUCTURAL STUDY','note','middle') + path('M295 550l30-30M325 520h45','fine')+text(376,524,'SLAB EDGE','note');
}
function framing() {
  let s = rect(240,180,720,420,'heavy')+rect(252,192,696,396,'fine')+rect(490,192,32,396,'cut');
  for(let x=280;x<942;x+=36) {
    s += rect(x,200,7,180,'fine')+rect(x,420,7,160,'fine');
    s += path(`M${x} 380l7-9M${x} 420l7 9`,'thin');
  }
  s += rect(252,386,696,28,'cut')+path('M252 400H948M506 192V588','dash');
  for(const x of [260,490,744,932]) for(const y of [193,387,576]) s += rect(x,y,15,15,'heavy');
  s += text(679,282,'FRAMING PLAN','room','middle')+text(679,302,'JOISTS / BEARING WALL / LINTELS','note','middle');
  return s;
}
function roof() {
  let s = rect(215,150,770,480,'heavy')+rect(240,180,720,420,'hidden')+path('M215 150l180 240-180 240M985 150 805 390l180 240M395 390H805','heavy');
  for(let x=251;x<955;x+=29) {
    const ridgeY=390;
    const hipX = x<395 ? x : x>805 ? x : null;
    if(hipX) {
      const y=150 + Math.min(x-215,985-x)*240/180;
      s += path(`M${x} 153V${Math.min(y,390)}M${x} ${630-(Math.min(y,390)-150)}V627`,'thin');
    } else s += path(`M${x} 153V387M${x} 393V627`,'thin');
  }
  for(let y=181;y<625;y+=29) s += path(`M218 ${y}H${215+Math.min(y-150,630-y)*.75}M${985-Math.min(y-150,630-y)*.75} ${y}H982`,'thin');
  s += path('M560 280V210m-6 8 6-8 6 8M700 500V572m-6-8 6 8 6-8','fine');
  s += text(586,251,'FALL','note')+text(718,551,'FALL','note');
  return s;
}
function elevation() {
  let s = path('M215 579H985M240 576V315l360-150 360 150v261M215 315 600 153l385 162M230 324 600 169l370 155M240 338H960M240 565H960','heavy');
  s += path('M240 577V605H960V577','hidden');
  for(let y=354;y<560;y+=14) s += path(`M243 ${y}H957`,'thin');
  s += `<g fill="${colors.paper}" stroke="${colors.ink}" stroke-width="1.2"><rect x="300" y="382" width="140" height="114"/><rect x="722" y="382" width="162" height="114"/><rect x="542" y="370" width="91" height="207"/></g>`;
  s += path('M305 387H435V491H305ZM370 387V491M305 425H435M727 387H879V491H727ZM777 387V491M830 387V491M548 376H627V577M556 388H619V492M556 510H619V562M611 502h7','fine');
  for(const [y,t] of [[576,'±0.000'],[338,'+2.700'],[165,'+5.000']]) s += path(`M1010 ${y}h62m-62-5 8 5-8 5Z`,'fine')+text(1080,y+4,t,'dim');
  s += dimension(240,960,645,'12 000',576);
  return s;
}
const refs = [
  { id:'room-101', x:392,y:348,lx:42,ly:272, label:'Bedroom 101', field:'rooms.type · bedroom' },
  { id:'room-102', x:750,y:349,lx:1010,ly:270, label:'Living room', field:'rooms.type · living' },
  { id:'opening-d01', x:512,y:348,lx:43,ly:454, label:'Door D-01', field:'openings.kind · door' },
  { id:'opening-w01', x:689,y:189,lx:805,ly:49, label:'Window W-01', field:'openings.kind · window' },
  { id:'wall-02', x:506,y:489,lx:1005,ly:450, label:'Internal wall', field:'walls.thickness_m' },
  { id:'dimension-01', x:600,y:83,lx:410,ly:40, label:'Overall 12.00 m', field:'texts.role · dimension' },
];
function callout({id,x,y,lx,ly,label,field},mobile=false) {
  const w=mobile?235:165, h=mobile?37:41;
  const sx=lx+(x>lx+w?w:0), sy=ly+8;
  return `<g class="source" data-source-ref="demo:${id}" aria-label="${label}"><path d="M${x} ${y}L${(x+sx)/2} ${sy}H${sx}"/><circle cx="${x}" cy="${y}" r="${mobile?7:6}"/><rect x="${lx-4}" y="${ly-18}" width="${w}" height="${h}"/>${text(lx,ly,label,mobile?'mobile-tag':'tag')}${mobile?'':text(lx,ly+17,field,'tag-ref')}</g>`;
}
const subject = floor();
function svg(content,title,viewBox='0 0 1200 800',extraStyle='') {
  return `<svg xmlns="http://www.w3.org/2000/svg" viewBox="${viewBox}" role="img" aria-labelledby="title desc"><title id="title">${title}</title><desc id="desc">Authored illustrative architectural study. CAD cut weights, material hatching, thin dimensions and extensions, grid axes, and labeled source references. Not for construction.</desc>${defs}${style}${extraStyle}${content}</svg>\n`;
}
const base = grid()+dimensions()+subject+north()+scale();
writeFileSync(out+'cad-plan.svg',svg(base+refs.map(r=>callout(r)).join('')+titleBlock('GROUND FLOOR / SOURCE REFERENCES','A-101'),'Ground floor with named source references'));
writeFileSync(out+'cad-plan-clean.svg',svg(base+titleBlock('GROUND FLOOR PLAN','A-101'),'Architectural ground floor plan'));

const mobileRefs = refs.map((r,i)=>({...r,x:r.x*.85-110,y:r.y*.85+70,lx:[28,518,28,518,285,270][i],ly:[175,175,668,86,708,44][i]}));
const mobile = `<g transform="translate(-110 70) scale(.85)">${grid()+dimensions()+subject}</g>`+mobileRefs.map(r=>callout(r,true)).join('')+
  `<g aria-label="North arrow">${text(742,248,'N','room','middle')}${path('M742 295V258m-6 15 6-15 6 15','fine')}</g>`+
  `<g aria-label="Graphic scale in metres">${text(601,680,'0','dim')}${text(654,680,'1','dim')}${text(705,680,'2 m','dim')}${rect(600,688,102,8)}<path d="M600 688h51v8h-51Z" style="fill:${colors.ink}"/></g>`+
  rect(12,12,776,776,'fine')+path('M12 735H788M542 735V788','fine')+text(30,757,'STOA / ILLUSTRATIVE DRAWING','note')+text(30,779,'GROUND FLOOR / SOURCE REFERENCES','title')+text(559,760,'A-101 / SCHEMATIC','dim')+text(559,778,'NOT FOR CONSTRUCTION','note');
writeFileSync(out+'cad-plan-mobile.svg',svg(mobile,'Ground floor with mobile source callouts','0 0 800 800',`<style>.mobile-tag{fill:${colors.accent};font-size:25px;font-weight:600}.title{font-size:16px}</style>`));

for (const [slug,title,num,geometry] of [
  ['foundation','FOUNDATION / FOOTING PLAN','S-101',foundation()],
  ['floor','GROUND FLOOR PLAN','A-101',subject],
  ['framing','WALLS / FRAMING PLAN','S-201',framing()],
  ['roof','ROOF STRUCTURE PLAN','S-301',roof()],
  ['elevation','SOUTH ELEVATION','A-201',elevation()],
]) {
  writeFileSync(out+`cad-${slug}.svg`,svg(grid()+dimensions()+geometry+north()+scale()+titleBlock(title,num),title));
}
console.log('Built 8 native CAD SVG assets: source plan, mobile plan, clean plan, and five structural sheets.');
