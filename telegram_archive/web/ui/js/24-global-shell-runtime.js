/* ============================================================================
 * FILE: js/24-global-shell-runtime.js
 * PURPOSE: Keeps the top bar fixed in place.
 * AREA: Shell and navigation
 * LOAD: Classic script. Order is set by index.html, the HTML partials and
 *       _LAYERS in web/html.py. Later scripts can wrap earlier globals,
 *       so check docs/CODE_MAP.md before moving code between files.
 * ============================================================================ */

(function(){
  'use strict';
  const enforce=()=>{
    const el=document.getElementById('topbar');
    if(!el)return;
    el.style.setProperty('position','fixed','important');
    el.style.setProperty('top','0','important');
    el.style.setProperty('left','0','important');
    el.style.setProperty('right','0','important');
    el.style.setProperty('width','100%','important');
    el.style.setProperty('z-index','1500','important');
  };
  if(document.readyState==='loading')document.addEventListener('DOMContentLoaded',enforce,{once:true});else enforce();
  window.addEventListener('resize',enforce,{passive:true});
})();
