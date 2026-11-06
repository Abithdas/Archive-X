const {chromium}=require('playwright');
const B=process.env.BASE||'https://127.0.0.1:8443';
(async()=>{
 const b=await chromium.launch();const c=await b.newContext({ignoreHTTPSErrors:true,viewport:{width:1440,height:900}});
 await c.request.post(B+'/api/auth/setup',{data:{username:'admin',password:'correct-horse-battery',confirmation:'correct-horse-battery'},headers:{Origin:B}}).catch(()=>{});
 await c.request.post(B+'/login',{data:{username:'admin',password:'correct-horse-battery'},headers:{Origin:B}});
 await c.route(/\/api\/(thumb|performers\/\d+\/image|play\/preview)/,r=>r.fulfill({status:200,contentType:'image/svg+xml',body:'<svg xmlns="http://www.w3.org/2000/svg" width="640" height="360"><rect width="100%" height="100%" fill="#456"/></svg>'}));
 const p=await c.newPage();await p.goto(B+'/');await p.waitForTimeout(1500);
 await p.evaluate(()=>showView('admin'));await p.waitForTimeout(500);
 await p.evaluate(()=>loadSceneMatchReview());
 await p.waitForSelector('#u-assets-scene-match-modal[open] article.ax-scene-match-review',{timeout:15000});
 await p.waitForTimeout(800);
 // instrumentation
 await p.evaluate(()=>{
   const d=document.getElementById('u-assets-scene-match-modal'),box=document.getElementById('u-assets-scene-match-review');
   window.__t={closes:0,opens:0,loadingSeen:0,leaving:0,removed:[]};
   d.addEventListener('close',()=>window.__t.closes++);
   new MutationObserver(m=>{for(const x of m){x.addedNodes.forEach(n=>{if(n.nodeType===1&&/Loading saved suggestions/.test(n.textContent||''))window.__t.loadingSeen++;});
     x.removedNodes.forEach(n=>{if(n.nodeType===1&&n.matches&&n.matches('article.ax-scene-match-review'))window.__t.removed.push(n.dataset.suggestion)});
     if(x.type==='attributes'&&x.target.classList&&x.target.classList.contains('is-leaving'))window.__t.leaving++;}}).observe(box,{childList:true,subtree:true,attributes:true,attributeFilter:['class']});
   const arts=[...box.querySelectorAll('article[data-suggestion]')];arts.forEach(a=>a.__mark=a.dataset.suggestion);
   window.__before=arts.map(a=>a.dataset.suggestion);
 });
 const before=await p.evaluate(()=>window.__before);
 console.log('cards before:',before.length,before.slice(0,4));
 // click Approve on first card
 const first=await p.$('article[data-suggestion] .ax-scene-match-review-actions button.pri');
 await first.click();
 await p.waitForTimeout(150);
 await p.screenshot({path:'shots/review-toast-'+(process.env.TAG||'new')+'.png'});
 const mid=await p.evaluate(()=>({leavingNow:document.querySelectorAll('article.is-leaving').length,dialogOpen:document.getElementById('u-assets-scene-match-modal').open}));
 // toast stacking check while the toast is visible
 const toastInfo=await p.evaluate(()=>{const t=document.getElementById('toast');const it=t.querySelector('.ax2-toast-item');if(!it)return {toast:'none'};const r=it.getBoundingClientRect();const hit=document.elementFromPoint(r.left+r.width/2,r.top+r.height/2);const hr=t.getBoundingClientRect();return {hostRect:[Math.round(hr.left),Math.round(hr.top),Math.round(hr.width),Math.round(hr.height)],itemRect:[Math.round(r.left),Math.round(r.top),Math.round(r.width),Math.round(r.height)],parent:t.parentElement.id||t.parentElement.tagName,vis:getComputedStyle(it).opacity,popoverOpen:t.matches(':popover-open'),hitIsToast:!!hit&&t.contains(hit),hit:hit?hit.tagName+'.'+hit.className.toString().slice(0,40):null,text:it.textContent.trim().slice(0,60)}});
 await p.waitForTimeout(1200);
 const after=await p.evaluate(()=>{const box=document.getElementById('u-assets-scene-match-review');const arts=[...box.querySelectorAll('article[data-suggestion]')];return {t:window.__t,ids:arts.map(a=>a.dataset.suggestion),keptSameNodes:arts.every(a=>a.__mark===a.dataset.suggestion),open:document.getElementById('u-assets-scene-match-modal').open,count:document.getElementById('u-assets-scene-match-count').textContent}});
 console.log('mid:',JSON.stringify(mid));
 console.log('toast:',JSON.stringify(toastInfo));
 console.log('after:',JSON.stringify(after));
 // reject the next one too
 const rej=await p.$('article[data-suggestion] .ax-scene-match-review-actions button:not(.pri)');
 await rej.click();await p.waitForTimeout(1400);
 const after2=await p.evaluate(()=>({t:window.__t,ids:[...document.querySelectorAll('article[data-suggestion]')].map(a=>a.dataset.suggestion),open:document.getElementById('u-assets-scene-match-modal').open,count:document.getElementById('u-assets-scene-match-count').textContent}));
 console.log('after reject:',JSON.stringify(after2));
 await p.screenshot({path:'shots/review-'+(process.env.TAG||'new')+'.png'});
 await b.close();
})();
