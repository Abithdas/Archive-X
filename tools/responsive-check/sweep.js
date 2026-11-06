const {chromium}=require('playwright');
const fs=require('fs');const {execSync}=require('child_process');
const BASE='https://127.0.0.1:8443';
const VPS=(process.env.VPS||'320x568,360x640,375x667,390x844,414x896,430x932,600x960,768x1024,820x1180,1024x768,1024x1366,1280x720,1440x900,1920x1080,2560x1440,667x375,844x390').split(',').map(s=>s.split('x').map(Number));
const SHOT=(process.env.SHOT||'').split(',');
const TAG=process.env.TAG||'before';
const ONLY=process.env.ONLY?process.env.ONLY.split(','):null;
const svg=(w,h,seed)=>`<svg xmlns="http://www.w3.org/2000/svg" width="${w}" height="${h}"><defs><linearGradient id="g" x1="0" y1="0" x2="1" y2="1"><stop offset="0" stop-color="hsl(${seed*47%360},45%,38%)"/><stop offset="1" stop-color="hsl(${(seed*47+60)%360},50%,22%)"/></linearGradient></defs><rect width="100%" height="100%" fill="url(#g)"/></svg>`;
const SCEN=[
 ['library',async p=>{await p.evaluate(()=>showView('library'));await p.waitForFunction(()=>typeof _chans!=='undefined'&&_chans.length>0,null,{timeout:8000}).catch(()=>{});await p.evaluate(()=>chAll(true));await p.waitForSelector('.vc',{timeout:8000}).catch(()=>{});await p.waitForTimeout(700);await p.evaluate(()=>{const c=document.querySelector('.vc');if(c)c.scrollIntoView({block:'start'})});await p.waitForTimeout(500)}],
 ['filters',async p=>{await p.evaluate(()=>showView('library'));await p.waitForTimeout(400);await p.evaluate(()=>uxOpenFilters());await p.waitForTimeout(700)}],
 ['video',async p=>{await p.evaluate(()=>{try{closeDBtn(true)}catch(e){}});await p.evaluate(()=>showView('library'));await p.waitForTimeout(500);await p.evaluate(()=>openD(1003,'-1004',true));await p.waitForTimeout(2000)}],
 ['performers',async p=>{await p.evaluate(()=>showView('performers'));await p.waitForTimeout(1200)}],
 ['performer-detail',async p=>{await p.evaluate(()=>{showView('performers');});await p.waitForTimeout(800);await p.evaluate(()=>openPerf(1));await p.waitForTimeout(1500)}],
 ['sites',async p=>{await p.evaluate(()=>showView('sites'));await p.waitForTimeout(1200)}],
 ['site-detail',async p=>{await p.evaluate(()=>showView('sites'));await p.waitForTimeout(800);await p.evaluate(()=>openSite(1));await p.waitForTimeout(1500)}],
 ['photos',async p=>{await p.evaluate(()=>showView('photos'));await p.waitForTimeout(900)}],
 ['transfers',async p=>{await p.evaluate(()=>showView('transfers'));await p.waitForTimeout(700)}],
 ['admin',async p=>{await p.evaluate(()=>showView('admin'));await p.waitForTimeout(900)}],
 ['settings',async p=>{await p.evaluate(()=>showView('settings'));await p.waitForTimeout(700)}],
 ['ai',async p=>{await p.evaluate(()=>showView('ai'));await p.waitForTimeout(700)}],
];
const METRICS=()=>{
  const vw=document.documentElement.clientWidth, vh=document.documentElement.clientHeight, doc=document.documentElement;
  const vis=el=>{const s=getComputedStyle(el);if(s.display==='none'||s.visibility==='hidden'||+s.opacity===0)return false;const r=el.getBoundingClientRect();return r.width>0&&r.height>0};
  const clipped=(el)=>{for(let p=el.parentElement;p&&p!==document.body;p=p.parentElement){const s=getComputedStyle(p);if(/(auto|scroll|hidden|clip)/.test(s.overflowX)&&p.getBoundingClientRect().width<=vw+1)return true}return false};
  const sel=el=>{let t=el.tagName.toLowerCase();if(el.id)t+='#'+el.id;const c=[...el.classList].slice(0,2).join('.');if(c)t+='.'+c;return t};
  const issues={overflow:[],tinyText:[],smallTarget:[],clippedText:[],offscreenFixed:[]};
  const seen={};const add=(k,el,extra='')=>{const key=sel(el);if(seen[k+key])return;seen[k+key]=1;if(issues[k].length<10)issues[k].push(key+extra)};
  for(const el of document.body.querySelectorAll('*')){
    if(!vis(el))continue;
    const r=el.getBoundingClientRect(),s=getComputedStyle(el);
    if(s.position==='fixed'){if(r.right>vw+2||r.left<-2||r.bottom>vh+60&&r.top<vh)add('offscreenFixed',el,` [${Math.round(r.left)}..${Math.round(r.right)}]`);}
    else if(r.right>vw+1&&r.width<vw*3&&!clipped(el))add('overflow',el,` [${Math.round(r.left)}..${Math.round(r.right)}]`);
    const hasText=[...el.childNodes].some(n=>n.nodeType===3&&n.textContent.trim().length>1);
    const fs=parseFloat(s.fontSize);
    if(hasText&&fs<10.5&&!el.matches('.ax2-more-dots'))add('tinyText',el,` ${fs}px`);
    if(vw<=900&&!el.matches('.ax2-command-kbd,#gs,input[type=range],.video-performer-avatar-wrap,.video-performer-chip,.video-tag-chip,.tag,.dsection,.rx-more3,.ax2-more-dots')&&(el.matches('button,a[href],input:not([type=hidden]),select,[role=button],[role=tab],[role=menuitem]')))
      {const m=Math.min(r.width,r.height);if(m<30&&r.width>0)add('smallTarget',el,` ${Math.round(r.width)}x${Math.round(r.height)}`)}
    if(hasText&&/(hidden|clip)/.test(s.overflowX)&&el.scrollWidth>el.clientWidth+3&&s.textOverflow!=='ellipsis'&&!/-webkit-box|flex/.test(s.display))add('clippedText',el,` ${el.scrollWidth}>${el.clientWidth}`);
  }
  return {vw,vh,scrollW:doc.scrollWidth,hScroll:doc.scrollWidth>vw+1,issues};
};
(async()=>{
  const b=await chromium.launch();
  const ctx=await b.newContext({ignoreHTTPSErrors:true,viewport:{width:1280,height:800}});
  await ctx.request.post(BASE+'/login',{data:{username:'admin',password:'correct-horse-battery'},headers:{Origin:BASE}});
  let n=0;
  await ctx.route(/\/api\/(thumb|performers\/\d+\/image|studios\/\d+\/image|channels\/[^/]+\/image)/,r=>{n++;const u=r.request().url();const portrait=/thumb\/-1002/.test(u);r.fulfill({status:200,contentType:'image/svg+xml',body:svg(portrait?360:640,portrait?640:360,n)})});
  const page=await ctx.newPage();
  const out=[];
  for(const [w,h] of VPS){
    for(let t=0;t<3;t++){const r=await ctx.request.get(BASE+'/api/library?per=1');const j=await r.json().catch(()=>({total:0}));if(j.total>0)break;execSync('./axv/bin/python -W ignore seed.py',{stdio:'ignore'});}
    await page.setViewportSize({width:w,height:h});
    await page.goto(BASE+'/',{waitUntil:'domcontentloaded'});
    await page.waitForTimeout(1500);
    for(const [name,fn] of SCEN){
      if(ONLY&&!ONLY.includes(name))continue;
      if(['library','video','performer-detail','site-detail','filters'].includes(name)){const r=await ctx.request.get(BASE+'/api/library?per=1');const j=await r.json().catch(()=>({total:0}));if(!(j.total>0))execSync('./axv/bin/python -W ignore seed.py',{stdio:'ignore'});}
      try{await fn(page)}catch(e){console.log('scenario error',name,w,h,String(e).slice(0,120))}
      const m=await page.evaluate(METRICS);
      out.push({w,h,view:name,...m});
      if(SHOT.includes(w+'x'+h))await page.screenshot({path:`shots/${TAG}-${name}-${w}x${h}.png`});
    }
  }
  fs.writeFileSync(`sweep2-${TAG}.json`,JSON.stringify(out));
  const tot={};let bad=0;
  for(const o of out){let c=0;for(const k in o.issues){tot[k]=(tot[k]||0)+o.issues[k].length;c+=o.issues[k].length}if(o.hScroll||c)bad++}
  console.log('combos',out.length,'with issues',bad,JSON.stringify(tot));
  await b.close();
})();
