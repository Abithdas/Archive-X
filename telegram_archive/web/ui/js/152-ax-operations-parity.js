/* ============================================================================
 * FILE: js/152-ax-operations-parity.js
 * PURPOSE: Operations page tidy-up: Jobs heading and the job status filter.
 * AREA: Operations and jobs
 * LOAD: Classic script. Order is set by index.html, the HTML partials and
 *       _LAYERS in web/html.py. Later scripts can wrap earlier globals,
 *       so check docs/CODE_MAP.md before moving code between files.
 * ============================================================================ */
(function(){'use strict';
function tidy(){
  var h=document.querySelector('#va .ax2-ops-active-jobs>h3');
  if(h&&h.textContent!=='Jobs')h.textContent='Jobs';
  var wrap=document.querySelector('#va .ax2-ops-active-jobs'),pan=document.getElementById('jobs-pan');
  if(wrap&&pan&&!wrap.querySelector('.ax-jobs-filter')){
    var f=document.createElement('div');f.className='ax-jobs-filter';f.setAttribute('role','group');
    [['','All'],['running','Running'],['queued','Queued'],['failed','Failed'],['completed','Completed']].forEach(function(t,i){
      var b=document.createElement('button');b.type='button';b.textContent=t[1];b.dataset.f=t[0];if(!i)b.className='on';
      b.addEventListener('click',function(){f.querySelectorAll('button').forEach(function(x){x.classList.toggle('on',x===b)});
        if(t[0])pan.dataset.filter=t[0];else pan.removeAttribute('data-filter');});
      f.appendChild(b);});
    wrap.insertBefore(f,pan);
  }
  var m=document.querySelectorAll('#va .ops-metric>span:last-child');
  m.forEach(function(s){if(s.textContent==='Completed with errors')s.textContent='With errors';});
}
tidy();
var mo=new MutationObserver(function(){tidy()});
var t=document.getElementById('va');if(t)mo.observe(t,{childList:true,subtree:true});
})();
