import { chromium } from 'playwright-core';
import { readFileSync, writeFileSync, mkdirSync } from 'node:fs';
import { createHash } from 'node:crypto';
import assert from 'node:assert/strict';

const url=process.env.STOA_PREVIEW_URL || 'http://127.0.0.1:3000';
const out=process.env.STOA_VERIFY_OUT || 'lab/cad-functional';
mkdirSync(out,{recursive:true});
const browser=await chromium.launch({channel:'chrome',headless:true});
const results=[], errors=[], external=[];
const safe=async context=>{
  await context.addInitScript(()=>{
    Element.prototype.requestPointerLock=()=>Promise.reject(new Error('Native pointer lock disabled'));
    Element.prototype.setPointerCapture=()=>{};
    Element.prototype.releasePointerCapture=()=>{};
    Document.prototype.exitPointerLock=()=>{};
  });
};
const settle=async page=>page.waitForTimeout(100);
async function at(page,id,p) {
  await page.evaluate(({id,p})=>{
    const el=document.getElementById(id),top=el.getBoundingClientRect().top+scrollY;
    scrollTo({top:top+(el.offsetHeight-innerHeight)*p,behavior:'instant'});
  },{id,p});
  await settle(page);
}
async function measure(page) {
  return page.evaluate(()=>{
    const r=el=>{const b=el.getBoundingClientRect();return {x:b.x,y:b.y,w:b.width,h:b.height,right:b.right,bottom:b.bottom};};
    return {
      width:innerWidth,height:innerHeight,overflow:document.documentElement.scrollWidth-innerWidth,
      sheets:[...document.querySelectorAll('[data-sheet]')].map(el=>({
        name:el.dataset.sheet,paper:r(el.querySelector('.cad-paper')),caption:r(el.querySelector('figcaption')),
        label:r(el.querySelector('.source-label')),labelOpacity:+getComputedStyle(el.querySelector('.source-label')).opacity,
        text:el.querySelector('.source-label').textContent,
      })),
      resolvedOpacity:+getComputedStyle(document.querySelector('.resolved-plan')).opacity,
      stage:r(document.querySelector('.cad-stage')),
      total:document.documentElement.scrollHeight/innerHeight
    };
  });
}
function overlap(a,b) {return Math.min(a.right,b.right)-Math.max(a.x,b.x)>1 && Math.min(a.bottom,b.bottom)-Math.max(a.y,b.y)>1;}
try {
  for(const viewport of [{width:1440,height:900},{width:1280,height:720},{width:390,height:844},{width:360,height:640},{width:768,height:1024}]) {
    const context=await browser.newContext({viewport});
    await safe(context);
    const page=await context.newPage();
    page.on('pageerror',e=>errors.push(String(e)));
    page.on('requestfailed',r=>errors.push(r.url()+': '+r.failure()?.errorText));
    page.on('request',r=>{if(!r.url().startsWith(url)) external.push(r.url());});
    await page.goto(url,{waitUntil:'networkidle'});
    const opening=await page.evaluate(()=>{
      const h=document.querySelector('h1'),a=document.querySelector('.hero-copy');
      const r=h.getBoundingClientRect(),b=document.querySelector('.hero-sheet').getBoundingClientRect();
      return {headline:h.textContent,lines:h.offsetHeight/parseFloat(getComputedStyle(h).lineHeight),opacity:getComputedStyle(a).opacity,headlineBottom:r.bottom,extraCopy:document.querySelectorAll('.hero p,.hero .cta').length,blueprint:{left:b.left,right:b.right,top:b.top,bottom:b.bottom,center:b.x+b.width/2,width:b.width},broken:[...document.images].filter(i=>!i.complete||!i.naturalWidth).map(i=>i.src)};
    });
    assert(opening.lines<=2.1,'Hero exceeds two lines at '+viewport.width);
    assert.equal(+opening.opacity,1,'Hero copy hidden');
    assert.equal(opening.headline,'Drawings, made answerable.');
    assert.equal(opening.extraCopy,0,'Opening description or button retained');
    const blueprint=opening.blueprint;
    assert(blueprint.left>=0&&blueprint.right<=viewport.width&&blueprint.top>=opening.headlineBottom&&blueprint.bottom<=viewport.height,'Opening blueprint clipped or overlapping copy at '+viewport.width);
    assert(Math.abs(blueprint.center-viewport.width/2)<2,'Opening blueprint off center at '+viewport.width);
    assert(blueprint.width>=(viewport.width<=1000?viewport.width*.9:Math.min(viewport.width,viewport.height*1.5)*.7),'Opening blueprint too small at '+viewport.width);
    assert.equal(opening.broken.length,0,'Broken image');
    await page.screenshot({path:out+'/opening-'+viewport.width+'.png'});
    const families=await page.locator('[data-device-family]').evaluateAll(els=>els.map(e=>e.dataset.deviceFamily));
    assert(new Set(families).size>=4,'Fewer than four device families');
    assert(families.every((f,i)=>i===0||f!==families[i-1]),'Adjacent family repeated');
    const types=await page.locator('[data-sc-act]').evaluateAll(els=>els.map(e=>e.dataset.scAct));
    assert(types.every((f,i)=>i===0||f!==types[i-1]),'Adjacent act type repeated');
    const phases=[];
    for(const p of [0,.5,1]) {
      await at(page,'rotation',p);
      const state=await measure(page);
      assert(state.overflow<=1,'Page overflow at '+viewport.width);
      for(let i=0;i<state.sheets.length;i++) {
        const a=state.sheets[i];
        assert.equal(a.labelOpacity,1,'Reference label not fully visible: '+a.name);
        for(const b of [a.paper,a.caption,a.label]) assert(b.x>=-1&&b.right<=viewport.width+1&&b.y>=-1&&b.bottom<=viewport.height+1,'Sheet or label clipped: '+a.name+' at '+viewport.width);
        for(let j=i+1;j<state.sheets.length;j++) {
          const b=state.sheets[j];
          assert(!overlap(a.paper,b.paper),'Overlapping separated sheets: '+a.name+'/'+b.name+' at '+viewport.width);
          assert(!overlap(a.label,b.caption)&&!overlap(a.label,b.label),'Callout collision: '+a.name+'/'+b.name);
        }
      }
      phases.push(state);
      if(p===.5) await page.screenshot({path:out+'/separated-'+viewport.width+'.png'});
    }
    // Crossing a phase boundary must not reset the drawing.
    const continuity=[];
    for(const id of ['rotation','registration']) {
      const y=await page.locator('#'+id).evaluate(el=>el.getBoundingClientRect().top+scrollY);
      await page.evaluate(y=>scrollTo(0,y-1),y); await settle(page);
      const before=await measure(page);
      await page.evaluate(y=>scrollTo(0,y+1),y); await settle(page);
      const after=await measure(page);
      const shift=Math.max(...after.sheets.map((s,i)=>Math.hypot(s.paper.x-before.sheets[i].paper.x,s.paper.y-before.sheets[i].paper.y)));
      assert(shift<3,'Phase boundary jump: '+id);
      continuity.push({id,shift});
    }
    await at(page,'registration',1);
    const final=await measure(page);
    assert.equal(final.resolvedOpacity,1,'Plan did not resolve');
    await page.screenshot({path:out+'/resolved-'+viewport.width+'.png'});
    // A live preference change at the climax must restore every static sheet.
    await page.emulateMedia({reducedMotion:'reduce'});await settle(page);
    assert.equal(await page.locator('[data-sheets]').evaluate(el=>+getComputedStyle(el).opacity),1,'Motion toggle lost the drawing set');
    await page.emulateMedia({reducedMotion:'no-preference'});await settle(page);
    await page.evaluate(()=>scrollTo(0,document.documentElement.scrollHeight));await settle(page);
    const closing=await page.locator('.close .cta').evaluate(el=>({opacity:getComputedStyle(el.closest('.close-inner')).opacity,href:el.href}));
    assert.equal(+closing.opacity,1,'Closing CTA hidden');
    assert.equal(closing.href,new URL('/workspace/',url).href);
    // Browser tabbing must reveal every link, including from the final screen.
    await page.goto(url,{waitUntil:'networkidle'});
    const focus=[];
    for(let i=0;i<5;i++) {
      await page.keyboard.press('Tab');await settle(page);
      focus.push(await page.evaluate(()=>{
        const el=document.activeElement,b=el.getBoundingClientRect();
        let opacity=1;for(let p=el;p;p=p.parentElement) opacity*=+getComputedStyle(p).opacity;
        return {text:el.textContent.trim(),tag:el.tagName,visible:opacity>.9&&b.bottom>0&&b.top<innerHeight,outline:getComputedStyle(el).outlineStyle};
      }));
    }
    assert(focus.every(f=>f.tag==='A'&&f.visible&&f.outline!=='none'),'Invisible keyboard stop: '+JSON.stringify(focus));
    await page.locator('.bar-link').click();
    await settle(page);
    await page.waitForURL(new URL('/workspace/',url).href);
    await page.waitForFunction(()=>!!window.workspace);
    await page.waitForLoadState('networkidle');
    assert.equal(new URL(page.url()).pathname,'/workspace/');
    assert(await page.locator('#openBtn').isVisible());
    await page.goto(url,{waitUntil:'networkidle'});
    const hrefs=await page.locator('a[href]').evaluateAll(els=>els.map(a=>a.getAttribute('href')));
    assert(hrefs.every(h=>h.startsWith('#')||h==='/workspace/'||h.startsWith('https://github.com/tsvetomir-zhivkov/STOA_CONSTRUCTION_INTEPRETER_AI')),'Unexpected destination');
    results.push({viewport,opening,families,types,totalViewports:final.total,continuity,focus,minimumPaperGaps:phases.map(s=>Math.min(...s.sheets.slice(1).map((v,i)=>Math.max(v.paper.y-s.sheets[i].paper.bottom,v.paper.x-s.sheets[i].paper.right))))});
    await context.close();
  }
  for(const js of [true,false]) {
    const context=await browser.newContext({viewport:{width:390,height:844},reducedMotion:'reduce',javaScriptEnabled:js});
    await safe(context);
    const page=await context.newPage();
    await page.goto(url,{waitUntil:'networkidle'});
    assert.equal(await page.locator('.motion-ready').count(),0,'Reduced-motion pinned space retained');
    assert.equal(await page.locator('.journey-timeline').isVisible(),false);
    for(const el of await page.locator('.sheet-item,.resolved-plan,.close .cta').all()) {
      await el.scrollIntoViewIfNeeded();await settle(page);assert(await el.isVisible(),'Missing fallback content');
    }
    const fallback=await page.evaluate(()=>({height:document.documentElement.scrollHeight,width:document.documentElement.scrollWidth,close:getComputedStyle(document.querySelector('.close-inner')).opacity}));
    assert(fallback.width<=390,'Fallback overflow');
    assert.equal(+fallback.close,1,'No-JS close is invisible');
    await page.screenshot({path:out+'/fallback-'+(js?'reduced':'no-js')+'.png',fullPage:true});
    results.push({fallback:js?'reduced-motion':'no-JS',...fallback});
    await context.close();
  }
  const schema=JSON.parse(readFileSync('../../../translator/schema/drawing.schema.json','utf8'));
  for(const type of ['bedroom','living','kitchen','bathroom','corridor']) assert(schema.$defs.room.properties.type.enum.includes(type));
  for(const type of ['door','window']) assert(schema.$defs.opening.properties.kind.enum.includes(type));
  for(const f of ['cad-plan.svg','cad-plan-mobile.svg']) {
    const source=readFileSync('assets/'+f,'utf8');
    assert(source.includes('background:#164E91')&&source.includes('stroke:#FFFFFF'),'Missing blue paper or white drafting outlines');
    const markers=[...source.matchAll(/<g class="source"[\s\S]*?<\/g>/g)];
    assert.equal(markers.length,6,'Expected six named source markers');
    for(const [group] of markers) assert(group.includes('<circle')&&group.includes('<text')&&group.includes('aria-label='),'Unnamed source marker');
    assert(source.includes('North arrow')&&source.includes('Graphic scale'),'Missing drafting detail');
  }
  const hashes={};
  for(const name of ['scrollcraft.js','scrollcraft.css']) {
    const hash=p=>createHash('sha256').update(readFileSync(p)).digest('hex');
    hashes[name]=hash(name);
    assert.equal(hashes[name],hash('../../../.agents/skills/scroll-craft/engine/'+name),'Engine changed');
  }
  assert.equal(errors.length,0,'Browser errors: '+errors.join('; '));
  assert.equal(external.length,0,'External request: '+external.join('; '));
  const report={passed:true,results,engineHashes:hashes,browserErrors:errors,externalRequests:external};
  writeFileSync(out+'/report.json',JSON.stringify(report,null,2));
  console.log(JSON.stringify(report,null,2));
} catch(error) {
  writeFileSync(out+'/failure-'+Date.now()+'.json',JSON.stringify({passed:false,error:String(error),results,errors,external},null,2));
  throw error;
} finally {await browser.close();}

