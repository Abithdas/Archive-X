/* ============================================================================
 * FILE: js/27-large-screen-zoom.js
 * AREA: Responsive and input
 * LOAD: Classic script. Order is set by index.html, the HTML partials and
 *       _LAYERS in web/html.py. Later scripts can wrap earlier globals,
 *       so check docs/CODE_MAP.md before moving code between files.
 * ============================================================================ */
/* Archive X v29.4.295 — continuous wide/8K scale plus viewport-unit correction.
   Device pixel ratio intentionally does not participate: layout is expressed in CSS px.
   Viewports <=1920 CSS px remain at document zoom 1. Larger viewports use a smooth
   area-based factor so 4K is about 2x, 8K is capped at 4x, and portrait/ultrawide
   displays incorporate both width and height rather than scaling by width alone. */
(function(){
  'use strict';
  const root=document.documentElement;
  const UNIT=/(-?\d*\.?\d+)(dvh|svh|lvh|dvw|svw|lvw|vmin|vmax|vh|vw)\b/g;
  const HAS=/\d(?:dvh|svh|lvh|dvw|svw|lvw|vmin|vmax|vh|vw)\b/;
  const saved=new Map();
  let scanned=false, applied=1, largeScale=1, resizeTimer=0, observer=null;
  function remember(style){
    if(!style||saved.has(style))return null;
    const list=[];
    for(let i=0,n=style.length;i<n;i++){
      const p=style[i],v=style.getPropertyValue(p);
      if(v&&HAS.test(v))list.push([p,v,style.getPropertyPriority(p)]);
    }
    if(!list.length)return null;
    saved.set(style,list);return [style,list];
  }
  function set(style,list,z){
    for(const [p,v,pr] of list)style.setProperty(p,z===1?v:v.replace(UNIT,(m,n,u)=>`calc(${n}${u} / ${z})`),pr);
  }
  function walk(rules){
    for(const r of rules){
      if(typeof CSSKeyframesRule!=='undefined'&&r instanceof CSSKeyframesRule)continue;
      if(r.cssRules)walk(r.cssRules);
      if(r.style)remember(r.style);
    }
  }
  function scan(){for(const sheet of document.styleSheets){try{walk(sheet.cssRules);}catch(_){}}scanned=true;}
  function scanInline(base){
    const found=[];
    (base||document).querySelectorAll('[style*="vh"],[style*="vw"],[style*="vmin"],[style*="vmax"]').forEach(el=>{const r=remember(el.style);if(r)found.push(r);});
    return found;
  }
  function watchNewElements(){
    if(observer||!document.body)return;
    observer=new MutationObserver(muts=>{
      if(applied===1)return;
      for(const m of muts)for(const n of m.addedNodes){
        if(n.nodeType!==1)continue;
        const found=scanInline(n);
        if(n.getAttribute&&/v[hw]|vmin|vmax/.test(n.getAttribute('style')||'')){const r=remember(n.style);if(r)found.push(r);}
        for(const [style,list] of found)set(style,list,applied);
      }
    });
    observer.observe(document.body,{subtree:true,childList:true});
  }
  function computedAppZoom(){
    const node=document.getElementById('ax-ui-scale-root');
    const a=node?parseFloat(getComputedStyle(node).zoom):1;
    return Number.isFinite(a)&&a>0?a:1;
  }
  function combinedZoom(){
    const h=parseFloat(getComputedStyle(root).zoom);
    return (Number.isFinite(h)&&h>0?h:1)*computedAppZoom();
  }
  function desiredScale(){
    const w=Math.max(1,window.innerWidth||root.clientWidth||1);
    const h=Math.max(1,window.innerHeight||root.clientHeight||1);
    if(w<=1920)return 1;
    const widthFactor=w/1920, heightFactor=h/1080;
    const areaFactor=Math.sqrt(widthFactor*heightFactor);
    return Math.min(4,Math.max(1,Math.round(areaFactor*1000)/1000));
  }
  function applyCompensation(){
    const z=combinedZoom();
    if(Math.abs(z-applied)<.0005)return;
    if(z!==1){if(!scanned)scan();scanInline();watchNewElements();}
    saved.forEach((list,style)=>set(style,list,z));
    applied=z;
  }
  function refreshScale(){
    const next=desiredScale();
    if(Math.abs(next-largeScale)>.001){
      largeScale=next;
      root.style.zoom=next===1?'':String(next);
      root.style.setProperty('--ax-responsive-layout-scale',String(next));
    }
    applyCompensation();
  }
  window.axZoomFactor=combinedZoom;
  window.axZoomCompensate=applyCompensation;
  if(document.readyState==='loading')document.addEventListener('DOMContentLoaded',()=>{refreshScale();watchNewElements();},{once:true});
  else {refreshScale();watchNewElements();}
  window.addEventListener('resize',()=>{clearTimeout(resizeTimer);resizeTimer=setTimeout(refreshScale,80);},{passive:true});
  window.addEventListener('orientationchange',()=>{clearTimeout(resizeTimer);resizeTimer=setTimeout(refreshScale,80);},{passive:true});
})();
