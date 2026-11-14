(() => {
  const motion = matchMedia('(prefers-reduced-motion: reduce)');
  const stage = document.querySelector('[data-cad-stage]');
  const sheets = [...document.querySelectorAll('[data-sheet]')];
  const resolved = document.querySelector('.resolved-plan');
  const phases = ['separation', 'rotation', 'registration'].map(id => document.getElementById(id));
  const clamp = value => Math.max(0, Math.min(1, value));
  const mix = (a,b,t) => a + (b-a)*t;
  const smooth = (a,b,v) => { const t = clamp((v-a)/(b-a)); return t*t*(3-2*t); };
  let engine;
  let queued = false;
  let lastState = '';

  function progress(el) {
    return clamp(parseFloat(getComputedStyle(el).getPropertyValue('--sc-p')) || 0);
  }
  // The marker is projected with exactly the same transform as its source
  // point on the CAD sheet. Its attached text stays face-on for legibility.
  function project(x,y,w,h,rx,ry,rz) {
    const rad = Math.PI/180;
    const a=rx*rad,b=ry*rad,c=rz*rad;
    let px=x-w/2, py=y-h/2;
    const zx=px*Math.cos(c)-py*Math.sin(c), zy=px*Math.sin(c)+py*Math.cos(c);
    const yx=zx*Math.cos(b), yz=-zx*Math.sin(b);
    const xx=yx, xy=zy*Math.cos(a)-yz*Math.sin(a), xz=zy*Math.sin(a)+yz*Math.cos(a);
    const perspective=1500/(1500-xz);
    return [w/2+xx*perspective,h/2+xy*perspective];
  }
  function mark(sheet,w,h,rx,ry,rz,visible=1,offsetX=0,offsetY=0) {
    const [ax,ay]=sheet.dataset.anchor.split(' ').map(Number);
    const [x,y]=project(ax/1200*w,ay/800*h,w,h,rx,ry,rz);
    const bounds=[[0,0],[w,0],[0,h],[w,h]].map(([a,b])=>project(a,b,w,h,rx,ry,rz));
    const minY=Math.min(...bounds.map(p=>p[1])), maxY=Math.max(...bounds.map(p=>p[1]));
    const label=sheet.querySelector('.source-label'), caption=sheet.querySelector('figcaption');
    const phone=innerWidth<=600 && !motion.matches;
    const ly=phone?minY-23:maxY+9, lx=phone?w*.56:6;
    caption.style.transform=`translate(${(6+offsetX).toFixed(1)}px,${(minY-(phone?22:26)+offsetY).toFixed(1)}px)`;
    caption.style.opacity=visible;
    label.style.transform=`translate(${(lx+offsetX).toFixed(1)}px,${(ly+offsetY).toFixed(1)}px)`;
    label.style.opacity=visible;
    const svg=sheet.querySelector('.source-leader');
    svg.style.opacity=visible;
    svg.style.height='100%';
    svg.querySelector('circle').setAttribute('cx',(x+offsetX).toFixed(2));
    svg.querySelector('circle').setAttribute('cy',(y+offsetY).toFixed(2));
    svg.querySelectorAll('path').forEach(path=>path.setAttribute('d',`M${(x+offsetX).toFixed(2)} ${(y+offsetY).toFixed(2)}L${(x+offsetX).toFixed(2)} ${(ly+9+offsetY).toFixed(2)}H${(lx+offsetX).toFixed(2)}`));
  }
  function staticLayout() {
    document.querySelector('[data-sheets]').style.opacity='1';
    sheets.forEach(sheet=>{
      sheet.removeAttribute('style');
      sheet.querySelector('.cad-paper').removeAttribute('style');
      const w=sheet.clientWidth,h=w*2/3;
      mark(sheet,w,h,0,0,0);
      // Static captions and labels reserve their own flow space.
      const caption=sheet.querySelector('figcaption'), label=sheet.querySelector('.source-label');
      caption.style.transform='none'; label.style.transform='none';
      const svg=sheet.querySelector('.source-leader');
      svg.style.height=h+'px';
      const [ax,ay]=sheet.dataset.anchor.split(' ').map(Number);
      svg.querySelectorAll('path').forEach(path=>path.setAttribute('d',`M${ax/1200*w} ${ay/800*h}V${h+47}H0`));
    });
    resolved.removeAttribute('style');
    stage.dataset.scVerifyState='static-detailed-sheets-and-resolved-plan';
    stage.dataset.scVerifyHold='true';
  }
  function paint() {
    queued=false;
    if (motion.matches) { staticLayout(); return; }
    const rect=stage.getBoundingClientRect();
    if (rect.bottom<0 || rect.top>innerHeight) { stage.dataset.scVerifyHold='false'; return; }
    const W=stage.clientWidth,H=stage.clientHeight,phone=W<=600;
    const split=smooth(0,.98,progress(phases[0]));
    const turn=progress(phases[1]);
    const join=smooth(.02,.88,progress(phases[2]));
    const spread=split*(1-join);
    const full=phone?W-28:Math.min(W*.79,1080,(H-150)*1.5);
    const small=phone?Math.min(W*.63,(H-110)*.34):Math.min(W*.245,(H-180)*.59,380);
    const width=mix(full,small,spread),height=width*2/3;
    const heroRotation=-8+turn*16;
    const step=phone?(H-150)/5:0;
    const places=phone?
      [[-12,-step*2],[9,-step],[0,0],[-9,step],[12,step*2]]:
      [[-W*.315,-H*.225],[0,-H*.225],[W*.315,-H*.225],[-W*.175,H*.235],[W*.175,H*.235]];
    const states=[];
    sheets.forEach((sheet,i)=>{
      const paper=sheet.querySelector('.cad-paper');
      const [px,py]=places[i];
      const x=(px+(turn-.5)*(phone?12:36))*spread;
      const y=(py+(phone?0:Math.sin(i*1.7)*8*turn))*spread;
      const rx=(phone?58:8+(i%2)*2)*spread;
      const ry=(phone?(-3+turn*6+(i-2)*.65):(heroRotation+(i-2)*2))*spread;
      const rz=(phone?0:(i-2)*.8)*spread;
      const hide=i===1?1:1-smooth(.78,.91,join);
      const labels=smooth(.86,.98,split)*(1-smooth(.02,.16,join));
      // Layout width stays constant throughout the entire sequence. Scale is
      // composited on the paper; face-on labels use projected source positions.
      sheet.style.setProperty('--sheet-width',full.toFixed(2)+'px');
      sheet.style.transform=`translate(calc(-50% + ${x.toFixed(2)}px),calc(-50% + ${y.toFixed(2)}px))`;
      sheet.style.opacity=hide.toFixed(3);
      sheet.style.zIndex=i===1?'10':String(5-i);
      paper.style.transform=`perspective(1500px) rotateX(${rx.toFixed(3)}deg) rotateY(${ry.toFixed(3)}deg) rotateZ(${rz.toFixed(3)}deg) scale(${(width/full).toFixed(6)})`;
      mark(sheet,width,height,rx,ry,rz,labels.toFixed(3),(full-width)/2,(full*2/3-height)/2);
      states.push([width,x,y,rx,ry,rz,hide,labels].map((v,n)=>v.toFixed(n>5?2:1)).join(','));
    });
    const reveal=smooth(.77,.9,join);
    resolved.style.opacity=reveal.toFixed(3);
    resolved.style.clipPath=`inset(0 ${((1-reveal)*100).toFixed(2)}% 0 0)`;
    resolved.style.transform=`translate(-50%,-50%) scale(${mix(.985,1,reveal).toFixed(4)})`;
    document.querySelector('[data-sheets]').style.opacity=(1-smooth(.91,.98,join)).toFixed(3);
    const state=states.join('|')+';plan:'+reveal.toFixed(3);
    if(state!==lastState) { stage.dataset.scVerifyState=state; lastState=state; }
    stage.dataset.scVerifyHold=String(join>=1);
  }
  function schedule() {
    if(!queued) { queued=true; requestAnimationFrame(paint); }
  }
  function setup() {
    document.body.classList.toggle('motion-ready',!motion.matches);
    document.body.classList.toggle('static-ready',motion.matches);
    if(engine) engine.layout();
    if(motion.matches) staticLayout();
    else schedule();
  }
  document.body.classList.toggle('motion-ready',!motion.matches);
  document.body.classList.toggle('static-ready',motion.matches);
  engine=ScrollCraft.mount(document.body);
  addEventListener('scroll',schedule,{passive:true});
  addEventListener('resize',()=>{ lastState=''; schedule(); },{passive:true});
  addEventListener('load',()=>{ engine.layout(); schedule(); },{once:true});
  motion.addEventListener('change',setup);
  schedule();
})();

