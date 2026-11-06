/* ============================================================================
 * FILE: js/16-player-visual-state.js
 * PURPOSE: Keeps the player's visual container in sync with the selected video's dimensions.
 * AREA: Player and watch page
 * LOAD: Classic script. Order is set by index.html, the HTML partials and
 *       _LAYERS in web/html.py. Later scripts can wrap earlier globals,
 *       so check docs/CODE_MAP.md before moving code between files.
 * ============================================================================ */
(function(){
  function $p(id){return document.getElementById(id);}
  function syncPlayerVisualState(){
    const v=$p('pv'), visual=$p('pvisual'); if(!visual)return;
    const hint=window.__axPlayerDimensionHint;
    let w=0,h=0;
    // Never use stale intrinsic dimensions from the previous media after a new
    // card has been selected. The selection hint is published before playback.
    if(hint&&hint.key){
      w=Number(hint.width)||0; h=Number(hint.height)||0;
      if(!w||!h){w=16;h=9;}
    }else if(v&&v.readyState>=1&&Number(v.videoWidth)>0&&Number(v.videoHeight)>0){
      w=Number(v.videoWidth);h=Number(v.videoHeight);
    }
    if(!w||!h){w=16;h=9;}
    const ratio=w/h;
    visual.dataset.orientation=ratio<0.92?'portrait':(ratio<=1.08?'square':'landscape');
    visual.style.setProperty('--ax-player-aspect-ratio',`${w} / ${h}`);
    visual.style.removeProperty('height');
  }
  window.syncPlayerVisualState=syncPlayerVisualState;
  document.addEventListener('DOMContentLoaded',()=>{
    syncPlayerVisualState();
    window.addEventListener('resize',syncPlayerVisualState,{passive:true});
    const v=$p('pv');
    if(v){['loadedmetadata','durationchange','resize'].forEach(name=>v.addEventListener(name,()=>{
      const hint=window.__axPlayerDimensionHint;
      if(hint&&hint.key&&v.dataset.axMediaKey===hint.key&&v.readyState>=1&&v.videoWidth>0&&v.videoHeight>0&&window.setArchiveXPlayerDimensionHint){
        window.setArchiveXPlayerDimensionHint({key:hint.key,width:v.videoWidth,height:v.videoHeight,waitingForMetadata:false});
      }else syncPlayerVisualState();
    },{passive:true}));}
  });
})();
