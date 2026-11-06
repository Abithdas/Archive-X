const {chromium}=require('playwright');const {execSync}=require('child_process');
const B='https://127.0.0.1:8443';
const IMG='<svg xmlns="http://www.w3.org/2000/svg" width="640" height="360"><rect width="100%" height="100%" fill="#456"/></svg>';
(async()=>{
 const b=await chromium.launch();
 execSync('./axv/bin/python -W ignore seed.py',{stdio:'ignore'});execSync('./axv/bin/python seed_sugg.py',{stdio:'ignore'});
 const mk=async(opts={})=>{const c=await b.newContext({ignoreHTTPSErrors:true,viewport:{width:1440,height:900},...opts});
   await c.request.post(B+'/api/auth/setup',{data:{username:'admin',password:'correct-horse-battery',confirmation:'correct-horse-battery'},headers:{Origin:B}}).catch(()=>{});
   await c.request.post(B+'/login',{data:{username:'admin',password:'correct-horse-battery'},headers:{Origin:B}});
   await c.route(/\/api\/(thumb|performers\/\d+\/image|studios\/\d+\/image|channels\/[^/]+\/image|play\/preview)/,r=>r.fulfill({status:200,contentType:'image/svg+xml',body:IMG}));
   const held=[];let hold=null;
   await c.route(/\/api\/(library|operations\/resources|metadata\/scene-match-review|metadata\/auto-match-review|settings\/provider|providers|metadata\/providers)/,async r=>{if(hold&&hold.test(r.request().url()))held.push(r);else await r.continue();});
   return {c,held,setHold:x=>hold=x,release:async()=>{hold=null;while(held.length)await held.shift().continue();}};};
 // ---------------- motion: does the shimmer actually move? ----------------
 {const {c,held,setHold,release}=await mk();const p=await c.newPage();await p.goto(B+'/');await p.waitForTimeout(1200);
  await p.evaluate(()=>showView('library'));await p.waitForFunction(()=>_chans&&_chans.length);setHold(/\/api\/library/);await p.evaluate(()=>chAll(true));await p.waitForSelector('#vg .ax-sk-card');
  const t=()=>p.evaluate(()=>{const e=document.querySelector('#vg .ax-sk-card .ax-sk-line');const st=getComputedStyle(e,'::after');const m=new DOMMatrix(st.transform);return {x:Math.round(m.m41*10)/10,name:st.animationName,dur:st.animationDuration,running:st.animationPlayState}});
  const a=await t();await p.waitForTimeout(350);const b2=await t();await p.waitForTimeout(350);const c2=await t();
  console.log('shimmer translateX samples (px):',a.x,b2.x,c2.x,'| animation:',a.name,a.dur,a.running,'| moving:',new Set([a.x,b2.x,c2.x]).size>1);
  await release();await c.close();}
 // ---------------- review dialog + AMR: skeleton vs real ----------------
 {const {c,held,setHold,release}=await mk();const p=await c.newPage();await p.goto(B+'/');await p.waitForTimeout(1200);
  await p.evaluate(()=>showView('admin'));await p.waitForTimeout(400);
  setHold(/scene-match-review/);await p.evaluate(()=>{loadSceneMatchReview()});await p.waitForSelector('#u-assets-scene-match-modal[open] .ax-sk-card',{timeout:5000});await p.waitForTimeout(250);
  const r=(sel)=>p.evaluate(sel=>{const e=document.querySelector(sel);if(!e)return null;const r=e.getBoundingClientRect();return {w:Math.round(r.width),h:Math.round(r.height)}},sel);
  const skA=await r('#u-assets-scene-match-review article'),skF=await r('#u-assets-scene-match-review .ax-scene-match-frame');
  await p.screenshot({path:'shots/skel-review-1440.png'});
  await release();await p.waitForSelector('#u-assets-scene-match-review article:not(.ax-sk-card)');await p.waitForTimeout(900);
  const reA=await r('#u-assets-scene-match-review article[data-suggestion]'),reF=await r('#u-assets-scene-match-review article[data-suggestion] .ax-scene-match-frame');
  console.log('review card   skeleton',JSON.stringify(skA),'real',JSON.stringify(reA),'| frame',JSON.stringify(skF),JSON.stringify(reF));
  await p.evaluate(()=>closeSceneMatchReview());
  setHold(/auto-match-review/);await p.evaluate(()=>{amrOpen()});await p.waitForSelector('#amr .ax-sk-card',{timeout:5000});await p.waitForTimeout(250);
  const skR=await r('#amr .amr-item');await p.screenshot({path:'shots/skel-amr-1440.png'});
  await release();await p.waitForTimeout(1200);
  const reR=await r('#amr .amr-item:not(.ax-sk-card)');
  console.log('amr row       skeleton',JSON.stringify(skR),'real',JSON.stringify(reR));
  await c.close();}
 // ---------------- operations tiles ----------------
 {const {c,held,setHold,release}=await mk();const p=await c.newPage();await p.goto(B+'/');await p.waitForTimeout(1200);
  setHold(/operations\/resources/);await p.evaluate(()=>showView('admin'));await p.waitForTimeout(600);
  console.log('ops tile skeleton inline bars:',await p.evaluate(()=>document.querySelectorAll('#va .ax-resource-card .ax-sk-inline').length));
  await p.locator('.ax-resource-card').first().scrollIntoViewIfNeeded().catch(()=>{});await p.waitForTimeout(200);
  await p.screenshot({path:'shots/skel-ops-1440.png'});
  await release();await p.waitForTimeout(1500);
  console.log('ops tile skeleton bars after load:',await p.evaluate(()=>document.querySelectorAll('#va .ax-resource-card .ax-sk-inline').length));
  await c.close();}
 // ---------------- light theme + reduced motion ----------------
 {const {c,held,setHold,release}=await mk({reducedMotion:'reduce'});const p=await c.newPage();await p.goto(B+'/');await p.waitForTimeout(1200);
  await p.evaluate(()=>{document.documentElement.dataset.theme='light';document.documentElement.dataset.themeMode='light'});
  await p.evaluate(()=>showView('library'));await p.waitForFunction(()=>_chans&&_chans.length);setHold(/\/api\/library/);await p.evaluate(()=>chAll(true));await p.waitForSelector('#vg .ax-sk-card');await p.waitForTimeout(300);
  console.log('reduced motion: ::after display =',await p.evaluate(()=>getComputedStyle(document.querySelector('.ax-sk'),'::after').display),'animation =',await p.evaluate(()=>getComputedStyle(document.querySelector('.ax-sk'),'::after').animationName));
  await p.screenshot({path:'shots/skel-light-1440.png'});
  await release();await c.close();}
 await b.close();
})();
