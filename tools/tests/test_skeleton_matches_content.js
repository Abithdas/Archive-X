const {chromium}=require('playwright');
const {execSync}=require('child_process');
const B='https://127.0.0.1:8443';
const TAG=process.env.TAG||'new';
const VPS=(process.env.VPS||'390x844,768x1024,1440x900').split(',').map(s=>s.split('x').map(Number));
const IMG='<svg xmlns="http://www.w3.org/2000/svg" width="640" height="360"><rect width="100%" height="100%" fill="#456"/></svg>';
(async()=>{
 const b=await chromium.launch();
 for(const [w,h] of VPS){
  execSync('./axv/bin/python -W ignore seed.py',{stdio:'ignore'});
  const c=await b.newContext({ignoreHTTPSErrors:true,viewport:{width:w,height:h}});
  await c.request.post(B+'/api/auth/setup',{data:{username:'admin',password:'correct-horse-battery',confirmation:'correct-horse-battery'},headers:{Origin:B}}).catch(()=>{});
  await c.request.post(B+'/login',{data:{username:'admin',password:'correct-horse-battery'},headers:{Origin:B}});
  await c.route(/\/api\/(thumb|performers\/\d+\/image|studios\/\d+\/image|channels\/[^/]+\/image)/,r=>r.fulfill({status:200,contentType:'image/svg+xml',body:IMG}));
  const held=[];let hold=null;                       // hold = regex of requests to freeze
  await c.route(/\/api\/(library|performers|studios|channels|source-channels|metadata\/scene-match-review|metadata\/auto-match-review)(\?|$|\/\d+\/videos)/,async r=>{
    if(hold&&hold.test(r.request().url())){held.push(r)}else await r.continue();});
  const release=async()=>{hold=null;while(held.length)await held.shift().continue();};
  const p=await c.newPage();const errs=[];p.on('pageerror',e=>errs.push(String(e)));
  const rect=(sel,idx=0)=>p.evaluate(([sel,idx])=>{const e=document.querySelectorAll(sel)[idx];if(!e)return null;const r=e.getBoundingClientRect();return {w:Math.round(r.width),h:Math.round(r.height),x:Math.round(r.left),y:Math.round(r.top)}},[sel,idx]);
  const cols=sel=>p.evaluate(sel=>new Set([...document.querySelectorAll(sel)].map(e=>Math.round(e.getBoundingClientRect().left))).size,sel);
  const anim=()=>p.evaluate(()=>{const e=document.querySelector('.ax-sk');return e?getComputedStyle(e,'::after').animationName:'none'});
  const report=(name,sk,real,ck,cr)=>{const dh=sk&&real?Math.round((sk.h-real.h)/real.h*100):null;const dw=sk&&real?Math.round((sk.w-real.w)/real.w*100):null;
    console.log(`${w}x${h} ${name.padEnd(17)} skeleton ${sk?sk.w+'x'+sk.h:'—'}  real ${real?real.w+'x'+real.h:'—'}  Δw ${dw}%  Δh ${dh}%  cols ${ck}/${cr}`)};
  // ---------- library video grid
  await p.goto(B+'/');await p.waitForTimeout(1200);
  await p.evaluate(()=>showView('library'));await p.waitForFunction(()=>_chans&&_chans.length);
  hold=/\/api\/library/;await p.evaluate(()=>chAll(true));await p.waitForSelector('#vg .ax-sk-card',{timeout:5000});await p.waitForTimeout(200);
  const skV=await rect('#vg .vc',0),ckV=await cols('#vg .vc');
  await p.screenshot({path:`shots/skel-${TAG}-video-${w}.png`});
  console.log('   shimmer animation:',await anim());
  await release();await p.waitForSelector('#vg .vc:not(.ax-sk-card)');await p.waitForTimeout(500);
  report('video card',skV,await rect('#vg .vc',0),ckV,await cols('#vg .vc'));
  console.log('   leftover skeleton nodes after load:',await p.evaluate(()=>document.querySelectorAll('.ax-sk-card').length));
  // ---------- performers
  hold=/\/api\/performers(\?|$)/;await p.evaluate(()=>{_perfAll=[];loadPerformers&&sv('performers')});
  await p.waitForSelector('#perf-list .ax-sk-card',{timeout:5000}).catch(()=>{});await p.waitForTimeout(200);
  const skP=await rect('#perf-list .pcard',0),ckP=await cols('#perf-list .pcard');
  await p.screenshot({path:`shots/skel-${TAG}-performers-${w}.png`});
  await release();await p.waitForSelector('#perf-list .pcard:not(.ax-sk-card)');await p.waitForTimeout(500);
  report('performer card',skP,await rect('#perf-list .pcard',0),ckP,await cols('#perf-list .pcard'));
  // ---------- studios
  hold=/\/api\/studios(\?|$)/;await p.evaluate(()=>{sv('sites')});
  await p.waitForSelector('#site-list .ax-sk-card',{timeout:5000}).catch(()=>{});await p.waitForTimeout(200);
  const skS=await rect('#site-list .pcard',0),ckS=await cols('#site-list .pcard');
  await p.screenshot({path:`shots/skel-${TAG}-studios-${w}.png`});
  await release();await p.waitForSelector('#site-list .pcard:not(.ax-sk-card)');await p.waitForTimeout(500);
  report('studio card',skS,await rect('#site-list .pcard',0),ckS,await cols('#site-list .pcard'));
  // ---------- performer detail hero
  await p.goto(B+'/');await p.waitForTimeout(1200);
  hold=/\/api\/performers\/\d+\/videos/;await p.evaluate(()=>{openPerf(1)});
  await p.waitForSelector('#perf-detail .ax-sk-hero',{timeout:5000}).catch(()=>{});await p.waitForTimeout(200);
  const skH=await rect('#perf-detail .ax2-entity-hero'),skHm=await rect('#perf-detail .ax2-entity-media'),skT=await rect('#perf-detail .ax2-entity-tabs'),skG=await rect('#perf-detail .vc',0);
  await p.screenshot({path:`shots/skel-${TAG}-performer-detail-${w}.png`});
  await release();await p.waitForTimeout(2500);console.log('   [dbg] after release:',JSON.stringify(await p.evaluate(()=>({open:document.getElementById('perf-detail').classList.contains('open'),html:document.getElementById('perf-detail').innerHTML.slice(0,90),status:(document.getElementById('st')||{}).textContent,cur:_perfCurId}))));await p.waitForSelector('#perf-detail h1.ax2-entity-title:not(.ax-sk-group *)',{timeout:8000}).catch(()=>{});await p.waitForTimeout(900);
  report('perf hero',skH,await rect('#perf-detail .ax2-entity-hero'),'-','-');
  report('perf hero media',skHm,await rect('#perf-detail .ax2-entity-media'),'-','-');
  report('perf tabs',skT,await rect('#perf-detail .ax2-entity-tabs'),'-','-');
  report('perf video card',skG,await rect('#perf-detail .vc',0),'-','-');
  // ---------- studio detail hero
  await p.goto(B+'/');await p.waitForTimeout(1200);
  hold=/\/api\/studios\/\d+\/videos/;await p.evaluate(()=>{openSite(1)});
  await p.waitForSelector('#site-detail .ax-sk-hero',{timeout:5000}).catch(()=>{});await p.waitForTimeout(200);
  const skSH=await rect('#site-detail .ax2-entity-hero');
  await p.screenshot({path:`shots/skel-${TAG}-studio-detail-${w}.png`});
  await release();await p.waitForTimeout(2200);await p.waitForSelector('#site-detail h1.ax2-entity-title:not(.ax-sk-group *)',{timeout:8000}).catch(()=>{});await p.waitForTimeout(600);
  report('studio hero',skSH,await rect('#site-detail .ax2-entity-hero'),'-','-');
  console.log(`${w}x${h} page errors:`,errs.length?errs:'none');
  await c.close();
 }
 await b.close();
})();
