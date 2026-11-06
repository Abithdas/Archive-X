/* ============================================================================
 * FILE: js/75-loading-feedback-patterns.js
 * AREA: Motion and feedback
 * LOAD: Classic script. Order is set by index.html, the HTML partials and
 *       _LAYERS in web/html.py. Later scripts can wrap earlier globals,
 *       so check docs/CODE_MAP.md before moving code between files.
 * ============================================================================ */
/* V29.4.204: consistent spinner, skeleton, measured-progress and step feedback.
   Progress bars only use real counters; no synthetic percent timers are used. */
(function(){
  'use strict';
  const $=id=>document.getElementById(id);
  const escText=value=>String(value??'').replace(/&/g,'&amp;').replace(/</g,'&lt;').replace(/>/g,'&gt;').replace(/\"/g,'&quot;').replace(/'/g,'&#39;');
  // One shared status scale for measurable progress and app-wide status signals.
  // The RGB stops mirror the reference scale and are interpolated smoothly.
  const STATUS_STOPS=[
    {at:0,rgb:[220,47,80]},
    {at:25,rgb:[255,111,78]},
    {at:50,rgb:[255,195,14]},
    {at:75,rgb:[35,159,140]},
    {at:100,rgb:[49,191,121]}
  ];
  const STATUS_COLORS={poor:'#dc2f50',low:'#ff6f4e',fair:'#ffc30e',goodMid:'#239f8c',good:'#31bf79',neutral:null};
  function colorForPercent(value){
    const pct=Math.max(0,Math.min(100,Number(value)||0));
    let left=STATUS_STOPS[0],right=STATUS_STOPS[STATUS_STOPS.length-1];
    for(let i=1;i<STATUS_STOPS.length;i++){if(pct<=STATUS_STOPS[i].at){left=STATUS_STOPS[i-1];right=STATUS_STOPS[i];break;}}
    const f=(pct-left.at)/(right.at-left.at);
    const rgb=left.rgb.map((v,i)=>Math.round(v+(right.rgb[i]-v)*f));
    return `rgb(${rgb[0]}, ${rgb[1]}, ${rgb[2]})`;
  }
  function statusTone(status){
    const s=String(status??'').trim().toUpperCase().replace(/[\s-]+/g,'_');
    if(/^(FAILED|FAILURE|ERROR|FATAL|CRITICAL|BLOCKED|OFFLINE|DISCONNECTED|INVALID|CANCELLED_WITH_ERROR)$/.test(s))return 'poor';
    if(/(DEGRADED|SUSPECTED|STALLED|WARNING|ATTENTION|INCOMPLETE|COMPLETED_WITH_ERRORS|PARTIAL|LAGGING)/.test(s))return 'low';
    if(/^(QUEUED|WAITING|PENDING|PAUSED|PAUSING|RETRYING|UNKNOWN|IDLE|DISABLED|NOT_CONFIGURED|NOT_READY)$/.test(s))return 'fair';
    if(/^(RUNNING|RECOVERING|PROCESSING|ACTIVE|CONNECTING|SYNCING|STARTING|DOWNLOADING|UPLOADING|IN_PROGRESS|WORKING)$/.test(s))return 'goodMid';
    if(/^(COMPLETED|SUCCESS|SUCCESSFUL|HEALTHY|READY|MATCHED|SYNCED|CONNECTED|PUBLISHED|VERIFIED|FINISHED|ONLINE|AVAILABLE)$/.test(s))return 'good';
    return 'neutral';
  }
  function colorForStatus(status){const tone=statusTone(status);return STATUS_COLORS[tone]||null;}
  function setStatusIndicator(element,status){
    if(!element)return element;
    const tone=statusTone(status);
    element.classList.add('ax-status-indicator');
    element.dataset.axStatusTone=tone;
    return element;
  }
  function rollerMarkup(size='sm',extraClass=''){
    const allowedSizes=new Set(['xs','sm','md','lg','xl']);
    const safeSize=allowedSizes.has(String(size))?String(size):'sm';
    const safeExtra=String(extraClass||'').split(/\s+/).filter(x=>/^[a-z0-9_-]+$/i.test(x)).join(' ');
    return `<span class="ax-lds-roller ${safeSize}${safeExtra?` ${safeExtra}`:''}" aria-hidden="true">${'<i></i>'.repeat(8)}</span>`;
  }
  function spinner(label='',size='sm'){
    return `<span class="ax-fb-inline" role="status" aria-live="polite">${rollerMarkup(size)}${label?`<span>${escText(label)}</span>`:''}</span>`;
  }
  // Skeleton markup now comes from js/192-shimmer-skeleton.js (AXSkeleton): one shimmer system whose
  // placeholders reuse the real card classes so each type matches its actual content. These names are
  // kept for existing callers.
  function skeletonCard(type='video'){return window.AXSkeleton?window.AXSkeleton.cards(type,1):'';}
  function skeletonGrid(type='video',count=6){return window.AXSkeleton?window.AXSkeleton.cards(type,count):'';}
  function progressMarkup(current,total,title,detail=''){
    const max=Math.max(1,Number(total)||1),value=Math.max(0,Math.min(max,Number(current)||0)),pct=Math.max(0,Math.min(100,Math.round(value/max*100)));
    return `<div class="ax-fb-progress-wrap" role="group" aria-label="${escText(title)}"><div class="ax-fb-progress-head"><span class="ax-fb-progress-title">${escText(title)}</span><span class="ax-fb-progress-detail">${pct}% · ${value} / ${max}</span></div><div class="ax-fb-progress-track" role="progressbar" aria-label="${escText(title)}" aria-valuemin="0" aria-valuemax="${max}" aria-valuenow="${value}" aria-valuetext="${pct}%"><div class="ax-fb-progress-fill" style="--ax-fb-progress:${pct}%;--ax-fb-status-color:${colorForPercent(pct)}"></div></div>${detail?`<div class="ax-fb-progress-detail">${escText(detail)}</div>`:''}</div>`;
  }
  function stepsMarkup(steps,current,summaryLabel='Progress'){
    const items=(Array.isArray(steps)?steps:[]).map((s,i)=>({label:String(s),n:i+1}));
    if(!items.length)return '';
    const cur=Math.max(1,Math.min(items.length,Number(current)||1));
    const pct=items.length<=1?100:Math.round((cur-1)/(items.length-1)*100);
    return `<div class="ax-fb-steps" style="--ax-fb-step-count:${items.length};--ax-fb-status-color:${colorForPercent(pct)}" role="group" aria-label="${escText(summaryLabel)}"><div class="ax-fb-steps-summary"><strong>Step ${cur} of ${items.length}</strong><span>· ${escText(items[cur-1].label)}</span></div>${items.map(s=>{const cls=s.n<cur?'is-done':s.n===cur?'is-current':'is-pending';const stagePct=items.length<=1?100:Math.round((s.n-1)/(items.length-1)*100);return `<div class="ax-fb-step ${cls}" ${s.n===cur?'aria-current="step"':''}><span class="ax-fb-step-dot" style="--ax-fb-step-color:${colorForPercent(stagePct)}">${s.n<cur?'✓':s.n}</span><span class="ax-fb-step-label">${escText(s.label)}</span></div>`;}).join('')}</div>`;
  }
  function showInitialSkeleton(target,type,count){
    if(!target||!window.AXSkeleton)return false;
    const shown=window.AXSkeleton.show(target,type,count);
    if(shown)target.dataset.axInitialLoading='1';
    return shown;
  }
  function clearInitialSkeleton(target){
    if(!target)return;
    if(target.dataset.axInitialLoading==='1'){window.AXSkeleton?.clear(target);delete target.dataset.axInitialLoading;}
  }
  function buttonBusy(button,label,work){
    if(!button)return Promise.resolve().then(work);
    if(button.dataset.axBusy==='1')return Promise.resolve();
    const old=button.innerHTML,wasDisabled=button.disabled;button.dataset.axBusy='1';button.disabled=true;button.classList.add('ax-fb-busy-button');button.innerHTML=spinner(label,'sm');
    return Promise.resolve().then(work).finally(()=>{button.innerHTML=old;button.disabled=wasDisabled;button.classList.remove('ax-fb-busy-button');delete button.dataset.axBusy;});
  }
  window.AXLoadingFeedback={spinner,rollerMarkup,skeletonCard,skeletonGrid,progressMarkup,stepsMarkup,showInitialSkeleton,clearInitialSkeleton,buttonBusy,colorForPercent,statusTone,colorForStatus,setStatusIndicator,statusColors:STATUS_COLORS};
  // 09-app.js initializes the automation wizard before this enhancement layer
  // is loaded. Re-color its existing real step value now that interpolation is available.
  const automationBar=$('automation-step-progress');
  if(automationBar){
    const currentStep=Number(document.querySelector('#automation-stepper .automation-step.on')?.dataset.autoStep||1);
    const widthPct=Number.parseFloat(automationBar.style.width)||Math.round(currentStep/6*100);
    const toneColor=colorForPercent(widthPct);
    automationBar.style.setProperty('--ax-fb-status-color',toneColor);
    const currentButton=document.querySelector('#automation-stepper .automation-step.on');
    if(currentButton)currentButton.style.setProperty('--ax-fb-status-color',toneColor);
  }

  function ensureLibraryProgress(){
    let box=$('ax-library-load-feedback');
    if(!box){const grid=$('vg');if(!grid)return null;box=document.createElement('div');box.id='ax-library-load-feedback';box.hidden=true;box.setAttribute('aria-live','polite');box.setAttribute('aria-atomic','true');grid.parentNode.insertBefore(box,grid);}
    return box;
  }
  // Initial-only skeletons avoid flashing over a list that is already populated.
  const originalLoadChannels=window.loadChannels;
  if(typeof originalLoadChannels==='function'&&!originalLoadChannels.__axFeedbackWrapped){
    const wrapped=async function(){const box=$('channel-cards'),show=showInitialSkeleton(box,'channel',8);try{return await originalLoadChannels.apply(this,arguments);}finally{clearInitialSkeleton(box);}};
    wrapped.__axFeedbackWrapped=true;window.loadChannels=wrapped;
  }
  const originalLoadPerformers=window.loadPerformers;
  if(typeof originalLoadPerformers==='function'&&!originalLoadPerformers.__axFeedbackWrapped){
    const wrapped=async function(){const box=$('perf-list'),show=showInitialSkeleton(box,'performer',12);try{return await originalLoadPerformers.apply(this,arguments);}finally{if(show&&box?.dataset.axInitialLoading==='1'){/* render/error branch normally replaced it */clearInitialSkeleton(box);}}};
    wrapped.__axFeedbackWrapped=true;window.loadPerformers=wrapped;
  }
  const originalLoadSites=window.loadSites;
  if(typeof originalLoadSites==='function'&&!originalLoadSites.__axFeedbackWrapped){
    const wrapped=async function(){const box=$('site-list'),show=showInitialSkeleton(box,'studio',10);try{return await originalLoadSites.apply(this,arguments);}finally{if(show&&box?.dataset.axInitialLoading==='1')clearInitialSkeleton(box);}};
    wrapped.__axFeedbackWrapped=true;window.loadSites=wrapped;
  }
  function setLibraryProgress(current,total,title,detail){
    const box=ensureLibraryProgress();if(!box)return;
    box.hidden=false;box.innerHTML=progressMarkup(current,total,title,detail);
  }
  function hideLibraryProgress(){const box=$('ax-library-load-feedback');if(box){box.hidden=true;box.innerHTML='';}}
  window.AXLoadingFeedback.setLibraryProgress=setLibraryProgress;
  window.AXLoadingFeedback.hideLibraryProgress=hideLibraryProgress;

  // The Telegram phone flow has three actual stages; Cloud Password is only
  // shown when Telegram requires 2FA. A successful code skips that optional step.
  function phoneStage(){
    const panel=$('ax-tg-phone-panel');if(!panel)return 1;
    const code=$('ph-code'),pw=$('ph-2fa'),passwordPanel=$('ax-tg-password-panel');
    if((pw&&pw.style.display!=='none')||(passwordPanel&&!passwordPanel.hidden))return 3;
    if(code&&code.style.display!=='none')return 2;
    return 1;
  }
  function renderPhoneSteps(){
    const panel=$('ax-tg-phone-panel'),tabs=$('ax-tg-method-tabs');if(!panel)return;
    let root=$('ax-tg-phone-steps');if(!root){root=document.createElement('div');root.id='ax-tg-phone-steps';if(tabs)tabs.insertAdjacentElement('afterend',root);else panel.insertBefore(root,panel.firstChild);}
    root.innerHTML=stepsMarkup(['Phone number','Verification code','Cloud password (if required)'],phoneStage(),'Telegram phone sign-in');
    // Keep step 3 visible in the password panel for phone sign-in, while QR
    // sign-in continues using its separate three-item instruction list.
    root.hidden=!($('ax-tg-tab-phone')?.classList.contains('on'));
  }
  function wrapAuth(name,buttonSelector,label,nextStage){
    const original=window[name];if(typeof original!=='function'||original.__axFeedbackWrapped)return;
    const wrapped=async function(){const button=document.querySelector(buttonSelector);renderPhoneSteps();return buttonBusy(button,label,async()=>{try{return await original.apply(this,arguments);}finally{renderPhoneSteps();if(nextStage)nextStage();}});};
    wrapped.__axFeedbackWrapped=true;window[name]=wrapped;
  }
  wrapAuth('sendCode','#ax-tg-phone-panel .ax-tg-action-row button','Sending code…',renderPhoneSteps);
  wrapAuth('verifyCode','#ax-tg-phone-panel #ph-code button','Verifying code…',renderPhoneSteps);
  wrapAuth('verify2fa','#ax-tg-password-panel #ph-2fa button','Checking password…',renderPhoneSteps);
  const phonePanel=$('ax-tg-phone-panel');if(phonePanel){const observer=new MutationObserver(renderPhoneSteps);observer.observe(phonePanel,{attributes:true,attributeFilter:['hidden']});const code=$('ph-code'),pw=$('ph-2fa'),password=$('ax-tg-password-panel');[code,pw,password].filter(Boolean).forEach(el=>observer.observe(el,{attributes:true,attributeFilter:['style','hidden']}));renderPhoneSteps();}
})();
