const {chromium}=require('playwright');const B='https://127.0.0.1:8443';
(async()=>{
 const b=await chromium.launch();
 for(const [w,h] of [[1440,900],[390,844]]){
 const c=await b.newContext({ignoreHTTPSErrors:true,viewport:{width:w,height:h}});
 await c.request.post(B+'/login',{data:{username:'admin',password:'correct-horse-battery'},headers:{Origin:B}});
 const p=await c.newPage();const errs=[];p.on('pageerror',e=>errs.push(String(e)));
 await p.goto(B+'/');await p.waitForTimeout(1500);
 const st=()=>p.evaluate(()=>{const t=document.getElementById('toast');const it=t.querySelector('.ax2-toast-item.is-visible');const r=it?it.getBoundingClientRect():null;return {parent:t.parentElement.id||t.parentElement.tagName,inDialog:!!t.closest('dialog'),popoverOpen:t.matches(':popover-open'),hidden:t.hidden,visibleItems:t.querySelectorAll('.ax2-toast-item.is-visible').length,rect:r?[Math.round(r.left),Math.round(r.top),Math.round(r.width),Math.round(r.height)]:null,vw:innerWidth,vh:innerHeight}});
 await p.evaluate(()=>toast('Plain toast without a dialog.'));await p.waitForTimeout(450);
 console.log(w+'px 1 no dialog      ',JSON.stringify(await st()));
 await p.screenshot({path:`shots/toast-nodialog-${w}.png`});
 await p.evaluate(()=>showView('admin'));await p.waitForTimeout(300);
 await p.evaluate(()=>loadSceneMatchReview());await p.waitForSelector('#u-assets-scene-match-modal[open]');await p.waitForTimeout(600);
 await p.evaluate(()=>toast('Toast inside the dialog.'));await p.waitForTimeout(450);
 console.log(w+'px 2 dialog open    ',JSON.stringify(await st()));
 await p.screenshot({path:`shots/toast-dialog-${w}.png`});
 await p.evaluate(()=>closeSceneMatchReview());await p.waitForTimeout(450);
 console.log(w+'px 3 dialog closed  ',JSON.stringify(await st()));
 await p.evaluate(()=>toast('Toast after the dialog closed.'));await p.waitForTimeout(450);
 console.log(w+'px 4 toast after    ',JSON.stringify(await st()));
 console.log(w+'px page errors:',errs.length?errs:'none');
 await c.close();}
 await b.close();
})();
