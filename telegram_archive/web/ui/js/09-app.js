/* ============================================================================
 * FILE: js/09-app.js
 * PURPOSE: Main application runtime: API use, navigation, library, performers, studios, jobs, settings and player. Inline HTML handlers and later scripts rely on its top-level names being global.
 * AREA: General
 * LOAD: Classic script. Order is set by index.html, the HTML partials and
 *       _LAYERS in web/html.py. Later scripts can wrap earlier globals,
 *       so check docs/CODE_MAP.md before moving code between files.
 * SECTIONS (search for "SECTION:"):
 *   - BOOTSTRAP
 *   - VIEWS + NAVIGATION (browser Back/Forward stay inside the app)
 *   - THUMBNAILS: load a few at a time so video playback is never starved
 *   - LIBRARY — CHANNELS + GRID
 *   - FILTERS
 *   - GRID
 *   - DETAIL
 *   - PLAYBACK
 *   - LOCAL PERFORMER MAPPING
 *   - PERFORMERS PAGE
 *   - SITES PAGE — mirror the performer page, backed by the existing studios/video_studios tables
 *   - ADMIN — MODULES
 *   - Unified Telegram Transfer + Metadata & Assets managers
 *   - Playback Snapshot Builder
 *   - Telegram Media Manager console / structured workflow UI
 *   - ADMIN — ACTIVE JOBS / EVENTS
 *   - ADMIN — SETTINGS
 *   - UTILS
 *   - v18.8 — THUMBNAILS (fetch-based: cached, retried, never lost)
 *   - SMALL HELPERS
 *   - GRID — cards are updated in place (no full redraw)
 *   - Background details for the visible page
 *   - VIDEO QUICK EDIT — title + performer + studio
 *   - WATCH VIEW + PLAYER
 *   - Multi-part playable split helpers
 *   - detail panel
 *   - PERFORMER PICKER (search as you type, link to an existing one)
 *   - LOCAL SITE MAPPING
 *   - SEND TO TELEGRAM
 *   - COMBINED PERFORMER + STUDIO MAPPING
 *   - v18.10 — playback button icons, performer random
 *   - V5.5 — Library discovery, watch history, markers, providers
 *   - V5.4 — Operations / Settings control plane
 *   - MANUAL METADATA MAPPING / CORRECTION
 *   - V29.4.133 REVIEW OF EARLIER FILENAME AUTO-MATCHES
 * ============================================================================ */

// ═══════════════════════════════════════════════
// SECTION: BOOTSTRAP
// ═══════════════════════════════════════════════
// This classic script owns the app-wide runtime. Inline HTML handlers and
// later scripts rely on its top-level functions remaining global.
const $=id=>document.getElementById(id);
const _axWriteTextIfChanged=(node,value)=>{const next=String(value??'');if(node&&node.textContent!==next)node.textContent=next;};
const _axToggleClassIfChanged=(node,name,on)=>{const wanted=!!on;if(node&&node.classList.contains(name)!==wanted)node.classList.toggle(name,wanted);};
const _axSetAttrIfChanged=(node,name,value)=>{const next=String(value);if(node&&node.getAttribute(name)!==next)node.setAttribute(name,next);};
const {initializeSessionGate}=window.ArchiveXAuth;
const esc=s=>String(s??'').replace(/&/g,'&amp;').replace(/</g,'&lt;').replace(/>/g,'&gt;').replace(/"/g,'&quot;').replace(/'/g,'&#39;');
function channelDisplayName(channelId,suppliedTitle=''){
  const raw=String(channelId??'').trim(),legacy=raw.match(/^channel\s+(-?\d+)$/i),id=legacy?legacy[1]:raw;
  const isDisplayName=value=>{
    const title=String(value??'').trim();
    return !!title&&!/^unknown channel$/i.test(title)&&!/^channel\s+-?\d+$/i.test(title)&&!/^-?\d+$/.test(title);
  };
  if(isDisplayName(suppliedTitle))return String(suppliedTitle).trim();
  const channels=[...(Array.isArray(_sourceChans)?_sourceChans:[]),...(Array.isArray(_chans)?_chans:[])];
  const channel=channels.find(item=>String(item?.telegram_id??'').trim()===id);
  if(isDisplayName(channel?.title))return String(channel.title).trim();
  if(raw&&!legacy&&!/^-?\d+$/.test(raw))return raw;
  return 'Unknown channel';
}
function channelDisplayNames(value){
  const raw=String(value??'').trim();
  if(!raw)return 'Unknown channel';
  const parts=raw.split(',').map(part=>part.trim()).filter(Boolean);
  if(parts.length>1&&parts.every(part=>/^-?\d+$/.test(part)||/^channel\s+-?\d+$/i.test(part))){
    return parts.map(part=>channelDisplayName(part)).join(', ');
  }
  return channelDisplayName(raw);
}
const axIcon=(name,cls='')=>`<svg class="ax-icon ${cls}" aria-hidden="true"><use href="#ax-${name}"></use></svg>`;
const axBrandIconForUrl=(raw)=>{
  try{const h=new URL(String(raw||''),location.href).hostname.toLowerCase().replace(/^www\./,'');
    if(h==='t.me'||h.endsWith('.telegram.org')||h==='telegram.org')return 'brand-telegram';
    if(h==='instagram.com'||h.endsWith('.instagram.com'))return 'brand-instagram';
    if(h==='x.com'||h.endsWith('.x.com')||h==='twitter.com'||h.endsWith('.twitter.com'))return 'brand-x';
    if(h==='youtube.com'||h.endsWith('.youtube.com')||h==='youtu.be')return 'brand-youtube';
    if(h==='tiktok.com'||h.endsWith('.tiktok.com'))return 'brand-tiktok';
    if(h==='reddit.com'||h.endsWith('.reddit.com'))return 'brand-reddit';
    if(h==='facebook.com'||h.endsWith('.facebook.com'))return 'brand-facebook';
    if(h==='patreon.com'||h.endsWith('.patreon.com'))return 'brand-patreon';
    if(h==='onlyfans.com'||h.endsWith('.onlyfans.com'))return 'brand-onlyfans';
    if(h==='linktr.ee'||h.endsWith('.linktr.ee'))return 'brand-linktree';
    return 'brand-website';
  }catch(_){return 'brand-website';}
};
// A value placed inside an inline onclick="fn(...)" handler: JS-quote, then HTML-escape.
const jsa=s=>esc(JSON.stringify(String(s??'')));
const safeHttpUrl=u=>/^https?:\/\//i.test(String(u||''))?String(u):'';
function _listSig(arr,key='id'){if(!arr)return '';let h=arr.length;for(let i=0;i<arr.length;i++)h=h*31+(Number(arr[i]?.[key])||0)|0;return h+':'+arr.length;}
const sl=ms=>new Promise(r=>setTimeout(r,ms));
const fmt=b=>{const u=['B','KB','MB','GB'];let v=b||0;for(let i=0;i<3;i++){if(Math.abs(v)<1024)return v.toFixed(1)+u[i];v/=1024}return v.toFixed(1)+'GB'};
const fdur=s=>{if(!s)return'';const h=Math.floor(s/3600),m=Math.floor((s%3600)/60),sec=Math.floor(s%60);return h?`${h}:${String(m).padStart(2,'0')}:${String(sec).padStart(2,'0')}`:`${m}:${String(sec).padStart(2,'0')}`};

// Explicit import from the client-diagnostics module. The aliases keep this
// legacy classic script readable while the module owns diagnostics behavior.
const {
  state: _axDiag,
  playbackIncidentCooldownMs: PLAYBACK_INCIDENT_COOLDOWN_MS,
  playbackIncidentDelayMs: PLAYBACK_INCIDENT_DELAY_MS,
  record: axDiagRecord,
  sessionRecovered: axDiagSessionRecovered,
  media: _axDiagMedia
} = window.ArchiveXDiagnostics;
const {
  patchLiveMarkup: _axPatchLiveMarkup,
  reconcileJobCards: _opsReconcileJobCards
} = window.ArchiveXLiveDom;
const { completeSetup, doLogin, doLogout, showLP, hideLP } = window.ArchiveXAuth;

async function loadBootstrap(){
  const b=await api('/api/bootstrap',{cache:'no-store'});
  if(!b||!b._ok)return b;
  _apiSeed('/api/channels',b.channels||[],12000);
  _apiSeed('/api/settings/telegram',b.settings?.telegram||{},8000);
  _apiSeed('/api/settings/metadata',b.settings?.metadata||{},15000);
  _apiSeed('/api/settings/send',b.settings?.send||{},15000);
  _apiSeed('/api/settings/experience',b.settings?.experience||{},15000);
  window.__AX_PARTIAL_CACHE_PROMPT=b.settings?.experience?.partial_cache_prompt===true;
  window.AXNoBufPlayback?.setPartialCachePrompt?.(window.__AX_PARTIAL_CACHE_PROMPT);
  // Apply playback preferences at bootstrap, before the user has to open Settings.
  // Resume-dialog decisions must not start from the enabled default when the saved value is off.
  if(b.settings?.experience && typeof b.settings.experience.resume_prompt === 'boolean'){
    _resumePromptEnabled=b.settings.experience.resume_prompt;
  }
  _apiSeed('/api/settings/weblogin',b.settings?.weblogin||{},15000);
  _apiSeed('/api/operations/automation/config',b.automation||{},10000);
  _apiSeed('/api/media-manager/preflight',b.preflight||{},30000);
  _opsState=b.status||null;
  _noteDataVersions(b.status&&b.status.data_versions);
  return b;
}

// ═══════════════════════════════════════════════
// V29.4.131 INSTANT UPDATES
// The server status (websocket, or the 12 s poll) carries change counters
// for videos, performers/studios, channels and settings. When one moves, the
// page refreshes what is on screen -- keeping the page number, scroll
// position, open tab and any unsaved form edits -- and marks hidden views to
// refresh when they are next shown. No app restart or manual reload needed.
// ═══════════════════════════════════════════════
let _dataVersions=null,_dataChanged=new Set(),_dataRefreshTimer=0,_dataRefreshFirstAt=0;
let _lastUserInput=0,_libStale=false,_settingsDirty=false,_dataRefreshRunning=false;
// While a scan keeps changing data, views refresh at most every 20 s.
const DATA_REFRESH_QUIET_MS=1500, DATA_REFRESH_MAX_WAIT_MS=20000, DATA_REFRESH_IDLE_MS=2500;
['pointerdown','keydown','wheel','touchstart'].forEach(ev=>window.addEventListener(ev,()=>{_lastUserInput=performance.now();},{passive:true,capture:true}));
document.addEventListener('input',e=>{if(e.target&&e.target.closest&&e.target.closest('#vs'))_settingsDirty=true;},true);
function _noteDataVersions(v){
  if(!v||typeof v!=='object')return;
  if(!_dataVersions){_dataVersions={...v};return;}
  let moved=false;
  for(const k of Object.keys(v)){if(v[k]!==_dataVersions[k]){_dataChanged.add(k);moved=true;}}
  _dataVersions={...v};
  if(!moved)return;     // other status fields (jobs, events) change all the time
  // Wait for a short quiet period so a burst of changes refreshes once, but
  // never longer than DATA_REFRESH_MAX_WAIT_MS while changes keep coming.
  const now=performance.now();
  if(!_dataRefreshFirstAt)_dataRefreshFirstAt=now;
  clearTimeout(_dataRefreshTimer);
  const wait=Math.max(0,Math.min(DATA_REFRESH_QUIET_MS,_dataRefreshFirstAt+DATA_REFRESH_MAX_WAIT_MS-now));
  _dataRefreshTimer=setTimeout(_applyDataChanges,wait);
}
function _dialogOpen(){
  return !!document.querySelector('#amr.open,#suggest-preview.open,#sk.open,#pk.open,#vmr.open,#video-quick-edit.open,#sm.open,#psmap.open,#mm.open,#entity-edit.open,dialog[open]');
}
function _dataRefreshBlocked(){
  // Never redraw under the user's hands: recent input, an open dialog or a
  // multi-video selection in progress postpones the refresh.
  return performance.now()-_lastUserInput<DATA_REFRESH_IDLE_MS || _dialogOpen() || (typeof _mapMode!=='undefined'&&_mapMode);
}
async function _applyDataChanges(){
  _dataRefreshTimer=0;_dataRefreshFirstAt=0;
  if(!_dataChanged.size)return;
  if(_dataRefreshRunning||_dataRefreshBlocked()){_dataRefreshTimer=setTimeout(_applyDataChanges,DATA_REFRESH_IDLE_MS);return;}
  const scopes=new Set(_dataChanged);_dataChanged.clear();
  _dataRefreshRunning=true;
  try{
    _apiInvalidate();
    const videos=scopes.has('videos'),entities=scopes.has('entities'),channels=scopes.has('channels'),settings=scopes.has('settings');
    if(videos)await loadSmartTagTaxonomy(true,{rerender:false}).catch(()=>{});
    if(entities){_entityLookupCache={performers:null,studios:null};}
    if(videos||entities||channels)_libStale=true;
    if(channels)await loadChannels().catch(()=>{});
    if(videos||entities)loadFilterLists().catch(()=>{});
    await _refreshVisibleView({videos,entities,channels,settings});
  }finally{_dataRefreshRunning=false;}
}
function _viewOn(id){const e=$(id);return !!e&&(e.classList.contains('on')||e.offsetParent!==null);}
async function _refreshVisibleView(s){
  const keepY=window.scrollY||0;
  const restore=()=>{if(Math.abs((window.scrollY||0)-keepY)>2)window.scrollTo(0,keepY);};
  const playerOpen=!!$('do')?.classList.contains('open');
  if((s.videos||s.entities||s.channels)&&_viewOn('vl')&&!playerOpen){await _softRefreshLibrary();restore();}
  if((s.videos||s.entities)&&_viewOn('vp')){
    if($('perf-detail')?.classList.contains('open'))await _softRefreshEntity('performer');
    else if(!playerOpen&&$('perf-list')&&$('perf-list').style.display!=='none'){await _softRefreshPerformerList();}
    restore();
  }
  if((s.videos||s.entities)&&_viewOn('vsite')){
    if($('site-detail')?.classList.contains('open'))await _softRefreshEntity('studio');
    else if(!playerOpen)await _softRefreshSiteList();
    restore();
  }
  if(s.settings){
    if(!_viewOn('vs'))loadExperienceSettings().catch(()=>{});   // theme, autoplay, seek preview apply now
    else if(!_settingsDirty&&!document.activeElement?.closest?.('#vs'))_reloadSettingsForms();
  }
}
// List refreshes never bump the navigation counters, so a performer/studio
// the user just clicked keeps opening; if a navigation starts meanwhile, the
// refresh steps aside.
async function _softRefreshPerformerList(){
  const seq=_perfSeq,nav=_perfNavSeq,q=_perfQuery||'';
  const url='/api/performers'+(q?`?q=${encodeURIComponent(q)}&limit=5000`:'?limit=5000');
  const list=await api(url,{cache:'no-store'});
  if(seq!==_perfSeq||nav!==_perfNavSeq||!Array.isArray(list)||$('perf-detail')?.classList.contains('open'))return;
  if(_listSig(list)===_listSig(_perfAll))return;
  _perfAll=list;
  const pages=Math.max(1,Math.ceil(getFilteredPerformers().length/_PERF_PER_PAGE));
  _perfPage=Math.max(0,Math.min(_perfPage,pages-1));
  _perfQuietNext=true;
  renderPerformersPage();
}
let _siteListSig='';
async function _softRefreshSiteList(){
  const seq=_siteSeq,nav=_siteNavSeq,q=($('sitesearch')?.value||'').trim();
  const list=await api('/api/studios'+(q?`?q=${encodeURIComponent(q)}`:''),{cache:'no-store'});
  if(seq!==_siteSeq||nav!==_siteNavSeq||!Array.isArray(list)||_siteSeqLoading||$('site-detail')?.classList.contains('open'))return;
  let rows=list;
  if(q){const n=q.toLowerCase();rows=rows.filter(x=>String(x.name||'').toLowerCase().includes(n));}
  // Read-only refresh: only repaint changed directory data; do not queue provider jobs.
  const sig=_listSig(rows);if(sig===_siteListSig)return;_siteListSig=sig;
  const wrap=$('site-list');if(!wrap||!rows.length)return;
  _siteRows=rows;_siteQuietNext=true;renderSitesPage();
}
// Studio artwork/metadata refresh is deliberately not queued from page renders.
// Run provider metadata refreshes explicitly from Metadata & Assets Manager.
function renderSitesPage(){
  const wrap=$('site-list');if(!wrap)return;
  wrap.classList.toggle('rx-static',_siteQuietNext);_siteQuietNext=false;
  const pages=Math.max(1,Math.ceil(_siteRows.length/_SITE_PER_PAGE));
  _sitePage=Math.max(0,Math.min(_sitePage,pages-1));
  const start=_sitePage*_SITE_PER_PAGE;
  const pageRows=_siteRows.slice(start,start+_SITE_PER_PAGE);
  wrap.dataset.totalCount=String(_siteRows.length);
  wrap.dataset.visibleCount=String(pageRows.length);
  _axPatchLiveMarkup(wrap,pageRows.map(siteCard).join(''));
  hydrateThumbs(wrap);
  const pg=$('site-pg');if(!pg)return;
  if(!_siteRows.length){pg.innerHTML='';pg.style.display='none';return;}
  pg.style.display='block';
  pg.innerHTML=renderAxPaginationBar({page:_sitePage,pages,changePage:'gSitePage',inputId:'site-page-input',goInputFn:'gotoSitePageInput',pageSizeInputId:'site-page-size',pageSize:_SITE_PER_PAGE,pageSizeFn:'setStudioRowsPerPage',ariaLabel:'Studio directory pagination'});
}
function gSitePage(p){
  const pages=Math.max(1,Math.ceil(_siteRows.length/_SITE_PER_PAGE));
  const previousPage=_sitePage;
  _sitePage=Math.max(0,Math.min(Number(p)||0,pages-1));
  renderSitesPage();
  if(_sitePage!==previousPage)window.AXMotion?.replayGrid($('site-list'));
  window.scrollTo(0,0);
}
function gotoSitePageInput(){const input=$('site-page-input');const raw=String(input?.value||'').trim();if(!raw)return;gSitePage(Math.max(1,parseInt(raw,10)||1)-1);}
function setStudioRowsPerPage(value){
  const size=_axNormalizePageSize(value);if(!size||size===_SITE_PER_PAGE)return;
  const firstItem=_sitePage*_SITE_PER_PAGE;
  _SITE_PER_PAGE=size;_sitePage=Math.floor(firstItem/size);_axSavePageSize('studios',size);
  renderSitesPage();
}

async function _softRefreshLibrary(){
  if(!_sel.size){_libStale=false;return;}
  await loadLib(null,true,true);
  _libStale=false;
}
async function _softRefreshEntity(kind){
  const perf=kind==='performer';
  const root=$(perf?'perf-detail':'site-detail');
  const id=perf?_perfCurId:_siteCurId;
  if(!root||!root.classList.contains('open')||!id)return;
  const seq=perf?_perfSeq:_siteSeq,nav=perf?_perfNavSeq:_siteNavSeq,detailNav=_entityDetailLoadSeq;
  const rel=perf?Number(window._perfRelationStudioId)||0:Number(window._siteRelationPerformerId)||0;
  const url=perf?`/api/performers/${Number(id)}/videos${rel?`?studio_id=${rel}`:''}`:`/api/studios/${Number(id)}/videos${rel?`?performer_id=${rel}`:''}`;
  const r=await api(url,{cache:'no-store'});
  const ent=perf?r?.performer:r?.site;
  if(seq!==(perf?_perfSeq:_siteSeq)||nav!==(perf?_perfNavSeq:_siteNavSeq)||detailNav!==_entityDetailLoadSeq||!r||!ent||!root.classList.contains('open')||(perf?_perfCurId:_siteCurId)!==id)return;
  const videos=Array.isArray(r.videos)?r.videos:[];
  const old=perf?_perfVideos:_siteVideos;
  const name=root.querySelector('.entity-name-row h2');
  if(name&&ent.name&&name.textContent!==ent.name)name.textContent=ent.name;
  const backName=root.querySelector('.entity-back-label');
  if(backName&&ent.name&&backName.textContent!==ent.name)backName.textContent=ent.name;
  const photo=root.querySelector('.pd-photo img');
  if(photo&&perf){const src=`/api/performers/${Number(ent.id)}/image?v=${encodeURIComponent(ent.image||'')}`;if(photo.getAttribute('src')!==src)photo.setAttribute('src',src);}
  if(_listSig(old||[])===_listSig(videos))return;
  if(perf)_perfVideos=videos;else _siteVideos=videos;
  const badge=root.querySelector('.pd-meta .badge');if(badge)badge.textContent=`${videos.length.toLocaleString()} videos`;
  const tabCount=root.querySelector('.entity-detail-tab[data-entity-tab="videos"] .tab-count');if(tabCount)tabCount.textContent=videos.length.toLocaleString();
  if(perf)await renderPerfTab('videos');else await renderSiteTab('videos');
}
function _reloadSettingsForms(){
  _settingsDirty=false;
  [loadTgSettings,loadMetaSettings,loadSendSettings,loadExperienceSettings,loadSettingsStatus].forEach(fn=>{try{const p=fn&&fn();if(p&&p.catch)p.catch(()=>{});}catch(e){}});
}

let _opsSocket=null,_opsSocketTimer=0,_opsSocketRetry=0,_opsLiveEnabled=false,_opsLoopsStarted=false;
let _opsSocketStartedAt=0,_opsSocketLastMessageAt=0;
function stopOpsSocket(){
  _opsLiveEnabled=false;
  clearTimeout(_opsSocketTimer);_opsSocketTimer=0;
  const socket=_opsSocket;_opsSocket=null;
  _opsSocketRetry=0;_opsSocketLastMessageAt=0;
  try{socket?.close();}catch(_){}
}
function opsSocketHealthy(){
  return !!(_opsLiveEnabled&&_opsSocket?.readyState===WebSocket.OPEN&&_opsSocketLastMessageAt&&Date.now()-_opsSocketLastMessageAt<30000);
}
function retryOpsSocket(){
  if(!_opsLiveEnabled||navigator.onLine===false)return;
  clearTimeout(_opsSocketTimer);_opsSocketTimer=0;
  const socket=_opsSocket;_opsSocket=null;_opsSocketLastMessageAt=0;
  try{socket?.close();}catch(_){}
  startOpsSocket();
  void refresh();
}
function checkOpsSocketHealth(){
  if(!_opsLiveEnabled||navigator.onLine===false)return;
  if(_opsSocket){
    const age=Date.now()-(_opsSocketLastMessageAt||_opsSocketStartedAt);
    const limit=_opsSocket.readyState===WebSocket.CONNECTING?12000:30000;
    if(age>limit){
      axDiagRecord('websocket.stale',{message:'Operations websocket stopped responding',data:{ready_state:_opsSocket.readyState,silent_ms:age}},'WARN');
      retryOpsSocket();
    }
  }else if(!_opsSocketTimer)startOpsSocket();
}
window.addEventListener('online',()=>retryOpsSocket());
document.addEventListener('visibilitychange',()=>{if(!document.hidden){checkOpsSocketHealth();if(_opsLiveEnabled&&!opsSocketHealthy())void refresh();}});
window.addEventListener('pageshow',ev=>{if(ev.persisted&&_opsLiveEnabled)retryOpsSocket();});
function startOpsSocket(){
  if(!_opsLiveEnabled||navigator.onLine===false)return;
  if(_opsSocket&&( _opsSocket.readyState===WebSocket.OPEN || _opsSocket.readyState===WebSocket.CONNECTING))return;
  const proto=location.protocol==='https:'?'wss':'ws';
  const open=()=>{
    if(_opsSocket&&( _opsSocket.readyState===WebSocket.OPEN || _opsSocket.readyState===WebSocket.CONNECTING))return;
    clearTimeout(_opsSocketTimer);_opsSocketTimer=0;
    try{_opsSocket=new WebSocket(`${proto}://${location.host}/ws`);}catch(e){scheduleOpsSocket();return;}
    const socket=_opsSocket;
    _opsSocketStartedAt=Date.now();_opsSocketLastMessageAt=0;
    socket.onopen=()=>{if(socket!==_opsSocket)return;axDiagRecord('websocket.open',{message:'Operations websocket connected'});void refresh();};
    socket.onmessage=ev=>{
      if(socket!==_opsSocket)return;
      try{if(!JSON.parse(ev.data)?.stats)return;}catch(_){return;}
      _opsSocketRetry=0;_opsSocketLastMessageAt=Date.now();
      applyOpsSocketMessage(ev);
    };
    socket.onclose=ev=>{
      if(socket!==_opsSocket)return;
      _opsSocket=null;_opsSocketLastMessageAt=0;
      axDiagRecord('websocket.close',{message:'Operations websocket closed',data:{code:ev.code,clean:ev.wasClean,retry:_opsSocketRetry}},'WARN');
      if(ev.code===4401){showLP();return;}
      scheduleOpsSocket();
    };
    socket.onerror=()=>{if(socket!==_opsSocket)return;axDiagRecord('websocket.error',{message:'Operations websocket error'},'ERROR');try{socket.close();}catch(e){}};
  };
  open();
}
function applyOpsSocketMessage(ev){
  try{const s=JSON.parse(ev.data);if(s&&s.stats){_opsState=s;_noteDataVersions(s.data_versions);renderJobs(s.jobs||[]);_evts=s.events||[];renderEvts();window.uxRefreshNotifications?.();renderModStatus(s);renderAutomationStatus(s.content_automation||{});const ok=s.telegram_connected??(s.telegram&&s.telegram.connected)??false;const dot=$('tgdot');if(dot){_axToggleClassIfChanged(dot,'ax-status-indicator',true);_axToggleClassIfChanged(dot,'off',!ok);_axSetAttrIfChanged(dot,'data-ax-status-tone',ok?'good':'poor');}const label=$('tglabel');if(label){const rawStatus=ok?'Telegram connected':(s.telegram?.status||'Offline');_axWriteTextIfChanged(label,rawStatus);const indicatorStatus=ok?'CONNECTED':(s.telegram?.status||'OFFLINE');const indicatorTone=window.AXLoadingFeedback?.statusTone?.(indicatorStatus);if(window.AXLoadingFeedback?.setStatusIndicator&&(!label.classList.contains('ax-status-indicator')||label.dataset.axStatusTone!==indicatorTone))window.AXLoadingFeedback.setStatusIndicator(label,indicatorStatus);}}}catch(e){}
}
function scheduleOpsSocket(){
  if(!_opsLiveEnabled||navigator.onLine===false)return;
  if(_opsSocket&&( _opsSocket.readyState===WebSocket.OPEN || _opsSocket.readyState===WebSocket.CONNECTING))return;
  clearTimeout(_opsSocketTimer);
  const delay=Math.min(15000,800*2**Math.min(_opsSocketRetry++,5));
  _opsSocketTimer=setTimeout(()=>{_opsSocketTimer=0;startOpsSocket();},delay);
}


function renderTaskTray(jobs){
  const tray=$('task-tray');const body=$('task-tray-body');const count=$('task-tray-count');if(!tray||!body||!count)return;
  const active=(jobs||[]).filter(j=>['RUNNING','QUEUED','PAUSED'].includes(String(j.status||'').toUpperCase()));
  count.textContent=`${active.length} ${active.length===1?'running':'running'}`;
  const html=active.length?active.slice(0,6).map(j=>{
    const hasTotal=Number(j.total)>0,pct=hasTotal?Math.min(100,Math.round(100*(j.completed||0)/j.total)):0;
    const st=String(j.status||'RUNNING').toUpperCase();
    const statusTone=window.AXLoadingFeedback?.statusTone?.(st)||'neutral';
    const statusColor=hasTotal?(window.AXLoadingFeedback?.colorForPercent?.(pct)||'var(--ax-status-fair)'):'var(--accent)';
    return `<div data-ax-live-key="task-tray:${esc(String(j.id||j.job_id||j.name||j.type||''))}" class="jc"><div class="jc-h"><span data-ax-live-key="task-title">${esc(j.name||j.type||'Task')}</span><span data-ax-live-key="task-status" class="st ax-status-indicator" data-ax-status-tone="${statusTone}">${esc(j.status||'RUNNING')}</span></div><div class="jc-b"><div data-ax-live-key="task-progress" class="pt ${hasTotal?'':'ax-fb-indeterminate'}" role="progressbar" aria-label="${esc(j.name||j.type||'Task')} progress" aria-valuemin="0" aria-valuemax="${hasTotal?Number(j.total):100}" ${hasTotal?`aria-valuenow="${Number(j.completed||0)}" aria-valuetext="${pct}%"`:'aria-valuetext="In progress"'}><div data-ax-live-key="task-fill" class="pb progress-purple ${hasTotal?'ax-fb-status-progress':''}" style="width:${pct}%;${hasTotal?`--ax-fb-status-color:${statusColor}`:''}"></div></div></div><div data-ax-live-key="task-operation" class="jc-o">${esc(j.operation||j.item||'')}</div></div>`;
  }).join(''):'<div data-ax-live-key="task-tray-empty" class="muted" style="padding:8px 0">No running tasks.</div>';
  _axPatchLiveMarkup(body,html);
}
function renderOpsSummary(jobs){
  const list=jobs||[];
  const c={RUNNING:0,QUEUED:0,FAILED:0,COMPLETED:0,COMPLETED_WITH_ERRORS:0};list.forEach(j=>{const k=String(j.status||'').toUpperCase();if(k in c)c[k]++;});
  ['running','queued','failed','completed','completed-with-errors'].forEach(k=>{const el=$('ops-'+k+'-count');if(el){const v=String(c[k==='completed-with-errors'?'COMPLETED_WITH_ERRORS':k.toUpperCase()]||0);if(el.textContent!==v)el.textContent=v;}});
}
let _automationStep=1;
function _automationValue(id,fallback='—'){const e=$(id);return e?String(e.value||fallback):fallback;}
function _automationReview(){
  const rows=[
    ['RSS feed',_automationValue('m-auto-rss')],['Destination',_automationValue('m-auto-dst')],['Download folder',_automationValue('m-auto-path')],
    ['Download adapter',_automationValue('m-auto-backend')],['Delivery mode','Serial'],['Poll interval',_automationValue('m-auto-poll')+' sec'],
    ['Resolution',(($('m-auto-only1080')?.value||'1080p')==='1080p')?'1080p only':'Any'],['Must contain',_automationValue('m-auto-must')],['Min size',_automationValue('m-auto-minsize')+' MiB'],['Max size',_automationValue('m-auto-maxsize')+' MiB']
  ];
  const grid=$('automation-review-grid');if(grid)grid.innerHTML=rows.map(([a,b])=>`<div class="automation-review-row"><b>${esc(a)}</b><span>${esc(b)}</span></div>`).join('');
  const warnings=[]; const min=Number($('m-auto-minsize')?.value||0), max=Number($('m-auto-maxsize')?.value||0);
  if(min&&max&&min>max)warnings.push('Minimum size is greater than maximum size.');
  if(!$('m-auto-rss')?.value?.trim())warnings.push('RSS feed URL is required.');
  if(!$('m-auto-dst')?.value)warnings.push('Destination channel is not selected.');
  $('automation-review-warning').textContent=warnings.length?warnings.join(' '):'Configuration looks valid. Confirm the settings, then continue to Run.';
  $('automation-review-warning').style.color=warnings.length?'var(--warning)':'var(--text2)';
  return warnings.length===0;
}
function _validateAutomationStep(step){
  if(step===1){
    const rss=$('m-auto-rss')?.value?.trim()||'';
    if(rss && !/^https?:\/\//i.test(rss)){toast('Enter a valid RSS/Feed URL before continuing.');return false;}
    if(!rss){toast('RSS feed URL is required.');return false;}
    if(!$('m-auto-dst')?.value){toast('Select a destination channel before continuing.');return false;}
  }
  if(step===2){
    const poll=Number($('m-auto-poll')?.value||0), conc=1;
    if(poll<30||poll>3600){toast('Poll interval must be between 30 and 3600 seconds.');return false;}
    if(conc<1||conc>10){toast('Concurrency must be between 1 and 10.');return false;}
  }
  if(step===3){
    const min=Number($('m-auto-minsize')?.value||0), max=Number($('m-auto-maxsize')?.value||0);
    if(min&&max&&min>max){toast('Minimum size cannot exceed maximum size.');return false;}
  }
  return true;
}
function setAutomationStep(step){
  step=Math.max(1,Math.min(6,Number(step)||1));
  if(step>_automationStep&&!_validateAutomationStep(_automationStep))return;
  _automationStep=step;
  document.querySelectorAll('.automation-step').forEach(b=>{const n=Number(b.dataset.autoStep);b.classList.toggle('on',n===step);b.classList.toggle('done',n<step);b.setAttribute('aria-selected',n===step?'true':'false');});
  document.querySelectorAll('.automation-step-panel[data-wizard-step]').forEach(p=>p.classList.toggle('is-hidden',Number(p.dataset.wizardStep)!==step));
  const pct=Math.round(step/6*100); const bar=$('automation-step-progress');
  const stepColor=window.AXLoadingFeedback?.colorForPercent?.(pct)||'var(--ax-status-fair)';
  if(bar){bar.style.width=pct+'%';bar.style.setProperty('--ax-fb-status-color',stepColor);bar.setAttribute('aria-valuenow',String(pct));bar.setAttribute('aria-valuetext',pct+'%');}
  document.querySelectorAll('.automation-step').forEach(b=>{const n=Number(b.dataset.autoStep);if(n===step)b.style.setProperty('--ax-fb-status-color',stepColor);else if(n<step)b.style.setProperty('--ax-fb-status-color','var(--ax-status-good)');else b.style.removeProperty('--ax-fb-status-color');});
  const prev=$('automation-prev'),next=$('automation-next');if(prev)prev.disabled=step<=1;if(next)next.textContent=step>=6?'Done':'Next';
  const save=$('automation-save-secondary');if(save)save.style.display=step===6?'none':'inline-flex';
  const actions=document.querySelector('.automation-actions');if(actions)actions.style.display=step===6?'flex':'none';
  if(step===5)_automationReview();
  if(step===4){const st=$('m-auto-status');if(st&&$('automation-review-warning'))$('automation-review-warning').textContent=st.textContent||'Queue and activity status will appear here.';}
  requestAnimationFrame(()=>document.getElementById('automation-stepper')?.scrollIntoView({block:'nearest'}));
}
function automationNextStep(){if(_automationStep>=6){return;} if(_automationStep===5&&!_automationReview())return; setAutomationStep(_automationStep+1);}
function initAutomationWizard(){setAutomationStep(1);}


function ensureTopLevelViewStage(){
  const anchor=$('mapbar');
  if(!anchor)return null;
  let stage=$('view-stage');
  if(!stage){
    stage=document.createElement('main');
    stage.id='view-stage';
    anchor.parentNode.insertBefore(stage,anchor.nextSibling);
  }
  ['vphotos','vl','vp','vsite','vtg','vs','va','vai','vtransfers'].forEach(id=>{const e=$(id);if(e&&e.parentNode!==stage)stage.appendChild(e);});
  return stage;
}

function boot(){
  _opsLiveEnabled=true;
  loadSmartTagTaxonomy().catch(()=>{});
  ensureTopLevelViewStage();
  initAutomationWizard();
  // Browser Back/Forward: restore the view after a reload, start at Library otherwise.
  if(history.state&&history.state.view&&history.state.view!=='home')applyNav(history.state);
  else{history.replaceState({view:'library',page:0},'');showView('library');}   // V29.4.131: was left blank
  setGlobalSearchContext('library');
  setLinks();
  loadFolderPreference('u-transfer-folder','transfer_source');
  loadFilterLists();
  // Bootstrap contains these common reads and seeds the short-lived API cache.
  // Wait for it before starting the matching loaders so startup does not query
  // the same settings/channels twice while bootstrap is still in flight.
  loadBootstrap().finally(()=>{
    loadWL();
    loadChannels();
    loadTgSettings();
    loadMetaSettings();
    loadSendSettings();
    // Experience preferences are applied from bootstrap before this preflight.
    loadMMPreflight();
    refresh();
  });
  startOpsSocket();
  if(!_opsLoopsStarted){
    _opsLoopsStarted=true;
    let _opsTick=0;
    setInterval(()=>{
      _opsTick++;
      checkOpsSocketHealth();
      if(_opsTick%2===0&&_opsLiveEnabled&&navigator.onLine!==false)void refreshTransferLanes(false);
      if(_opsTick%3===0&&_opsLiveEnabled&&navigator.onLine!==false&&$('va')?.classList.contains('on'))void refreshOpsResourceDashboard(false,_opsState);
      if(_opsTick%6===0&&_opsLiveEnabled&&navigator.onLine!==false&&!opsSocketHealthy())void refresh();
    },2000);
  }
}

let _statusRefreshPromise=null;
let _transferLaneRefreshPromise=null;
function _axTransferLaneName(type,source,workflow){
  if(String(workflow||'').toUpperCase()==='CONTENT_AUTOMATION')return 'Content Automation · delivery';
  if(String(type||'')==='TELEGRAM_TRANSFER'&&['local','local_files','upload_local'].includes(String(source||'').toLowerCase()))return 'Local → channel upload';
  if(String(type||'')==='TELEGRAM_TRANSFER')return 'Telegram → Telegram transfer';
  return ({UPLOAD_LOCAL:'Local → channel upload',MEDIA_MANAGER:'Telegram Media Manager',METADATA_CHANNEL_PIPELINE:'Metadata & Assets scan',METADATA_ASSETS:'Metadata & Assets',CONTENT_AUTOMATION:'Content Automation'})[String(type||'')]||String(type||'Background work');
}
function _axTransferChip(key,label,value){return `<span data-ax-live-key="transfer-resource:${esc(key)}" class="ax-transfer-resource-chip"><span data-ax-live-key="label" class="ax-transfer-resource-label muted">${esc(label)}</span><strong data-ax-live-key="value" class="ax-transfer-resource-value">${esc(String(value))}</strong></span>`;}
function _axTransferLaneStats(job,percent){
  const metrics=job.metrics||{},parts=[`${percent.toFixed(1)}%`];
  const total=Number(job.total)||0,done=Number(job.completed)||0;
  if(total>0)parts.push(`${done.toLocaleString()} / ${total.toLocaleString()} ${['METADATA_ASSETS','METADATA_CHANNEL_PIPELINE','MEDIA_MANAGER'].includes(String(job.type).toUpperCase())?'videos':'files'}`);
  const bytes=Number(metrics.bytes_total),units=['B','KB','MB','GB','TB'];
  if(bytes>0&&metrics.bytes_done!=null){
    const unit=Math.min(4,Math.max(0,Math.floor(Math.log(bytes)/Math.log(1024)))),scale=1024**unit;
    parts.push(`File ${(Number(metrics.bytes_done)/scale).toFixed(unit?1:0)} / ${(bytes/scale).toFixed(unit?1:0)} ${units[unit]}`);
  }
  if(String(job.status).toUpperCase()==='RUNNING'||metrics.state==='paused'){
    const rate=Number(metrics.rate_bytes_per_second),eta=Number(metrics.eta_seconds);
    const reportedRate=String(job.operation||'').match(/\b\d+(?:\.\d+)?\s*(?:B|KB|MB|GB)\/s\b/i);
    const reportedEta=String(job.operation||'').match(/\bETA\s+([^·]+)/i);
    const telemetryState=String(metrics.state||'');
    if(telemetryState==='paused'){
      parts.push('Transfer paused','Rate unavailable','ETA unavailable');
    }else if(telemetryState==='stale'){
      parts.push('Progress stale','Rate unavailable','ETA unavailable');
    }else if(telemetryState==='warming_up'){
      parts.push('Warming up','Rate unavailable','ETA unavailable');
    }else if(telemetryState==='complete'){
      parts.push('File transfer complete');
    }else{
      if(metrics.rate_bytes_per_second!=null&&Number.isFinite(rate)&&rate>=0){
        const unit=rate>0?Math.min(4,Math.max(0,Math.floor(Math.log(rate)/Math.log(1024)))):0;
        parts.push(`${metrics.direction==='download'?'Download':'Upload'} ${(rate/1024**unit).toFixed(unit?1:0)} ${units[unit]}/s`);
      }else parts.push(reportedRate?reportedRate[0]:'Rate unavailable');
      if(metrics.eta_seconds!=null&&Number.isFinite(eta)&&eta>=0){
        const secs=Math.floor(eta),hours=Math.floor(secs/3600),mins=Math.floor(secs%3600/60);
        parts.push(`File ETA ${hours?hours+':'+String(mins).padStart(2,'0'):mins}:${String(secs%60).padStart(2,'0')}`);
      }else parts.push(reportedEta?`ETA ${reportedEta[1].trim()}`:'ETA unavailable');
    }
  }
  return `<div class="ax2-transfer-lane-stats">${parts.map(text=>`<span>${esc(text)}</span>`).join('')}</div>`;
}
async function refreshTransferLanes(force=false){
  const root=$('ax-transfer-live-board');
  if(!root||(!force&&!$('vtransfers')?.classList.contains('on'))||_transferLaneRefreshPromise)return _transferLaneRefreshPromise;
  _transferLaneRefreshPromise=(async()=>{
    const r=await api('/api/telegram-transfer/lanes',{cache:'no-store'});
    if(!r||!r._ok||r.ok===false)return;
    const gov=r.governor||{},res=r.resources||{},jobs=Array.isArray(r.jobs)?r.jobs:[];
    const activeCount=jobs.filter(j=>['RUNNING','PAUSING','PAUSED','WAITING','CANCEL_REQUESTED'].includes(String(j.status||'').toUpperCase())).length;
    const use=$('ax-transfer-resource-usage'),status=$('ax-transfer-resource-status');
    if(status){const next=`${activeCount} active/queued lane job${activeCount===1?'':'s'} · refreshed ${new Date().toLocaleTimeString()}`;if(status.textContent!==next)status.textContent=next;}
    if(use){
      const fileBits=[];
      if(gov.telegram_file_active_small!=null)fileBits.push(`${gov.telegram_file_active_small}/${gov.telegram_file_limit_small??'—'} small-file slots`);
      if(gov.telegram_file_active_large!=null)fileBits.push(`${gov.telegram_file_active_large}/${gov.telegram_file_limit_large??'—'} large-file slots`);
      _axPatchLiveMarkup(use,[
        _axTransferChip('uploads','Uploads / transfers',`${gov.transfer_active??0}/${gov.transfer_capacity??2}`),
        _axTransferChip('telegram-downloads','Telegram downloads',fileBits.join(' · ')||'shared file-count limits'),
        _axTransferChip('playback','Playback',gov.playback_active?'priority active':'idle'),
        _axTransferChip('background-workers','CPU/disk workers',`${res.background_active??0}/${res.background_capacity??'—'}`),
        _axTransferChip('media-workers','Media workers',`${res.media_workers??'—'}`),
      ].join(''));
    }
    const box=$('ax-transfer-lane-list');if(!box)return;
    const laneMarkup=jobs.length?jobs.map((j,index)=>{
      const p=Math.max(0,Math.min(100,Number(j.progress)||0));
      const op=String(j.operation||'Queued');
      const state=String(j.status||'QUEUED').replaceAll('_',' ').toLowerCase();
      const type=String(j.type||'').toUpperCase();
      const laneName=_axTransferLaneName(j.type,j.source,j.workflow);
      const localSource=['local','local_files','upload_local'].includes(String(j.source||'').toLowerCase());
      const laneKind=type==='CONTENT_AUTOMATION'||String(j.workflow||'').toUpperCase()==='CONTENT_AUTOMATION'?'automation':(['METADATA_ASSETS','METADATA_CHANNEL_PIPELINE','MEDIA_MANAGER'].includes(type)?'metadata':'transfer');
      const shortName=laneKind==='automation'?'Automation':laneKind==='metadata'?'Metadata':(type==='UPLOAD_LOCAL'||(type==='TELEGRAM_TRANSFER'&&localSource)?'Local upload':'Transfer');
      const currentFile=String(j.current_item||'').split(/[\\/]/).pop().replace(/\s·\s\d+(?:\.\d+)?%$/,'');
      const sourceChannelName=channelDisplayNames(j.source_channel),destinationChannelName=channelDisplayNames(j.destination_channel);
      const title=laneKind==='metadata'?['Channel scan',sourceChannelName].filter(Boolean).join(' · '):(currentFile?`${/upload/i.test(op)?'Uploading ':''}${currentFile}`:shortName);
      const subtitle=[laneName,j.destination_channel?`to ${destinationChannelName}`:'',j.source_channel&&laneKind!=='metadata'?`from ${sourceChannelName}`:'',laneKind==='metadata'||!currentFile?op:''].filter(Boolean).join(' · ');
      const detail=laneKind==='metadata'?currentFile:'';
      const jobId=String(j.id||'');
      const laneKey=jobId||`${type}:${String(j.source||'')}:${index}`;
      const jobStatus=String(j.status||'QUEUED').toUpperCase();
      const interrupted=['INTERRUPTED','RECOVERING'].includes(jobStatus);
      const paused=['PAUSED','PAUSING'].includes(jobStatus);
      const cancelPending=jobStatus==='CANCEL_REQUESTED';
      const terminal=['COMPLETED','COMPLETED_WITH_ERRORS','FAILED','CANCELLED','ERROR'].includes(jobStatus);
      const jobArg=jsa(jobId);
      const controls=jobId&&!terminal?`<div class="ax2-transfer-lane-controls" aria-label="Controls for ${esc(laneName)}" onclick="event.stopPropagation()">${cancelPending?'<span class="muted" role="status">Stopping…</span>':interrupted?`<button class="btn xs pri" type="button" onclick="jobRecover(${jobArg})">${axIcon('play')} Recover &amp; Resume</button>`:paused?`<button class="btn xs" type="button" onclick="jobResume(${jobArg})">${axIcon('play')} Resume</button>`:`<button class="btn xs" type="button" onclick="jobPause(${jobArg})">${axIcon('pause')} Pause</button>`}${cancelPending?`<button class="btn xs danger" type="button" disabled>${axIcon('stop')} Stopping</button>`:`<button class="btn xs danger" type="button" onclick="jobCancel(${jobArg})">${axIcon('stop')} ${interrupted?'Stop':'Cancel'}</button>`}<button class="btn xs" type="button" onclick="jobRefresh(${jobArg})">${axIcon('refresh')} Refresh</button></div>`:'';
      return `<article data-ax-live-key="transfer-lane:${esc(laneKey)}" class="ax2-transfer-lane" data-kind="${laneKind}" data-job-id="${esc(jobId)}"><div class="ax2-transfer-lane-top"><span class="ax2-transfer-lane-icon" aria-hidden="true">${axIcon(laneKind==='metadata'?'database':'transfer')}</span><div class="ax2-transfer-lane-copy"><strong class="ax2-transfer-lane-name" title="${esc(title)}">${esc(title)}</strong><span class="ax2-transfer-lane-operation" title="${esc(subtitle)}">${esc(subtitle)}</span></div><span class="ax2-transfer-lane-state">${esc(state)}</span></div>${detail?`<div class="ax2-transfer-lane-detail" title="${esc(detail)}">${esc(detail)}</div>`:''}<div class="ax2-transfer-lane-progress" role="progressbar" aria-label="${esc(laneName)} progress" aria-valuemin="0" aria-valuemax="100" aria-valuenow="${p}"><span style="width:${p}%;"></span></div>${_axTransferLaneStats(j,p)}${controls}</article>`;
    }).join(''):'<div data-ax-live-key="transfer-lanes-empty" class="muted" style="font-size:12px;padding:6px 0">No active lane jobs. Start a transfer, local upload, manual Metadata &amp; Assets scan, or Content Automation to see its independent progress here.</div>';
    _axPatchLiveMarkup(box,laneMarkup);
  })().catch(()=>{}).finally(()=>{_transferLaneRefreshPromise=null;});
  return _transferLaneRefreshPromise;
}
async function refresh(){
  if(_statusRefreshPromise)return _statusRefreshPromise;
  _statusRefreshPromise=(async()=>{
    const s=await api('/api/status',{cache:'no-store'});
    if(!s||!s.stats)return;
  const ok = s.telegram_connected ?? (s.telegram && s.telegram.connected) ?? false;
  const statusText = ok ? 'Telegram connected' : (s.telegram ? s.telegram.status || 'Offline' : 'Connecting…');
  const dot = $('tgdot');
  if (dot) {
    _axToggleClassIfChanged(dot, 'off', !ok);
    _axSetAttrIfChanged(dot, 'data-ax-status-tone', ok ? 'good' : 'poor');
  }
  _axWriteTextIfChanged($('tglabel'), statusText);
  _opsState=s;
  _noteDataVersions(s.data_versions);
  renderOpsHealth(s);
  if($('va')?.classList.contains('on'))void refreshOpsResourceDashboard(false,s);
  refreshDiagnosticsStrip();
  renderJobs(s.jobs||[]);
  _evts=s.events||[];renderEvts();
  window.uxRefreshNotifications?.();
  renderModStatus(s);
  renderAutomationStatus(s.content_automation||{});
  const tchan=s.telegram&&s.telegram.connected;
  // Site list intentionally does not auto-reload here. The Performer page
  // keeps its entity grid stable during the global status poll; Sites now
  // follows the same behavior so scrolling never triggers a full grid rebuild.
  if(tchan&&$('tab-admin').classList.contains('on')){}
  })().finally(()=>{_statusRefreshPromise=null;});
  return _statusRefreshPromise;
}

// ═══════════════════════════════════════════════
// SECTION: VIEWS + NAVIGATION (browser Back/Forward stay inside the app)
// ═══════════════════════════════════════════════
// v18.44.110 — deterministic viewport reset for top-level views.
try{history.scrollRestoration='manual';}catch(e){}
let _navApplying=false;
let _topLevelViewSeq=0;
let _entityDetailLoadSeq=0;
function _navPush(state){
  if(_navApplying)return;
  const cur=history.state||{};
  if(JSON.stringify(cur)===JSON.stringify(state))return;
  history.pushState(state,'');
}
window.addEventListener('popstate',e=>{applyNav(e.state||{view:'library'});});
function restoreEntityListScroll(view, scroll, anchorId){
  const y=Math.max(0,Number(scroll)||0);
  const id=Number(anchorId)||0;
  const selector=id?`.pcard[data-entity-id=\"${id}\"]`:'';
  const apply=()=>{
    const target=selector?document.querySelector(selector):null;
    if(target){
      const top=Math.max(0,target.getBoundingClientRect().top+window.scrollY-120);
      window.scrollTo(0,top);
    }else{
      window.scrollTo(0,y);
    }
  };
  requestAnimationFrame(apply);
  setTimeout(apply,80);
  setTimeout(apply,250);
  setTimeout(apply,700);
}
async function applyNav(s){
  _navApplying=true;
  try{
    if($('do').classList.contains('open'))closeDBtn(true,'history-navigation');
    if(s.view==='performer'&&s.id){await openPerf(s.id,null,Number(s.relationStudioId)||0,s.tab);}
    else if(s.view==='site'&&s.id){await openSite(s.id,null,Number(s.relationPerformerId)||0,s.tab);}
    else if(s.view==='tag'&&s.name){await openTagByName(s.name);}
    else if(s.view==='home'){showView('library');}
    else if(s.view==='performers'){
      _perfGenderFilter=['all','female','male'].includes(String(s.gender||''))?String(s.gender):'all';
      _perfSort=['default','name_asc','name_desc'].includes(String(s.sort||''))?String(s.sort):'default';
      showView('performers');
      setPerformerGenderFilter(_perfGenderFilter);
      await loadPerformers(Number(s.page)||0, s.q||'');
      if(Number.isFinite(Number(s.scroll)))restoreEntityListScroll('performers',s.scroll,s.anchorId);
    }
    else if(s.view==='sites'){
      showView('sites');
      await loadSites();
      if(Number.isFinite(Number(s.scroll)))restoreEntityListScroll('sites',s.scroll,s.anchorId);
    }
    else if(s.view==='library'){showView('library'); if(Number.isFinite(Number(s.page))) { _page=Math.max(0,Number(s.page)||0); if(_serverLibraryMode&&_sel.size)loadLib(null,false,true);else renderGrid(); }}
    else if(s.view==='photos'){showView('photos');}
    else if(s.view==='transfers'){showView('transfers');if(!_automationSettingsLoaded)loadAutomationSettings().catch(e=>{const msg=$('m-auto-msg');if(msg)msg.textContent='Could not load saved Content Automation settings: '+(e?.message||e);});}
    else if(s.view==='ai')showView('ai');
    else showView(['admin','settings'].includes(s.view)?s.view:'library');
  }finally{_navApplying=false;}
}
function setCurrentEntityNavMapping(kind,id,visible){
  const b=$('entity-nav-map');
  if(!b)return;
  if(visible && id!=null){ b.dataset.kind=kind||''; b.dataset.id=String(id); b.classList.add('entity-nav-visible'); b.title=kind==='performer'?'Map videos for this performer':'Map videos for this studio'; }
  else { delete b.dataset.kind; delete b.dataset.id; b.classList.remove('entity-nav-visible'); }
}
// V29.4.131: leaving a Performer/Studio page ends its multi-video selection
// (refreshing the same page after an edit keeps it).
function _endEntityMapping(){
  if(typeof _mapMode!=='undefined'&&_mapMode&&(_mapContext==='performer'||_mapContext==='site'||_mapContext==='library'))exitMappingMode();
}
function openCurrentEntityMapping(){
  const b=$('entity-nav-map'); const kind=b?.dataset?.kind, id=Number(b?.dataset?.id);
  if(!kind||!Number.isFinite(id))return;
  if(kind==='performer') perfBulkMap();
  else if(kind==='studio') siteBulkMap();
}
function _resetTopLevelViewport(){
  const reset=()=>{
    try{window.scrollTo({top:0,left:0,behavior:'auto'});}catch(e){window.scrollTo(0,0);}
    try{document.documentElement.scrollTop=0;}catch(e){}
    try{document.body.scrollTop=0;}catch(e){}
  };
  reset();
  requestAnimationFrame(reset);
  setTimeout(reset,0);
  setTimeout(reset,80);
  setTimeout(reset,250);
}

function setSideActive(view){
  document.querySelectorAll('.ax-side-item[data-side]').forEach(b=>b.classList.toggle('on',b.dataset.side===view));
}

function showView(v){
  // The player is a standalone page layered over the app shell. Always close it
  // before changing the top-level view; stopping only the media element left
  // the full-page player surface over the destination and made navigation feel stuck.
  if($('do')?.classList.contains('open')) closeDBtn(true,'navigate');
  closeVideoCardActionMenu();
  queuePerformerToolbarPinSync();
  syncPerformerHeaderControls(v);
  _resetTopLevelViewport();
  if(v==='home'){v='library';}
  _topLevelViewSeq++;
  window.axInvalidatePendingAccountProfileEditOpen?.();
  if(!['performers','sites'].includes(v)){
    _entityDetailLoadSeq++;
    _perfCurId=null;
    _siteCurId=null;
  }
  setSideActive(v);
  document.querySelectorAll('.tab').forEach(t=>t.classList.remove('on'));
  document.querySelectorAll('.view').forEach(e=>e.classList.remove('on'));
  setCurrentEntityNavMapping('',null,false);
  if(!((v==='performers'&&_mapContext==='performer')||(v==='sites'&&_mapContext==='site')||(v==='library'&&_mapContext==='library')))_endEntityMapping();
  if(v==='tag'){$('vtg').classList.add('on');setGlobalSearchContext('library');return;}
  if(v==='sites'){$('tab-sites')?.classList.add('on');$('vsite').classList.add('on');setGlobalSearchContext('sites');return;}
  $('tab-'+v)?.classList.add('on');
  // Most top-level views use a single-letter legacy DOM id (library -> vl,
  // admin -> va), but AI is a distinct `vai` view and cannot share `va`.
  const viewIds={photos:'vphotos',library:'vl',performers:'vp',sites:'vsite',tag:'vtg',admin:'va',settings:'vs',ai:'vai',transfers:'vtransfers'};
  const viewEl=$(viewIds[v] || ('v'+v[0]));
  viewEl?.classList.add('on');
  // Resource Controller data is fetched only while Operations is visible and
  // immediately when navigating there (including browser Back/Forward).
  if(v==='admin')requestAnimationFrame(()=>{try{if(_evtsDirty)renderEvts();}catch(e){}void refreshOpsResourceDashboard(true,_opsState);void loadOpsChannelMaintenanceChannels();});
  setGlobalSearchContext(v==='photos'?'photos':v==='performers'?'performers':v==='library'?'library':v==='sites'?'sites':'library');
  const photoLibrary = window.ArchiveXPhotos;
  if(v==='photos' && typeof photoLibrary?.loadChannels==='function'){
    photoLibrary.loadChannels().catch(()=>{});
  }
  if(v==='ai'){
    aiPageLoad().catch(()=>{
      const state=$('ai-page-state'), model=$('ai-page-model'), tool=$('ai-page-tool-status');
      if(state)state.textContent='AI status unavailable';
      if(model)model.textContent='Local AI service unavailable';
      if(tool)tool.textContent='The AI page is open, but the local AI service could not be reached.';
    });
  }
  if(v==='admin'||v==='settings'||v==='performers'||v==='sites'||v==='transfers'||v==='photos'){
    ensureTopLevelViewStage();
    _resetTopLevelViewport();
    requestAnimationFrame(_resetTopLevelViewport);
    setTimeout(_resetTopLevelViewport,80);
    setTimeout(_resetTopLevelViewport,250);
    setTimeout(_resetTopLevelViewport,600);
  }
}
function sv(v){
  if(v==='home')v='library';
  try{_axApplyPageSizes(false);}catch(e){}
  showView(v);
  if(v==='settings'){renderBrowserCompat();loadStorageSettings();loadProviderDashboard();_reloadSettingsForms();loadScanSchedule().catch(e=>{const el=$('ax-scan-window-status');if(el)el.textContent='Could not load settings';});loadCacheUsage().catch(()=>{});}
  if(v==='library'&&_libStale)_softRefreshLibrary().catch(()=>{});
   if(v==='admin'){const b=document.querySelector('#ops-module-filter .btn[data-filter=\"all\"]');if(b)filterOpsModules('all',b);restoreLibraryHealthLastChecked();loadLibraryMaintenanceSummary().catch(()=>{});}
  if(v==='transfers'){refreshTransferLanes(true);if(!_automationSettingsLoaded)loadAutomationSettings().catch(e=>{const msg=$('m-auto-msg');if(msg)msg.textContent='Could not load saved Content Automation settings: '+(e?.message||e);});}
  if(v==='admin')amrRefreshCount().catch(()=>{});   // V29.4.133: auto-match review count
  if(v==='performers'){syncPerformerHeaderControls('performers');_perfPage=0;loadPerformers(0);}
  if(v==='sites')loadSites();
  _navPush(v==='library'?{view:'library',page:_page||0}:v==='performers'?{view:'performers',page:_perfPage||0,q:_perfQuery||''}:{view:v});
}

// ── SECTION: THUMBNAILS: load a few at a time so video playback is never starved ──

// ═══════════════════════════════════════════════
// SECTION: LIBRARY — CHANNELS + GRID
// ═══════════════════════════════════════════════
let _chans=[],_sourceChans=[],_sel=new Set(),_metaSel=new Set(),_uMetadataChannels=new Set(),_allV=[],_filtV=[],_favOnly=false;
let _suggestionPerformers=[],_suggestionSites=[];
let _smartTagTaxonomy=null,_smartTagTaxonomyPromise=null,_smartTagTaxonomyLoadedAt=0,_smartTagTaxonomySeq=0;
// Rows per page are user-controlled and retained between visits.
const _AX_PAGE_SIZE_OPTIONS=[10,25,50,100];
const _AX_PAGE_SIZE_STORAGE_KEY='ax_pagination_rows_per_page_v1';
function _axSavedPageSize(scope){
  try{
    const saved=JSON.parse(localStorage.getItem(_AX_PAGE_SIZE_STORAGE_KEY)||'{}');
    const size=Number(saved?.[scope]);
    return _AX_PAGE_SIZE_OPTIONS.includes(size)?size:10;
  }catch(e){return 10;}
}
function _axNormalizePageSize(value){
  const size=Number(value);
  return _AX_PAGE_SIZE_OPTIONS.includes(size)?size:0;
}
function _axSavePageSize(scope,size){
  try{
    const saved=JSON.parse(localStorage.getItem(_AX_PAGE_SIZE_STORAGE_KEY)||'{}');
    saved[scope]=size;
    localStorage.setItem(_AX_PAGE_SIZE_STORAGE_KEY,JSON.stringify(saved));
  }catch(e){}
}
let _PER=_axSavedPageSize('videos');
let _page=0;let _serverLibraryMode=true,_serverLibTotal=0,_serverFilterTimer=0;
let _pl=[],_plIdx=-1,_aChan=null,_aMsg=null,_externalPid=null,_externalPoll=0;
let _chanV={};

function selectedChannelItems(){return _chans.filter(c=>_sel.has(String(c.telegram_id)));}


async function loadFilterLists(){
  const [studios,perfs,tags]=await Promise.all([api('/api/studios?limit=240&brief=1'),api('/api/performers?limit=240&brief=1'),api('/api/tags?limit=320&brief=1')]);
  const fill=(id,arr)=>{const e=$(id);if(!e||!Array.isArray(arr))return;e.innerHTML=arr.map(x=>`<option value="${esc(x.name)}"></option>`).join('');};
  fill('site-filter-list',studios);fill('performer-list',perfs);fill('tag-list',tags);
  _suggestionPerformers=Array.isArray(perfs)?perfs:[];
  _suggestionSites=Array.isArray(studios)?studios:[];
  fillSuggestionScanPerformers();
  fillSuggestionScanSites();
}

async function loadChannels(){
  const [published,source]=await Promise.all([api('/api/channels'),api('/api/source-channels')]);
  if(!Array.isArray(published)||!Array.isArray(source)){const bad=!Array.isArray(published)?published:source;showLibraryLoadError(new Error('Channel API failed: '+(bad?.detail||bad?.error||'unknown error')),()=>loadChannels());return;}
  const unique=list=>{const seen=new Set();return list.filter(c=>{const k=String(c.telegram_id);if(seen.has(k))return false;seen.add(k);return true;});};
  _chans=unique(published); _sourceChans=unique(source);
  renderChannelCards();
  renderChips();renderMetaChannels();renderMetadataAssetChannels();fillModSels();renderThumbChannels();thumbPoll();
}
async function doRefreshChannels(){
  setSt('Refreshing channel list from Telegram…');
  const r=await api('/api/telegram/refresh',{method:'POST'});
  if(r.count!=null){
    await loadChannels();
    // Channel refresh rebuilds the picker, but the selected library contents
    // are a separate projection. Reload them after the picker is refreshed so
    // a still-selected channel never appears empty just because the list was
    // refreshed during/after indexing.
    if(_sel.size)await loadLib([..._sel],true);
    await loadFilterLists();
    if(!_sel.size)setSt(`Loaded ${r.count} channel${r.count!==1?'s':''} from Telegram.`);
  }else{
    setSt('Channel refresh failed: '+(r.detail||'unknown error'));
  }
}


function channelAvatarFallback(img, fallbackSrc){
  if(!img)return;
  if(img.dataset.axFallbackTried==='1'){
    img.remove();
    img.closest('.channel-profile-avatar')?.classList.add('no-photo');
    return;
  }
  img.dataset.axFallbackTried='1';
  const fallback=String(fallbackSrc||'').trim();
  if(fallback && fallback!==img.src){img.src=fallback;return;}
  img.remove();
  img.closest('.channel-profile-avatar')?.classList.add('no-photo');
}

let _channelShuffleTargetId='',_channelShuffleBusy=false;
function openChannelShufflePlayerChoice(cid){
  const channel=_chans.find(c=>String(c.telegram_id)===String(cid));
  if(!channel){toast('Channel not found. Refresh the channel list and try again.');return;}
  _channelShuffleTargetId=String(cid);
  const box=$('channel-shuffle-player');if(!box){toast('Player chooser is unavailable.');return;}
  const title=$('channel-shuffle-title'),copy=$('channel-shuffle-copy'),status=$('channel-shuffle-status');
  if(title)title.textContent='Shuffle channel videos';
  if(copy)copy.textContent=`Choose a player for a randomized playlist from ${channelDisplayName(channel.telegram_id,channel.title)}. Archive X stays on this page.`;
  if(status)status.textContent='';
  _channelShuffleBusy=false;
  ['channel-shuffle-vlc','channel-shuffle-mpc'].forEach(id=>{const b=$(id);if(b)b.disabled=false;});
  box.classList.add('open');box.setAttribute('aria-hidden','false');
  setTimeout(()=>$('channel-shuffle-vlc')?.focus(),0);
}
function closeChannelShufflePlayerChoice(){
  if(_channelShuffleBusy)return;
  const box=$('channel-shuffle-player');if(box){box.classList.remove('open');box.setAttribute('aria-hidden','true');}
  _channelShuffleTargetId='';
}
function _shuffleInPlace(list){for(let i=list.length-1;i>0;i--){const j=Math.floor(Math.random()*(i+1));[list[i],list[j]]=[list[j],list[i]];}return list;}
async function startChannelShuffle(player){
  if(_channelShuffleBusy)return;
  const cid=String(_channelShuffleTargetId||'');if(!cid)return;
  player=String(player||'').toLowerCase();if(!['vlc','mpc','mpc-hc'].includes(player))return;
  _channelShuffleBusy=true;
  const status=$('channel-shuffle-status'),buttons=['channel-shuffle-vlc','channel-shuffle-mpc'];
  buttons.forEach(id=>{const b=$(id);if(b)b.disabled=true;});
  const setStatus=value=>{if(status)status.textContent=String(value||'');};
  try{
    setStatus('Loading videos for this channel…');
    const per=48,baseParams=new URLSearchParams({page:'0',per:String(per),channels:cid,sort:'date_desc'});
    const first=await api('/api/library?'+baseParams.toString(),{cache:'no-store'});
    if(!first||!Array.isArray(first.items))throw new Error(first?.detail||'Could not load the channel catalogue.');
    const total=Math.max(0,Number(first.total)||first.items.length);
    if(!total||!first.items.length)throw new Error('This channel has no published videos in the local catalogue yet.');
    const records=first.items.slice();
    const pages=Math.ceil(total/per);
    for(let start=1;start<pages;start+=4){
      const batch=[];
      for(let page=start;page<Math.min(pages,start+4);page++){
        const params=new URLSearchParams({page:String(page),per:String(per),channels:cid,sort:'date_desc'});
        batch.push(api('/api/library?'+params.toString(),{cache:'no-store'}));
      }
      const results=await Promise.all(batch);
      for(const result of results){if(!result||!Array.isArray(result.items))throw new Error(result?.detail||'A channel page could not be loaded.');records.push(...result.items);}
      setStatus(`Loading channel videos… ${Math.min(records.length,total).toLocaleString()} / ${total.toLocaleString()}`);
    }
    const seen=new Set(),videos=[];
    for(const raw of records){
      const video={...raw,_cid:String(raw._cid??raw.source_channel??cid),id:Number(raw.id??raw.source_message_id??0)};
      const key=`${video._cid}:${video.id}`;
      if(video._cid!==cid||video.id<=0||seen.has(key))continue;
      seen.add(key);videos.push(video);
    }
    if(!videos.length)throw new Error('No playable videos were found for this channel.');
    _shuffleInPlace(videos);
    const items=videos.map(v=>({channel_id:v._cid,message_id:v.id,name:v.api_title||v.title||v.name||`video ${v.id}`}));
    const label=player==='mpc'||player==='mpc-hc'?'MPC-HC':'VLC';
    setStatus(`Starting ${label} shuffle with ${items.length.toLocaleString()} videos…`);
    let started=false;
    if(_isRemote()){
      if(player==='vlc'&&(_isIOS()||_isAndroid())){
        started=await _openRemoteVLCPlaylist(items,'archive-x-channel-shuffle.m3u');
      }else{
        const response=await fetch('/api/play/device-playlist',{method:'POST',credentials:'include',headers:{'Content-Type':'application/json'},body:JSON.stringify({items})});
        if(!response.ok){let detail='Could not create the playlist.';try{const err=await response.json();detail=err?.detail||detail;}catch(e){}throw new Error(detail);}
        _downloadText(await response.text(),`archive-x-${player==='mpc'||player==='mpc-hc'?'mpc-hc':'vlc'}-channel-shuffle.m3u`,'audio/x-mpegurl');
        toast(`Random playlist downloaded — open it in ${label} on this device.`,6000);started=true;
      }
    }else{
      started=await _startLibraryExternalPlaylist(player,videos);
    }
    _channelShuffleBusy=false;
    if(started)closeChannelShufflePlayerChoice();
    else{setStatus(`${label} could not be started. Check the message and try again.`);buttons.forEach(id=>{const b=$(id);if(b)b.disabled=false;});}
  }catch(error){
    _channelShuffleBusy=false;setStatus(String(error?.message||error||'Could not start channel shuffle.'));
    buttons.forEach(id=>{const b=$(id);if(b)b.disabled=false;});
  }
}
function renderChannelCards(){
  const el=$('channel-cards'); if(!el)return;
  const list=Array.isArray(_chans)?_chans:[];
  const totalVideos=list.reduce((sum,c)=>sum+Number(c.video_count||0),0);
  const allSelected=list.length>0&&list.every(c=>_sel.has(String(c.telegram_id||'')));
  const allCard=`<button type="button" class="ax2-channel-card ax2-channel-all${allSelected?' selected':''}" aria-pressed="${allSelected?'true':'false'}" title="Select all channel cards" onclick="chAll(1)"><span class="ax2-channel-avatar"><svg class="ax-icon" aria-hidden="true"><use href="#ax-grid"></use></svg></span><span class="ax2-channel-name">All</span><span class="ax2-channel-count">${totalVideos.toLocaleString()}</span></button>`;
  const cards=list.map(c=>{
    const cid=String(c.telegram_id||'');
    const title=channelDisplayName(cid,c.title);
    const count=Number(c.video_count||0);
    const img=String(c.image||'').trim();
    const telegramPhotoUrl=`/api/channels/${encodeURIComponent(cid)}/telegram-photo?v=${encodeURIComponent(String(c.updated_at||'0'))}`;
    const isLocalUpload=img.startsWith('manual-upload:');
    const localPhotoUrl=`/api/channels/${encodeURIComponent(cid)}/image?v=${encodeURIComponent(String(c.updated_at||'0'))}`;
    const avatarUrl=isLocalUpload?localPhotoUrl:telegramPhotoUrl;
    const fallbackSrc=isLocalUpload?telegramPhotoUrl:(/^https?:\/\//i.test(img)?img:'');
    const initials=title.split(/\s+/).filter(Boolean).slice(0,2).map(part=>Array.from(part)[0]||'').join('').toUpperCase()||'TG';
    const selected=_sel.has(cid);
    const known=Number(c.known_count??count),prepared=Number(c.prepared_count??count),pending=Number(c.pending_count||0),removed=Number(c.removed_count||0);
    const scanAt=c.last_scan_at?new Date(c.last_scan_at):null;
    const scanLabel=scanAt&&Number.isFinite(scanAt.getTime())?scanAt.toLocaleString():String(c.last_scan_status||'NEVER')==='NEVER'?'Never scanned':'Scan time unavailable';
    const username=String(c.username||'').trim();
    const channelType=username?`@${username.replace(/^@/,'')}`:'TELEGRAM CHANNEL';
    const metadataLabel=known>0?'Synced':'Not synced';
    const details=`Known ${known.toLocaleString()} · Prepared ${prepared.toLocaleString()} · Pending ${pending.toLocaleString()} · Removed ${removed.toLocaleString()} · Last scan: ${scanLabel}`;
    return `<div class="pcard channel-file-card channel-profile-card ax2-channel-card${selected?' selected':''}" data-channel-id="${esc(cid)}" role="button" tabindex="0" aria-pressed="${selected?'true':'false'}" title="${esc(details)}" onclick="quickPickChannel(${jsa(cid)})" onkeydown="if((event.key==='Enter'||event.key===' ')&&event.target===this){event.preventDefault();quickPickChannel(${jsa(cid)})}">
      <div class="channel-profile-avatar ax2-channel-avatar"><span class="channel-profile-initials" aria-hidden="true">${esc(initials)}</span><img src="${esc(avatarUrl)}" alt="${esc(title)} Telegram profile photo" loading="lazy" decoding="async" onerror="channelAvatarFallback(this,${jsa(fallbackSrc)})"></div>
      <div class="channel-profile-title ax2-channel-name" title="${esc(title)}">${esc(title)}</div>
      <div class="ax2-channel-count">${count.toLocaleString()}</div>
      <div class="channel-profile-kind" hidden>${esc(channelType)}</div>
      <div class="channel-profile-stats" hidden><span class="channel-profile-video-count"><svg viewBox="0 0 24 24" aria-hidden="true"><path d="M8 5.5h8a3 3 0 0 1 3 3v7a3 3 0 0 1-3 3H8a3 3 0 0 1-3-3v-7a3 3 0 0 1 3-3Z"/><path d="m10 9 5 3-5 3Z"/></svg><strong>${count.toLocaleString()}</strong> video${count===1?'':'s'}</span><span class="channel-profile-linked${known>0?' is-linked':''}"><i aria-hidden="true"></i>${esc(metadataLabel)}</span></div>
      <div class="channel-profile-actions"><button class="channel-profile-action channel-profile-shuffle" type="button" title="Shuffle this channel's videos in VLC or MPC-HC" aria-label="Shuffle channel videos in an external player" onclick="event.stopPropagation();openChannelShufflePlayerChoice(${jsa(cid)})">${axIcon('shuffle')}</button><button class="channel-profile-action channel-profile-edit" type="button" title="Edit channel name and profile photo; optionally sync to Telegram" aria-label="Edit channel name and profile photo" onclick="event.stopPropagation();editChannelCard(${jsa(cid)})"><svg class="ax-icon" aria-hidden="true"><use href="#ax-edit"></use></svg></button></div>
    </div>`;
  }).join('');
  const favourites=`<button type="button" class="ax2-channel-card ax2-channel-fav${_favOnly?' is-on':''}" id="favbtn" onclick="tgFav()" aria-pressed="${_favOnly?'true':'false'}" title="Show favourite videos"><span class="ax2-channel-avatar"><svg class="ax-icon" aria-hidden="true"><use href="#ax-star"></use></svg></span><span class="ax2-channel-name">Favourites</span></button>`;
  el.innerHTML=allCard+cards+favourites;
}
function quickPickChannel(cid){
  const id=String(cid);
  if(_sel.has(id))_sel.delete(id);else _sel.add(id);
  _page=0;
  renderChips();
  loadLib(null,true,true,false,true);
}
async function rebuildSelectedIndexes(){
  const channels=selectedChannelItems();
  if(!channels.length){toast('Select one or more channels first.');return;}
  const button=$('index-btn');if(button)button.disabled=true;
  const status=$('library-bg-pipeline');
  const states=new Map();
  try{
    for(const channel of channels){
      const cid=String(channel.telegram_id);
      const channelName=channelDisplayName(cid,channel.title);
      if(status)status.textContent=`Starting or resuming index for ${channelName}…`;
      const started=await api(`/api/channels/${encodeURIComponent(cid)}/index`,{method:'POST',body:JSON.stringify({})});
      if(!started?.ok){states.set(cid,{done:true,error:started?.detail||'Could not start index',title:channelName});continue;}
      states.set(cid,{done:false,error:'',title:channelName});
    }
    let timedOut=true;
    for(let round=0;round<1800;round++){
      let active=0;
      for(const [cid,state] of states){
        if(state.done)continue;
        const result=await api(`/api/channels/${encodeURIComponent(cid)}/index-status`,{cache:'no-store'});
        if(!result){state.done=true;state.error='No status response';continue;}
        if(result.done||result.error){state.done=true;state.error=result.error||'';state.stats=result.stats||{};}
        else if(result.running===false){
          // The server may have restarted after its in-memory task registry was
          // lost. Re-POST once; the durable checkpoint makes this a resume.
          if(!state.restartAttempted){state.restartAttempted=true;const resumed=await api(`/api/channels/${encodeURIComponent(cid)}/index`,{method:'POST',body:JSON.stringify({})});if(resumed?.ok)active++;else{state.done=true;state.error=resumed?.detail||'Index task is no longer running';}}
          else{state.done=true;state.error='Index task disappeared before completion';}
        }else active++;
      }
      if(status){const completed=[...states.values()].filter(x=>x.done).length;status.textContent=`Channel indexes: ${completed}/${states.size} complete${active?` · ${active} running`:''}. ${[...states.values()].filter(x=>x.error).map(x=>x.title+': '+x.error).join(' · ')}`;}
      if(!active){timedOut=false;break;}
      await new Promise(resolve=>setTimeout(resolve,1000));
    }
    if(timedOut)for(const state of states.values())if(!state.done){state.done=true;state.error='Index monitoring timed out; the durable checkpoint can be resumed.';}
    const failed=[...states.values()].filter(x=>x.error);
    if(failed.length)toast(`${failed.length} channel index task(s) failed; their checkpoints were retained where available.`);
    else toast('Channel index refresh completed.');
    await loadChannels();
    await loadLib(null,true);
  }catch(e){toast('Could not rebuild channel index: '+String(e?.message||e));}
  finally{if(button)button.disabled=false;}
}
let _channelEditState=null;
function channelEditPreviewSource(cid,channel){
  const id=encodeURIComponent(String(cid));
  const key=encodeURIComponent(String(channel?.updated_at||'0'));
  return String(channel?.image||'').startsWith('manual-upload:')
    ?`/api/channels/${id}/image?v=${key}`
    :`/api/channels/${id}/telegram-photo?v=${key}`;
}
async function openChannelEditModal(cid){
  let c=_chans.find(x=>String(x.telegram_id)===String(cid));
  if(!c){
    try{
      const loaded=await api(`/api/channels/${encodeURIComponent(String(cid))}/edit`,{cache:'no-store'});
      if(!loaded||loaded._ok===false||String(loaded.telegram_id||'')!==String(cid))throw new Error(loaded?.detail||'Channel not found.');
      c=loaded;
    }catch(error){
      toast(String(error?.message||error||'Could not load channel details.'));
      return;
    }
  }
  const box=$('channel-edit-modal');if(!box){toast('Channel editor is unavailable.');return;}
  const channelName=channelDisplayName(cid,c.title);
  _channelEditState={cid:String(cid),originalTitle:String(c.title||''),imageData:'',resetImage:false,saving:false};
  const title=$('channel-edit-title'),input=$('channel-edit-photo-input'),status=$('channel-edit-status'),preview=$('channel-edit-photo-preview'),initials=$('channel-edit-photo-initials');
  if(title)title.value=channelName==='Unknown channel'?'':channelName;
  if(input)input.value='';
  if(status)status.textContent='Change the name, choose a profile photo, or do both. Save locally first, then explicitly choose whether to sync changes to Telegram.';
  if(initials)initials.textContent=(channelName==='Unknown channel'?'TG':channelName).trim().split(/\s+/).filter(Boolean).slice(0,2).map(x=>Array.from(x)[0]||'').join('').toUpperCase()||'TG';
  if(preview){preview.style.display='block';preview.src=channelEditPreviewSource(cid,c);preview.onerror=()=>{preview.style.display='none';};}
  ['channel-edit-save','channel-edit-choose-photo','channel-edit-reset-photo'].forEach(id=>{const b=$(id);if(b)b.disabled=false;});
  box.classList.add('open');box.setAttribute('aria-hidden','false');
  setTimeout(()=>title?.focus(),0);
}
function editChannelCard(cid){return openChannelEditModal(cid);}
function closeChannelEditModal(){
  if(_channelEditState?.saving)return;
  const box=$('channel-edit-modal');if(box){box.classList.remove('open');box.setAttribute('aria-hidden','true');}
  _channelEditState=null;
  const input=$('channel-edit-photo-input');if(input)input.value='';
}
function channelEditChoosePhoto(){if(!_channelEditState)return;$('channel-edit-photo-input')?.click();}
async function channelEditPhotoChanged(input){
  const state=_channelEditState,file=input?.files?.[0];if(!state||!file)return;
  if(file.size>8*1024*1024){if(input)input.value='';toast('Channel profile photo must be 8 MiB or smaller.');return;}
  if(file.type&&!['image/png','image/jpeg','image/webp','image/gif'].includes(file.type)){if(input)input.value='';toast('Choose a PNG, JPEG, WebP, or GIF image.');return;}
  try{
    const data=await channelFileAsDataUrl(file);
    if(_channelEditState!==state)return;
    state.imageData=data;state.resetImage=false;
    const preview=$('channel-edit-photo-preview'),status=$('channel-edit-status');
    if(preview){preview.onerror=null;preview.src=data;preview.style.display='block';}
    if(status)status.textContent=`Selected ${file.name}. It will be saved locally first; you can then choose whether to push the photo to Telegram.`;
  }catch(error){toast(String(error?.message||error||'Could not read the selected image.'));}
}
function channelEditResetPhoto(){
  const state=_channelEditState;if(!state)return;
  state.imageData='';state.resetImage=true;
  const c=_chans.find(x=>String(x.telegram_id)===state.cid),preview=$('channel-edit-photo-preview'),status=$('channel-edit-status');
  if(preview){preview.onerror=()=>{preview.style.display='none';};preview.style.display='block';preview.src=`/api/channels/${encodeURIComponent(state.cid)}/telegram-photo?refresh=${Date.now()}`;}
  if(status)status.textContent='Saving will remove the local photo override and show the current Telegram profile photo.';
  const input=$('channel-edit-photo-input');if(input)input.value='';
}
function channelFileAsDataUrl(file){
  return new Promise((resolve,reject)=>{const reader=new FileReader();reader.onload=()=>resolve(String(reader.result||''));reader.onerror=()=>reject(new Error('Could not read the selected image'));reader.readAsDataURL(file);});
}
async function refreshChannelEditorViews(){
  await loadChannels();
  const photosView=$('vphotos');
  const photoLibrary = window.ArchiveXPhotos;
  if(photosView?.classList.contains('on')&&typeof photoLibrary?.loadChannels==='function'){
    await photoLibrary.loadChannels(true);
  }
}
async function saveChannelCardEdit(){
  const state=_channelEditState;if(!state||state.saving)return;
  const title=String($('channel-edit-title')?.value||'').trim();
  const status=$('channel-edit-status'),save=$('channel-edit-save');
  if(!title){if(status)status.textContent='Channel name is required.';$('channel-edit-title')?.focus();return;}
  const titleChanged=title!==state.originalTitle;
  if(!titleChanged&&!state.imageData&&!state.resetImage){closeChannelEditModal();return;}
  const body={title};if(state.imageData)body.image_data=state.imageData;if(state.resetImage)body.reset_image_to_api=true;
    state.saving=true;if(save)save.disabled=true;
  ['channel-edit-choose-photo','channel-edit-reset-photo','channel-edit-cancel'].forEach(id=>{const b=$(id);if(b)b.disabled=true;});
  if(status)status.textContent='Saving channel changes locally…';
  try{
    const r=await api(`/api/channels/${encodeURIComponent(state.cid)}/edit`,{method:'POST',body:JSON.stringify(body)});
    if(!r||r.ok===false||r._ok===false)throw new Error(r?.detail||'Could not update channel.');
    const cid=state.cid,imageData=state.imageData;
    const shouldAskSync=titleChanged||!!imageData;
    state.saving=false;closeChannelEditModal();await refreshChannelEditorViews();
    if(shouldAskSync){
      const changes=[titleChanged?'channel name':'',imageData?'profile photo':''].filter(Boolean).join(' and ');
      const push=await axConfirm(`Push the changed ${changes} to Telegram now? This modifies the real Telegram channel and requires owner/admin change-info permission.`,{title:'Sync changes to Telegram?',okLabel:'Push to Telegram',confirmLabel:'Push to Telegram',cancelLabel:'Save locally only',danger:true});
      if(push){
        const synced=await api(`/api/channels/${encodeURIComponent(cid)}/sync-to-telegram`,{method:'POST',body:JSON.stringify({sync_title:titleChanged,sync_photo:!!imageData})});
        if(!synced||synced.ok===false||synced._ok===false){toast(`Local changes saved, but Telegram sync failed: ${synced?.detail||synced?.error||'unknown error'}`);return;}
          await refreshChannelEditorViews();toast(`Saved locally and synchronized to Telegram: ${(synced.synced||[]).join(', ')}.`);
      }else toast('Changes saved locally only. Telegram was not changed.');
    }else{
      await refreshChannelEditorViews();toast('Channel photo reset to the current Telegram profile photo locally.');
    }
  }catch(error){
    if(_channelEditState===state){state.saving=false;if(save)save.disabled=false;['channel-edit-choose-photo','channel-edit-reset-photo','channel-edit-cancel'].forEach(id=>{const b=$(id);if(b)b.disabled=false;});if(status)status.textContent=String(error?.message||error||'Could not save channel changes.');}
    else toast(String(error?.message||error||'Could not save channel changes.'));
  }
}

function renderChips(){
  // Legacy function name retained for existing callers; selected channels are now
  // represented directly on the cards, with no separate chips/selection strip.
  const selected=selectedChannelItems();
  const summary=$('library-channel-summary');
  if(summary) summary.textContent=selected.length
    ?`${selected.length} selected · ${selected.reduce((n,c)=>n+Number(c.video_count||0),0).toLocaleString()} videos`
    :`${_chans.length.toLocaleString()} channels`;
  document.querySelectorAll('#channel-cards .pcard[data-channel-id]').forEach(card=>{
    const on=_sel.has(String(card.dataset.channelId||''));
    card.classList.toggle('selected',on);card.setAttribute('aria-pressed',on?'true':'false');
  });
}
function chAll(v){if(v)_chans.forEach(c=>_sel.add(String(c.telegram_id)));else _sel.clear();_page=0;renderChips();loadLib(null,true,true,false,true);}

const _idxPoll={};
let _bgPipeTimer=0;
// Per-channel load generations prevent an older, slower response from
// overwriting a newer library reload with an empty/stale list.
const _libLoadGeneration=new Map();
let _libLoadSequence=0,_libLoadActive=0,_libHasLoaded=false;


async function loadLib(targetIds=null,forceRefresh=false,preservePage=true,quietRefresh=false,animateCards=false){
  _serverLibraryMode=true;
  if(!_sel.size){
    _serverLibTotal=0;_allV=[];_filtV=[];_chanV={};
    renderChips2();renderGrid(quietRefresh);updateRS();_libStale=false;return;
  }
  const seq=++_libLoadSequence;
  const params=new URLSearchParams({page:String(Math.max(0,_page)),per:String(_PER),channels:[..._sel].map(String).join(','),
    q:String($('gs')?.value||'').trim(),studio:String($('f-studio')?.value||'').trim(),performer:String($('f-perf')?.value||'').trim(),
    tag:String($('f-tag')?.value||'').trim(),date_from:String($('f-d1')?.value||''),date_to:String($('f-d2')?.value||''),
    resolution:String($('tb-res')?.value||''),sort:String($('tb-sort')?.value||'date_desc'),favorites:_favOnly?'1':'0'});
  const scanButton=$('scan-btn');
  if(forceRefresh&&scanButton){scanButton.innerHTML=window.AXLoadingFeedback?.spinner('Loading…','sm')||'Loading…';scanButton.disabled=true;}
  // Video-card skeletons (same .vc card as the real grid) appear only if the page takes >140ms and the
  // grid is empty, so fast loads never flash and an already-populated grid is left alone.
  const libSkeleton=quietRefresh?null:window.AXSkeleton?.later(()=>{if(seq===_libLoadSequence)window.AXSkeleton.show($('vg'),'video',Math.min(Number(_PER)||12,12));},140);
  try{
    const r=await api('/api/library?'+params.toString(),{cache:'no-store'});
    libSkeleton?.cancel();
    if(seq!==_libLoadSequence)return;
    window.AXSkeleton?.clear($('vg'));
    if(!r||!Array.isArray(r.items))throw new Error(r?.detail||'Library response was invalid');
    _serverLibTotal=Math.max(0,Number(r.total)||0);
    const fav=new Set(_allV.filter(v=>v._fav).map(v=>vkey(v._cid,v.id)));
    _filtV=r.items.map(raw=>{
      const v={...raw};v._cid=String(v._cid??v.source_channel??'');v.id=Number(v.id??v.source_message_id??0);
      v.date=String(v.date||v.upload_date||v.created_at||'');v.name=String(v.name||v.title||v.filename||`video ${v.id}`);
      v.telegram_filename=String(v.telegram_filename||v.filename||'');v.thumbnail_id=Number(v.thumbnail_id||v.source_message_id||v.id);
      v.performers=Array.isArray(v.performers)?v.performers:[];v.studios=Array.isArray(v.studios)?v.studios:[];v.tags=Array.isArray(v.tags)?v.tags:[];
      v._fav=Boolean(v._fav??v.favorite);if(fav.has(vkey(v._cid,v.id)))v._fav=true;return v;
    });
    _allV=_filtV.slice();_chanV={};
    for(const cid of _sel)_chanV[String(cid)]=_filtV.filter(v=>String(v._cid)===String(cid));
    _page=Math.max(0,Math.min(_page,Math.max(0,Math.ceil(_serverLibTotal/_PER)-1)));
    renderChips2();renderGrid(quietRefresh,animateCards);updateRS();_libStale=false;_libHasLoaded=true;
    if(_isRemote()&&(_isIOS()||_isAndroid()))_primeRandomVLCPool(_filtV).catch(()=>{});
  }catch(error){libSkeleton?.cancel();if(seq===_libLoadSequence){window.AXSkeleton?.clear($('vg'));showLibraryLoadError(error);}}
  finally{libSkeleton?.cancel();if(seq===_libLoadSequence&&forceRefresh&&scanButton){scanButton.innerHTML='Reload selected';scanButton.disabled=false;}}
}

function showLibraryLoadError(error,retryLoad=()=>loadLib(null,true)){
  const status=$('rs');if(!status)return;
  status.replaceChildren();
  const message=document.createElement('span');
  const hasPreviousData=_libHasLoaded||_filtV.length>0||_chans.length>0;
  message.textContent=`Could not load Library page: ${String(error?.message||error)}${hasPreviousData?' Existing results are still shown.':''}`;
  const retry=document.createElement('button');
  retry.type='button';retry.className='btn xs';retry.textContent='Retry';
  retry.setAttribute('aria-label','Retry loading the Library page');
  retry.addEventListener('click',()=>retryLoad());
  status.append(message,document.createTextNode(' '),retry);
}

function rebuildV(opts){
  if(_serverLibraryMode){applyF(opts);return;}
  const fav=new Set(_allV.filter(v=>v._fav).map(v=>vkey(v._cid,v.id)));
  _allV=[];
  for(const cid of _sel)(_chanV[cid]||[]).forEach(v=>_allV.push({...v,_cid:cid,...(fav.has(vkey(cid,v.id))?{_fav:true}:{})}));
  _allV.sort((a,b)=>(b.date||'').localeCompare(a.date||''));
  applyF(opts);
}

// ── SECTION: FILTERS ──
function tgFP(){$('fp').classList.toggle('open')}
function tgFav(){_favOnly=!_favOnly;$('favbtn').style.borderColor=_favOnly?'var(--amber)':'';applyF();}
function qDate(days){const t=new Date(),f=new Date();f.setDate(f.getDate()-days);$('f-d1').value=f.toISOString().slice(0,10);$('f-d2').value=t.toISOString().slice(0,10);applyF();}
function resetF(){['f-studio','f-perf','f-tag','f-d1','f-d2'].forEach(id=>{const e=$(id);if(e)e.value=''});$('tb-res').value='';$('gs').value='';_favOnly=false;$('favbtn').style.borderColor='';applyF();}
let _searchTimer=null;
function currentSearchContext(){
  if($('do')?.classList.contains('open')) return 'library';
  if($('vphotos')?.classList.contains('on')) return 'photos';
  if($('tab-performers')?.classList.contains('on')) return 'performers';
  if($('tab-sites')?.classList.contains('on')) return 'sites';
  return 'library';
}
async function _primeRandomVLCPool(list){
  // Warm a small set of signed device links so the first iOS/Android VLC action
  // can launch without making the user wait for link creation. The actual
  // randomized queue is still created server-side when Random VLC is invoked.
  if(!_isRemote()||(!_isIOS()&&!_isAndroid()))return;
  const candidates=[...list].sort(()=>Math.random()-0.5).slice(0,6);
  await Promise.all(candidates.map(v=>primeDeviceLink(v._cid,v.id).catch(()=>null)));
}

function setGlobalSearchContext(view){
  const input=$('gs');
  if(!input)return;
  input.dataset.searchContext=view;
  const placeholder=view==='photos'?'Search photos…':view==='performers'?'Search performer…':view==='sites'?'Search studio…':'Search video…';
  input.placeholder=placeholder;
  input.setAttribute('aria-label',view==='photos'?'Search photo filenames and captions':placeholder);
}
function onSearch(){
  clearTimeout(_searchTimer);
  const q=($('gs').value||'').trim();
  const ctx=currentSearchContext();
  if(ctx==='photos'){
    const photoLibrary=window.ArchiveXPhotos;
    if(typeof photoLibrary?.search==='function') photoLibrary.search(q);
    return;
  }
  if(ctx==='performers'){
    _searchTimer=setTimeout(()=>loadPerformers(0,q),180);
    return;
  }
  if(ctx==='sites'){
    const local=$('sitesearch');if(local)local.value=q;
    _searchTimer=setTimeout(()=>loadSites(),180);
    return;
  }
  _searchTimer=setTimeout(()=>applyF(),180);
}


function applyF(opts){
  const keepPage=!!(opts&&opts.keepPage),prevPage=_page;
  if(_serverLibraryMode){
    _page=keepPage?prevPage:0;
    renderChips2();
    clearTimeout(_serverFilterTimer);
    _serverFilterTimer=setTimeout(()=>loadLib(null,false,true),140);
    return;
  }
  const q=($('gs').value||'').toLowerCase();
  const st=($('f-studio').value||'').toLowerCase();
  const pe=($('f-perf').value||'').toLowerCase();
  const tg=($('f-tag').value||'').toLowerCase();
  const d1=$('f-d1').value,d2=$('f-d2').value;
  const res=parseInt($('tb-res').value)||0;
  const sort=$('tb-sort').value||'date_desc';
  let v=[..._allV];
  if(_favOnly)v=v.filter(x=>x._fav);
  if(q)v=v.filter(x=>(x.name||'').toLowerCase().includes(q)||(x.studio||'').toLowerCase().includes(q)||((x.performers||[]).some(p=>p.toLowerCase().includes(q))));
  if(st)v=v.filter(x=>(x.studio||'').toLowerCase().includes(st));
  if(pe)v=v.filter(x=>(x.performers||[]).some(p=>p.toLowerCase().includes(pe)));
  if(tg)v=v.filter(x=>(x.tags||[]).some(t=>t.toLowerCase().includes(tg)));
  if(d1)v=v.filter(x=>!x.date||x.date>=d1);
  if(d2)v=v.filter(x=>!x.date||x.date<=d2+'T99');
  if(res){const rm={2160:'h>=2160',1440:'h>=1440&&h<2160',1080:'h>=1080&&h<1440',720:'h>=720&&h<1080',480:'h>0&&h<720'};const fn=new Function('h','return '+rm[res]);v=v.filter(x=>fn(x.height||0));}
  if(sort==='date_asc')v.sort((a,b)=>(a.date||'').localeCompare(b.date||''));
  else if(sort==='size_desc')v.sort((a,b)=>(b.size||0)-(a.size||0));
  else if(sort==='dur_desc')v.sort((a,b)=>(b.duration||0)-(a.duration||0));
  else if(sort==='name_asc')v.sort((a,b)=>(a.name||'').localeCompare(b.name||''));
  else if(sort==='studio_asc')v.sort((a,b)=>(a.studio||'').localeCompare(b.studio||'')||(a.name||'').localeCompare(b.name||''));
  else if(sort==='performer_asc')v.sort((a,b)=>((a.performers||[])[0]||'').localeCompare((b.performers||[])[0]||'')||(a.name||'').localeCompare(b.name||''));
  else if(sort==='tag_asc')v.sort((a,b)=>((a.tags||[])[0]||'').localeCompare((b.tags||[])[0]||'')||(a.name||'').localeCompare(b.name||''));
  _filtV=v;
  // A background refresh keeps the page being looked at; a filter change starts at page 1.
  _page=keepPage?Math.max(0,Math.min(prevPage,Math.ceil(v.length/_PER)-1)):0;
  renderChips2();
  renderGrid();
  if(_isRemote()&&(_isIOS()||_isAndroid()))_primeRandomVLCPool(v).catch(()=>{});
  updateRS();
}

function renderChips2(){
  const w=$('afc'),chips=[];
  const q=$('gs').value;if(q)chips.push({l:`Search: ${q}`,c:()=>{$('gs').value='';applyF();}});
  const st=$('f-studio').value;if(st)chips.push({l:`Studio: ${st}`,c:()=>{$('f-studio').value='';applyF();}});
  const pe=$('f-perf').value;if(pe)chips.push({l:`Performer: ${pe}`,c:()=>{$('f-perf').value='';applyF();}});
  const tg=$('f-tag').value;if(tg)chips.push({l:`Tag: ${tg}`,c:()=>{$('f-tag').value='';applyF();}});
  const res=$('tb-res').value;if(res)chips.push({l:'Res: '+$('tb-res').options[$('tb-res').selectedIndex].text,c:()=>{$('tb-res').value='';applyF();}});
  const d1=$('f-d1').value,d2=$('f-d2').value;if(d1||d2)chips.push({l:`Date: ${d1||'…'} → ${d2||'…'}`,c:()=>{$('f-d1').value='';$('f-d2').value='';applyF();}});
  if(_favOnly)chips.push({l:'Favourites',c:()=>{_favOnly=false;$('favbtn').style.borderColor='';applyF();}});
  w.className='afc'+(chips.length?' has':'');
  if(!chips.length){w.innerHTML='';return;}
  w.innerHTML='<span class="muted">Filters:</span>'+chips.map((c,i)=>`<span class="pill">${esc(c.l)}<span class="x" onclick="_fc(${i})">×</span></span>`).join('');
  w._c=chips;
  $('fbc').textContent=chips.length;$('fbc').style.display=chips.length?'':'none';
}
function _fc(i){const w=$('afc');if(w._c&&w._c[i])w._c[i].c();}
function setSt(m){const e=$('rs');if(e)e.textContent=m;}
function updateRS(){
  const tot=_serverLibraryMode?_serverLibTotal:_filtV.length,cs=_sel.size;
  const summary=$('rs');
  if(summary)summary.textContent=tot?`${tot.toLocaleString()} video${tot!==1?'s':''} · ${cs.toLocaleString()} channel${cs!==1?'s':''}`:'';
  const pill=$('ax2-selected-channel-count');if(pill){pill.textContent=`${cs} channel${cs===1?'':'s'} selected`;pill.hidden=cs===0;}
  const canRun=cs===1;
  ['ax2-selected-channel-shuffle','ax2-selected-channel-edit'].forEach(id=>{const button=$(id);if(button){button.disabled=!canRun;button.title=canRun?'Use selected channel':'Select one channel';}});
}

// ── SECTION: GRID ──


async function togFav(id,cid,btn){
  const target=findVideo(id,cid);if(!target||!btn)return;
  const wanted=!Boolean(target._fav),previous=Boolean(target._fav);btn.disabled=true;
  try{
    const lookup=await api(`/api/videos/lookup?channel_id=${encodeURIComponent(cid)}&message_id=${Number(id)}`);
    const mediaId=Number(lookup?.media_id||0);if(!mediaId)throw new Error('Video is not yet in the published catalogue');
    const result=await api('/api/library/bulk-favorite',{method:'POST',body:JSON.stringify({media_ids:[mediaId],favorite:wanted})});
    if(!result||result._ok===false||!result.ok||Number(result.updated||0)!==1)throw new Error(result?.detail||'Favourite was not saved');
    const key=vkey(cid,id);
    [_chanV[String(cid)]||[],_allV,_filtV,_pl].forEach(list=>(list||[]).forEach(item=>{if(vkey(item._cid,item.id)===key)item._fav=wanted;}));
    btn.classList.toggle('on',wanted);btn.setAttribute('aria-pressed',wanted?'true':'false');
    applyF({keepPage:true});
  }catch(e){target._fav=previous;btn.classList.toggle('on',previous);toast(e?.message||'Could not save favourite.');}
  finally{btn.disabled=false;}
}

function renderAxPaginationBar({page,pages,changePage,inputId,goInputFn,pageSizeInputId,pageSize,pageSizeFn,ariaLabel}){
  const disabledPrev=page<=0?' disabled':'';
  const disabledNext=page>=pages-1?' disabled':'';
  let h=`<div class="ax-pagination-bar" role="navigation" aria-label="${ariaLabel}">`;
  h+=`<div class="ax-pagination-summary"><label for="${inputId}">Page</label><input id="${inputId}" class="ax-pagination-input" type="number" inputmode="numeric" min="1" max="${pages}" step="1" value="${page+1}" aria-label="Current page, enter a page number" onchange="${goInputFn}()" onkeydown="if(event.key==='Enter'){event.preventDefault();this.blur()}"><span>of ${pages}</span></div>`;
  h+=`<div class="ax-pagination-page-size"><label for="${pageSizeInputId}">Items per page</label><select id="${pageSizeInputId}" class="ax-pagination-size-select" aria-label="Items per page" onchange="${pageSizeFn}(this.value)">${_AX_PAGE_SIZE_OPTIONS.map(size=>`<option value="${size}"${Number(pageSize)===size?' selected':''}>${size}</option>`).join('')}</select></div>`;
  h+=`<div class="ax-pagination-controls" aria-label="Page navigation">`;
  h+=`<button type="button" class="ax-page-nav" onclick="${changePage}(0)"${disabledPrev} aria-label="First page" title="First page">≪</button>`;
  h+=`<button type="button" class="ax-page-nav" onclick="${changePage}(${page-1})"${disabledPrev} aria-label="Previous page" title="Previous page">‹</button>`;
  const candidates=new Set([0,1,2,pages-3,pages-2,pages-1,page-1,page,page+1].filter(n=>n>=0&&n<pages));
  let desktopPages='';
  let last=-1;
  for(const n of [...candidates].sort((a,b)=>a-b)){
    if(last>=0&&n-last>1)desktopPages+=`<span class="ax-pagination-ellipsis" aria-hidden="true">…</span>`;
    desktopPages+=`<button type="button" class="ax-page-number${n===page?' is-current':''}" onclick="${changePage}(${n})"${n===page?' aria-current="page"':''} aria-label="Page ${n+1}" title="Page ${n+1}">${n+1}</button>`;
    last=n;
  }
  h+=`<span class="ax-page-number-set ax-page-number-set-desktop">${desktopPages}</span>`;
  const mobileStart=Math.max(0,Math.min(page-2,pages-3));
  h+='<span class="ax-page-number-set ax-page-number-set-mobile">';
  for(let n=mobileStart;n<Math.min(pages,mobileStart+3);n++){
    h+=`<button type="button" class="ax-page-number${n===page?' is-current':''}" onclick="${changePage}(${n})"${n===page?' aria-current="page"':''} aria-label="Page ${n+1}" title="Page ${n+1}">${n+1}</button>`;
  }
  h+='</span>';
  h+=`<button type="button" class="ax-page-nav" onclick="${changePage}(${page+1})"${disabledNext} aria-label="Next page" title="Next page">›</button>`;
  h+=`<button type="button" class="ax-page-nav" onclick="${changePage}(${pages-1})"${disabledNext} aria-label="Last page" title="Last page">≫</button>`;
  h+='</div></div>';
  return h;
}
function renderPg(){
  const tot=_serverLibraryMode?_serverLibTotal:_filtV.length,pages=Math.max(1,Math.ceil(tot/_PER));
  const pg=$('pg');
  if(!pg)return;
  if(!tot){pg.innerHTML='';return;}
  pg.innerHTML=renderAxPaginationBar({page:_page,pages,changePage:'gp',inputId:'lib-page-input',goInputFn:'gotoLibPageInput',pageSizeInputId:'lib-page-size',pageSize:_PER,pageSizeFn:'setLibraryRowsPerPage',ariaLabel:'Video library pagination'});
}
function gp(p){
  const total=_serverLibraryMode?_serverLibTotal:_filtV.length;
  const pages=Math.max(1,Math.ceil(total/_PER));
  _page=Math.max(0,Math.min(Number(p)||0,pages-1));
  _navPush({view:'library',page:_page});
  if(_serverLibraryMode)loadLib(null,false,true);else renderGrid();
  window.scrollTo(0,0);
}
function gotoLibPageInput(){
  const input=$('lib-page-input');
  const raw=String(input?.value||'').trim();
  if(!raw)return;
  const page=Math.max(1,parseInt(raw,10)||1);
  gp(page-1);
}
function setLibraryRowsPerPage(value){
  const size=_axNormalizePageSize(value);if(!size||size===_PER)return;
  const firstItem=_page*_PER;
  _PER=size;_page=Math.floor(firstItem/size);_axSavePageSize('videos',size);
  if(_serverLibraryMode)loadLib(null,false,true);else renderGrid();
}

// ── SECTION: DETAIL ──
let _dv=null,_npMode=null,_tagVideos=[];
function findVideo(msgId,cid){
  const m=x=>String(x.id)===String(msgId)&&String(x._cid)===String(cid);
  return _filtV.find(m)||_allV.find(m)||_perfVideos.find(m)||_siteVideos.find(m)||_tagVideos.find(m)||(_pl||[]).find(m)||null;
}
function contextList(){
  if($('vp').classList.contains('on')&&$('perf-detail').classList.contains('open'))return _perfVideos;
  if($('vsite').classList.contains('on')&&$('site-detail').classList.contains('open'))return _siteVideos;
  if($('vtg').classList.contains('on'))return _tagVideos;
  return _filtV;
}

// ── SECTION: PLAYBACK ──
async function identV(msgId,cid){
  setSt('Fingerprinting…');
  const r=await api('/api/identify',{method:'POST',body:JSON.stringify({channel_id:cid,message_id:msgId})});
  if(r.detail){setSt('Identify error: '+r.detail);return;}
  const h=r.filename_hints||{};
  const best=(r.matches&&r.matches[0])||null;
  let t=`OSHash: ${r.oshash||'n/a'} | studio: ${h.studio||'?'} | title: ${h.title||'?'}`;
  if(best){
    t+=` → ${best.title||'?'} (${best.studio||best.site||'?'}) via ${best.source||'?'}`;
    if(r.saved_to_catalogue){
      t+=' — saved.';
      // Reflect only applied metadata in the currently-loaded video record.
      const v=_allV.find(x=>x.id===msgId&&String(x._cid)===String(cid));
      if(v){
        if(best.studio||best.site)v.studio=best.studio||best.site;
        if(best.performers&&best.performers.length)v.performers=best.performers;
        if(best.tags&&best.tags.length)v.tags=best.tags;
        if(best.title)v.title=best.title;
        applyF();
        if(_dv&&String(_dv.id)===String(msgId)&&String(_dv._cid)===String(cid))fillDetail(_dv);
      }
    }else if(r.action==='suggested'){
      t+=' — saved as a review suggestion; uncertain performer identities were not created or applied.';
    }else{
      t+=' — not applied; review the result or scan this channel first.';
    }
  } else {
    t+=' → no match found.';
  }
  setSt(t);
}

// ── SECTION: LOCAL PERFORMER MAPPING ──
// ═══════════════════════════════════════════════
// SECTION: PERFORMERS PAGE
// ═══════════════════════════════════════════════
let _perfVideos=[],_perfCurId=null;
let _perfAuditData=null,_perfAuditBusy=false;
let _perfSuggestedMap={};
let _perfSeq=0;
let _perfNavSeq=0;
let _perfMappingRefreshSeq=0;
let _perfAll=[];
const _performerMerge={active:false,keepId:null,mergeIds:new Set(),busy:false};
let _perfPage=0;
let _perfQuery='';
let _perfGender='all';

// "← Back" inside the app behaves exactly like the browser Back button.

// Name of the performer/studio an artwork <img> belongs to (cards, related tiles, profile headers).
function _axEntityNameOf(img){
  try{
    if(img.dataset.entityName)return img.dataset.entityName;
    const lab=img.closest('[aria-label^="Open performer "],[aria-label^="Open studio "]');
    if(lab)return lab.getAttribute('aria-label').replace(/^Open (performer|studio) /,'');
    const logo=img.closest('.studio-profile-logo');
    if(logo)return String(logo.getAttribute('aria-label')||'').replace(/ logo$/,'');
    if(img.alt)return img.alt;
    return img.closest('.pcard')?.querySelector('.pname')?.textContent||'';
  }catch(e){return '';}
}
const _AX_UNKNOWN_RE=/^\s*unknown\s+(performer|studio)\s*$/i;
function entityImageFallback(img, fallback=''){
  if(!img||img.dataset.fallbackApplied)return;
  img.dataset.fallbackApplied='1';
  img.style.display='none';
  const parent=img.parentElement;
  if(!parent)return;
  let el=parent.querySelector('.entity-fallback');
  if(!el){
    el=document.createElement('span');
    el.className='entity-fallback';
    el.style.cssText='font-size:42px;display:flex;align-items:center;justify-content:center;width:100%;height:100%;color:var(--accent);font-weight:700';
    parent.appendChild(el);
  }
  // Missing performer/studio artwork uses the Archive X brand mark consistently,
  // rather than switching between initials, a person icon, and different sizes.
  // The protected Unknown Performer / Unknown Studio entities get the Archive X default profile picture.
  if(_AX_UNKNOWN_RE.test(_axEntityNameOf(img))){
    el.classList.add('ax-unknown');
    el.innerHTML='<img class="ax-unknown-art" src="/static/icons/ax-unknown.jpg" alt="Archive X" decoding="async">';
    return;
  }
  el.innerHTML='<span class="ax-ph-bg" aria-hidden="true"></span><img class="entity-fallback-logo" src="/static/icons/ax-placeholder.png" alt="Archive X" loading="lazy" decoding="async">';
  const logo=el.querySelector('.entity-fallback-logo');
  if(logo)logo.onerror=()=>{logo.remove();el.textContent='AX';};
}
// Unknown Performer / Unknown Studio always show the Archive X default profile picture, even when an older image was stored for them.
document.addEventListener('load',e=>{
  const i=e.target;
  if(!i||i.tagName!=='IMG'||i.dataset.fallbackApplied||i.classList.contains('ax-unknown-art')||i.classList.contains('entity-fallback-logo'))return;
  if(!i.closest('.pimg,.studio-profile-logo,.performer-profile-avatar,.ax2-video-entity-avatar,.entity-art,.entity-performer-art,.entity-studio-art,.related-studio-art'))return;
  if(_AX_UNKNOWN_RE.test(_axEntityNameOf(i)))entityImageFallback(i);
},true);
function entityImageLoaded(img){
  if(!img||img.dataset.fallbackApplied||!img.complete||!img.naturalWidth)return;
  img.classList.add('ax-img-ready');
  const parent=img.parentElement;
  if(!parent)return;
  parent.querySelector('.entity-fallback')?.remove();
  parent.style.setProperty('--entity-bg', `url("${String(img.currentSrc||img.src||'').replace(/\"/g,'%22').replace(/\)/g,'%29')}")`);
}
function performerImageFallback(img, fallback=''){
  if(!img)return;
  // Prefer the provider URL directly when the local image endpoint/cache fails.
  // This avoids a failed cache -> failed cache retry loop and also avoids the
  // CORS-sensitive fetch/blob path used for video thumbnails.
  const provider=img.dataset.providerSrc||'';
  if(provider && img.dataset.providerTried!=='1'){
    img.dataset.providerTried='1';
    if(img.src!==provider){img.src=provider;return;}
  }
  const local=img.dataset.localSrc||'';
  if(local && img.dataset.localTried!=='1'){
    img.dataset.localTried='1';
    const localUrl=new URL(local, location.href).href;
    if(img.src!==localUrl){img.src=localUrl;return;}
  }
  entityImageFallback(img,fallback);
}
function studioImageFallback(img, fallback=''){
  if(!img)return;
  const provider=img.dataset.providerSrc||'';
  if(provider && img.dataset.providerTried!=='1'){
    img.dataset.providerTried='1';
    if(img.src!==provider){img.src=provider;return;}
  }
  const local=img.dataset.localSrc||'';
  if(local && img.dataset.localTried!=='1'){
    img.dataset.localTried='1';
    const localUrl=new URL(local, location.href).href;
    if(img.src!==localUrl){img.src=localUrl;return;}
  }
  entityImageFallback(img,fallback);
}
let _perfQuietNext=false,_siteQuietNext=false;let _PERF_PER_PAGE=_axSavedPageSize('performers');let _SITE_PER_PAGE=_axSavedPageSize('studios'),_sitePage=0,_siteRows=[];
let _perfGenderFilter='all';
let _perfSort='default';
function setPerformerGenderFilter(value){
  const next=['all','female','male'].includes(String(value||''))?String(value):'all';
  _perfGenderFilter=next;
  document.querySelectorAll('#perf-gender-filter .perf-gender-btn').forEach(btn=>{btn.classList.toggle('on',btn.dataset.gender===next);btn.setAttribute('aria-pressed',btn.dataset.gender===next?'true':'false');});
  _perfPage=0; renderPerformersPage();
  _navPush({view:'performers',page:0,q:_perfQuery||'',gender:_perfGenderFilter,sort:_perfSort});
  window.scrollTo(0,0);
}
function setPerformerSort(value){
  const next=['default','name_asc','name_desc'].includes(String(value||''))?String(value):'default';
  _perfSort=next;
  const select=$('perf-sort-select');if(select)select.value=next;
  _perfPage=0;renderPerformersPage();
  _navPush({view:'performers',page:0,q:_perfQuery||'',gender:_perfGenderFilter,sort:_perfSort});
}
let _perfToolbarPinRaf=0,_perfToolbarPinListenersReady=false;
function queuePerformerToolbarPinSync(){
  if(_perfToolbarPinRaf)return;
  _perfToolbarPinRaf=requestAnimationFrame(()=>{_perfToolbarPinRaf=0;syncPerformerToolbarPin();});
}
function releasePerformerToolbarPin(bar,spacer){
  bar.classList.remove('performer-toolbar-pinned');
  ['position','left','top','width','right','margin','max-width','transform'].forEach(property=>bar.style.removeProperty(property));
  if(spacer){spacer.style.setProperty('display','none','important');spacer.style.height='0px';spacer.style.marginBottom='0';}
}
function syncPerformerToolbarPin(){
  const bar=$('perf-toolbar'),view=$('vp');if(!bar||!view)return;
  let spacer=$('perf-toolbar-spacer');
  if(window.matchMedia?.('(max-width: 767px)')?.matches){
    releasePerformerToolbarPin(bar,spacer);
    return;
  }
  if(!view.classList.contains('on')&&!bar.classList.contains('performer-toolbar-pinned'))return;
  if(!spacer){spacer=document.createElement('div');spacer.id='perf-toolbar-spacer';spacer.setAttribute('aria-hidden','true');spacer.style.cssText='display:none;width:100%;pointer-events:none';bar.parentNode?.insertBefore(spacer,bar);}
  const active=view.classList.contains('on')&&getComputedStyle(view).display!=='none'&&!document.querySelector('#perf-detail.open')&&getComputedStyle(bar).display!=='none'&&bar.childElementCount>0;
  const pinned=bar.classList.contains('performer-toolbar-pinned');
  const sourceRect=pinned?spacer.getBoundingClientRect():bar.getBoundingClientRect();
  const header=$('hdr'),headerBottom=header?Math.max(0,header.getBoundingClientRect().bottom):62;
  const top=Math.round(headerBottom+4),height=Math.max(1,bar.offsetHeight||sourceRect.height||48);
  const viewRect=view.getBoundingClientRect();
  const shouldPin=active&&sourceRect.top<top&&viewRect.bottom>top+Math.min(height,36);
  if(!shouldPin){
    if(pinned)releasePerformerToolbarPin(bar,spacer);
    else{spacer.style.setProperty('display','none','important');spacer.style.height='0px';spacer.style.marginBottom='0';}
    return;
  }
  // The spacer preserves the toolbar's original place in document flow while
  // the real controls are viewport-fixed. This works even when a nested panel
  // or browser-specific overflow rule breaks CSS sticky positioning.
  if(!pinned){spacer.style.setProperty('display','block','important');spacer.style.height=`${height}px`;spacer.style.marginBottom=getComputedStyle(bar).marginBottom||'12px';}
  const r=spacer.getBoundingClientRect();
  spacer.style.height=`${height}px`;
  bar.classList.add('performer-toolbar-pinned');
  bar.style.setProperty('position','fixed','important');
  bar.style.setProperty('left',`${Math.round(r.left)}px`,'important');
  bar.style.setProperty('top',`${top}px`,'important');
  bar.style.setProperty('width',`${Math.round(r.width)}px`,'important');
  bar.style.setProperty('right','auto','important');
  bar.style.setProperty('margin','0','important');
  bar.style.setProperty('max-width',`${Math.round(r.width)}px`,'important');
  bar.style.setProperty('transform','none','important');
}
function setupPerformerToolbarPinning(){
  if(_perfToolbarPinListenersReady)return;
  _perfToolbarPinListenersReady=true;
  window.addEventListener('scroll',queuePerformerToolbarPinSync,{passive:true});
  document.addEventListener('scroll',queuePerformerToolbarPinSync,{passive:true,capture:true});
  window.addEventListener('resize',queuePerformerToolbarPinSync,{passive:true});
}
function syncPerformerHeaderControls(view){
  setupPerformerToolbarPinning();
  // Filter, sort and multi-select merge actions stay in the Performers toolbar.
  const filter=$('perf-gender-filter'), bar=$('perf-toolbar'), activity=$('global-activity-btn');
  if(filter&&bar&&filter.parentElement!==bar)bar.appendChild(filter);
  if(filter)filter.style.display='flex';
  if(bar&&!$('perf-sort-wrap')){
    const label=document.createElement('label');label.id='perf-sort-wrap';label.className='perf-sort-wrap';label.innerHTML='<span>Sort</span><select id="perf-sort-select" class="inp sm" aria-label="Sort performers" onchange="setPerformerSort(this.value)"><option value="default">Default order</option><option value="name_asc">Name A–Z</option><option value="name_desc">Name Z–A</option></select>';
    bar.insertBefore(label,$('perf-merge-mode-btn')||null);
  }
  if(bar&&!$('perf-merge-mode-btn')){
    const btn=document.createElement('button');btn.type='button';btn.id='perf-merge-mode-btn';btn.className='btn sm';btn.onclick=togglePerformerMergeMode;bar.appendChild(btn);
  }
  if(bar&&!$('perf-merge-commit-btn')){
    const btn=document.createElement('button');btn.type='button';btn.id='perf-merge-commit-btn';btn.className='btn sm pri';btn.onclick=performerMergeCommit;btn.style.display='none';bar.appendChild(btn);
  }
  if(bar&&!$('perf-merge-hint')){
    const hint=document.createElement('span');hint.id='perf-merge-hint';hint.className='muted';hint.style.cssText='font-size:11px;max-width:480px';bar.appendChild(hint);
  }
  const select=$('perf-sort-select');if(select)select.value=_perfSort;
  updatePerformerMergeUI();
  if(activity)activity.style.display='';
  queuePerformerToolbarPinSync();
}
function togglePerformerMergeMode(){
  if(_performerMerge.busy)return;
  _performerMerge.active=!_performerMerge.active;
  _performerMerge.keepId=null;
  _performerMerge.mergeIds.clear();
  updatePerformerMergeUI();
  renderPerformersPage();
  if(_performerMerge.active)toast('Select one performer to keep, then select every duplicate to merge into it.');
}
function updatePerformerMergeUI(){
  const btn=$('perf-merge-mode-btn'),commit=$('perf-merge-commit-btn'),hint=$('perf-merge-hint'),view=$('vp');
  const keep=_perfAll.find(p=>Number(p.id)===Number(_performerMerge.keepId));
  const count=_performerMerge.mergeIds.size;
  if(btn){btn.textContent=_performerMerge.active?'Cancel merge':'Merge duplicates';btn.classList.toggle('pri',_performerMerge.active);btn.disabled=!!_performerMerge.busy;}
  if(commit){commit.textContent=`Merge selected (${count})`;commit.style.display=_performerMerge.active?'inline-flex':'none';commit.disabled=!!_performerMerge.busy||!keep||count===0;}
  if(hint){hint.textContent=!_performerMerge.active?'':_performerMerge.busy?`Merging ${count} duplicate${count===1?'':'s'} atomically…`:keep?`Keep: ${keep.name} · ${count} duplicate${count===1?'':'s'} selected. Click cards to add/remove, then merge.`:'Select the canonical performer to keep first.';hint.style.display=_performerMerge.active?'inline':'none';}
  if(view)view.classList.toggle('performer-merge-mode',_performerMerge.active);
  document.querySelectorAll('#perf-list .performer-card').forEach(card=>{
    const id=Number(card.dataset.entityId),isKeep=_performerMerge.active&&id===Number(_performerMerge.keepId),isSelected=_performerMerge.active&&_performerMerge.mergeIds.has(id);
    card.classList.toggle('merge-keeper',isKeep);card.classList.toggle('merge-selected',isSelected);
    if(_performerMerge.active)card.setAttribute('aria-pressed',String(isKeep||isSelected));else card.removeAttribute('aria-pressed');
  });
}
async function performerMergePick(id){
  id=Number(id);
  if(!_performerMerge.active||_performerMerge.busy)return;
  const chosen=_perfAll.find(p=>Number(p.id)===id);
  if(!chosen){toast('Performer not found in the current list. Reload and try again.');return;}
  if(_performerMerge.keepId===null){_performerMerge.keepId=id;updatePerformerMergeUI();toast(`Keep “${chosen.name}”. Now select all duplicates to merge into it.`);return;}
  if(Number(_performerMerge.keepId)===id){toast('This is the keeper. Select other performer cards to add them as duplicates.');return;}
  if(!_perfAll.some(p=>Number(p.id)===Number(_performerMerge.keepId))){_performerMerge.keepId=null;_performerMerge.mergeIds.clear();updatePerformerMergeUI();toast('Keeper record is no longer available. Select it again.');return;}
  if(_performerMerge.mergeIds.has(id))_performerMerge.mergeIds.delete(id);else _performerMerge.mergeIds.add(id);
  updatePerformerMergeUI();
}
async function performerMergeCommit(){
  if(!_performerMerge.active||_performerMerge.busy||_performerMerge.keepId===null||!_performerMerge.mergeIds.size)return;
  const keep=_perfAll.find(p=>Number(p.id)===Number(_performerMerge.keepId));
  const ids=[..._performerMerge.mergeIds].filter(id=>id!==Number(_performerMerge.keepId));
  const names=ids.map(id=>{const p=_perfAll.find(item=>Number(item.id)===Number(id));return p?`${p.name} (ID ${id})`:`#${id}`;});
  if(!keep||!ids.length){toast('Select a keeper and at least one duplicate.');return;}
  const preview=names.slice(0,8).map(name=>`• ${name}`).join('\n')+(names.length>8?`\n• …and ${names.length-8} more`:'');
  const ok=await axConfirm(`Merge ${ids.length} duplicate performer record${ids.length===1?'':'s'} into “${keep.name}”?\n\n${preview}\n\nProvider IDs, aliases, video links and related metadata will be consolidated. All selected merges run in one transaction and are rolled back if any merge fails. This cannot be undone.`,{title:'Merge duplicate performers',okLabel:`Merge ${ids.length} selected`,confirmLabel:`Merge ${ids.length} selected`,danger:true});
  if(!ok)return;
  _performerMerge.busy=true;updatePerformerMergeUI();
  try{
    const result=await api('/api/performers/merge-batch',{method:'POST',body:JSON.stringify({keep_id:Number(keep.id),merge_ids:ids})});
    if(!result||!result.ok)throw new Error(result?.detail||'Performer batch merge failed');
    const mergedCount=Number(result.merged_count||ids.length);
    _performerMerge.active=false;_performerMerge.keepId=null;_performerMerge.mergeIds.clear();_performerMerge.busy=false;updatePerformerMergeUI();
    await loadPerformers(_perfPage,_perfQuery);
    toast(`Merged ${mergedCount} duplicate${mergedCount===1?'':'s'} into “${result.name||keep.name}”. Video associations and provider IDs were preserved.`);
  }catch(e){
    _performerMerge.busy=false;updatePerformerMergeUI();
    toast(e?.message||'Performer batch merge failed. No changes were committed.');
  }
}
const _perfAuditLabels={
  strong_merge:{title:'Same canonical name and compatible provider IDs',treatment:'Eligible for automatic cleanup',note:'Normalized canonical names match, with no conflicting provider IDs.'},
  candidate_merge:{title:'Different spelling with a shared alias or provider ID',treatment:'Depends on evidence',note:'An identical namespaced provider ID can be auto-merged; alias-only evidence stays in review.'},
  review:{title:'Conflicting identity evidence',treatment:'Keep in review',note:'Different confirmed provider IDs may indicate different people. Do not merge automatically.'},
  suggest_review:{title:'Similar but not identical names',treatment:'Suggest for review only',note:'Name similarity alone is not proof of identity. Keep separate until confirmed.'}
};
function perfAuditClose(){const dlg=$('perf-duplicate-audit');if(dlg){dlg.classList.remove('open');dlg.setAttribute('aria-hidden','true');}}
function _perfAuditIdentityText(p){
  const ids=p.provider_ids||{};const values=[];
  Object.keys(ids).forEach(provider=>{const raw=Array.isArray(ids[provider])?ids[provider]:[ids[provider]];if(raw.length)values.push(`${provider}: ${raw.join(', ')}`);});
  return values.length?values.join(' · '):'No saved provider ID';
}
function _perfAuditGroupHtml(group,index){
  if(group.kind==='junk_fragment'){
    const p=(group.performers||[])[0]||{};
    const target=group.suggested_target||null;
    const linked=Number(p.video_count||0)>0;
    const targetHtml=target?`<div style="padding:8px 10px;border:1px solid var(--line);border-radius:8px;margin:8px 0;background:var(--panel2)"><small class="muted">Suggested merge target</small><div style="display:flex;align-items:center;justify-content:space-between;gap:8px"><b>${esc(target.name||'Performer')}</b><span class="muted">ID ${Number(target.id)} · ${Number(target.video_count||0)} linked videos</span></div></div>`:'<div class="muted" style="margin:8px 0">No safe suggested target found. Review manually before changing this record.</div>';
    const mergeBtn=target&&Number(target.id)!==Number(p.id)?`<button type="button" class="btn sm pri" onclick="performerAuditJunkAction(${Number(p.id)},'merge',${Number(target.id)})">Merge into ${esc(target.name||'target')}</button>`:'';
    const deleteBtn=!linked?`<button type="button" class="btn sm" onclick="performerAuditJunkAction(${Number(p.id)},'delete',null)">Delete unlinked record</button>`:'<span class="muted" style="font-size:11px">Delete disabled · merge linked videos first</span>';
    return `<section class="pan perf-audit-group" id="perf-audit-group-${index}" data-group-index="${index}" style="margin:0 0 10px;border-color:var(--warn,#c48b3b)"><div style="display:flex;justify-content:space-between;gap:10px;align-items:flex-start;flex-wrap:wrap"><div style="min-width:0;flex:1"><div style="font-size:13px;font-weight:700">Likely filename/title fragment: ${esc(p.name||'Unnamed performer')}</div><div class="muted" style="font-size:11px;margin-top:3px">ID ${Number(p.id)} · ${Number(p.video_count||0)} linked video(s)${p.has_image?' · artwork':''}</div><div style="font-size:11px;line-height:1.45;margin-top:5px;color:var(--muted)">${esc(group.reason||'This performer name resembles a release tag or scene-title fragment.')}</div></div></div>${targetHtml}<div style="display:flex;gap:7px;align-items:center;flex-wrap:wrap">${mergeBtn}${deleteBtn}</div></section>`;
  }
  const info=_perfAuditLabels[group.kind]||_perfAuditLabels.review;
  const similarity=group.similarity?` · Similarity ${Math.round(Number(group.similarity)*100)}%`:'';
  const protectedUnknown=!!group.contains_unknown_performer;
  const members=(group.performers||[]).map(p=>`<div class="perf-audit-member"><div class="perf-audit-member-main"><b>${esc(p.name||'Unnamed performer')}</b><span class="muted">ID ${Number(p.id)} · ${Number(p.video_count||0)} video${Number(p.video_count||0)===1?'':'s'}${p.has_image?' · artwork':''}</span><small>${esc(_perfAuditIdentityText(p))}</small>${p.aliases?.length?`<small>Aliases: ${esc(p.aliases.join(', '))}</small>`:''}</div><div class="perf-audit-member-controls">${protectedUnknown?'<span class="muted">Protected fallback</span>':`<label><input type="radio" name="perf-audit-keep-${index}" value="${Number(p.id)}" onchange="updatePerformerAuditGroup(${index})"> Keep</label><label><input type="checkbox" class="perf-audit-merge" value="${Number(p.id)}" onchange="updatePerformerAuditGroup(${index})"> Merge</label>`}</div></div>`).join('');
  const mergeButton=protectedUnknown?'':`<button type="button" class="btn sm pri perf-audit-merge-btn" disabled onclick="performerAuditMergeGroup(${index})">Merge checked</button>`;
  const footer=protectedUnknown?'Unknown Performer is protected. Keep this group separate; unresolved video identities should stay under Unknown Performer or in Performer Suggestions.':'For manual merge, choose exactly one “Keep” record and check one or more “Merge” records. Similar-name suggestions should stay separate unless identity is confirmed.';
  return `<section class="pan perf-audit-group" id="perf-audit-group-${index}" data-group-index="${index}" style="margin:0 0 10px"><div style="display:flex;justify-content:space-between;gap:10px;align-items:flex-start;flex-wrap:wrap"><div style="min-width:0;flex:1"><div style="font-size:13px;font-weight:700">${esc(group.title||info.title)}${similarity}</div><div style="font-size:11px;line-height:1.45;margin-top:3px;color:var(--muted)">${esc(group.reason||info.note)}</div><div style="font-size:11px;margin-top:4px;color:var(--accent)">Recommended treatment: ${esc(group.recommended_treatment||info.treatment)}</div></div>${mergeButton}</div><div class="perf-audit-members">${members}</div><div class="muted" style="font-size:11px;margin-top:5px">${esc(footer)}</div></section>`;
}
let _perfAuditLastOutcome='';
function renderPerformerDuplicateAudit(data){
  const status=$('perf-audit-status'),results=$('perf-audit-results');if(!results||!status)return;
  const groups=Array.isArray(data.groups)?data.groups:[];
  const autoCount=groups.filter(group=>group?.auto_merge_eligible===true).length;
  const autoBtn=$('perf-audit-auto-btn');if(autoBtn){autoBtn.disabled=!autoCount||_perfAuditAutoBusy;autoBtn.textContent=autoCount?`Auto-merge high-confidence (${autoCount})`:'No safe auto-merges';}
  const totals=`Scanned ${Number(data.performer_count||0).toLocaleString()} performer records · ${autoCount} high-confidence groups safe to auto-merge · ${Number(data.candidate_merge_groups||0)} alias/provider candidates · ${Number(data.review_groups||0)} identity-conflict groups · ${Number(data.suggestion_groups||0)} similar-name suggestions · ${Number(data.junk_fragment_groups||0)} likely filename/title fragments · ${Number(data.unmatched_performer_count||0)} records with no linked duplicate evidence`;
  status.textContent=_perfAuditLastOutcome?`${_perfAuditLastOutcome} ${totals}`:totals;
  if(!groups.length){results.innerHTML='<div class="pan"><b>No likely duplicate groups found.</b><div class="muted" style="font-size:11px;margin-top:5px">No strong name, provider-ID, alias, or review-only similarity candidates were found. No records were changed.</div></div>';return;}
  const heading={strong_merge:'Strong candidates',candidate_merge:'Alias / provider candidates',review:'Review before merging',suggest_review:'Similar-name suggestions',junk_fragment:'Likely filename/title fragments'};
  let out='';let previous='';
  groups.forEach((group,index)=>{if(group.kind!==previous){previous=group.kind;out+=`<h4 style="font-size:12px;margin:12px 0 7px">${esc(heading[group.kind]||'Candidates')}</h4>`;}out+=_perfAuditGroupHtml(group,index);});
  if(data.suggestions_truncated)out+=`<div class="muted" style="padding:8px;font-size:11px">Showing ${Number(data.suggestion_groups_returned||0)} of ${Number(data.suggestion_groups||0)} similar-name suggestions. The audit scanned the full catalogue; lower-confidence suggestions are capped to keep the review dialog usable.</div>`;
  out+='<div class="muted" style="font-size:11px;padding:6px 0 12px">Records without reliable identity evidence are not paired and should be kept separate.</div>';
  results.innerHTML=out;
}
async function openPerformerDuplicateAudit(){
  const dlg=$('perf-duplicate-audit'),status=$('perf-audit-status'),results=$('perf-audit-results');if(!dlg)return;
  dlg.classList.add('open');dlg.setAttribute('aria-hidden','false');
  if(_perfAuditBusy)return;
  _perfAuditLastOutcome='';
  _perfAuditBusy=true;if(status)status.textContent='Auditing the entire performer catalogue…';if(results)results.innerHTML='<div class="pan muted">Reading performer identities, aliases, provider IDs and video links. The audit itself is read-only; automatic merges require a separate explicit confirmation.</div>';
  const button=$('ops-perf-audit-btn')||$('perf-audit-btn');if(button)button.disabled=true;
  try{
    const data=await api('/api/performers/duplicate-audit',{cache:'no-store'});
    if(!data||data.ok!==true)throw new Error(data?.detail||'The performer audit failed.');
    _perfAuditData=data;renderPerformerDuplicateAudit(data);
  }catch(e){if(status)status.textContent=e?.message||'The performer audit failed; no records were changed.';if(results)results.innerHTML='';}
  finally{_perfAuditBusy=false;if(button)button.disabled=false;}
}
let _perfAuditAutoBusy=false;
async function performerAuditAutoCleanup(){
  if(_perfAuditAutoBusy||!_perfAuditData)return;
  const groups=Array.isArray(_perfAuditData.groups)?_perfAuditData.groups:[];
  const eligible=groups.filter(group=>group?.auto_merge_eligible===true);
  if(!eligible.length){toast('No high-confidence performer duplicates are safe to auto-merge. Ambiguous matches remain separate for review.');return;}
  const rows=eligible.reduce((total,group)=>total+Math.max(0,(group.performers||[]).length-1),0);
  const ok=await axConfirm(`Automatically merge ${rows} duplicate performer record(s) in ${eligible.length} high-confidence group(s)?

Archive X will merge only records with the same normalized canonical name or an identical namespaced provider ID, with no conflicting identity evidence. It will keep alias-only matches, different confirmed provider IDs, similar spellings, and Unknown Performer separate for review. Uncertain video identities remain in Performer Suggestions or Unknown Performer.

All selected automatic merges run in one transaction and are rolled back together if any merge fails.`,{title:'Auto-clean high-confidence duplicates',okLabel:`Auto-merge ${rows}`,confirmLabel:`Auto-merge ${rows}`,danger:true});
  if(!ok)return;
  _perfAuditAutoBusy=true;
  const btn=$('perf-audit-auto-btn'),status=$('perf-audit-status');if(btn){btn.disabled=true;btn.textContent='Cleaning…';}if(status)status.textContent=`Safely merging ${rows} duplicate performer record(s)…`;
  try{
    const result=await api('/api/performers/duplicate-audit/auto-cleanup',{method:'POST',body:JSON.stringify({})});
    if(!result||result.ok!==true)throw new Error(result?.detail||'Automatic performer cleanup failed; no changes were committed.');
    _perfAuditData=result.audit||null;
    if(_perfAuditData)renderPerformerDuplicateAudit(_perfAuditData);
    await loadPerformers(_perfPage,_perfQuery);
    _perfAuditLastOutcome=`Auto-cleanup complete: merged ${Number(result.merged_performer_count||0)} performer record(s) in ${Number(result.merged_group_count||0)} group(s). Left ${Number(result.ambiguous_groups_left_for_review||0)} ambiguous group(s) separate for review; protected Unknown Performer groups: ${Number(result.protected_unknown_group_count||0)}.`;
    toast(`Auto-cleanup merged ${Number(result.merged_performer_count||0)} high-confidence duplicate performer record(s). Ambiguous matches were left for review.`);
  }catch(e){_perfAuditLastOutcome=e?.message||'Automatic cleanup failed; no changes were committed.';toast(e?.message||'Automatic cleanup failed. No changes were committed.');}
  finally{_perfAuditAutoBusy=false;if(_perfAuditData)renderPerformerDuplicateAudit(_perfAuditData);}
}
async function performerAuditJunkAction(performerId,action,targetId=null){
  const pId=Number(performerId),tId=targetId==null?null:Number(targetId);
  if(!pId||!['merge','delete'].includes(action)){toast('Choose a valid performer cleanup action.');return;}
  const record=(_perfAuditData?.groups||[]).filter(g=>g.kind==='junk_fragment').flatMap(g=>g.performers||[]).find(p=>Number(p.id)===pId);
  if(!record){toast('This audit record is stale. Refresh the audit before changing it.');return;}
  if(action==='merge'&&!tId){toast('No suggested merge target is available.');return;}
  if(action==='delete'&&Number(record.video_count||0)>0){toast('This performer still has linked videos; merge them first.');return;}
  const junkGroup=(_perfAuditData?.groups||[]).find(g=>g.kind==='junk_fragment'&&(g.performers||[]).some(p=>Number(p.id)===pId));
  const targetName=action==='merge'?(junkGroup?.suggested_target?.name||(_perfAuditData?.groups||[]).flatMap(g=>g.performers||[]).find(p=>Number(p.id)===tId)?.name||'the selected target'):'';
  const message=action==='merge'?`Merge “${record.name}” into “${targetName}”? All video links, aliases, provider identities and related metadata will be preserved on the target.`:`Delete “${record.name}”? This is allowed only because the record has no linked videos. This cannot be undone.`;
  if(!await axConfirm(message,{title:action==='merge'?'Merge filename fragment':'Delete unlinked fragment',okLabel:action==='merge'?'Merge performer':'Delete record',confirmLabel:action==='merge'?'Merge performer':'Delete record',danger:true}))return;
  try{
    const result=await api('/api/performers/junk-cleanup',{method:'POST',body:JSON.stringify({performer_id:pId,action,target_id:tId})});
    if(!result?.ok)throw new Error(result?.detail||'Performer cleanup failed.');
    _perfAuditLastOutcome=action==='merge'?`Merged likely fragment “${record.name}” into “${result.name||targetName}”.`:`Deleted unlinked junk record “${record.name}”.`;
    await loadPerformers(_perfPage,_perfQuery);
    _perfAuditBusy=false;
    await openPerformerDuplicateAudit();
    toast(_perfAuditLastOutcome);
  }catch(e){toast(e?.message||'Performer cleanup failed; no records were changed.');}
}

let _opsChannelMaintenanceLoaded=false;
let _channelDupData=null;
let _channelDupBusy=false;
let _channelDupDeleting=false;
const CHANNEL_DUP_DELETE_BATCH_LIMIT=20;
const CHANNEL_DUP_SNAPSHOT_FRAME_INDICES=[0,11,23,35];
let _channelDupSnapshotObserver=null;
let _channelDupSnapshotGeneration=0;
async function loadOpsChannelMaintenanceChannels(force=false){
  const select=$('ops-channel-maintenance-channel');if(!select)return;
  if(_opsChannelMaintenanceLoaded&&!force)return;
  const previous=String(select.value||'');select.disabled=true;select.innerHTML='<option value="">Loading saved channels…</option>';
  try{
    const channels=await api('/api/source-channels',{cache:'no-store'});
    if(!Array.isArray(channels))throw new Error(channels?.detail||'Could not load the saved channel list.');
    const unique=new Map();
    channels.forEach(channel=>{
      const id=String(channel?.telegram_id??'').trim();
      if(!/^-?\d+$/.test(id)||unique.has(id))return;
      unique.set(id,{id,title:channelDisplayName(id,channel?.title)});
    });
    const items=[...unique.values()].sort((a,b)=>a.title.localeCompare(b.title,undefined,{sensitivity:'base'}));
    select.innerHTML='<option value="">Choose a scan scope…</option><option value="__all_indexed__">All indexed videos · local review</option>'+items.map(item=>`<option value="${esc(item.id)}">${esc(item.title)}</option>`).join('');
    select.value=previous==='__all_indexed__'||items.some(item=>item.id===previous)?previous:'';
    _opsChannelMaintenanceLoaded=true;
  }catch(e){select.innerHTML='<option value="">Channel list unavailable — retry</option>';toast(e?.message||'Could not load channels for maintenance.');}
  finally{select.disabled=false;}
}
function channelDuplicateAuditClose(){
  if(_channelDupDeleting){toast('The delete batch is still running. Wait for it to finish before closing this review.');return;}
  const dlg=$('channel-duplicate-audit');if(!dlg)return;dlg.classList.remove('open');dlg.setAttribute('aria-hidden','true');
}
async function openChannelDuplicateMaintenance(){
  const viewSeq=_topLevelViewSeq;
  await loadOpsChannelMaintenanceChannels();
  if(viewSeq!==_topLevelViewSeq||!_viewOn('va'))return;
  const select=$('ops-channel-maintenance-channel'),channelId=String(select?.value||'').trim();
  if(!channelId){toast('Choose a channel or All indexed videos to scan first.');select?.focus();return;}
  const dlg=$('channel-duplicate-audit');if(dlg){dlg.classList.add('open');dlg.setAttribute('aria-hidden','false');}
  await scanChannelDuplicateAudit();
}
function channelDuplicateFormatSize(value){
  const n=Math.max(0,Number(value)||0);if(n<1024)return `${n} B`;if(n<1024*1024)return `${(n/1024).toFixed(1)} KB`;if(n<1024*1024*1024)return `${(n/(1024*1024)).toFixed(1)} MB`;return `${(n/(1024*1024*1024)).toFixed(2)} GB`;
}
function channelDuplicateCandidateKey(row){
  return `${String(row?.channel_id||'')}:${Number(row?.message_id||0)}`;
}
function channelDuplicateSharesIdentity(a,b){
  const documentId=row=>{const id=String(row?.document_id||'').trim(),key=String(row?.registry_key||'');return id|| (key.startsWith('doc:')?key.slice(4):'');};
  const docA=documentId(a),docB=documentId(b);
  if(docA&&docA===docB)return true;
  const hashA=String(a?.oshash||a?.registry_oshash||a?.uploaded_oshash||'').trim().toLowerCase(),hashB=String(b?.oshash||b?.registry_oshash||b?.uploaded_oshash||'').trim().toLowerCase();
  const sizeA=Number(a?.size||0),sizeB=Number(b?.size||0);
  return !!hashA&&hashA===hashB&&sizeA>0&&sizeA===sizeB;
}
function channelDuplicateSelectedTargets(){
  const selected=new Set([...document.querySelectorAll('.ax-channel-duplicate-select-input:checked')].map(input=>String(input.value||'')));
  return (_channelDupData?.groups||[]).flatMap((group,groupIndex)=>(group.candidates||[])
    .filter(row=>selected.has(channelDuplicateCandidateKey(row)))
    .map(row=>({group,groupIndex,row,key:channelDuplicateCandidateKey(row)})));
}
function channelDuplicateFindKeeper(group,target,selectedKeys){
  return (group?.candidates||[]).find(candidate=>
    channelDuplicateCandidateKey(candidate)!==channelDuplicateCandidateKey(target)
    &&!selectedKeys.has(channelDuplicateCandidateKey(candidate))
    &&channelDuplicateSharesIdentity(target,candidate)
  )||null;
}
function channelDuplicateUpdateSelectionUI(){
  const selected=channelDuplicateSelectedTargets();
  const selectedKeys=new Set(selected.map(item=>item.key));
  const channelCount=new Set(selected.map(item=>String(item.row.channel_id||''))).size;
  const hasMissingKeep=selected.some(item=>!channelDuplicateFindKeeper(item.group,item.row,selectedKeys));
  const count=$('channel-duplicate-selected-count'),hint=$('channel-duplicate-selection-hint'),button=$('channel-duplicate-audit-delete');
  if(count)count.textContent=`${selected.length} selected${channelCount?` · ${channelCount} channel${channelCount===1?'':'s'}`:''}`;
  if(hint)hint.textContent=selected.length>CHANNEL_DUP_DELETE_BATCH_LIMIT
    ?`Select no more than ${CHANNEL_DUP_DELETE_BATCH_LIMIT} videos per batch.`
    :hasMissingKeep
      ?'Leave one directly matching copy unselected in each group before deleting.'
      :selected.length
        ?'Each selected copy will be checked against a retained match before deletion.'
        :'Select copies to delete; leave one directly matching copy in each group.';
  if(button){
    button.innerHTML=`${axIcon('trash','ax-inline-action-icon')} Delete${selected.length?` ${selected.length}`:''} selected`;
    button.disabled=!selected.length||selected.length>CHANNEL_DUP_DELETE_BATCH_LIMIT||hasMissingKeep||_channelDupDeleting||_channelDupBusy;
  }
  const close=$('channel-duplicate-audit-close'),headerClose=$('channel-duplicate-audit-header-close');
  if(close)close.disabled=_channelDupDeleting;
  if(headerClose)headerClose.disabled=_channelDupDeleting;
  document.querySelectorAll('.ax-channel-duplicate-candidate').forEach(card=>{
    const checkbox=card.querySelector('.ax-channel-duplicate-select-input');
    card.classList.toggle('is-selected',!!checkbox?.checked);
  });
  document.querySelectorAll('.ax-channel-duplicate-select-input').forEach(input=>{
    input.disabled=_channelDupDeleting||_channelDupBusy||(!input.checked&&selected.length>=CHANNEL_DUP_DELETE_BATCH_LIMIT);
  });
}
function channelDuplicateSelectionChanged(input){
  if(_channelDupDeleting||_channelDupBusy)return;
  if(input?.checked&&channelDuplicateSelectedTargets().length>CHANNEL_DUP_DELETE_BATCH_LIMIT){
    input.checked=false;
    toast(`Delete no more than ${CHANNEL_DUP_DELETE_BATCH_LIMIT} videos per batch.`,5000);
  }
  channelDuplicateUpdateSelectionUI();
}
function renderChannelDuplicateAudit(data){
  const status=$('channel-duplicate-audit-status'),results=$('channel-duplicate-audit-results');if(!status||!results)return;
  const count=Number(data.indexed_video_count||0),groups=Array.isArray(data.groups)?data.groups:[];
  const allIndexed=data.scope==='all_indexed';
  const computed=Number(data.hashes_computed||0);
  const candidateCount=groups.reduce((sum,group)=>sum+(Array.isArray(group.candidates)?group.candidates.length:0),0);
  const auditChannelName=channelDisplayName(data.channel_id,data.channel_title);
  const title=allIndexed?'Duplicate videos across channels':`Duplicate videos in ${auditChannelName}`;
  const dialogTitle=$('channel-duplicate-audit-title'),context=$('channel-duplicate-audit-context');
  if(dialogTitle)dialogTitle.textContent=title;
  if(context)context.textContent=allIndexed?'Cross-channel matches · select copies to delete after review':'Verified matches · select unwanted copies to delete';
  const indexedSummary=allIndexed
    ? `${count.toLocaleString()} indexed videos across ${Number(data.indexed_channel_count||0).toLocaleString()} channels · ${Number(data.complete_channel_count||0).toLocaleString()} channels with complete scans`
    : `${count.toLocaleString()} indexed videos · ${computed.toLocaleString()} files fingerprinted`;
  const warnings=[data.warning,...(data.fingerprint_warnings||[]).slice(0,4).map(message=>String(message))].filter(Boolean);
  status.innerHTML=`
    <div class="ax-channel-duplicate-status-main">
      <span class="ax-channel-duplicate-scan-state" data-state="${warnings.length?'warning':'complete'}"><span class="ax-channel-duplicate-status-dot" aria-hidden="true"></span>${warnings.length?'Scan complete · check coverage':'Scan complete'}</span>
      <span class="ax-channel-duplicate-stat">${groups.length} group${groups.length===1?'':'s'}</span>
      <span class="ax-channel-duplicate-stat">${candidateCount} cop${candidateCount===1?'y':'ies'}</span>
      <span class="ax-channel-duplicate-stat ${allIndexed?'is-cross-channel':''}">${allIndexed?'Across channels':esc(auditChannelName)}</span>
    </div>
    <div class="ax-channel-duplicate-status-detail">${indexedSummary}</div>
    ${warnings.length?`<div class="ax-channel-duplicate-warnings">${warnings.map(esc).join('<br>')}${data.fingerprint_warnings_truncated?'<br>Additional fingerprint warnings were omitted.':''}</div>`:''}`;
  if(!groups.length){results.innerHTML=`<div class="ax-channel-duplicate-empty"><span class="ax-channel-duplicate-empty-icon" aria-hidden="true">${axIcon('duplicate')}</span><div><b>No verified duplicate videos found.</b><p>${count?'The scan found no repeated Telegram document IDs or matching OSHASH content fingerprints.':allIndexed?'No active indexed video records were found.':'No active indexed videos were found for this channel.'} No Telegram messages were changed.</p></div></div>`;channelDuplicateUpdateSelectionUI();channelDuplicateWatchSnapshots(results);return;}
  results.innerHTML=groups.map((group,index)=>{
    const rows=Array.isArray(group.candidates)?group.candidates:[];
    const proof=(group.proof||[]).map(esc).join(' · ');
    return `<section class="ax-channel-duplicate-group" data-group-index="${index}">
      <div class="ax-channel-duplicate-group-head">
        <div class="ax-channel-duplicate-group-title"><b>Group ${index+1}</b><span>${Number(group.duplicate_count||rows.length)} matching copies</span></div>
        <span class="ax-channel-duplicate-proof">${proof}</span>
      </div>
      <div class="ax-channel-duplicate-candidates">
      ${rows.map(row=>{
        const messageId=Number(row.message_id||0),channelId=String(row.channel_id||data.channel_id||''),videoTitle=String(row.title||row.filename||`Video ${messageId}`);
        const channelTitle=channelDisplayName(channelId,row.channel_title||data.channel_title);
        const width=Number(row.width||0),height=Number(row.height||0),duration=Number(row.duration||0),size=channelDuplicateFormatSize(row.size);
        const resolution=width>0&&height>0?`${width}×${height}`:'Unknown';
        const durationText=duration>0?`${Math.floor(duration/60)}:${String(Math.floor(duration%60)).padStart(2,'0')}`:'Unknown';
        const key=channelDuplicateCandidateKey({...row,channel_id:channelId,message_id:messageId});
        const frames=CHANNEL_DUP_SNAPSHOT_FRAME_INDICES.map((frameIndex,slot)=>`<figure class="ax-channel-duplicate-snapshot" data-frame-index="${frameIndex}">
          <div class="ax-channel-duplicate-snapshot-image"><img alt="Cached snapshot ${slot+1} of ${esc(videoTitle)}" decoding="async"><span class="ax-channel-duplicate-snapshot-state">Loading cached frame…</span></div>
          <figcaption>Frame ${frameIndex+1} / 36</figcaption>
        </figure>`).join('');
        return `<article class="ax-channel-duplicate-candidate" data-group-index="${index}" data-cid="${esc(channelId)}" data-mid="${messageId}" data-preview-state="idle">
          <div class="ax-channel-duplicate-candidate-head">
            <label class="ax-channel-duplicate-select">
              <input class="ax-channel-duplicate-select-input" type="checkbox" value="${esc(key)}" aria-label="Select ${esc(videoTitle)} from ${esc(channelTitle)}, message ${messageId}, to delete" onchange="channelDuplicateSelectionChanged(this)">
              <span>Select to delete</span>
            </label>
            <div class="ax-channel-duplicate-facts">
              <b class="ax-channel-duplicate-facts-title" title="${esc(videoTitle)}">${esc(videoTitle)}</b>
              <div class="ax-channel-duplicate-facts-identity">
                <span class="ax-channel-duplicate-facts-channel">${esc(channelTitle)}</span>
                <span class="ax-channel-duplicate-facts-message">Message ID <b>${messageId}</b></span>
              </div>
              <div class="ax-channel-duplicate-facts-properties" aria-label="Video properties">
                <span class="ax-channel-duplicate-facts-property"><small>Resolution</small><b>${esc(resolution)}</b></span>
                <span class="ax-channel-duplicate-facts-property"><small>File size</small><b>${esc(size||'Unknown')}</b></span>
                <span class="ax-channel-duplicate-facts-property"><small>Duration</small><b>${esc(durationText)}</b></span>
              </div>
            </div>
          </div>
          <div class="ax-channel-duplicate-snapshots" aria-label="Four cached snapshots">${frames}</div>
        </article>`;
      }).join('')}
      </div>
    </section>`;
  }).join('');
  channelDuplicateUpdateSelectionUI();
  channelDuplicateWatchSnapshots(results);
}
function channelDuplicateMarkSnapshotsUnavailable(card,message='Snapshot not cached'){
  card.querySelectorAll('.ax-channel-duplicate-snapshot').forEach(slot=>{
    slot.classList.add('is-missing');
    const state=slot.querySelector('.ax-channel-duplicate-snapshot-state');if(state)state.textContent=message;
  });
}
async function channelDuplicateLoadSnapshots(card,generation){
  if(!card?.isConnected||card.dataset.previewState==='loading'||card.dataset.previewState==='loaded')return;
  const channelId=String(card.dataset.cid||''),messageId=Number(card.dataset.mid||0);
  if(!channelId||!messageId){channelDuplicateMarkSnapshotsUnavailable(card);return;}
  card.dataset.previewState='loading';
  let manifest=null;
  try{manifest=await _snapshotFrameCache.getManifest(channelId,messageId);}
  catch{if(generation===_channelDupSnapshotGeneration&&card.isConnected){card.dataset.previewState='unavailable';channelDuplicateMarkSnapshotsUnavailable(card,'Preview unavailable');}return;}
  if(generation!==_channelDupSnapshotGeneration||!card.isConnected)return;
  if(!manifest?.frames_ready){card.dataset.previewState='unavailable';channelDuplicateMarkSnapshotsUnavailable(card,'No cached snapshot');return;}
  const slots=[...card.querySelectorAll('.ax-channel-duplicate-snapshot')];
  const revision=String(manifest.revision||manifest.updated_at||manifest.version||'');
  slots.forEach(slot=>{
    if(generation!==_channelDupSnapshotGeneration||!card.isConnected)return;
    const frameIndex=Number(slot.dataset.frameIndex);
    const img=slot.querySelector('img'),state=slot.querySelector('.ax-channel-duplicate-snapshot-state');
    if(!img)return;
    img.onload=()=>slot.classList.add('is-ready');
    img.onerror=()=>{slot.classList.add('is-missing');img.remove();if(state)state.textContent='Frame unavailable';};
    const query=revision?`?rev=${encodeURIComponent(revision)}`:'';
    img.src=`/api/play/preview-frame/${encodeURIComponent(channelId)}/${encodeURIComponent(messageId)}/${frameIndex}${query}`;
  });
  if(generation===_channelDupSnapshotGeneration&&card.isConnected)card.dataset.previewState='loaded';
}
function channelDuplicateWatchSnapshots(results){
  _channelDupSnapshotObserver?.disconnect();_channelDupSnapshotObserver=null;
  const generation=++_channelDupSnapshotGeneration,cards=[...results.querySelectorAll('.ax-channel-duplicate-candidate')];
  if(typeof IntersectionObserver!=='function'){cards.forEach(card=>void channelDuplicateLoadSnapshots(card,generation));return;}
  _channelDupSnapshotObserver=new IntersectionObserver(entries=>{
    entries.forEach(entry=>{if(!entry.isIntersecting)return;_channelDupSnapshotObserver?.unobserve(entry.target);void channelDuplicateLoadSnapshots(entry.target,generation);});
  },{root:results,rootMargin:'200px 0px',threshold:0.01});
  cards.forEach(card=>_channelDupSnapshotObserver.observe(card));
}
async function scanChannelDuplicateAudit(){
  if(_channelDupBusy||_channelDupDeleting)return;
  const select=$('ops-channel-maintenance-channel'),channelId=String(select?.value||'').trim();if(!channelId){toast('Choose a Telegram channel to scan first.');return;}
  const allIndexed=channelId==='__all_indexed__';
  const dlg=$('channel-duplicate-audit');if(dlg){dlg.classList.add('open');dlg.setAttribute('aria-hidden','false');}
  const channelLabel=String(select?.selectedOptions?.[0]?.textContent||'selected channel').replace(/\s+\([^)]*\)\s*$/,'').trim();
  const dialogTitle=$('channel-duplicate-audit-title'),context=$('channel-duplicate-audit-context');
  if(dialogTitle)dialogTitle.textContent=allIndexed?'Duplicate videos across channels':`Duplicate videos in ${channelLabel}`;
  if(context)context.textContent=allIndexed?'Cross-channel matches · select copies to delete after review':'Verified matches · select unwanted copies to delete';
  const intro=$('channel-duplicate-audit-intro');if(intro)intro.textContent=allIndexed
    ?'Only the same saved Telegram document ID or OSHASH plus file size counts as a duplicate. Similar filenames are not proof. Select the copies you want removed; each will be checked against an unselected matching copy before deletion. Missing fingerprints are not fetched automatically.'
    :'Only exact Telegram document IDs or matching OSHASH plus file size count as duplicates. Select unwanted copies and leave one directly matching copy in each group. Archive X verifies each selected copy before deletion.';
  const status=$('channel-duplicate-audit-status'),results=$('channel-duplicate-audit-results'),scan=$('ops-channel-maintenance-scan'),rescan=$('channel-duplicate-audit-rescan');
  _channelDupSnapshotObserver?.disconnect();_channelDupSnapshotObserver=null;_channelDupSnapshotGeneration++;
  _channelDupBusy=true;if(scan)scan.disabled=true;if(rescan)rescan.disabled=true;if(status)status.textContent=allIndexed?'Reading saved identities across indexed videos. No messages are changed during the scan.':'Scanning this channel’s indexed videos. Un-hashed candidates may be fingerprinted as part of this explicit scan; no messages are deleted during a scan.';if(results)results.innerHTML=`<div class="pan muted">${allIndexed?'Reading saved video identities across channels…':'Reading the saved channel inventory and verifying duplicate content identities against Telegram…'}</div>`;channelDuplicateUpdateSelectionUI();
  try{
    const endpoint=allIndexed?'/api/channel-maintenance/duplicates/all':`/api/channel-maintenance/duplicates?channel_id=${encodeURIComponent(channelId)}`;
    const data=await api(endpoint,{cache:'no-store'});
    if(!data||data.ok!==true)throw new Error(data?.detail||(allIndexed?'The cross-channel duplicate review failed. No messages were changed.':'The channel duplicate scan failed. No messages were changed.'));
    _channelDupData=data;renderChannelDuplicateAudit(data);
  }catch(e){if(status)status.textContent=e?.message||'The channel duplicate scan failed. No messages were changed.';if(results)results.innerHTML='';}
  finally{_channelDupBusy=false;if(scan)scan.disabled=false;if(rescan)rescan.disabled=false;channelDuplicateUpdateSelectionUI();}
}
async function deleteSelectedChannelDuplicates(){
  if(_channelDupDeleting||_channelDupBusy||!_channelDupData)return;
  const selected=channelDuplicateSelectedTargets(),selectedKeys=new Set(selected.map(item=>item.key));
  if(!selected.length){toast('Select at least one video to delete.');return;}
  if(selected.length>CHANNEL_DUP_DELETE_BATCH_LIMIT){toast(`Delete no more than ${CHANNEL_DUP_DELETE_BATCH_LIMIT} videos per batch.`);return;}
  const plan=selected.map(item=>({...item,keeper:channelDuplicateFindKeeper(item.group,item.row,selectedKeys)}));
  if(plan.some(item=>!item.keeper)){channelDuplicateUpdateSelectionUI();toast('Leave one directly matching copy unselected in each duplicate group.');return;}
  const channelCount=new Set(plan.map(item=>String(item.row.channel_id||''))).size;
  const list=plan.map(item=>{
    const row=item.row,cid=String(row.channel_id||''),mid=Number(row.message_id||0),title=String(row.title||row.filename||`Video ${mid}`);
    const channel=channelDisplayName(cid,row.channel_title),size=channelDuplicateFormatSize(row.size),resolution=`${Number(row.width||0)}×${Number(row.height||0)}`;
    return `• ${title} — ${channel}, message ${mid}, ${resolution}, ${size}`;
  }).join('\n');
  const ok=await axConfirm(`Permanently delete ${plan.length} selected video${plan.length===1?'':'s'} from ${channelCount} Telegram channel${channelCount===1?'':'s'} and remove them from the Archive X library?\n\n${list}\n\nEach video will be checked against an unselected copy with a directly matching Telegram document ID or saved OSHASH + file size before it is deleted. A linked thumbnail is deleted only if it is verified as an unshared image for that video; missing or unsafe thumbnails are left alone. A batch can stop partway if a live check fails. This cannot be undone.`,{title:`Delete ${plan.length} selected video${plan.length===1?'':'s'}?`,okLabel:`Delete ${plan.length} video${plan.length===1?'':'s'}`,confirmLabel:`Delete ${plan.length} video${plan.length===1?'':'s'}`,danger:true});
  if(!ok)return;
  _channelDupDeleting=true;
  const status=$('channel-duplicate-audit-status'),rescan=$('channel-duplicate-audit-rescan');
  if(rescan)rescan.disabled=true;
  channelDuplicateUpdateSelectionUI();
  const deleted=[],warnings=[];
  let attempted=false;
  let failedItem=null,failure='';
  for(let index=0;index<plan.length;index++){
    const item=plan[index],row=item.row,keeper=item.keeper;
    const channelId=Number(row.channel_id||_channelDupData.channel_id||0),messageId=Number(row.message_id||0);
    const keeperChannelId=Number(keeper.channel_id||_channelDupData.channel_id||0),keeperMessageId=Number(keeper.message_id||0);
    const channelTitle=channelDisplayName(channelId,row.channel_title);
    if(status)status.textContent=`Deleting ${index+1} of ${plan.length}: ${channelTitle} · message ${messageId}…`;
    const hint=$('channel-duplicate-selection-hint');if(hint)hint.textContent=`Deleting ${index+1} of ${plan.length}. Do not close this panel until the batch finishes.`;
    try{
      if(!channelId||!keeperChannelId||messageId<=0||keeperMessageId<=0)throw new Error('A selected channel or message ID is invalid.');
      attempted=true;
      const result=await api('/api/channel-maintenance/delete-duplicate',{method:'POST',body:JSON.stringify({confirm_delete:true,channel_id:channelId,message_id:messageId,keep_channel_id:keeperChannelId,keep_message_id:keeperMessageId})});
      if(!result||result.ok!==true)throw new Error(result?.detail||'Telegram did not confirm the deletion.');
      deleted.push({channelId,messageId});
      if(result.thumbnail_deleted)warnings.push(`Thumbnail ${result.thumbnail_message_id} deleted`);
      if(Array.isArray(result.warnings))warnings.push(...result.warnings);
    }catch(error){failedItem={channelTitle,messageId};failure=String(error?.message||'The delete request failed.');break;}
  }
  _channelDupDeleting=false;
  if(attempted)await scanChannelDuplicateAudit();
  else channelDuplicateUpdateSelectionUI();
  if(failure){
    toast(`Deleted ${deleted.length} of ${plan.length}; stopped before ${failedItem?.channelTitle||'the next video'} · message ${failedItem?.messageId||''}. ${failure}`,10000);
    if(status)status.textContent=`Batch stopped after ${deleted.length} of ${plan.length} deletion${deleted.length===1?'':'s'}. ${failure}`;
  }else{
    toast(`Deleted ${deleted.length} selected video${deleted.length===1?'':'s'} across ${channelCount} channel${channelCount===1?'':'s'}.${warnings.length?` ${warnings.slice(0,3).join(' · ')}`:''}`,10000);
  }
  if(rescan)rescan.disabled=false;
}

function updatePerformerAuditGroup(index){
  const group=$(`perf-audit-group-${Number(index)}`);if(!group)return;
  const keep=group.querySelector('input[type="radio"]:checked')?.value;
  const merges=[...group.querySelectorAll('input.perf-audit-merge:checked')].filter(x=>x.value!==keep);
  const btn=group.querySelector('.perf-audit-merge-btn');if(btn)btn.disabled=!keep||!merges.length;
}
async function performerAuditMergeGroup(index){
  const group=$(`perf-audit-group-${Number(index)}`);const source=(_perfAuditData?.groups||[])[Number(index)];if(!group||!source)return;
  const keepInput=group.querySelector('input[type="radio"]:checked');const keepId=Number(keepInput?.value||0);
  const mergeIds=[...group.querySelectorAll('input.perf-audit-merge:checked')].map(x=>Number(x.value)).filter(id=>id>0&&id!==keepId);
  const keep=(source.performers||[]).find(p=>Number(p.id)===keepId);
  if(!keep||!mergeIds.length){toast('Choose one keeper and at least one different performer to merge.');return;}
  if(mergeIds.length>500){toast('Merge no more than 500 duplicate performers in one operation.');return;}
  const names=mergeIds.map(id=>(source.performers||[]).find(p=>Number(p.id)===id)?.name||`#${id}`);
  const preview=names.slice(0,8).map(name=>`• ${name}`).join('\n')+(names.length>8?`\n• …and ${names.length-8} more`:'');
  const ok=await axConfirm(`Merge ${mergeIds.length} selected record(s) into “${keep.name}”?\n\n${preview}\n\nThe keeper name will remain canonical. Video links, provider IDs, aliases and related metadata are consolidated. This is a permanent change and all selected merges run atomically.`,{title:'Confirm performer cleanup',okLabel:`Merge ${mergeIds.length}`,confirmLabel:`Merge ${mergeIds.length}`,danger:true});
  if(!ok)return;
  const status=$('perf-audit-status');if(status)status.textContent=`Merging ${mergeIds.length} selected performer record(s)…`;
  const btn=group.querySelector('.perf-audit-merge-btn');if(btn)btn.disabled=true;
  try{
    const result=await api('/api/performers/merge-batch',{method:'POST',body:JSON.stringify({keep_id:keepId,merge_ids:mergeIds})});
    if(!result||!result.ok)throw new Error(result?.detail||'The performer cleanup failed; no changes were committed.');
    await loadPerformers(_perfPage,_perfQuery);
    await openPerformerDuplicateAudit();
    toast(`Merged ${Number(result.merged_count||mergeIds.length)} performer record(s) into “${result.name||keep.name}”.`);
  }catch(e){if(status)status.textContent=e?.message||'Merge failed. No changes were committed.';updatePerformerAuditGroup(index);}
}

async function loadPerformers(page=0, queryOverride=null){
  const q=String(queryOverride||'').trim();
  const queryChanged=q!==_perfQuery;
  if(queryChanged)page=0;
  _perfQuery=q;
  _perfPage=Math.max(0,Number(page)||0);
  const seq=++_perfSeq, nav=++_perfNavSeq;
  closePerfDetail();
  // Opening the tab again paints the performers already in memory straight
  // away, then refreshes quietly; the list only re-renders if something changed.
  const hadCache=_perfAll.length>0&&!queryChanged;
  if(hadCache){
    const cachedPages=Math.max(1,Math.ceil(getFilteredPerformers().length/_PERF_PER_PAGE));
    _perfPage=Math.max(0,Math.min(_perfPage,cachedPages-1));
    renderPerformersPage();
    window.AXMotion?.replayGrid($('perf-list'));
  }
  const url='/api/performers'+(q?`?q=${encodeURIComponent(q)}&limit=5000`:'?limit=5000');
  // The performer catalogue is mutable after metadata/entity synchronization.
  // Do not let a previously cached empty catalogue hide newly indexed performers.
  let list=await api(url,{cache:'no-store'});
  if(!Array.isArray(list)){await sl(350);list=await api(url,{cache:'no-store'});}
  if(seq!==_perfSeq||nav!==_perfNavSeq)return;
  if(!Array.isArray(list)){const wrap=$('perf-list');if(wrap)wrap.innerHTML='<div class="muted" style="padding:18px">Performer API failed: '+esc(list?.detail||'unknown error')+'</div>';return;}
  if(hadCache&&JSON.stringify(list)===JSON.stringify(_perfAll))return;
  _perfAll=list;
  const pages=Math.max(1,Math.ceil(getFilteredPerformers().length/_PERF_PER_PAGE));
  _perfPage=Math.max(0,Math.min(_perfPage,pages-1));
  _perfQuietNext=hadCache;
  renderPerformersPage();
  if(!hadCache)window.AXMotion?.replayGrid($('perf-list'));
}
function normalizePerformerGender(value){
  const s=String(value||'').trim().toLowerCase();
  if(['male','m','man','men','masculine'].includes(s))return 'male';
  if(['female','f','woman','women','feminine'].includes(s))return 'female';
  return '';
}
function getFilteredPerformers(){
  const q=_perfQuery.toLowerCase();
  const items=_perfAll.filter(p=>{if(q&&!String(p.name||'').toLowerCase().includes(q))return false;if(_perfGenderFilter!=='all'&&normalizePerformerGender(p.gender)!==_perfGenderFilter)return false;return true;});
  if(_perfSort==='name_asc')items.sort((a,b)=>String(a.name||'').localeCompare(String(b.name||''),undefined,{sensitivity:'base',numeric:true})||Number(a.id)-Number(b.id));
  else if(_perfSort==='name_desc')items.sort((a,b)=>String(b.name||'').localeCompare(String(a.name||''),undefined,{sensitivity:'base',numeric:true})||Number(a.id)-Number(b.id));
  return items;
}
function renderPerfPagination(){
  const pg=$('perf-pg'); if(!pg)return;
  const count=getFilteredPerformers().length;
  const pages=Math.max(1,Math.ceil(count/_PERF_PER_PAGE));
  if(!count){pg.innerHTML='';pg.style.display='none';return;}
  pg.style.display='block';
  pg.innerHTML=renderAxPaginationBar({page:_perfPage,pages,changePage:'gPerfPage',inputId:'perf-page-input',goInputFn:'gotoPerfPageInput',pageSizeInputId:'perf-page-size',pageSize:_PERF_PER_PAGE,pageSizeFn:'setPerformerRowsPerPage',ariaLabel:'Performer directory pagination'});
}
function gPerfPage(p){
  const pages=Math.max(1,Math.ceil(getFilteredPerformers().length/_PERF_PER_PAGE));
  const previousPage=_perfPage;
  _perfPage=Math.max(0,Math.min(Number(p)||0,pages-1));
  renderPerformersPage();
  if(_perfPage!==previousPage)window.AXMotion?.replayGrid($('perf-list'));
  _navPush({view:'performers',page:_perfPage,q:_perfQuery||'',gender:_perfGenderFilter,sort:_perfSort});
  window.scrollTo(0,0);
}
function gotoPerfPageInput(){const input=$('perf-page-input');const raw=String(input?.value||'').trim();if(!raw)return;gPerfPage(Math.max(1,parseInt(raw,10)||1)-1);}
function setPerformerRowsPerPage(value){
  const size=_axNormalizePageSize(value);if(!size||size===_PERF_PER_PAGE)return;
  const firstItem=_perfPage*_PERF_PER_PAGE;
  _PERF_PER_PAGE=size;_perfPage=Math.floor(firstItem/size);_axSavePageSize('performers',size);
  renderPerformersPage();
  _navPush({view:'performers',page:_perfPage,q:_perfQuery||'',gender:_perfGenderFilter,sort:_perfSort});
}
function performerCardArtworkMarkup(p,index){
  const name=String(p?.name||'').trim();
  if(/^unknown performer$/i.test(name)){
    return '<span class="entity-fallback ax-unknown" aria-hidden="true"><img class="ax-unknown-art" src="/static/icons/ax-unknown.jpg" alt="" loading="eager" decoding="async"></span>';
  }
  const fallback='<span class="entity-fallback" aria-hidden="true"><span class="ax-ph-bg"></span><img class="entity-fallback-logo" src="/static/icons/ax-placeholder.png" alt="" loading="eager" decoding="async"></span>';
  const hasImage=!!String(p?.image||'').trim()||Number(p?.has_cached_image)>0;
  if(!hasImage)return fallback;
  const id=Number(p?.id);
  const revision=String(p?.card_image_revision||'').trim();
  const src=`/api/performers/${id}/image?v=${encodeURIComponent(p?.image||'')}${revision?`&r=${encodeURIComponent(revision)}`:''}`;
  return `${fallback}<img src="${src}" alt="${esc(name)}" loading="${index<8?'eager':'lazy'}" decoding="async" data-provider-src="${esc(safeHttpUrl(p?.image)||'')}" data-local-src="${src}" onload="entityImageLoaded(this)" onerror="performerImageFallback(this,'')">`;
}
function renderPerformersPage(){
  const wrap=$('perf-list');if(!wrap)return;
  wrap.style.display='grid';
  // A background refresh swaps cards without replaying the entrance animation.
  wrap.classList.toggle('rx-static',_perfQuietNext);_perfQuietNext=false;
  const filtered=getFilteredPerformers();
  const start=_perfPage*_PERF_PER_PAGE;
  const pageItems=filtered.slice(start,start+_PERF_PER_PAGE);
  if(!_perfAll.length){wrap.innerHTML='<div class="entity-empty-state"><div class="entity-empty-title">No performers available</div><div class="entity-empty-copy">Performers appear when prepared videos are published by Media Manager.</div><button class="btn sm" onclick="sv(\'admin\')">Open Operations</button></div>';}
  else if(!filtered.length){wrap.innerHTML=`<div class="entity-empty-state"><div class="entity-empty-title">No matching performers</div><div class="entity-empty-copy">Try a different search.</div></div>`;}
  else {
    _axPatchLiveMarkup(wrap,pageItems.map((p,pi)=>{
      const videoCount=Number(p.video_count||0);
      return `<div class="pcard performer-card" data-ax-live-key="perf:${Number(p.id)}" data-entity-id="${Number(p.id)}" onclick="openPerf(${Number(p.id)})" role="button" tabindex="0" aria-label="Open performer ${esc(p.name)}" onkeydown="if(event.key==='Enter'||event.key===' '){event.preventDefault();openPerf(${Number(p.id)})}">
        <div class="pimg">${performerCardArtworkMarkup(p,pi)}</div>
        <div class="p-overlay">
          <div class="pname" title="${esc(p.name)}">${esc(p.name)}</div>
          <div class="performer-tile-footer">
            <div class="performer-tile-stat" aria-label="${videoCount} related videos"><span>${videoCount}</span><span class="performer-tile-stat-label">${videoCount === 1 ? 'video' : 'videos'}</span></div>
            <button type="button" class="performer-open-action" onclick="event.stopPropagation();openEntityEditor('performer',${Number(p.id)})" title="Edit performer" aria-label="Edit ${esc(p.name)}"><svg class="ax-icon" aria-hidden="true"><use href="#ax-edit"></use></svg></button>
          </div>
        </div>
      </div>`;
    }).join(''));
  }
  renderPerfPagination();
  updatePerformerMergeUI();
  queuePerformerToolbarPinSync();
  // Performer artwork comes from the API/provider and is loaded directly by
  // the browser. Do not send it through the video-thumbnail queue.
}
function applyPerformerProfileSaveToDirectory(id,performer,{visible=false,forceImageRefresh=false}={}){
  const performerId=Number(id);
  const index=Array.isArray(_perfAll)?_perfAll.findIndex(row=>Number(row.id)===performerId):-1;
  if(index<0||!performer)return false;
  const previous=_perfAll[index];
  const nameChanged=String(previous.name||'')!==String(performer.name||'');
  const imageChanged=forceImageRefresh||String(previous.image||'')!==String(performer.image||'');
  const updated={
    ...previous,...performer,id:performerId,
    video_count:performer.video_count??previous.video_count,
    has_cached_image:performer.has_cached_image??previous.has_cached_image,
    card_image_revision:forceImageRefresh?`saved-${Date.now()}`:previous.card_image_revision
  };
  _perfAll[index]=updated;
  if(!visible)return true;
  const wrap=$('perf-list');
  const card=wrap?.querySelector(`.performer-card[data-entity-id="${performerId}"]`);
  if(!wrap||!card)return true;
  const expected=getFilteredPerformers().slice(_perfPage*_PERF_PER_PAGE,(_perfPage+1)*_PERF_PER_PAGE).map(row=>Number(row.id));
  const current=[...wrap.querySelectorAll('.performer-card[data-entity-id]')].map(row=>Number(row.dataset.entityId));
  if(expected.join(',')!==current.join(',')){
    _perfQuietNext=true;
    renderPerformersPage();
    return true;
  }
  card.setAttribute('aria-label',`Open performer ${String(updated.name||'')}`);
  const name=card.querySelector('.pname');
  if(name){name.textContent=String(updated.name||'');name.title=String(updated.name||'');}
  const fallbackChanged=/^unknown performer$/i.test(String(previous.name||''))!==/^unknown performer$/i.test(String(updated.name||''));
  if(imageChanged||fallbackChanged){
    const artwork=card.querySelector('.pimg');
    if(artwork){
      card.classList.add('ax2-quiet-refresh');
      artwork.innerHTML=performerCardArtworkMarkup(updated,Math.max(0,expected.indexOf(performerId)));
    }
  }else if(nameChanged){
    const image=card.querySelector('.pimg img:not(.entity-fallback-logo):not(.ax-unknown-art)');
    if(image){image.alt=String(updated.name||'');image.dataset.entityName=String(updated.name||'');}
  }
  return true;
}
function closePerfDetail(){
  // Exit the dedicated performer page before restoring the catalogue. The CSS
  // mode guard also prevents background list refreshes from exposing the grid
  // while a profile is open.
  $('vp')?.classList.remove('performer-detail-mode');
  $('perf-detail').classList.remove('open');
  $('perf-detail').innerHTML='';
  $('perf-list').style.display='grid';
  const pg=$('perf-pg'); if(pg) pg.style.display='flex';
  _entityDetailLoadSeq++;
  _perfCurId=null;
  setCurrentEntityNavMapping('',null,false);
  _endEntityMapping();
}
const _genericTagKeys = new Set([
  'adult','all sex','full sex','sex','sexy','porn','porn video','xxx','video','movie','scene','official','uncensored',
  'hd','full hd','uhd','sd','4k','8k','h264','h265','hevc','x264','x265','mp4','mkv','avi','web dl','webdl',
  '60fps','50fps','30fps','25fps','24fps','new','free','online','240p','360p','480p','540p','576p','720p','1080p','1440p','2160p','4320p'
].map(x=>x.replace(/&/g,' and ').replace(/[^\p{L}\p{N}]+/gu,' ').trim()));
function _smartTagKey(value){return String(value||'').normalize('NFKC').toLocaleLowerCase().replace(/&/g,' and ').replace(/[_]+/g,' ').replace(/[^\p{L}\p{N}]+/gu,' ').trim();}
function _smartTagIsGeneric(value){const key=_smartTagKey(value);return !key||_genericTagKeys.has(key)||/^(240|360|480|540|576|720|1080|1440|2160|4320)\s?p$/.test(key)||/^(h\.?264|h\.?265|hevc|x26[45])(?:\s+(encode|rip))?$/.test(key);}
async function loadSmartTagTaxonomy(force=false,options={}){
  const now=Date.now();
  if(!force&&_smartTagTaxonomy&&now-_smartTagTaxonomyLoadedAt<60000)return _smartTagTaxonomy;
  if(_smartTagTaxonomyPromise&&!force)return _smartTagTaxonomyPromise;
  const seq=++_smartTagTaxonomySeq;
  const promise=(async()=>{
    const r=await api('/api/tag-taxonomy'+(force?'?refresh=1':''),{cache:'no-store'});
    if(seq!==_smartTagTaxonomySeq||!r||r._ok===false||!r.tag_map)return _smartTagTaxonomy;
    _smartTagTaxonomy=r;_smartTagTaxonomyLoadedAt=Date.now();
    if(options.rerender!==false){
      _refreshSmartTagLabelsInDOM();
      if(_dv)_renderSmartDetailTags(_dv,{preserveState:true});
    }
    return _smartTagTaxonomy;
  })();
  _smartTagTaxonomyPromise=promise;
  try{return await promise;}finally{if(_smartTagTaxonomyPromise===promise)_smartTagTaxonomyPromise=null;}
}
function _smartTagPresentation(values){
  const map=_smartTagTaxonomy?.tag_map||{},seen=new Map();
  (Array.isArray(values)?values:[]).forEach(raw=>{
    const original=String(raw||'').trim(),key=_smartTagKey(original);if(!key||_smartTagIsGeneric(original))return;
    const entry=map[key];if(entry&&entry.display===false)return;
    const item={key,name:String(entry?.name||original),rank_score:Number(entry?.rank_score??0.25),video_count:Number(entry?.video_count||0),categories:Array.isArray(entry?.categories)?entry.categories:[]};
    const prior=seen.get(key);if(!prior||item.rank_score>prior.rank_score)seen.set(key,item);
  });
  const tags=[...seen.values()].sort((a,b)=>(b.rank_score-a.rank_score)||(a.video_count-b.video_count)||a.name.localeCompare(b.name));
  const categories=[];
  tags.forEach(t=>(t.categories||[]).forEach(c=>{if(c&&!categories.includes(c))categories.push(c);}));
  return {tags:tags.map(t=>t.name),categories,ranked:tags};
}
function _smartTagMarkup(values,options={}){
  const p=_smartTagPresentation(values),maxTags=Math.max(0,Number(options.maxTags??3)),maxCategories=Math.max(0,Number(options.maxCategories??2));
  const tags=p.tags.slice(0,maxTags),cats=p.categories.slice(0,maxCategories);
  let html=tags.map(t=>`<span class="video-tag-chip" title="${esc(t)}" onclick="event.stopPropagation();openTagByName(${jsa(t)})">${options.hash===false?'':'#'}${esc(t)}</span>`).join('');
  html+=cats.map(c=>`<span class="video-category-chip" title="Derived category from specific scene tags">${esc(c)}</span>`).join('');
  const hidden=Math.max(0,p.tags.length-tags.length)+Math.max(0,p.categories.length-cats.length);
  if(hidden&&options.showMore!==false)html+=`<span class="video-more-chip video-smart-more" title="${Math.max(0,p.tags.length-tags.length)} more specific tag(s), ${Math.max(0,p.categories.length-cats.length)} more category/categories">+${hidden}</span>`;
  if(!html&&options.emptyText!==false)html=`<span class="muted video-no-specific-tags">${esc(options.emptyText||'No specific tags')}</span>`;
  return html;
}
function _refreshSmartTagLabelsInDOM(){
  document.querySelectorAll('.vc[data-cid][data-mid] .video-card-tags').forEach(box=>{
    const card=box.closest('.vc[data-cid][data-mid]');if(!card)return;
    const video=findVideo(card.dataset.mid,card.dataset.cid);if(!video)return;
    if(card.closest('#vg')||card.closest('.entity-video-grid')||card.closest('#perf-video-grid')||card.closest('#site-video-grid')){
      // Library, performer and studio cards now share the same metadata layout;
      // keep source studio/channel text in its dedicated row, not duplicated in tags.
      box.innerHTML=_smartTagMarkup(video.tags||[],{maxTags:3,maxCategories:2,hash:true,emptyText:'No specific tags'});
    }else{
      const studio=String(video.studio||video.site_name||'').trim();
      const tags=_smartTagMarkup(video.tags||[],{maxTags:2,maxCategories:1,hash:false,emptyText:false});
      box.innerHTML=(studio?`<span class="video-studio-chip" title="${esc(studio)}">${esc(studio)}</span>`:'')+(tags||(!studio?'<span class="muted" style="font-size:11px">No specific tags</span>':''));
    }
  });
}
function _renderSmartDetailTags(v,options={}){
  const tagHead=$('dtag-head'),tagBox=$('dtags');if(!tagHead||!tagBox)return;
  const preserveExpanded=!!options.preserveState&&tagBox.dataset.expanded==='1';
  const p=_smartTagPresentation(Array.isArray(v?.tags)?v.tags:[]);
  tagHead.textContent=`Scene tags${p.tags.length?` (${p.tags.length})`:''}${p.categories.length?` · ${p.categories.length} categor${p.categories.length===1?'y':'ies'}`:''}`;
  tagHead.title='Specific tags are ranked by rarity and category usefulness. Generic tags and tags present in 90% or more of videos are hidden; categories are derived from the remaining tags.';
  tagHead.classList.toggle('expanded',preserveExpanded);tagBox.dataset.expanded=preserveExpanded?'1':'0';tagBox.classList.toggle('tags-collapsed',!preserveExpanded);tagBox.classList.toggle('tags-expanded',preserveExpanded);
  const tags=p.tags.map(t=>`<span class="tag ax-specific-tag" title="${esc(t)}" onclick="closeDBtn(true);openTagByName(${jsa(t)})">#${esc(t)}</span>`).join('');
  const categories=p.categories.map(c=>`<span class="tag ax-derived-category-tag" title="Derived category from specific scene tags">${esc(c)}</span>`).join('');
  tagBox.innerHTML=(tags+categories)||'<span class="muted">No specific tags after common and generic tags are filtered.</span>';
}

function perfCard(v,showVideoActions=false,actionPosition='bottom'){
  const k=vkey(v._cid,v.id),duration=Math.max(0,Number(v.duration)||0);
  const thumb=thumbUrl(v);
  const picked=_mapMode&&_mapSel.has(mappingKey(v._cid,v.id));
  const isPlaying=String(v._cid)===String(_aChan)&&String(v.id)===String(_aMsg);
  const nm=String(v.api_title||v.title||v.name||('video '+v.id));
  const actions=showVideoActions?`<div class="video-card-actions${actionPosition==='top'?' video-card-actions-top':''}"><button class="video-card-more-trigger btn xs" type="button" onclick="toggleVideoCardActionMenu(event,${jsa(v._cid)},${Number(v.id)},${jsa(nm)})" title="Video actions" aria-label="Video actions for ${esc(nm)}" aria-haspopup="menu" aria-expanded="false">⋯</button></div>`:'';
  const topActions=showVideoActions&&actionPosition==='top'?actions:'';
  const bottomActions=showVideoActions&&actionPosition!=='top'?actions:'';
  const selected=picked?' map-picked':'';
  const playing=isPlaying?' playing':'';
  return `<div class="vc ax2-library-card${selected}${playing}" tabindex="0" data-ax-keyboard-activate="true" aria-label="${esc(nm)}" data-k="${esc(k)}" data-cid="${esc(v._cid)}" data-mid="${Number(v.id)}" data-duration="${duration}"><div class="vt"><input type="checkbox" class="map-select" aria-label="Select video"${picked?' checked':''} style="display:${_mapMode?'block':'none'};position:absolute;top:7px;left:7px;z-index:5;width:17px;height:17px" onclick="event.stopPropagation();toggleMapSelection(this,${jsa(v._cid)},${Number(v.id)})"><div class="vc-preview-deck"><img class="vc-thumb-fallback" data-src="${thumb}" alt="" decoding="async"><canvas class="vc-preview-canvas preview-a" aria-hidden="true"></canvas><canvas class="vc-preview-canvas preview-b" aria-hidden="true"></canvas></div><span class="ax-video-hover-play" aria-hidden="true">${axIcon('play')}</span><div class="vbadges">${cardBadgesHTML(v,{showStudio:false})}</div><span class="video-duration-overlay">${duration>0?esc(fdur(duration)):''}</span><button class="fav-btn${v._fav?' on':''}" onclick="event.stopPropagation();togFav(${Number(v.id)},${jsa(v._cid)},this)" aria-label="Favourite" title="Favourite" aria-pressed="${v._fav?'true':'false'}">${axIcon('star')}</button></div><div class="vi">${cardInfoHTML(v,{library:true,titleActions:topActions,includeStudioInChannel:false})}${bottomActions}</div></div>`;
}
async function openPerformerByName(name){
  const wanted=String(name||'').trim();
  if(!wanted)return;
  const nav=++_perfNavSeq;
  const list=await api('/api/performers?q='+encodeURIComponent(wanted));
  if(nav!==_perfNavSeq)return;
  // Only navigate when the local catalogue contains an exact name match. A
  // fuzzy first-result fallback can open the wrong performer's profile.
  const norm=value=>String(value||'').trim().toLocaleLowerCase();
  const p=Array.isArray(list)?list.find(x=>norm(x.name)===norm(wanted)):null;
  if(p) await openPerf(p.id,nav);
  else setSt('Performer “'+wanted+'” is not available as a separate catalogue entry yet.');
}
async function openStudioByName(name){
  const wanted=String(name||'').trim();
  if(!wanted)return;
  const nav=++_siteNavSeq;
  try{
    const list=await entityLookup('studio');
    if(nav!==_siteNavSeq)return;
    const norm=value=>String(value||'').trim().toLocaleLowerCase();
    const studio=(Array.isArray(list)?list:[]).find(x=>norm(x.name)===norm(wanted));
    if(studio)await openSite(Number(studio.id),nav);
    else setSt(`Studio “${wanted}” is not available as a separate catalogue entry yet.`);
  }catch(e){setSt('Could not load the related studio from the local catalogue.');}
}
async function tagBack(){_tagVideos=[];if(history.length>1)history.back();else sv('library');}
async function openTagByName(name){
  const list=await api('/api/tags?q='+encodeURIComponent(name));
  const t=Array.isArray(list)?list.find(x=>(x.name||'').toLowerCase()===String(name).toLowerCase())||list[0]:null;
  if(!t){setSt('Tag not found in local catalogue.');return;}
  const r=await api(`/api/tags/${t.id}/videos`);
  if(!r||!r.tag){setSt('Tag page failed.');return;}
  const videos=r.videos||[]; _tagVideos=videos;
  showView('tag');
  $('tag-detail').innerHTML=`<div class="entity-detail-backbar ax2-entity-backbar"><button type="button" class="entity-detail-back" onclick="tagBack()" aria-label="Go back" title="Go back"><svg class="ax-icon" aria-hidden="true"><use href="#ax-arrow-left"></use></svg></button><span class="universal-back-label">Tags</span></div><section class="ax2-tag-hero"><div class="ax2-entity-eyebrow">TAG</div><div class="ax2-tag-heading"><h1 class="ax2-entity-title">#${esc(r.tag.name)}</h1><span class="ax2-entity-pill">${videos.length.toLocaleString()} video${videos.length===1?'':'s'}</span></div></section><div id="tag-video-grid" class="ax2-entity-video-grid">${videos.length?videos.map(perfCard).join(''):'<div class="ax2-empty-state"><span class="ax2-empty-icon">'+axIcon('tag')+'</span><strong>No videos linked to this tag</strong><p>Try another tag from the video metadata.</p></div>'}</div>`;
  hydrateThumbs($('tag-detail'));
  bindCardPreviews($('tag-detail'));
  window.scrollTo(0,0);
  _navPush({view:'tag',name:r.tag.name});
}

function entityTabButton(tab,label,count){
  return `<button type="button" class="entity-detail-tab${tab==='videos'?' active':''}" data-entity-tab="${tab}" onclick="switchEntityTab('${tab}')">${label}${count!==undefined?`<span class="tab-count">${Number(count).toLocaleString()}</span>`:''}</button>`;
}
let _entityLookupCache={performers:null,studios:null};

async function entityLookup(kind){
  if(_entityLookupCache[kind])return _entityLookupCache[kind];
  const endpoint=kind==='performer'?'/api/performers?limit=5000':'/api/studios?limit=5000';
  const r=await api(endpoint);
  const list=Array.isArray(r)?r:[];
  _entityLookupCache[kind]=list;
  return list;
}
function activeEntityDetailRoot(){
  const perf=$('perf-detail'),site=$('site-detail');
  if(perf&&perf.classList.contains('open'))return perf;
  if(site&&site.classList.contains('open'))return site;
  return null;
}
function entityTabActivate(tab){
  document.querySelectorAll('#perf-detail .entity-detail-tab,#site-detail .entity-detail-tab').forEach(b=>b.classList.toggle('active',b.dataset.entityTab===tab));
  const root=activeEntityDetailRoot();
  if(!root)return;
  root.querySelectorAll('.entity-tab-panel').forEach(p=>p.classList.toggle('active',p.dataset.tabPanel===tab));
}
function isEntityDetailTab(tab){
  return ['videos','studios','performers','details','similar','suggestions'].includes(String(tab||''));
}
async function switchEntityTab(tab){
  const root=activeEntityDetailRoot();
  if(!root)return;
  if(root.id==='perf-detail'&&String(tab)==='details')tab='videos';
  if(!isEntityDetailTab(tab))return;
  entityTabActivate(tab);
  // The selected entity tab is part of navigation state, but it must not
  // create a new history entry. This makes Back return to the exact parent
  // entity/tab instead of reopening the destination's default Videos tab.
  const st=history.state;
  if(st && (st.view==='performer'||st.view==='site')){
    const next={...st,tab:String(tab)};
    if(JSON.stringify(next)!==JSON.stringify(st))history.replaceState(next,'');
  }
  if(root.id==='perf-detail') await renderPerfTab(tab);
  else await renderSiteTab(tab);
}
function entityCountMap(names){
  const m=new Map();
  (names||[]).forEach(n=>{const k=String(n||'').trim();if(k)m.set(k,(m.get(k)||0)+1);});
  return m;
}
function relatedEntityCard(item,kind,relation=null,hideEdit=false){
  const id=Number(item.id);
  const count=Number(item.count ?? item.video_count ?? 0);
  const relationAttr=relation&&relation.performerId?` onclick="openSite(${id},null,${Number(relation.performerId)})"`:relation&&relation.studioId?` onclick="openPerf(${id},null,${Number(relation.studioId)})"`:kind==='studio'?` onclick="openSite(${id})"`:` onclick="openPerf(${id})"`;
  if(kind==='studio'){
    const name=String(item.name||'Unknown');
    const letter=esc(name.trim().charAt(0).toUpperCase()||'?');
    const image=studioImageApiUrl(item);
    const providerImage=safeHttpUrl(item.image)||'';
    const editAction=hideEdit?'':`<button type="button" class="entity-related-edit-icon entity-related-edit-icon-studio" onclick="event.stopPropagation();openEntityEditor('studio',${id})" title="Edit studio" aria-label="Edit ${esc(name)}"><svg class="ax-icon" aria-hidden="true"><use href="#ax-edit"></use></svg></button>`;
    return `<div class="entity-studio-card related-studio-card" data-entity-id="${id}" role="button" tabindex="0" aria-label="Open studio ${esc(name)}"${relationAttr} onkeydown="if(event.target===this&&(event.key==='Enter'||event.key===' ')){event.preventDefault();this.click()}">
      <div class="entity-studio-art related-studio-art" style="--entity-bg:url('${esc(image)}')">
        <img src="${esc(image)}" data-provider-src="${esc(providerImage)}" data-local-src="${esc(image)}" alt="" loading="lazy" decoding="async" onload="entityImageLoaded(this)" onerror="studioImageFallback(this,${jsa(letter)})">
      </div>
      <div class="entity-studio-info related-studio-info">
        <div class="entity-studio-name" title="${esc(name)}">${esc(name)}</div>
        <div class="related-studio-footer"><span class="entity-studio-count related-studio-count">${count.toLocaleString()} video${count!==1?'s':''}</span>${editAction}</div>
      </div>
    </div>`;
  }
  const name=String(item.name||'Unknown');
  const letter=esc(name.trim().charAt(0).toUpperCase()||'?');
  const image=`/api/performers/${id}/image?v=${encodeURIComponent(item.image||'')}`;
  const hasSimilarity=item.similarity_score!==undefined&&item.similarity_score!==null&&Number.isFinite(Number(item.similarity_score));
  const similarityPct=hasSimilarity?Math.max(0,Math.min(100,Math.round(Number(item.similarity_score)*100))):0;
  const matchedFields=Array.isArray(item.matched_fields)?item.matched_fields.map(x=>String(x||'').trim()).filter(Boolean):[];
  const matchedCount=Number(item.matched_detail_count||0);
  const comparedCount=Number(item.compared_detail_count||0);
  const coveragePct=Number.isFinite(Number(item.metadata_coverage))?Math.max(0,Math.min(100,Math.round(Number(item.metadata_coverage)*100))):0;
  const matchTip=hasSimilarity?`Matched attributes: ${matchedFields.join(', ')||'gender'}. Compared ${comparedCount} personal/physical attributes; metadata coverage ${coveragePct}%.`:'';
  const matchBadge=hasSimilarity?`<span class="related-performer-match-score" title="${esc(matchTip)}">${similarityPct}% match · ${matchedCount}/${comparedCount} traits</span>`:'';
  const editAction=hideEdit?'':`<button type="button" class="entity-related-edit-icon entity-related-edit-icon-performer" onclick="event.stopPropagation();openEntityEditor('performer',${id})" title="Edit performer" aria-label="Edit ${esc(name)}"><svg class="ax-icon" aria-hidden="true"><use href="#ax-edit"></use></svg></button>`;
  return `<div class="entity-performer-card related-performer-card" data-entity-id="${id}" role="button" tabindex="0" aria-label="Open performer ${esc(name)}"${relationAttr} onkeydown="if(event.target===this&&(event.key==='Enter'||event.key===' ')){event.preventDefault();this.click()}">
    <div class="entity-performer-art" style="--entity-bg:url('${esc(image)}')">
      <img src="${image}" alt="" loading="lazy" decoding="async" onload="entityImageLoaded(this)" onerror="entityImageFallback(this,${jsa(letter)})">
    </div>
    <div class="entity-performer-info related-performer-info">
      <div class="entity-performer-name" title="${esc(name)}">${esc(name)}</div>
      ${matchBadge?`<div class="related-performer-match-line">${matchBadge}</div>`:''}
      <div class="related-performer-footer">
        <span class="entity-performer-count related-performer-count">${count.toLocaleString()} video${count!==1?'s':''}</span>${editAction}
      </div>
    </div>
  </div>`;
}
function renderEntityVideosPanel(kind){
  const videos=kind==='performer'?_perfVideos:_siteVideos;
  const label=kind==='performer'?`Videos for ${esc((_perfVideos[0]?.performers||[]).find(x=>x)||'')}`:'';
  // V29.4.131: the multi-video editor (mapping mode) is reachable from the
  // page itself; it was only on a top-bar tab the new layout no longer shows.
  const bulkBtn=videos.length?`<button type="button" class="btn sm entity-bulk-btn${_mapMode?' on':''}" aria-pressed="${_mapMode?'true':'false'}" onclick="entityBulkEdit('${kind}')" title="Select several videos and change their performer, studio, title or metadata together">${axIcon('edit')} <span class="lbl">${_mapMode?`Selecting · ${_mapSel.size}`:'Edit multiple videos'}</span></button>`:'';
  return `<div class="entity-videos-head"><div class="rs">${kind==='performer'?'Videos linked to this performer':'Videos linked to this studio'}</div>${bulkBtn}</div>
    <div id="${kind==='performer'?'perf':'site'}-video-grid" class="entity-video-grid">${videos.length?videos.map(v=>perfCard(v,true,'top')).join(''):'<div class="entity-empty-tab">No linked videos in the local catalogue.</div>'}</div>`;
}
async function renderPerfDetails(){
  const p=window._currentPerfEntity||{};
  const meta=await loadEntityProviderMetadata('performer',Number(p.id));
  const bio=String(p.bio||'').trim();
  return `<div class="entity-info-grid">
    ${renderMergedEntityProfile(p,meta,'performer',(_perfVideos||[]).length)}
    ${bio?`<div class="entity-info-card" style="grid-column:1/-1"><h3>Biography</h3><div style="font-size:12px;color:#c0c0c0;line-height:1.65">${esc(bio)}</div></div>`:''}
  </div>`;
}
async function renderPerfTab(tab){
  const root=$('perf-detail');if(!root)return;
  const panel=root.querySelector(`.entity-tab-panel[data-tab-panel="${tab}"]`);
  if(!panel)return;
  const entityId=Number(_perfCurId),seq=_perfSeq,nav=_perfNavSeq,detailNav=_entityDetailLoadSeq;
  const isCurrent=()=>seq===_perfSeq&&nav===_perfNavSeq&&detailNav===_entityDetailLoadSeq&&Number(_perfCurId)===entityId&&root.classList.contains('open')&&panel.isConnected;
  if(tab==='videos'){
    panel.innerHTML=renderEntityVideosPanel('performer');hydrateThumbs(panel);bindCardPreviews(panel);return;
  }
  if(tab==='suggestions'){
    renderPerfSuggestedPanel(panel);return;
  }
  if(tab==='details'){const html=await renderPerfDetails();if(isCurrent())panel.innerHTML=html;return;}
  if(tab==='studios'){
    const counts=entityCountMap((_perfVideos||[]).flatMap(v=>v.studios||[]));
    const all=await entityLookup('studio');
    if(!isCurrent())return;
    const items=all.filter(x=>counts.has(String(x.name||''))).map(x=>({...x,count:counts.get(String(x.name||''))}));
    panel.innerHTML=items.length?`<div class="entity-studio-grid">${items.map(x=>relatedEntityCard(x,'studio',{performerId:_perfCurId},true)).join('')}</div>`:'<div class="entity-empty-tab">No linked studios were found for this performer.</div>';
    hydrateThumbs(panel);return;
  }
  if(tab==='similar'){
    const r=await api(`/api/performers/${entityId}/similar?limit=40`);
    if(!isCurrent())return;
    const items=Array.isArray(r?.items)?r.items:[];
    if(!items.length){
      const msg=r?.reason==='gender_required'?'Similar performers require a known gender on the current profile.':'No similar performers with matching personal and physical details were found.';
      panel.innerHTML=`<div class="entity-empty-tab">${esc(msg)}</div>`;
      return;
    }
    panel.innerHTML=`<div class="entity-related-grid">${items.map(x=>relatedEntityCard({...x,count:x.video_count},'performer',null,true)).join('')}</div>`;
  }
}
async function openPerf(id,navToken=null,relationStudioId=null,requestedTab=null){
  if(_performerMerge.active){await performerMergePick(id);return;}
  const seq=++_perfSeq;
  const nav=navToken??++_perfNavSeq;
  const detailNav=++_entityDetailLoadSeq;
  if(_perfCurId!==Number(id))_endEntityMapping();   // a different performer: new selection
  _perfCurId=Number(id); _siteCurId=null;
  $('site-detail')?.classList.remove('open');
  const relationQuery=Number(relationStudioId)>0?`?studio_id=${Number(relationStudioId)}`:'';
  // Hero + details + tabs + video-card skeleton in the real performer page layout, shown only if the
  // profile takes >140ms to load.
  const perfSkeleton=window.AXSkeleton?.later(()=>{
    if(seq!==_perfSeq||nav!==_perfNavSeq||detailNav!==_entityDetailLoadSeq)return;
    showView('performers');
    $('vp')?.classList.add('performer-detail-mode');
    $('perf-list').style.display='none';
    if($('perf-pg'))$('perf-pg').style.display='none';
    const d=$('perf-detail');d.classList.add('open');d.innerHTML=window.AXSkeleton.entityPage('performer');
  },140);
  const r=await api(`/api/performers/${Number(id)}/videos${relationQuery}`);
  perfSkeleton?.cancel();
  if(seq!==_perfSeq||nav!==_perfNavSeq||detailNav!==_entityDetailLoadSeq)return;
  if(!r||!r.performer){if(perfSkeleton?.shown)closePerfDetail();setSt('Performer API failed: '+(r.detail||'unknown error'));return;}
  showView('performers');
  // The performer profile is a separate page state, not a section appended
  // under the performer directory. Keep the shell but suppress directory UI.
  $('vp')?.classList.add('performer-detail-mode');
  $('perf-list').style.display='none';
  if($('perf-pg'))$('perf-pg').style.display='none';
  const p=r.performer, videos=Array.isArray(r.videos)?r.videos:[];
  _perfVideos=videos; _perfCurId=Number(p.id); window._currentPerfEntity=p; window._perfRelationStudioId=Number(relationStudioId)||0;
  const performerLocalImage=`/api/performers/${Number(p.id)}/image?v=${encodeURIComponent(p.image||'')}`;
  const performerProviderImage=safeHttpUrl(p.image)||'';
  const photo=`<img class="performer-profile-photo" data-entity-name="${esc(p.name||'')}" src="${esc(performerLocalImage)}" data-provider-src="${esc(performerProviderImage)}" data-local-src="${esc(performerLocalImage)}" onload="entityImageLoaded(this)" onerror="performerImageFallback(this,'')">`;
  detail=$('perf-detail'); detail.classList.add('open');
  const providerIdText=String(p.provider_ids||'');
  let initialProviderNames=[];
  try{const parsed=typeof p.provider_ids==='string'?JSON.parse(p.provider_ids||'{}'):p.provider_ids;if(parsed&&typeof parsed==='object')initialProviderNames=Object.entries(parsed).filter(([,v])=>v!==null&&v!==undefined&&String(v).trim()).map(([k])=>k);}catch(e){}
  const providerPills=initialProviderNames.map(name=>{const low=String(name).toLowerCase();const label=/stash/.test(low)?'StashDB':/tpdb|porn.?db|theporn/.test(low)?'ThePornDB':String(name);const cls=label==='StashDB'?'is-violet':label==='ThePornDB'?'is-accent':'';return `<span class="ax2-entity-pill ${cls}">${esc(label)}</span>`;}).join('');
  detail.innerHTML=`<div class="entity-detail-backbar ax2-entity-backbar"><button type="button" class="entity-detail-back" onclick="universalBack()" aria-label="Go back" title="Go back"><svg class="ax-icon" aria-hidden="true"><use href="#ax-arrow-left"></use></svg></button><span class="universal-back-label entity-back-label">Performers › ${esc(p.name)}</span></div>
    <section class="ax2-entity-hero ax2-performer-hero" aria-label="Performer profile">
      <div class="ax2-entity-media performer-profile-avatar is-adjustable" data-performer-id="${Number(p.id)}" role="button" tabindex="0" aria-haspopup="dialog" title="Adjust portrait framing" aria-label="Adjust portrait framing for ${esc(p.name)}" onclick="openPerformerPortraitAdjust(event,${Number(p.id)})" onkeydown="if(event.key==='Enter'||event.key===' '){event.preventDefault();openPerformerPortraitAdjust(event,${Number(p.id)})}">${photo}<span class="performer-portrait-hover-hint" aria-hidden="true">${axIcon('crop')} Adjust framing</span></div>
      <div class="ax2-entity-main">
        <div class="ax2-entity-eyebrow">PERFORMER</div>
        <h1 class="ax2-entity-title">${esc(p.name)}</h1>
        <div class="ax2-entity-pills">
          ${p.gender?`<span class="ax2-entity-pill">${esc(p.gender)}</span>`:''}
          <span class="ax2-entity-pill" data-performer-video-count>${videos.length.toLocaleString()} video${videos.length===1?'':'s'}</span>
          <span id="performer-overview-status" class="performer-overview-status performer-identity-link-status ax2-entity-pill ${p.provider_ids&&p.provider_ids!=='{}'?'is-linked':''}" title="Checking metadata"><i></i>${String(p.name||'').trim().toLowerCase()==='unknown performer'?'Local profile':(providerIdText&&providerIdText!=='{}'?'Metadata linked':'Local profile')}</span>
          <span id="performer-provider-pills" class="ax2-entity-provider-pills">${providerPills}</span>
        </div>
        <div id="performer-overview-bio-text" class="performer-overview-bio ax2-entity-bio${String(p.bio||'').trim()?'':' is-empty'}">${esc(String(p.bio||'').trim()||'Loading available biography…')}</div>
        <button type="button" class="ax2-bio-toggle" data-ax2-bio-toggle="performer-overview-bio-text" hidden>Read more</button>
        <div class="ax2-entity-actions">
          <button type="button" class="btn sm pri performer-icon-action" id="perf-rnd" onclick="perfRandomMPC()" title="Play a random video of this performer in MPC-HC" aria-label="Shuffle in MPC-HC">${axIcon('shuffle')}<span>Shuffle in MPC-HC</span></button>
          ${entityFavButton('performer',Number(p.id),!!p.favorite,false)}
          <button type="button" class="btn sm" onclick="openEntityEditor('performer',${Number(p.id)})" title="Edit performer details" aria-label="Edit performer details">${axIcon('edit')}<span>Edit profile</span></button>
        </div>
        <div class="performer-social-panel ax2-entity-social" aria-label="Social media links"><div id="performer-social-links" class="performer-social-links"><span class="performer-social-empty">Checking saved profile links…</span></div></div>
      </div>
    </section>
    <section class="ax2-entity-details-card" aria-label="Performer details"><div class="ax2-entity-details-head"><h2>Details</h2><span>From saved profile &amp; providers</span></div><div id="performer-overview-details" class="performer-overview-details">${renderPerformerOverviewDetails(p,{providers:[]},videos.length)}</div></section>
    <div class="entity-detail-tabs ax2-entity-tabs" role="tablist" aria-label="Performer details">
      ${entityTabButton('videos','Videos',videos.length)}
      ${entityTabButton('studios','Studios',new Set(videos.flatMap(v=>v.studios||[]).map(x=>String(x||'').trim()).filter(Boolean)).size)}
      ${entityTabButton('similar','Similar Performers')}
      ${entityTabButton('suggestions','Suggestions')}
    </div>
    <div id="perf-tab-videos" class="entity-tab-panel active" data-tab-panel="videos"></div>
    <div id="perf-tab-studios" class="entity-tab-panel" data-tab-panel="studios"></div>
    <div id="perf-tab-similar" class="entity-tab-panel" data-tab-panel="similar"></div>
    <div id="perf-tab-suggestions" class="entity-tab-panel" data-tab-panel="suggestions"></div>`;
  window.initPerformerPortraitCard?.(detail);
  setCurrentEntityNavMapping('performer',Number(p.id),true);
  const currentNav=history.state||{};
  const requestedPerfTab=String(requestedTab||'');
  const targetTab=isEntityDetailTab(requestedPerfTab)&&requestedPerfTab!=='details'?requestedPerfTab:'videos';
  renderPerfTab('videos');
  hydratePerformerOverview(p,videos.length,seq);
  hydrateThumbs(detail);
  const returnView=currentNav.view==='performers'?'performers':(currentNav.view||'library');
  const returnTab=(currentNav.view==='performer'&&isEntityDetailTab(currentNav.tab))?String(currentNav.tab):null;
  const returnPage=returnView==='performers'?(_perfPage||0):(_page||0);
  const returnQ=returnView==='performers'?(_perfQuery||''):'';
  const returnScroll=returnView==='performers'?Math.max(0,window.scrollY||window.pageYOffset||0):0;
  if(!_navApplying && currentNav.view==='performers'){
    history.replaceState({...currentNav,scroll:returnScroll,anchorId:Number(p.id)},'');
  }
  window.scrollTo(0,0);
  _navPush({view:'performer',id:Number(p.id),relationStudioId:Number(relationStudioId)||0,tab:targetTab,returnView,returnTab,returnPage,returnQ,returnScroll,returnAnchorId:Number(p.id)});
  entityTabActivate(targetTab);
  if(targetTab!=='videos') await renderPerfTab(targetTab);
  loadPerfSuggested(Number(p.id), seq, nav);
}

function renderPerfSuggestedPanel(panel){
  const list=Object.values(_perfSuggestedMap||{});
  const performerName=String(window._currentPerfEntity?.name||'this performer');
  if(!panel)return;
  panel.innerHTML=list.length?`<div class="entity-suggestion-toolbar"><div><b>Suggested Videos</b><div class="muted" style="font-size:11px;margin-top:3px">Does each video belong to ${esc(performerName)}? Confirming links this performer and hides other performer suggestions for the video. Studio links are separate; use Edit Video or Scene Match Review for full scene identification.</div></div><span class="ax-section-chip">${list.length} pending</span></div><div class="entity-suggestion-grid">${list.map(s=>`
    <div class="entity-suggestion-card" data-suggid="${Number(s.suggestion_id)}" onclick="openSuggestedPreview(${Number(s.suggestion_id)},${_perfCurId})">
      <div class="vt"><img data-src="/api/thumb/${encodeURIComponent(s.channel_id)}/${Number(s.message_id)}?r=f3" alt="" decoding="async"><div class="ph" style="display:none"><svg class="ax-icon" aria-hidden="true"><use href="#ax-play"></use></svg></div></div>
      <div class="sug-body"><div class="vn">${esc(s.title||s.filename||('video '+s.message_id))}</div>
      <div class="sug-confidence">${s.duration?fdur(s.duration):''}${s.duration&&s.size?' · ':''}${s.size?fmt(s.size):''}</div>
      <div class="sug-reason">Does this video belong to ${esc(performerName)}?</div>
      <div class="sug-actions"><button class="btn xs pri" onclick="event.stopPropagation();perfConfirmSuggested(${Number(s.suggestion_id)},${_perfCurId})">Yes, link performer</button><button class="btn xs" onclick="event.stopPropagation();perfRejectSuggested(${Number(s.suggestion_id)},${_perfCurId})">No, don’t link</button></div></div>
    </div>`).join('')}</div>`:'<div class="entity-empty-tab"><b>No pending suggestions</b><div style="margin-top:5px">Run Metadata &amp; Suggestion Scan from Operations to create new suggestions.</div></div>';
  hydrateThumbs(panel);
}
async function loadPerfSuggested(performerId, seq, nav){
  let list=[];
  try{const r=await api(`/api/performers/${performerId}/suggested`);list=Array.isArray(r)?r:[];}catch(e){list=[];}
  if(seq!==_perfSeq||nav!==_perfNavSeq||Number(_perfCurId)!==Number(performerId)||!$('perf-detail')?.classList.contains('open'))return;
  _perfSuggestedMap={};list.forEach(s=>{_perfSuggestedMap[String(s.suggestion_id)]=s;});
  const tab=document.querySelector('#perf-detail .entity-detail-tab[data-entity-tab="suggestions"]');
  if(tab)tab.innerHTML=`Suggestions${list.length?`<span class="tab-count">${list.length}</span>`:''}`;
  const panel=$('perf-tab-suggestions');
  if(panel&&panel.classList.contains('active'))renderPerfSuggestedPanel(panel);
}
let _entityEditKind='', _entityEditId=0, _entityEditOpenSeq=0, _entityEditImageData='', _entityEditProviderImage=null, _entityEditCandidates=[], _entityEditPendingProviderImage=null, _entityEditResetApi=false, _entityEditImageOffset=0, _entityEditImageHasMore=false;
// Keep the performer/studio image editor outside the app-wide zoom root so its
// fixed backdrop stays pinned to the real browser viewport at every UI scale.
function _portalEntityEditDialog(){
  const dialog=$('entity-edit');
  if(dialog && dialog.parentElement!==document.body)document.body.appendChild(dialog);
  return dialog;
}
async function openEntityEditor(kind,id){
  const entityId=Number(id||0),requestSeq=++_entityEditOpenSeq;
  _entityEditKind=String(kind||''); _entityEditId=entityId; _entityEditImageData=''; _entityEditProviderImage=null; _entityEditPendingProviderImage=null; _entityEditCandidates=[]; _entityEditResetApi=false; _entityEditImageOffset=0; _entityEditImageHasMore=false;
  const requestKind=_entityEditKind;
  const r=await api(`/api/${requestKind==='performer'?'performers':'studios'}/${entityId}/edit`);
  if(requestSeq!==_entityEditOpenSeq||_entityEditKind!==requestKind||_entityEditId!==entityId)return;
  if(!r||r._ok===false){toast('Could not load entity: '+(r?.detail||'error'));return;}
  $('entity-edit-kind').value=requestKind; $('entity-edit-id').value=String(entityId);
  $('entity-edit-title').textContent=requestKind==='performer'?'Profile picture':'Studio image';
  $('entity-edit-subtitle').textContent=r.name||'';
  $('entity-edit-save').textContent=requestKind==='performer'?'Save profile picture':'Save studio image';
  $('entity-edit-name').value=r.name||'';
  $('entity-edit-image-url').value=(r.image_source==='manual'&&/^https?:/i.test(r.image||''))?r.image:'';
  $('entity-edit-profile').value=r.profile||''; $('entity-edit-bio').value=r.bio||'';
  $('entity-edit-profile-wrap').style.display=requestKind==='performer'?'block':'none'; $('entity-edit-bio-wrap').style.display=requestKind==='performer'?'block':'none';
  $('entity-edit-api-block').style.display='block';
  $('entity-edit-image-file').value=''; $('entity-edit-api-candidates').innerHTML=''; $('entity-edit-api-status').textContent='Loading provider images…'; $('entity-edit-footer-status').textContent='Loading provider images…';
  $('entity-edit-details').open=false;
  const img=$('entity-edit-preview'); img.src=`/api/${requestKind==='performer'?'performers':'studios'}/${entityId}/image?v=${encodeURIComponent(r.image||'')}`; img.onerror=()=>{img.removeAttribute('src');};
  $('entity-edit-selection-state').textContent = r.image_locked ? 'Locked current choice' : 'Current provider image';
  const dialog=_portalEntityEditDialog();
  dialog.classList.add('open');
  // Opened editor immediately fetches candidates. No checkbox/find/use step is needed.
  setTimeout(()=>{if(requestSeq===_entityEditOpenSeq&&dialog.classList.contains('open'))void findEntityApiImages(false,requestSeq);},30);
}
function closeEntityEditor(){ _entityEditOpenSeq++; const dialog=$('entity-edit'); if(dialog){dialog.classList.remove('open');dialog.setAttribute('aria-hidden','true');} }
function previewEntityUpload(input){
  const f=input?.files?.[0]; if(!f)return;
  _entityEditResetApi=false;
  const max=8*1024*1024; if(f.size>max){toast('Image must be 8 MiB or smaller.');input.value='';return;}
  _entityEditProviderImage=null; _entityEditPendingProviderImage=null;
  $('entity-edit-image-url').value='';
  const reader=new FileReader(); reader.onload=()=>{_entityEditImageData=String(reader.result||''); $('entity-edit-preview').src=_entityEditImageData; $('entity-edit-selection-state').textContent='Local image selected — save to apply'; $('entity-edit-footer-status').textContent='Local image selected.';}; reader.readAsDataURL(f);
}
async function findEntityApiImages(loadMore=false,requestSeq=_entityEditOpenSeq){
  const dialog=$('entity-edit');
  if(!_entityEditId||requestSeq!==_entityEditOpenSeq||!dialog?.classList.contains('open'))return;
  const kind=_entityEditKind==='performer'?'performer':'studio';
  const entityId=_entityEditId;
  const pageSize=10;
  const offset=loadMore?_entityEditImageOffset:0;
  $('entity-edit-api-status').textContent=loadMore?'Loading more provider images…':'Loading provider images…';
  try{
    const base=kind==='performer'?`/api/performers/${entityId}/image-candidates`:`/api/studios/${entityId}/image-candidates`;
    const endpoint=`${base}?limit=${pageSize}&offset=${offset}`;
    const r=await api(endpoint);
    if(requestSeq!==_entityEditOpenSeq||_entityEditId!==entityId||_entityEditKind!==kind||!dialog.classList.contains('open'))return;
    if(!r||r._ok===false)throw new Error(r?.detail||'provider lookup failed');
    const incoming=Array.isArray(r.candidates)?r.candidates:[];
    if(!loadMore){ _entityEditCandidates=[]; }
    _entityEditCandidates=_entityEditCandidates.concat(incoming);
    _entityEditImageOffset=offset+pageSize;
    _entityEditImageHasMore=Boolean(r.has_more);
    renderEntityApiCandidates(kind);
    const providerCounts=_entityEditCandidates.reduce((m,c)=>{const p=String(c.provider||'provider').toUpperCase();m[p]=(m[p]||0)+1;return m;},{});
    const summary=Object.entries(providerCounts).map(([p,n])=>`${p}: ${n}`).join(' · ');
    $('entity-edit-api-status').textContent=_entityEditCandidates.length
      ? `${summary}${_entityEditImageHasMore?' · More images available':''}`
      : 'No provider images found.';
    $('entity-edit-footer-status').textContent=_entityEditCandidates.length
      ? 'Select an image, then Save profile picture.'
      : 'You can use a manual image URL or file.';
  }catch(e){
    if(requestSeq!==_entityEditOpenSeq||_entityEditId!==entityId||_entityEditKind!==kind||!dialog.classList.contains('open'))return;
    $('entity-edit-api-status').textContent='Provider image lookup failed: '+(e?.message||e);
    $('entity-edit-footer-status').textContent='You can still use a manual image URL or file.';
  }
}
function renderEntityApiCandidates(kind){
  const box=$('entity-edit-api-candidates');
  if(!_entityEditCandidates.length){
    box.innerHTML='<div class="muted" style="padding:8px 0">No provider images returned.</div>';
    return;
  }
  const groups=[['theporndb','ThePornDB'],['stashdb','STASHDB']];
  box.innerHTML=groups.map(([key,label])=>{
    const items=_entityEditCandidates.map((c,i)=>({c,i})).filter(x=>String(x.c.provider||'').toLowerCase()===key);
    if(!items.length)return '';
    return `<section class="entity-api-provider-group"><div class="entity-api-provider-title"><strong>${label}</strong><span>${items.length} loaded</span></div><div class="entity-api-provider-grid">${items.map(({c,i})=>{
      const wh=(c.width&&c.height)?`${c.width}×${c.height}`:'';
      const current=c.current?' current':'';
      return `<button type="button" class="entity-api-candidate${current}" data-index="${i}" onclick="useEntityApiImage(${i})" title="Select this image"><img src="${esc(c.url)}" alt="${esc(kind)} ${esc(label)} image ${i+1}" loading="lazy" onerror="this.closest('.entity-api-candidate')?.remove()"><span class="ec-meta">${esc(label)}${wh?' · '+esc(wh):''}</span></button>`;
    }).join('')}</div></section>`;
  }).join('')+( _entityEditImageHasMore ? '<button type="button" class="entity-api-load-more" onclick="findEntityApiImages(true)">Load more images</button>' : '' );
}
function useEntityApiImage(index){
  const c=_entityEditCandidates[Number(index)]; if(!c?.url)return;
  _entityEditProviderImage=c; _entityEditPendingProviderImage=null; _entityEditImageData=''; _entityEditResetApi=false;
  $('entity-edit-image-url').value=''; $('entity-edit-image-file').value='';
  $('entity-edit-preview').src=c.url;
  document.querySelectorAll('.entity-api-candidate').forEach((el,i)=>el.classList.toggle('selected',i===Number(index)));
  $('entity-edit-selection-state').textContent=`Selected ${String(c.provider||'provider').toUpperCase()} image — save to apply`;
  $('entity-edit-api-status').textContent='Image selected.';
  $('entity-edit-footer-status').textContent='Selected image will be saved when you click Save profile picture.';
}
function applyPerformerProfileSaveToOpenDetail(id,performer,forceImageRefresh=false){
  const performerId=Number(id),detail=$('perf-detail');
  if(!detail?.classList.contains('open')||Number(_perfCurId)!==performerId||!performer)return null;
  const previous=window._currentPerfEntity||{};
  const entity={...previous,...performer,id:performerId};
  const name=String(entity.name||'');
  const nameChanged=String(previous.name||'')!==name;
  const imageChanged=forceImageRefresh||String(previous.image||'')!==String(entity.image||'');
  window._currentPerfEntity=entity;
  const title=detail.querySelector('.ax2-entity-title');if(title)title.textContent=name;
  const backLabel=detail.querySelector('.entity-detail-backbar .universal-back-label');
  if(backLabel)backLabel.textContent=`Performers › ${name}`;
  const avatar=detail.querySelector('.performer-profile-avatar');
  if(avatar){avatar.title=`Adjust portrait framing for ${name}`;avatar.setAttribute('aria-label',`Adjust portrait framing for ${name}`);}
  const photo=detail.querySelector('.performer-profile-photo');
  if(photo){
    photo.dataset.entityName=name;
    const local=`/api/performers/${performerId}/image?v=${encodeURIComponent(entity.image||'')}`;
    const provider=safeHttpUrl(entity.image)||'';
    photo.dataset.providerSrc=provider;
    if(imageChanged){
      const nextSrc=forceImageRefresh?`${local}&r=saved-${Date.now()}`:local;
      photo.dataset.localSrc=nextSrc;
      photo.classList.add('ax2-quiet-refresh');
      photo.onerror=()=>performerImageFallback(photo,'');
      photo.src=nextSrc;
    }
  }
  const bio=String(entity.bio||'').trim(),bioEl=$('performer-overview-bio-text');
  if(bioEl){bioEl.textContent=bio||'Loading available biography…';bioEl.classList.toggle('is-empty',!bio);}
  return {entity,nameChanged,imageChanged};
}
// Backward-compatible aliases for older cached markup.



function resetEntityImageToApi(){
  _entityEditProviderImage=null; _entityEditPendingProviderImage=null; _entityEditImageData=''; _entityEditResetApi=true; $('entity-edit-image-url').value=''; $('entity-edit-image-file').value='';
  $('entity-edit-preview').src=`/api/${_entityEditKind==='performer'?'performers':'studios'}/${Number(_entityEditId)}/image?v=${Date.now()}`;
  $('entity-edit-selection-state').textContent='Provider image will be restored on save'; $('entity-edit-footer-status').textContent='Provider image restore selected.';
}
async function saveEntityEditor(event){
  event.preventDefault();
  const kind=_entityEditKind, id=_entityEditId; if(!kind||!id)return false;
  const manualUrl=$('entity-edit-image-url').value.trim();
  if(manualUrl){ _entityEditResetApi=false; _entityEditProviderImage=null; _entityEditPendingProviderImage=null; }
  const resetApi=!!_entityEditResetApi;
  const body={name:$('entity-edit-name').value.trim(),image_url:manualUrl,reset_image_to_api:resetApi};
  if(kind==='performer'){body.profile=$('entity-edit-profile').value.trim();body.bio=$('entity-edit-bio').value.trim();}
  if(_entityEditImageData)body.image_data=_entityEditImageData;
  if(!_entityEditImageData && !manualUrl && !resetApi && _entityEditProviderImage?.url){
    body.provider_image_url=_entityEditProviderImage.url;
    body.provider_image_provider=_entityEditProviderImage.provider||'provider';
  }
  const r=await api(`/api/${kind==='performer'?'performers':'studios'}/${id}/edit`,{method:'POST',body:JSON.stringify(body)});
  if(!r||r._ok===false||r.ok===false){toast('Save failed: '+(r?.detail||'error'));return false;}
  const currentNav=history.state||{};
  const currentView=String(currentNav.view||'');
  const currentScroll=Math.max(0,window.scrollY||window.pageYOffset||0);
  closeEntityEditor();
  if(kind==='studio')applyStudioSaveToDirectory(id,r.site);
  toast(_entityEditProviderImage&&!_entityEditImageData&&!manualUrl?'Selected profile image saved.':resetApi?'Provider profile image restored.':'Profile picture saved.');

  // Refresh the entity in place. Saving an image/name must never navigate from
  // the current Performer/Studio context into the entity detail page.
  // - On an entity detail page, refresh that detail without pushing history.
  // - On the index page, refresh the grid and keep the current page/query.
  if(kind==='performer'){
    const savedPerformer=r.performer&&typeof r.performer==='object'?r.performer:null;
    const forceImageRefresh=!!(_entityEditImageData||manualUrl||resetApi||_entityEditProviderImage?.url);
    const directoryUpdated=applyPerformerProfileSaveToDirectory(id,savedPerformer,{
      visible:currentView==='performers',
      forceImageRefresh
    });
    if(currentView==='performer' && Number(currentNav.id)===Number(id)){
      const wasApplying=_navApplying;
      _navApplying=true;
      try{
        const changed=applyPerformerProfileSaveToOpenDetail(id,savedPerformer,forceImageRefresh);
        if(!changed)await openPerf(id);
        else{
          const detailSeq=++_perfSeq;
          if(changed.nameChanged||changed.imageChanged)await refreshPerformerAfterMapping();
          hydratePerformerOverview(changed.entity,(_perfVideos||[]).length,detailSeq);
          loadPerfSuggested(id,detailSeq,_perfNavSeq);
        }
      } finally { _navApplying=wasApplying; }
      requestAnimationFrame(()=>window.scrollTo(0,currentScroll));
    }else if(currentView==='performers'&&directoryUpdated){
      // The visible performer tile was patched in place; avoid rebuilding the
      // whole directory and reloading every profile image.
    }else{
      const wasApplying=_navApplying;
      _navApplying=true;
      try{ await loadPerformers(_perfPage,_perfQuery||''); } finally { _navApplying=wasApplying; }
      requestAnimationFrame(()=>window.scrollTo(0,currentScroll));
    }
  }else{
    // Studio detail navigation uses view:'site' (the directory itself is 'sites').
    // Treat 'studio' as a legacy alias, and refresh the open detail in place so
    // saving a name/image cannot accidentally render the directory behind it.
    if((currentView==='site'||currentView==='studio') && Number(currentNav.id)===Number(id)){
      const wasApplying=_navApplying;
      const relationPerformerId=Number(currentNav.relationPerformerId)||Number(window._siteRelationPerformerId)||0;
      const detailTab=isEntityDetailTab(currentNav.tab)?String(currentNav.tab):undefined;
      _navApplying=true;
      try{ await openSite(id,null,relationPerformerId,detailTab); } finally { _navApplying=wasApplying; }
      requestAnimationFrame(()=>window.scrollTo(0,currentScroll));
    }else{
      const wasApplying=_navApplying;
      _navApplying=true;
      try{ await loadSites(true); } finally { _navApplying=wasApplying; }
      requestAnimationFrame(()=>window.scrollTo(0,currentScroll));
    }
  }
  return false;
}
function closeSuggestPreview(){
  const preview=$('suggest-preview');
  if(!preview)return;
  preview.classList.remove('open');
  delete preview.dataset.suggestionKind;
  delete preview.dataset.suggestionId;
  suggestionPreviewStatus('');
}
// Prefer persisted local snapshots, but fall back to the normal live-preview
// endpoint when a frame is missing. That endpoint can read the source video
// through Archive X/Telegram and caches its generated JPEG for later use.
function _suggestSnapshotIndex(frac){
  return Math.max(0,Math.min(35,Math.round((Number(frac)-0.02)/0.96*35)));
}
function _suggestImageFingerprint(frame){
  try{
    const canvas=document.createElement('canvas');canvas.width=24;canvas.height=14;
    const context=canvas.getContext('2d',{willReadFrequently:true});if(!context)return null;
    context.drawImage(frame,0,0,canvas.width,canvas.height);
    const bytes=context.getImageData(0,0,canvas.width,canvas.height).data;
    let hash=2166136261;
    for(let i=0;i<bytes.length;i++){hash^=bytes[i];hash=Math.imul(hash,16777619);}
    return (hash>>>0).toString(16);
  }catch(e){return null;}
}
function _suggestPreviewFrames(s,frames){
  const cid=encodeURIComponent(s.channel_id),mid=Number(s.message_id),duration=Math.max(0,Number(s.duration||0));
  let settled=0,failed=0,liveFallbacks=0,liveActive=0;
  const liveQueue=[];
  const grid=$('suggest-preview-grid');
  const drainLiveQueue=()=>{
    while(liveActive<2&&liveQueue.length){
      const start=liveQueue.shift();liveActive++;
      let released=false;
      const release=()=>{if(released)return;released=true;liveActive=Math.max(0,liveActive-1);drainLiveQueue();};
      try{start(release);}catch{release();}
    }
  };
  const finish=()=>{
    settled++;
    if(settled!==frames.length)return;
    const fingerprints=frames.map(item=>item.frame&&item.frame.naturalWidth>0?_suggestImageFingerprint(item.frame):null).filter(Boolean);
    const distinct=new Set(fingerprints).size;
    if(failed>0 || fingerprints.length!==frames.length || distinct<Math.min(3,frames.length)){
      if(grid){
        grid.classList.add('preview-incomplete');
        grid.setAttribute('aria-label','Some verification previews remain unavailable or are not visually distinct. Check the loaded frames before linking.');
      }
      const title=$('suggest-preview-title');
      if(title && !title.dataset.previewNotice){
        title.dataset.previewNotice='1';
        toast('Some verification frames could not be generated or looked identical. Check the available previews before linking.',7000);
      }
    }else if(grid){
      grid.classList.remove('preview-incomplete');
      grid.setAttribute('aria-label',liveFallbacks
        ? 'Video verification thumbnails; missing snapshots were generated as live previews.'
        : 'Video verification thumbnails from cached local snapshots.');
    }
  };
  frames.forEach(({frac,frame,box})=>{
    let done=false,liveTried=false;
    if(!frame||!box){failed++;finish();return;}
    box.classList.add('loading');box.classList.remove('failed','live-fallback');
    box.dataset.previewSource='snapshot';box.removeAttribute('title');
    const idx=_suggestSnapshotIndex(frac);
    const tm=box.querySelector('.tm');if(tm)tm.textContent=`${Math.round((0.02+0.96*idx/35)*100)}%`;
    const finishFrame=(isFailed,source,releaseLive)=>{
      if(done){if(releaseLive)releaseLive();return;}
      done=true;
      if(isFailed){failed++;box.dataset.previewSource='failed';box.classList.remove('loading');box.classList.add('failed');}
      else{
        box.classList.remove('loading','failed');
        if(source==='live'){
          liveFallbacks++;
          box.classList.add('live-fallback');
          box.dataset.previewSource='live';
          box.title='Live preview generated because the saved snapshot was unavailable';
        }else box.dataset.previewSource='snapshot';
      }
      finish();
      if(releaseLive)releaseLive();
    };
    const tryLivePreview=()=>{
      if(done||liveTried){finishFrame(true);return;}
      liveTried=true;
      box.classList.add('loading');box.classList.remove('failed');
      const time=duration>0?Math.max(0,Math.min(duration-0.05,duration*Number(frac||0))):0;
      liveQueue.push(releaseLive=>{
        if(done){releaseLive();return;}
        frame.onload=()=>finishFrame(!(frame.naturalWidth>1&&frame.naturalHeight>1),'live',releaseLive);
        frame.onerror=()=>finishFrame(true,'live',releaseLive);
        // pv creates a separate cache namespace from older scrub thumbnails.
        frame.src=`/api/play/preview/${cid}/${mid}?t=${encodeURIComponent(time.toFixed(3))}&w=480&pv=17`;
      });
      drainLiveQueue();
    };
    frame.onload=()=>{
      if(frame.naturalWidth>1&&frame.naturalHeight>1)finishFrame(false,'snapshot');
      else tryLivePreview();
    };
    frame.onerror=tryLivePreview;
    frame.src=`/api/play/preview-frame/${cid}/${mid}/${idx}?v=8`;
  });
  // Missing DOM entries are unusual, but must not leave the grid waiting forever.
  if(!frames.length&&grid)grid.setAttribute('aria-label','No verification previews are available.');
}

function openSuggestedPreview(sid,performerId){
  const s=_perfSuggestedMap[String(sid)];
  if(!s)return;
  const dur=Number(s.duration||0);
  const fractions=[0.10,0.35,0.60,0.85];
  // Rebuild the verification grid every time so an older cached DOM cannot
  // collapse this back to the former single-thumbnail preview.
  const grid=$('suggest-preview-grid');
  if(grid){
    grid.classList.remove('preview-incomplete');grid.setAttribute('aria-label','Video verification thumbnails');
     grid.innerHTML=fractions.map((frac,i)=>`<div class=\"sugg-preview-frame loading\" data-slot=\"${i}\"><img id=\"suggest-preview-img-${i}\" alt=\"Verification thumbnail ${i+1}\" src=\"\">${window.AXLoadingFeedback?.rollerMarkup?.('md','sugg-preview-loader')||''}<span class=\"tm\">${Math.round(frac*100)}%</span></div>`).join('');
  }
  const titleElement=$('suggest-preview-title');if(titleElement)delete titleElement.dataset.previewNotice;
  const frames=fractions.map((frac,i)=>{
    const frame=document.getElementById(`suggest-preview-img-${i}`);
    return {frac,frame,box:frame?.closest('.sugg-preview-frame')};
  });
  _suggestPreviewFrames(s,frames);
  $('suggest-preview-title').textContent=s.title||s.filename||('Video '+s.message_id);
  $('suggest-preview-meta').innerHTML=`<span class="badge">${esc(s.filename||'Telegram video')}</span>${dur?`<span class="badge">${fdur(dur)}</span>`:''}${s.size?`<span class="badge">${fmt(s.size)}</span>`:''}`;
  const previewModal=$('suggest-preview');previewModal.dataset.suggestionKind='performer';previewModal.dataset.suggestionId=String(Number(s.suggestion_id));
  suggestionPreviewStatus('');
  const performerName=String(window._currentPerfEntity?.name||'this performer');
  $('suggest-preview-reason').textContent=`Does this video belong to ${performerName}? Confirming links only this performer. Use Edit Video or Scene Match Review for full scene identification.`;
  $('suggest-preview-confirm').textContent='Yes, link performer';
  $('suggest-preview-reject').textContent='No, don’t link';
  $('suggest-preview-confirm').onclick=()=>perfConfirmSuggested(Number(s.suggestion_id),Number(performerId));
  $('suggest-preview-reject').onclick=()=>perfRejectSuggested(Number(s.suggestion_id),Number(performerId));
  $('suggest-preview').classList.add('open');
  suggestionActionBusy('performer',Number(s.suggestion_id),_suggestionActionInflight.has(`performer:${Number(s.suggestion_id)}`));
}
// Keep suggestion actions consistent across the performer, studio and player review surfaces.
const _suggestionActionInflight=new Set();
function suggestionActionSucceeded(r){return !!r && r._ok!==false && r.ok!==false;}
function suggestionCardFor(kind,sid){
  const id=String(Number(sid));
  const selector=kind==='performer'?`.entity-suggestion-card[data-suggid="${id}"]`
    :kind==='studio'?`.entity-suggestion-card[data-site-suggid="${id}"]`
    :kind==='media'?`.pk-suggestion[data-sid="${id}"]`:'';
  return selector?document.querySelector(selector):null;
}
function suggestionActionBusy(kind,sid,busy){
  const id=String(Number(sid));
  const card=suggestionCardFor(kind,sid);
  if(card)card.querySelectorAll('button').forEach(button=>{button.disabled=!!busy;button.setAttribute('aria-busy',busy?'true':'false');});
  const preview=$('suggest-preview');
  if(preview?.classList.contains('open')&&preview.dataset.suggestionKind===kind&&preview.dataset.suggestionId===id){
    ['suggest-preview-confirm','suggest-preview-reject'].forEach(buttonId=>{
      const button=$(buttonId);if(button){button.disabled=!!busy;button.setAttribute('aria-busy',busy?'true':'false');}
    });
    if(busy)suggestionPreviewStatus('Saving this relationship…');
  }
}
function suggestionPreviewStatus(message,isError=false){
  const status=$('suggest-preview-action-status');
  if(!status)return;
  status.textContent=String(message||'');
  status.hidden=!message;
  status.classList.toggle('is-error',!!message&&!!isError);
  status.classList.toggle('is-busy',!!message&&!isError);
}
function suggestionActionStatus(kind,sid,message){
  const card=suggestionCardFor(kind,sid);
  if(!card)return;
  let status=card.querySelector('.sug-action-status');
  if(!status){status=document.createElement('div');status.className='sug-action-status';status.setAttribute('role','status');status.style.cssText='margin-top:7px;font-size:11px;line-height:1.4;color:var(--danger,var(--error,#ff8585));overflow-wrap:anywhere';(card.querySelector('.sug-body')||card).appendChild(status);}
  status.textContent=String(message||'');status.hidden=!message;
}
function updateSuggestionMap(kind,sid){
  const key=String(Number(sid));
  if(kind==='performer')delete _perfSuggestedMap[key];
  else if(kind==='studio')delete _siteSuggestedMap[key];
  const card=suggestionCardFor(kind,sid);if(card)card.remove();
  if(kind==='performer'){
    const panel=$('perf-tab-suggestions');if(panel)renderPerfSuggestedPanel(panel);
  }else if(kind==='studio'){
    const panel=$('site-tab-suggestions');if(panel)renderSiteSuggestedPanel(panel);
  }
}
async function perfConfirmSuggested(sid,performerId){
  sid=Number(sid);performerId=Number(performerId);
  const key=`performer:${sid}`;if(!sid||_suggestionActionInflight.has(key))return false;
  _suggestionActionInflight.add(key);suggestionActionBusy('performer',sid,true);suggestionActionStatus('performer',sid,'');
  try{
    const r=await api(`/api/suggestions/${sid}/confirm-entity`,{method:'POST',body:JSON.stringify({entity_type:'performer',entity_id:performerId})});
    if(!suggestionActionSucceeded(r)){
      const reason=r?.detail||r?.message||'The server did not confirm this suggestion.';
      suggestionActionStatus('performer',sid,`Could not confirm: ${reason}`);suggestionPreviewStatus(`Could not confirm: ${reason}`,true);toast(`Could not confirm: ${reason}`,8000);return false;
    }
    updateSuggestionMap('performer',sid);closeSuggestPreview();toast('Performer linked to video.');
    const panel=$('perf-tab-suggestions');if(panel)renderPerfSuggestedPanel(panel);
    const tab=document.querySelector('#perf-detail .entity-detail-tab[data-entity-tab="suggestions"]');
    if(tab)tab.classList.add('active');
    entityTabActivate('suggestions');
    const st=history.state;
    if(st&&st.view==='performer'&&Number(st.id)===performerId)history.replaceState({...st,tab:'suggestions'},'');
    await loadPerfSuggested(performerId,_perfSeq,_perfNavSeq);
    return true;
  }catch(e){const reason=e?.message||String(e);suggestionActionStatus('performer',sid,`Could not confirm: ${reason}`);suggestionPreviewStatus(`Could not confirm: ${reason}`,true);toast(`Could not confirm: ${reason}`,8000);return false;}
  finally{_suggestionActionInflight.delete(key);suggestionActionBusy('performer',sid,false);}
}
async function perfRejectSuggested(sid,performerId){
  sid=Number(sid);const key=`performer:${sid}`;if(!sid||_suggestionActionInflight.has(key))return false;
  _suggestionActionInflight.add(key);suggestionActionBusy('performer',sid,true);suggestionActionStatus('performer',sid,'');
  try{
    const r=await api(`/api/suggestions/${sid}/reject-entity`,{method:'POST',body:JSON.stringify({entity_type:'performer',entity_id:Number(performerId)})});
    if(!suggestionActionSucceeded(r)){
      const reason=r?.detail||r?.message||'The server did not dismiss this performer link.';
      suggestionActionStatus('performer',sid,`Could not reject: ${reason}`);suggestionPreviewStatus(`Could not reject: ${reason}`,true);toast(`Could not reject: ${reason}`,8000);return false;
    }
    updateSuggestionMap('performer',sid);closeSuggestPreview();toast('Not linked to this performer. Kept available for full scene review.');
    await loadPerfSuggested(Number(performerId)||0,_perfSeq,_perfNavSeq);return true;
  }catch(e){const reason=e?.message||String(e);suggestionActionStatus('performer',sid,`Could not reject: ${reason}`);suggestionPreviewStatus(`Could not reject: ${reason}`,true);toast(`Could not reject: ${reason}`,8000);return false;}
  finally{_suggestionActionInflight.delete(key);suggestionActionBusy('performer',sid,false);}
}
// ═══════════════════════════════════════════════
// SECTION: SITES PAGE — mirror the performer page, backed by the existing studios/video_studios tables
// ═══════════════════════════════════════════════
let _siteVideos=[],_siteCurId=null;
let _siteSuggestedMap={};
let _siteSeq=0,_siteNavSeq=0;
const _siteRecent={};
function closeSiteDetail(){
  $('site-detail').classList.remove('open');
  $('site-detail').innerHTML='';
  $('site-list').style.display='grid';
  _entityDetailLoadSeq++;
  _siteCurId=null;
  setCurrentEntityNavMapping('',null,false);
  _endEntityMapping();
}
function studioImageApiUrl(studio){
  const id=Number(studio?.id);
  if(!Number.isSafeInteger(id)||id<=0)return '';
  const revision=[
    String(studio?.image||''),
    String(studio?.image_cache_updated_at||''),
    String(studio?.card_image_revision||''),
  ].join('|');
  return `/api/studios/${id}/image?v=${encodeURIComponent(revision)}`;
}
function studioCardArtworkMarkup(s,index=0){
  const name=String(s?.name||'').trim();
  if(/^unknown studio$/i.test(name)){
    return '<span class="entity-fallback ax-unknown" aria-hidden="true"><img class="ax-unknown-art" src="/static/icons/ax-unknown.jpg" alt="" loading="eager" decoding="async"></span>';
  }
  const fallback='<span class="entity-fallback" aria-hidden="true"><span class="ax-ph-bg"></span><img class="entity-fallback-logo" src="/static/icons/ax-placeholder.png" alt="" loading="eager" decoding="async"></span>';
  const providerImg=safeHttpUrl(s?.image)||'';
  const apiImg=studioImageApiUrl(s);
  const letter=esc(name.charAt(0).toUpperCase()||'?');
  // Always request the local image endpoint, including when the list record has
  // no image URL yet. The endpoint can repair a missing provider cache from
  // linked metadata. This directory is paginated (10/25/50/100 cards per page),
  // so every card on the current page should start its image request: relying on
  // native lazy loading left cards after the first eight as placeholders in some
  // browser/scroll-container layouts. Keep the first eight high priority and
  // let the browser schedule the remaining current-page images at low priority.
  const loading='eager';
  const priority=index<8 ? 'high' : 'low';
  return `${fallback}<img src="${esc(apiImg)}" alt="" loading="${loading}" fetchpriority="${priority}" decoding="async" data-provider-src="${esc(providerImg)}" data-local-src="${esc(apiImg)}" onload="entityImageLoaded(this)" onerror="studioImageFallback(this,${jsa(letter)})">`;
}
function siteCard(s,index=0){
  const art=studioCardArtworkMarkup(s,index);
  const videoCount=Number(s.video_count||0);
  return `<div class="pcard" data-ax-live-key="studio:${Number(s.id)}" data-entity-id="${Number(s.id)}"><div class="pimg">${art}</div><div class="pinfo"><div class="pname-row"><div class="pname" title="${esc(s.name)}">${esc(s.name)}</div></div><div class="studio-tile-footer"><div class="pcount studio-tile-stat" aria-label="${videoCount} related videos">${videoCount.toLocaleString()} video${videoCount!==1?'s':''}</div></div></div></div>`;
}
function applyStudioSaveToDirectory(id,site){
  const studioId=Number(id);
  const index=Array.isArray(_siteRows)?_siteRows.findIndex(row=>Number(row.id)===studioId):-1;
  const previousName=index>=0?String(_siteRows[index]?.name||'').trim().toLowerCase():'';
  const nextName=String(site?.name||(index>=0?_siteRows[index]?.name:'')||'').trim().toLowerCase();
  if(previousName)_axEntityLookup.delete(`studio:${previousName}`);
  if(nextName)_axEntityLookup.delete(`studio:${nextName}`);
  if(index<0||!site)return;
  _siteRows[index]={..._siteRows[index],...(site&&typeof site==='object'?site:{}),card_image_revision:`saved-${Date.now()}`};
  // Force a new local image URL even when the provider reuses the same image URL.
  // The image endpoint revalidates, so this immediately shows newly saved bytes.
  _siteListSig='';
  _siteQuietNext=true;
  renderSitesPage();
}
let _siteSeqLoading=false;


async function loadSites(silent=false){
  if(_siteSeqLoading)return;
  _siteSeqLoading=true;
  const seq=++_siteSeq, nav=++_siteNavSeq;
  const q=($('sitesearch')?.value||'').trim();
  if(!silent)closeSiteDetail();
  // Reopening the tab paints the studios already in memory at once, then
  // refreshes quietly; the list only re-renders if the data changed.
  const hadCache=!silent&&!q&&_siteRows.length>0;
  if(hadCache){renderSitesPage();window.AXMotion?.replayGrid($('site-list'));}
  const list=await api('/api/studios'+(q?`?q=${encodeURIComponent(q)}`:''));
  if(seq!==_siteSeq||nav!==_siteNavSeq){_siteSeqLoading=false;return;}
  if(hadCache&&Array.isArray(list)&&JSON.stringify(list)===_siteListSig){_siteSeqLoading=false;return;}
  if(hadCache)_siteQuietNext=true;
  const wrap=$('site-list');if(!wrap){_siteSeqLoading=false;return;}
  wrap.style.display='grid';
  let rows=Array.isArray(list)?list:[];
  if(q){const needle=q.toLowerCase();rows=rows.filter(x=>String(x.name||'').toLowerCase().includes(needle));}
  if(!rows.length){
    _siteRows=[];_sitePage=0;_siteListSig=JSON.stringify(rows);
    wrap.dataset.totalCount='0';wrap.dataset.visibleCount='0';
    wrap.innerHTML=q
      ?'<div class="entity-empty-state"><div class="entity-empty-title">No matching studios</div><div class="entity-empty-copy">Try a different search.</div></div>'
      :'<div class="entity-empty-state"><div class="entity-empty-title">No studios available</div><div class="entity-empty-copy">Studios appear when prepared videos are published by Media Manager.</div><button class="btn sm" onclick="sv(\'admin\')">Open Operations</button></div>';
    const pg=$('site-pg');if(pg){pg.innerHTML='';pg.style.display='none';}
    _siteSeqLoading=false;return;
  }
  _siteRows=rows;_siteListSig=JSON.stringify(rows);
  renderSitesPage();
  if(!silent&&!hadCache)window.AXMotion?.replayGrid(wrap);
  _siteSeqLoading=false;
}

const _entityProviderMetadataInflight = new Map();
async function loadEntityProviderMetadata(kind,id){
  const key=`${kind}:${Number(id)}`;
  if(_entityProviderMetadataInflight.has(key))return _entityProviderMetadataInflight.get(key);
  const request=(async()=>{
    const base=`/api/${kind==='performer'?'performers':'studios'}/${Number(id)}`;
    try{
      let payload=await api(`${base}/metadata`);
      if(Array.isArray(payload?.providers)&&payload.providers.length)return payload;
      // Studio pages are read-only: never start ENTITY_IMAGE_SYNC merely because
      // the user opened a Studio whose provider metadata is empty. The user can
      // run the intended refresh from Metadata & Assets Manager.
      if(kind==='studio')return payload||{providers:[]};
      const ensure=await api(`${base}/metadata/ensure`,{method:'POST',body:'{}'});
      if(ensure?.id){
        for(let i=0;i<80;i++){
          await new Promise(r=>setTimeout(r,750));
          const j=await api('/api/jobs/'+encodeURIComponent(ensure.id));
          const st=String(j?.status||'').toUpperCase();
          if(st==='COMPLETED'||st==='FAILED'||st==='CANCELLED')break;
        }
        payload=await api(`${base}/metadata`);
      }
      return payload||{providers:[]};
    }catch(_){return {providers:[]};}
  })();
  _entityProviderMetadataInflight.set(key,request);
  try{return await request;}finally{if(_entityProviderMetadataInflight.get(key)===request)_entityProviderMetadataInflight.delete(key);}
}
function _performerMetaRecords(payload){
  const providers=Array.isArray(payload?.providers)?payload.providers:[];
  return providers.map(x=>{
    let n={};try{n=typeof x.normalized==='string'?JSON.parse(x.normalized||'{}'):(x.normalized||{});}catch(_){}
    const d=n&&n.details&&typeof n.details==='object'?n.details:{};
    return {provider:String(x.provider||'Provider'),normalized:n&&typeof n==='object'?n:{},details:d};
  });
}
function _performerMetaText(value){
  if(value===null||value===undefined||value==='')return '';
  if(Array.isArray(value))return value.map(_performerMetaText).filter(Boolean).join(', ');
  if(typeof value==='object')return _performerMetaText(value.name||value.label||value.value||value.text||'');
  return String(value).trim();
}
function _careerSceneCountRows(payload){
  const source=Array.isArray(payload?.career_counts)?payload.career_counts:[];
  const out=[],seen=new Set();
  for(const item of source){
    if(!item||typeof item!=='object')continue;
    const provider=String(item.provider||'').trim();
    const count=Number(item.count);
    if(!provider||!Number.isFinite(count)||count<0||!Number.isInteger(count))continue;
    const key=provider.toLowerCase();if(seen.has(key))continue;seen.add(key);
    out.push([`Scenes listed by ${provider}`,count.toLocaleString()]);
  }
  return out;
}
function _performerBirthDateWithAge(value){
  const raw=_performerMetaText(value);if(!raw)return '';
  // Avoid interpreting a year-only value as January 1; age is shown only when
  // the provider supplies a complete date. Keep the provider's original text.
  const hasCompleteDate=/^(?:\d{4}[-/.]\d{1,2}[-/.]\d{1,2}|\d{1,2}[-/.]\d{1,2}[-/.]\d{4}|[A-Za-z]{3,}\s+\d{1,2},?\s+\d{4}|\d{1,2}\s+[A-Za-z]{3,}\s+\d{4})/.test(raw);
  if(!hasCompleteDate)return raw;
  let born;
  const iso=raw.match(/^(\d{4})[-/.](\d{1,2})[-/.](\d{1,2})$/);
  if(iso){const y=Number(iso[1]),m=Number(iso[2]),d=Number(iso[3]);born=new Date(y,m-1,d);if(born.getFullYear()!==y||born.getMonth()!==m-1||born.getDate()!==d)return raw;}
  else born=new Date(raw);
  if(!Number.isFinite(born.getTime()))return raw;
  const now=new Date();let age=now.getFullYear()-born.getFullYear();
  if(now.getMonth()<born.getMonth()||(now.getMonth()===born.getMonth()&&now.getDate()<born.getDate()))age--;
  if(age<0||age>125)return raw;
  return `${raw} (${age})`;
}
function _performerPrettyEnum(value){
  const raw=_performerMetaText(value);if(!raw)return '';
  if(/^[A-Z][A-Z _-]+$/.test(raw))return raw.toLowerCase().replace(/(^|[ _-])([a-z])/g,(_,sep,ch)=>sep+ch.toUpperCase());
  return raw;
}
function renderPerformerOverviewDetails(entity,payload,videoCount){
  const records=_performerMetaRecords(payload);
  const values=(keys)=>{
    const wanted=Array.isArray(keys)?keys:[keys],out=[],seen=new Set();
    for(const r of records){for(const obj of [r.details,r.normalized])for(const key of wanted){
      const v=_performerMetaText(obj?.[key]);if(!v)continue;const k=v.toLowerCase();if(!seen.has(k)){seen.add(k);out.push(v);}
    }}
    return out;
  };
  const first=(keys)=>values(keys)[0]||'';
  const birthDate=_performerBirthDateWithAge(first(['birth_date','date_of_birth','born']));
  const rows=[
    ['Gender',_performerMetaText(entity?.gender)||first(['gender'])],
    ['Country',first(['country','nationality'])],
    ['Birth date',birthDate],
    ['Ethnicity',first(['ethnicity'])],
    ['Height',first(['height','height_cm'])],
    ['Eye color',first(['eye_color','eyes'])],
    ['Hair color',first(['hair_color','hair'])],
    ['Breast type',_performerPrettyEnum(first(['breast_type','breastType']))],
    ['Cup size',first(['cup_size','cupSize'])],
    ['Career start',first(['career_start_year','career_start','career_start_date'])]
  ].filter(([label,v])=>String(v||'').trim()&&!['Gender'].includes(label));
  rows.push(['Videos in Archive X',Number(videoCount||0).toLocaleString()]);
  rows.push(..._careerSceneCountRows(payload));
  const providerNames=[...new Set(records.map(r=>r.provider.toUpperCase()))];
  const isUnknownLocal=String(entity?.name||'').trim().toLowerCase()==='unknown performer';
  // Metadata status is shown beside the entity title; avoid duplicating it in Details.
  return rows.map(([label,value])=>`<div class="performer-overview-detail"><span>${esc(label)}</span><strong>${esc(value)}</strong></div>`).join('');
}
function _performerSocialLinks(entity,payload){
  const records=_performerMetaRecords(payload),found=[],seen=new Set();
  const platformKeys={instagram:'Instagram',twitter:'X / Twitter',twitter_url:'X / Twitter',x:'X / Twitter',x_url:'X / Twitter',youtube:'YouTube',youtube_url:'YouTube',tiktok:'TikTok',facebook:'Facebook',reddit:'Reddit',onlyfans:'OnlyFans',patreon:'Patreon',threads:'Threads',linktree:'Linktree',linktr:'Linktree'};
  const knownHosts=[
    [/instagram\.com$/i,'Instagram'],[/(^|\.)x\.com$/i,'X / Twitter'],[/twitter\.com$/i,'X / Twitter'],[/youtube\.com$/i,'YouTube'],[/youtu\.be$/i,'YouTube'],[/tiktok\.com$/i,'TikTok'],[/facebook\.com$/i,'Facebook'],[/reddit\.com$/i,'Reddit'],[/onlyfans\.com$/i,'OnlyFans'],[/patreon\.com$/i,'Patreon'],[/threads\.net$/i,'Threads'],[/linktr\.ee$/i,'Linktree']
  ];
  const isProviderHost=host=>/(stashdb|theporndb|porn-db|archive-x)/i.test(host||'');
  const platformFrom=(key,host)=>{
    const k=String(key||'').toLowerCase().replace(/[\s-]+/g,'_');
    for(const [needle,label] of Object.entries(platformKeys))if(k===needle||k.includes(needle))return label;
    for(const [re,label] of knownHosts)if(re.test(host||''))return label;
    return '';
  };
  const add=(key,value,depth=0,insideSocial=false)=>{
    if(value===null||value===undefined||depth>5)return;
    if(Array.isArray(value)){value.forEach(v=>add(key,v,depth+1,insideSocial));return;}
    if(typeof value==='object'){
      const url=value.url||value.href||value.link||value.website||value.profile;
      const childLabel=value.platform||value.network||value.service||value.name||key;
      if(url)add(childLabel,url,depth+1,insideSocial||/social|link|contact/i.test(key));
      for(const [child,val] of Object.entries(value))if(!['url','href','link','website','profile','name','platform','network','service'].includes(child.toLowerCase()))add(child,val,depth+1,insideSocial||/social|link|contact/i.test(key));
      return;
    }
    const raw=String(value).trim();if(!/^https?:\/\//i.test(raw))return;
    let u;try{u=new URL(raw);}catch(_){return;}
    const host=u.hostname.replace(/^www\./i,'');if(isProviderHost(host))return;
    let label=platformFrom(key,host);
    const k=String(key||'').toLowerCase();
    if(!label&&(/^(website|homepage|official_website|site_url|personal_website)$/.test(k)))label='Website';
    if(!label&&insideSocial)label='Website';
    if(!label&&(/^(profile|url|link)$/.test(k)))label=platformFrom('',host);
    if(!label)return;
    const href=u.href;if(seen.has(href))return;seen.add(href);
    found.push({label,href,icon:label==='Instagram'?'IG':label==='X / Twitter'?'𝕏':label==='YouTube'?'▶':label==='TikTok'?'♪':label==='Facebook'?'f':label==='Reddit'?'r/':label==='OnlyFans'?'OF':label==='Patreon'?'P':label==='Threads'?'@':label==='Linktree'?'↗':'↗'});
  };
  const keys=new Set([...Object.keys(platformKeys),'social','socials','social_media','social_links','links','website','homepage','official_website','site_url','personal_website','profile','url']);
  for(const r of records){
    for(const obj of [r.details,r.normalized]){
      for(const [key,val] of Object.entries(obj||{}))if(keys.has(String(key).toLowerCase())||/social|instagram|twitter|youtube|tiktok|facebook|reddit|onlyfans|patreon|threads|linktree/i.test(key))add(key,val,0,/social|links|contact/i.test(key));
    }
  }
  // A manually maintained official profile URL is useful when it points to a
  // public social account; provider-specific URLs are filtered in add().
  if(entity?.profile)add('profile',entity.profile);
  return found;
}
function hydratePerformerOverview(p,videoCount,seq){
  const id=Number(p?.id);if(!id)return;
  const currentVideoCount=()=>Number(_perfCurId)===id?(_perfVideos||[]).length:videoCount;
  const isUnknownLocal=String(p?.name||'').trim().toLowerCase()==='unknown performer';
  if(isUnknownLocal){
    const bioEl=$('performer-overview-bio-text');
    if(bioEl){const bio=String(p.bio||'').trim()||'Local fallback profile for videos without a confirmed performer.';bioEl.textContent=bio;bioEl.classList.toggle('is-empty',!String(p.bio||'').trim());}
    const detailEl=$('performer-overview-details');if(detailEl)detailEl.innerHTML=renderPerformerOverviewDetails(p,{providers:[]},currentVideoCount());
    const socialEl=$('performer-social-links');if(socialEl){const links=_performerSocialLinks(p,{providers:[]});socialEl.innerHTML=links.length?links.map(x=>`<a class="performer-social-link" href="${esc(x.href)}" target="_blank" rel="noopener noreferrer" aria-label="Open ${esc(x.label)}" title="${esc(x.label)}"><span class="performer-social-icon">${axIcon(axBrandIconForUrl(x.href))}</span><span>${esc(x.label)}</span><svg class="ax-icon" aria-hidden="true"><use href="#ax-external"></use></svg></a>`).join(''):'<span class="performer-social-empty">No local social links are available for this fallback profile.</span>';}
    const status=$('performer-overview-status');if(status){status.classList.remove('is-linked');status.title='Local fallback profile';}
    const identityStatus=document.querySelector('#perf-detail .performer-identity-link-status');if(identityStatus){identityStatus.classList.remove('is-linked');identityStatus.innerHTML='<i></i>Local profile';}
    return;
  }
  loadEntityProviderMetadata('performer',id).then(payload=>{
    if(seq!==_perfSeq||Number(_perfCurId)!==id||!document.getElementById('performer-overview-details'))return;
    const providers=Array.isArray(payload?.providers)?payload.providers:[];
    const records=_performerMetaRecords(payload);
    const providerBadgeHost=$('performer-provider-pills');if(providerBadgeHost){const labels=[...new Set(records.map(r=>String(r.provider||'').trim()).filter(Boolean))].map(name=>{const low=name.toLowerCase();return /stash/.test(low)?'StashDB':/tpdb|theporn|porn.?db/.test(low)?'ThePornDB':name;});providerBadgeHost.innerHTML=labels.map(name=>`<span class=\"ax2-entity-pill ${name==='StashDB'?'is-violet':name==='ThePornDB'?'is-accent':''}\">${esc(name)}</span>`).join('');}
    const bioFromProvider=records.map(r=>_performerMetaText(r.details.bio||r.details.biography||r.details.description||r.normalized.bio||r.normalized.biography||r.normalized.description)).find(Boolean)||'';
    const bioEl=$('performer-overview-bio-text');
    if(bioEl){const bio=String(p.bio||'').trim()||bioFromProvider;bioEl.textContent=bio||'No biography has been supplied by the available metadata sources.';bioEl.classList.toggle('is-empty',!bio);}
    const detailEl=$('performer-overview-details');if(detailEl)detailEl.innerHTML=renderPerformerOverviewDetails(p,payload,currentVideoCount());
    api(`/api/performers/${id}/career-counts`).then(countPayload=>{
      if(seq!==_perfSeq||Number(_perfCurId)!==id||!document.getElementById('performer-overview-details'))return;
      const careerCounts=Array.isArray(countPayload?.career_counts)?countPayload.career_counts:[];
      if(!careerCounts.length)return;
      const currentDetails=$('performer-overview-details');
      if(currentDetails)currentDetails.innerHTML=renderPerformerOverviewDetails(p,{...payload,career_counts:careerCounts},currentVideoCount());
    }).catch(()=>{});
    const socialEl=$('performer-social-links');
    if(socialEl){const links=_performerSocialLinks(p,payload);socialEl.innerHTML=links.length?links.map(x=>`<a class="performer-social-link" href="${esc(x.href)}" target="_blank" rel="noopener noreferrer" aria-label="Open ${esc(x.label)}" title="${esc(x.label)}"><span class="performer-social-icon">${axIcon(axBrandIconForUrl(x.href))}</span><span>${esc(x.label)}</span><svg class="ax-icon" aria-hidden="true"><use href="#ax-external"></use></svg></a>`).join(''):'<span class="performer-social-empty">No social media links are available from the saved profile or metadata providers.</span>';}
    const isLinked=providers.length>0||!!(p.provider_ids&&p.provider_ids!=='{}');
    const status=$('performer-overview-status');if(status){status.classList.toggle('is-linked',isLinked);status.title=isLinked?'Provider metadata linked':'No provider metadata linked';}
    const identityStatus=document.querySelector('#perf-detail .performer-identity-link-status');
    if(identityStatus){identityStatus.classList.toggle('is-linked',isLinked);identityStatus.innerHTML=`<i></i>${isLinked?'Metadata linked':'Local profile'}`;}
  }).catch(()=>{});
}
function renderMergedEntityProfile(entity,payload,kind,videoCount){
  const providers=Array.isArray(payload?.providers)?payload.providers:[];
  const clean=(v)=>{
    if(v===null||v===undefined||v==='')return '';
    if(Array.isArray(v))return v.map(x=>typeof x==='object'?(x.name||x.label||x.url||''):String(x)).filter(Boolean).join(', ');
    if(typeof v==='object')return v.name||v.label||v.url||'';
    return String(v);
  };
  const uniq=(values)=>{
    const out=[];const seen=new Set();
    for(const raw of values){const value=clean(raw).trim();if(!value)continue;const key=value.toLowerCase();if(seen.has(key))continue;seen.add(key);out.push(value);}
    return out;
  };
  const row=(label,values)=>{const vals=uniq(Array.isArray(values)?values:[values]);return vals.length?`<div class="entity-info-row"><span class="entity-info-label">${esc(label)}</span><span class="entity-info-value">${esc(vals.join(' · '))}</span></div>`:'';};
  const linkRow=(label,values)=>{const vals=uniq(Array.isArray(values)?values:[values]).filter(v=>/^https?:\/\//i.test(v));return vals.length?`<div class="entity-info-row"><span class="entity-info-label">${esc(label)}</span><span class="entity-info-value">${vals.map(v=>`<a href="${esc(v)}" target="_blank" rel="noopener noreferrer">Open</a>`).join(' · ')}</span></div>`:'';};
  const parsed=providers.map(x=>{let n={};try{n=typeof x.normalized==='string'?JSON.parse(x.normalized||'{}'):(x.normalized||{});}catch(_){}return {provider:String(x.provider||'Provider'),id:x.provider_id,details:n.details&&typeof n.details==='object'?n.details:{},normalized:n};});
  const all=(key)=>parsed.map(x=>x.details[key]).concat(parsed.map(x=>x.normalized[key]));
  const fields=kind==='performer'?{
    personal:[['Gender','gender'],['Birth date','birth_date'],['Death date','death_date'],['Country','country'],['Ethnicity','ethnicity'],['Eye color','eye_color'],['Hair color','hair_color']],
    physical:[['Height (cm)','height'],['Measurements','measurements'],['Cup size','cup_size'],['Band size','band_size'],['Waist','waist_size'],['Hip','hip_size'],['Breast type','breast_type'],['Tattoos','tattoos'],['Piercings','piercings']],
    career:[['Career start','career_start_year'],['Career end','career_end_year']]
  }:{
    personal:[['Country','country'],['Parent studio','parent']],
    physical:[],
    career:[['Founded','founded_year'],['Career start','career_start_year'],['Career end','career_end_year']]
  };
  const section=(title,list)=>{const body=list.map(([label,key])=>row(label,all(key))).join('');return body?`<div class="entity-provider-section"><h4>${esc(title)}</h4>${body}</div>`:'';};
  const providerNames=uniq(parsed.map(x=>x.provider.toUpperCase())).join(' · ');
  const providerIds=parsed.map(x=>`${x.provider}: ${clean(x.id)}`).filter(Boolean);
  const aliases=all('aliases');
  const profileUrls=all('profile').concat(all('url'));
  const websites=all('website');
  const identity=[row('Provider sources',providerNames),row('Provider IDs',providerIds),row('Aliases',aliases),linkRow('Profile URL',profileUrls),linkRow('Website',websites)].join('');
  const identitySection=identity?`<div class="entity-provider-section"><h4>Metadata sources</h4>${identity}</div>`:'';
  const providerNote=providers.length?`<div class="entity-provider-source-note">Synchronized from ${esc(providerNames||'metadata providers')}. Duplicate values are merged into the profile.</div>`:'<div class="muted">No synchronized provider details are available yet.</div>';
  const profileName=entity?.name||'';
  const profileGender=entity?.gender||'';
  const profileCount=Number(videoCount||0);
  return `<div class="entity-info-card entity-profile-card" style="grid-column:1/-1">
    <div class="entity-provider-head"><h3>Profile</h3><span class="muted">${profileCount.toLocaleString()} video${profileCount===1?'':'s'}</span></div>
    <div class="entity-profile-summary">${row('Name',profileName)}${row('Gender',profileGender||all('gender'))}${row('Favourite',entity?.favorite?'Yes':'No')}</div>
    ${identitySection}
    <div class="entity-profile-detail-columns">
      ${section(kind==='performer'?'Personal details':'Studio details',fields.personal)}
      ${section('Physical details',fields.physical)}
    </div>
    ${section('Career',fields.career)}
    ${providerNote}
  </div>`;
}

async function renderSiteDetails(){
  const s=window._currentSiteEntity||{};
  const meta=await loadEntityProviderMetadata('studio',Number(s.id));
  const bio=String(s.bio||'').trim();
  const url=String(s.url||s.site_url||'').trim();
  return `<div class="entity-info-grid">
    ${renderMergedEntityProfile(s,meta,'studio',(_siteVideos||[]).length)}
    ${url?`<div class="entity-info-card"><h3>Studio link</h3><div class="entity-info-row"><span class="entity-info-label">Local URL</span><span class="entity-info-value"><a href="${esc(url)}" target="_blank" rel="noopener noreferrer">Open</a></span></div></div>`:''}
    ${bio?`<div class="entity-info-card" style="grid-column:1/-1"><h3>Biography</h3><div style="font-size:12px;color:#c0c0c0;line-height:1.65">${esc(bio)}</div></div>`:''}
  </div>`;
}
function renderStudioOverviewDetails(site,payload,videoCount,linkedPerformerCount=0,relatedStudios=[]){
  const records=_performerMetaRecords(payload);
  const values=(keys)=>{
    const wanted=Array.isArray(keys)?keys:[keys],out=[],seen=new Set();
    for(const r of records)for(const obj of [r.details,r.normalized])for(const key of wanted){
      const value=_performerMetaText(obj?.[key]);if(!value)continue;
      const norm=value.toLowerCase();if(!seen.has(norm)){seen.add(norm);out.push(value);}
    }
    return out;
  };
  const first=keys=>values(keys)[0]||'';
  const providers=[...new Set(records.map(r=>r.provider.toUpperCase()).filter(Boolean))];
  let localAliases=[];
  if(Array.isArray(site?.aliases))localAliases=site.aliases;
  else if(typeof site?.aliases==='string'&&site.aliases.trim()){
    try{const parsed=JSON.parse(site.aliases);localAliases=Array.isArray(parsed)?parsed:[site.aliases];}
    catch(_){localAliases=site.aliases.split(/[,;|]/);}
  }
  const aliasList=[...new Set(localAliases.concat(values(['aliases','alternative_names','alias_list'])).map(_performerMetaText).map(x=>x.trim()).filter(x=>x&&x.toLowerCase()!==String(site?.name||'').trim().toLowerCase()))];
  const rows=[
    ['Country',_performerMetaText(site?.country)||first(['country','country_name'])],
    ['Parent studio',_performerMetaText(site?.parent)||first(['parent','parent_studio','parent_name'])],
    ['Founded',_performerMetaText(site?.founded_year)||first(['founded_year','founded','founding_year','start_year'])],
    ['Career start',first(['career_start_year','career_start','career_start_date'])],
    ['Career end',first(['career_end_year','career_end','career_end_date'])],
    ['Videos in Archive X',Number(videoCount||0).toLocaleString()],
    ..._careerSceneCountRows(payload),
    ['Linked performers',Number(linkedPerformerCount||0).toLocaleString()],
    ['Aliases',aliasList.join(' · ')],
    ['Metadata',providers.length?providers.join(' · '):(site?.provider_ids&&site.provider_ids!=='{}'?'Linked':'Local profile')]
  ].filter(([_,value])=>String(value??'').trim());
  const details=rows.map(([label,value])=>`<div class="performer-overview-detail"><span>${esc(label)}</span><strong>${esc(value)}</strong></div>`).join('');
  const related=(Array.isArray(relatedStudios)?relatedStudios:[]).filter(x=>Number(x?.id)>0&&String(x?.name||'').trim());
  const relatedRow=related.length?`<div class="performer-overview-detail studio-family-detail"><span>Related studios</span><div class="studio-family-links">${related.map(x=>`<button type="button" class="studio-family-link" title="${esc(x.relation||'Related studio')}" onclick="openSite(${Number(x.id)})">${esc(x.name)}<small>${esc(x.relation||'Related')}</small></button>`).join('')}</div></div>`:'';
  return details+relatedRow;
}
function hydrateStudioOverview(site,videoCount,seq){
  const id=Number(site?.id);if(!id)return;
  loadEntityProviderMetadata('studio',id).then(payload=>{
    if(seq!==_siteSeq||Number(_siteCurId)!==id||!document.getElementById('studio-overview-details'))return;
    const providers=Array.isArray(payload?.providers)?payload.providers:[];
    const records=_performerMetaRecords(payload);
    const providerBio=records.map(r=>_performerMetaText(r.details.bio||r.details.biography||r.details.description||r.details.summary||r.normalized.bio||r.normalized.biography||r.normalized.description||r.normalized.summary)).find(Boolean)||'';
    const bio=String(site.bio||'').trim()||providerBio;
    const bioEl=$('studio-overview-bio-text');
    if(bioEl){bioEl.textContent=bio||'No biography has been supplied by the available metadata sources.';bioEl.classList.toggle('is-empty',!bio);}
    const linkedPerformers=entityCountMap((_siteVideos||[]).flatMap(v=>v.performers||[])).size;
    let relatedStudios=[],careerCounts=[];
    const renderDetails=()=>{
      if(seq!==_siteSeq||Number(_siteCurId)!==id||!document.getElementById('studio-overview-details'))return;
      const detailsEl=$('studio-overview-details');
      if(detailsEl)detailsEl.innerHTML=renderStudioOverviewDetails(site,{...payload,career_counts:careerCounts},videoCount,linkedPerformers,relatedStudios);
    };
    api(`/api/studios/${id}/family`).then(family=>{
      relatedStudios=Array.isArray(family?.related)?family.related:[];
      renderDetails();
    }).catch(()=>{relatedStudios=[];renderDetails();});
    api(`/api/studios/${id}/career-counts`).then(countPayload=>{
      careerCounts=Array.isArray(countPayload?.career_counts)?countPayload.career_counts:[];
      renderDetails();
    }).catch(()=>{});
    const links=_performerSocialLinks(site,payload);
    const website=String(site.site_url||site.url||'').trim();
    if(/^https?:\/\//i.test(website)&&!links.some(x=>x.href===website))links.unshift({label:'Official website',href:website,icon:'↗'});
    const socialEl=$('studio-social-links');
    if(socialEl)socialEl.innerHTML=links.length?links.map(x=>`<a class="performer-social-link" href="${esc(x.href)}" target="_blank" rel="noopener noreferrer" aria-label="Open ${esc(x.label)}" title="${esc(x.label)}"><span class="performer-social-icon">${axIcon(axBrandIconForUrl(x.href))}</span><span>${esc(x.label)}</span><svg class="ax-icon" aria-hidden="true"><use href="#ax-external"></use></svg></a>`).join(''):'<span class="performer-social-empty">No website or social links are available from the saved profile or metadata providers.</span>';
    const isLinked=providers.length>0||!!(site.provider_ids&&site.provider_ids!=='{}');
    const status=$('studio-overview-status');if(status){status.classList.toggle('is-linked',isLinked);status.title=isLinked?'Provider metadata linked':'No provider metadata linked';}
    const identityStatus=document.querySelector('#site-detail .performer-identity-link-status');
    if(identityStatus){identityStatus.classList.toggle('is-linked',isLinked);identityStatus.innerHTML=`<i></i>${isLinked?'Metadata linked':'Local profile'}`;}
  }).catch(()=>{});
}
async function renderSiteTab(tab){
  const root=$('site-detail');if(!root)return;
  const panel=root.querySelector(`.entity-tab-panel[data-tab-panel="${tab}"]`);
  if(!panel)return;
  const entityId=Number(_siteCurId),seq=_siteSeq,nav=_siteNavSeq,detailNav=_entityDetailLoadSeq;
  const isCurrent=()=>seq===_siteSeq&&nav===_siteNavSeq&&detailNav===_entityDetailLoadSeq&&Number(_siteCurId)===entityId&&root.classList.contains('open')&&panel.isConnected;
  if(tab==='videos'){panel.innerHTML=renderEntityVideosPanel('studio');hydrateThumbs(panel);bindCardPreviews(panel);return;}
  if(tab==='suggestions'){renderSiteSuggestedPanel(panel);return;}
  if(tab==='details'){const html=await renderSiteDetails();if(isCurrent())panel.innerHTML=html;return;}
  if(tab==='performers'){
    const counts=entityCountMap((_siteVideos||[]).flatMap(v=>v.performers||[]));
    const all=await entityLookup('performer');
    if(!isCurrent())return;
    const items=all.filter(x=>counts.has(String(x.name||''))).map(x=>({...x,count:counts.get(String(x.name||''))}));
    panel.innerHTML=items.length?`<div class="entity-related-grid">${items.map(x=>relatedEntityCard(x,'performer',{studioId:_siteCurId},true)).join('')}</div>`:'<div class="entity-empty-tab">No linked performers were found for this studio.</div>';
    return;
  }
  if(tab==='similar'){
    const counts=entityCountMap((_siteVideos||[]).flatMap(v=>v.studios||[]).filter(x=>String(x).toLowerCase()!==String(window._currentSiteEntity?.name||'').toLowerCase()));
    const all=await entityLookup('studio');
    if(!isCurrent())return;
    const items=all.filter(x=>counts.has(String(x.name||''))).sort((a,b)=>(counts.get(String(b.name||''))||0)-(counts.get(String(a.name||''))||0)).slice(0,40);
    panel.innerHTML=items.length?`<div class="entity-studio-grid">${items.map(x=>relatedEntityCard({...x,count:counts.get(String(x.name||''))},'studio',null,true)).join('')}</div>`:'<div class="entity-empty-tab">No similar studios are available from the current catalogue relationships.</div>';
  }
}
async function openSite(id,navToken=null,relationPerformerId=null,requestedTab=null){
  const seq=++_siteSeq;
  const nav=navToken??++_siteNavSeq;
  const detailNav=++_entityDetailLoadSeq;
  if(_siteCurId!==Number(id))_endEntityMapping();   // a different studio: new selection
  _siteCurId=Number(id); _perfCurId=null;
  $('perf-detail')?.classList.remove('open');
  const relationQuery=Number(relationPerformerId)>0?`?performer_id=${Number(relationPerformerId)}`:'';
  const siteSkeleton=window.AXSkeleton?.later(()=>{
    if(seq!==_siteSeq||nav!==_siteNavSeq||detailNav!==_entityDetailLoadSeq)return;
    showView('sites');
    $('site-list').style.display='none';
    const d=$('site-detail');d.classList.add('open');d.innerHTML=window.AXSkeleton.entityPage('studio');
  },140);
  const r=await api(`/api/studios/${Number(id)}/videos${relationQuery}`);
  siteSkeleton?.cancel();
  if(seq!==_siteSeq||nav!==_siteNavSeq||detailNav!==_entityDetailLoadSeq)return;
  if(!r||!r.site){if(siteSkeleton?.shown)closeSiteDetail();setSt('Studio API failed: '+(r?.detail||'unknown error'));return;}
  showView('sites');
  $('site-list').style.display='none';
  const site=r.site, videos=Array.isArray(r.videos)?r.videos:[];
  _siteVideos=videos;_siteCurId=Number(site.id);window._currentSiteEntity=site; window._siteRelationPerformerId=Number(relationPerformerId)||0;
  const detail=$('site-detail');detail.classList.add('open');
  const url=String(r.site_url||site.site_url||site.url||'').trim();
  if(url&&!site.site_url)site.site_url=url;
  const linkedPerformerCount=entityCountMap(videos.flatMap(v=>v.performers||[])).size;
  let studioProviderNames=[];
  try{const parsed=typeof site.provider_ids==='string'?JSON.parse(site.provider_ids||'{}'):site.provider_ids;if(parsed&&typeof parsed==='object')studioProviderNames=Object.entries(parsed).filter(([,v])=>v!==null&&v!==undefined&&String(v).trim()).map(([k])=>k);}catch(e){}
  const studioProviderPills=studioProviderNames.map(name=>{const low=String(name).toLowerCase();const label=/stash/.test(low)?'StashDB':/tpdb|porn.?db|theporn/.test(low)?'ThePornDB':String(name);return `<span class="ax2-entity-pill ${label==='StashDB'?'is-violet':label==='ThePornDB'?'is-accent':''}">${esc(label)}</span>`;}).join('');
  const studioLocalImage=studioImageApiUrl(site);
  const studioProviderImage=safeHttpUrl(site.image)||'';
  const studioFallbackLetter=String(site.name||'?').trim().charAt(0).toUpperCase()||'?';
  detail.innerHTML=`<div class="entity-detail-backbar ax2-entity-backbar"><button type="button" class="entity-detail-back" onclick="universalBack()" aria-label="Go back" title="Go back"><svg class="ax-icon" aria-hidden="true"><use href="#ax-arrow-left"></use></svg></button><span class="universal-back-label entity-back-label">Studios › ${esc(site.name)}</span></div>
    <section class="ax2-entity-hero ax2-studio-hero" aria-label="Studio profile">
      <div class="ax2-studio-media"><div class="studio-profile-logo ax2-studio-logo" aria-label="${esc(site.name)} logo"><img src="${esc(studioLocalImage)}" data-provider-src="${esc(studioProviderImage)}" data-local-src="${esc(studioLocalImage)}" alt="" onload="entityImageLoaded(this)" onerror="studioImageFallback(this,${jsa(studioFallbackLetter)})"></div></div>
      <div class="ax2-entity-main">
        <div class="ax2-entity-eyebrow">STUDIO</div>
        <h1 class="ax2-entity-title">${esc(site.name)}</h1>
        <div class="ax2-entity-pills">
          <span class="ax2-entity-pill">${videos.length.toLocaleString()} video${videos.length===1?'':'s'}</span>
          <span class="ax2-entity-pill">${linkedPerformerCount.toLocaleString()} performer${linkedPerformerCount===1?'':'s'}</span>
          <span id="studio-overview-status" class="performer-overview-status ax2-entity-pill ${site.provider_ids&&site.provider_ids!=='{}'?'is-linked':''}" title="Checking metadata"><i></i>${site.provider_ids&&site.provider_ids!=='{}'?'Metadata linked':'Local profile'}</span>
          <span class="ax2-entity-provider-pills">${studioProviderPills}</span>
        </div>
        <div id="studio-overview-bio-text" class="performer-overview-bio ax2-entity-bio${String(site.bio||'').trim()?'':' is-empty'}">${esc(String(site.bio||'').trim()||'Loading available studio biography…')}</div>
        <button type="button" class="ax2-bio-toggle" data-ax2-bio-toggle="studio-overview-bio-text" hidden>Read more</button>
        <div class="ax2-entity-actions">
          <button type="button" class="btn sm pri performer-icon-action" id="site-rnd" onclick="siteRandomMPC()" title="Play a random video from this studio in MPC-HC" aria-label="Shuffle in MPC-HC">${axIcon('shuffle')}<span>Shuffle in MPC-HC</span></button>
          ${entityFavButton('studio',Number(site.id),!!site.favorite,false)}
          <button type="button" class="btn sm" onclick="openEntityEditor('studio',${Number(site.id)})" title="Edit studio details" aria-label="Edit studio details">${axIcon('edit')}<span>Edit studio</span></button>
        </div>
        <div class="performer-social-panel studio-social-panel ax2-entity-social" aria-label="Website and social links"><div id="studio-social-links" class="performer-social-links"><span class="performer-social-empty">Checking saved profile links…</span></div></div>
      </div>
    </section>
    <section class="ax2-entity-details-card" aria-label="Studio details"><div class="ax2-entity-details-head"><h2>Details</h2><span>From saved profile &amp; providers</span></div><div id="studio-overview-details" class="performer-overview-details">${renderStudioOverviewDetails(site,{providers:[]},videos.length,linkedPerformerCount,[])}</div></section>
    <div class="entity-detail-tabs ax2-entity-tabs" role="tablist" aria-label="Studio details">
      ${entityTabButton('videos','Videos',videos.length)}
      ${entityTabButton('performers','Performers',linkedPerformerCount)}
      ${entityTabButton('similar','Similar Studios')}
      ${entityTabButton('suggestions','Suggestions')}
    </div>
    <div id="site-tab-videos" class="entity-tab-panel active" data-tab-panel="videos"></div>
    <div id="site-tab-performers" class="entity-tab-panel" data-tab-panel="performers"></div>
    <div id="site-tab-similar" class="entity-tab-panel" data-tab-panel="similar"></div>
    <div id="site-tab-suggestions" class="entity-tab-panel" data-tab-panel="suggestions"></div>`;
  setCurrentEntityNavMapping('studio',Number(site.id),true);
  const currentNav=history.state||{};
  const targetTab=isEntityDetailTab(requestedTab)?String(requestedTab):'videos';
  renderSiteTab('videos');hydrateStudioOverview(site,videos.length,seq);hydrateThumbs(detail);
  const returnView=currentNav.view==='sites'?'sites':(currentNav.view||'sites');
  const returnTab=(currentNav.view==='site'&&isEntityDetailTab(currentNav.tab))?String(currentNav.tab):null;
  const returnScroll=returnView==='sites'?Math.max(0,window.scrollY||window.pageYOffset||0):0;
  if(!_navApplying && currentNav.view==='sites')history.replaceState({...currentNav,scroll:returnScroll,anchorId:Number(site.id)},'');
  window.scrollTo(0,0);
  _navPush({view:'site',id:Number(site.id),relationPerformerId:Number(relationPerformerId)||0,tab:targetTab,returnView,returnTab,returnScroll,returnAnchorId:Number(site.id)});
  entityTabActivate(targetTab);
  if(targetTab!=='videos') await renderSiteTab(targetTab);
  loadSiteSuggested(Number(site.id), seq, nav);
}

function renderSiteSuggestedPanel(panel){
  const list=Object.values(_siteSuggestedMap||{});
  const studioName=String(window._currentSiteEntity?.name||'this studio');
  if(!panel)return;
  panel.innerHTML=list.length?`<div class="entity-suggestion-toolbar"><div><b>Suggested Videos</b><div class="muted" style="font-size:11px;margin-top:3px">Does each video belong to ${esc(studioName)}? Confirming links this studio and hides other studio suggestions for the video. Performer links are separate; use Edit Video or Scene Match Review for full scene identification.</div></div><span class="ax-section-chip">${list.length} pending</span></div><div class="entity-suggestion-grid">${list.map(s=>`
    <div class="entity-suggestion-card" data-site-suggid="${Number(s.suggestion_id)}" onclick="openSiteSuggestedPreview(${Number(s.suggestion_id)},${_siteCurId})">
      <div class="vt"><img data-src="/api/thumb/${encodeURIComponent(s.channel_id)}/${Number(s.message_id)}?r=f3" alt="" decoding="async"><div class="ph" style="display:none"><svg class="ax-icon" aria-hidden="true"><use href="#ax-play"></use></svg></div></div>
      <div class="sug-body"><div class="vn">${esc(s.title||s.filename||('video '+s.message_id))}</div>
      <div class="sug-confidence">${s.duration?fdur(s.duration):''}${s.duration&&s.size?' · ':''}${s.size?fmt(s.size):''}</div>
      <div class="sug-reason">Does this video belong to ${esc(studioName)}?</div>
      <div class="sug-actions"><button class="btn xs pri" onclick="event.stopPropagation();siteConfirmSuggested(${Number(s.suggestion_id)},${_siteCurId})">Yes, link studio</button><button class="btn xs" onclick="event.stopPropagation();siteRejectSuggested(${Number(s.suggestion_id)},${_siteCurId})">No, don’t link</button></div></div>
    </div>`).join('')}</div>`:'<div class="entity-empty-tab"><b>No pending suggestions</b><div style="margin-top:5px">Run Metadata &amp; Suggestion Scan from Operations to create new suggestions.</div></div>';
  hydrateThumbs(panel);
}
async function loadSiteSuggested(siteId, seq, nav){
  let list=[];
  try{const r=await api(`/api/studios/${siteId}/suggested`);list=Array.isArray(r)?r:[];}catch(e){list=[];}
  if(seq!==_siteSeq||nav!==_siteNavSeq||Number(_siteCurId)!==Number(siteId)||!$('site-detail')?.classList.contains('open'))return;
  _siteSuggestedMap={};list.forEach(s=>{_siteSuggestedMap[String(s.suggestion_id)]=s;});
  const tab=document.querySelector('#site-detail .entity-detail-tab[data-entity-tab="suggestions"]');
  if(tab)tab.innerHTML=`Suggestions${list.length?`<span class="tab-count">${list.length}</span>`:''}`;
  const panel=$('site-tab-suggestions');
  if(panel&&panel.classList.contains('active'))renderSiteSuggestedPanel(panel);
}
function openSiteSuggestedPreview(sid,siteId){
  const s=_siteSuggestedMap[String(sid)];
  if(!s)return;
  const dur=Number(s.duration||0);
  const fractions=[0.10,0.35,0.60,0.85];
  const grid=$('suggest-preview-grid');
  if(grid){
    grid.classList.remove('preview-incomplete');grid.setAttribute('aria-label','Video verification thumbnails');
     grid.innerHTML=fractions.map((frac,i)=>`<div class="sugg-preview-frame loading" data-slot="${i}"><img id="suggest-preview-img-${i}" alt="Verification thumbnail ${i+1}" src="">${window.AXLoadingFeedback?.rollerMarkup?.('md','sugg-preview-loader')||''}<span class="tm">${Math.round(frac*100)}%</span></div>`).join('');
  }
  const titleElement=$('suggest-preview-title');if(titleElement)delete titleElement.dataset.previewNotice;
  const frames=fractions.map((frac,i)=>{const frame=document.getElementById(`suggest-preview-img-${i}`);return {frac,frame,box:frame?.closest('.sugg-preview-frame')};});
  _suggestPreviewFrames(s,frames);
  $('suggest-preview-title').textContent=s.title||s.filename||('Video '+s.message_id);
  $('suggest-preview-meta').innerHTML=`<span class="badge">${esc(s.filename||'Telegram video')}</span>${dur?`<span class="badge">${fdur(dur)}</span>`:''}${s.size?`<span class="badge">${fmt(s.size)}</span>`:''}`;
  const previewModal=$('suggest-preview');previewModal.dataset.suggestionKind='studio';previewModal.dataset.suggestionId=String(Number(s.suggestion_id));
  suggestionPreviewStatus('');
  const studioName=String(window._currentSiteEntity?.name||'this studio');
  $('suggest-preview-reason').textContent=`Does this video belong to ${studioName}? Confirming links only this studio. Use Edit Video or Scene Match Review for full scene identification.`;
  $('suggest-preview-confirm').textContent='Yes, link studio';
  $('suggest-preview-reject').textContent='No, don’t link';
  $('suggest-preview-confirm').onclick=()=>siteConfirmSuggested(Number(s.suggestion_id),Number(siteId));
  $('suggest-preview-reject').onclick=()=>siteRejectSuggested(Number(s.suggestion_id),Number(siteId));
  $('suggest-preview').classList.add('open');
  suggestionActionBusy('studio',Number(s.suggestion_id),_suggestionActionInflight.has(`studio:${Number(s.suggestion_id)}`));
}
async function siteConfirmSuggested(sid,siteId){
  sid=Number(sid);siteId=Number(siteId);const key=`studio:${sid}`;
  if(!sid||_suggestionActionInflight.has(key))return false;
  _suggestionActionInflight.add(key);suggestionActionBusy('studio',sid,true);suggestionActionStatus('studio',sid,'');
  try{
    const r=await api(`/api/suggestions/${sid}/confirm-entity`,{method:'POST',body:JSON.stringify({entity_type:'studio',entity_id:siteId})});
    if(!suggestionActionSucceeded(r)){
      const reason=r?.detail||r?.message||'The server did not confirm this suggestion.';
      suggestionActionStatus('studio',sid,`Could not confirm: ${reason}`);suggestionPreviewStatus(`Could not confirm: ${reason}`,true);toast(`Could not confirm: ${reason}`,8000);return false;
    }
    updateSuggestionMap('studio',sid);closeSuggestPreview();toast('Studio linked to video.');
    await loadSiteSuggested(siteId,_siteSeq,_siteNavSeq);return true;
  }catch(e){const reason=e?.message||String(e);suggestionActionStatus('studio',sid,`Could not confirm: ${reason}`);suggestionPreviewStatus(`Could not confirm: ${reason}`,true);toast(`Could not confirm: ${reason}`,8000);return false;}
  finally{_suggestionActionInflight.delete(key);suggestionActionBusy('studio',sid,false);}
}
async function siteRejectSuggested(sid,siteId){
  sid=Number(sid);const key=`studio:${sid}`;
  if(!sid||_suggestionActionInflight.has(key))return false;
  _suggestionActionInflight.add(key);suggestionActionBusy('studio',sid,true);suggestionActionStatus('studio',sid,'');
  try{
    const r=await api(`/api/suggestions/${sid}/reject-entity`,{method:'POST',body:JSON.stringify({entity_type:'studio',entity_id:Number(siteId)})});
    if(!suggestionActionSucceeded(r)){
      const reason=r?.detail||r?.message||'The server did not dismiss this studio link.';
      suggestionActionStatus('studio',sid,`Could not reject: ${reason}`);suggestionPreviewStatus(`Could not reject: ${reason}`,true);toast(`Could not reject: ${reason}`,8000);return false;
    }
    updateSuggestionMap('studio',sid);closeSuggestPreview();toast('Not linked to this studio. Kept available for full scene review.');
    await loadSiteSuggested(Number(siteId)||0,_siteSeq,_siteNavSeq);return true;
  }catch(e){const reason=e?.message||String(e);suggestionActionStatus('studio',sid,`Could not reject: ${reason}`);suggestionPreviewStatus(`Could not reject: ${reason}`,true);toast(`Could not reject: ${reason}`,8000);return false;}
  finally{_suggestionActionInflight.delete(key);suggestionActionBusy('studio',sid,false);}
}

async function siteRandomMPC(){
  const list=(_siteVideos||[]).filter(v=>v&&v.id!=null&&v._cid!=null);
  if(!list.length){toast('This studio has no videos in the local catalogue yet.');return;}
  const shuffled=[...list];
  for(let i=shuffled.length-1;i>0;i--){const j=Math.floor(Math.random()*(i+1));[shuffled[i],shuffled[j]]=[shuffled[j],shuffled[i]];}
  _siteRandomQueue=shuffled;
  const btn=$('site-rnd');if(btn)btn.disabled=true;
  toast(`Starting random MPC-HC playlist: ${shuffled.length} videos…`,8000);
  const playlist=shuffled.map(v=>({channel_id:v._cid,message_id:v.id,name:v.title||v.name||`video ${v.id}`}));
  const r=await api('/api/play/external',{method:'POST',body:JSON.stringify({player:'mpc',random:true,playlist})});
  if(btn)btn.disabled=false;
  if(r&&r.ok){
    stopExternalStatusPoll();_externalPid=Number(r.pid)||null;_npMode='external';pollExternalPlayer(_externalPid);
    toast(`MPC-HC random playlist started: ${r.count||shuffled.length} videos${_isRemote()?' (on the PC)':''}`,6000);
  } else toast('MPC-HC could not start: '+((r&&r.detail)||'unknown error'),8000);
}

function renderMetaChannels(){
  const dd=$('m-meta-chan'), chips=$('meta-chips');
  if(!dd||!chips)return;
  const cur=dd.value;
  dd.innerHTML='<option value="">— add channel —</option>'+_sourceChans.map(c=>`<option value="${esc(c.telegram_id)}">${esc(channelDisplayName(c.telegram_id,c.title))}</option>`).join('');
  dd.value=(cur&&!_metaSel.has(String(cur))&&_sourceChans.some(c=>String(c.telegram_id)===String(cur)))?cur:'';
  const selected=_sourceChans.filter(c=>_metaSel.has(String(c.telegram_id)));
  chips.innerHTML=selected.map(c=>`<span class="cp on"><span>${esc(channelDisplayName(c.telegram_id,c.title))}</span><button class="btn xs" style="padding:1px 5px;line-height:1" onclick="removeMetaChannel(${jsa(c.telegram_id)})">×</button></span>`).join('');
}

function removeMetaChannel(cid){ _metaSel.delete(String(cid)); renderMetaChannels(); }




// ═══════════════════════════════════════════════
// SECTION: ADMIN — MODULES
// ═══════════════════════════════════════════════
const MN={METADATA_ASSETS:'Metadata & Assets Manager',METADATA_CHANNEL_PIPELINE:'Metadata & Assets Manager',TELEGRAM_TRANSFER:'Telegram Transfer Manager',METADATA_SYNC:'Metadata match',UPLOAD_LOCAL:'Upload Local Files',MEDIA_MANAGER:'Telegram Media Manager',CONTENT_AUTOMATION:'Content Automation'};


let _automationConfig={};
let _automationSettingsLoaded=false;
let _automationSettingsLoadPromise=null;

async function refreshHardwareProfile(){
  const el=$('hardware-summary'); if(el)el.textContent='Detecting CPU/GPU/RAM/storage…';
  try{
    const r=await api('/api/system/hardware');
    if(!r||r.ok===false){if(el)el.textContent='Hardware detection unavailable.';return;}
    const c=r.cpu||{}, ram=r.ram||{}, gs=r.gpus||[];
    const gpu=gs.map(g=>`${g.name||'GPU'}${(g.hardware_encoders||[]).length?' — '+g.hardware_encoders.join(', '):''}`).join(' | ')||'No dedicated GPU detected';
    const enc=Object.entries(r.verified_encoders||{}).filter(([_,v])=>v).map(([k])=>k).join(', ');
    const ramTotal = Number.isFinite(Number(ram.total_gb)) && Number(ram.total_gb)>0 ? `${ram.total_gb} GB` : 'Unavailable';
    const ramAvail = Number.isFinite(Number(ram.available_gb)) && Number(ram.available_gb)>0 ? `${ram.available_gb} GB` : 'Unavailable';
    const storageFree = Number.isFinite(Number(r.storage?.free_gb)) && Number(r.storage.free_gb)>0 ? `${r.storage.free_gb} GB` : 'Unavailable';
    const kv=(k,v)=>`<div><span>${esc(k)}</span><div>${v}</div></div>`;
    const threads=`${c.physical_cores||'?'} cores / ${c.logical_threads||'?'} threads`;
    if(el){el.classList.remove('muted');el.classList.add('ax-kv','ax-hw-kv');el.innerHTML=kv('CPU',`${esc(c.name||'Unknown')} · ${esc(threads)}`)+kv('GPU',esc(gpu))+kv('RAM',`${esc(ramTotal)} · ${esc(ramAvail)} free`)+kv('Storage',`${esc(storageFree)} free`)+kv('Conversion',`${esc(r.conversion_backend||'libx264')}${enc?' · verified':''}`)+(enc?kv('Hardware encoding',esc(enc)):'');}
  }catch(e){if(el)el.textContent='Hardware detection failed: '+e;}
}
async function runInternetSpeedTest(){
  const st=$('internet-test-status'), out=$('internet-test-result');
  if(st)st.textContent='Testing latency, download and upload…'; if(out)out.textContent='';
  try{
    const r=await api('/api/system/internet-test',{method:'POST'});
    if(!r||r.ok===false)throw new Error(r?.detail||r?.error||'test failed');
    if(st){st.textContent=r.ok?'Test complete':'No network result';st.style.color=r.ok?'var(--green)':'var(--amber)';}
    const fmtMbps=v => (typeof v==='number'&&isFinite(v)) ? `${v} Mbps` : 'Unavailable';
    if(out){out.classList.remove('muted');out.innerHTML=`<div class="ax-kv-label">Internet speed</div><div class="ax-kv"><div><span>Download</span><div>${esc(fmtMbps(r.download_mbps))}</div></div><div><span>Upload</span><div>${esc(fmtMbps(r.upload_mbps))}</div></div><div><span>Latency</span><div>${r.latency_ms==null?'Unavailable':esc(String(r.latency_ms))+' ms'}</div></div><div><span>Route</span><div>${esc(String(r.quality||'unknown'))}</div></div></div><div class="ax-kv-note">Network mode: ${esc(String(r.network_mode||'adaptive'))} · Background capacity: ${esc(String(r.background_capacity??'auto'))}${r.note?' · '+esc(String(r.note)):''}</div>`;}
  }catch(e){if(st){st.textContent='Internet test failed';st.style.color='var(--red)';}if(out)out.textContent=String(e);}
}

function restoreAutomationDestination(){
  const e=$('m-auto-dst');
  if(!e)return;
  const saved=String(_automationConfig?.destination_channel||'').trim();
  if(!saved)return;
  let option=Array.from(e.options||[]).find(o=>String(o.value)===saved);
  if(!option){
    option=document.createElement('option');
    option.value=saved;
    option.textContent=`Saved destination not in current channel list (${saved})`;
    option.dataset.savedAutomationDestination='1';
    e.appendChild(option);
  }
  e.value=saved;
}
async function loadAutomationSettings(force=false){
  if(_automationSettingsLoaded&&!force){
    restoreAutomationDestination();
    return _automationConfig;
  }
  if(_automationSettingsLoadPromise)return _automationSettingsLoadPromise;
  _automationSettingsLoadPromise=(async()=>{
    const r=await api('/api/operations/automation/config');
    if(!r||r._ok===false||r.ok===false)return null;
    _automationConfig=r;
    _automationSettingsLoaded=true;
    const set=(id,v)=>{const e=$(id);if(e)e.value=v==null?'':v;};
    set('m-auto-rss',r.rss_url);set('m-auto-backend','qbittorrent');set('m-auto-qbt-url',r.qbittorrent_url||'http://127.0.0.1:8080');set('m-auto-port',r.torrent_listen_port||6881);
    set('m-auto-engine-path',r.torrent_engine_metadata||'');set('m-auto-path',r.download_path);if($('m-auto-qbt-user'))$('m-auto-qbt-user').value=r.qbittorrent_username||'';if($('m-auto-qbt-pass'))$('m-auto-qbt-pass').value='';if($('m-auto-qbt-key'))$('m-auto-qbt-key').value='';
    const setCredentialState=(id,saved)=>{const e=$(id);if(!e)return;e.className='credential-state '+(saved?'saved':'not-saved');e.innerHTML=`<span class="status-dot ${saved?'good':'idle'}"></span>${saved?'Saved':'Not saved'}`;};
    setCredentialState('m-auto-qbt-user-state',!!r.qbittorrent_username_saved);
    setCredentialState('m-auto-qbt-pass-state',!!r.qbittorrent_password_saved);
    setCredentialState('m-auto-qbt-key-state',!!r.qbittorrent_api_key_saved);
    automationBackendChanged();
    set('m-auto-poll',r.poll_interval||120);set('m-auto-must',r.rss_must_contain||'1080p');set('m-auto-minsize',r.rss_min_size_mb||0);set('m-auto-maxsize',r.rss_max_size_mb||0);
    if($('m-auto-only1080'))$('m-auto-only1080').value=r.only_1080p?'1080p':'any';
    if($('m-auto-rmtorrent'))$('m-auto-rmtorrent').checked=!!r.remove_torrent_after_delivery;
    if($('m-auto-rmlocal'))$('m-auto-rmlocal').checked=!!r.delete_local_after_delivery;if($('m-auto-oversized-policy'))$('m-auto-oversized-policy').value=r.oversized_file_policy==='skip'?'skip':'optimize';
    if($('m-auto-regex'))$('m-auto-regex').checked=!!r.rss_use_regex;
    if($('m-auto-dhtupnp'))$('m-auto-dhtupnp').checked=!!r.torrent_enable_dht_upnp;
    if($('m-auto-incoming'))$('m-auto-incoming').checked=!!r.torrent_allow_incoming_peers;
    restoreAutomationDestination();
    return r;
  })();
  try{return await _automationSettingsLoadPromise;}
  finally{_automationSettingsLoadPromise=null;}
}
function automationForm(){
  return {
    rss_url:($('m-auto-rss')?.value||'').trim(),
    destination_channel:$('m-auto-dst')?.value||'',
    torrent_backend:'qbittorrent',
    qbittorrent_url:($('m-auto-qbt-url')?.value||'http://127.0.0.1:8080').trim(),
    qbittorrent_username:($('m-auto-qbt-user')?.value||'').trim(),
    qbittorrent_password:($('m-auto-qbt-pass')?.value||''),
    qbittorrent_api_key:($('m-auto-qbt-key')?.value||''),
    torrent_listen_port:Math.max(0,parseInt($('m-auto-port')?.value||'6881')||6881),
    torrent_engine_metadata:($('m-auto-engine-path')?.value||'').trim(),
    download_path:($('m-auto-path')?.value||'').trim(),
    poll_interval:Math.max(30,parseInt($('m-auto-poll')?.value||'120')||120),
    max_concurrent_torrents:1,
    rss_must_contain:($('m-auto-must')?.value||'1080p').trim(),
    rss_min_size_mb:Math.max(0,parseInt($('m-auto-minsize')?.value||'0')||0),
    rss_max_size_mb:Math.max(0,parseInt($('m-auto-maxsize')?.value||'0')||0),
    rss_use_regex:!!$('m-auto-regex')?.checked,
    only_1080p:!!(($('m-auto-only1080')?.value||'1080p')==='1080p'),
    remove_torrent_after_delivery:!!$('m-auto-rmtorrent')?.checked,
    delete_local_after_delivery:!!$('m-auto-rmlocal')?.checked,
    oversized_file_policy:$('m-auto-oversized-policy')?.value||'optimize',
    torrent_enable_dht_upnp:!!$('m-auto-dhtupnp')?.checked,
    torrent_allow_incoming_peers:!!$('m-auto-incoming')?.checked
  };
}
async function browseFolderInto(fieldId,title,key,mustExist=false){
  const field=$(fieldId);if(!field)return;
  const msg=fieldId==='u-transfer-folder'?$('u-transfer-folder-msg'):fieldId==='backup-repository'?$('backup-status-msg'):null;
  if(msg)msg.textContent='Opening folder picker…';
  try{
    const r=await api('/api/settings/folder-picker',{method:'POST',body:JSON.stringify({title,initial_path:field.value||'',must_exist:!!mustExist,allow_create:!mustExist})});
    if(r?.cancelled){if(msg)msg.textContent='Folder selection cancelled.';return;}
    if(r?.path){field.value=r.path;if(msg)msg.textContent='Folder selected. Click Save folder to remember it.';}
    else if(msg)msg.textContent='No folder selected.';
  }catch(e){if(msg)msg.textContent=e?.message||'Folder picker unavailable.';else toast(e?.message||'Folder picker unavailable.');}
}
async function saveFolderPreference(fieldId,key,messageId){
  const field=$(fieldId),msg=$(messageId);const path=String(field?.value||'').trim();
  if(!path){if(msg)msg.textContent='Choose a folder first.';return false;}
  if(msg)msg.textContent='Saving folder…';
  try{
    const r=await api('/api/settings/folder-preference',{method:'POST',body:JSON.stringify({key,path})});
    if(!r?.ok)throw new Error(r?.detail||'Could not save folder');
    field.value=r.path||path;if(msg)msg.textContent='Folder saved.';return true;
  }catch(e){if(msg)msg.textContent=e?.message||'Could not save folder.';return false;}
}
async function loadFolderPreference(fieldId,key){
  const field=$(fieldId);if(!field||String(field.value||'').trim())return;
  try{const r=await api('/api/settings/folder-preference?key='+encodeURIComponent(key),{cache:'no-store'});if(r?.ok&&r.path)field.value=r.path;}catch{}
}
async function pickAutomationFolder(){
  const r=await api('/api/operations/automation/pick-folder',{method:'POST',body:JSON.stringify({title:'Select Content Automation download folder',initial_path:$('m-auto-path')?.value||'',allow_create:true})});
  if(r?.cancelled)return;
  if(r?.ok&&r.path){
    const e=$('m-auto-path'); if(e)e.value=r.path;
    $('m-auto-msg').textContent='Download folder selected. Click Save configuration to apply it.';
    return;
  }
  $('m-auto-msg').textContent='Folder selection failed: '+(r?.detail||'unknown error');
}
async function saveAutomationDownloadFolder(){
  const path=($('m-auto-path')?.value||'').trim();
  if(!path){$('m-auto-msg').textContent='Enter or browse to a torrent download folder first.';return null;}
  const r=await api('/api/operations/automation/config',{method:'POST',body:JSON.stringify({download_path:path})});
  if(r.ok){_automationConfig=r.config||_automationConfig;_automationSettingsLoaded=true;$('m-auto-path').value=r.config?.download_path||path;restoreAutomationDestination();$('m-auto-msg').textContent='Torrent download folder saved.';return r;}
  $('m-auto-msg').textContent='Save folder failed: '+(r.detail||'unknown error');
  return r;
}
function automationBackendChanged(){
  const backend=$('m-auto-backend');if(backend)backend.value='qbittorrent';
  const label=$('m-auto-test-engine');if(label)label.textContent='Validate qBittorrent';
}
async function saveAutomationSettings(silent=false){
  const body=automationForm();
  const r=await api('/api/operations/automation/config',{method:'POST',body:JSON.stringify(body)});
  if(r.ok){_automationConfig=r.config||{};_automationSettingsLoaded=false;$('m-auto-msg').textContent='Saved.';await loadAutomationSettings(true);return r;}
  $('m-auto-msg').textContent='Save failed: '+(r.detail||'unknown error');return r;
}
async function testAutomationEngine(){
  const saved=await saveAutomationSettings(true);
  if(!saved||saved.ok===false){$('m-auto-msg').textContent='Save engine selection before testing.';return;}
  const selected=($('m-auto-backend')?.value||'qbittorrent');
  $('m-auto-msg').textContent='Testing qBittorrent...';
  const r=await api('/api/operations/automation/test-engine',{method:'POST'});
  if(r.ok){
    const name='qBittorrent';
    const extra=r.url?` · ${r.url}`:'';
    $('m-auto-msg').textContent=`${name}: ${r.version||'?'} · ${r.mode||'ready'}${extra}${r.session_notes?' · '+r.session_notes:''}`;
    if(selected==='qbittorrent') setQbitConnectionState(true,r.version||'');
  } else {
    $('m-auto-msg').textContent='Download engine test failed: '+(r.detail||'unknown error');
    if(selected==='qbittorrent') setQbitConnectionState(false);
  }
}


function setQbitConnectionState(connected, version=''){
  const el=$('m-auto-qbit-connection'); if(!el)return;
  const dot=el.querySelector('.status-dot'); const label=el.querySelector('span:last-child');
  if(connected){ dot.className='status-dot good'; label.innerHTML=axIcon('check')+' Connected'+(version?` · ${version}`:''); el.title='Archive X ↔ qBittorrent communication verified'; }
  else { dot.className='status-dot bad'; label.textContent='Not connected'; el.title='Archive X could not verify qBittorrent communication'; }
}
async function startAutomation(){
  const draft=automationForm();
  if(!draft.rss_url){$('m-auto-msg').textContent='Enter an RSS feed URL before starting Content Automation.';return;}
  if(!draft.destination_channel){$('m-auto-msg').textContent='Select a destination Telegram channel before starting Content Automation.';return;}
  const saved=await saveAutomationSettings(true);
  if(!saved||saved.ok===false){$('m-auto-msg').textContent='Save configuration before starting.';return;}
  const r=await api('/api/operations/automation/start',{method:'POST'});
  $('m-auto-msg').textContent=r.ok?`Started job ${r.id}.`:'Start failed: '+(r.detail||'unknown error');
  if(r.ok && ($('m-auto-backend')?.value||'qbittorrent')==='qbittorrent') setQbitConnectionState(true,r.version||'');
  if(!r.ok && ($('m-auto-backend')?.value||'qbittorrent')==='qbittorrent') setQbitConnectionState(false);
  refresh();
}
async function stopAutomation(){
  const r=await api('/api/operations/automation/stop',{method:'POST'});
  $('m-auto-msg').textContent=r.ok?'Stopped.':'Stop failed: '+(r.detail||'unknown error');
  refresh();
}

let _axConfirmResolve=null,_axConfirmFocus=null,_axConfirmMode='confirm';
function _openAxDialog(message,options={},mode='confirm',initialValue=''){
  return new Promise(resolve=>{
    const box=$('ax-confirm'),input=$('ax-prompt-input');
    if(!box){resolve(mode==='prompt'?null:false);return;}
    _axConfirmResolve=resolve;_axConfirmFocus=document.activeElement;_axConfirmMode=mode;
    const media=options.media&&typeof options.media==='object'?options.media:null;
    const mediaCard=$('ax-confirm-media'),mediaArt=$('ax-confirm-media-art'),mediaThumb=$('ax-confirm-media-thumb');
    box.classList.toggle('ax-confirm-video-delete',!!media);
    const titleIcon=$('ax-confirm-title-icon');if(titleIcon)titleIcon.hidden=!media;
    if(mediaCard){
      mediaCard.hidden=!media;
      mediaCard.classList.remove('has-image');
      $('ax-confirm-media-title').textContent=String(media?.title||'');
      $('ax-confirm-media-meta').textContent=String(media?.meta||'');
      if(mediaThumb){
        mediaThumb.onload=null;mediaThumb.onerror=null;mediaThumb.removeAttribute('src');
        if(media?.src){
          mediaThumb.onload=()=>{if(mediaThumb.naturalWidth>0)mediaCard.classList.add('has-image');};
          mediaThumb.onerror=()=>mediaCard.classList.remove('has-image');
          mediaThumb.src=String(media.src);
        }
      }
      mediaArt?.classList.toggle('is-unavailable',!media?.src);
    }
    $('ax-confirm-title').textContent=String(options.title||(mode==='prompt'?'Enter a value':'Confirm action'));
    $('ax-confirm-message').textContent=String(message||'');
    $('ax-confirm-ok').textContent=String(options.confirmLabel||options.okLabel||(mode==='prompt'?'Save':'Confirm'));
    $('ax-confirm-cancel').textContent=String(options.cancelLabel||'Cancel');
    $('ax-confirm-ok').classList.toggle('danger',mode==='confirm'&&options.danger!==false);
    $('ax-confirm-ok').classList.toggle('pri',mode==='prompt'||(mode==='confirm'&&options.danger===false));
    if(input){input.style.display=mode==='prompt'?'block':'none';input.value=String(initialValue??'');input.placeholder=String(options.placeholder||'');input.setAttribute('aria-label',String(options.inputLabel||options.title||'Enter a value'));}
    box.classList.toggle('prompting',mode==='prompt');
    box.classList.add('open');box.setAttribute('aria-hidden','false');
    setTimeout(()=>{const target=mode==='prompt'?input:$('ax-confirm-ok');target?.focus();if(mode==='prompt')input?.select();},0);
  });
}
function axConfirm(message,options={}){return _openAxDialog(message,options,'confirm');}
function axPrompt(message,initialValue='',options={}){return _openAxDialog(message,options,'prompt',initialValue);}
function axConfirmClose(value){
  const box=$('ax-confirm');if(!box)return;box.classList.remove('open','prompting','ax-confirm-video-delete');box.setAttribute('aria-hidden','true');
  const mediaCard=$('ax-confirm-media'),mediaArt=$('ax-confirm-media-art'),mediaThumb=$('ax-confirm-media-thumb'),titleIcon=$('ax-confirm-title-icon');
  if(mediaCard){mediaCard.hidden=true;mediaCard.classList.remove('has-image');}
  mediaArt?.classList.remove('is-unavailable');
  if(mediaThumb){mediaThumb.onload=null;mediaThumb.onerror=null;mediaThumb.removeAttribute('src');}
  if(titleIcon)titleIcon.hidden=true;
  const mode=_axConfirmMode,input=$('ax-prompt-input'),r=_axConfirmResolve;_axConfirmResolve=null;_axConfirmMode='confirm';
  try{_axConfirmFocus?.focus?.();}catch(e){};_axConfirmFocus=null;
  if(r)r(mode==='prompt'?(value?String(input?.value??''):null):!!value);
}

async function clearAutomationAll(){
  const button=document.querySelector('[onclick="clearAutomationAll()"]');
  if(!(await axConfirm('Clear all Content Automation queue, qBittorrent automation torrents, download/cache files, engine cache, jobs, and activity? This does not remove Telegram library data.',{title:'Clear all automation data',okLabel:'Clear all',confirmLabel:'Clear all',danger:true})))return;
  if(button)button.disabled=true;
  const msg=$('m-auto-msg');
  if(msg)msg.textContent='Clearing automation queue and cache…';
  try{
    const r=await api('/api/operations/automation/clear-all',{method:'POST',body:'{}'});
    if(r.ok){
      if(msg)msg.textContent=`Cleared ${Number(r.queue_items||0).toLocaleString()} queue item(s), ${Number(r.torrents||0).toLocaleString()} torrent(s), and automation cache.`;
      const status=$('m-auto-status');if(status)status.textContent='Queue and automation cache are clear.';
      refresh();
    }else if(msg){msg.textContent=r.detail||'Could not clear Content Automation.';}
  }finally{
    if(button)button.disabled=false;
  }
}




async function skipAutomationItem(itemKey,title){
  itemKey=String(itemKey||'').trim();
  if(!itemKey)return;
  const label=String(title||itemKey);
  if(!(await axConfirm(`Skip "${label}" and remove its torrent/download? It will not be processed or uploaded.`,{title:'Skip this download',okLabel:'Skip download',danger:false})))return;
  const r=await api('/api/operations/automation/skip-item',{method:'POST',body:JSON.stringify({item_key:itemKey})});
  const msg=$('m-auto-msg');
  if(msg)msg.textContent=r.ok?`Skipped ${r.title||label}.`:'Skip failed: '+(r.detail||'unknown error');
  refresh();
}
async function resumePreviousAutomation(){
  const r=await api('/api/operations/automation/resume',{method:'POST'});
  $('m-auto-msg').textContent=r.ok?'Previous Content Automation task resumed.':'Resume failed: '+(r.detail||'unknown error');
  refresh();
}
async function cancelPreviousAutomation(){
  if(!(await axConfirm('Cancel the previous Content Automation task? The queue and downloaded files will be kept; use Clear all to remove them.',{title:'Cancel previous automation',okLabel:'Cancel task',confirmLabel:'Cancel task',danger:true})))return;
  const r=await api('/api/operations/automation/stop',{method:'POST'});
  $('m-auto-msg').textContent=r.ok?'Previous Content Automation task cancelled.':'Cancel failed: '+(r.detail||'unknown error');
  refresh();
}
function renderAutomationStatus(data){
  const el=$('m-auto-status');if(!el)return;
  const c=data.counts||{};
  const parts=Object.entries(c).map(([k,v])=>`${esc(k)}: ${Number(v).toLocaleString()}`);
  const items=data.items||[];
  const fmtRate=(n)=>{n=Number(n||0);if(!n)return '';const u=['B/s','KB/s','MB/s','GB/s'];let i=0;while(n>=1024&&i<u.length-1){n/=1024;i++;}return `${n.toFixed(i?1:0)} ${u[i]}`;};
  const fmtEta=(n)=>{n=Number(n);if(!Number.isFinite(n)||n<0)return '';const h=Math.floor(n/3600),m=Math.floor((n%3600)/60),s=Math.floor(n%60);return h?`${h}h ${m}m`:m?`${m}m ${s}s`:`${s}s`;};
  const ACTIVE=new Set(['DOWNLOADING','CONVERTING','VALIDATING','METADATA_ASSETS','READY_TO_TRANSFER','TRANSFERRING_VIDEO','TRANSFERRING_THUMBNAIL','VERIFYING']);
  const active=items.filter(x=>ACTIVE.has(x.status));
  const queued=items.filter(x=>x.status==='QUEUED').sort((a,b)=>String(a.created_at||'').localeCompare(String(b.created_at||'')));
  const recent=items.filter(x=>!ACTIVE.has(x.status)&&x.status!=='QUEUED').slice(0,6);

  const progressBar=(pct,tone)=>`<div class="ax-progress"><div class="pb progress-${tone||((Number(pct)||0)>=100?'green':'purple')}" style="width:${Math.max(0,Math.min(100,pct)).toFixed(1)}%"></div></div>`;

  const rule=_automationConfig;
  let html=parts.length?parts.join(' · '):'No releases recorded.';
  const restartPending=data.restart_pending;
  if(restartPending){
    html+=`<div data-ax-live-key="automation-restart-pending" style="margin-top:9px;padding:10px;border:1px solid var(--amber,#e6a23c);border-radius:9px;background:color-mix(in srgb,var(--amber,#e6a23c) 9%,transparent)">
      <div style="font-weight:700">Previous Content Automation task is paused</div>
      <div class="muted" style="font-size:11px;margin-top:3px">The application was closed while it was running. It will not start automatically.</div>
      <div style="display:flex;gap:6px;flex-wrap:wrap;margin-top:8px">
        <button class="btn xs pri ax-icon-btn" onclick="resumePreviousAutomation()"><svg class="ax-icon" aria-hidden="true"><use href="#ax-play"></use></svg> Resume previous task</button>
        <button class="btn xs danger" onclick="cancelPreviousAutomation()">■ Cancel previous task</button>
      </div>
    </div>`;
  }
  const maxDl=Math.max(1,Math.min(10,Number(rule.max_concurrent_torrents||1)));
  const dlCount=items.filter(x=>x.status==='DOWNLOADING').length;
  html+=`<div data-ax-live-key="automation-download-summary" class="muted" style="font-size:11px;margin-top:6px">Downloads: ${dlCount}/${maxDl} · download slots; post-download processing is serialized (Metadata & Assets → Transfer → verify → cleanup)</div>`;
  const runtime=data.runtime||{};
  if(runtime.paused) html += `<div data-ax-live-key="automation-paused" class="muted ax-icon-btn" style="font-size:11px;margin-top:6px"><svg class="ax-icon" aria-hidden="true"><use href="#ax-pause"></use></svg><b>Automation paused by operator</b></div>`;
  if(rule.rss_must_contain||rule.rss_must_not_contain||rule.rss_min_size_mb||rule.rss_max_size_mb){html+=`<div data-ax-live-key="automation-rss-rules" class=\"muted\" style=\"font-size:11px;margin-top:6px\">RSS rule: ${rule.rss_must_contain?`must contain <b>${esc(rule.rss_must_contain)}</b>`:'—'}${rule.rss_must_not_contain?` · must not contain <b>${esc(rule.rss_must_not_contain)}</b>`:''}${rule.rss_min_size_mb?` · min ${Number(rule.rss_min_size_mb).toLocaleString()} MiB`:''}${rule.rss_max_size_mb?` · max ${Number(rule.rss_max_size_mb).toLocaleString()} MiB`:''}</div>`;}

  if(active.length){
    html+=active.map(x=>{
      const downloadPct=Math.max(0,Math.min(100,Number(x.download_progress||0)*100));
      const uploadPct=Math.max(0,Math.min(100,Number(x.upload_progress||0)*100));
      const rate=x.status==='DOWNLOADING'?fmtRate(x.download_rate):'';
      const eta=x.status==='DOWNLOADING'?fmtEta(x.eta_seconds):'';
      const telemetry=[rate,eta?'ETA '+eta:''].filter(Boolean).join(' · ');
      const sending=['TRANSFERRING_VIDEO','TRANSFERRING_THUMBNAIL'].includes(x.status);
      const uploadLabel=uploadPct>=100?'Upload completed':(x.upload_state||'Waiting for upload');
      return `<div data-ax-live-key="automation-active:${esc(String(x.item_key||x.id||x.title||x.status||''))}" style="margin-top:9px;padding:8px;border:1px solid var(--accent);border-radius:8px;background:color-mix(in srgb, var(--accent) 8%, transparent)">
        <div style="font-weight:600;overflow:hidden;text-overflow:ellipsis;white-space:nowrap">${esc(x.title||x.item_key||'')}</div>
        <div class="muted" style="font-size:11px;margin-top:2px">${esc(x.task_label||x.status||'')}${telemetry?' · '+esc(telemetry):''}</div>
        <div style="display:flex;justify-content:space-between;align-items:center;gap:8px;margin-top:7px"><div class="muted" style="font-size:11px">DOWNLOAD · ${downloadPct.toFixed(0)}%</div>${x.status==='DOWNLOADING'?`<button class="btn xs" type="button" data-item-key="${esc(x.item_key||'')}" data-item-title="${esc(x.title||x.item_key||'this torrent')}" onclick="skipAutomationItem(this.dataset.itemKey,this.dataset.itemTitle)">Skip</button>`:''}</div>
        ${progressBar(downloadPct, downloadPct>=100?'green':'purple')}
        ${sending?`<div class="muted" style="font-size:11px;margin-top:7px">UPLOAD · ${uploadPct.toFixed(0)}%${uploadLabel?' · '+esc(uploadLabel):''}</div>${progressBar(uploadPct, uploadPct>=100?'green':'purple')}`:''}
        ${x.status==='CONVERTING'||x.status==='VALIDATING'||x.status==='VERIFYING'||x.status==='CLEANUP'?`<div class="muted" style="font-size:11px;margin-top:7px">PROCESSING · ${esc(x.task_label||x.status)}</div>`:''}
      </div>`;
    }).join('');
  }

  if(queued.length){
    html+=`<div data-ax-live-key="automation-queued-heading" class="muted" style="font-size:11px;margin-top:9px">QUEUED (${queued.length})</div>`+
      queued.slice(0,8).map((x,i)=>`<div data-ax-live-key="automation-queued:${esc(String(x.item_key||x.id||x.title||i))}" style="display:flex;gap:7px;margin:2px 0;align-items:center"><span class="tag">#${i+1}</span><span style="overflow:hidden;text-overflow:ellipsis;white-space:nowrap">${esc(x.title||x.item_key||'')}</span></div>`).join('')+
      (queued.length>8?`<div data-ax-live-key="automation-queued-more" class="muted" style="font-size:11px">+${queued.length-8} more</div>`:'');
  }

  if(recent.length){
    html+=`<div data-ax-live-key="automation-recent-heading" class="muted" style="font-size:11px;margin-top:9px">RECENT</div>`+
      recent.map((x,i)=>`<div data-ax-live-key="automation-recent:${esc(String(x.item_key||x.id||`${x.title||''}:${x.status||''}:${i}`))}" style="display:flex;gap:7px;margin:2px 0"><span class="tag">${esc(x.status||'')}</span><span style="overflow:hidden;text-overflow:ellipsis;white-space:nowrap">${esc(x.title||x.item_key||'')}</span>${x.error?`<span class="muted" style="font-size:11px;overflow:hidden;text-overflow:ellipsis;white-space:nowrap" title="${esc(x.error)}">${esc(x.error)}</span>`:''}</div>`).join('');
  }

  _axPatchLiveMarkup(el,html);
}

let _recoveryStatusPromise=null,_recoveryStatusLastRequest=0;
async function loadRecoveryStatus(force=false){
  const box=$('ops-recovery-status'); if(!box)return;
  const now=Date.now();
  // renderModStatus is driven by every Operations websocket message. Recovery
  // state changes slowly, so coalesce requests and avoid rewriting this panel
  // on every progress tick.
  if(_recoveryStatusPromise)return _recoveryStatusPromise;
  if(!force&&now-_recoveryStatusLastRequest<8000)return;
  _recoveryStatusLastRequest=now;
  _recoveryStatusPromise=(async()=>{
    try{const r=await api('/api/jobs/recovery',{cache:'no-store'}); const x=r.recovery||{};
      const events=Array.isArray(x.events)?x.events.slice(0,5):[];
      const history=events.length?`<div data-ax-live-key="recovery-history" style="margin-top:6px;border-top:1px solid var(--line);padding-top:5px">${events.map((ev,i)=>`<div data-ax-live-key="recovery-event:${esc(String(ev.id||ev.job_id||`${ev.event_type||'EVENT'}:${ev.operation||''}:${ev.status||''}:${i}`))}" class="muted" style="font-size:10.5px;line-height:1.45"><b>${esc(ev.event_type||'EVENT')}</b>${ev.status?` · ${esc(ev.status)}`:''}${ev.operation?` · ${esc(ev.operation)}`:''}${ev.job_id?` · ${esc(ev.job_id)}`:''}</div>`).join('')}</div>`:'<div data-ax-live-key="recovery-history-empty" class="muted" style="font-size:10.5px;margin-top:6px">No journal events yet</div>';
      _axPatchLiveMarkup(box,`<div data-ax-live-key="recovery-panel" class="ax-recovery-panel"><b data-ax-live-key="recovery-title">Recovery</b><div data-ax-live-key="recovery-counts" class="muted" style="font-size:11px;margin-top:4px">${Number(x.interrupted||0)} interrupted · ${Number(x.recovering||0)} recovering · ${Number(x.pending_idempotency||0)} pending operations</div><div data-ax-live-key="recovery-launch" class="muted" style="font-size:10.5px;margin-top:3px">Launch ${esc(x.launch_id||'')} · ${Number(x.resource_leases||0)} active resource leases</div>${history}</div>`);
    }catch(e){_axPatchLiveMarkup(box,'<span data-ax-live-key="recovery-error">Recovery status unavailable</span>');}
  })();
  try{return await _recoveryStatusPromise;}finally{_recoveryStatusPromise=null;}
}

const _jobControlInFlight=new Set();
async function _jobControl(id,action,path,options={}){
  const key=String(id||'');
  if(!key||_jobControlInFlight.has(key))return;
  _jobControlInFlight.add(key);
  try{
    const r=await api('/api/jobs/'+encodeURIComponent(key)+path,{method:'POST',cache:'no-store'});
    if(!r||r._ok===false||r.ok===false){toast(r?.detail||options.error||`Could not ${action.toLowerCase()} job`);return;}
    if(action==='Pause')toast(r.status==='PAUSED'?'Job paused.':'Pause requested; the current safe operation will finish first.');
    else if(action==='Resume')toast(`Job ${String(r.status||'resuming').toLowerCase()}.`);
    else if(action==='Stop')toast(r.status==='CANCELLED'?'Job stopped.':'Stop requested; Archive X is finishing cancellation safely.');
  }catch(e){toast(e?.message||`Could not ${action.toLowerCase()} job`);}
  finally{
    _jobControlInFlight.delete(key);
    await refresh();
    void refreshTransferLanes(true);
  }
}
async function jobPause(id){return _jobControl(id,'Pause','/pause',{error:'Could not pause job'});}
async function jobResume(id){return _jobControl(id,'Resume','/resume',{error:'Could not resume job'});}
async function jobSkip(id){if(!(await axConfirm('Skip the current file and continue with the next file?',{title:'Skip current file',okLabel:'Skip',danger:false})))return;const r=await api('/api/jobs/'+encodeURIComponent(id)+'/skip-current',{method:'POST',cache:'no-store'});if(!r||r._ok===false||r.ok===false)toast(r?.detail||'Could not skip current file');refresh();}
async function stopPlaybackJob(){
  if(!(await axConfirm('Stop the current playback session? Active range requests will be cancelled and cleanup will finish in the background.',{title:'Stop playback',okLabel:'Stop playback',confirmLabel:'Stop playback',danger:true})))return;
  // Stop the browser element immediately; the API detaches the engine and
  // schedules slower Telegram/cache teardown outside the request path.
  try{pStop();}catch(_){ }
  toast('Stopping playback…');
  const r=await api('/api/play/stop',{method:'POST'});
  if(!r||r._ok===false||r.detail){toast(r?.detail||'Playback stop was requested, but cleanup status is unavailable.');refresh();return;}
  toast(r.stopping?'Playback stopped; resource cleanup is finishing.':'Playback stopped.');
  refresh();
}
async function jobCancel(id){
  const key=String(id||'');
  if(!key||_jobControlInFlight.has(key))return;
  const current=await api('/api/jobs/'+encodeURIComponent(key),{cache:'no-store'});
  if(!current||current._ok===false){toast(current?.detail||'Could not read current job state');return;}
  const st=String(current?.status||'').trim().toUpperCase();
  if(['COMPLETED','COMPLETED_WITH_ERRORS','FAILED','CANCELLED'].includes(st)){toast(`Job is already ${st.toLowerCase().replaceAll('_',' ')}.`);await refresh();return;}
  const interrupted=st==='INTERRUPTED'||st==='RECOVERING';
  const message=interrupted
    ? 'Stop this interrupted job and discard its pending recovery? Saved checkpoints remain in the journal.'
    : 'Stop this active job? Archive X will cancel it cooperatively first; if it does not stop within the grace period, the job supervisor will escalate cancellation. Verified completed items remain saved.';
  if(!(await axConfirm(message,{title:interrupted?'Stop interrupted job':'Stop job',okLabel:'Stop job',confirmLabel:'Stop job',danger:true})))return;
  return _jobControl(key,'Stop','/cancel',{error:'Could not stop job'});
}
async function jobRefresh(id){
  const key=String(id||'');
  if(!key)return;
  // Refresh is strictly read-only. Recovery is a separate, explicit action;
  // it must never be triggered by clicking Refresh on a PAUSING/failed job.
  const current=await api('/api/jobs/'+encodeURIComponent(key),{cache:'no-store'});
  if(!current||current._ok===false){toast(current?.detail||'Could not refresh job status');return;}
  await refresh();
  // /api/status can already have been in-flight when Refresh was clicked.
  // Merge the direct, no-store job read into the targeted card so the control
  // reflects the response we just received rather than that older poll.
  const latestStatus=String(current.status||'').toUpperCase();
  if(_opsState&&Array.isArray(_opsState.jobs)){
    const mapped=_opsState.jobs.map(j=>String(j.id||j.job_id||'')===key?{
      ...j,
      status:latestStatus||j.status,
      operation:current.current_operation??j.operation,
      item:current.current_item??j.item,
      current_item:current.current_item??j.current_item,
      progress:current.progress??j.progress,
      completed:current.completed??j.completed,
      total:current.total??j.total,
      error:current.error??j.error,
      worker_state:current.worker_state??j.worker_state,
      watchdog_state:current.watchdog_state??j.watchdog_state,
      watchdog_reason:current.watchdog_reason??j.watchdog_reason,
      updated_at:current.updated_at??j.updated_at,
    }:j);
    _opsState={..._opsState,jobs:mapped};
    renderJobs(mapped);
    renderModStatus(_opsState);
  }
  void refreshTransferLanes(true);
  return current;
}
async function jobRecover(id){
  const r=await api('/api/jobs/'+encodeURIComponent(id)+'/recover',{method:'POST'});
  if(!r.ok)toast(r.detail||'Job could not be recovered');
  else toast('Job recovered and queued from its saved state.');
  refresh();
}



function fillModSels(){
  const ids=['m-send-target','m-upload-dst','m-auto-dst','m-suggest-channel'];
  const opts='<option value="">—</option>'+_sourceChans.map(c=>`<option value="${esc(c.telegram_id)}">${esc(channelDisplayName(c.telegram_id,c.title))}</option>`).join('');
  ids.forEach(id=>{const e=$(id);if(e){const v=e.value;e.innerHTML=opts;e.value=v;}});
  restoreAutomationDestination();
  fillSuggestionScanPerformers();
  fillSuggestionScanSites();
  fillUnifiedMetadataAssets();
  fillUnifiedTelegramChannelSelects();
  toggleSuggestionScanMode();
}
function fillSuggestionScanPerformers(){
  const e=$('m-suggest-performer');
  if(!e||!Array.isArray(_suggestionPerformers))return;
  const v=e.value;
  e.innerHTML='<option value="all">All performers</option><option value="">— select performer —</option>'+_suggestionPerformers.map(p=>`<option value="${Number(p.id)}">${esc(p.name||('Performer #'+p.id))} · ${Number(p.video_count||0)} videos</option>`).join('');
  e.value=v;
}
function fillSuggestionScanSites(){
  const e=$('m-suggest-site');
  if(!e||!Array.isArray(_suggestionSites))return;
  const v=e.value;
  e.innerHTML='<option value="all">All studios</option><option value="">— select studio —</option>'+_suggestionSites.map(s=>`<option value="${Number(s.id)}">${esc(s.name||('Studio #'+s.id))} · ${Number(s.video_count||0)} videos</option>`).join('');
  e.value=v;
}
function toggleSuggestionScanMode(){
  const channel=!!$('m-scope-channel')?.checked;
  const performer=!!$('m-scope-performer')?.checked;
  const site=!!$('m-scope-site')?.checked;
  const cw=$('m-suggest-channel-wrap'),pw=$('m-suggest-performer-wrap'),sw=$('m-suggest-site-wrap'),fw=$('m-suggest-force-wrap'),help=$('m-suggest-help');
  if(!cw||!pw||!sw||!fw||!help)return;
  cw.style.display=channel?'block':'none';
  pw.style.display=performer?'block':'none';
  sw.style.display=site?'block':'none';
  fw.style.display=channel?'flex':'none';
  help.textContent=channel&& (performer||site)
    ?'Each selected video is processed once. Performer/studio matches are forced rechecks; channel-only matches remain suggestion-only. The channel Force option applies only to channel-only videos.'
    :performer||site
      ?'Selected performer/studio videos are forced through the existing metadata/provider pipeline. HIGH matches may be applied; MEDIUM/LOW matches become suggestions.'
      :'Channel-only scan creates performer/studio suggestions and never silently applies a HIGH match.';
}

// ── SECTION: Unified Telegram Transfer + Metadata & Assets managers ──
let _uTelegramChannels=new Set();
function renderUnifiedTelegramChannels(){
  const box=$('u-tg-channel-list'),summary=$('u-tg-channel-summary'); if(!box)return;
  const q=String($('u-tg-channel-filter')?.value||'').trim().toLowerCase();
  const visible=_sourceChans.filter(c=>!q||channelDisplayName(c.telegram_id,c.title).toLowerCase().includes(q)||String(c.username||'').toLowerCase().includes(q)||String(c.telegram_id||'').includes(q));
  box.innerHTML=visible.map(c=>{const id=String(c.telegram_id), checked=_uTelegramChannels.has(id);return `<label style="display:flex;align-items:center;gap:7px;padding:5px 6px;cursor:pointer"><input type="checkbox" ${checked?'checked':''} onchange="toggleUnifiedTelegramChannel(${jsa(id)},this.checked)" style="width:auto"><span style="flex:1;min-width:0;overflow:hidden;text-overflow:ellipsis;white-space:nowrap">${esc(channelDisplayName(id,c.title))}</span></label>`;}).join('')||'<div class="muted" style="padding:7px;font-size:11px">No channels match.</div>';
  if(summary)summary.textContent=`Selected: ${_uTelegramChannels.size} channel${_uTelegramChannels.size===1?'':'s'}`;
}
function toggleUnifiedTelegramChannel(id,on){const k=String(id);if(on)_uTelegramChannels.add(k);else _uTelegramChannels.delete(k);renderUnifiedTelegramChannels();}
function selectAllUnifiedTelegramChannels(on){if(on)_sourceChans.forEach(c=>_uTelegramChannels.add(String(c.telegram_id)));else _uTelegramChannels.clear();renderUnifiedTelegramChannels();}
function fillUnifiedTelegramChannelSelects(){
  renderUnifiedTelegramChannels();
  const opts='<option value="">— select channel —</option>'+_sourceChans.map(c=>`<option value="${esc(c.telegram_id)}">${esc(channelDisplayName(c.telegram_id,c.title))}</option>`).join('');
  ['u-transfer-local-dst','u-tg-destination','u-tg-trash'].forEach(id=>{const e=$(id);if(e){const v=e.value;e.innerHTML=opts;e.value=v;}});
}
function toggleUnifiedTelegramCleanup(){const on=!!$('u-tg-cleanup')?.checked;if($('u-tg-trash-wrap'))$('u-tg-trash-wrap').style.display=on?'block':'none';}
function unifiedLocalMediaTypeChanged(){
  const images=!!$('u-transfer-local-images')?.checked;
  // The legacy local shell is hidden once the step-based wizard is built.
  // Apply mode styling to the visible Options & Checks wrapper instead.
  const localPane=$('u-transfer-local');
  const visibleLocalOptions=$('ax-transfer-local-options');
  if(localPane)localPane.classList.toggle('ax-transfer-images-mode',images);
  if(visibleLocalOptions)visibleLocalOptions.classList.toggle('ax-transfer-images-mode',images);
  const start=$('u-transfer-start');
  if(start)start.textContent=images?'Upload images →':'Inspect & Prepare →';
}
function unifiedTransferModeChanged(){
  const local=($('u-transfer-source')?.value||'local')==='local';
  const localPane=$('u-transfer-local'), telegramPane=$('u-transfer-telegram');
  // Use a dedicated class instead of inline display values: the Transfers layout uses
  // grid !important for these panels, which otherwise overrides style.display='none'.
  if(localPane)localPane.classList.toggle('ax-transfer-mode-hidden',!local);
  if(telegramPane)telegramPane.classList.toggle('ax-transfer-mode-hidden',local);
  if($('u-transfer-console'))$('u-transfer-console').style.display='none';
  if(!local)fillUnifiedTelegramChannelSelects();
  unifiedLocalMediaTypeChanged();
  const images=!!$('u-transfer-local-images')?.checked;
  const msg=$('u-transfer-msg');if(msg)msg.textContent=local?(images?'Ready: local images will be uploaded as Telegram documents, verified, and indexed in Photos.':'Ready: local videos will be prepared, saved, then sent.'):'Select source/destination and processing rules, then Inspect & Prepare.';
}
async function startUnifiedTransfer(){
  const source=$('u-transfer-source')?.value||'local';
  if(source==='local'){
    const folder=($('u-transfer-folder')?.value||'').trim(),dst=$('u-transfer-local-dst')?.value||'';
    const imageUpload=!!$('u-transfer-local-images')?.checked;
    if(!folder){const text='Enter a source folder.';const msg=$('u-transfer-msg');if(msg)msg.textContent=text;toast(text);return;}
    if(!dst){const text='Choose a destination channel.';const msg=$('u-transfer-msg');if(msg)msg.textContent=text;toast(text);return;}
    let includeSubfolders=!!$('u-transfer-subfolders')?.checked;
    let imageCount=null;
    if(imageUpload){
      const scan=await api('/api/telegram-transfer/scan-photos',{method:'POST',body:JSON.stringify({folder})});
      if(!scan||scan._ok===false||typeof scan.direct_count!=='number'){
        const text='Could not scan the selected photo folder: '+(scan?.detail||'folder scan failed. No upload was queued.');
        const msg=$('u-transfer-msg');if(msg)msg.textContent=text;toast(text,6000);return;
      }
      const directCount=Number(scan.direct_count||0),nestedCount=Number(scan.nested_count||0);
      if(!includeSubfolders&&nestedCount>0){
        const nestedMessage=directCount===0
          ? `No supported photos were found directly in the selected folder, but ${nestedCount} supported photo(s) were found in subfolders. Include subfolders and upload them? Click Cancel to stop.`
          : `Folder scan found ${directCount} supported photo(s) directly in the selected folder and ${nestedCount} more in subfolders. Click OK to include subfolders; click Cancel to upload only the ${directCount} directly listed photo(s).`;
        if(window.confirm(nestedMessage)){
          includeSubfolders=true;
          const checkbox=$('u-transfer-subfolders');if(checkbox)checkbox.checked=true;
        }else if(directCount===0){
          const text=`No upload queued. ${nestedCount} supported photo(s) are in subfolders; enable Include subfolders to send them.`;
          const msg=$('u-transfer-msg');if(msg)msg.textContent=text;toast(text,6000);return;
        }
      }
      imageCount=includeSubfolders?Number(scan.recursive_count||0):directCount;
      if(imageCount===0){
        const extMap=scan.recursive_extensions||{};
        const seen=Object.entries(extMap).sort((a,b)=>b[1]-a[1]).slice(0,8).map(([ext,count])=>`${ext}: ${count}`).join(', ')||'none';
        const text=`No supported photos detected in ${folder}. The scan inspected ${Number(scan.recursive_file_count||0)} file(s). Extensions seen: ${seen}. No upload was queued.`;
        const msg=$('u-transfer-msg');if(msg)msg.textContent=text;toast(text,7000);return;
      }
    }
    const payload={source:'local',media_type:imageUpload?'PHOTOS':'VIDEO',folder,destination_channel:dst,include_subfolders:includeSubfolders,delete_local_after_delivery:$('u-transfer-delete-source')?.checked!==false};
    if(!imageUpload){Object.assign(payload,{resolution_filter:$('u-transfer-local-resolution')?.value||'ANY',orientation:$('u-transfer-local-orientation')?.value||'BOTH',oversized_file_policy:$('u-transfer-oversized-policy')?.value||'optimize'});}
    const r=await api('/api/telegram-transfer/start',{method:'POST',body:JSON.stringify(payload)});
    const text=r.id?(imageUpload?`Image document upload queued (${imageCount} supported photo(s) detected). Already-delivered Telegram documents may be verified and reused instead of uploaded again; verified images are indexed in Photos.`:'Local preparation/transfer queued.'):'Could not start: '+(r.detail||'unknown error');const msg=$('u-transfer-msg');if(msg)msg.textContent=text;toast(text,5000);refresh();return;
  }
  const channels=[..._uTelegramChannels];const dst=$('u-tg-destination')?.value||'';
  if(!channels.length){const text='Select at least one source channel.';const msg=$('u-transfer-msg');if(msg)msg.textContent=text;toast(text);return;}
  if(!dst){const text='Choose a destination channel.';const msg=$('u-transfer-msg');if(msg)msg.textContent=text;toast(text);return;}
  if(channels.includes(String(dst))){const text='Destination cannot be one of the selected source channels.';const msg=$('u-transfer-msg');if(msg)msg.textContent=text;toast(text);return;}
  const cleanup=!!$('u-tg-cleanup')?.checked,trash=$('u-tg-trash')?.value||'';
  if(cleanup&&!trash){const text='Choose a trash channel when cleanup is enabled.';const msg=$('u-transfer-msg');if(msg)msg.textContent=text;toast(text);return;}
  const payload={source:'telegram',source_channels:channels,destination_channel:dst,source_mode:$('u-tg-source-media')?.value||'VIDEO',destination_mode:$('u-tg-destination-media')?.value||'VIDEO',orientation:$('u-tg-orientation')?.value||'BOTH',resolution_filter:$('u-tg-resolution')?.value||'ANY',duplicate_check:{source:!!$('u-tg-dup-source')?.checked,destination:!!$('u-tg-dup-destination')?.checked,database:!!$('u-tg-dup-db')?.checked},cleanup:{enabled:cleanup,trash_channel:trash}};
  const r=await api('/api/telegram-transfer/start',{method:'POST',body:JSON.stringify(payload)});
  const text=r.id?'Telegram media inspection/preparation queued.':'Could not start: '+(r.detail||'unknown error');const msg=$('u-transfer-msg');if(msg)msg.textContent=text;toast(text,5000);refresh();
}


function renderMetadataAssetChannels(){
  const box=$('u-assets-channel-list'),summary=$('u-assets-channel-summary');
  if(!box)return;
  const q=String($('u-assets-channel-filter')?.value||'').trim().toLowerCase();
  const visible=_sourceChans.filter(c=>!q||channelDisplayName(c.telegram_id,c.title).toLowerCase().includes(q)||String(c.username||'').toLowerCase().includes(q)||String(c.telegram_id||'').includes(q));
  const html=visible.map(c=>{
    const id=String(c.telegram_id), checked=_uMetadataChannels.has(id);
    const count=Number(c.video_count||c.videos_count||c.count||0);
    const channelName=channelDisplayName(id,c.title);
    return `<label data-ax-live-key="metadata-channel:${esc(id)}" style="display:flex;align-items:center;gap:7px;padding:6px 7px;border-radius:5px;cursor:pointer" title="${esc(channelName)}"><input type="checkbox" ${checked?'checked':''} onchange="toggleMetadataAssetChannel(${jsa(id)},this.checked)" style="width:auto"><span style="flex:1;min-width:0;overflow:hidden;text-overflow:ellipsis;white-space:nowrap">${esc(channelName)}</span>${count?`<span class="muted" style="font-size:10.5px">${count.toLocaleString()} videos</span>`:''}</label>`;
  }).join('') || '<div data-ax-live-key="metadata-channel-empty" class="muted" style="padding:8px;font-size:11px">No channels match the filter.</div>';
  _axPatchLiveMarkup(box,html);
  if(summary)summary.textContent=`Selected: ${_uMetadataChannels.size} channel${_uMetadataChannels.size===1?'':'s'}`;
}
function toggleMetadataAssetChannel(id,on){
  const key=String(id);if(on)_uMetadataChannels.add(key);else _uMetadataChannels.delete(key);renderMetadataAssetChannels();
}
function selectAllMetadataAssetChannels(on){
  if(on)_sourceChans.forEach(c=>_uMetadataChannels.add(String(c.telegram_id)));else _uMetadataChannels.clear();
  renderMetadataAssetChannels();
}
function fillUnifiedMetadataAssets(){
  renderMetadataAssetChannels();
  unifiedTransferModeChanged();
}
let _metadataChannelStartInFlight=false;
function metadataChannelName(id){const c=_sourceChans.find(x=>String(x.telegram_id)===String(id));return channelDisplayName(id,c?.title);}
function syncMetadataAssetScope(value){
  const all=$('u-assets-process-all'),limit=$('u-assets-process-limit');
  if(!all||!limit)return;
  const scope=String(value||'all');
  if(scope==='all'){all.checked=true;return;}
  const n=Number(scope);
  if([25,50,100,250].includes(n)){limit.value=String(n);all.checked=false;}
}

const METADATA_ENTITY_STAGE_IDS = Object.freeze(['metadata','performers','channel_data']);
const METADATA_ASSET_STAGE_ORDER = Object.freeze(['inventory','metadata','performers','channel_data','snapshots']);
function syncMetadataEntityStageSelection(){
  const enabled=!!$('u-assets-stage-metadata-entities')?.checked;
  ['u-assets-stage-metadata','u-assets-stage-performers','u-assets-stage-channel'].forEach(id=>{
    const legacy=$(id); if(legacy)legacy.checked=enabled;
  });
}
function syncFullInventorySelection(){
  // The combined reconciliation option is explicit authorization to scan
  // older Telegram history, so Inventory must be enabled for the option to
  // have the visible behavior promised by its label.
  if($('u-assets-full-inventory')?.checked){
    const inventory=$('u-assets-stage-inventory');
    if(inventory)inventory.checked=true;
  }
}
function expandMetadataEntityStages(stages){
  const selected=new Set(stages||[]);
  if(METADATA_ENTITY_STAGE_IDS.some(stage=>selected.has(stage)))METADATA_ENTITY_STAGE_IDS.forEach(stage=>selected.add(stage));
  return METADATA_ASSET_STAGE_ORDER.filter(stage=>selected.has(stage));
}
function metadataStageLabels(stages){
  const labels=[];
  for(const stage of expandMetadataEntityStages(stages)){
    const label=metadataStageLabel(stage);
    if(!labels.includes(label))labels.push(label);
  }
  return labels;
}

async function runUnifiedMetadataAssets(){
  syncMetadataEntityStageSelection();
  syncFullInventorySelection();
  const msg=$('u-assets-process-msg'),button=$('u-assets-run-selected');
  if(_metadataChannelStartInFlight)return;
  const channels=[..._uMetadataChannels].map(v=>String(v||'').trim()).filter(Boolean);
  if(!channels.length){if(msg)msg.textContent='Select at least one channel.';toast('Select at least one channel.');return;}
  const stages=[];
  if($('u-assets-stage-inventory')?.checked)stages.push('inventory');
  if($('u-assets-stage-metadata-entities')?.checked)stages.push(...METADATA_ENTITY_STAGE_IDS);
  if($('u-assets-stage-snapshots')?.checked)stages.push('snapshots');
  const imageScan=!!$('u-assets-stage-image-scan')?.checked;
  const photoReconcile=!!$('u-assets-stage-photo-reconcile')?.checked;
  if(!stages.length&&!imageScan&&!photoReconcile){if(msg)msg.textContent='Select at least one processing stage.';toast('Select at least one processing stage.');return;}
  const all=!!$('u-assets-process-all')?.checked;
  const limit=Math.max(25,parseInt($('u-assets-process-limit')?.value||'50')||50);
  const forceMetadata=!!$('u-assets-force-metadata')?.checked;
  const forceEntity=!!$('u-assets-force-entity')?.checked;
  const fullInventory=!!$('u-assets-full-inventory')?.checked;
  _metadataChannelStartInFlight=true;
  if(button){button.disabled=true;button.dataset.busy='1';button.textContent='Starting…';}
  let metadataResult=null;
  let metadataDescription='';
  try{
    // The Image scan is a separate read-only Telegram-history job. It is an
    // optional stage in this workflow but does not belong to video prerequisites.
    if(stages.length){
      const preflight=await api('/api/metadata-assets/preflight',{method:'POST',body:JSON.stringify({channel_ids:channels,stages})});
      if(!preflight.ok){
        const text='Could not validate prerequisites: '+(preflight.detail||'unknown error');if(msg)msg.textContent=text;toast(text,6500);
        return;
      }
      let runStages=stages.slice();
      let autoAddedPrerequisites={};
      if(!preflight.ready){
        const labels=metadataStageLabels(preflight.required_stages||[]).join(', ');
        const affected=(preflight.missing_by_channel||[]).map(x=>String(x.channel_id)).join(', ');
        const prompt='The selected stages require persisted prerequisite data that is not available yet.\n\nRequired stage(s): '+labels+'\nAffected channel(s): '+affected+'\n\nAdd the required stage(s) before running the selected stages?';
        if(!(await axConfirm(prompt,{title:'Add required stages',okLabel:'Add stages and continue',danger:false})))return;
        const stageIds={inventory:'u-assets-stage-inventory',metadata:'u-assets-stage-metadata-entities',performers:'u-assets-stage-metadata-entities',channel_data:'u-assets-stage-metadata-entities',snapshots:'u-assets-stage-snapshots'};
        (preflight.required_stages||[]).forEach(stage=>{const el=$(stageIds[stage]);if(el)el.checked=true;});
        syncMetadataEntityStageSelection();
        (preflight.missing_by_channel||[]).forEach(item=>{(item.missing||[]).forEach(stage=>{if(!autoAddedPrerequisites[stage])autoAddedPrerequisites[stage]=[];autoAddedPrerequisites[stage].push(String(item.channel_id));});});
        runStages=expandMetadataEntityStages([...preflight.required_stages,...stages]);
        if(msg)msg.textContent='Required prerequisite options added: '+metadataStageLabels(preflight.required_stages||[]).join(', ')+'.';
      }
      const r=await api('/api/metadata-assets/process-channel',{method:'POST',body:JSON.stringify({channel_ids:channels,stages:runStages,limit,unbounded:all,force_metadata:forceMetadata,force_entity:forceEntity,full_inventory:fullInventory,reconcile_uploaded_captions:fullInventory,auto_added_prerequisites:autoAddedPrerequisites})});
      if(!r.ok){const text='Could not start Metadata & Assets: '+(r.detail||'unknown error');if(msg)msg.textContent=text;toast(text,7000);return;}
      metadataResult=r;
      metadataDescription=r.already_running?(r.message||'A Metadata & Assets job is already running.'):`Metadata & Assets queued${r.id?' as job '+r.id:''}: ${metadataStageLabels(runStages).join(' → ')}${fullInventory?' → older-message + uploaded-caption reconciliation':''}`;
      if(!r.already_running){const forceToggle=$('u-assets-force-entity');if(forceToggle)forceToggle.checked=false;}
    }

    let imageScanResult=null;
    let photoReconcileResult=null;
    if(imageScan&&photoReconcile){
      // One job ensures channel indexing completes before reconciliation checks
      // the exact newly indexed message IDs. No concurrent scan/reconcile race.
      photoReconcileResult=await api('/api/photos/reconcile',{method:'POST',body:JSON.stringify({channel_ids:channels,also_scan:true})});
      if(!photoReconcileResult||photoReconcileResult._ok===false||!photoReconcileResult.id){
        const failure='Image scan + reconciliation could not be queued: '+(photoReconcileResult?.detail||'unknown error');
        const text=metadataDescription?metadataDescription+'. '+failure:failure;
        if(msg)msg.textContent=text;toast(text,7000);refresh();return;
      }
      imageScanResult=photoReconcileResult;
    }else if(imageScan){
      imageScanResult=await api('/api/photos/scan',{method:'POST',body:JSON.stringify({channel_ids:channels})});
      if(!imageScanResult||imageScanResult._ok===false||!imageScanResult.id){
        const failure='Image scan could not be queued: '+(imageScanResult?.detail||'unknown error');
        const text=metadataDescription?metadataDescription+'. '+failure:failure;
        if(msg)msg.textContent=text;toast(text,7000);refresh();return;
      }
    }else if(photoReconcile){
      photoReconcileResult=await api('/api/photos/reconcile',{method:'POST',body:JSON.stringify({channel_ids:channels})});
      if(!photoReconcileResult||photoReconcileResult._ok===false||!photoReconcileResult.id){
        const failure='Photo reconciliation could not be queued: '+(photoReconcileResult?.detail||'unknown error');
        const text=metadataDescription?metadataDescription+'. '+failure:failure;
        if(msg)msg.textContent=text;toast(text,7000);refresh();return;
      }
    }
    const pieces=[];
    if(metadataDescription)pieces.push(metadataDescription);
    if(imageScanResult&&photoReconcileResult?.also_scan)pieces.push(`Image scan followed by deleted-image reconciliation queued as job ${imageScanResult.id} for ${channels.length} selected channel${channels.length===1?'':'s'}.`);
    else if(imageScanResult)pieces.push(`Image scan queued as job ${imageScanResult.id} for ${channels.length} selected channel${channels.length===1?'':'s'}; it scans full Telegram history without modifying messages.`);
    if(photoReconcileResult&&!photoReconcileResult.also_scan)pieces.push(`Deleted Telegram image reconciliation queued as job ${photoReconcileResult.id}; it checks already-indexed message IDs only and does not rescan channel history.`);
    const launchText=pieces.join('. ');
    if(msg)msg.textContent=launchText;
    toast(launchText,7000);
    refresh();
  }catch(e){
    const text=(metadataDescription?metadataDescription+'. ':'')+'Could not start '+(imageScan&&!metadataResult?'Image scan':'the selected workflow')+': '+(e?.message||String(e));
    if(msg)msg.textContent=text;toast(text,7000);
  }finally{
    _metadataChannelStartInFlight=false;
    if(button){button.disabled=false;button.dataset.busy='0';button.textContent='Run ';}
  }
}
async function processPendingLibraryItems(){
  syncMetadataEntityStageSelection();
  const msg=$('u-assets-process-msg'),button=$('u-assets-process-pending');
  if(_metadataChannelStartInFlight)return;
  const channels=[..._uMetadataChannels].map(v=>String(v||'').trim()).filter(Boolean);
  if(!channels.length){if(msg)msg.textContent='Select at least one channel.';toast('Select at least one channel.');return;}
  const stages=[];
  if($('u-assets-stage-metadata-entities')?.checked)stages.push(...METADATA_ENTITY_STAGE_IDS);
  if($('u-assets-stage-snapshots')?.checked)stages.push('snapshots');
  if(!stages.length){const text='Select at least one processing stage (Inventory is intentionally skipped).';if(msg)msg.textContent=text;toast(text);return;}
  const all=!!$('u-assets-process-all')?.checked;
  const limit=Math.max(25,parseInt($('u-assets-process-limit')?.value||'50')||50);
  const forceMetadata=!!$('u-assets-force-metadata')?.checked;
  const forceEntity=!!$('u-assets-force-entity')?.checked;
  _metadataChannelStartInFlight=true;
  if(button){button.disabled=true;button.dataset.busy='1';button.textContent='Starting…';}
  try{
    const r=await api('/api/metadata-assets/process-pending',{method:'POST',body:JSON.stringify({channel_ids:channels,stages,limit,unbounded:all,force_metadata:forceMetadata,force_entity:forceEntity})});
    const launchText=!r.ok?'Could not start: '+(r.detail||'unknown error'):r.no_pending?(r.message||'No pending Library items.'):r.already_running?(r.message||'A Metadata & Assets job is already running.'):r.snapshot_audit?(r.message||`Checking local snapshot files for ${Number(r.queued_channels||channels.length)} channel(s).`):`Processing saved pending items in ${Number(r.queued_channels||channels.length)} channel${Number(r.queued_channels||channels.length)===1?'':'s'} without reading Telegram history.`;
    if(msg)msg.textContent=launchText;
    toast(launchText,r.ok?5000:7000);
    if(r.ok&&!r.already_running&&!r.no_pending){const forceToggle=$('u-assets-force-entity');if(forceToggle)forceToggle.checked=false;}
    refresh();
  }catch(e){const text='Could not start: '+(e?.message||String(e));if(msg)msg.textContent=text;toast(text,7000);}
  finally{_metadataChannelStartInFlight=false;if(button){button.disabled=false;button.dataset.busy='0';button.textContent='Run Pending (no scan)';}}
}
function metadataStageLabel(stage){return ({inventory:'Inventory',metadata:'Metadata & Entity Data',performers:'Metadata & Entity Data',channel_data:'Metadata & Entity Data',snapshots:'Snapshots'})[stage]||stage;}

async function retryUnmatchedSceneMatches(){
  const btn=$('u-assets-retry-unmatched'),msg=$('ops-retry-unmatched-status')||$('u-assets-process-msg');
  if(btn){btn.disabled=true;btn.textContent='Queueing recheck…';}
  try{
    const r=await api('/api/metadata/scene-match-review/retry-unmatched',{method:'POST',body:JSON.stringify({})});
    if(msg)msg.textContent=r&&r.ok?(r.message||`Queued ${Number(r.count||0).toLocaleString()} unmatched/suggested video(s) for one manual recheck.`):(r?.detail||'Could not queue unmatched recheck.');
    if(r&&r.ok&&r.id){toast(`Retry queued for ${Number(r.count||0).toLocaleString()} video(s).`);}
    else if(r&&r.ok)toast(r.message||'No unmatched videos found.');
    await loadSceneMatchReview();
  }catch(e){if(msg)msg.textContent='Retry failed: '+String(e?.message||e);}
  finally{if(btn){btn.disabled=false;btn.textContent='Retry & review';}}
}

function closeSceneMatchReview(){
  const dialog=$('u-assets-scene-match-modal');
  if(dialog?.open)dialog.close();
}

let _sceneMatchReviewSuggestions=[];
let _sceneMatchReviewUnresolved=[];
let _sceneMatchReviewSuggestionCount=0;
let _sceneMatchReviewUnresolvedCount=0;
let _sceneMatchReviewSuggestionOffset=0;
let _sceneMatchReviewUnresolvedOffset=0;
let _sceneMatchReviewSuggestionHasMore=false;
let _sceneMatchReviewUnresolvedHasMore=false;
let _sceneMatchReviewLoadingMore=false;
let _sceneMatchReviewSelectedSuggestions=new Set();
let _sceneMatchReviewBulkBusy=false;
let _sceneMatchReviewRowActionCount=0;
let _sceneMatchReviewLoadSeq=0;
const SCENE_MATCH_REVIEW_PAGE_SIZE=24;
const SCENE_MATCH_REVIEW_UNRESOLVED_PAGE_SIZE=24;
const SCENE_MATCH_REVIEW_MAX_SELECTION=500;
let _sceneMatchReviewRenderedSuggestionCount=0;
let _sceneMatchReviewRenderedUnresolvedCount=0;
const _sceneMatchReviewVideoById=new Map();
const SCENE_MATCH_PREVIEW_FRAME_INDICES=[0,11,23,35];
let _sceneMatchPreviewObserver=null;
let _sceneMatchPreviewGeneration=0;

function sceneMatchChannelLabel(channelId,suppliedTitle=''){
  return channelDisplayName(channelId,suppliedTitle);
}

function sceneMatchReviewFilters(){
  const channel=$('u-assets-scene-match-channel-filter'),field=$('u-assets-scene-match-field-filter');
  return {channel:String(channel?.value||''),field:String(field?.value||'all')};
}

function updateSceneMatchReviewClearButton(){
  const filters=sceneMatchReviewFilters(),button=$('u-assets-scene-match-clear-filters');
  if(button)button.disabled=!filters.channel&&filters.field==='all';
}

function updateSceneMatchReviewChannelOptions(channels){
  const select=$('u-assets-scene-match-channel-filter');
  if(!select)return false;
  const previous=String(select.value||'');
  const options=Array.isArray(channels)?channels:[];
  const containsPrevious=options.some(item=>String(item.channel_id||'')===previous);
  select.innerHTML='<option value="">All channels</option>'+options.map(item=>{
    const id=String(item.channel_id||''),title=sceneMatchChannelLabel(id,item.title);
    return `<option value="${esc(id)}">${esc(title)}</option>`;
  }).join('');
  if(previous&&!containsPrevious){
    select.value='';
    updateSceneMatchReviewClearButton();
    return true;
  }
  select.value=previous;
  updateSceneMatchReviewClearButton();
  return false;
}

function sceneMatchReviewFiltersChanged(){
  updateSceneMatchReviewClearButton();
  loadSceneMatchReview();
}

function clearSceneMatchReviewFilters(){
  const channel=$('u-assets-scene-match-channel-filter'),field=$('u-assets-scene-match-field-filter');
  if(channel)channel.value='';
  if(field)field.value='all';
  updateSceneMatchReviewClearButton();
  loadSceneMatchReview();
}

function renderSceneMatchReview({append=false}={}){
  const box=$('u-assets-scene-match-review'),count=$('u-assets-scene-match-count');
  if(!box)return;
  const suggestions=_sceneMatchReviewSuggestions,unresolved=_sceneMatchReviewUnresolved;
  const suggestionsTotal=_sceneMatchReviewSuggestionCount,unresolvedTotal=_sceneMatchReviewUnresolvedCount;
  const suggestionStart=append?_sceneMatchReviewRenderedSuggestionCount:0;
  const unresolvedStart=append?_sceneMatchReviewRenderedUnresolvedCount:0;
  const suggestionsToRender=suggestions.slice(suggestionStart);
  const unresolvedToRender=unresolved.slice(unresolvedStart);
  const activeFilters=sceneMatchReviewFilters();
  const filtered=!!activeFilters.channel||activeFilters.field!=='all';
  if(count)count.textContent=`${suggestionsTotal.toLocaleString()} suggestion${suggestionsTotal===1?'':'s'} · ${unresolvedTotal.toLocaleString()} need metadata${filtered?' · filtered':''}`;
  updateSceneMatchReviewClearButton();
  const suggestionCards=suggestionsToRender.map(it=>{
    const sid=Number(it.suggestion_id)||0,score=Math.max(0,Math.min(100,Number(it.score)||0));
    const providerImg=safeHttpUrl(it.provider_image);
    const channelId=String(it.channel_id||'').trim(),messageId=Number(it.message_id||0);
    const channelName=sceneMatchChannelLabel(channelId,it.channel_title);
    const selected=_sceneMatchReviewSelectedSuggestions.has(sid);
    const localTitle=String(it.filename||it.local_title||it.candidate_title||'video');
    const people=(it.performers||[]).filter(Boolean).join(', ');
    const candidateMeta=[people,it.release_date].filter(Boolean).join(' · ');
    const missing=(Array.isArray(it.missing_fields)?it.missing_fields:[]).map(field=>field==='performer'?'performer':'studio');
    const gap=missing.length?`<div class="ax-scene-match-missing-tags"><span class="muted">Needs</span>${missing.map(field=>`<span class="ax-scene-match-missing-tag">${esc(field)}</span>`).join('')}</div>`:'';
    const signals=String(it.signals||'').trim(),reason=String(it.reason||'').trim();
    const normalizedSignals=signals.toLowerCase().replace(/\s+/g,' ').trim();
    const normalizedReason=reason.toLowerCase().replace(/\s+/g,' ').trim();
    const distinctReason=reason&&normalizedReason!==normalizedSignals&&!normalizedSignals.includes(normalizedReason);
    const providerUrl=safeHttpUrl(it.provider_url);
    const providerSite=String(it.provider_site||it.studio||'').trim();
    const providerSiteUrl=safeHttpUrl(it.provider_site_url);
    const providerRecordId=String(it.provider_record_id||'').trim();
    const providerRecordLink=providerUrl?`<a href="${esc(providerUrl)}" target="_blank" rel="noopener noreferrer" class="ax-scene-match-provider-link">Open provider record</a>`:'';
    const providerRecordLine=`<div class="ax-scene-match-provider-record">
      ${providerSite?`<span><b>Site</b> ${providerSiteUrl?`<a href="${esc(providerSiteUrl)}" target="_blank" rel="noopener noreferrer">${esc(providerSite)}</a>`:esc(providerSite)}</span>`:'<span><b>Site</b> Not listed</span>'}
      ${providerRecordId?`<span><b>Record ID</b> ${esc(providerRecordId)}</span>`:''}
      ${it.website_variant_conflict?'<span class="ax-scene-match-site-variant">Possible same content · separate sites</span>':''}
      ${providerRecordLink}
    </div>`;
    const matchDetails=`${candidateMeta?`<div class="ax-scene-match-detail-block"><b>Candidate metadata</b><p>${esc(candidateMeta)}</p></div>`:''}${signals?`<div class="ax-scene-match-detail-block"><b>Signals</b><p>${esc(signals)}</p></div>`:''}${distinctReason?`<div class="ax-scene-match-detail-block"><b>Additional note</b><p>${esc(reason)}</p></div>`:''}`;
    const localFrames=SCENE_MATCH_PREVIEW_FRAME_INDICES.map((frameIndex,slot)=>`<figure class="ax-scene-match-frame" data-frame-index="${frameIndex}">
      <div class="ax-scene-match-frame-image"><img alt="Video snapshot ${slot+1} of ${esc(localTitle)}" decoding="async"><span class="ax-scene-match-frame-state">Loading cached frame…</span></div>
      <figcaption>Frame ${frameIndex+1} / 36</figcaption>
    </figure>`).join('');
    return `<article class="ax-scene-match-review" data-suggestion="${sid}">
      <div class="ax-scene-match-review-content">
        <div class="ax-scene-match-review-title"><label class="ax-scene-match-select"><input class="ax-scene-match-select-input" type="checkbox" ${selected?'checked':''} aria-label="Select this suggestion for rejection" onchange="sceneMatchToggleSuggestionSelection(${sid},this.checked,this)"><span>Select</span></label><span class="ax-scene-match-score">${score.toFixed(1)}%</span><b class="ax-scene-match-candidate-title">${esc(it.candidate_title||'(untitled candidate)')}</b><span class="ax-scene-match-provider-badge">${esc(it.provider||'provider')}</span></div>
        <div class="ax-scene-match-review-identity"><span>${esc(channelName)}</span><span>${esc(it.filename||it.local_title||'Untitled video')}</span>${messageId?`<span>Message #${messageId}</span>`:''}</div>
         ${providerRecordLine}
        ${gap}
        <div class="ax-scene-match-preview-grid">
          <div class="ax-scene-match-provider-preview"><div class="muted ax-scene-match-preview-label">Provider scene</div>${providerImg?`<img class="ax-scene-match-provider-image" src="${esc(providerImg)}" alt="Provider scene candidate" loading="lazy" onerror="this.style.display='none'">`:'<div class="muted ax-scene-match-provider-empty">No provider image</div>'}</div>
          <div class="ax-scene-match-local-preview">
            <div class="muted ax-scene-match-preview-label">Video snapshots · 4 of 36</div>
            <div class="ax-scene-match-frame-grid" data-channel-id="${esc(channelId)}" data-message-id="${messageId}" data-preview-state="idle" aria-label="Four cached video snapshots">${localFrames}</div>
          </div>
        </div>
        <details class="ax-scene-match-details">
          <summary><span>Why this match?</span><span class="muted">Optional details</span></summary>
          <div class="ax-scene-match-details-body">${matchDetails||'<span class="muted">No additional match details.</span>'}</div>
        </details>
      </div>
      <div class="ax-scene-match-review-actions"><button class="btn sm pri" onclick="sceneMatchSuggestionAction(${sid},'approve',this)">Approve</button><button class="btn sm" onclick="sceneMatchSuggestionAction(${sid},'reject',this)">Reject</button></div>
    </article>`;
  }).join('');
  const unresolvedCards=unresolvedToRender.map(it=>{
    const mediaId=Number(it.media_id)||0;
    _sceneMatchReviewVideoById.set(mediaId,it);
    const channelId=String(it.channel_id||'').trim(),messageId=Number(it.message_id||0);
    const channelName=sceneMatchChannelLabel(channelId,it.channel_title);
    const missing=(Array.isArray(it.missing_fields)?it.missing_fields:[]).map(field=>field==='performer'?'performer':'studio');
    const missingLabel=missing.length?missing.join(' · '):'scene identity';
    const people=(Array.isArray(it.performers)?it.performers:[]).filter(Boolean).join(', ');
    const current=[it.studio_name,people].filter(Boolean).join(' · ');
    const title=it.title||it.filename||'(untitled video)';
    const metadataFrames=SCENE_MATCH_PREVIEW_FRAME_INDICES.map((frameIndex,slot)=>`<figure class="ax-scene-match-frame" data-frame-index="${frameIndex}">
      <div class="ax-scene-match-frame-image"><img alt="Video snapshot ${slot+1} of ${esc(title)}" decoding="async"><span class="ax-scene-match-frame-state">Loading cached frame…</span></div>
      <figcaption>Frame ${frameIndex+1} / 36</figcaption>
    </figure>`).join('');
    return `<article class="ax-scene-match-review ax-scene-match-unresolved" data-media-id="${mediaId}">
      <div class="ax-scene-match-review-content">
        <div class="ax-scene-match-review-title"><b>${esc(title)}</b><span class="muted">Needs metadata</span></div>
        <div class="ax-scene-match-missing"><b>Missing:</b> ${esc(missingLabel)}</div>
        <div class="muted ax-scene-match-review-meta">File: ${esc(it.filename||'')} · Channel: ${esc(channelName)}${messageId?` · Message ID ${messageId}`:''}${current?` · Current: ${esc(current)}`:''}</div>
        <div class="ax-scene-match-local-preview ax-scene-match-metadata-preview">
          <div class="muted ax-scene-match-preview-label">Video snapshots · 4 of 36</div>
          <div class="ax-scene-match-frame-grid ax-scene-match-metadata-frame-grid" data-channel-id="${esc(channelId)}" data-message-id="${messageId}" data-preview-state="idle" aria-label="Four cached video snapshots">${metadataFrames}</div>
        </div>
      </div>
      <div class="ax-scene-match-review-actions"><button class="btn sm pri" type="button" onclick="openSceneReviewVideoEdit(${mediaId},this)">Edit video</button></div>
    </article>`;
  }).join('');
  const suggestionsMarkup=`<section class="ax-scene-match-review-section" aria-labelledby="ax-scene-match-suggestions-title">
    <div class="ax-scene-match-review-section-heading"><h3 id="ax-scene-match-suggestions-title">Ranked scene suggestions</h3><span class="muted" id="u-assets-scene-match-suggestion-visible">${suggestions.length.toLocaleString()} of ${suggestionsTotal.toLocaleString()}</span></div>
    <div class="ax-scene-match-selection-toolbar"><span class="muted" id="u-assets-scene-match-selected-count">0 selected</span><div><button class="btn sm danger" id="u-assets-scene-match-reject-selected" type="button" onclick="rejectSelectedSceneMatches()" disabled>Reject selected</button><button class="btn sm" id="u-assets-scene-match-clear-selection" type="button" onclick="clearSceneMatchSuggestionSelection()" disabled>Clear</button></div></div>
    <div class="ax-scene-match-review-card-list" id="u-assets-scene-match-suggestion-cards">${suggestionCards||'<div class="ax-scene-match-section-empty muted">No pending scene suggestions.</div>'}</div>
  </section>`;
  const unresolvedMarkup=`<section class="ax-scene-match-review-section" aria-labelledby="ax-scene-match-unresolved-title">
    <div class="ax-scene-match-review-section-heading"><h3 id="ax-scene-match-unresolved-title">Videos needing metadata</h3><span class="muted" id="u-assets-scene-match-unresolved-visible">${unresolved.length.toLocaleString()} of ${unresolvedTotal.toLocaleString()}</span></div>
    <div class="ax-scene-match-review-card-list" id="u-assets-scene-match-unresolved-cards">${unresolvedCards||'<div class="ax-scene-match-section-empty muted">No unresolved catalogue videos.</div>'}</div>
  </section>`;
  const hasMore=_sceneMatchReviewSuggestionHasMore||_sceneMatchReviewUnresolvedHasMore;
  const moreButton=hasMore?'<div class="ax-scene-match-load-more" id="u-assets-scene-match-load-more"><button class="btn sm" type="button" onclick="loadMoreSceneMatchReview(this)">Load more</button></div>':'';
  if(append){
    const suggestionList=box.querySelector('#u-assets-scene-match-suggestion-cards');
    const unresolvedList=box.querySelector('#u-assets-scene-match-unresolved-cards');
    if(suggestionCards&&suggestionList){
      suggestionList.querySelector('.ax-scene-match-section-empty')?.remove();
      suggestionList.insertAdjacentHTML('beforeend',suggestionCards);
    }
    if(unresolvedCards&&unresolvedList){
      unresolvedList.querySelector('.ax-scene-match-section-empty')?.remove();
      unresolvedList.insertAdjacentHTML('beforeend',unresolvedCards);
    }
    const suggestionVisible=box.querySelector('#u-assets-scene-match-suggestion-visible');
    const unresolvedVisible=box.querySelector('#u-assets-scene-match-unresolved-visible');
    if(suggestionVisible)suggestionVisible.textContent=`${suggestions.length.toLocaleString()} of ${suggestionsTotal.toLocaleString()}`;
    if(unresolvedVisible)unresolvedVisible.textContent=`${unresolved.length.toLocaleString()} of ${unresolvedTotal.toLocaleString()}`;
    box.querySelector('#u-assets-scene-match-load-more')?.remove();
    if(hasMore)box.insertAdjacentHTML('beforeend',moreButton);
  }else{
    const scrollTop=box.scrollTop;
    box.innerHTML=`${suggestionsMarkup}${unresolvedMarkup}${moreButton}`;
    box.scrollTop=scrollTop;
  }
  _sceneMatchReviewRenderedSuggestionCount=suggestions.length;
  _sceneMatchReviewRenderedUnresolvedCount=unresolved.length;
  sceneMatchWatchPreviewFrames(box,{append});
  sceneMatchUpdateSelectionToolbar();
}

function sceneMatchMarkPreviewUnavailable(container,message='Snapshot unavailable'){
  container.querySelectorAll('.ax-scene-match-frame').forEach(frame=>{
    frame.classList.add('is-missing');
    const state=frame.querySelector('.ax-scene-match-frame-state');
    if(state)state.textContent=message;
  });
}

async function sceneMatchLoadPreviewFrames(container,generation){
  if(!container?.isConnected||container.dataset.previewState==='loading'||container.dataset.previewState==='loaded')return;
  const channelId=String(container.dataset.channelId||''),messageId=Number(container.dataset.messageId||0);
  if(!channelId||!messageId){
    container.dataset.previewState='unavailable';
    sceneMatchMarkPreviewUnavailable(container,'No saved video snapshots');
    return;
  }
  container.dataset.previewState='loading';
  let manifest=null;
  try{manifest=await _snapshotFrameCache.getManifest(channelId,messageId);}
  catch{
    if(generation===_sceneMatchPreviewGeneration&&container.isConnected){
      container.dataset.previewState='unavailable';
      sceneMatchMarkPreviewUnavailable(container,'Preview unavailable');
    }
    return;
  }
  if(generation!==_sceneMatchPreviewGeneration||!container.isConnected)return;
  if(!manifest?.frames_ready){
    container.dataset.previewState='unavailable';
    sceneMatchMarkPreviewUnavailable(container,'No cached snapshot');
    return;
  }
  const revision=String(manifest.revision||manifest.updated_at||manifest.version||'');
  container.querySelectorAll('.ax-scene-match-frame').forEach(frame=>{
    if(generation!==_sceneMatchPreviewGeneration||!container.isConnected)return;
    const image=frame.querySelector('img'),state=frame.querySelector('.ax-scene-match-frame-state');
    const frameIndex=Number(frame.dataset.frameIndex);
    if(!image)return;
    image.onload=()=>frame.classList.add('is-ready');
    image.onerror=()=>{
      frame.classList.add('is-missing');
      image.remove();
      if(state)state.textContent='Frame unavailable';
    };
    const query=revision?`?rev=${encodeURIComponent(revision)}`:'';
    image.src=`/api/play/preview-frame/${encodeURIComponent(channelId)}/${encodeURIComponent(messageId)}/${frameIndex}${query}`;
  });
  if(generation===_sceneMatchPreviewGeneration&&container.isConnected)container.dataset.previewState='loaded';
}

function sceneMatchWatchPreviewFrames(box,{append=false}={}){
  const containers=[...box.querySelectorAll('.ax-scene-match-frame-grid[data-preview-state="idle"]')];
  if(append&&_sceneMatchPreviewObserver){
    containers.forEach(container=>_sceneMatchPreviewObserver.observe(container));
    return;
  }
  _sceneMatchPreviewObserver?.disconnect();
  _sceneMatchPreviewObserver=null;
  const generation=++_sceneMatchPreviewGeneration;
  if(typeof IntersectionObserver!=='function'){
    containers.forEach(container=>void sceneMatchLoadPreviewFrames(container,generation));
    return;
  }
  _sceneMatchPreviewObserver=new IntersectionObserver(entries=>{
    entries.forEach(entry=>{
      if(!entry.isIntersecting)return;
      _sceneMatchPreviewObserver?.unobserve(entry.target);
      void sceneMatchLoadPreviewFrames(entry.target,generation);
    });
  },{root:box,rootMargin:'200px 0px',threshold:0.01});
  containers.forEach(container=>_sceneMatchPreviewObserver.observe(container));
}

// ── Scene Match Review: in-place updates ─────────────────────────────────────
// Approving or rejecting a suggestion used to call loadSceneMatchReview(), which blanked the list
// ("Loading…"), re-fetched page one and re-rendered every card, so the dialog looked like it closed
// and reopened. These helpers instead slide the finished card out, let the next card slide up, and
// reconcile the list with the server in the background.
function sceneMatchUpdateCountLabels(){
  const count=$('u-assets-scene-match-count');
  const filters=sceneMatchReviewFilters(),filtered=!!filters.channel||filters.field!=='all';
  if(count)count.textContent=`${_sceneMatchReviewSuggestionCount.toLocaleString()} suggestion${_sceneMatchReviewSuggestionCount===1?'':'s'} · ${_sceneMatchReviewUnresolvedCount.toLocaleString()} need metadata${filtered?' · filtered':''}`;
  const sv=$('u-assets-scene-match-suggestion-visible'),uv=$('u-assets-scene-match-unresolved-visible');
  if(sv)sv.textContent=`${_sceneMatchReviewSuggestions.length.toLocaleString()} of ${_sceneMatchReviewSuggestionCount.toLocaleString()}`;
  if(uv)uv.textContent=`${_sceneMatchReviewUnresolved.length.toLocaleString()} of ${_sceneMatchReviewUnresolvedCount.toLocaleString()}`;
}
function sceneMatchRefreshEmptyStates(){
  [['u-assets-scene-match-suggestion-cards','No pending scene suggestions.'],['u-assets-scene-match-unresolved-cards','No unresolved catalogue videos.']].forEach(([id,text])=>{
    const list=$(id);if(!list)return;
    const live=list.querySelector('article.ax-scene-match-review:not(.is-leaving)');
    const empty=list.querySelector('.ax-scene-match-section-empty');
    if(live&&empty)empty.remove();
    if(!live&&!empty&&!list.querySelector('article.ax-scene-match-review')){
      list.insertAdjacentHTML('beforeend',`<div class="ax-scene-match-section-empty muted">${text}</div>`);
    }
  });
}
function sceneMatchAnimateRemove(el,direction='left'){
  if(!el||el.dataset.leaving)return;
  el.dataset.leaving='1';
  const reduce=window.matchMedia&&matchMedia('(prefers-reduced-motion: reduce)').matches;
  if(reduce){el.remove();sceneMatchRefreshEmptyStates();return;}
  el.style.maxHeight=el.offsetHeight+'px';
  el.style.setProperty('--ax-leave-x',direction==='right'?'28px':'-28px');
  void el.offsetHeight;   // commit the start state so the collapse can transition
  el.classList.add('is-leaving');
  let done=false;
  const finish=()=>{if(done)return;done=true;el.remove();sceneMatchRefreshEmptyStates();};
  el.addEventListener('transitionend',ev=>{if(ev.target===el&&ev.propertyName==='max-height')finish();});
  setTimeout(finish,600);
}
function sceneMatchDropSuggestionLocal(suggestionId){
  const id=Number(suggestionId);
  const before=_sceneMatchReviewSuggestions.length;
  _sceneMatchReviewSuggestions=_sceneMatchReviewSuggestions.filter(item=>Number(item.suggestion_id)!==id);
  if(_sceneMatchReviewSuggestions.length<before){
    _sceneMatchReviewSuggestionCount=Math.max(0,_sceneMatchReviewSuggestionCount-1);
    _sceneMatchReviewSuggestionOffset=Math.max(0,_sceneMatchReviewSuggestionOffset-1);
    _sceneMatchReviewRenderedSuggestionCount=Math.max(0,_sceneMatchReviewRenderedSuggestionCount-1);
  }
  _sceneMatchReviewSelectedSuggestions.delete(id);
  sceneMatchUpdateCountLabels();
}
function sceneMatchReconcile(r){
  const box=$('u-assets-scene-match-review');
  const nextSuggestions=Array.isArray(r.items)?r.items:[],nextUnresolved=Array.isArray(r.unresolved_items)?r.unresolved_items:[];
  const keepS=new Set(nextSuggestions.map(i=>Number(i.suggestion_id))),keepU=new Set(nextUnresolved.map(i=>Number(i.media_id)));
  box.querySelectorAll('article.ax-scene-match-review[data-suggestion]').forEach(el=>{if(!keepS.has(Number(el.dataset.suggestion)))sceneMatchAnimateRemove(el);});
  box.querySelectorAll('article.ax-scene-match-review[data-media-id]').forEach(el=>{if(!keepU.has(Number(el.dataset.mediaId)))sceneMatchAnimateRemove(el);});
  _sceneMatchReviewSuggestions=_sceneMatchReviewSuggestions.filter(i=>keepS.has(Number(i.suggestion_id)));
  _sceneMatchReviewUnresolved=_sceneMatchReviewUnresolved.filter(i=>keepU.has(Number(i.media_id)));
  _sceneMatchReviewRenderedSuggestionCount=_sceneMatchReviewSuggestions.length;
  _sceneMatchReviewRenderedUnresolvedCount=_sceneMatchReviewUnresolved.length;
  const haveS=new Set(_sceneMatchReviewSuggestions.map(i=>Number(i.suggestion_id))),haveU=new Set(_sceneMatchReviewUnresolved.map(i=>Number(i.media_id)));
  nextSuggestions.forEach(i=>{if(!haveS.has(Number(i.suggestion_id)))_sceneMatchReviewSuggestions.push(i);});
  nextUnresolved.forEach(i=>{if(!haveU.has(Number(i.media_id)))_sceneMatchReviewUnresolved.push(i);});
  [..._sceneMatchReviewSelectedSuggestions].forEach(id=>{if(!keepS.has(Number(id))&&!_sceneMatchReviewSuggestions.some(i=>Number(i.suggestion_id)===Number(id)))_sceneMatchReviewSelectedSuggestions.delete(id);});
  _sceneMatchReviewSuggestionCount=Number(r.count)||0;
  _sceneMatchReviewUnresolvedCount=Number(r.unresolved_count)||0;
  _sceneMatchReviewSuggestionOffset=_sceneMatchReviewSuggestions.length;
  _sceneMatchReviewUnresolvedOffset=_sceneMatchReviewUnresolved.length;
  _sceneMatchReviewSuggestionHasMore=!!r.has_more;
  _sceneMatchReviewUnresolvedHasMore=!!r.unresolved_has_more;
  renderSceneMatchReview({append:true});   // appends only new tail cards, refreshes counts and "Load more"
  sceneMatchRefreshEmptyStates();
}
async function loadSceneMatchReview({preserveSelection=false,quiet=false}={}){
  const box=$('u-assets-scene-match-review'),dialog=$('u-assets-scene-match-modal'),count=$('u-assets-scene-match-count');
  if(!box)return;
  if(quiet&&dialog?.open&&box.querySelector('#u-assets-scene-match-suggestion-cards')){
    const seq=++_sceneMatchReviewLoadSeq,filters=sceneMatchReviewFilters();
    try{
      const params=new URLSearchParams({
        limit:String(Math.min(200,_sceneMatchReviewSuggestions.length+SCENE_MATCH_REVIEW_PAGE_SIZE)),offset:'0',
        unresolved_limit:String(Math.min(200,_sceneMatchReviewUnresolved.length+SCENE_MATCH_REVIEW_UNRESOLVED_PAGE_SIZE)),unresolved_offset:'0',
        unresolved_channel_id:filters.channel,unresolved_missing_field:filters.field
      });
      const r=await api('/api/metadata/scene-match-review?'+params.toString(),{cache:'no-store'});
      if(seq!==_sceneMatchReviewLoadSeq||!dialog.open)return;
      if(!r||r._ok===false)return;   // keep what is on screen; the next action retries
      updateSceneMatchReviewChannelOptions(r.unresolved_channels);
      sceneMatchReconcile(r);
      return;
    }catch(e){return;}
  }
  const seq=++_sceneMatchReviewLoadSeq;
  if(!preserveSelection)_sceneMatchReviewSelectedSuggestions.clear();
  _sceneMatchPreviewObserver?.disconnect();
  _sceneMatchPreviewObserver=null;
  _sceneMatchPreviewGeneration++;
  _sceneMatchReviewLoadingMore=false;
  _sceneMatchReviewRenderedSuggestionCount=0;
  _sceneMatchReviewRenderedUnresolvedCount=0;
  const filters=sceneMatchReviewFilters();
  if(dialog&&!dialog.open){try{dialog.showModal();}catch(_){dialog.setAttribute('open','');}}
  box.innerHTML=window.AXSkeleton?window.AXSkeleton.reviewCards(3):'<div class="muted">Loading saved suggestions and unresolved videos…</div>';
  if(count)count.textContent='Loading…';
  _sceneMatchReviewVideoById.clear();
  try{
    const params=new URLSearchParams({
      limit:String(SCENE_MATCH_REVIEW_PAGE_SIZE),offset:'0',
      unresolved_limit:String(SCENE_MATCH_REVIEW_UNRESOLVED_PAGE_SIZE),unresolved_offset:'0',
      unresolved_channel_id:filters.channel,unresolved_missing_field:filters.field
    });
    const r=await api('/api/metadata/scene-match-review?'+params.toString(),{cache:'no-store'});
    if(seq!==_sceneMatchReviewLoadSeq)return;
    if(!r||r._ok===false){box.innerHTML=`<div class="muted">${esc(r?.detail||'Could not load scene-match review.')}</div>`;if(count)count.textContent='Load failed';return;}
    if(updateSceneMatchReviewChannelOptions(r.unresolved_channels)&&filters.channel)return loadSceneMatchReview();
    _sceneMatchReviewSuggestions=Array.isArray(r.items)?r.items:[];
    _sceneMatchReviewUnresolved=Array.isArray(r.unresolved_items)?r.unresolved_items:[];
    _sceneMatchReviewSuggestionCount=Number(r.count)||0;
    _sceneMatchReviewUnresolvedCount=Number(r.unresolved_count)||0;
    _sceneMatchReviewSuggestionOffset=Number(r.offset||0)+_sceneMatchReviewSuggestions.length;
    _sceneMatchReviewUnresolvedOffset=Number(r.unresolved_offset||0)+_sceneMatchReviewUnresolved.length;
    _sceneMatchReviewSuggestionHasMore=!!r.has_more;
    _sceneMatchReviewUnresolvedHasMore=!!r.unresolved_has_more;
    renderSceneMatchReview();
  }catch(e){
    if(seq!==_sceneMatchReviewLoadSeq)return;
    box.innerHTML=`<div class="muted">Review request failed: ${esc(e?.message||e)}</div>`;
    if(count)count.textContent='Load failed';
  }
}

async function loadMoreSceneMatchReview(button){
  if(_sceneMatchReviewLoadingMore)return;
  _sceneMatchReviewLoadingMore=true;
  const loadSeq=_sceneMatchReviewLoadSeq,filters=sceneMatchReviewFilters();
  if(button){button.disabled=true;button.textContent='Loading…';}
  try{
    const params=new URLSearchParams({
      limit:String(SCENE_MATCH_REVIEW_PAGE_SIZE),offset:String(_sceneMatchReviewSuggestionOffset),
      unresolved_limit:String(SCENE_MATCH_REVIEW_UNRESOLVED_PAGE_SIZE),unresolved_offset:String(_sceneMatchReviewUnresolvedOffset),
      unresolved_channel_id:filters.channel,unresolved_missing_field:filters.field
    });
    const r=await api('/api/metadata/scene-match-review?'+params.toString(),{cache:'no-store'});
    if(loadSeq!==_sceneMatchReviewLoadSeq)return;
    if(!r||r._ok===false){toast(r?.detail||'Could not load more review items.');return;}
    if(updateSceneMatchReviewChannelOptions(r.unresolved_channels)&&filters.channel)return loadSceneMatchReview();
    const suggestionIds=new Set(_sceneMatchReviewSuggestions.map(item=>Number(item.suggestion_id)));
    (Array.isArray(r.items)?r.items:[]).forEach(item=>{if(!suggestionIds.has(Number(item.suggestion_id)))_sceneMatchReviewSuggestions.push(item);});
    const mediaIds=new Set(_sceneMatchReviewUnresolved.map(item=>Number(item.media_id)));
    (Array.isArray(r.unresolved_items)?r.unresolved_items:[]).forEach(item=>{if(!mediaIds.has(Number(item.media_id)))_sceneMatchReviewUnresolved.push(item);});
    _sceneMatchReviewSuggestionCount=Number(r.count)||0;
    _sceneMatchReviewUnresolvedCount=Number(r.unresolved_count)||0;
    _sceneMatchReviewSuggestionOffset=Number(r.offset||_sceneMatchReviewSuggestionOffset)+((Array.isArray(r.items)?r.items:[]).length);
    _sceneMatchReviewUnresolvedOffset=Number(r.unresolved_offset||_sceneMatchReviewUnresolvedOffset)+((Array.isArray(r.unresolved_items)?r.unresolved_items:[]).length);
    _sceneMatchReviewSuggestionHasMore=!!r.has_more;
    _sceneMatchReviewUnresolvedHasMore=!!r.unresolved_has_more;
    renderSceneMatchReview({append:true});
  }catch(e){if(loadSeq===_sceneMatchReviewLoadSeq)toast(`Could not load more review items: ${e?.message||e}`);}
  finally{
    _sceneMatchReviewLoadingMore=false;
    if(button?.isConnected){button.disabled=false;button.textContent='Load more';}
  }
}

function sceneMatchUpdateSelectionToolbar(){
  const count=_sceneMatchReviewSelectedSuggestions.size;
  const busy=_sceneMatchReviewBulkBusy||_sceneMatchReviewRowActionCount>0;
  const countLabel=$('u-assets-scene-match-selected-count');
  const reject=$('u-assets-scene-match-reject-selected');
  const clear=$('u-assets-scene-match-clear-selection');
  if(countLabel)countLabel.textContent=`${count} selected`;
  if(reject){
    reject.disabled=!count||busy;
    reject.textContent=_sceneMatchReviewBulkBusy?'Working…':`Reject selected${count?` (${count})`:''}`;
  }
  if(clear)clear.disabled=!count||busy;
  const box=$('u-assets-scene-match-review');
  box?.querySelectorAll('.ax-scene-match-select-input').forEach(input=>{input.disabled=busy;});
  if(_sceneMatchReviewBulkBusy){
    box?.querySelectorAll('.ax-scene-match-review-actions button').forEach(button=>{button.disabled=true;});
  }
}

function sceneMatchToggleSuggestionSelection(suggestionId,selected,input){
  const id=Number(suggestionId);
  if(!Number.isSafeInteger(id)||id<=0||_sceneMatchReviewBulkBusy||_sceneMatchReviewRowActionCount)return;
  if(selected&&!_sceneMatchReviewSelectedSuggestions.has(id)&&_sceneMatchReviewSelectedSuggestions.size>=SCENE_MATCH_REVIEW_MAX_SELECTION){
    if(input)input.checked=false;
    toast(`Select up to ${SCENE_MATCH_REVIEW_MAX_SELECTION} suggestions at once.`);
    return;
  }
  if(selected)_sceneMatchReviewSelectedSuggestions.add(id);
  else _sceneMatchReviewSelectedSuggestions.delete(id);
  sceneMatchUpdateSelectionToolbar();
}

function clearSceneMatchSuggestionSelection(){
  if(_sceneMatchReviewBulkBusy||_sceneMatchReviewRowActionCount)return;
  _sceneMatchReviewSelectedSuggestions.clear();
  const box=$('u-assets-scene-match-review');
  box?.querySelectorAll('.ax-scene-match-select-input').forEach(input=>{input.checked=false;});
  sceneMatchUpdateSelectionToolbar();
}

async function rejectSelectedSceneMatches(){
  if(_sceneMatchReviewBulkBusy||_sceneMatchReviewRowActionCount)return;
  const suggestionIds=[..._sceneMatchReviewSelectedSuggestions].filter(id=>Number.isSafeInteger(id)&&id>0);
  if(!suggestionIds.length)return;
  _sceneMatchReviewBulkBusy=true;
  sceneMatchUpdateSelectionToolbar();
  try{
    const r=await api('/api/metadata/scene-match-review/reject-selected',{
      method:'POST',body:JSON.stringify({suggestion_ids:suggestionIds})
    });
    if(!r||r._ok===false){toast(r?.detail||'Selected suggestions could not be rejected.');return;}
    [...(Array.isArray(r.rejected_ids)?r.rejected_ids:[]),...(Array.isArray(r.not_pending_ids)?r.not_pending_ids:[])]
      .forEach(id=>_sceneMatchReviewSelectedSuggestions.delete(Number(id)));
    toast(`Rejected ${Number(r.rejected||0)} selected suggestion(s); ${Number(r.not_pending||0)} were no longer pending; ${Number(r.failed||0)} failed.`);
    await loadSceneMatchReview({preserveSelection:true,quiet:true});
  }catch(e){toast(`Selected suggestions could not be rejected: ${e?.message||e}`);}
  finally{
    _sceneMatchReviewBulkBusy=false;
    const box=$('u-assets-scene-match-review');
    box?.querySelectorAll('.ax-scene-match-review-actions button').forEach(button=>{button.disabled=false;});
    sceneMatchUpdateSelectionToolbar();
  }
}

async function sceneMatchSuggestionAction(suggestionId,action,button){
  if(_sceneMatchReviewBulkBusy||_sceneMatchReviewRowActionCount)return;
  _sceneMatchReviewRowActionCount++;
  sceneMatchUpdateSelectionToolbar();
  const row=button?.closest('.ax-scene-match-review');
  row?.querySelectorAll('button').forEach(b=>b.disabled=true);
  try{
    const endpoint=action==='approve'?`/api/suggestions/${Number(suggestionId)}/confirm`:`/api/suggestions/${Number(suggestionId)}/reject`;
    const r=await api(endpoint,{method:'POST',body:JSON.stringify({})});
    if(!r||r._ok===false){toast(r?.detail||`Could not ${action} this suggestion.`);return;}
    toast(action==='approve'?'Scene match approved.':'Scene suggestion rejected.');
    // Slide this card out and the next one up; do not reload the dialog.
    sceneMatchDropSuggestionLocal(suggestionId);
    sceneMatchAnimateRemove(row,action==='approve'?'right':'left');
    loadSceneMatchReview({preserveSelection:true,quiet:true});
  }catch(e){toast(`Could not ${action} scene: ${e?.message||e}`);}
  finally{
    _sceneMatchReviewRowActionCount=Math.max(0,_sceneMatchReviewRowActionCount-1);
    row?.querySelectorAll('button').forEach(b=>{b.disabled=false;});
    sceneMatchUpdateSelectionToolbar();
  }
}

async function openSceneReviewVideoEdit(mediaId,button){
  const item=_sceneMatchReviewVideoById.get(Number(mediaId));
  if(!item){toast('This catalogue entry is no longer in the review list. Reload the review and try again.');return;}
  const messageId=Number(item.message_id),channelId=String(item.channel_id||'');
  if(!messageId||!channelId){toast('This video is missing its Telegram source details and cannot be opened for editing.');return;}
  const names=Array.isArray(item.performers)?item.performers:[];
  const sourceVideo={
    id:messageId,_cid:channelId,telegram_filename:item.filename||'',
    name:item.title||item.filename||'Video',title:item.title||'',api_title:item.title||'',
    duration:Number(item.duration)||0,performers:names,local_performers:names,
    site_name:item.studio_name||'',studio:item.studio_name||''
  };
  if(button)button.disabled=true;
  closeSceneMatchReview();
  await openVideoQuickEdit(messageId,channelId,sourceVideo,true);
}

async function approveSceneMatchesAbove(){
  if(_sceneMatchReviewBulkBusy||_sceneMatchReviewRowActionCount)return;
  const input=$('u-assets-match-approve-threshold');
  const threshold=Math.max(0,Math.min(100,Number(input?.value??95)));
  if(!(await axConfirm(`Approve all pending candidates scored ${threshold.toFixed(1)}% or higher? Performer conflicts and safety checks will still block approval.`,{title:'Approve high-confidence scenes',okLabel:'Approve candidates',danger:false})))return;
  _sceneMatchReviewBulkBusy=true;
  sceneMatchUpdateSelectionToolbar();
  const btn=$('u-assets-match-approve-above');if(btn){btn.disabled=true;btn.textContent='Approving…';}
  try{
    const r=await api('/api/metadata/scene-match-review/approve-above',{method:'POST',body:JSON.stringify({threshold})});
    if(!r||r._ok===false){toast(r?.detail||'Bulk approval failed.');return;}
    toast(`Approved ${Number(r.approved||0)}; ${Number(r.ambiguous_skipped||0)} ambiguous video(s) left for review; ${Number(r.website_variants_skipped||0)} cross-site variant(s) need individual review; ${Number(r.performer_conflicts||0)} blocked by performer conflicts; ${Number(r.failed||0)} failed.`);
    await loadSceneMatchReview({quiet:true});
  }catch(e){toast('Bulk approval failed: '+(e?.message||e));}
  finally{
    _sceneMatchReviewBulkBusy=false;
    if(btn){btn.disabled=false;btn.textContent='Approve above';}
    const box=$('u-assets-scene-match-review');
    box?.querySelectorAll('.ax-scene-match-review-actions button').forEach(button=>{button.disabled=false;});
    sceneMatchUpdateSelectionToolbar();
  }
}

// ── SECTION: Playback Snapshot Builder ──
let _thumbSel=new Set(),_thumbTimer=null;
function renderThumbChannels(){
  const dd=$('m-thumb-chan');if(!dd)return;
  const cur=dd.value;
  dd.innerHTML='<option value="">— add channel —</option>'+_sourceChans.map(c=>`<option value="${esc(c.telegram_id)}">${esc(channelDisplayName(c.telegram_id,c.title))}</option>`).join('');
  dd.value=cur;
  $('thumb-chips').innerHTML=_sourceChans.filter(c=>_thumbSel.has(String(c.telegram_id))).map(c=>`<span class="cp on"><span>${esc(channelDisplayName(c.telegram_id,c.title))}</span><button class="btn xs" style="padding:1px 5px;line-height:1" onclick="_thumbSel.delete(${jsa(c.telegram_id)});renderThumbChannels()">×</button></span>`).join('');
}




async function thumbPoll(){
  clearTimeout(_thumbTimer);
  const r=await api('/api/operations/thumbnails/status');
  if(!r||!r._ok)return;
  const total=Number(r.total||0),done=Number(r.completed||0),pct=total?Math.round(done*100/total):0;
  const msg=r.running?`Running · ${done.toLocaleString()} / ${total.toLocaleString()} (${pct}%) · fetched ${Number(r.fetched||0).toLocaleString()} · failed ${Number(r.failed||0).toLocaleString()}${r.current?' · '+r.current:''}`:(r.status==='completed'?`Completed · ${done.toLocaleString()} processed · ${Number(r.fetched||0).toLocaleString()} fetched · ${Number(r.cached||0).toLocaleString()} already cached · ${Number(r.failed||0).toLocaleString()} failed`:r.status==='stopped'||r.status==='stopping'?`Stopped · ${done.toLocaleString()} / ${total.toLocaleString()}`:(r.error?'Error: '+r.error:'Ready.'));
  if($('thumb-status'))$('thumb-status').textContent=msg;
  const thumbStatus=$('ms-THUMBNAIL_INDEXER');if(thumbStatus){let badge=thumbStatus.querySelector('[data-ax-thumb-live-status]');if(!badge){badge=document.createElement('span');badge.setAttribute('data-ax-thumb-live-status','1');thumbStatus.replaceChildren(badge);}badge.className=r.running?'badge':'muted';badge.textContent=r.running?'Running':String(r.status||'Idle');}
  if($('v54-maint-thumb'))$('v54-maint-thumb').textContent=r.running?`Running ${pct}%`:(r.status==='completed'?'Completed':(r.error?'Error':'Ready'));
  if(r.running)_thumbTimer=setTimeout(thumbPoll,800);
}

// ── SECTION: Telegram Media Manager console / structured workflow UI ──
const _mm={seq:0,timer:null,open:false,running:false,catalog:[],promptKey:''};
function _mmSetStep(step){document.querySelectorAll('#mm-steps .mm-step').forEach(el=>{const n=Number(el.dataset.mmStep||0);el.classList.toggle('on',n===step);el.classList.toggle('done',n<step);});}

function _mmRenderPrompt(r){
  const panel=$('mm-panel');
  if(!panel)return;
  const prompt=String(r.prompt||'').trim();
  const running=!!r.running;
  const error=String(r.error||'');
  const live=String(r.live||'');
  const operation=String(r.operation||'');

  // While a prompt is active, never rebuild the input because the polling
  // loop may run while the operator is typing. Replacing the DOM node loses
  // the caret/selection and can make normal typing appear broken.
  if(running || prompt){
    let cli=panel.querySelector('.mm-cli');
    let input=panel.querySelector('#mm-in2');
    const needsBuild=!cli || !input;
    if(needsBuild){
      panel.innerHTML=`<div class="mm-cli"><div class="mm-cli-head"><span class="dot"></span><span>Interactive Media Manager CLI</span><span class="muted">Enter the response requested by the CLI.</span></div><div id="mm-prompt-body" class="mm-cli-body"></div><div class="mm-cli-input"><input id="mm-in2" type="text" autocomplete="off" placeholder="Enter your answer…"><button class="btn pri" id="mm-in2-send" type="button">Send</button></div></div>`;
      cli=panel.querySelector('.mm-cli');
      input=panel.querySelector('#mm-in2');
      const send=panel.querySelector('#mm-in2-send');
      input.addEventListener('keydown',ev=>{
        if(ev.key==='Enter' && !ev.isComposing){ev.preventDefault();mmSendValue(input.value);}
      });
      send.addEventListener('click',()=>mmSendValue(input.value));
    }
    const body=panel.querySelector('#mm-prompt-body');
    const display=prompt || live || operation || 'Processing…';
    if(body && body.textContent!==display)body.textContent=display;
    if(input){
      const shouldDisable=!prompt;
      if(input.disabled!==shouldDisable)input.disabled=shouldDisable;
      const send=panel.querySelector('#mm-in2-send');
      if(send)send.disabled=shouldDisable;
      const placeholder=prompt?'Enter your answer…':'Waiting…';
      if(input.placeholder!==placeholder)input.placeholder=placeholder;
    }
    const promptKey=JSON.stringify([running,prompt,error]);
    if(panel.dataset.mmPromptKey!==promptKey)panel.dataset.mmPromptKey=promptKey;
    return;
  }

  const finalKey=JSON.stringify([running,prompt,error]);
  if(panel.dataset.mmPromptKey===finalKey && panel.querySelector('.mm-finished'))return;
  if(panel.dataset.mmPromptKey!==finalKey)panel.dataset.mmPromptKey=finalKey;
  panel.innerHTML=`<div class="mm-finished"><div class="mm-panel-title">${error?'Media Manager stopped':'Media Manager finished'}</div><div class="mm-panel-help">${esc(error||'The CLI workflow is no longer active.')}</div></div>`;
}
async function mmSendValue(value){
  const text=String(value??'').trim();
  if(!text)return;
  const input=document.getElementById('mm-in2');
  const button=input?.parentElement?.querySelector('button');
  if(input)input.disabled=true;
  if(button)button.disabled=true;
  const r=await api('/api/telegram-transfer/input',{method:'POST',body:JSON.stringify({text})});
  if(!r.ok){
    if(input)input.disabled=false;
    if(button)button.disabled=false;
    toast(r.detail||'The Media Manager is no longer waiting for input.');
    await mmPoll();
    return;
  }
  // The submitted answer is already committed to the server-side CLI queue.
  // Clear the local field so the previous answer cannot look unsent when the
  // next prompt appears. Polling will keep the same input DOM node.
  if(input){input.value='';input.disabled=true;}
  if(button)button.disabled=true;
  await mmPoll();
}

function mmOpen(){$('mm').classList.add('open');_mm.open=true;mmPoll();}
function mmClose(){$('mm').classList.remove('open');_mm.open=false;clearTimeout(_mm.timer);}
function mmSyncCustomScroll(){
  const sc=document.getElementById('mm-out-scroll');
  const rail=document.getElementById('mm-custom-scrollbar');
  const thumb=document.getElementById('mm-custom-scrollthumb');
  if(!sc||!rail||!thumb)return;
  const max=Math.max(0,sc.scrollHeight-sc.clientHeight);
  if(max<=1){rail.style.display='none';return;}
  rail.style.display='block';
  const trackH=Math.max(1,rail.clientHeight-2);
  const thumbH=Math.max(28,Math.round(trackH*(sc.clientHeight/sc.scrollHeight)));
  const maxTop=Math.max(0,trackH-thumbH);
  const top=max?Math.round(maxTop*(sc.scrollTop/max)):0;
  thumb.style.height=thumbH+'px';
  thumb.style.transform=`translateY(${top}px)`;
}
function mmBindCustomScroll(){
  const sc=document.getElementById('mm-out-scroll');
  const rail=document.getElementById('mm-custom-scrollbar');
  const thumb=document.getElementById('mm-custom-scrollthumb');
  if(!sc||!rail||!thumb||sc.dataset.customScrollBound)return;
  sc.dataset.customScrollBound='1';
  sc.addEventListener('scroll',mmSyncCustomScroll,{passive:true});
  rail.addEventListener('pointerdown',ev=>{
    if(ev.target===thumb)return;
    // V29.4.131: pointer/rect values are in screen px, thumb sizes in CSS px;
    // they differ by the page zoom on 4K/8K screens.
    const z=(window.axZoomFactor&&window.axZoomFactor())||1;
    const r=rail.getBoundingClientRect();
    const trackH=r.height/z-2;
    const th=thumb.offsetHeight;
    const y=Math.max(0,Math.min(trackH-th,(ev.clientY-r.top)/z-1-th/2));
    const max=Math.max(0,sc.scrollHeight-sc.clientHeight);
    sc.scrollTop=max*((trackH-th)?y/(trackH-th):0);
  });
  thumb.addEventListener('pointerdown',ev=>{
    ev.preventDefault();
    thumb.setPointerCapture?.(ev.pointerId);
    const startY=ev.clientY;
    const z=(window.axZoomFactor&&window.axZoomFactor())||1;
    const startTop=parseFloat((thumb.style.transform.match(/translateY\(([-.\d]+)/)||[])[1]||'0');
    const r=rail.getBoundingClientRect();
    const trackH=r.height/z-2;
    const th=thumb.offsetHeight;
    const max=Math.max(0,sc.scrollHeight-sc.clientHeight);
    const maxTop=Math.max(1,trackH-th);
    const move=e=>{
      const next=Math.max(0,Math.min(maxTop,startTop+(e.clientY-startY)/z));
      thumb.style.transform=`translateY(${next}px)`;
      sc.scrollTop=max*(next/maxTop);
    };
    const up=()=>{
      thumb.releasePointerCapture?.(ev.pointerId);
      thumb.removeEventListener('pointermove',move);
      thumb.removeEventListener('pointerup',up);
      thumb.removeEventListener('pointercancel',up);
    };
    thumb.addEventListener('pointermove',move);
    thumb.addEventListener('pointerup',up);
    thumb.addEventListener('pointercancel',up);
  });
  window.addEventListener('resize',mmSyncCustomScroll,{passive:true});
  mmSyncCustomScroll();
}
async function mmPoll(){
  clearTimeout(_mm.timer);
  if(!_mm.open)return;
  mmBindCustomScroll();
  const r=await api('/api/telegram-transfer?since='+_mm.seq);
  if(r&&r._ok){
    const out=$('mm-out');
    const sc=$('mm-out-scroll')||out;
    if(r.seq<_mm.seq){out.textContent='';_mm.seq=0;}
    if((r.lines||[]).length){
      const atEnd=sc.scrollTop+sc.clientHeight>=sc.scrollHeight-40;
      const chunk=r.lines.map(l=>l.t).join('\n');
      out.textContent+=out.textContent?(out.textContent.endsWith('\n')?chunk:'\n'+chunk):chunk;
      const max=400000;
      if(out.textContent.length>max)out.textContent=out.textContent.slice(-max);
      _mm.seq=r.lines[r.lines.length-1].n;
      if(atEnd)requestAnimationFrame(()=>{sc.scrollTop=sc.scrollHeight;});
      mmSyncCustomScroll();
    }
    const live=$('mm-live');
    if(live){
      const liveText=String(r.live||''),liveDisplay=r.live?'block':'none';
      if(live.textContent!==liveText)live.textContent=liveText;
      if(live.style.display!==liveDisplay)live.style.display=liveDisplay;
    }
    const title=$('mm-title');
    if(title){const next='Telegram Job Manager'+(r.mode_name?' — '+r.mode_name:'');if(title.textContent!==next)title.textContent=next;}
    const counts=r.total?`${(r.completed||0).toLocaleString()} / ${r.total.toLocaleString()} — ${Math.round(100*(r.completed||0)/r.total)}%`:'';
    const job=$('mm-job');
    if(job){const next=r.running?('Running'+(counts?' · '+counts:'')):(r.error?'Stopped with an error: '+r.error:(r.seq?'Finished.':'Choose a workflow and press Start.'));if(job.textContent!==next)job.textContent=next;}
    const stop=$('mm-stop');
    if(stop){const display=r.running?'':'none';if(stop.style.display!==display)stop.style.display=display;}
    const lp=$('mm-live-progress');
    if(lp){
      if(r.running){
        const total=Number(r.total||0),done=Number(r.completed||0),known=total>0,pct=known?Math.max(0,Math.min(100,Math.round(done*100/total))):0;
        if(lp.style.display!=='block')lp.style.display='block';
        const statusColor=known?(window.AXLoadingFeedback?.colorForPercent?.(pct)||'var(--ax-status-fair)'):'var(--accent)';
        _axPatchLiveMarkup(lp,`<div data-ax-live-key="mm-live-line" class="job-live-line"><span class="live-pulse"></span><span data-ax-live-key="mm-live-operation" class="job-live-op"><b>LIVE</b> · ${esc(r.operation||r.live||'Processing')}</span></div><div data-ax-live-key="mm-progress-line" class="ax-progress-line">${known?`<div data-ax-live-key="mm-progress-track" class="pt"><div data-ax-live-key="mm-progress-fill" class="pb progress-purple ax-fb-status-progress" style="width:${pct}%;--ax-fb-status-color:${statusColor}"></div></div><span data-ax-live-key="mm-progress-label" class="ax-progress-label">${pct}%</span>`:`<div data-ax-live-key="mm-progress-track" class="pt"><div data-ax-live-key="mm-progress-fill" class="pb progress-purple ind" style="width:35%"></div></div><span data-ax-live-key="mm-progress-label" class="ax-progress-label">working</span>`}</div><div data-ax-live-key="mm-live-meta" class="job-live-meta">${known?`${done.toLocaleString()} / ${total.toLocaleString()} completed`:'Progress count not yet available'}</div>`);
      }else{if(lp.style.display!=='none')lp.style.display='none';_axPatchLiveMarkup(lp,'');}
    }
    _mm.running=!!r.running;
    const hadPrompt=!!document.querySelector('#mm-panel #mm-in2');
    _mmRenderPrompt(r);
    const input=document.getElementById('mm-in2');
    if(input&&r.prompt&&!hadPrompt){
      // Focus once when a new prompt appears. Never select the contents.
      setTimeout(()=>{try{input.focus();input.setSelectionRange(input.value.length,input.value.length);}catch(_){ }},0);
    }
  }
  _mm.timer=setTimeout(mmPoll,_mm.running?700:2500);
}
async function mmSend(){const value=$('mm-in')?.value||'';if(value)mmSendValue(value);}
async function mmStop(){if(!(await axConfirm('Stop the Telegram job? Work already verified is kept; it can resume after you choose Resume.',{title:'Stop Telegram job',okLabel:'Stop job',confirmLabel:'Stop job',danger:true})))return;await api('/api/telegram-transfer/stop',{method:'POST'});mmPoll();refresh();}

// ═══════════════════════════════════════════════
// SECTION: ADMIN — ACTIVE JOBS / EVENTS
// ═══════════════════════════════════════════════
function renderJobs(jobs){
  jobs=Array.isArray(jobs)?jobs:[];
  renderOpsSummary(jobs);renderTaskTray(jobs);
  const jobById=new Map((jobs||[]).map(j=>[String(j.id||''),j]));
  const el=$('jobs-pan');if(!el)return;
  el.classList.toggle('has-jobs', jobs.length>0);
  if(!jobs.length){_axPatchLiveMarkup(el,'<div data-ax-live-key="active-jobs-empty" class="muted" style="padding:14px 0">No active jobs.</div>');return;}
  const staged=document.createElement('div');
  staged.innerHTML=jobs.map(j=>{
    const status=String(j.status||'').trim().toUpperCase();
    const isPlayback=j.type==='PLAYBACK';
    // Display-only module identity for the Operations job-card icon.
    const moduleType=String(j.type||'').toUpperCase();
    const moduleIcon=/TRANSFER|UPLOAD/.test(moduleType)?'upload':(/METADATA|ASSET|MEDIA_MANAGER/.test(moduleType)?'database':(/AUTOMATION/.test(moduleType)?'lightning':'gear'));
    const moduleTone=/TRANSFER|UPLOAD/.test(moduleType)?'transfer':(/METADATA|ASSET|MEDIA_MANAGER/.test(moduleType)?'assets':(/AUTOMATION/.test(moduleType)?'automation':'neutral'));
    const known=j.total!==null&&j.total!==undefined&&j.total>0;
    const pct=known?Math.max(0,Math.min(100,Math.round(100*(j.completed||0)/j.total))):Math.max(0,Math.min(100,Math.round(j.progress||0)));
    const statusFeedbackTone=window.AXLoadingFeedback?.statusTone?.(status)||'neutral';
    const progressStatusColor=known?(window.AXLoadingFeedback?.colorForPercent?.(pct)||'var(--ax-status-fair)'):'var(--accent)';
    const st=status==='RUNNING'?'':status==='QUEUED'?' q':' p';
    const tone=status==='FAILED'||status==='ERROR'?'red':status==='COMPLETED_WITH_ERRORS'?'orange':status==='COMPLETED'?'green':status==='QUEUED'?'orange':status==='PAUSED'||status==='INTERRUPTED'||status==='RECOVERING'?'neutral':'purple';
    const interrupted=status==='INTERRUPTED'||status==='RECOVERING';
    const counts=known?`${(j.completed||0).toLocaleString()} / ${j.total.toLocaleString()} · ${(j.pending??(j.total-(j.completed||0))).toLocaleString()} pending`:(status==='QUEUED'?'Waiting to start':interrupted?'Recovery pending':'Processing…');
    const op=[j.operation,j.item&&!String(j.operation||'').includes(j.item)?j.item:''].filter(Boolean).join(' — ');
    const w=String(j.watchdog_state||'UNKNOWN').toUpperCase();
    const wClass=w==='HEALTHY'?'green':w==='WAITING'?'neutral':(w==='DEGRADED'||w==='SUSPECTED')?'orange':(w==='STALLED'||w==='FAILED')?'red':'neutral';
    const watchdogTone=window.AXLoadingFeedback?.statusTone?.(w)||'neutral';
    const watchdog=w!=='UNKNOWN'?`<span data-job-key="watchdog" class="job-badge watchdog ${wClass} ax-status-indicator" data-ax-status-tone="${watchdogTone}" title="Watchdog: ${esc(String(j.watchdog_reason||''))}">Watchdog ${esc(w)}</span>`:'';
    const paused=status==='PAUSED'||status==='PAUSING';
    const controls=isPlayback?`<div class="job-controls" onclick="event.stopPropagation()">
      <button class="btn xs danger" onclick="stopPlaybackJob()">${axIcon('stop')} Stop playback</button>
      <button class="btn xs" onclick="refresh()">${axIcon('refresh')} Refresh</button>
    </div>`:`<div class="job-controls" onclick="event.stopPropagation()">
      ${interrupted?`<button class="btn xs pri" onclick="jobRecover(${jsa(j.id)})">${axIcon('play')} ${j.status==='RECOVERING'?'Retry recovery':'Recover & Resume'}</button>`:paused?`<button class="btn xs" onclick="jobResume(${jsa(j.id)})">${axIcon('play')} Resume</button>`:`<button class="btn xs" onclick="jobPause(${jsa(j.id)})">${axIcon('pause')} Pause</button>`}
      <button class="btn xs danger" onclick="jobCancel(${jsa(j.id)})">${axIcon('stop')} Stop</button>
      <button class="btn xs" onclick="jobRefresh(${jsa(j.id)})">${axIcon('refresh')} Refresh</button>
      ${j.type==='MEDIA_MANAGER'||j.type==='TELEGRAM_TRANSFER'?`<button class="btn xs" onclick="mmOpen()">Open console</button>`:''}
    </div>`;
    const resumeHint=paused&&String(j.operation||'').toLowerCase().includes('interrupted by restart')?`<div class="job-resume-note"><b>Resume is safe.</b><span>Saved transfer state, completed items, and metadata are retained.</span></div>`:'';
    const meta=op?`<div data-job-key="operation" class="job-operation" title="${esc(op)}"><span class="job-meta-label ml-[0px] mr-[0px] pl-[10px] pr-[10px]">Current</span><span>${esc(op)}</span></div>`:'';
    const downloadWait=status==='RUNNING'&&String(j.rate_limit_message||'').trim()?`<div data-job-key="download-wait" class="job-operation" title="Telegram download lane is paused by a server rate limit"><span class="job-meta-label">Telegram</span><span>${esc(String(j.rate_limit_message))}</span></div>`:'';
    const admissionState=String(j.admission_state||'READY').toUpperCase();
    const admissionReason=String(j.admission_reason||'').trim();
    const admission=(status==='QUEUED'||admissionState!=='READY')?`<div data-job-key="admission" class="job-operation" title="Admission: ${esc(admissionReason||admissionState)}"><span class="job-meta-label">Admission</span><span>${esc(admissionState)}${admissionReason?' · '+esc(admissionReason):''}</span></div>`:'';
    const execution=(j.execution_id||j.worker_id)?`<div data-job-key="execution" class="job-operation" title="Worker ${esc(String(j.worker_id||''))}"><span class="job-meta-label pl-[9px] pr-[9px]">Worker</span><span>${esc(String(j.worker_id||'—'))} · attempt ${esc(String(j.attempt??0))} · ${esc(String(j.worker_state||'IDLE'))}</span></div>`:'';
    const retry=(Number(j.retry_count||0)>0||j.failure_class)?`<div data-job-key="retry" class="job-operation"><span class="job-meta-label">Retry</span><span>${esc(String(j.retry_count||0))} / ${esc(String(j.max_retries??3))}${j.failure_class?' · '+esc(String(j.failure_class)):''}</span></div>`:'';
    const parent=String(j.parent_job_id||'').trim();
    const parentJob=parent?jobById.get(parent):null;
    const parentLine=parent?`<div data-job-key="parent" class="job-operation"><span class="job-meta-label">Parent</span><span>${esc(parentJob?.name||'Content Automation')} · ${esc(parent)}</span></div>`:'';
    const dependencyCount=Array.isArray(j.dependencies)?j.dependencies.filter(d=>String(d.dependency_status||'').toUpperCase()!=='COMPLETED').length:0;
    const dependency=dependencyCount?`<div data-job-key="dependencies" class="job-operation"><span class="job-meta-label">Dependencies</span><span>${dependencyCount} waiting</span></div>`:'';
    const jobClickAttrs=isPlayback?'':`onclick="openJobDetails(${jsa(j.id)})" onkeydown="if(event.key==='Enter'||event.key===' '){event.preventDefault();openJobDetails(${jsa(j.id)})}"`;
    return `<div data-job-id="${esc(String(j.id||''))}" data-job-status="${esc(status)}" class="jc v54-job-click${isPlayback?' playback-runtime-job':''}" role="${isPlayback?'status':'button'}" tabindex="${isPlayback?'-1':'0'}" ${jobClickAttrs}>
      <div class="job-head" data-job-key="head"><span data-job-key="module-icon" class="ax2-job-module-icon ${moduleTone}" title="${esc(j.type||'Job')}" aria-hidden="true">${axIcon(moduleIcon)}</span><div class="job-title-wrap" data-job-key="title-wrap"><strong data-job-key="title" class="job-title" title="${esc(j.name)}">${esc(j.name)}</strong><span data-job-key="type" class="job-type">${esc(j.type||'Job')}</span></div><div class="job-badges" data-job-key="badges"><span data-job-key="status" class="job-badge status ax-status-indicator ${st?'queued':status==='FAILED'||status==='ERROR'?'failed':status==='COMPLETED_WITH_ERRORS'?'warning':status==='COMPLETED'?'completed':status==='PAUSED'?'paused':'running'}" data-ax-status-tone="${statusFeedbackTone}">${esc(status)}</span>${watchdog}</div></div>
      <div class="jc-b" data-job-key="progress">${known?`<span data-job-key="percent" class="job-pct" style="color:${progressStatusColor}">${pct}%</span>`:`<span class="job-pct muted">Working</span>`}<div class="pt" data-job-key="track"><div data-job-key="fill" class="pb progress-${tone}${known?' ax-fb-status-progress':''}${known||j.status!=='RUNNING'?'':' ind'}" style="width:${known?pct:0}%;${known?`--ax-fb-status-color:${progressStatusColor}`:''}"></div></div></div>
      <div class="job-counts" data-job-key="counts">${esc(counts)}</div>
      ${meta}
      ${downloadWait}
      ${parentLine}
      ${admission}
      ${execution}
      ${retry}
      ${dependency}
      ${j.started?`<div data-job-key="started" class="jc-t">Started ${esc(_fmtTime(j.started))}</div>`:''}
      ${resumeHint?resumeHint.replace('<div class="job-resume-note"','<div data-job-key="resume" class="job-resume-note"'):''}
      ${controls.replace('<div class="job-controls"','<div data-job-key="controls" class="job-controls"').replace('onclick="stopPlaybackJob()"','data-job-key="playback-stop" onclick="stopPlaybackJob()"').replace('onclick="refresh()"','data-job-key="refresh" onclick="refresh()"').replace('class="btn xs pri" onclick="jobRecover','data-job-key="primary" class="btn xs pri" onclick="jobRecover').replace('class="btn xs" onclick="jobResume','data-job-key="primary" class="btn xs" onclick="jobResume').replace('class="btn xs" onclick="jobPause','data-job-key="primary" class="btn xs" onclick="jobPause').replace('class="btn xs danger" onclick="jobCancel','data-job-key="stop" class="btn xs danger" onclick="jobCancel').replace('class="btn xs" onclick="jobRefresh','data-job-key="refresh" class="btn xs" onclick="jobRefresh').replace('class="btn xs" onclick="mmOpen()"','data-job-key="console" class="btn xs" onclick="mmOpen()"')}
      <div class="job-detail-hint" data-job-key="hint">Open for pipeline, worker, timing and error details</div>
    </div>`;
  }).join('');
  _opsReconcileJobCards(el,staged);
}


let _evts=[],_logLv='',_logOutcome='',_opsState=null,_evtsDirty=false;
function setLogLv(btn){_logLv=btn.dataset.lv||'';document.querySelectorAll('#log-lv button').forEach(b=>b.classList.toggle('on',b===btn));renderEvts();}
function _evtModule(msg){
  const m=String(msg||'');
  for(const n of ['Telegram Transfer Manager','Metadata & Assets Manager','Content Automation','Backup & Restore'])if(m.startsWith(n))return n;
  if(/playback|compatibility|vlc|mpc|player|stream/i.test(m))return 'playback';
  return 'other';
}
function renderEvts(){
  const el=$('evts-log');if(!el)return;
  // The log lives on the Operations page. The live socket pushes every second, so rendering it
  // while hidden only forced layout work on whatever page the user was actually using.
  if(!$('va')?.classList.contains('on')){_evtsDirty=true;return;}
  _evtsDirty=false;
  const mod=$('log-mod')?$('log-mod').value:'';
  const query=($('log-search')?.value||'').trim().toLowerCase();
  const list=(_evts||[]).filter(e=>{
    if(typeof e!=='object'||!e)return !_logLv&&!mod;
    const lv=String(e.level||'INFO').toUpperCase();
    const outcome=String(e.outcome||'').toUpperCase();
    if(_logOutcome&&outcome!==_logOutcome)return false;
    if(_logLv==='INFO'&&!(lv==='INFO'||lv==='SUCCESS'))return false;
    if(_logLv&&_logLv!=='INFO'&&lv!==_logLv)return false;
    if(mod&&_evtModule(e.message)!==mod)return false;
    if(query){
      const hay=String(e.message||'')+' '+String(e.module||'')+' '+String(e.level||'')+' '+String(e.event||'')+' '+String(e.outcome||'');
      if(!hay.toLowerCase().includes(query))return false;
    }
    return true;
  }).slice(-80);
  const atEnd=el.scrollTop+el.clientHeight>=el.scrollHeight-30;
  const elRect=el.getBoundingClientRect?.();
  const firstVisible=Array.from(el.children||[]).find(node=>{const r=node.getBoundingClientRect?.();return r&&elRect&&r.bottom>elRect.top;});
  const anchorKey=firstVisible?.getAttribute?.('data-ax-live-key')||'';
  const anchorOffset=firstVisible&&elRect?firstVisible.getBoundingClientRect().top-elRect.top:0;
  const seenKeys=new Map();
  const html=list.length?list.map((e,index)=>{
    let base='';
    if(e&&typeof e==='object'){
      const id=e.id??e.event_id??e.event_key??e.key;
      if(id!==undefined&&id!==null&&String(id)!=='')base='id:'+String(id);
      else base=[e.created_at||e.ts||'',e.level||'INFO',e.module||'',e.event||'',typeof e.message==='string'?e.message:JSON.stringify(e.message)].join('|');
    }else base='value:'+String(e);
    const occurrence=seenKeys.get(base)||0;seenKeys.set(base,occurrence+1);
    const key=`event:${base}#${occurrence}`;
    if(typeof e!=='object'||!e)return `<div data-ax-live-key="${esc(key)}" class="ev"><span>${esc(e)}</span></div>`;
    const d=e.created_at?new Date(e.created_at):null;
    const tm=d&&!isNaN(d)?d.toLocaleTimeString([], {hour:'2-digit',minute:'2-digit',hour12:false}):'';
    const lv=String(e.level||'INFO').toUpperCase();
    let msg=e.message;if(typeof msg!=='string')msg=JSON.stringify(msg);
    const moduleName=String(e.module||_evtModule(msg));
    return `<div data-ax-live-key="${esc(key)}" class="ev ${esc(lv)}"><span data-ax-live-key="event-time" class="tm">${esc(tm)}</span><span data-ax-live-key="event-level" class="lv" aria-label="${esc(lv)}" title="${esc(lv)}"></span><span data-ax-live-key="event-module" class="event-module" title="${esc(moduleName)}">${esc(moduleName)}</span><span data-ax-live-key="event-message" title="${esc(msg)}">${esc(msg)}</span></div>`;
  }).join(''):'<div data-ax-live-key="events-empty" class="muted">No matching entries.</div>';
  _axPatchLiveMarkup(el,html);
  if(atEnd)el.scrollTop=el.scrollHeight;
  else if(anchorKey&&elRect){const anchor=Array.from(el.children||[]).find(node=>node.getAttribute?.('data-ax-live-key')===anchorKey);const rect=anchor?.getBoundingClientRect?.();const nowRect=el.getBoundingClientRect?.();if(rect&&nowRect)el.scrollTop+=rect.top-nowRect.top-anchorOffset;}
}
const _fmtTime=iso=>{const d=iso?new Date(iso):null;return d&&!isNaN(d)?d.toLocaleString([], {month:'short',day:'numeric',hour:'2-digit',minute:'2-digit'}):'';};
function _fmtAgeSeconds(sec){sec=Math.max(0,Number(sec)||0);const h=Math.floor(sec/3600),m=Math.floor((sec%3600)/60),s=Math.floor(sec%60);return h?`${h}h ${m}m`:m?`${m}m ${s}s`:`${s}s`; }
function _jobLiveAge(ts){const t=ts?Date.parse(ts):NaN;return Number.isFinite(t)?Math.max(0,(Date.now()-t)/1000):0;}
function _jobLiveHtml(j){
  const known=Number(j.total||0)>0;
  const pct=known?Math.max(0,Math.min(100,Math.round(100*(Number(j.completed||0)/Number(j.total||1))))):0;
  const age=_jobLiveAge(j.updated_at||j.started||'');
  const elapsed=_jobLiveAge(j.started||'');
  const op=String(j.operation||'Working…');
  const stale=age>15;
  const wd=String(j.watchdog_state||'UNKNOWN');
  const wdTone=window.AXLoadingFeedback?.colorForStatus?.(wd)||'var(--text2)';
  const progressStatusColor=known?(window.AXLoadingFeedback?.colorForPercent?.(pct)||'var(--ax-status-fair)'):'var(--accent)';
  const bar=known?`<div data-ax-live-key="module-live-track" class="pt" style="margin-top:5px"><div data-ax-live-key="module-live-fill" class="pb ax-fb-status-progress" style="width:${pct}%;--ax-fb-status-color:${progressStatusColor}"></div></div>`:`<div data-ax-live-key="module-live-track" class="pt" style="margin-top:5px"><div data-ax-live-key="module-live-fill" class="pb progress-purple ind" style="width:35%"></div></div>`;
  const count=known?`${pct}% · ${(j.completed||0).toLocaleString()} / ${(j.total||0).toLocaleString()}`:'progress not yet measurable';
  const pending=known?Math.max(0,Number(j.total||0)-Number(j.completed||0)):null;
  const queueLine=pending!==null?`<div data-ax-live-key="module-live-queue" class="muted" style="font-size:11px;margin-top:4px">Queue: ${pending} file${pending===1?'':'s'} remaining</div>`:'';
  const hbAge=j.watchdog_heartbeat_age!=null?_fmtAgeSeconds(j.watchdog_heartbeat_age):'—';
  const progAge=j.watchdog_progress_age!=null?_fmtAgeSeconds(j.watchdog_progress_age):'—';
  const tracking=(j.type==='TELEGRAM_TRANSFER'||j.type==='UPLOAD_LOCAL'||j.type==='MEDIA_MANAGER')
    ? `<div data-ax-live-key="module-live-tracking" class="muted" style="font-size:10.5px;margin-top:3px">Job ${esc(String(j.id||j.job_id||'—'))} · same execution is also listed in Operations; this is not a second run.</div>` : '';
  return `<div data-ax-live-key="module-live-box" class="job-live-box"><div data-ax-live-key="module-live-line" class="job-live-line"><span class="live-pulse"></span><span data-ax-live-key="module-live-operation" class="job-live-op"><b>ACTIVE</b> · ${esc(op)}</span><span data-ax-live-key="module-live-watchdog" style="margin-left:auto;font-size:10.5px;color:${wdTone}">WD ${esc(wd)}</span></div><div data-ax-live-key="module-live-count" class="muted" style="font-size:11px;margin-top:4px">${esc(count)}</div>${queueLine}${bar}<div data-ax-live-key="module-live-meta" class="job-live-meta ${stale?'job-live-stale':''}">Current: ${esc(String(j.current_item||'').slice(0,90))} · Active ${esc(_fmtAgeSeconds(elapsed))} · Update ${esc(_fmtAgeSeconds(age))} ago · HB ${esc(hbAge)} · Progress ${esc(progAge)}${stale?' · status update is slow':''}</div>${tracking}</div>`;
}
function _jobErrorSummary(error){
  const text=String(error||'').trim();
  const child=text.match(/child job\s+([A-Za-z0-9._-]+)\s+ended\s+(FAILED|CANCELLED|COMPLETED_WITH_ERRORS)/i)
    || text.match(/failed in child job\s+([A-Za-z0-9._-]+)\s+\((FAILED|CANCELLED|COMPLETED_WITH_ERRORS)\)/i);
  if(child)return `Child task ${child[1]} ${child[2].toLowerCase()}. Open the child job in Operations for the detailed cause.`;
  return text.slice(0,220);
}
function renderModStatus(s){
  loadRecoveryStatus();
  if(!$('ops-recovery-status')){const anchor=$('ops-sum')?.parentElement;if(anchor){const d=document.createElement('div');d.id='ops-recovery-status';d.style.marginTop='8px';anchor.parentElement?.appendChild(d);}}
  const act={};(s.jobs||[]).forEach(j=>{if(!act[j.type])act[j.type]=j;});
  const last=s.last_jobs||{};
  const metadataTypes=['METADATA_CHANNEL_PIPELINE','METADATA_SYNC','SUGGESTION_SCAN','ENTITY_IMAGE_SYNC','THUMBNAIL_INDEXER','METADATA_ASSETS'];
  const metadataActive=(s.jobs||[]).filter(j=>metadataTypes.includes(j.type));
  const metadataLast=metadataTypes.map(t=>last[t]).filter(Boolean).sort((a,b)=>String(b.updated_at||'').localeCompare(String(a.updated_at||'')))[0]||null;
  const liveByJobId={};
  ['METADATA_ASSETS','TELEGRAM_TRANSFER','METADATA_SYNC','ENTITY_IMAGE_SYNC','SUGGESTION_SCAN','UPLOAD_LOCAL','MEDIA_MANAGER','CONTENT_AUTOMATION'].forEach(t=>{
    const el=$('ms-'+t);if(!el)return;
    const j=t==='METADATA_ASSETS'?(metadataActive[0]||null):(act[t]||(t==='TELEGRAM_TRANSFER'?(act.UPLOAD_LOCAL||act.MEDIA_MANAGER):null));
    const l=t==='METADATA_ASSETS'?metadataLast:(last[t]||(t==='TELEGRAM_TRANSFER'?(last.UPLOAD_LOCAL||last.MEDIA_MANAGER):null));
    if(j){
      const known=Number(j.total||0)>0,pct=known?Math.round(100*(Number(j.completed||0)/Number(j.total||1))):null;
      let live=['UPLOAD_LOCAL','TELEGRAM_TRANSFER','MEDIA_MANAGER'].includes(j.type)?_jobLiveHtml(j):'';
      const recoveryState=['INTERRUPTED','RECOVERING'].includes(String(j.status||'').toUpperCase());
      if(recoveryState){
        live+=`<div data-ax-live-key="module-recovery-note" style="margin-top:7px;padding:7px 8px;border-radius:8px;border:1px solid var(--line);background:var(--panel2)"><b>${j.status==='INTERRUPTED'?'Recovery is pending.':'Recovery is in progress.'}</b><div class="muted" style="font-size:11px;margin-top:3px">The saved checkpoint is preserved. Nothing will resume until recovery is started.</div></div>`;
        live+=`<div data-ax-live-key="module-recovery-controls" style="display:flex;gap:5px;flex-wrap:wrap;margin-top:7px"><button data-ax-live-key="module-recover" class="btn xs pri" onclick="jobRecover(${jsa(j.id)})">${j.status==='RECOVERING'?'Retry recovery':'Recover & Resume'}</button><button data-ax-live-key="module-recovery-stop" class="btn xs danger ax-icon-btn" onclick="jobCancel(${jsa(j.id)})">${axIcon('stop')} Stop</button><button data-ax-live-key="module-recovery-refresh" class="btn xs ax-icon-btn" onclick="jobRefresh(${jsa(j.id)})">${axIcon('refresh')} Refresh</button></div>`;
      }
      if(j.type==='UPLOAD_LOCAL'){
        const restartPaused=j.status==='PAUSED'&&String(j.operation||'').toLowerCase().includes('interrupted by restart');
        if(restartPaused)live+=`<div data-ax-live-key="module-upload-restart-note" style="margin-top:7px;padding:7px 8px;border-radius:8px;border:1px solid var(--line);background:var(--panel2)"><b>Previous upload task is waiting for your choice.</b><div class="muted" style="font-size:11px;margin-top:3px">It will not start automatically after the app is reopened.</div></div>`;
        live+=`<div data-ax-live-key="module-upload-controls" style="display:flex;gap:5px;flex-wrap:wrap;margin-top:7px">${(j.status==='PAUSED'||j.status==='PAUSING')?`<button data-ax-live-key="module-upload-primary" class="btn xs" onclick="jobResume(${jsa(j.id)})"><svg class="ax-icon" aria-hidden="true"><use href="#ax-play"></use></svg> Resume</button>`:`<button data-ax-live-key="module-upload-primary" class="btn xs ax-icon-btn" onclick="jobPause(${jsa(j.id)})">${axIcon('pause')} Pause</button>`}<button data-ax-live-key="module-upload-skip" class="btn xs ax-icon-btn" onclick="jobSkip(${jsa(j.id)})">${axIcon('next')} Skip current</button><button data-ax-live-key="module-upload-cancel" class="btn xs danger ax-icon-btn" onclick="jobCancel(${jsa(j.id)})">${axIcon('stop')} Cancel</button></div><div data-ax-live-key="module-upload-help" class="muted" style="font-size:10.5px;margin-top:3px">Pause/Skip finish the active media item safely; Cancel stops after the current Telegram upload.</div>`;
      }
      const metaSummary=t==='METADATA_ASSETS'&&metadataActive.length>1?` · ${metadataActive.length} background jobs active`:'';
      const statusText=j.status==='QUEUED'?'Queued':j.status==='PAUSED'?'Paused':j.status==='PAUSING'?'Pausing':j.status==='CANCEL_REQUESTED'?'Canceling':j.status==='INTERRUPTED'?'Interrupted':j.status==='RECOVERING'?'Recovering':'Running';
      const isContentAutomation=t==='CONTENT_AUTOMATION';
      /* Detailed progress belongs in each module's dedicated live panel. Keeping it out of the tiny status badge avoids duplicate status and header-row stretching. */
      liveByJobId[String(j.id??j.job_id??'')]=live;
      const hasDedicatedLive=['CONTENT_AUTOMATION','METADATA_ASSETS','TELEGRAM_TRANSFER','UPLOAD_LOCAL','MEDIA_MANAGER'].includes(t);
      const statusHtml=`<span data-ax-live-key="module-status-label" class="run"><span class="live-pulse"></span>${esc(statusText)}</span>${known?` · <span data-ax-live-key="module-status-count">${pct}% (${(j.completed||0).toLocaleString()} / ${j.total.toLocaleString()})</span>`:''}${metaSummary}${!hasDedicatedLive&&j.operation?` · <span data-ax-live-key="module-status-operation" class="muted">${esc(j.operation.slice(0,140))}</span>`:''}`;
      _axPatchLiveMarkup(el,statusHtml);
      if(isContentAutomation){
        const liveEl=$('ms-CONTENT_AUTOMATION-live');
        if(liveEl){
          const op=String(j.operation||'Processing…');
          const item=String(j.current_item||'');
          const progress=known?`<div data-ax-live-key="content-auto-progress-track" class="automation-job-progress"><span data-ax-live-key="content-auto-progress-fill" style="width:${pct}%"></span></div>`:'';
          const count=known?`${pct}% · ${(j.completed||0).toLocaleString()} / ${Number(j.total||0).toLocaleString()} · ${Math.max(0,Number(j.total||0)-Number(j.completed||0)).toLocaleString()} remaining`:'Progress not yet measurable';
          const summary=`<div data-ax-live-key="content-auto-live-summary" class="automation-live-summary"><div class="automation-live-top"><span class="live-pulse" aria-hidden="true"></span><strong>${esc(statusText)}</strong><span data-ax-live-key="content-auto-count" class="muted">${esc(count)}</span></div><div data-ax-live-key="content-auto-operation" class="automation-live-operation">${esc(op.slice(0,240))}</div>${item?`<div data-ax-live-key="content-auto-item" class="automation-live-item">${esc(item.slice(0,180))}</div>`:''}${progress}</div>`;
          _axPatchLiveMarkup(liveEl,summary+live);
        }
      }
      if(t==='METADATA_ASSETS'){
        const liveEl=$('ms-METADATA_ASSETS-live');
        if(liveEl){
          const liveHtml=metadataActive.map((mj,i)=>{
            const mk=Number(mj.total||0)>0,mp=mk?Math.max(0,Math.min(100,Math.round(100*(Number(mj.completed||0)/Number(mj.total||1))))):0;
            const mn=String(mj.operation||'Processing…'),mi=String(mj.current_item||'');
            const key=esc(String(mj.id??mj.job_id??mj.job_key??`${mj.type||'metadata'}:${i}`));
            const label=mj.type==='METADATA_CHANNEL_PIPELINE'?'Metadata & Assets':mj.type==='METADATA_SYNC'?'Metadata Match':mj.type==='SUGGESTION_SCAN'?'Suggestions':mj.type==='ENTITY_IMAGE_SYNC'?'Performer / Channel Data':mj.type==='THUMBNAIL_INDEXER'?'Snapshots':'Metadata & Assets';
            const mst=String(mj.status||'').toUpperCase(),mrec=mst==='INTERRUPTED'||mst==='RECOVERING',mpaused=mst==='PAUSED'||mst==='PAUSING';
            const mjid=jsa(mj.id??mj.job_id);
            const ctl=`<div data-ax-live-key="metadata-live-controls" style="display:flex;gap:5px;flex-wrap:wrap;margin-top:7px">${mrec?`<button data-ax-live-key="metadata-recover" class="btn xs pri" onclick="jobRecover(${mjid})">${mst==='RECOVERING'?'Retry recovery':'Recover & Resume'}</button>`:(mpaused?`<button data-ax-live-key="metadata-primary" class="btn xs ax-icon-btn" onclick="jobResume(${mjid})">${axIcon('play')} Resume</button>`:`<button data-ax-live-key="metadata-primary" class="btn xs ax-icon-btn" onclick="jobPause(${mjid})">${axIcon('pause')} Pause</button>`)}<button data-ax-live-key="metadata-stop" class="btn xs danger ax-icon-btn" onclick="jobCancel(${mjid})">${axIcon('stop')} ${mrec?'Stop':'Cancel'}</button><button data-ax-live-key="metadata-refresh" class="btn xs ax-icon-btn" onclick="jobRefresh(${mjid})">${axIcon('refresh')} Refresh</button></div>${mrec?`<div data-ax-live-key="metadata-recovery-note" class="muted" style="font-size:11px;margin-top:4px">The saved checkpoint is preserved. Nothing resumes until recovery is started.</div>`:''}`;
            return `<div data-ax-live-key="metadata-live-job:${key}">${_jobLiveHtml({...mj,operation:`${label} · ${mn}`})}${ctl}</div>`;
          }).join('');
          _axPatchLiveMarkup(liveEl,liveHtml);
        }
      }
    }else if(t==='METADATA_ASSETS'){
      const liveEl=$('ms-METADATA_ASSETS-live');if(liveEl)_axPatchLiveMarkup(liveEl,'');
      if(l){
        const cls=l.status==='COMPLETED'?'ok':l.status==='COMPLETED_WITH_ERRORS'?'warn':l.status==='FAILED'?'bad':'muted';
        _axPatchLiveMarkup(el,`<span data-ax-live-key="module-idle-label" class="muted">Idle · last run</span> <span data-ax-live-key="module-idle-status" class="${cls}">${esc(String(l.status||'').toLowerCase())}</span> <span data-ax-live-key="module-idle-time" class="muted">${esc(_fmtTime(l.updated_at))}</span>${(l.status==='FAILED'||l.status==='COMPLETED_WITH_ERRORS')&&l.error?`<div data-ax-live-key="module-idle-error" class="bad" style="font-size:11px">${esc(t==='METADATA_ASSETS'?String(l.error).slice(0,260):_jobErrorSummary(l.error))}</div>`:''}`);
      }else _axPatchLiveMarkup(el,'<span data-ax-live-key="module-idle-label" class="muted">Idle · never run</span>');
    }else if(l){
      if(t==='CONTENT_AUTOMATION'){const liveEl=$('ms-CONTENT_AUTOMATION-live');if(liveEl)_axPatchLiveMarkup(liveEl,'');}
      const cls=l.status==='COMPLETED'?'ok':l.status==='COMPLETED_WITH_ERRORS'?'warn':l.status==='FAILED'?'bad':'muted';
      _axPatchLiveMarkup(el,`<span data-ax-live-key="module-idle-label" class="muted">Idle · last run</span> <span data-ax-live-key="module-idle-status" class="${cls}">${esc(String(l.status||'').toLowerCase())}</span> <span data-ax-live-key="module-idle-time" class="muted">${esc(_fmtTime(l.updated_at))}</span>${(l.status==='FAILED'||l.status==='COMPLETED_WITH_ERRORS')&&l.error?`<div data-ax-live-key="module-idle-error" class="bad" style="font-size:11px">${esc(t==='METADATA_ASSETS'?String(l.error).slice(0,260):_jobErrorSummary(l.error))}</div>`:''}`);
    }else {
      if(t==='CONTENT_AUTOMATION'){const liveEl=$('ms-CONTENT_AUTOMATION-live');if(liveEl)_axPatchLiveMarkup(liveEl,'');}
      _axPatchLiveMarkup(el,'<span data-ax-live-key="module-idle-label" class="muted">Idle · never run</span>');
    }
  });
  const transfer=(s.jobs||[]).filter(j=>['TELEGRAM_TRANSFER','UPLOAD_LOCAL','MEDIA_MANAGER'].includes(j.type)).sort((a,b)=>{const rank=j=>j.type==='TELEGRAM_TRANSFER'?0:j.type==='UPLOAD_LOCAL'?1:2;return rank(a)-rank(b);})[0];
  const tLive=$('ms-TELEGRAM_TRANSFER-live');
  if(tLive){
    if(transfer){
      const detailHtml=liveByJobId[String(transfer.id??transfer.job_id??'')]||_jobLiveHtml(transfer);
      _axPatchLiveMarkup(tLive,detailHtml);
    }else _axPatchLiveMarkup(tLive,'');
  }
  const running=(s.jobs||[]).filter(j=>j.status==='RUNNING').length,queued=(s.jobs||[]).filter(j=>j.status==='QUEUED').length;
  const sum=$('ops-sum');if(sum)sum.textContent=`· ${running} running · ${queued} queued`+(s.paused?' · PAUSED':'')+(s.telegram_connected?' · Telegram connected':' · Telegram offline');
}
async function loadMMPreflight(){
  const el=$('mm-deps');
  const uel=$('u-transfer-mm-deps');
  const r=await api('/api/media-manager/preflight');
  if(!r||!r._ok){if(el)el.textContent='';if(uel)uel.textContent='';return;}
  if(r.telethon.ok&&r.ffmpeg.ok){const msg='<span class="ok" style="color:var(--green)">'+axIcon('check')+' Thumbnail tools ready (FFmpeg fallback)</span>'+(r.av.ok?' · PyAV active':' · PyAV optional');if(el)el.innerHTML=msg;if(uel)uel.innerHTML=msg;return;}
  const miss=[['telethon','Telethon'],['ffmpeg','FFmpeg']].filter(([k])=>!r[k].ok).map(([,n])=>n);
  const msg=`<span style="color:var(--amber)">${axIcon('alert')} ${esc(miss.join(', '))} required for thumbnails.</span>`;if(el)el.innerHTML=msg;if(uel)uel.innerHTML=msg;
}

// ═══════════════════════════════════════════════
// SECTION: ADMIN — SETTINGS
// ═══════════════════════════════════════════════
function renderTelegramApiStatus(r){
  const el=$('tg-api-status'); if(!el)return;
  if(r&&r.api_verified){el.innerHTML=axIcon('check')+' API credentials verified';el.style.color='var(--green)';return;}
  if(r&&r.configured){el.innerHTML=axIcon('check')+' API credentials saved <span class="muted">(account login may still be required)</span>';el.style.color='var(--green)';return;}
  el.innerHTML=axIcon('close')+' API credentials not verified';el.style.color='var(--amber)';
}
async function loadTgSettings(){
  const r=await api('/api/settings/telegram');
  const idEl=$('api-id'),hashEl=$('api-hash'),phoneEl=$('tg-phone'),loginEl=$('login-pan'),logoutBtn=$('tg-logout-btn'),sessionHint=$('tg-sess');
  if(r&&r.api_id&&idEl)idEl.value=r.api_id;
  if(r&&r.api_hash_masked&&hashEl)hashEl.placeholder=r.api_hash_masked;
  if(r&&r.phone&&phoneEl)phoneEl.value=r.phone;
  renderTelegramApiStatus(r);
  if(logoutBtn)logoutBtn.hidden=!(r&&r.connected);
  if(loginEl)loginEl.style.display=(r&&r.connected)?'none':'';
  if(sessionHint&&r&&r.configured&&!r.connected)sessionHint.textContent='API credentials are saved. Choose a sign-in method.';
}
async function logoutTelegramAccount(){
  const button=$('tg-logout-btn'),msg=$('tg-save-msg'),loginEl=$('login-pan'),sessionHint=$('tg-sess');
  if(!(await axConfirm('Log out of the Telegram account on this Archive X installation? This revokes the current Telegram session. Your API ID and API Hash will remain saved. Telegram-backed operations may need you to sign in again.',{title:'Log out of Telegram?',okLabel:'Log out of Telegram',confirmLabel:'Log out of Telegram',danger:true})))return;
  if(button)button.disabled=true;
  if(msg)msg.textContent='Logging out of Telegram…';
  try{
    const r=await api('/api/telegram/logout-account',{method:'POST',body:'{}',cache:'no-store'});
    if(r&&r.ok){
      if(button)button.hidden=true;
      if(loginEl)loginEl.style.display='';
      if(sessionHint)sessionHint.textContent='API credentials are saved. Choose a sign-in method.';
      renderTelegramApiStatus({configured:!!r.credentials_retained,api_verified:false});
      if(msg)msg.textContent='Logged out of Telegram. API credentials remain saved.';
      try{refresh();}catch{}
      return;
    }
    if(msg)msg.textContent='Error: '+(r?.detail||'Could not log out of Telegram.');
  }catch(e){
    if(msg)msg.textContent='Error: '+(e?.message||'Could not log out of Telegram.');
  }finally{
    if(button)button.disabled=false;
  }
}
async function saveTg(){
  const idEl=$('api-id'),hashEl=$('api-hash'),msgEl=$('tg-save-msg');
  if(!idEl||!hashEl)return;
  const r=await api('/api/settings/telegram',{method:'POST',body:JSON.stringify({api_id:idEl.value.trim(),api_hash:hashEl.value.trim()})});
  if(msgEl)msgEl.textContent=r&&r.ok?'Saved.':'Error: '+(r?.detail||'Could not save Telegram settings.');
  renderTelegramApiStatus(r);
  await loadTgSettings();
  setTimeout(()=>{if(msgEl)msgEl.textContent='';},2500);
  refresh();
}
async function testTelegramApi(){
  const msg=$('tg-self-test-msg');
  if(msg)msg.textContent='Testing Telegram API…';
  const r=await api('/api/telegram/self-test',{method:'POST',body:'{}',cache:'no-store'});
  if(r&&r.ok){
    if(msg)msg.textContent=r.authorized?'API reachable; account already authorized.':'API reachable; login is not required for this test.';
    if(msg)msg.style.color='var(--green)';
  }else{
    if(msg)msg.textContent='Test failed: '+(r?.detail||'Telegram API connection failed.');
    if(msg)msg.style.color='var(--red)';
  }
}

async function sendCode(){
  const ph=$('tg-phone').value.trim();
  if(!ph){$('login-msg').textContent='Enter your phone number (+countrycode).';return;}
  $('login-msg').textContent='Sending code…';
  const r=await api('/api/telegram/send_code',{method:'POST',body:JSON.stringify({phone:ph})});
  if(r.ok || r.phone_code_hash || r.status==='CODE_SENT' || r.code_sent){
    $('ph-code').style.display='flex';
    $('login-msg').textContent='Code sent — check Telegram.';
  } else {
    $('login-msg').textContent='Error: '+(r.detail||r.error||'Could not send code');
  }
}
async function verifyCode(){
  $('login-msg').textContent='Verifying…';
  const phone=$('tg-phone').value.trim();
  const r=await api('/api/telegram/verify_code',{method:'POST',body:JSON.stringify({phone,code:$('tg-code').value.trim()})});
  if(r.ok && r.two_factor_required){
    // 2FA needed — show password field, keep panel open
    $('ph-2fa').style.display='flex';
    $('login-msg').textContent='Two-factor password required.';
  } else if(r.ok && !r.two_factor_required){
    $('login-pan').style.display='none';
    $('login-msg').textContent='Logged in!';
    refresh();
    doRefreshChannels();
  } else {
    $('login-msg').textContent='Error: '+(r.detail||r.error||'Verification failed');
  }
}
async function verify2fa(){
  $('login-msg').textContent='Verifying 2FA password…';
  const r=await api('/api/telegram/verify_2fa',{method:'POST',body:JSON.stringify({password:$('tg-2fa').value})});
  if(r.ok && !r.two_factor_required){
    $('login-pan').style.display='none';
    $('login-msg').textContent='Logged in!';
    refresh();
    doRefreshChannels();
  } else {
    $('login-msg').textContent='Error: '+(r.detail||r.error||'Invalid 2FA password');
  }
}

async function loadMetaSettings(){
  const r=await api('/api/settings/metadata');
  if(r.tpdb_token){$('tpdb-key').placeholder='Saved token '+r.tpdb_token+' — leave blank to keep';$('tpdb-status').innerHTML=axIcon('check')+' Saved';$('tpdb-status').style.color='var(--green)';}
  if(r.stashdb_api_key){$('stash-key').placeholder='Saved key '+r.stashdb_api_key+' — leave blank to keep';$('stash-status').innerHTML=axIcon('check')+' Saved';$('stash-status').style.color='var(--green)';}
  renderMetaChannels();
}
async function saveMeta(){
  const body={};
  if($('tpdb-key').value)body.tpdb_token=$('tpdb-key').value.trim();
  if($('stash-key').value)body.stashdb_api_key=$('stash-key').value.trim();
  const r=await api('/api/settings/metadata',{method:'POST',body:JSON.stringify(body)});
  $('meta-msg').textContent=r.ok?'Saved.':'Error: '+(r.detail||'');
  setTimeout(()=>$('meta-msg').textContent='',2500);
  if(r.ok)loadMetaSettings();
}

let _cacheUsageLoadedAt=0;
function _axBytes(n){n=Math.max(0,Number(n)||0);const units=['B','KB','MB','GB','TB'];let i=0;while(n>=1024&&i<units.length-1){n/=1024;i++;}return `${n.toLocaleString(undefined,{maximumFractionDigits:1})} ${units[i]}`;}
async function loadScanSchedule(){
  const r=await api('/api/settings/scan-schedule',{cache:'no-store'});if(!r||r._ok===false)throw new Error(r?.detail||'Could not load scan window');
  const enabled=$('ax-scan-window-enabled'),start=$('ax-scan-window-start'),end=$('ax-scan-window-end');
  if(enabled)enabled.checked=!!r.enabled;if(start)start.value=String(r.start||'01:00');if(end)end.value=String(r.end||'07:00');
  const status=$('ax-scan-window-status');if(status)status.textContent=r.enabled?`Enabled · ${r.start}–${r.end}`:'Disabled';return r;
}
async function saveScanSchedule(){
  const status=$('ax-scan-window-status');if(status)status.textContent='Saving…';
  const body={enabled:!!$('ax-scan-window-enabled')?.checked,start:$('ax-scan-window-start')?.value||'01:00',end:$('ax-scan-window-end')?.value||'07:00'};
  try{const r=await api('/api/settings/scan-schedule',{method:'POST',body:JSON.stringify(body)});if(!r||r._ok===false||!r.ok)throw new Error(r?.detail||'Could not save scan window');if(status)status.textContent=r.enabled?`Saved · ${r.start}–${r.end}`:'Saved · window disabled';}
  catch(e){if(status)status.textContent=`Error: ${e?.message||e}`;}
}
async function loadCacheUsage(force=false){
  const now=Date.now();if(!force&&now-_cacheUsageLoadedAt<60000&&$('ax-cache-usage')?.dataset.loaded==='1')return;
  const out=$('ax-cache-usage'),status=$('ax-cache-usage-status');if(out)out.textContent='Calculating local cache sizes…';if(status)status.textContent='';
  try{const r=await api('/api/settings/cache-usage',{cache:'no-store'});if(!r||r._ok===false)throw new Error(r?.detail||'Could not calculate cache usage');
    _cacheUsageLoadedAt=Date.now();
    const budget=$('ax-cache-budget-gib'),retention=$('ax-cache-retention');
    if(budget&&document.activeElement!==budget){const gib=String(r.range_cache_budget_gib||50);if(![...budget.options].some(o=>o.value===gib))budget.add(new Option(gib+' GiB (custom)',gib));budget.value=gib;}
    const prof=$('ax-cache-profile');if(prof&&document.activeElement!==prof)prof.value=String(r.playback_connection_profile||'balanced');
    if(retention&&document.activeElement!==retention)retention.value=String(r.range_cache_retention||'idle_30m');
    const ra=$('ax-cache-readahead'),ram=$('ax-cache-ram');
    if(ra&&document.activeElement!==ra)ra.value=String(r.playback_readahead_seconds||180);
    if(ram&&document.activeElement!==ram)ram.value=String(r.playback_ram_cache_mb||128);
    const keptBtn=$('ax-release-kept-btn');if(keptBtn)keptBtn.style.display=Number(r.kept_entries)>0?'':'none';
    const retentionNote=r.range_cache_retention==='manual'
      ?'Playback and prefetch ranges are kept until you clear them, except when the size limit requires inactive ranges to be evicted.'
      :r.range_cache_retention==='idle_10m'
        ?'Playback byte caches expire after 10 minutes idle; speculative prefetch also expires after 10 minutes.'
        :'Playback byte caches expire after 30 minutes idle; speculative prefetch expires after 10 minutes.';
    if(out){out.dataset.loaded='1';out.innerHTML=`<div class="v55-data-grid"><div><strong>Persistent video byte cache</strong><br>${esc(_axBytes(r.range_cache_bytes))} of ${esc(_axBytes(r.range_cache_budget_bytes))}</div><div><strong>Cached videos</strong><br>${esc(r.range_cache_entries||0)} entries · ${esc(r.range_cache_complete||0)} complete</div><div><strong>Kept buffers</strong><br>${esc(r.kept_entries||0)} videos · ${esc(_axBytes(r.kept_bytes))}</div><div><strong>Snapshots</strong><br>${esc(_axBytes(r.snapshots_bytes))}</div><div><strong>MP4 index cache</strong><br>${esc(_axBytes(r.mp4_index_bytes))}</div><div><strong>Keyframe index data</strong><br>${esc(_axBytes(r.keyframe_index_bytes))}</div></div><div class="muted" style="margin-top:6px">${esc(retentionNote)} Keyframe data is included in the MP4 index total.</div><div class="muted" style="margin-top:3px">${esc(r.manifests||0)} MP4 index manifests.</div>`;}
  }catch(e){if(out)out.textContent=`Could not read cache sizes: ${e?.message||e}`;}
}
async function savePlaybackCachePolicy(){
  const status=$('ax-cache-usage-status');
  const budget=Number($('ax-cache-budget-gib')?.value);
  const retention=String($('ax-cache-retention')?.value||'idle_30m');
  if(!Number.isInteger(budget)||budget<1||budget>500){if(status)status.textContent='Choose a whole-number limit between 1 and 500 GiB.';return;}
  if(status)status.textContent='Saving cache settings…';
  try{
    const r=await api('/api/settings/cache-policy',{method:'POST',body:JSON.stringify({budget_gib:budget,retention,readahead_seconds:Number($('ax-cache-readahead')?.value||180),ram_mb:Number($('ax-cache-ram')?.value||128),connection_profile:String($('ax-cache-profile')?.value||'balanced')})});
    if(!r||r._ok===false||!r.ok)throw new Error(r?.detail||'Could not save cache settings');
    _cacheUsageLoadedAt=0;
    await loadCacheUsage(true);
    const policy=r.retention==='manual'?'playback cache kept until manually cleared':r.retention==='idle_10m'?'playback cache cleared after 10 minutes idle':'playback cache cleared after 30 minutes idle';
    const evicted=Number(r.evicted_bytes||0);
    if(status)status.textContent=`Saved · ${r.budget_gib} GiB · ${policy}${evicted?` · removed ${_axBytes(evicted)} of inactive cache`:''}`;
  }catch(e){if(status)status.textContent=`Could not save cache settings: ${e?.message||e}`;}
}
async function releaseKeptBuffers(){
  const status=$('ax-cache-usage-status');if(status)status.textContent='Releasing kept buffers…';
  try{const r=await api('/api/settings/release-kept-video-buffers',{method:'POST',body:JSON.stringify({})});
    if(!r||r._ok===false||!r.ok)throw new Error(r?.detail||'Could not release kept buffers');
    try{localStorage.removeItem('ax_nobuf_kept_buffers_v1');localStorage.removeItem('ax_nobuf_buffer_decisions_v1');}catch(e){}
    _cacheUsageLoadedAt=0;await loadCacheUsage(true);
    if(status)status.textContent=`Released ${r.released||0} kept video${(r.released||0)===1?'':'s'}; they now follow the retention setting.`;
  }catch(e){if(status)status.textContent=`Could not release kept buffers: ${e?.message||e}`;}
}
async function clearVideoByteCache(){
  if(!(await axConfirm('Clear all persistently downloaded video bytes? Snapshots, metadata and MP4 indexes will be kept.',{title:'Clear video-byte cache?',okLabel:'Clear cache',confirmLabel:'Clear cache',danger:true})))return;
  const status=$('ax-cache-usage-status');if(status)status.textContent='Clearing video byte cache…';
  try{const r=await api('/api/settings/clear-video-byte-cache',{method:'POST',body:'{}'});if(!r||r._ok===false||!r.ok)throw new Error(r?.detail||'Could not clear video byte cache');if(status)status.textContent=`Cleared ${_axBytes(r.bytes_cleared)} from ${r.entries_cleared||0} videos`;_cacheUsageLoadedAt=0;await loadCacheUsage(true);}
  catch(e){if(status)status.textContent=`Could not clear cache: ${e?.message||e}`;}
}

async function testKey(which){
  const endpoints={tpdb:'/api/settings/check-tpdb',stash:'/api/settings/check-stashdb'};
  const statusIds={tpdb:'tpdb-status',stash:'stash-status'};
  const el=$(statusIds[which]);
  el.textContent='Testing…';el.style.color='var(--text2)';
  // Save first if there's a new value
  const inputIds={tpdb:'tpdb-key',stash:'stash-key'};
  const fieldNames={tpdb:'tpdb_token',stash:'stashdb_api_key'};
  const v=$(inputIds[which]).value.trim();
  if(v)await api('/api/settings/metadata',{method:'POST',body:JSON.stringify({[fieldNames[which]]:v})});
  const r=await api(endpoints[which],{method:'POST'});
  if(r.ok){el.innerHTML=axIcon('check')+' Working';el.style.color='var(--green)';}
  else{el.innerHTML=axIcon('close')+' '+esc(r.error||'Failed');el.style.color='var(--red)';}
}

async function loadWL(){
  const r=await api('/api/settings/weblogin');
  if(r&&r.username!==undefined){$('web-user').value=r.username;}
  const w=$('wl-default');if(w)w.style.display=(r&&r.default_login)?'block':'none';
}
async function saveWL(){
  const pw=$('web-pass').value, pw2=$('web-pass2').value;
  if(pw&&pw!==pw2){$('wl-msg').textContent='The two passwords do not match.';return;}
  if(pw&&pw.length<12){$('wl-msg').textContent='Use at least 12 characters.';return;}
  const cur=($('web-pass-current')||{}).value||'';
  if(!cur){$('wl-msg').textContent='Enter your current password to change login details.';return;}
  const r=await api('/api/settings/weblogin',{method:'POST',body:JSON.stringify({username:$('web-user').value.trim(),password:pw,current_password:cur})});
  $('wl-msg').textContent=r.ok?(pw?'Saved. Other devices were signed out.':'Saved.'):'Error: '+(r.detail||'');
  if(r.ok){$('web-pass').value='';$('web-pass2').value='';if($('web-pass-current'))$('web-pass-current').value='';loadWL();}
  setTimeout(()=>$('wl-msg').textContent='',5000);
}

// ═══════════════════════════════════════════════
// SECTION: UTILS
// ═══════════════════════════════════════════════
function setLinks(){
  const h=location.host;
  const c=$('ca-link');if(c)c.textContent='https://'+h+'/ca.crt';
  const a=$('app-link');if(a)a.textContent='https://'+h;
}


// ═══════════════════════════════════════════════
// SECTION: v18.8 — THUMBNAILS (fetch-based: cached, retried, never lost)
// ═══════════════════════════════════════════════
const _thumbURL=new Map(), _thumbWait=new Map();
const _thumbQ=[];let _thumbActive=0;
const _thumbObs=('IntersectionObserver' in window)?new IntersectionObserver(es=>{
  es.forEach(e=>{if(e.isIntersecting){_thumbObs.unobserve(e.target);_thumbQ.push(e.target);}});
  _pumpThumbs();
},{rootMargin:'500px'}):null;
function hydrateThumbs(root){
  (root||document).querySelectorAll('img[data-src]:not([data-q])').forEach(img=>{
    img.dataset.q='1';
    const u=_thumbURL.get(img.dataset.src);
    if(u){img.src=u;img.style.display='';img.closest('.vt')?.classList.add('thumb-loaded');return;}
    if(_thumbObs)_thumbObs.observe(img);else _thumbQ.push(img);
  });
  _pumpThumbs();
}
function _pumpThumbs(){
  const max=_P.playing()?3:6;
  while(_thumbActive<max&&_thumbQ.length){
    const img=_thumbQ.shift();
    if(!img.isConnected)continue;
    const src=img.dataset.src, have=_thumbURL.get(src);
    if(have){img.src=have;img.style.display='';img.closest('.vt')?.classList.add('thumb-loaded');continue;}
    _thumbActive++;
    _loadThumb(src).then(u=>{if(u){img.src=u;img.style.display='';img.closest('.vt')?.classList.add('thumb-loaded');const ph=img.nextElementSibling;if(ph&&ph.classList.contains('ph'))ph.style.display='none';}else _thumbFail(img);})
      .finally(()=>{_thumbActive=Math.max(0,_thumbActive-1);_pumpThumbs();});
  }
}
async function _loadThumb(src){
  if(_thumbURL.has(src))return _thumbURL.get(src);
  if(_thumbWait.has(src))return _thumbWait.get(src);
  const p=(async()=>{
    for(let a=0;a<4;a++){
      try{
        const r=await fetch(src,{credentials:'include'});
        if(r.ok){
          if(r.headers.get('X-Thumbnail-Status')==='pending')return null;
          const b=await r.blob();if(b.size){const u=URL.createObjectURL(b);_thumbURL.set(src,u);
          if(_thumbURL.size>900){const k=_thumbURL.keys().next().value;URL.revokeObjectURL(_thumbURL.get(k));_thumbURL.delete(k);}
          return u;}}
        if(r.status===401){showLP();return null;}
        if(r.status===404)return null;
      }catch(e){}
      await sl([2000,5000,12000][a]||12000);
    }
    return null;
  })();
  _thumbWait.set(src,p);
  try{return await p;}finally{_thumbWait.delete(src);}
}
function _thumbFail(img){
  img.style.display='none';
  const vt=img.closest('.vt');if(vt)vt.classList.remove('thumb-loaded');
  const ph=img.nextElementSibling; if(ph&&ph.classList.contains('ph'))ph.style.display='flex';
  if(!img.dataset.late){img.dataset.late='1';setTimeout(()=>{if(img.isConnected){delete img.dataset.late;_thumbQ.push(img);_pumpThumbs();}},60000);}
}

// ═══════════════════════════════════════════════
// SECTION: SMALL HELPERS
// ═══════════════════════════════════════════════
const _isIOS=()=>/iPad|iPhone|iPod/.test(navigator.userAgent)||(navigator.platform==='MacIntel'&&navigator.maxTouchPoints>1);
const _isAndroid=()=>/Android/i.test(navigator.userAgent);
const _isTouch=()=>{try{return typeof matchMedia==='function'?matchMedia('(pointer:coarse)').matches:('ontouchstart' in window||Number(navigator.maxTouchPoints||0)>0);}catch(e){return Number(navigator.maxTouchPoints||0)>0;}};
const _canHover=()=>{try{return typeof matchMedia==='function'?matchMedia('(hover:hover) and (pointer:fine)').matches:!_isTouch();}catch(e){return !_isTouch();}};
const _newAbortController=()=>{try{return typeof AbortController==='function'?new AbortController():null;}catch(e){return null;}};
if(!(window.CSS&&typeof window.CSS.escape==='function')){window.CSS=window.CSS||{};window.CSS.escape=s=>String(s).replace(/[^a-zA-Z0-9_\u00A0-\uFFFF-]/g,c=>'\\'+c.charCodeAt(0).toString(16)+' '); }
const _isRemote=()=>!['127.0.0.1','localhost','::1','[::1]'].includes(location.hostname);
const vkey=(cid,id)=>String(cid)+':'+String(id);
const _snapshotFrameCache=window.ArchiveXPreviewModules.createPreviewFrameCache({keyFor:vkey});
function getOpt(k,d){const v=localStorage.getItem(k);return v===null?d:v==='1';}
function setOpt(k,v){try{localStorage.setItem(k,v?'1':'0');}catch(e){}}
let _toastT=null;
function toast(msg,ms=3500){const t=$('toast');if(!t)return;t.textContent=String(msg??'');t.hidden=false;t.classList.add('is-visible');clearTimeout(_toastT);_toastT=setTimeout(()=>{t.classList.remove('is-visible');t.hidden=true;},ms);}
const thumbUrl=v=>`/api/thumb/${encodeURIComponent(v._cid)}/${v.id}?r=f3${v.thumbnail_version?`&v=${encodeURIComponent(v.thumbnail_version)}`:''}`;

// ═══════════════════════════════════════════════
// SECTION: GRID — cards are updated in place (no full redraw)
// ═══════════════════════════════════════════════
// "Matched": details came from a metadata provider (same rule the database
// uses when it back-fills identification_state).

let _videoCardActionMenu=null;
function closeVideoCardActionMenu(){
  const state=_videoCardActionMenu;_videoCardActionMenu=null;
  if(state?.menu?.isConnected)state.menu.remove();
  if(state?.trigger?.isConnected)state.trigger.setAttribute('aria-expanded','false');
  document.removeEventListener('pointerdown',_videoCardActionMenuOutside,true);
  document.removeEventListener('keydown',_videoCardActionMenuKeydown,true);
  document.removeEventListener('scroll',_videoCardActionMenuScroll,true);
  window.removeEventListener('resize',closeVideoCardActionMenu);
}
function _videoCardActionMenuOutside(ev){
  const s=_videoCardActionMenu;if(!s)return;
  if(s.menu.contains(ev.target)||s.trigger===ev.target||s.trigger.contains(ev.target))return;
  closeVideoCardActionMenu();
}
function _videoCardActionMenuKeydown(ev){if(ev.key==='Escape'){ev.preventDefault();closeVideoCardActionMenu();}}
function _videoCardActionMenuScroll(){if(_videoCardActionMenu)closeVideoCardActionMenu();}
function toggleVideoCardActionMenu(ev,cid,mid,name){
  ev?.preventDefault?.();ev?.stopPropagation?.();
  const trigger=ev?.currentTarget;if(!trigger)return;
  if(_videoCardActionMenu?.trigger===trigger){closeVideoCardActionMenu();return;}
  closeVideoCardActionMenu();
  const menu=document.createElement('div');menu.className='video-card-action-popover';menu.id='video-card-action-popover';menu.setAttribute('role','menu');menu.setAttribute('aria-label','Video actions');
  const heading=document.createElement('div');heading.className='video-card-action-heading';heading.textContent='Video actions';heading.setAttribute('aria-hidden','true');menu.appendChild(heading);
  const addAction=(label,iconMarkup,handler,{danger=false,title=''}={})=>{
    const button=document.createElement('button');button.type='button';button.className=`video-card-action-option${danger?' danger':''}`;button.setAttribute('role','menuitem');button.title=title||label;
    const icon=document.createElement('span');icon.className='video-card-action-icon';icon.setAttribute('aria-hidden','true');icon.innerHTML=iconMarkup;
    const text=document.createElement('span');text.className='video-card-action-label';text.textContent=label;
    button.append(icon,text);
    button.addEventListener('click',ev2=>{ev2.preventDefault();ev2.stopPropagation();closeVideoCardActionMenu();handler();});
    menu.appendChild(button);
  };
  addAction('Move / delete source',axIcon('transfer'),()=>openVideoMoveDialog(String(cid),Number(mid),String(name||'')),{title:'Copy to another Telegram channel, verify the copy, then delete source'});
  addAction('Send to Telegram',ICO.tg,()=>sendTG(Number(mid),String(cid)));
  addAction('Mapping / report',axIcon('edit'),()=>openVideoMapping(Number(mid),String(cid)));
  addAction('Open in VLC',ICO.vlc,()=>_isRemote()?openVLCDevice(Number(mid),String(cid)):sendPD('vlc',Number(mid),String(cid)));
  if(!_isIOS())addAction('Open in MPC-HC',ICO.mpc,()=>sendPD('mpc',Number(mid),String(cid)));
  const divider=document.createElement('div');divider.className='video-card-action-divider';divider.setAttribute('role','separator');menu.appendChild(divider);
  addAction('Delete',axIcon('trash'),()=>deleteLibraryVideo(String(cid),Number(mid),String(name||'')),{danger:true,title:'Permanently delete from Telegram and remove from the Archive X Library'});
  menu.addEventListener('click',ev2=>ev2.stopPropagation());(document.getElementById('ax-ui-scale-root')||document.body).appendChild(menu);
  const r=trigger.getBoundingClientRect(),zoom=Math.max(.1,Number(getComputedStyle(document.documentElement).getPropertyValue('--ax-ui-zoom'))||1),w=menu.offsetWidth||190,h=menu.offsetHeight||84,pad=8;
  const vw=window.innerWidth/zoom,vh=window.innerHeight/zoom,leftEdge=r.left/zoom,rightEdge=r.right/zoom,topEdge=r.top/zoom,bottomEdge=r.bottom/zoom;
  const left=Math.max(pad,Math.min(vw-w-pad,rightEdge-w));
  const top=(bottomEdge+h+pad<=vh)?bottomEdge+4:Math.max(pad,topEdge-h-4);
  menu.style.left=`${left}px`;menu.style.top=`${top}px`;
  trigger.setAttribute('aria-expanded','true');
  _videoCardActionMenu={menu,trigger,cid:String(cid),mid:Number(mid)};
  document.addEventListener('pointerdown',_videoCardActionMenuOutside,true);
  document.addEventListener('keydown',_videoCardActionMenuKeydown,true);
  document.addEventListener('scroll',_videoCardActionMenuScroll,true);
  window.addEventListener('resize',closeVideoCardActionMenu);
  const first=menu.querySelector('button');first?.focus({preventScroll:true});
}

function _videoCardResolvePerformer(value){
  const raw=(value&&typeof value==='object')?value:{name:value};
  const name=String(raw.name||raw.performer_name||'').trim();
  if(!name)return null;
  const key=name.normalize('NFKC').toLocaleLowerCase();
  const local=(Array.isArray(_suggestionPerformers)?_suggestionPerformers:[]).find(p=>String(p?.name||'').trim().normalize('NFKC').toLocaleLowerCase()===key);
  const id=Number(raw.id||raw.performer_id||local?.id||0)||null;
  return {name,id,image:String(raw.image||local?.image||''),count:Number(local?.video_count||0)};
}

function _videoCardPerformerRow(values,max=2){
  // v29.4.312: reference layout — overlapping avatars, then "First Name +N". Every performer stays clickable.
  const list=(Array.isArray(values)?values:[]).map(_videoCardResolvePerformer).filter(Boolean);
  if(!list.length)return '<span class="muted video-performer-unknown">Unknown performer</span>';
  const open=p=>`onclick="event.stopPropagation();openPerformerByName(${jsa(p.name)})" onkeydown="if(event.key==='Enter'||event.key===' '){event.preventDefault();event.stopPropagation();openPerformerByName(${jsa(p.name)})}"`;
  const avatar=p=>{const initial=esc(p.name.slice(0,1).toLocaleUpperCase());
    const inner=p.id?`<img class="video-performer-avatar" src="/api/performers/${p.id}/image?v=${encodeURIComponent(p.image)}" alt="" aria-hidden="true" loading="lazy" decoding="async" onerror="this.style.display='none';if(this.nextElementSibling)this.nextElementSibling.style.display='inline-flex'"><span class="video-performer-avatar-fallback" style="display:none">${initial}</span>`:`<span class="video-performer-avatar-fallback">${initial}</span>`;
    return `<span class="video-performer-avatar-wrap axf-perf-avatar" role="button" tabindex="-1" title="${esc(p.name)}" ${open(p)}>${inner}</span>`;};
  const stack=list.slice(0,max).map(avatar).join('');
  const rest=list.slice(1);
  const more=rest.length?`<span class="video-more-chip axf-perf-more" title="${esc(rest.map(p=>p.name).join(', '))}">+${rest.length}</span>`:'';
  return `<span class="axf-perf-stack">${stack}</span><span class="video-performer-chip axf-perf-label" role="button" tabindex="0" title="Open ${esc(list[0].name)}'s performer profile" ${open(list[0])}><span class="video-performer-name">${esc(list[0].name)}</span>${more}</span>`;
}
function _videoCardChannelTitle(v,options={}){
  const cid=String(v?._cid??v?.source_channel??'');
  const cached=[...(Array.isArray(_chans)?_chans:[]),...(Array.isArray(_sourceChans)?_sourceChans:[])].find(c=>String(c?.telegram_id??'')===cid);
  const channel=channelDisplayName(cid,v?.channel_title||cached?.title);
  const studio=String(v?.studios?.[0]||v?.studio||v?.site_name||'').trim();
  return options.includeStudio!==false&&studio?`${studio} (${channel})`:channel;
}
function _videoCardCodecLabel(value){
  const raw=String(value||'').trim();if(!raw)return '';
  const key=raw.toLowerCase().replace(/[ ._-]/g,'');
  const labels={hevc:'HEVC',h265:'HEVC',h264:'H.264',avc:'H.264',avc1:'H.264',vp9:'VP9',vp8:'VP8',av1:'AV1',mpeg4:'MPEG-4',mpeg2video:'MPEG-2'};
  return labels[key]||raw.toUpperCase();
}
function axResLabel(w,h){
  // Orientation/aspect-aware badge: a 1080x1920 portrait clip is "1080p", not "1920p".
  w=Number(w||0);h=Number(h||0);
  if(!(w>0)&&!(h>0))return '';
  if(!(w>0)||!(h>0)){const only=Math.max(w,h);return only>=2160?'4K':only+'p';}
  const long=Math.max(w,h),short=Math.min(w,h);
  if(long>=3800||short>=2100)return '4K';
  if(long>=2500)return '1440p';
  if(long>=1900)return '1080p';
  if(long>=1260)return '720p';
  return short+'p';
}
function cardVideoPropertiesHTML(v){
  const parts=[];
  const height=Number(v?.height||0);
  {const rl=axResLabel(v?.width,height);if(rl)parts.push(rl);}
  const codec=_videoCardCodecLabel(v?.codec||v?.video_codec||v?.vcodec);
  if(codec)parts.push(codec);
  const duration=Number(v?.duration||0);
  if(duration>0)parts.push(fdur(duration));
  return `<div class="video-card-properties" aria-label="Video properties">${parts.length?parts.map((part,i)=>`${i?'<span class="video-property-separator" aria-hidden="true">·</span>':''}<span class="video-property">${esc(part)}</span>`).join(''):'<span class="video-property-unknown">Video properties unavailable</span>'}</div>`;
}
function cardInfoHTML(v,options={}){
  const telegramName=v.telegram_filename||v.name||('video '+v.id);
  const nm=(v.api_title||v.title||'').trim()||telegramName;
  const performers=(v.performer_meta||[]).length ? v.performer_meta : (v.performers||[]);
  const pp=_videoCardPerformerRow(performers,2);
  const titleActions=String(options.titleActions||'');
  const includeStudioInChannel=options.includeStudioInChannel!==false;
  const channelTitle=_videoCardChannelTitle(v,{includeStudio:includeStudioInChannel});
  const channelTitleDescription=includeStudioInChannel?'Studio and Telegram channel':'Telegram channel';
  if(!options.library){
    const tags=v.tags||[];
    const smartTagsHTML=_smartTagMarkup(tags,{maxTags:3,maxCategories:2,hash:true,emptyText:'No specific tags'});
    return `<div class="video-title-row"><div class="vn" title="${esc(nm)}">${esc(nm)}</div>${titleActions}</div><div class="video-card-entities video-card-performers">${pp}</div><div class="video-card-channel" title="${channelTitleDescription}: ${esc(channelTitle)}"><span class="video-card-channel-brand">${axIcon('brand-telegram')}</span>${esc(channelTitle)}</div>${cardVideoPropertiesHTML(v)}<div class="video-card-entities video-card-tags">${smartTagsHTML}</div>`;
  }
  const libraryChannelTitle=channelTitle.replace(/\s+\(([^()]*)\)$/, ' · $1');
  const tags=v.tags||[];
  const tagPresentation=_smartTagPresentation(tags);
  const visibleTags=tagPresentation.tags.slice(0,2);
  const tagTones=['accent','violet','ok','sky'];
  const hiddenTagCount=Math.max(0,tagPresentation.tags.length-visibleTags.length)+(tagPresentation.categories||[]).length;
  const smartTagsHTML=visibleTags.map((t,i)=>`<span class="video-tag-chip ax2-tag-tone-${tagTones[i%tagTones.length]}" title="${esc(t)}" onclick="event.stopPropagation();openTagByName(${jsa(t)})">${esc(t)}</span>`).join('')+(hiddenTagCount?`<span class="video-more-chip" title="${hiddenTagCount} more tags or categories">+${hiddenTagCount}</span>`:'');
  return `<div class="video-title-row"><div class="vn" title="${esc(nm)}">${esc(nm)}</div>${titleActions}</div><div class="video-card-entities video-card-performers">${pp}</div><div class="video-card-channel" title="${channelTitleDescription}: ${esc(libraryChannelTitle)}"><span class="video-card-channel-brand">${axIcon('brand-telegram')}</span><span class="video-card-channel-label">${esc(libraryChannelTitle)}</span></div><div class="video-card-entities video-card-tags">${smartTagsHTML}</div>`;
}
function cardBadgesHTML(v,options={}){
  const h=v.height||0;
  const rb=h?`<span class="badge" style="background:rgba(0,0,0,.65);color:#fff">${axResLabel(v.width,h)}</span>`:'';
  const ready=!(v.library_ready===0||v.library_ready===false);
  const prep=ready?'':`<span class="badge" title="Library preparation: ${esc(v.library_state||'Pending')}" style="background:rgba(120,92,24,.88);color:#ffe5a0">${esc(v.library_state||'Preparing')}</span>`;
  const studio=options.showStudio===false?'':`<span class="badge video-studio-badge" style="background:rgba(26,50,96,.8);color:var(--accent)">${esc(v.site_name||v.studio||'Unknown studio')}</span>`;
  return rb+prep+studio;
}
function cardHTML(v){
  const k=vkey(v._cid,v.id), isp=String(v._cid)===String(_aChan)&&String(v.id)===String(_aMsg);
  const duration=Math.max(0,Number(v.duration)||0);
  return `<div class="vc ax2-library-card${isp?' playing':''}" tabindex="0" data-ax-keyboard-activate="true" aria-label="${esc(v.api_title||v.title||v.name||('Video '+v.id))}" data-ax-live-key="card:${esc(k)}" data-k="${esc(k)}" data-cid="${esc(v._cid)}" data-mid="${Number(v.id)}" data-duration="${duration}">
    <div class="vt"><input type="checkbox" class="map-select" style="display:none;position:absolute;top:7px;left:7px;z-index:5;width:17px;height:17px" onclick="event.stopPropagation();toggleMapSelection(this,${jsa(v._cid)},${Number(v.id)})"><div class="vc-preview-deck"><img class="vc-thumb-fallback" data-src="${thumbUrl(v)}" alt="" decoding="async"><canvas class="vc-preview-canvas preview-a" aria-hidden="true"></canvas><canvas class="vc-preview-canvas preview-b" aria-hidden="true"></canvas></div><span class="ax-video-hover-play" aria-hidden="true">${axIcon('play')}</span><div class="vbadges">${cardBadgesHTML(v)}</div><span class="video-duration-overlay">${duration>0?esc(fdur(duration)):''}</span><button class="fav-btn${v._fav?' on':''}" onclick="event.stopPropagation();togFav(${Number(v.id)},${jsa(v._cid)},this)" aria-label="Favourite" title="Favourite" aria-pressed="${v._fav?'true':'false'}">${axIcon('star')}</button></div><div class="vi">${cardInfoHTML(v,{library:true})}</div></div>`;
}
// Shared snapshot manifest/frame cache is isolated in player/preview-frame-cache.js.
// These thin wrappers keep the existing card-preview call sites and cache contract stable.
async function _getSnapshotManifest(cid,mid,force=false){return _snapshotFrameCache.getManifest(cid,mid,force);}

async function _loadCachedTimelineFrame(cid,mid,index,cache,wait,maxFrames=8,revision=''){return _snapshotFrameCache.loadFrame(cid,mid,index,cache,wait,maxFrames,revision);}
async function _loadCachedGridSprite(cid,mid,manifest){return _snapshotFrameCache.loadGridSprite(cid,mid,manifest);}

// Snapshot-grid implementation lives in player/snapshot-grid.js. The player keeps
// these function names as a stable integration surface for existing UI events.
let _snapshotGridModule=null;
let _liveSeekPreviewModule=null;
function pSnapshotGridHide(){_snapshotGridModule?.hide();}
async function pLoadPreplaySnapshotGrid(cid,mid,force=false,onFirstImage=null){if(!_snapshotGridModule)return;return _snapshotGridModule.load(cid,mid,force,onFirstImage);}
function pSnapshotGridShow(animate=true){return _snapshotGridModule?.show(animate);}
function _pSnapshotGridClick(event){return _snapshotGridModule?.click(event);}

// Card hover previews use independent, cached video-derived frames.
function _drawTimelineCardImage(canvas,img){
  if(!canvas||!img)return;
  const rect=canvas.getBoundingClientRect(),cw=Math.max(1,Math.round(rect.width||300)),ch=Math.max(1,Math.round(rect.height||170)),dpr=window.ArchiveXPreviewModules.getCanvasDpr(cw,ch);
  canvas.width=Math.round(cw*dpr);canvas.height=Math.round(ch*dpr);
  const ctx=canvas.getContext('2d');ctx.setTransform(dpr,0,0,dpr,0,0);ctx.imageSmoothingEnabled=true;ctx.imageSmoothingQuality='high';ctx.fillStyle='#101010';ctx.fillRect(0,0,cw,ch);
  const scale=Math.max(cw/Math.max(1,img.naturalWidth||1),ch/Math.max(1,img.naturalHeight||1));
  const dw=Math.max(1,Math.round((img.naturalWidth||1)*scale)),dh=Math.max(1,Math.round((img.naturalHeight||1)*scale));
  ctx.drawImage(img,0,0,img.naturalWidth||1,img.naturalHeight||1,(cw-dw)/2,(ch-dh)/2,dw,dh);
}

const _axActiveCardPreviews=new Set();
function _stopAllCardPreviews(){
  // Closing the player must not walk the whole 9K+ video catalogue. Only hover
  // previews that actually started have timers/decoders that need teardown.
  for(const card of Array.from(_axActiveCardPreviews))_cardPreviewStop(card);
}
function _cardPreviewState(card){
  if(!card._axPreview)card._axPreview={idx:0,displayedIdx:-1,img:null,timer:0,load:null,downX:0,downY:0,moved:false,active:false,layer:'a',transitionSeq:0,requestSeq:0,frameMode:'snapshot',frames:new Map(),frameLoads:new Map(),failedFrames:new Set(),frameTimes:[],revision:'',duration:0,durationLabel:null,targetRatio:0,currentRatio:0,lastCommittedX:null,hysteresisPx:10,raf:0,scrubFeedbackActive:false};
  const st=card._axPreview;
  if(!st.frames)st.frames=new Map();
  if(!st.frameLoads)st.frameLoads=new Map();
  if(!st.failedFrames)st.failedFrames=new Set();
  if(!Array.isArray(st.frameTimes))st.frameTimes=[];
  if(!Number.isFinite(Number(st.requestSeq)))st.requestSeq=0;
  if(!Number.isFinite(Number(st.targetRatio)))st.targetRatio=0;
  if(!Number.isFinite(Number(st.currentRatio)))st.currentRatio=0;
  if(st.lastCommittedX===undefined)st.lastCommittedX=null;
  if(!Number.isFinite(Number(st.hysteresisPx)))st.hysteresisPx=10;
  if(!Number.isFinite(Number(st.raf)))st.raf=0;
  if(st.durationLabel===undefined)st.durationLabel=null;
  if(typeof st.scrubFeedbackActive!=='boolean')st.scrubFeedbackActive=false;
  return st;
}
function _cardPreviewCanvas(card,layer){return card?.querySelector(`.vc-preview-canvas.preview-${layer}`)||null;}
function _cardPreviewShowLayer(card,layer){
  const vt=card?.querySelector('.vt,.unx-art');if(!vt)return;
  if(!vt.classList.contains('previewing')){
    vt.classList.add('previewing');
    vt.style.setProperty('--ax-card-preview-transition',`${CARD_PREVIEW_TRANSITION_MS}ms`);
  }
  const fb=vt.querySelector('.vc-thumb-fallback');
  if(fb)fb.style.opacity='0';
  vt.classList.toggle('preview-layer-a',layer==='a');
  vt.classList.toggle('preview-layer-b',layer==='b');
  vt.querySelectorAll('.vc-preview-canvas').forEach(canvas=>{
    canvas.classList.toggle('active',canvas.classList.contains(`preview-${layer}`));
  });
}
const CARD_PREVIEW_FRAME_MS=300;
// Keep the crossfade close to the frame interval for a continuous preview.
const CARD_PREVIEW_TRANSITION_MS=30;
function _cardPreviewRatioFromPointer(card,event){
  const image=card?.querySelector('.vt'),rect=image?.getBoundingClientRect();
  if(!rect||!(rect.width>0))return 0;
  return Math.max(0,Math.min(1,((Number(event?.clientX)||0)-rect.left)/rect.width));
}
function _cardPreviewIndexFromPointer(card,event){
  return Math.min(35,Math.floor(_cardPreviewRatioFromPointer(card,event)*36));
}
function _cardPreviewPointerX(card,event){
  // Screen-space X keeps the deadzone stable even if card hover transforms alter the image's bounding box.
  return Math.max(0,Number(event?.clientX)||0);
}
function _cardPreviewShouldCommitPointer(card,event,index){
  const st=_cardPreviewState(card),candidate=Math.max(0,Math.min(35,Number(index)||0));
  const x=_cardPreviewPointerX(card,event);
  if(st.lastCommittedX===null||!Number.isFinite(Number(st.lastCommittedX))){st.lastCommittedX=x;return true;}
  if(candidate===st.idx)return false;
  if(Math.abs(x-Number(st.lastCommittedX))<st.hysteresisPx)return false;
  st.lastCommittedX=x;
  return true;
}
function _cardPreviewScheduleScrubLerp(card){
  if(!card?.classList?.contains('vc'))return;
  const st=_cardPreviewState(card);if(st.raf)return;
  const tick=()=>{
    st.raf=0;
    if(!card.isConnected){st.scrubFeedbackActive=false;st.targetRatio=st.currentRatio=0;return;}
    const delta=st.targetRatio-st.currentRatio;
    st.currentRatio=Math.abs(delta)<0.0015?st.targetRatio:st.currentRatio+delta*0.25;
    const ratio=Math.max(0,Math.min(1,st.currentRatio)),percent=ratio*100;
    const bar=card.querySelector('.ax-card-watch-progress'),fill=bar?.querySelector('.ax-card-watch-progress-fill');
    if(bar&&fill&&st.scrubFeedbackActive){
      bar.dataset.previewing='1';fill.style.width=`${percent.toFixed(2)}%`;
      bar.setAttribute('aria-label','Preview frame position');bar.setAttribute('aria-valuenow',String(Math.round(percent)));
    }
    if(!st.scrubFeedbackActive&&Math.abs(st.currentRatio)<0.005){
      st.currentRatio=0;st.targetRatio=0;
      if(bar){delete bar.dataset.previewing;bar.setAttribute('aria-label','Playback progress');}
      if(fill){
        const saved=Number(bar?.dataset.playbackPercent);
        if(Number.isFinite(saved)){fill.style.width=`${saved.toFixed(2)}%`;bar?.setAttribute('aria-valuenow',String(Math.round(saved)));}
        else{fill.style.removeProperty('width');bar?.setAttribute('aria-valuenow','0');}
      }
      return;
    }
    // Stop scheduling frames once the playhead reaches its target; the next pointer event restarts the loop.
    if(st.scrubFeedbackActive&&Math.abs(st.targetRatio-st.currentRatio)<0.0015)return;
    st.raf=requestAnimationFrame(tick);
  };
  st.raf=requestAnimationFrame(tick);
}
function _cardPreviewSetTime(card,index,restore=false){
  const st=_cardPreviewState(card),label=card?.querySelector('.video-duration-overlay');
  if(!label)return;
  if(restore){
    if(st.durationLabel!==null)label.textContent=st.durationLabel;
    st.durationLabel=null;
    return;
  }
  if(st.durationLabel===null)st.durationLabel=label.textContent;
  const frameIndex=Math.max(0,Math.min(35,Number(index)||0));
  let seconds=Number(st.frameTimes[frameIndex]);
  if(!Number.isFinite(seconds)||seconds<0)seconds=st.duration>0?st.duration*frameIndex/35:frameIndex;
  if(st.duration>0)seconds=Math.min(st.duration,seconds);
  label.textContent=fdur(seconds);
}
function _cardPreviewUpdateScrubFeedback(card,index,active=true,ratio=null){
  if(!card?.classList?.contains('vc'))return;
  const st=_cardPreviewState(card),frameIndex=Math.max(0,Math.min(35,Number(index)||0));
  const hasExplicitRatio=ratio!==null&&ratio!==undefined&&Number.isFinite(Number(ratio));
  const frameTime=Number(st.frameTimes[frameIndex]);
  const frameRatio=st.duration>0&&Number.isFinite(frameTime)?Math.max(0,Math.min(1,frameTime/st.duration)):frameIndex/35;
  const normalized=hasExplicitRatio?Math.max(0,Math.min(1,Number(ratio))):frameRatio;
  if(active){st.scrubFeedbackActive=true;st.targetRatio=normalized;}
  else{st.scrubFeedbackActive=false;st.targetRatio=0;}
  const bar=card.querySelector('.ax-card-watch-progress');
  if(bar&&active){bar.dataset.previewing='1';bar.setAttribute('aria-label','Preview frame position');}
  _cardPreviewScheduleScrubLerp(card);
}
async function _cardPreviewWarmFrames(card,startIndex,adoptStart=false,prefetch=true){
  const st=_cardPreviewState(card);if(!st.active)return null;
  const cid=card.dataset.cid,mid=card.dataset.mid;
  const manifest=await _getSnapshotManifest(cid,mid);
  if(!st.active||!manifest?.frames_ready||Number(manifest.frame_count||0)<36)return null;
  const revision=String(manifest.revision||'');
  if(st.revision!==revision){st.frames.clear();st.frameLoads.clear();st.failedFrames=new Set();st.frameTimes=[];st.revision=revision;}
  const times=Array.isArray(manifest.frame_times)?manifest.frame_times.map(Number):[];
  st.frameTimes=times.length===36&&times.every(value=>Number.isFinite(value)&&value>=0)
    ?times
    :Array.from({length:36},(_,index)=>st.duration>0?st.duration*index/35:index);
  if(!st.failedFrames)st.failedFrames=new Set();
  const first=Math.max(0,Math.min(35,Number(startIndex)||0));
  let firstFrame=null,firstIndex=first;
  // If frame 0 is absent despite a validated manifest, walk forward briefly
  // rather than blanking the poster or repeatedly requesting the same URL.
  for(let n=0;n<4&&!firstFrame;n++){
    const idx=(first+n)%36;if(st.failedFrames.has(idx))continue;
    firstFrame=st.frames.get(idx)||await _loadCachedTimelineFrame(cid,mid,idx,st.frames,st.frameLoads,8,revision);
    if(!firstFrame)st.failedFrames.add(idx);else firstIndex=idx;
  }
  if(!firstFrame)return null;
  // Prefetch relative to the frame returned, but never mutate the active
  // animation index from an asynchronous warm-up (that used to skip frames).
  if(prefetch){
    for(let n=1;n<6;n++){
      const idx=(firstIndex+n)%36;
      if(!st.frames.has(idx)&&!st.failedFrames.has(idx))_loadCachedTimelineFrame(cid,mid,idx,st.frames,st.frameLoads,10,revision).then(img=>{if(!img)st.failedFrames.add(idx);}).catch(()=>{});
    }
  }
  return adoptStart ? { frame:firstFrame, index:firstIndex } : firstFrame;
}


async function _cardPreviewStart(card,auto=true){
  if(!card||card.dataset.previewDisabled==='1')return;
  const st=_cardPreviewState(card),cid=card.dataset.cid,mid=card.dataset.mid,vt=card.querySelector('.vt,.unx-art');if(!vt)return;
  const requestSeq=++st.requestSeq;
  clearTimeout(st.timer);st.timer=0;
  st.active=true;_axActiveCardPreviews.add(card);st.img=null;
  st.duration=Math.max(0,Number(card.dataset.duration||st.duration)||0);
  const startIndex=Math.max(0,Math.min(35,Number(st.idx)||0));
  const initial=await _cardPreviewWarmFrames(card,startIndex,true);
  if(!st.active||requestSeq!==st.requestSeq)return;
  let firstFrame=initial?.frame||null;
  if(initial?.index!==undefined)st.idx=initial.index;
  if(!firstFrame){
    // Hover is strictly local-cache-only. Without all validated snapshots the
    // normal representative thumbnail remains visible; never request a stream
    // preview or trigger Telegram activity from a card.
    st.active=false;_axActiveCardPreviews.delete(card);clearTimeout(st.timer);st.timer=0;
    vt.classList.remove('previewing','preview-layer-a','preview-layer-b');
    vt.querySelectorAll('.vc-preview-canvas.active').forEach(canvas=>canvas.classList.remove('active'));
    _cardPreviewSetTime(card,st.idx,true);
    _cardPreviewUpdateScrubFeedback(card,st.idx,false);
    return;
  }
  st.displayedIdx=initial?.index??st.idx;
  const layer=st.layer||'a',canvas=_cardPreviewCanvas(card,layer);if(!canvas){st.active=false;_axActiveCardPreviews.delete(card);_cardPreviewSetTime(card,st.idx,true);_cardPreviewUpdateScrubFeedback(card,st.idx,false);return;}
  vt.style.setProperty('--ax-card-preview-transition',`${CARD_PREVIEW_TRANSITION_MS}ms`);
  _drawTimelineCardImage(canvas,firstFrame);
  vt.classList.add('previewing');
  _cardPreviewShowLayer(card,layer);
  _cardPreviewSetTime(card,st.displayedIdx);
  _cardPreviewUpdateScrubFeedback(card,st.idx,true);
  if(auto){clearTimeout(st.timer);st.timer=setTimeout(()=>_cardPreviewAdvance(card),CARD_PREVIEW_FRAME_MS);}
}
async function _cardPreviewScrub(card,index,ratio=null){
  if(!card||!card.classList.contains('vc'))return;
  const st=_cardPreviewState(card),target=Math.max(0,Math.min(35,Math.floor(Number(index)||0)));
  _cardPreviewUpdateScrubFeedback(card,target,true,ratio);
  if(target===st.idx&&(st.displayedIdx===target||st.requestSeq>0))return;
  st.idx=target;
  clearTimeout(st.timer);st.timer=0;
  if(!st.active)return;
  const requestSeq=++st.requestSeq;
  const result=await _cardPreviewWarmFrames(card,target,true,false);
  if(!st.active||requestSeq!==st.requestSeq||!result?.frame)return;
  const frame=result.frame,actualIndex=Number.isFinite(Number(result.index))?Number(result.index):target;
  st.idx=actualIndex;st.displayedIdx=actualIndex;
  const nextLayer=st.layer==='a'?'b':'a',canvas=_cardPreviewCanvas(card,nextLayer);
  if(!canvas)return;
  const vt=card.querySelector('.vt');
  if(vt)vt.style.setProperty('--ax-card-preview-transition',`${CARD_PREVIEW_TRANSITION_MS}ms`);
  _drawTimelineCardImage(canvas,frame);
  _cardPreviewSetTime(card,actualIndex);
  const transitionSeq=++st.transitionSeq;
  requestAnimationFrame(()=>{
    if(!st.active||requestSeq!==st.requestSeq||transitionSeq!==st.transitionSeq)return;
    st.layer=nextLayer;
    _cardPreviewShowLayer(card,nextLayer);
  });
  _cardPreviewUpdateScrubFeedback(card,actualIndex,true);
}
async function _cardPreviewAdvance(card){
  const st=_cardPreviewState(card);if(!st.active)return;
  if(!st.failedFrames)st.failedFrames=new Set();
  const nextIndex=(st.idx+1)%36;
  const nextLayer=st.layer==='a'?'b':'a';
  if(st.failedFrames.has(nextIndex)){
    st.idx=nextIndex;
    _cardPreviewUpdateScrubFeedback(card,nextIndex,true);
    if(st.failedFrames.size>=36){_cardPreviewStop(card);return;}
    clearTimeout(st.timer);st.timer=setTimeout(()=>_cardPreviewAdvance(card),CARD_PREVIEW_FRAME_MS);return;
  }
  const canvas=_cardPreviewCanvas(card,nextLayer);if(!canvas)return;
  const frame=st.frames.get(nextIndex)||await _loadCachedTimelineFrame(card.dataset.cid,card.dataset.mid,nextIndex,st.frames,st.frameLoads,10,st.revision||'');
  if(!st.active)return;
  if(!frame){
    st.failedFrames.add(nextIndex);st.idx=nextIndex;
    _cardPreviewUpdateScrubFeedback(card,nextIndex,true);
    if(st.failedFrames.size>=36){_cardPreviewStop(card);return;}
    clearTimeout(st.timer);st.timer=setTimeout(()=>_cardPreviewAdvance(card),250);return;
  }
  _drawTimelineCardImage(canvas,frame);
  _cardPreviewWarmFrames(card,nextIndex+1,false).catch(()=>{});
  st.idx=nextIndex;st.displayedIdx=nextIndex;
  _cardPreviewSetTime(card,nextIndex);
  _cardPreviewUpdateScrubFeedback(card,nextIndex,true);
  const seq=++st.transitionSeq;
  requestAnimationFrame(()=>{if(!st.active||seq!==st.transitionSeq)return;st.layer=nextLayer;_cardPreviewShowLayer(card,nextLayer);});
  clearTimeout(st.timer);st.timer=setTimeout(()=>_cardPreviewAdvance(card),CARD_PREVIEW_FRAME_MS);
}

function _cardPreviewStop(card){
  const st=_cardPreviewState(card);st.active=false;st.requestSeq++;st.displayedIdx=-1;_axActiveCardPreviews.delete(card);st.transitionSeq++;clearTimeout(st.timer);st.timer=0;
  // The scrub-feedback rAF is intentionally allowed to ease the playhead back to the saved playback position.
  // It self-terminates after the return animation (or immediately if this card is detached).
  const vt=card.querySelector('.vt,.unx-art');if(vt){vt.style.removeProperty('--ax-card-preview-transition');vt.classList.remove('previewing','preview-layer-a','preview-layer-b');vt.querySelectorAll('.vc-preview-canvas.active').forEach(canvas=>canvas.classList.remove('active'));const fb=vt.querySelector('.vc-thumb-fallback');if(fb)fb.style.removeProperty('opacity');}
  _cardPreviewSetTime(card,st.idx,true);
  _cardPreviewUpdateScrubFeedback(card,st.idx,false);
  // Clear both layers once the fade-out has finished so the next hover never shows a stale frame.
  setTimeout(()=>{if(st.active)return;card.querySelectorAll('.vc-preview-canvas').forEach(cv=>{try{cv.width=cv.width}catch(e){}});},450);
}
function _cardPreviewStep(card,delta){
  const st=_cardPreviewState(card);
  st.idx=Math.max(0,Math.min(35,st.idx+delta));_cardPreviewStart(card,false);clearTimeout(st.timer);
}
const _axVideoPreloadInflight=new Map();
function _axPreloadVideoBytes(channelId,messageId,startSeconds=0,seconds=30){
  const cid=Number(channelId),mid=Number(messageId);if(!Number.isFinite(cid)||!Number.isFinite(mid))return null;
  const start=Number.isFinite(Number(startSeconds))?Math.max(0,Number(startSeconds)):0;
  const windowSeconds=Number.isFinite(Number(seconds))?Math.max(1,Math.min(30,Number(seconds))):30;
  const key=`${cid}:${mid}:${start.toFixed(3)}:${windowSeconds.toFixed(1)}`;if(_axVideoPreloadInflight.has(key))return _axVideoPreloadInflight.get(key);
  const playbackClaim=_P.playbackToken&&_P.mediaKey===vkey(cid,mid)?{
    playback_client_id:_playbackClientId,
    playback_generation:Number(_P.playbackGeneration||0),
    playback_token:String(_P.playbackToken)
  }:{};
  const bufferRate=window.AXNoBufPlayback?.preferences?.().prebufferBps;
  const pending=api('/api/play/preload',{method:'POST',body:JSON.stringify({channel_id:cid,message_id:mid,start_seconds:start,seconds:windowSeconds,speed_limit_bps:Number.isFinite(Number(bufferRate))?Number(bufferRate):0,...playbackClaim})});
  _axVideoPreloadInflight.set(key,pending);
  pending.catch(()=>{}).finally(()=>setTimeout(()=>{if(_axVideoPreloadInflight.get(key)===pending)_axVideoPreloadInflight.delete(key);},60000));
  return pending;
}
function _axPreloadInitialAndResumeWindow(channelId,messageId,startSeconds){
  const start=Number(startSeconds);
  const queueResume=()=>_axPreloadVideoBytes(channelId,messageId,start,10);
  const initial=_axPreloadVideoBytes(channelId,messageId,0,10);
  if(initial&&typeof initial.then==='function')initial.then(queueResume,queueResume);
  else queueResume();
  return initial;
}
const _axCardWatchHistoryCache=new Map();
async function _axCardWatchHistory(cid,mid){
  const key=vkey(cid,mid),now=Date.now(),cached=_axCardWatchHistoryCache.get(key);
  if(cached&&now-cached.at<10000)return cached.promise||cached.value;
  const entry={at:now,promise:null,value:null};
  entry.promise=api(`/api/play/history/${encodeURIComponent(cid)}/${encodeURIComponent(mid)}`,{cache:'no-store'}).then(value=>{
    entry.at=Date.now();entry.value=value||null;entry.promise=null;return entry.value;
  }).catch(()=>{_axCardWatchHistoryCache.delete(key);return null;});
  _axCardWatchHistoryCache.set(key,entry);
  if(_axCardWatchHistoryCache.size>300)_axCardWatchHistoryCache.delete(_axCardWatchHistoryCache.keys().next().value);
  return entry.promise;
}
async function _axLoadCardWatchProgress(card){
  const bar=card?.querySelector('.ax-card-watch-progress');if(!bar||bar.dataset.loading==='1')return;
  bar.dataset.loading='1';
  const history=await _axCardWatchHistory(card.dataset.cid,card.dataset.mid);
  bar.dataset.loading='0';
  if(!card.isConnected)return;
  const position=Math.max(0,Number(history?.position)||0);
  const duration=Math.max(0,Number(history?.duration)||Number(card.dataset.duration)||0);
  const completed=!!history?.completed;
  if(!history||(!completed&&(!duration||position<=0))){
    bar.dataset.available='0';delete bar.dataset.playbackPercent;
    if(bar.dataset.previewing!=='1'){
      bar.querySelector('.ax-card-watch-progress-fill')?.style.removeProperty('width');
      bar.setAttribute('aria-valuenow','0');
    }
    return;
  }
  const percent=completed?100:Math.max(0,Math.min(100,position/duration*100));
  const fill=bar.querySelector('.ax-card-watch-progress-fill');if(!fill)return;
  bar.dataset.playbackPercent=percent.toFixed(2);
  if(bar.dataset.previewing!=='1'){
    fill.style.width=`${percent.toFixed(2)}%`;
    bar.setAttribute('aria-valuenow',String(Math.round(percent)));
  }
  bar.dataset.available='1';
}
const _axWatchProgressBound=new WeakSet();
function bindCardWatchProgress(card){
  if(!card||_axWatchProgressBound.has(card))return;
  const vt=card.querySelector('.vt');if(!vt)return;
  let bar=vt.querySelector('.ax-card-watch-progress');
  if(!bar){
    bar=document.createElement('span');bar.className='ax-card-watch-progress';bar.dataset.available='0';bar.setAttribute('role','progressbar');bar.setAttribute('aria-label','Playback progress');bar.setAttribute('aria-valuemin','0');bar.setAttribute('aria-valuemax','100');bar.setAttribute('aria-valuenow','0');
    vt.appendChild(bar);
  }
  if(!bar.querySelector('.ax-card-watch-progress-fill'))bar.insertAdjacentHTML('afterbegin','<span class="ax-card-watch-progress-fill"></span>');
  _axWatchProgressBound.add(card);
  const load=()=>{if(_canHover())_axLoadCardWatchProgress(card).catch(()=>{});};
  card.addEventListener('mouseenter',load);
  card.addEventListener('focusin',()=>_axLoadCardWatchProgress(card).catch(()=>{}));
}
const _axPreviewBound=new WeakSet();
function bindCardPreviews(root){
  root.querySelectorAll('.vc[data-cid][data-mid],.unx[data-cid][data-mid]').forEach(card=>{
    if(card.classList.contains('vc'))bindCardWatchProgress(card);
    if(_axPreviewBound.has(card))return;_axPreviewBound.add(card);
    let hoverTimer=0;
    const image=card.querySelector('.vt');
    if(card.classList.contains('vc')&&image){
      card.addEventListener('pointerleave',ev=>{
        const st=_cardPreviewState(card);
        if(ev.pointerType!=='mouse'&&st.touchPointerId!=null)return;
        clearTimeout(hoverTimer);_cardPreviewStop(card);
      });
      image.addEventListener('pointerenter',ev=>{
        if(ev.pointerType!=='mouse'||!_canHover())return;
        const st=_cardPreviewState(card);
        clearTimeout(hoverTimer);hoverTimer=0;
        if(st.active)return;
        st.idx=0;
        _cardPreviewStart(card,true);
      });
      image.addEventListener('pointerleave',ev=>{
        if(ev.pointerType!=='mouse')return;
        clearTimeout(hoverTimer);hoverTimer=0;
        _cardPreviewStop(card);
      });
    }else{
      card.addEventListener('mouseenter',()=>{if(!_canHover())return;clearTimeout(hoverTimer);hoverTimer=setTimeout(()=>_cardPreviewStart(card,true),80);});
      card.addEventListener('mouseleave',()=>{clearTimeout(hoverTimer);_cardPreviewStop(card);});
    }
    card.addEventListener('pointerdown',ev=>{
      if(ev.pointerType==='mouse')return;
      const control=ev.target.closest?.('button,a,input,select,textarea,[role="button"],[role="link"],[data-no-card-preview]');
      if(!image?.contains(ev.target)||(control&&control!==card))return;
      const st=_cardPreviewState(card);
      st.touchPointerId=ev.pointerId;st.downX=ev.clientX;st.downY=ev.clientY;st.startX=ev.clientX;st.startY=ev.clientY;
      st.moved=false;st.longPressPreview=false;
      st.idx=_cardPreviewIndexFromPointer(card,ev);st.lastCommittedX=_cardPreviewPointerX(card,ev);
      _cardPreviewUpdateScrubFeedback(card,st.idx,true,_cardPreviewRatioFromPointer(card,ev));
      try{card.setPointerCapture(ev.pointerId);}catch(_){}
      clearTimeout(hoverTimer);hoverTimer=setTimeout(()=>{
        if(st.touchPointerId!==ev.pointerId)return;
        st.longPressPreview=true;_cardPreviewStart(card,false);
      },300);
    },{passive:true});
    card.addEventListener('pointermove',ev=>{
      if(ev.pointerType==='mouse')return;
      const st=_cardPreviewState(card);if(st.touchPointerId!==ev.pointerId)return;
      const totalX=ev.clientX-st.startX,totalY=ev.clientY-st.startY;
      if(Math.abs(totalX)+Math.abs(totalY)>12)st.moved=true;
      if(Math.abs(totalY)>12&&Math.abs(totalY)>Math.abs(totalX)){
        clearTimeout(hoverTimer);
        if(st.active)_cardPreviewStop(card);
        return;
      }
      if(Math.abs(totalX)>12&&Math.abs(totalX)>Math.abs(totalY)){
        clearTimeout(hoverTimer);
        const index=_cardPreviewIndexFromPointer(card,ev),ratio=_cardPreviewRatioFromPointer(card,ev);
        _cardPreviewUpdateScrubFeedback(card,index,true,ratio);
        if(!st.active){st.idx=index;st.lastCommittedX=_cardPreviewPointerX(card,ev);_cardPreviewStart(card,false);}
        else if(index!==st.idx&&_cardPreviewShouldCommitPointer(card,ev,index))_cardPreviewScrub(card,index,ratio);
      }
    },{passive:true});
    card.addEventListener('pointerup',ev=>{
      if(ev.pointerType==='mouse')return;
      const st=_cardPreviewState(card);if(st.touchPointerId!==ev.pointerId)return;
      st.touchPointerId=null;clearTimeout(hoverTimer);
      if(st.moved||st.longPressPreview){
        card.dataset.suppressClick='1';setTimeout(()=>{card.dataset.suppressClick='0';},80);
        _cardPreviewStop(card);
      }
    });
    card.addEventListener('pointercancel',ev=>{
      const st=_cardPreviewState(card);if(st.touchPointerId!==ev.pointerId)return;
      st.touchPointerId=null;st.moved=false;st.longPressPreview=false;
      clearTimeout(hoverTimer);_cardPreviewStop(card);
    });
  });
}
document.addEventListener('click',ev=>{const card=ev.target.closest?.('.vc[data-suppress-click="1"]');if(card){ev.preventDefault();ev.stopPropagation();card.dataset.suppressClick='0';}},true);
// Delegated click handler for .vc video cards — single listener replaces per-card inline onclick
document.addEventListener('click',ev=>{if(ev.target.closest?.('button,a,.tag,.map-select,.fav-btn,.video-card-more-trigger,.card-edit-icon'))return;const card=ev.target.closest?.('.vc[data-cid][data-mid]');if(!card)return;mappingCardClick(ev,card,card.dataset.cid,Number(card.dataset.mid));});
// Delegated click handler for studio cards only. Performer cards also use
// .pcard[data-entity-id], but their own click handler calls openPerf(); a generic
// selector here caused both openPerf() and openSite() to run, racing navigation.
document.addEventListener('click',ev=>{if(ev.target.closest?.('button,a,.card-edit-icon'))return;const card=ev.target.closest?.('#site-list .pcard[data-entity-id]');if(!card)return;openSite(Number(card.dataset.entityId));});

function renderGrid(quietRefresh=false,animateCards=false){
  const g=$('vg');
  g.classList.toggle('ax2-quiet-refresh',!!quietRefresh);
  const page=_serverLibraryMode?_filtV:_filtV.slice(_page*_PER,(_page+1)*_PER);
  if(!page.length){
    const emptyText=!_sel.size?'Select channels to see their videos.':(_serverLibraryMode?_serverLibTotal===0:!_allV.length)?'No videos match these filters.':'No videos on this page.';
    const action=!_sel.size?'<button type="button" class="btn sm" onclick="chAll(1)">All channels</button>':'';
    g.innerHTML=`<div class="es ax2-library-empty-state" style="grid-column:1/-1"><div class="eb"><svg class="ax-icon" aria-hidden="true"><use href="#ax-play"></use></svg></div><p>${emptyText}</p>${action}</div>`;
    $('pg').innerHTML='';return;
  }
  _axPatchLiveMarkup(g,page.map(cardHTML).join(''));
  if(_mapMode)refreshMapMode();
  hydrateThumbs(g);
  bindCardPreviews(g);
  renderPg();
  if(animateCards&&!quietRefresh)window.AXMotion?.replayGrid(g);
}
function markPlaying(){
  document.querySelectorAll('.vc.playing').forEach(e=>e.classList.remove('playing'));
  if(_aChan&&_aMsg)document.querySelectorAll(`.vc[data-k="${CSS.escape(vkey(_aChan,_aMsg))}"]`).forEach(e=>e.classList.add('playing'));
}
function refreshCard(v){
  document.querySelectorAll(`.vc[data-k="${CSS.escape(vkey(v._cid,v.id))}"]`).forEach(el=>{
    const isEntityProfileCard=!!el.closest('#perf-video-grid, #site-video-grid');
    const usesLibraryLayout=!!el.closest('#vg')||isEntityProfileCard;
    const vi=el.querySelector('.vi');if(vi)vi.innerHTML=cardInfoHTML(v,{library:usesLibraryLayout,includeStudioInChannel:!isEntityProfileCard});
    const b=el.querySelector('.vbadges');if(b)b.innerHTML=cardBadgesHTML(v,{showStudio:!isEntityProfileCard});
    el.classList.add('fresh');setTimeout(()=>el.classList.remove('fresh'),1200);
  });
}
// Technical details are populated by Metadata & Assets scan and persisted
// locally. Only fetch missing play information after an explicit Play action.
const _infoCache=new Map();
async function fetchInfo(cid,id,wait){
  const k=vkey(cid,id);
  if(_infoCache.has(k))return _infoCache.get(k);
  const r=await api(`/api/play/info/${encodeURIComponent(cid)}/${id}?wait=${wait||20}`);
  if(r&&r._ok&&r._status===200&&!r.pending){_infoCache.set(k,r);return r;}
  return null;
}

// Publish the selected video's known dimensions before its media source changes.
// The HTMLVideoElement can briefly retain the previous video's videoWidth/Height;
// this hint prevents that stale ratio from sizing the next player's control row.
function axPublishPlayerDimensions(video,cid,msgId,preferredInfo=null){
  const key=vkey(cid,msgId);
  const cached=_infoCache.get(key)||{};
  const candidates=[preferredInfo,cached,video].filter(Boolean);
  let width=0,height=0;
  for(const candidate of candidates){
    const w=Number(candidate.width||candidate.video_width||candidate.videoWidth||0);
    const h=Number(candidate.height||candidate.video_height||candidate.videoHeight||0);
    if(w>0&&h>0){width=w;height=h;break;}
    const resolution=String(candidate.resolution||candidate.video_resolution||'');
    const match=resolution.match(/(\d{2,5})\s*[x×]\s*(\d{2,5})/i);
    if(match){width=Number(match[1]);height=Number(match[2]);if(width>0&&height>0)break;}
  }
  const hint={key,width:width||0,height:height||0,waitingForMetadata:!(width>0&&height>0)};
  if(typeof window.setArchiveXPlayerDimensionHint==='function')window.setArchiveXPlayerDimensionHint(hint);
  else window.__axPlayerDimensionHint=hint;
  if(window.syncPlayerVisualState)window.syncPlayerVisualState();
  if(window.syncAdaptivePlayerLayout)window.syncAdaptivePlayerLayout();
}

// ── SECTION: Background details for the visible page ──
let _enrichSeq=0;




// ═══════════════════════════════════════════════
// SECTION: VIDEO QUICK EDIT — title + performer + studio
// ═══════════════════════════════════════════════
const _vq={id:null,cid:'',performers:[],studio:null,performerPicked:false,studioPicked:false,perfItems:[],studioItems:[],perfSeq:0,studioSeq:0,lookupSeq:0,openSeq:0,lookupTimer:null,matches:[],sourceVideo:null,returnToSceneReview:false};
function _vqCurrentVideo(){
  if(!_vq.id)return {};
  const current=findVideo(_vq.id,_vq.cid);
  return current||_vq.sourceVideo?{...(current||{}),...(_vq.sourceVideo||{})}:{};
}
function _vqPerfKey(value){return String(value?.name||value||'').normalize('NFKD').replace(/[\u0300-\u036f]/g,'').toLowerCase().replace(/[^a-z0-9]+/g,'');}
function _vqRenderCurrent(){
  const p=$('vq-performer-current'),s=$('vq-studio-current');
  if(p)p.innerHTML=_vq.performers.length?_vq.performers.map((person,i)=>`<span class="pill vq-performer-chip">${esc(person.name)} <button type="button" class="x" aria-label="Remove ${esc(person.name)}" onclick="vqRemovePerformer(${i})">×</button></span>`).join(''):'<span class="muted">No performers selected</span>';
  if(s)s.innerHTML=_vq.studio?`<span class="pill">${esc(_vq.studio.name)} <span class="x" onclick="vqClear('studio')">×</span></span>`:'<span class="muted">No studio selected</span>';
}
function vqRemovePerformer(index){_vq.performers.splice(Number(index),1);_vqRenderCurrent();quickEditLookup();}
function vqClear(kind){if(kind==='performer')_vq.performers=[];else _vq.studio=null;_vqRenderCurrent();quickEditLookup();}
function closeVideoQuickEdit(){
  const dialog=$('video-quick-edit');
  if(dialog)dialog.classList.remove('open');
  _vq.openSeq++;_vq.lookupSeq++;_vq.perfSeq++;_vq.studioSeq++;
  clearTimeout(_vq.lookupTimer);_vq.lookupTimer=null;
  clearTimeout(quickEditSearch.perfT);clearTimeout(quickEditSearch.studioT);
  const returnToReview=_vq.returnToSceneReview;
  _vq.returnToSceneReview=false;
  const closeSeq=_vq.openSeq;
  if(returnToReview)setTimeout(()=>{if(closeSeq===_vq.openSeq&&!dialog?.classList.contains('open'))loadSceneMatchReview();},0);
}
async function openVideoQuickEdit(id,cid,sourceVideo=null,returnToSceneReview=false){
  const listed=findVideo(id,cid);
  const v=sourceVideo?{...(listed||{}),...sourceVideo}:(listed||{});
  if(!v||!id)return;
  const requestSeq=++_vq.openSeq;
  _vq.sourceVideo=sourceVideo||null;
  _vq.returnToSceneReview=!!returnToSceneReview;
  _vq.id=Number(id);_vq.cid=String(cid);_vq.performerPicked=false;_vq.studioPicked=false;_vq.perfSeq++;_vq.studioSeq++;_vq.lookupSeq++;clearTimeout(_vq.lookupTimer);_vq.matches=[];
  const currentPeople=Array.isArray(v.performers)?v.performers:(Array.isArray(v.local_performers)?v.local_performers:String(v.performers||v.local_performers||'').split(',').map(x=>x.trim()).filter(Boolean));
  _vq.performers=[];const seenPeople=new Set();currentPeople.forEach(person=>{const item=typeof person==='string'?{name:person,id:null}:{...person,name:String(person?.name||'').trim()};const key=_vqPerfKey(item);if(item.name&&key&&!seenPeople.has(key)){seenPeople.add(key);_vq.performers.push(item);}});
  _vq.studio=(v.site_name||v.studio)?{name:String(v.site_name||v.studio),id:v.site_id||null}:null;
  $('vq-summary').textContent=`${v.telegram_filename||v.name||'Video'} · ${v.duration?fdur(v.duration):''}`;
  $('vq-title').value=String(v.api_title||v.title||v.name||v.telegram_filename||'');
  $('vq-performer-q').value='';$('vq-studio-q').value='';$('vq-meta-list').innerHTML='';$('vq-match').style.display='none';$('vq-status').textContent='Loading current metadata…';
  _vqRenderCurrent();
  const dialog=$('video-quick-edit');dialog.classList.add('open');
  await Promise.all([quickEditSearch('performer',true),quickEditSearch('studio',true)]);
  if(requestSeq!==_vq.openSeq||!dialog.classList.contains('open'))return;
  quickEditLookup();
  setTimeout(()=>{if(requestSeq===_vq.openSeq&&dialog.classList.contains('open'))$('vq-title').focus();},40);
}
function videoQuickEditChanged(){
  clearTimeout(_vq.lookupTimer);
  _vq.lookupTimer=setTimeout(()=>quickEditLookup(),550);
}
function quickEditSearch(kind,now=false){
  clearTimeout(kind==='performer'?quickEditSearch.perfT:quickEditSearch.studioT);
  const fn=()=>quickEditDoSearch(kind); if(kind==='performer')quickEditSearch.perfT=setTimeout(fn,now?0:140); else quickEditSearch.studioT=setTimeout(fn,now?0:140);
}
async function quickEditDoSearch(kind){
  const dialog=$('video-quick-edit');if(!dialog?.classList.contains('open'))return;
  const perf=kind==='performer', q=$(perf?'vq-performer-q':'vq-studio-q').value.trim();
  const seq=perf?++_vq.perfSeq:++_vq.studioSeq;
  const videoId=Number(_vq.id),channelId=String(_vq.cid);
  const isCurrent=()=>seq===(perf?_vq.perfSeq:_vq.studioSeq)&&Number(_vq.id)===videoId&&String(_vq.cid)===channelId&&dialog.classList.contains('open');
  if(!q){
    const v=_vqCurrentVideo();
    const names=perf?(_vq.performers.map(person=>({...person,count:0,image:person.image||null,kind:person.kind||'local',selected:true}))):((v.site_name||v.studio)?[{name:String(v.site_name||v.studio),id:v.site_id||null,count:0,image:v.site_image,kind:'local'}]:[]);
    if(perf)_vq.perfItems=names;else _vq.studioItems=names;
    quickEditRenderList(kind);return;
  }
  let local=[];
  try{const r=await api(perf?('/api/performers?limit=18&q='+encodeURIComponent(q)):('/api/studios?limit=18&q='+encodeURIComponent(q)));local=Array.isArray(r)?r:[];}catch(e){local=[];}
  if(!isCurrent())return;
  const items=local.map(x=>({name:x.name,id:x.id,count:x.video_count||0,image:x.image,provider_ids:x.provider_ids||null,kind:'local'}));
  if(q.length>=2){
    try{
      const endpoint=perf?'/api/performers/provider-search?limit=8&q=':'/api/studios/provider-search?limit=8&q=';
      const r=await api(endpoint+encodeURIComponent(q));
      if(!isCurrent())return;
      const seen=new Set(items.map(x=>String(x.name||'').toLowerCase().replace(/[^a-z0-9]/g,'')));
      (Array.isArray(r)?r:[]).forEach(x=>{
        const key=String(x.name||'').toLowerCase().replace(/[^a-z0-9]/g,'');
        if(!key||!x.name||!x.provider_id)return;
        // Keep local rows first, but display provider alternatives even when they
        // share a name so the user can explicitly select the provider identity.
        const sameLocal=items.find(item=>String(item.name||'').toLowerCase().replace(/[^a-z0-9]/g,'')===key);
        const providerKey=String(x.provider||'').toLowerCase()+':'+String(x.provider_id);
        if(items.some(item=>item.kind==='provider'&&String(item.provider||'').toLowerCase()+':'+String(item.provider_id)===providerKey))return;
        items.push({name:x.name,id:null,count:0,image:x.image,kind:'provider',provider:x.provider,provider_id:x.provider_id,profile:x.profile,aliases:x.aliases||[],exact:!!x.exact,alias_exact:!!x.alias_exact,localMatch:!!sameLocal});
      });
    }catch(e){}
  }
  if(q.length>=2){
    const normalizedQuery=_vqPerfKey(q);
    const exactLocal=items.some(item=>item.kind==='local'&&!item.create&&_vqPerfKey(item.name)===normalizedQuery);
    const alreadyStaged=_vq.performers.some(person=>_vqPerfKey(person)===normalizedQuery)||_vqPerfKey(_vq.studio)===normalizedQuery;
    if(!exactLocal&&!alreadyStaged)items.push({name:q,id:null,count:0,image:null,kind:'create',create:true});
  }
  if(!isCurrent())return;
  const createCandidate=items.find(item=>item.create||item.kind==='create');
  const visibleItems=items.filter(item=>!item.create&&item.kind!=='create').slice(0,createCandidate?23:24);
  if(createCandidate)visibleItems.push(createCandidate);
  if(perf)_vq.perfItems=visibleItems;else _vq.studioItems=visibleItems;
  quickEditRenderList(kind);
  clearTimeout(_vq.lookupTimer);_vq.lookupTimer=setTimeout(()=>quickEditLookup(),450);
}
function _vqSamePerformer(person,item){
  const sameName=_vqPerfKey(person)===_vqPerfKey(item);
  const sameProviderId=!!(person?.provider_id&&item?.provider_id&&String(person.provider||'').toLowerCase()===String(item.provider||'').toLowerCase()&&String(person.provider_id)===String(item.provider_id));
  return sameName||sameProviderId;
}
function quickEditRenderList(kind){
  const perf=kind==='performer', box=$(perf?'vq-performer-list':'vq-studio-list'), list=perf?_vq.perfItems:_vq.studioItems;
  if(!box)return;
  box.innerHTML=list.length?list.map((x,i)=>{
    const isCreate=!!x.create||x.kind==='create';
    const img=x.image?(x.kind==='provider'?safeHttpUrl(x.image):(perf&&Number(x.id)?'/api/performers/'+Number(x.id)+'/image':(!perf&&Number(x.id)?'/api/studios/'+Number(x.id)+'/image':''))):'';
    const isSelected=perf&&_vq.performers.some(person=>_vqSamePerformer(person,x));
    const origin=isCreate?`Create a new local ${perf?'performer':'studio'}`:x.kind==='provider'?`${String(x.provider||'provider').toUpperCase()} · ${x.exact?'Exact name':x.alias_exact?'Alias match':'Provider candidate'}${x.localMatch?' · matches local record':''}`:'Local catalogue';
    const trailing=isCreate?'<span class="qei-create-mark">＋ Create</span>':x.kind==='provider'?`<span class="qei-provider-mark">${isSelected?'Added':'API'}</span>`:`<span class="qei-count" title="${Number(x.count||0)} videos">${Number(x.count||0)}</span>`;
    const initial=String(x.name||'?').trim().charAt(0).toUpperCase()||'?';
    const avatar=img?`<img class="qei-avatar" src="${esc(img)}" alt="" data-initial="${esc(initial)}" loading="lazy" decoding="async" onerror="this.replaceWith(Object.assign(document.createElement('span'),{className:'qei-avatar-fallback',textContent:this.dataset.initial||'?'}))">`:`<span class="qei-avatar-fallback">${esc(initial)}</span>`;
    return `<button type="button" class="quick-edit-item${x.kind==='provider'?' provider-candidate':''}${isCreate?' quick-edit-create':''}${isSelected?' selected':''}" onclick="quickEditPick('${kind}',${i})">${avatar}<span class="qei-main"><span class="qei-name">${esc(isCreate?`Create “${x.name}”`:x.name)}</span><span class="qei-meta">${esc(origin)}${x.provider_id?` · ID ${esc(String(x.provider_id))}`:''}</span></span>${trailing}</button>`
  }).join(''):'<div class="quick-edit-empty">No matches in local catalogue or configured providers.</div>';
}
function quickEditPick(kind,i){
  const x=(kind==='performer'?_vq.perfItems:_vq.studioItems)[i];if(!x)return;
  if(x.create||x.kind==='create'){
    if(kind==='performer'){
      const at=_vq.performers.findIndex(person=>_vqPerfKey(person)===_vqPerfKey(x.name));
      if(at<0)_vq.performers.push({name:String(x.name||'').trim(),id:null,provider:null,provider_id:null,kind:'local'});
      _vq.performerPicked=true;$('vq-performer-q').value='';quickEditRenderList('performer');
    }else{
      _vq.studio={name:String(x.name||'').trim(),id:null,provider:null,provider_id:null,kind:'local'};
      _vq.studioPicked=true;$('vq-studio-q').value='';
    }
    _vqRenderCurrent();quickEditLookup();return;
  }
  if(kind==='performer'){
    const at=_vq.performers.findIndex(person=>_vqSamePerformer(person,x));
    if(at>=0){
      const old=_vq.performers[at];
      // Picking a provider result for a selected local/legacy performer enriches
      // that chip with the namespace ID and image rather than toggling it away.
      if(x.kind==='provider'&&old.kind!=='provider'&&!old.provider_id){
        _vq.performers[at]={...old,...x,id:old.id||x.id||null,name:String(x.name||old.name).trim(),provider:x.provider,provider_id:x.provider_id||null,image:x.image||old.image||null,profile:x.profile||old.profile||null};
      }else _vq.performers.splice(at,1);
    }else _vq.performers.push({...x,name:String(x.name||'').trim(),provider_id:x.provider_id||null,provider:x.provider||null});
    _vq.performerPicked=true;$('vq-performer-q').value='';quickEditRenderList('performer');
  }else{_vq.studio=x;_vq.studioPicked=true;$('vq-studio-q').value='';}
  _vqRenderCurrent();quickEditLookup();
}
async function quickEditLookup(){
  const dialog=$('video-quick-edit');if(!_vq.id||!dialog?.classList.contains('open'))return;
  const videoId=Number(_vq.id),channelId=String(_vq.cid);
  const title=String($('vq-title').value||'').trim();
  const performerQuery=String($('vq-performer-q').value||'').trim();
  const studioQuery=String($('vq-studio-q').value||'').trim();
  // An in-progress typed query takes priority over the currently linked entity;
  // if the query box is empty, keep the selected relationship as a useful clue.
  const performer=performerQuery||String(_vq.performers[0]?.name||'').trim();
  const studio=studioQuery||String(_vq.studio?.name||'').trim();
  if(title.length<2&&performer.length<2&&studio.length<2){$('vq-meta-list').innerHTML='';$('vq-status').textContent='Enter a title, performer, or studio to find video data.';return;}
  const seq=++_vq.lookupSeq,btn=$('vq-lookup');
  if(btn){btn.disabled=true;const label=btn.querySelector('.vq-lookup-text');if(label)label.textContent='Searching…';else btn.textContent='Searching…';}
  $('vq-status').textContent='Finding video data from the filename and configured providers…';
  try{
    const r=await api('/api/media/quick-edit/lookup',{method:'POST',body:JSON.stringify({
      channel_id:channelId,message_id:videoId,title,performer,performers:_vq.performers.map(person=>person.name),studio,
      performer_query:performerQuery,studio_query:studioQuery,
      performer_selected:_vq.performers[0]?.name||'',studio_selected:_vq.studio?.name||'',
      performer_selected_explicit:!!_vq.performerPicked,studio_selected_explicit:!!_vq.studioPicked
    })});
    if(seq!==_vq.lookupSeq||Number(_vq.id)!==videoId||String(_vq.cid)!==channelId||!dialog.classList.contains('open'))return;
    if(!r||!r.ok){$('vq-status').textContent=r?.detail||'Video-data lookup failed. Check provider configuration and try again.';return;}
    const matches=Array.isArray(r.matches)?r.matches:[];
    $('vq-meta-list').innerHTML=matches.length?matches.map((m,i)=>{
      const release=m.date||m.release_date||m.production_date||'Release date unknown';
      const cast=(m.performers||m.performer_details||[]).slice(0,4).map(person=>esc(typeof person==='string'?person:(person?.name||''))).filter(Boolean).join(', ')||'No performer data';
      const studioName=m.studio||m.site||'No studio data';
      const source=String(m.provider||m.source||'provider').toUpperCase();
      const thumb=safeHttpUrl(m.thumbnail||m.image||m.poster||'');
      const rawConfidence=m.score_percent??m.confidence_percent??m.confidence??m._archive_confidence??'Review';
      let confidenceText=String(rawConfidence),confidenceNum=null;
      if(typeof rawConfidence==='number')confidenceNum=rawConfidence<=1?rawConfidence*100:rawConfidence;
      else {const cm=confidenceText.match(/(\d+(?:\.\d+)?)\s*%?/);if(cm)confidenceNum=Number(cm[1])<=1?Number(cm[1])*100:Number(cm[1]);}
      if(confidenceNum!==null&&Number.isFinite(confidenceNum)){confidenceNum=Math.max(0,Math.min(100,confidenceNum));confidenceText=`${Math.round(confidenceNum)}%`;}
      const confidenceTone=confidenceNum===null?'neutral':confidenceNum>=90?'good':confidenceNum>=70?'warn':'bad';
      const detail=[source,studioName,release].filter(Boolean).join(' · ');
      return `<article class="vq-meta-candidate"><div class="vq-meta-candidate-main">${thumb?`<img class="vq-meta-thumb" src="${esc(thumb)}" alt="" loading="lazy" decoding="async" onerror="this.style.visibility='hidden'">`:'<span class="vq-meta-thumb vq-meta-thumb-empty" aria-hidden="true"></span>'}<div class="qei-main"><span class="qei-name" title="${esc(m.title||'Untitled')}">${esc(m.title||'Untitled')}</span><span class="qei-meta">${esc(detail)}</span><span class="vq-meta-cast">${cast}</span></div><span class="vq-meta-confidence ${confidenceTone}">${esc(confidenceText)}</span></div><button type="button" class="btn xs vq-meta-apply" onclick="applyQuickMetadata(${i})">Apply match</button></article>`;
    }).join(''):'<div class="quick-edit-empty">No matching provider video data.</div>';
    const hints=r.filename_hints||{};
    const hintParts=[];
    (Array.isArray(hints.performers)?hints.performers:[]).forEach(name=>{if(name)hintParts.push({label:String(name),kind:'entity'});});
    if(!hintParts.length)(Array.isArray(hints.performer_candidates)?hints.performer_candidates:[]).forEach(name=>{if(name)hintParts.push({label:String(name),kind:'unverified'});});
    if(hints.studio)hintParts.push({label:String(hints.studio),kind:'entity'});
    if(!hints.studio)(Array.isArray(hints.studio_candidates)?hints.studio_candidates:[]).forEach(name=>{if(name)hintParts.push({label:String(name),kind:'unverified'});});
    if(hints.date||hints.year)hintParts.push({label:String(hints.date||hints.year),kind:'date'});
    const hintEl=$('vq-filename-hints');
    if(hintEl){hintEl.style.display=hintParts.length?'flex':'none';hintEl.innerHTML=hintParts.map(part=>`<span class="vq-clue-chip ${part.kind==='unverified'?'unverified':part.kind==='date'?'date':''}">${esc(part.label)}</span>`).join('');}
    const errors=Array.isArray(r.errors)?r.errors.filter(Boolean):[];
     const filterNames=r.mapping_filter_names||{};
     const filterParts=[
       ...(Array.isArray(filterNames.performer)&&filterNames.performer.length?['performer: '+filterNames.performer.join(', ')]:[]),
       ...(Array.isArray(filterNames.studio)&&filterNames.studio.length?['studio: '+filterNames.studio.join(', ')] :[])
     ];
     const filterNote=filterParts.length?` Filtered to confirmed ${filterParts.join(' and ')}.`:'';
     $('vq-status').textContent=matches.length?`${matches.length} video-data candidate${matches.length===1?'':'s'} found via ${String(r.method||'provider search')}.${filterNote} Apply a match to stage it; Save changes commits it and syncs the Telegram caption.`:
       (Number(r.filtered_count)>0?`No candidates match the confirmed performer/studio mapping. ${Number(r.filtered_count)} conflicting result${Number(r.filtered_count)===1?' was':'s were'} hidden. Enter or select a different entity only if you intentionally want to change the mapping.`:
       (errors.length?'No matching video data. Provider issue: '+errors.slice(0,2).join(' · '):'No matching video data. The filename clues were checked against configured providers; try a shorter title or a more specific performer/studio.'));
    _vq.matches=matches;
  }catch(e){
    if(seq===_vq.lookupSeq){$('vq-status').textContent='Video-data lookup failed. Check the network and provider configuration, then try again.';}
  }finally{
    if(seq===_vq.lookupSeq&&Number(_vq.id)===videoId&&String(_vq.cid)===channelId&&dialog.classList.contains('open')&&btn){btn.disabled=false;const label=btn.querySelector('.vq-lookup-text');if(label)label.textContent='Find video data';else btn.textContent='Find video data';}
  }
}

function applyQuickMetadata(i){
  const m=(_vq.matches||[])[Number(i)];if(!m)return;
  if(m.title)$('vq-title').value=String(m.title);
  const rawPeople=Array.isArray(m.performer_details)&&m.performer_details.length?m.performer_details:(Array.isArray(m.performers)?m.performers:[]);
  const cast=[],seen=new Set();rawPeople.forEach(raw=>{const person=typeof raw==='string'?{name:raw}:{...raw};const name=String(person.name||'').trim(),key=_vqPerfKey(name);if(!name||!key||seen.has(key))return;seen.add(key);cast.push({name,id:null,provider:person.provider||m.provider||m.source||'',provider_id:person.provider_id||person.id||null,image:person.image||person.image_url||null,profile:person.profile||person.url||null,aliases:person.aliases||[]});});
  // Applying a scene deliberately stages the complete provider cast. It does not
  // touch the database until Save; individual chips can still be removed first.
  _vq.performers=cast;
  if(m.studio||m.site)_vq.studio={name:String(m.studio||m.site),id:m.studio_id||null,provider:m.provider||m.source,provider_id:m.studio_id||null,image:m.studio_image||m.image};
  _vqRenderCurrent();quickEditRenderList('performer'); $('vq-match').style.display='block';$('vq-match').textContent=`Provider cast staged (${cast.length} performer${cast.length===1?'':'s'}). No database change has been made yet.`;
}
async function saveVideoQuickEdit(){
  if(!_vq.id)return;
  const btn=$('vq-save');if(btn){btn.disabled=true;btn.textContent='Saving & syncing…';}
  const body={channel_id:_vq.cid,message_id:_vq.id,title:String($('vq-title').value||'').trim(),performers:_vq.performers.map(person=>({...person,name:String(person.name||'').trim()})).filter(person=>person.name),studio:_vq.studio||null,lock:true};
  let r;
  try{r=await api('/api/media/quick-edit',{method:'POST',body:JSON.stringify(body)});}catch(e){
    if(btn){btn.disabled=false;btn.textContent='Save changes';}
    toast('Save failed. Check the connection and try again.');return;
  }
  if(!r||!r.ok){if(btn){btn.disabled=false;btn.textContent='Save changes';}toast(r?.detail||'Video changes could not be saved.');return;}
  const v=findVideo(_vq.id,_vq.cid); if(v){if(body.title)v.title=body.title;v.performers=body.performers.map(person=>person.name);v.local_performers=body.performers.map(person=>person.name);if(body.studio){v.site_name=body.studio.name;v.studio=body.studio.name;}refreshCard(v);if(_dv&&String(_dv.id)===String(_vq.id)&&String(_dv._cid)===String(_vq.cid))fillDetail(v);}
  loadLib();
  const sync=r.caption_sync;
  if(sync&&!sync.ok){
    const status=$('vq-status');if(status)status.textContent='Local metadata was saved, but Telegram caption sync failed. '+(sync.detail||'Check the Telegram connection and edit permissions, then press Save changes again to retry.');
    toast(sync.status==='partial'?'Some linked Telegram captions updated; some failed.':sync.status==='permission_denied'?'Metadata saved locally; Telegram edit permissions are missing.':'Metadata saved locally; Telegram caption sync failed. Press Save changes to retry.');
    if(btn){btn.disabled=false;btn.textContent='Save changes';}return;
  }
  closeVideoQuickEdit();
  toast(sync?.status==='synced'?'Metadata saved and Telegram caption synchronized.':sync?.status==='unchanged'?'Metadata saved; Telegram caption is already up to date.':'Metadata saved and caption-sync request completed.');
  if(btn){btn.disabled=false;btn.textContent='Save changes';}
}

// ═══════════════════════════════════════════════
// SECTION: WATCH VIEW + PLAYER
// ═══════════════════════════════════════════════
let _liveSeekPreviewEnabled=true;
// Seek-bar previews always use live frames; saved snapshots remain available for grids/contact sheets.
let _resumePromptEnabled=true;
// V29.4.162: player uses live timestamp seek previews only; no snapshot-grid scrub UI.
const _playbackClientId=(()=>{try{return globalThis.crypto?.randomUUID?.()||'';}catch(_){return '';}})()||`${Date.now().toString(36)}-${Math.random().toString(36).slice(2)}`;
function _newPlaybackToken(){try{return globalThis.crypto?.randomUUID?.()||'';}catch(_){return '';} }
const _P={cid:null,mid:null,mediaKey:'',preloadedKey:'',preloadReadyKey:'',mode:null,off:0,dur:0,info:null,seq:0,openTok:0,fell:false,autoFallbackTried:false,iosHlsFallbackTried:false,rate:1,dragging:false,hideT:null,lastSave:0,target:0,cacheStatus:'idle',previewExactTimer:0,cachePoll:0,seekSeq:0,state:'IDLE',previewCache:new Map(),previewAbort:null,previewSeq:0,previewRaf:0,previewLastBucket:-1,seekAbort:null,seekWarmAbort:null,scrubSession:null,resumeAfterScrub:null,seekStallTimer:0,lastSeekStallRecovery:0,startupStallTimer:0,previewWarmTimer:0,previewLastFrame:-1,playOpenAt:0,firstPlayingAt:0,firstFrameAt:0,firstFrameFallbackCleanup:null,browserStartupBufferCtl:null,rebufferCount:0,seekCount:0,lastWaitAt:0,lastServerSave:0,lastServerPosition:0,playbackIncidentT:0,lastPlaybackIncidentAt:0,activityTimer:0,activeSrc:'',pendingStartAt:null,playbackGeneration:0,playbackToken:'',
  // Multi-part playable split state
  parts:null,partIndex:0,partOffsets:null,partDurations:null,partTotalDur:0,logicalCid:null,logicalMid:null};
let _PStartupStillPending=false;
const _playerSceneChapterCache=new Map();
const _playerSceneChapterLoads=new Map();
function pSetState(state){_P.state=String(state||'IDLE');}
_P.playing=()=>{const e=$('pv');return !!(e&&e.getAttribute('src')&&!e.paused);};
let _watchPushed=false;
const pv=()=>$('pv');
function pMediaEventCurrent(){
  const e=pv(),active=String(_P.activeSrc||'');
  if(!active||!e)return false;
  const current=String(e.currentSrc||'');
  // currentSrc remains the strongest signal during a source replacement. If
  // the browser has not selected a resource yet, use the element's new src.
  const observed=current||String(e.src||'');
  try{return new URL(observed,location.href).href===new URL(active,location.href).href;}catch(x){return observed===active;}
}
function pCur(){
  if(_P.parts&&_P.partOffsets){
    return (_P.partOffsets[_P.partIndex]||0)+_P.off+(pv().currentTime||0);
  }
  return _P.off+(pv().currentTime||0);
}
function pDur(){
  if(_P.parts&&_P.partTotalDur>0)return _P.partTotalDur;
  const e=pv();
  if(_P.mode==='direct'&&isFinite(e.duration)&&e.duration>0)return e.duration;
  return _P.dur||(isFinite(e.duration)&&e.duration>0&&_P.mode!=='compat'?e.duration:0)||(_dv&&_dv.duration)||0;
}
// ── SECTION: Multi-part playable split helpers ──
function _pInitParts(info){
  // Called after play_info returns playable_parts.
  // Initialise the multi-part state so pCur/pDur/seek/ended all work
  // across the full logical video.
  const pp=info.playable_parts;
  if(!pp||pp.length<2){_P.parts=null;return;}
  _P.parts=pp;
  _P.partIndex=0;
  // Estimate equal durations from total; refined on loadedmetadata
  const totalDur=info.duration||0;
  const n=pp.length;
  const durs=pp.map(()=>totalDur/n);
  _P.partDurations=durs;
  _P.partTotalDur=totalDur;
  _pRebuildOffsets();
  // Remember the logical (parent) cid/mid for the video list
  _P.logicalCid=String(_P.cid);
  _P.logicalMid=Number(_P.mid);
}
function _pRebuildOffsets(){
  const d=_P.partDurations;if(!d)return;
  const o=[0];for(let i=1;i<d.length;i++)o[i]=o[i-1]+d[i-1];
  _P.partOffsets=o;
  _P.partTotalDur=o[d.length-1]+d[d.length-1];
}
function _pPartAdvance(idx,seekInPart){
  // Switch to a different part mid-playback.
  if(!_P.parts||idx<0||idx>=_P.parts.length)return;
  _P.partIndex=idx;
  const part=_P.parts[idx];
  const e=pv();
  const seq=++_P.seq;
  const playbackToken=_newPlaybackToken()||`${seq}-${Math.random().toString(36).slice(2)}`;
  _P.playbackGeneration=seq;_P.playbackToken=playbackToken;
  const claimQuery=`axclient=${encodeURIComponent(_playbackClientId)}&axgen=${seq}&axtoken=${encodeURIComponent(playbackToken)}`;
  _P.off=0;_P.mode='direct';
  $('dtop').classList.add('loading');
  try{e.pause();e.removeAttribute('src');e.load();e.currentTime=0;}catch(x){}
  const t=Number(seekInPart)||0;
  const url=`/stream/${encodeURIComponent(_P.logicalCid||_P.cid)}/${part.message_id}?${claimQuery}`;
  e.src=url;_P.activeSrc=url;
  // On loadedmetadata: refine this part's real duration
  e.addEventListener('loadedmetadata',()=>{
    if(seq!==_P.seq)return;
    if(isFinite(e.duration)&&e.duration>0&&_P.partDurations){
      _P.partDurations[idx]=e.duration;
      _pRebuildOffsets();
    }
    if(t>0.5){try{e.currentTime=Math.min(t,Math.max(0,(e.duration||t)-0.25));}catch(x){}}
    pSetState('PLAYING');pUI();
  },{once:true});
  pWatchFirstFrame(seq);
  e.play().catch(err=>{
    if(seq!==_P.seq)return;
    $('dtop').classList.add('paused');$('dtop').classList.remove('loading');
    if(err?.name==='NotAllowedError'&&_isIOS())pToast('Safari blocked autoplay. Tap Play to start this video.',4200);
  });
}
function _pPartSeek(globalT){
  // Seek to a global time position across parts.
  if(!_P.parts||!_P.partOffsets)return false;
  const n=_P.parts.length;
  let idx=0;
  for(let i=n-1;i>=0;i--){
    if(globalT>=(_P.partOffsets[i]||0)){idx=i;break;}
  }
  const local=globalT-(_P.partOffsets[idx]||0);
  if(idx===_P.partIndex){
    // Same part — just seek within it
    const e=pv();
    try{e.currentTime=Math.max(0,local);}catch(x){}
    pUI();pShowUI();
    return true;
  }
  // Different part — switch
  _pPartAdvance(idx,local);
  return true;
}

const _playerControlsUI=window.ArchiveXPlayerModules.createControlsUI({
  getElementById:id=>document.getElementById(id),
  getState:()=>_P,
  getPlayer:pv,
  getDuration:pDur,
  getCurrent:pCur,
  formatDuration:fdur,
  getRepeat:()=>_playerABRepeat,
  writeTextIfChanged:_axWriteTextIfChanged,
  isPinned:()=>!!window.AXNoBufPlayback?.preferences?.()?.pinned,
  toast:pToast,
  document
});
const {
  pControlsBelowVideo,
  pCenterPop,
  pShowUI,
  pAutoHide,
  pUI,
  pABRender,
  pABBindMenuEvents,
  pABMenuToggle,
  pABMenuClose,
  pABSetStart,
  pABSetEnd,
  pABClear,
  pABReset,
  pDragTo
}=_playerControlsUI;
Object.assign(window,_playerControlsUI);
const _playerABRepeat=window.ArchiveXPlayerModules.createABRepeat({
  getKey:()=>_P.mediaKey,
  getCurrent:pCur,
  getDuration:pDur,
  isPaused:()=>pv().paused,
  isSeeking:()=>pv().seeking,
  seekTo:time=>pSeek(time,false,true),
  onChange:pABRender
});
// Preview modules are initialized only after the protected player state/helpers exist.
// Dependencies are injected; these modules do not own playback source or seeking.
_snapshotGridModule=window.ArchiveXPreviewModules.createSnapshotGrid({
  $ ,getPlayerState:()=>_P,getPlayer:pv,keyFor:vkey,getVideo:()=>_dv, getDuration:pDur,
  getManifest:_getSnapshotManifest,loadFrame:_loadCachedTimelineFrame,loadGridSprite:_loadCachedGridSprite,formatDuration:fdur,startPlayback:playB
});
_liveSeekPreviewModule=window.ArchiveXPreviewModules.createLiveSeekPreview({
  $,getPlayer:pv,getContext:()=>({cid:_P.cid,mid:_P.mid,mediaKey:_P.mediaKey,openTok:_P.openTok,info:_P.info}),
  getDuration:pDur,isEnabled:()=>_liveSeekPreviewEnabled,isDragging:()=>_P.dragging,
  getChapterTitleAt:getPlayerSceneMarkerTitleNear,
  keyFor:vkey,formatDuration:fdur,newAbortController:_newAbortController
});
const _playerSeekController=window.ArchiveXPlayerModules.createSeekController({
  getState:()=>_P,
  getPlayer:pv,
  getDuration:pDur,
  getCurrent:pCur,
  getPreview:()=>_liveSeekPreviewModule,
  isPreviewEnabled:()=>_liveSeekPreviewEnabled,
  dragTo:pDragTo,
  showUI:pShowUI,
  updateUI:pUI,
  seekPart:_pPartSeek,
  startPlayback:_pStart,
  getInitialMode:pInitialMode,
  getCurrentVideo:()=>_dv
});
const {
  pLiveScrubBegin,
  pLiveScrubTo,
  pLiveScrubFinish,
  pSeek
}=_playerSeekController;
Object.assign(window,_playerSeekController);

function pSendTelemetry(reason='close'){
  if(!_P.cid||_P.mid==null||!_P.playOpenAt)return;
  const sessionId=String(_axDiag.sessionId||('play-'+Date.now()+'-'+Math.random().toString(16).slice(2))).slice(0,80);
  const payload={channel_id:Number(_P.cid),message_id:Number(_P.mid),reason,session_id:sessionId,
    startup_ms:_P.firstPlayingAt?Math.max(0,Math.round(_P.firstPlayingAt-_P.playOpenAt)):null,
    first_frame_ms:_P.firstFrameAt?Math.max(0,Math.round(_P.firstFrameAt-_P.playOpenAt)):null,
    session_ms:Math.max(0,Math.round(performance.now()-_P.playOpenAt)),
    rebuffer_count:Number(_P.rebufferCount||0),seek_count:Number(_P.seekCount||0),
    mode:String(_P.mode||''),state:String(_P.state||'IDLE')};
  try{void fetch('/api/play/telemetry',{method:'POST',credentials:'include',keepalive:true,headers:{'Content-Type':'application/json'},body:JSON.stringify(payload)}).catch(()=>{});}catch(x){}
}

function pRecordFirstFrame(seq){
  if(seq!==_P.seq||!_P.cid||_P.mid==null||!_P.playOpenAt||_P.firstFrameAt)return;
  const e=pv();
  // Require a decoded video frame. Sequence + active source reject callbacks
  // from a previous video; currentSrc can temporarily be empty on first paint.
  if(!e||Number(e.readyState||0)<2||(!(Number(e.videoWidth||0)>0)&&!(Number(e.videoHeight||0)>0)))return;
  if(_P.activeSrc&&e.currentSrc){try{if(new URL(e.currentSrc,location.href).href!==new URL(_P.activeSrc,location.href).href)return;}catch(x){}}
  _P.firstFrameAt=performance.now();
  _PStartupStillPending=false;
  const elapsed=Math.max(0,Math.round(_P.firstFrameAt-_P.playOpenAt));
  if(!e.paused){
    _snapshotGridModule?.playbackStarted?.();
    pCancelBrowserStartupBuffer();
  }
  const data={channel_id:Number(_P.cid),message_id:Number(_P.mid),click_to_first_frame_ms:elapsed,
    time_to_playing_ms:_P.firstPlayingAt?Math.max(0,Math.round(_P.firstPlayingAt-_P.playOpenAt)):null,
    mode:String(_P.mode||''),ready_state:Number(e.readyState||0),video_width:Number(e.videoWidth||0),video_height:Number(e.videoHeight||0)};
  // The first-frame telemetry endpoint also inserts this measurement into the
  // browser diagnostics session ledger. Send exactly one server event instead
  // of racing the regular diagnostics batch and duplicating the same sample.
  pSendTelemetry('first-frame');
  try{if(_P.firstFrameFallbackCleanup){_P.firstFrameFallbackCleanup();_P.firstFrameFallbackCleanup=null;}}catch(x){}
}

const P_BROWSER_STARTUP_RANGE_BYTES=512*1024;
const P_BROWSER_STARTUP_BUFFER_CHUNKS=8;
function pCancelBrowserStartupBuffer(){
  const controller=_P.browserStartupBufferCtl;
  _P.browserStartupBufferCtl=null;
  try{controller?.abort();}catch(x){}
}
function pPrebufferBrowserStartupRanges(source,seq){
  pCancelBrowserStartupBuffer();
  if(!source||typeof fetch!=='function'||typeof AbortController!=='function'||navigator.connection?.saveData)return;
  let url;
  try{url=new URL(source,location.href);}catch(x){return;}
  if(url.origin!==location.origin||!url.pathname.includes('/stream/'))return;
  const controller=new AbortController();
  _P.browserStartupBufferCtl=controller;
  const fetchRange=async(index)=>{
    if(controller.signal.aborted||seq!==_P.seq)return;
    const start=index*P_BROWSER_STARTUP_RANGE_BYTES;
    const end=start+P_BROWSER_STARTUP_RANGE_BYTES-1;
    try{
      const response=await fetch(url.href,{
        method:'GET',
        credentials:'same-origin',
        cache:'reload',
        headers:{Range:`bytes=${start}-${end}`,'X-Archive-X-Prebuffer':'1'},
        signal:controller.signal
      });
      if(response.status!==206)return;
      const bytes=await response.arrayBuffer();
      if(bytes.byteLength!==P_BROWSER_STARTUP_RANGE_BYTES)return;
    }catch(error){
      if(error?.name!=='AbortError')return;
    }
  };
  const run=async()=>{
    // Prime 4 MiB in bounded waves. These are ordinary Range requests to the
    // same short-lived, tokenized URL used by <video>; native playback remains
    // attached and free to proceed while this experiment runs.
    for(let first=0;first<P_BROWSER_STARTUP_BUFFER_CHUNKS;first+=4){
      if(controller.signal.aborted||seq!==_P.seq)return;
      await Promise.all(Array.from({length:4},(_,offset)=>fetchRange(first+offset)));
    }
  };
  void run().finally(()=>{
    if(_P.browserStartupBufferCtl===controller)_P.browserStartupBufferCtl=null;
  });
}

let _preparedSourceWatchCleanup=null;
function pClearPreparedSourceWatch(){
  if(_preparedSourceWatchCleanup){_preparedSourceWatchCleanup();_preparedSourceWatchCleanup=null;}
  _P.preloadReadyKey='';
  const top=$('dtop');
  if(top){delete top.dataset.axPreloadState;top.removeAttribute('aria-busy');}
}
function pWatchPreparedSource(e,seq,key){
  pClearPreparedSourceWatch();
  const top=$('dtop');
  if(top){top.dataset.axPreloadState='preparing';top.setAttribute('aria-busy','true');}
  let active=true;
  const cleanup=()=>{
    if(!active)return;
    active=false;
    e.removeEventListener('loadeddata',markReady);
    e.removeEventListener('canplay',markReady);
    e.removeEventListener('progress',markReady);
    e.removeEventListener('error',markFailed);
  };
  const markReady=()=>{
    if(seq!==_P.seq||key!==_P.mediaKey||!e.getAttribute('src'))return cleanup();
    // A server-side cache hit is not the same as a browser-ready player.
    // HAVE_FUTURE_DATA means the paused native element has enough data to begin
    // playback without replacing the source or waiting for its next range.
    if(Number(e.readyState||0)<3)return;
    _P.preloadReadyKey=key;
    if(top){top.dataset.axPreloadState='ready';top.setAttribute('aria-busy','false');}
    pSetPlayPauseIcon(true);
    cleanup();
    if(_preparedSourceWatchCleanup===cleanup)_preparedSourceWatchCleanup=null;
  };
  const markFailed=()=>{
    if(seq!==_P.seq||key!==_P.mediaKey)return cleanup();
    if(top){top.dataset.axPreloadState='error';top.setAttribute('aria-busy','false');}
    cleanup();
    if(_preparedSourceWatchCleanup===cleanup)_preparedSourceWatchCleanup=null;
  };
  _preparedSourceWatchCleanup=cleanup;
  e.addEventListener('loadeddata',markReady);
  e.addEventListener('canplay',markReady);
  e.addEventListener('progress',markReady);
  e.addEventListener('error',markFailed);
  markReady();
}

function pWatchFirstFrame(seq){
  const e=pv();
  try{if(_P.firstFrameFallbackCleanup){_P.firstFrameFallbackCleanup();_P.firstFrameFallbackCleanup=null;}}catch(x){}
  let pollTimer=0, deadline=performance.now()+30000, pollNotBefore=performance.now()+1200;
  const fallback=()=>pRecordFirstFrame(seq);
  // Arm independent decoded-data events even on browsers exposing RVFC; some
  // hardware decode paths fail to invoke the first RVFC callback until a later
  // playback frame, while loadeddata/playing already prove first-frame readiness.
  e.addEventListener('loadeddata',fallback);
  e.addEventListener('playing',fallback);
  try{if(typeof e.requestVideoFrameCallback==='function')e.requestVideoFrameCallback(fallback);}catch(x){}
  const poll=()=>{
    if(seq!==_P.seq||_P.firstFrameAt||performance.now()>deadline)return;
    if(performance.now()>=pollNotBefore&&Number(e.readyState||0)>=2&&Number(e.videoWidth||0)>0){fallback();if(_P.firstFrameAt)return;}
    pollTimer=setTimeout(poll,125);
  };
  pollTimer=setTimeout(poll,125);
  _P.firstFrameFallbackCleanup=()=>{
    clearTimeout(pollTimer);
    e.removeEventListener('loadeddata',fallback);
    e.removeEventListener('playing',fallback);
  };
}

function _capturePlaybackIncident(reason){
  if(!_P.cid||_P.mid==null)return;
  const now=performance.now();
  if(now-_P.lastPlaybackIncidentAt<PLAYBACK_INCIDENT_COOLDOWN_MS)return;
  _P.lastPlaybackIncidentAt=now;
  const media=_axDiagMedia();
  axDiagRecord('playback.incident.client',{message:`Playback ${reason}`,data:{reason,media,channel_id:_P.cid,message_id:_P.mid,playback_state:_P.state,rebuffer_count:_P.rebufferCount,seek_count:_P.seekCount}},'WARN');
  fetch('/api/diagnostics/playback-incident',{method:'POST',credentials:'include',headers:{'Content-Type':'application/json'},body:JSON.stringify({reason,channel_id:_P.cid,message_id:_P.mid,session_id:_axDiag.sessionId,media})}).catch(()=>{});
}

function _playbackActivity(playing){
  clearTimeout(_P.activityTimer);
  if(!_P.cid||_P.mid==null)return;
  const media=pv(), buffered=_previewBufferedSeconds();
  api('/api/play/activity',{method:'POST',body:JSON.stringify({channel_id:_P.cid,message_id:_P.mid,playing:!!playing})}).catch(()=>{});
  if(_P.mid!=null){
    api('/api/play/telemetry',{method:'POST',body:JSON.stringify({channel_id:_P.cid,message_id:_P.mid,reason:'buffer-heartbeat',buffered_seconds:buffered,target_buffer_seconds:4,current_time:pCur(),playing:!!playing})}).catch(()=>{});
  }
  if(playing){
    _P.activityTimer=setTimeout(()=>_playbackActivity(true),3000);
  }
}

function pInit(){
  const e=pv(), top=$('dtop');
  pABBindMenuEvents();
  pABRender();
  $('psbgrid')?.addEventListener('click',_pSnapshotGridClick);
  window.addEventListener('resize',()=>{if($('dtop')?.classList.contains('preplay-snapshot-grid-on'))pSnapshotGridShow(false);});
  pSetPlayPauseIcon(true);
  const mediaDiag=(type,level='INFO')=>()=>{if(!pMediaEventCurrent())return;const m=_axDiagMedia();axDiagRecord(type,{message:'Media element event',data:m},level);};
  ['loadstart','loadedmetadata','canplay','playing','pause','progress','seeking','seeked','waiting','stalled','canplaythrough','ended','emptied'].forEach(ev=>e.addEventListener(ev,mediaDiag('browser.media.'+ev)));
  e.addEventListener('error',mediaDiag('browser.media.error','ERROR'));
  const syncSnapshotGridVisibility=event=>_snapshotGridModule?.syncVisibility?.(event);
  ['loadeddata','playing','canplay'].forEach(ev=>e.addEventListener(ev,syncSnapshotGridVisibility));
  try{e.volume=parseFloat(localStorage.getItem('ta_vol')||'1');e.muted=localStorage.getItem('ta_muted')==='1';}catch(x){}
  $('pvol').value=e.muted?0:e.volume;
  pSyncVolumeTrack(e.muted ? 0 : e.volume);
  let raf=0;
  const ui=()=>{if(!raf)raf=requestAnimationFrame(()=>{raf=0;pUI();});};
  e.addEventListener('timeupdate',()=>{if(!pMediaEventCurrent())return;const looped=_playerABRepeat.onTimeUpdate();ui();if(!looped)pSaveResume();});
  e.addEventListener('progress',()=>{if(pMediaEventCurrent())ui();});
  e.addEventListener('durationchange',()=>{if(!pMediaEventCurrent())return;ui();const chapterRecord=_playerSceneChapterCache.get(_P.mediaKey);if(chapterRecord)renderPlayerSceneChapterTrack(_P.mediaKey,chapterRecord.mediaId,chapterRecord.markers);});
  e.addEventListener('seeking',()=>{
    if(!pMediaEventCurrent())return;
    // A committed seek is authoritative. Preview work is advisory; leave the
    // Native browser Range requests own exact seek priority.
    pSetState('SEEKING');
    _liveSeekPreviewModule?.suspendForSeek();
    clearTimeout(_P.seekStallTimer);
    const seq=_P.seq,key=_P.mediaKey;
    const resume=(!e.paused)||!!_P.scrubSession?.resume||!!_P.resumeAfterScrub;
    _P.seekStallTimer=setTimeout(()=>{
      if(seq!==_P.seq||key!==_P.mediaKey||!e.getAttribute('src')||!e.seeking||_P.mode!=='direct')return;
      const now=Date.now();
      if(now-Number(_P.lastSeekStallRecovery||0)<12000)return;
      _P.lastSeekStallRecovery=now;
      const target=Math.max(0,Number(e.currentTime)||0);
      pToast('Seek stalled — reconnecting at this position…',2200);
      _P.userPaused=!resume;
      _pStart('direct',target,resume);
    },8500);
  });
  e.addEventListener('seeked',()=>{
    if(!pMediaEventCurrent())return;
    _playerABRepeat.onSeeked();
    clearTimeout(_P.seekStallTimer);
    pSetState(_P.playing()?'PLAYING':'READY');
    if(_P.seekAbort){try{_P.seekAbort=null;}catch(x){}}
    // Do not leave the player in a synthetic loading state after a successful
    // native seek, including when the video was paused before seeking.
    if(e.readyState>=2)$('dtop').classList.remove('loading');
    const pending=_P.resumeAfterScrub;
    if(pending&&!pending.used&&pending.key===_P.mediaKey&&pending.seq===_P.seq){
      pending.used=true;_P.resumeAfterScrub=null;if(!e.error)e.play().catch(()=>{});
    }
  });
  let waitTimer=0;
  const clearLoading=()=>{clearTimeout(waitTimer);waitTimer=0;top.classList.remove('loading');};
  const delayedLoading=()=>{clearTimeout(waitTimer);waitTimer=setTimeout(()=>{if(!e.paused&&e.readyState<2)top.classList.add('loading');},550);};
  e.addEventListener('waiting',()=>{if(!pMediaEventCurrent())return;pSetState('BUFFERING');if(!e.paused){_P.rebufferCount++;_P.lastWaitAt=performance.now();clearTimeout(_P.playbackIncidentT);_P.playbackIncidentT=setTimeout(()=>{if(!e.paused&&e.readyState<3)_capturePlaybackIncident('buffering');},PLAYBACK_INCIDENT_DELAY_MS);}delayedLoading();});
  e.addEventListener('stalled',()=>{if(!pMediaEventCurrent())return;clearTimeout(_P.playbackIncidentT);_P.playbackIncidentT=setTimeout(()=>{if(!e.paused)_capturePlaybackIncident('stalled');},PLAYBACK_INCIDENT_DELAY_MS);delayedLoading();});
  e.addEventListener('playing',()=>{if(!pMediaEventCurrent())return;clearTimeout(_P.startupStallTimer);_playbackActivity(true);clearTimeout(_P.playbackIncidentT);pSetState('PLAYING');pSetPlayPauseIcon(false);if(!_P.firstPlayingAt&&_P.playOpenAt)_P.firstPlayingAt=performance.now();clearLoading();top.classList.remove('paused');$('pmsg').style.display='none';pAudioCheck();pAutoHide();});
  e.addEventListener('canplaythrough',()=>{if(pMediaEventCurrent())clearLoading();});
  e.addEventListener('error',()=>{ if(!pMediaEventCurrent())return; if(_P.mode==='direct'){ const mediaCode=Number(e.error&&e.error.code||0); if(mediaCode===2&&pDirectNetworkRecover())return; setTimeout(()=>{ if(_P.mode==='direct') pDirectFailure('browser media error',mediaCode); },180); } });
  e.addEventListener('canplay',()=>{if(pMediaEventCurrent())clearLoading();});
  e.addEventListener('timeupdate',()=>{if(pMediaEventCurrent()&&!e.paused&&e.readyState>=2)clearLoading();});
  e.addEventListener('pause',()=>{
    if(!pMediaEventCurrent())return;
    _playbackActivity(false);clearTimeout(_P.playbackIncidentT);top.classList.add('paused');pSetPlayPauseIcon(true);pShowUI();
  });
  e.addEventListener('play',()=>{
    if(!pMediaEventCurrent())return;
    try{if(_P.cid&&_P.mid!=null)localStorage.setItem('ta_last_played_video',vkey(_P.cid,_P.mid));}catch(x){}
    // `play` fires before a decoded frame exists. Keep the play glyph and loading
    // state until `playing` confirms that the video really rendered/advanced.
    top.classList.add('loading');top.classList.add('paused');pSetPlayPauseIcon(true);
  });
  e.addEventListener('ended',()=>{if(!pMediaEventCurrent())return;_playbackActivity(false);pSetState('ENDED');if(!e.getAttribute('src'))return;
    // Multi-part: advance to next part instead of next video
    if(_P.parts&&_P.partIndex<_P.parts.length-1){
      _pPartAdvance(_P.partIndex+1);return;
    }
    // Report progress for the logical video (use logicalCid/logicalMid if multi-part)
    const rCid=_P.logicalCid||_P.cid,rMid=_P.logicalMid||_P.mid;
    pClearResume();try{if(localStorage.getItem('ta_last_played_video')===vkey(rCid,rMid))localStorage.removeItem('ta_last_played_video');}catch(x){}api('/api/play/progress',{method:'POST',body:JSON.stringify({channel_id:rCid,message_id:rMid,position:pDur(),duration:pDur(),watch_delta:0})}).catch(()=>{});if(getOpt('ta_autonext',true))nextVSmart();});
  e.addEventListener('loadedmetadata',()=>{if(!pMediaEventCurrent())return;if(e.videoWidth>0&&e.videoHeight>0)axPublishPlayerDimensions(findVideo(_P.mid,_P.cid)||{width:e.videoWidth,height:e.videoHeight},_P.cid,_P.mid,{width:e.videoWidth,height:e.videoHeight});if(_P.mode==='direct'&&e.videoWidth===0&&_P.info&&_P.info.vcodec)pDirectFailure('no picture');ui();});
  e.addEventListener('error',()=>{if(!pMediaEventCurrent())return;_snapshotGridModule?.playbackFailed?.();pSetState('ERROR');
    if(!e.getAttribute('src'))return;
    if(_P.mode==='direct'){
      const mediaCode=Number(e.error&&e.error.code||0);
      const reason=mediaCode===2?'the connection to the video stream kept failing':([3,4].includes(mediaCode)?'the browser cannot decode this file':'browser media error');
      pDirectFailure(reason,mediaCode);
    }
    else if(_P.mode==='hls'||_P.mode==='compat')pCompatFailure();
    else pError('Playback failed.');
  });
  e.addEventListener('volumechange',()=>{try{localStorage.setItem('ta_vol',e.volume);localStorage.setItem('ta_muted',e.muted?'1':'0');}catch(x){}pSyncVolumeTrack(e.muted?0:e.volume);});
  // Pointer/touch gestures: double-click/double-tap on the left/right side seeks 30s.
  // Single taps remain non-destructive so the explicit center control remains the
  // authoritative play/pause target. Horizontal drag seeking is preserved.
  let lastTap=0,tapT=null,touch=null;
  const gesture=(m,ms=1000)=>{const g=$('pgesture');g.textContent=m;top.classList.add('gesture');clearTimeout(g._t);g._t=setTimeout(()=>top.classList.remove('gesture'),ms);};
  const skipBySide=(x,w)=>{const side=x/w;if(side<0.35){pSkip(-30);gesture('−30s',650);return true;}if(side>0.65){pSkip(30);gesture('+30s',650);return true;}return false;};
  $('ptap').addEventListener('pointerdown',ev=>{
    if(ev.pointerType==='mouse'){pShowUI();return;}
    pShowUI();
    const r=$('ptap').getBoundingClientRect();
    touch={x:ev.clientX,y:ev.clientY,lastX:ev.clientX,lastY:ev.clientY,t:Date.now(),w:r.width,h:r.height,moved:false};
    try{$('ptap').setPointerCapture(ev.pointerId);}catch(x){}
  });
  $('ptap').addEventListener('pointermove',ev=>{
    if(ev.pointerType==='mouse'||!touch)return;
    const dx=ev.clientX-touch.x,dy=ev.clientY-touch.y;
    if(Math.abs(dx)+Math.abs(dy)>12)touch.moved=true;
    if(Math.abs(dx)>Math.abs(dy)){
      if(!e.getAttribute('src')){
        if(Math.abs(ev.clientX-touch.lastX)>18){touch.lastX=ev.clientX;touch.moved=true;}
        return;
      }
      if(!touch.moved)return;
      const d=pDur();
      if(d){
        // Keep touch dragging visual-only too: preview follows the pointer, and
        // pLiveScrubFinish commits one native seek after the gesture ends.
        if(touch.seekBase==null){touch.seekBase=pCur();touch.seekX=touch.lastX;_P.dragging=true;top.classList.add('scrubbing');pLiveScrubBegin();}
        const delta=(ev.clientX-touch.seekX)/touch.w*d*0.9;
        const target=Math.max(0,Math.min(d-0.1,touch.seekBase+delta));
        touch.seekTarget=target;
        pLiveScrubTo(target);gesture((delta>=0?'+':'−')+fdur(Math.abs(delta)),900);
      }
    }
    touch.lastX=ev.clientX;touch.lastY=ev.clientY;
  });
  const finishPointer=ev=>{
    if(!touch)return;
    if(ev.pointerType==='mouse'){touch=null;return;}
    const wasMoved=touch.moved, x=ev.clientX-touch.x, y=ev.clientY-touch.y, w=touch.w, elapsed=Date.now()-touch.t, seekTarget=touch.seekTarget;
    touch=null;
    if(seekTarget!=null){
      _P.dragging=false;top.classList.remove('scrubbing');
      if(_liveSeekPreviewEnabled)pPreview(seekTarget/Math.max(1,pDur()),true,true);
      pLiveScrubFinish(seekTarget,true);
      clearTimeout(_P.previewTouchHide);
      _P.previewTouchHide=setTimeout(()=>{if(!_P.dragging)hideTimelinePreview();},450);
      return;
    }
    if(wasMoved||Math.abs(x)+Math.abs(y)>12||elapsed>500)return;
    const now=Date.now();
    if(now-lastTap<360){clearTimeout(tapT);tapT=null;lastTap=0;skipBySide(ev.clientX-( $('ptap').getBoundingClientRect().left ),w);return;}
    lastTap=now;
    clearTimeout(tapT);
    tapT=setTimeout(()=>{lastTap=0;tapT=null;},360);
  };
  $('ptap').addEventListener('pointerup',finishPointer);
  $('ptap').addEventListener('pointercancel',()=>{if(touch&&touch.seekTarget!=null){_P.dragging=false;top.classList.remove('scrubbing');pLiveScrubFinish(touch.seekTarget);pUI();hideTimelinePreview();}touch=null;});
  $('ptap').addEventListener('dblclick',ev=>{
    const r=$('ptap').getBoundingClientRect();
    if(skipBySide(ev.clientX-r.left,r.width))ev.preventDefault();
  });
  $('ptap').addEventListener('click',ev=>{ev.preventDefault();pShowUI();});
  top.addEventListener('mouseenter',()=>{if(!e.paused)pShowUI();});
  top.addEventListener('mousemove',()=>pShowUI());
  top.addEventListener('mouseleave',()=>{
    // Leaving the player starts the same 7-second idle window as pointer
    // movement; do not hide the controls abruptly on mouseleave.
    if(!e.paused&&!_P.dragging)pAutoHide();
  });
  // seek bar
  const seek=$('pseek');
  const frac=ev=>{const r=seek.getBoundingClientRect();return Math.max(0,Math.min(1,(ev.clientX-r.left)/r.width));};
  seek.addEventListener('pointerenter',()=>{if(_liveSeekPreviewEnabled)_liveSeekPreviewModule?.setHoverActive(true);});
  seek.addEventListener('pointerdown',ev=>{
    _P.dragging=true; _P.previewLastBucket=-1; _liveSeekPreviewModule?.cancelTimerOnly();
    top.classList.add('scrubbing');pLiveScrubBegin();
    try{seek.setPointerCapture(ev.pointerId);}catch(x){}
    const f=frac(ev),d=pDur();pDragTo(f);if(d)pLiveScrubTo(f*d);
  });
  seek.addEventListener('pointermove',ev=>{
    const f=frac(ev);
    updatePlayerSceneChapterHover(f*pDur());
    if(_P.dragging){const d=pDur();pDragTo(f);if(d)pLiveScrubTo(f*d);return;}
    // Hover previews are for a real mouse/pen hover only. Touch devices keep
    // native drag-to-seek and don't issue dozens of image requests while scrolling.
    if(_liveSeekPreviewEnabled&&(ev.pointerType==='mouse'||ev.pointerType==='pen'))pPreview(f);
  });
  seek.addEventListener('pointerup',ev=>{
    if(!_P.dragging)return;
    try{seek.releasePointerCapture(ev.pointerId);}catch(x){}
    _P.dragging=false;top.classList.remove('scrubbing');
    const f=frac(ev),d=pDur();
    // Queue the exact release target. Keep the live-only preview alive while its
    // request finishes instead of aborting it on the native seek event.
    if(d){if(_liveSeekPreviewEnabled)pPreview(f,true,true);pLiveScrubFinish(f*d,true);}
    if(ev.pointerType!=='mouse'){
      clearTimeout(_P.previewTouchHide);
      _P.previewTouchHide=setTimeout(()=>{if(!_P.dragging)hideTimelinePreview();},450);
    }
  });
  seek.addEventListener('pointercancel',()=>{_P.dragging=false;top.classList.remove('scrubbing');pLiveScrubFinish(null);pUI();hideTimelinePreview();});
  seek.addEventListener('pointerleave',()=>{clearPlayerSceneChapterHover();if(!_P.dragging)hideTimelinePreview();});
  seek.addEventListener('mouseleave',()=>{if(!_P.dragging)hideTimelinePreview();});
  seek.addEventListener('mouseout',ev=>{if(!_P.dragging&&(!ev.relatedTarget||!seek.contains(ev.relatedTarget)))hideTimelinePreview();});
  document.addEventListener('keydown',pKey);
  const applyOrientation=()=>{
    const land=pPhoneOrientation()==='landscape';
    top.classList.toggle('landscape',land);
    top.classList.toggle('portrait',!land);
  };
  applyOrientation();
  window.addEventListener('orientationchange',()=>{setTimeout(applyOrientation,80);});
  window.addEventListener('resize',()=>{setTimeout(applyOrientation,40);});
  if(window.screen&&screen.orientation&&screen.orientation.addEventListener)screen.orientation.addEventListener('change',applyOrientation);
  document.addEventListener('fullscreenchange',()=>{const fs=!!(document.fullscreenElement||document.webkitFullscreenElement);top.classList.toggle('fs',fs);if(fs)top.classList.remove('controls-below-video');applyOrientation();pShowUI();});
  document.addEventListener('webkitfullscreenchange',()=>{const fs=!!(document.fullscreenElement||document.webkitFullscreenElement);top.classList.toggle('fs',fs);if(fs)top.classList.remove('controls-below-video');applyOrientation();pShowUI();});
  e.addEventListener('webkitbeginfullscreen',()=>{top.classList.add('fs');top.classList.remove('controls-below-video');applyOrientation();pShowUI();});
  e.addEventListener('webkitendfullscreen',()=>{top.classList.remove('fs');applyOrientation();pShowUI();});
}

// Timeline scrubbing is visual-only during pointer movement. Live seek preview
// follows the target without moving the native media clock; the core commits one
// native seek on release. The 36-frame grid remains a separate pre-play surface.
function _previewBufferedSeconds(){
  const e=pv(),c=pCur();let end=0;
  try{for(let i=0;i<e.buffered.length;i++){if(e.buffered.start(i)<=c+0.5)end=Math.max(end,e.buffered.end(i));}}catch(x){}
  return Math.max(0,end-c);
}
// Live seek preview behavior is isolated in player/live-seek-preview.js.
// pPreview/hideTimelinePreview remain as compatibility wrappers for the player UI.
function hideTimelinePreview(){return _liveSeekPreviewModule?.hide();}
function pPreview(f,immediate=false,previewOnly=false){return _liveSeekPreviewModule?.preview(f,immediate,previewOnly);}

function pSetPlayPauseIcon(paused){
  const icon = paused ? 'play' : 'pause';
  // Play is a filled glyph; pause is a stroked glyph. Applying the `fill`
  // class to pause sets stroke:none, which makes the two pause bars invisible.
  const cls = paused ? 'fill' : '';
  const html = axIcon(icon, cls);
  const prepared = paused && _P.preloadedKey === _P.mediaKey && !!pv()?.getAttribute('src');
  const ready = prepared && _P.preloadReadyKey === _P.mediaKey;
  const label = paused
    ? (ready ? 'Play video — ready' : prepared ? 'Play video — preparing' : 'Play')
    : 'Pause';
  const small = $('pplaybtn');
  const big = $('pbig');
  if (small) small.innerHTML = html;
  if (big) big.innerHTML = html;
  if (small) { small.setAttribute('aria-label', label); small.title = label; }
  if (big) { big.setAttribute('aria-label', label); big.title = label; }
}

function pToggle(){
  const e=pv();
  if(_P.preloadedKey&&_P.preloadedKey===_P.mediaKey&&e.getAttribute('src')&&e.paused){
    pSetPlayPauseIcon(true);_P.userPaused=false;
    playB(_P.mid,_P.cid,_dv?.title||_dv?.name||'',false).catch(()=>{});
    return;
  }
  if(!e.getAttribute('src')){if(_dv){playB(_dv.id,_dv._cid,_dv.title||_dv.name);}return;}
  if(e.paused){
    // Playing never changes player size. Keep the play glyph until a decoded
    // `playing` event confirms video output; a promise resolution is too early.
    pSetPlayPauseIcon(true);_P.userPaused=false;
    e.play().catch(()=>{pSetPlayPauseIcon(true);});
  }else{
    pSetPlayPauseIcon(true);_P.userPaused=true;
    e.pause();
  }
}
// Skips: the first press seeks at once; presses that follow within 400 ms are
// merged into one trailing seek, so holding an arrow key sends a single
// byte-range request at the end instead of one per key repeat.
let _skipAt=0,_skipTarget=0,_skipTimer=0,_skipSum=0;
function pSkip(s){
  const d=pDur(),now=Date.now(),clamp=t=>Math.max(0,Math.min(t,d?d-1:t));
  if(!_skipTimer&&now-_skipAt>400){
    _skipAt=now;_skipSum=s;_skipTarget=clamp(pCur()+s);
    pSeek(_skipTarget);pToast((s>0?'+':'')+s+'s');return;
  }
  _skipAt=now;_skipSum+=s;_skipTarget=clamp(_skipTarget+s);
  pToast((_skipSum>0?'+':'')+_skipSum+'s');
  clearTimeout(_skipTimer);
  _skipTimer=setTimeout(()=>{_skipTimer=0;_skipAt=Date.now();pSeek(_skipTarget);},300);
}
function pToast(m){const t=$('ptoast');t.textContent=m;t.style.display='block';clearTimeout(t._t);t._t=setTimeout(()=>t.style.display='none',1400);}
function pError(m){_snapshotGridModule?.playbackFailed?.(true);const b=$('pmsg');$('dtop').classList.remove('loading');
  b.innerHTML=`<div>${esc(m)}</div><div style="display:flex;gap:6px;justify-content:center;margin-top:10px;flex-wrap:wrap">
    <button class="btn sm" onclick="pRetry()"><svg class="ax-icon" aria-hidden="true"><use href="#ax-refresh"></use></svg> Try again</button>
    <button class="btn sm" onclick="${_isRemote()?'playHere':'sendPD'}(${_isRemote()?'':"'vlc',"}${Number(_P.mid)},${jsa(_P.cid)})">${axIcon('brand-vlc','brand-icon')}Open in VLC</button></div>`;
  b.style.display='block';}
function pCompatFailure(){
  pError('HLS and FFmpeg compatibility playback are disabled. Retry the original stream or open it in VLC.');
}
function pRetryDirect(){$('pmsg').style.display='none';_P.fell=false;_P.autoFallbackTried=true;_P.netRetries=[];_P.netRecovering=0;_pStart('direct',pCur());}
function pRetry(){$('pmsg').style.display='none';_P.fell=false;_P.autoFallbackTried=false;_P.iosHlsFallbackTried=false;_P.netRetries=[];_P.netRecovering=0;_pStart(pInitialMode(_P.info||{}),pCur());}
function pRate(){const r=[0.75,1,1.25,1.5,2];_P.rate=r[(r.indexOf(_P.rate)+1)%r.length]||1;pv().playbackRate=_P.rate;$('prate').textContent=_P.rate+'×';}
function pSyncVolumeTrack(value){const input=$('pvol'),button=$('pmute');if(!input)return;const level=Math.max(0,Math.min(1,Number(value)||0));input.style.setProperty('--volume-level',(level*100).toFixed(1)+'%');if(button){const muted=level<=0;button.classList.toggle('muted',muted);button.setAttribute('aria-label',muted?'Unmute':'Mute');button.title=muted?'Unmute (M)':'Mute (M)';}}
function pMute(){const e=pv();e.muted=!e.muted;if(!e.muted&&e.volume===0)e.volume=0.5;$('pvol').value=e.muted?0:e.volume;pSyncVolumeTrack(e.muted?0:e.volume);}
function pVol(v){const e=pv();e.volume=Math.max(0,Math.min(1,parseFloat(v)||0));e.muted=e.volume===0;pSyncVolumeTrack(e.muted?0:e.volume);}
function pPip(){const e=pv();if(document.pictureInPictureElement)document.exitPictureInPicture().catch(()=>{});else if(e.requestPictureInPicture)e.requestPictureInPicture().catch(()=>toast('Picture-in-picture is not available here.'));}
function applyOrientationSafe(){
  const top=$('dtop');
  const land=pPhoneOrientation()==='landscape';
  top.classList.toggle('landscape',land);
  top.classList.toggle('portrait',!land);
}
function pPhoneOrientation(){
  try{
    const o=(screen.orientation&&screen.orientation.type)||'';
    if(o.startsWith('landscape'))return 'landscape';
    if(o.startsWith('portrait'))return 'portrait';
  }catch(x){}
  return window.innerWidth>window.innerHeight?'landscape':'portrait';
}

function pFull(fromGesture){
  const top=$('dtop'),e=pv();
  // Fullscreen is intentionally manual: only the fullscreen button calls this function.

  if(document.fullscreenElement||document.webkitFullscreenElement){
    try{if(document.exitFullscreen)document.exitFullscreen();else if(document.webkitExitFullscreen)document.webkitExitFullscreen();}catch(x){}
    return;
  }
  const orient=()=>{
    const type=pPhoneOrientation()==='landscape'?'landscape':'portrait';
    try{if(screen.orientation&&screen.orientation.lock)screen.orientation.lock(type).catch(()=>{});}catch(x){}
  };
  const done=()=>{top.classList.add('fs');top.classList.remove('controls-below-video');top.classList.toggle('landscape',pPhoneOrientation()==='landscape');top.classList.toggle('portrait',pPhoneOrientation()!=='landscape');orient();pShowUI();};
  if(top.requestFullscreen){
    const req=top.requestFullscreen({navigationUI:'hide'});
    Promise.resolve(req).then(done).catch(()=>{if(fromGesture&&e.webkitEnterFullscreen){try{e.webkitEnterFullscreen();}catch(x){}}});
  }else if(top.webkitRequestFullscreen){
    try{top.webkitRequestFullscreen();done();}catch(x){}
  }else if(e.webkitEnterFullscreen){
    try{e.webkitEnterFullscreen();}catch(x){}
  }
}
function pKey(ev){
  if(!$('do').classList.contains('open'))return;
  const target=ev.target;
  // Let focused controls receive their native keyboard activation. In particular,
  // Space on the Play/Pause button must not both invoke this shortcut and click the button.
  // Only keys the focused control itself needs are left to it: text fields keep every
  // key, buttons keep Space/Enter (no double toggle), sliders keep the arrow keys.
  // Previously ANY focused button (e.g. after clicking a control with the mouse)
  // disabled every shortcut, including Escape, until the video was clicked again.
  const k=ev.key;
  if($('pk').classList.contains('open')||$('sm').classList.contains('open'))return;
  if(target&&target.closest){
    if(target.closest('textarea,select,[contenteditable="true"],input:not([type=range]):not([type=checkbox]):not([type=radio]):not([type=button])'))return;
    if((k===' '||k==='Enter')&&target.closest('button,a,[role=button],input[type=checkbox],input[type=radio]'))return;
    if(/^Arrow/.test(k)&&target.closest('[role=slider],input[type=range]'))return;
  }
  const e=pv();
  if(k===' '||k==='k'){ev.preventDefault();pToggle();}
  else if(k==='ArrowLeft'){ev.preventDefault();pSkip(-5);}
  else if(k==='ArrowRight'){ev.preventDefault();pSkip(5);}
  else if(k==='j')pSkip(-30);
  else if(k==='l')pSkip(30);
  else if(k==='ArrowUp'){ev.preventDefault();e.volume=Math.min(1,e.volume+0.1);e.muted=false;$('pvol').value=e.volume;pSyncVolumeTrack(e.volume);pToast('Volume '+Math.round(e.volume*100)+'%');}
  else if(k==='ArrowDown'){ev.preventDefault();e.volume=Math.max(0,e.volume-0.1);e.muted=e.volume===0;$('pvol').value=e.volume;pSyncVolumeTrack(e.volume);pToast('Volume '+Math.round(e.volume*100)+'%');}
  else if(k==='f')pFull();
  else if(k==='m')pMute();
  else if(k==='n')nextV();
  else if(k==='P')prevV();
  else if(k==='Escape'&&!document.fullscreenElement&&!document.webkitFullscreenElement)closeDBtn();
}
function pInitialMode(info){
  // v29.5: try client-side smart streaming for non-direct-playable media
  if(info&&!info.direct_likely&&window.ArchiveXSmartStream&&window.ArchiveXSmartStream.canPlay(info))return 'mse';
  return 'direct';
}

// V29.4.120: a network/read error (MEDIA_ERR_NETWORK, Chrome's
// "PIPELINE_ERROR_READ: data source error") is not a decode problem. Reconnect
// the direct stream at the same position instead of switching a playable
// (e.g. HEVC) video to the slow compatibility transcoder. Bounded to 3
// reconnects per 2 minutes; after that the normal fallback applies.
function pDirectNetworkRecover(){
  if(_P.mode!=='direct'||!_P.cid||!_P.mid)return false;
  const now=Date.now();
  _P.netRetries=(_P.netRetries||[]).filter(x=>now-x<120000);
  if(_P.netRetries.length>=5){_P.netRecovering=0;return false;}
  const delay=[250,1000,2500,5000,8000][_P.netRetries.length];
  _P.netRetries.push(now);
  _P.netRecovering=now+delay;
  const t=Math.max(0,pCur()||0),resume=!_P.userPaused;
  pToast('Reconnecting…',1500);
  setTimeout(()=>{if(_P.mode==='direct')_pStart('direct',t,resume);},delay);
  return true;
}
function pDirectFailure(why,mediaErrorCode=0){
  if(_P.netRecovering&&Date.now()-_P.netRecovering<2500)return;
  if(_P.mode!=='direct'||_P.fell)return;
  // Keep recoverable network failures on the original byte-range path. A
  // decode-only failure may try the client-side MSE path, but never invokes
  // the server-side HLS/FFmpeg compatibility pipeline.
  const decodeFailure=[3,4].includes(Number(mediaErrorCode))||/no picture|cannot decode|not supported|unsupported codec/i.test(String(why||''));
  if(decodeFailure&&!_P.autoFallbackTried&&_P.cid&&_P.mid&&window.ArchiveXSmartStream&&window.ArchiveXSmartStream.canPlay(_P.info)){
    _P.autoFallbackTried=true;
    const at=Math.max(0,Number(pCur())||Number(_P.target)||0);
    const resume=!_P.userPaused;
    $('pmsg').style.display='none';
    _P.fell=false;
    pToast('Trying client-side smart playback…');
    _pStart('mse',at,resume);
    return;
  }
  _P.fell=true;
  const detail=why?` (${why})`:'';
  const box=$('pmsg');
  if(box){
    const note='Automatic HLS/FFmpeg conversion is disabled. You can retry the original stream or open it in VLC.';
    box.innerHTML=`<div>${esc('This video could not be played from the original stream'+detail+'.')}</div><div class="muted" style="font-size:11px;margin-top:6px">${esc(note)}</div><div style="display:flex;gap:6px;justify-content:center;margin-top:10px;flex-wrap:wrap"><button class="btn sm pri" onclick="pRetry()">Retry original</button><button class="btn sm" onclick="${_isRemote()?'playHere':'sendPD'}(${_isRemote()?'':"'vlc',"}${Number(_P.mid)},${jsa(_P.cid)})">${axIcon('brand-vlc','brand-icon')}Open in VLC</button></div>`;
    box.style.display='block';
  }
  _snapshotGridModule?.playbackFailed?.(true);
  $('dtop').classList.remove('loading');
}

function pAudioCheck(){
  // Chrome can expose a playable MP4/HEVC source while decoding audio but no
  // video frames. Detect that explicitly instead of leaving the user with a
  // silent/black picture and no explanation. Direct remains the default; this
  // only presents the manual Compatibility fallback.
  const e=pv(),seq=_P.seq;
  if(_P.mode!=='direct'||_P.fell)return;
  setTimeout(()=>{
    if(seq!==_P.seq||e.paused||_P.mode!=='direct')return;
    const noAudio=(e.webkitAudioDecodedByteCount===0)||(e.mozHasAudio===false);
    const expects=_P.info?!!_P.info.acodec:false;
    if(noAudio&&expects&&e.currentTime>1)pToast('No sound decoded for this video',2600);
    const q=e.getVideoPlaybackQuality?.();
    const decodedFrames=typeof e.webkitDecodedFrameCount==='number'?e.webkitDecodedFrameCount:(q&&typeof q.totalVideoFrames==='number'?q.totalVideoFrames:null);
    const noPicture=(e.videoWidth===0||e.videoHeight===0)||(decodedFrames!==null&&decodedFrames===0);
    if(noPicture&&e.currentTime>1)pDirectFailure('audio is playing but no video frames are being decoded');
  },3200);
}
function _pStart(mode,t,autoPlay=true,preloadForDetail=false){
  // Stamp the element before attaching the selected source. The responsive
  // player can then accept intrinsic dimensions only for this exact media key.
  const _selectedPlayerElement=pv();
  if(_selectedPlayerElement)_selectedPlayerElement.dataset.axMediaKey=vkey(_P.cid,_P.mid);
  if(mode==='compat'||mode==='hls'){
    pError('HLS and FFmpeg compatibility playback are disabled. Retry the original stream or open it in VLC.');
    return;
  }
  if(!['direct','cached','mse'].includes(mode))mode=pInitialMode(_P.info||{});
  pCancelBrowserStartupBuffer();
  clearTimeout(_P.prevTimer);
  _liveSeekPreviewModule?.cancelQueued();
  const e=pv(),seq=++_P.seq,cid=_P.cid,mid=_P.mid;t=Math.max(0,t||0);
  const playbackToken=_newPlaybackToken()||`${seq}-${Math.random().toString(36).slice(2)}`;
  _P.playbackGeneration=seq;_P.playbackToken=playbackToken;
  const claimQuery=`axclient=${encodeURIComponent(_playbackClientId)}&axgen=${seq}&axtoken=${encodeURIComponent(playbackToken)}`;
  if(autoPlay&&!preloadForDetail)_PStartupStillPending=true;
  const wasFS=!!(document.fullscreenElement||document.webkitFullscreenElement||$('dtop').classList.contains('fs'));
  _P.mode=mode;_P.off=0;_P.target=t;_P.keepFullscreen=wasFS;$('pmsg').style.display='none';
  if(!preloadForDetail)_snapshotGridModule?.beginPlayback?.(t);
  if(preloadForDetail)$('dtop').classList.remove('loading');else $('dtop').classList.add('loading');
  $('dtop').classList.add('paused');pSetPlayPauseIcon(true);
  // Hard-reset the media element before every new source/seek request. This prevents
  // the previous video's currentTime/buffer from being reused by the next video.
  pClearPreparedSourceWatch();
  try{e.pause();e.removeAttribute('src');e.load();e.currentTime=0;}catch(x){}
  if(preloadForDetail)pWatchPreparedSource(e,seq,vkey(cid,mid));
  let played=false;
  const startPlayback=()=>{
    if(seq!==_P.seq||played)return;
    if(!autoPlay)return;
    played=true;
    e.play().catch(err=>{
      if(seq!==_P.seq)return;
      $('dtop').classList.add('paused');$('dtop').classList.remove('loading');
      // Safari can revoke transient user activation while playB waits for the
      // previous source to release. Keep the source attached and tell the user
      // to tap the visible Play control; do not misreport this as a codec error.
      if(err?.name==='NotAllowedError'&&_isIOS())pToast('Safari blocked autoplay. Tap Play to start this video.',4200);
      else if(mode==='hls')pError('Safari could not start the compatibility stream. Tap Retry or open the video in VLC.');
    });
  };
  const at=()=>{
    if(seq!==_P.seq)return;
    if(t>0.5){try{e.currentTime=Math.min(t,Math.max(0,(e.duration||t)-0.25));}catch(x){}}
    pUI();
    if(mode!=='compat'&&autoPlay)startPlayback();
  };
  if(mode==='mse'&&window.ArchiveXSmartStream){
    // v29.5: Client-side MSE/WebCodecs smart streaming — no server transcoding
    // Destroy any previous smart pipeline
    if(_P._smartPipeline){try{_P._smartPipeline.destroy();}catch(_){}_P._smartPipeline=null;}
    const smartUrl=`/stream/${encodeURIComponent(cid)}/${mid}?${claimQuery}`;
    _P.activeSrc=smartUrl;
    const pipeline=window.ArchiveXSmartStream.start(e,smartUrl,_P.info||{},{
      onError:function(msg){
        if(seq!==_P.seq||_P.fell)return;
        console.warn('[SmartStream] error:',msg);
        _P._smartPipeline=null;
        _P.fell=true;
        pError('Client-side smart playback failed. Automatic HLS/FFmpeg conversion is disabled. Retry the original stream or open it in VLC.');
      },
      onProgress:function(loaded,total){},
      onComplete:function(){},
      startTime:t,
    });
    if(pipeline){
      _P._smartPipeline=pipeline;
      if(t>0.5)e.addEventListener('loadedmetadata',at,{once:true});
      pWatchFirstFrame(seq);
      if(autoPlay)startPlayback();
    }else{
      // Smart stream init returned null — library not ready, fall through to direct
      _pStart('direct',t,autoPlay,preloadForDetail);
      return;
    }
  }else if(mode==='direct'){
    if(t>0.5)e.addEventListener('loadedmetadata',at,{once:true});
    e.src=`/stream/${encodeURIComponent(cid)}/${mid}?${claimQuery}`;_P.activeSrc=e.src;pWatchFirstFrame(seq);
    if(t<=0.5&&(preloadForDetail||autoPlay))pPrebufferBrowserStartupRanges(e.src,seq);
    if(t<=0.5&&autoPlay)startPlayback();
  }else if(mode==='cached'){
    e.src=`/stream-cached/${encodeURIComponent(cid)}/${mid}?${claimQuery}`;_P.activeSrc=e.src;pWatchFirstFrame(seq);
    if(t>0.5)e.addEventListener('loadedmetadata',at,{once:true});
    if(t<=0.5&&autoPlay)startPlayback();
  }
  // Promote the first available cached still while the stream starts. This is
  // fire-and-forget: neither the media source nor Play waits for snapshot I/O.
  const showStartupStill=()=>{
    if(seq!==_P.seq||_P.mediaKey!==vkey(cid,mid)||!_PStartupStillPending||_P.firstFrameAt||_P.state==='ERROR')return;
    const current=pv();
    if(Number(current?.readyState||0)>=2&&Number(current?.videoWidth||0)>0)return;
    _snapshotGridModule?.preserveForPlayback?.(vkey(cid,mid),t);
  };
  if(!preloadForDetail)pLoadPreplaySnapshotGrid(cid,mid,false,showStartupStill).then(showStartupStill).catch(()=>{});
  if(autoPlay&&t>0.5&&mode!=='compat')setTimeout(()=>{if(seq===_P.seq)startPlayback();},5000);
  // A rejected/stalled metadata load otherwise leaves only a black canvas and
  // the misleading pause icon. Keep a bounded startup diagnostic + retry path.
  clearTimeout(_P.startupStallTimer);
  if(!preloadForDetail)_P.startupStallTimer=setTimeout(()=>{
    if(seq!==_P.seq||!e.getAttribute('src')||e.readyState>=2||_P.state==='PLAYING')return;
    const box=$('pmsg');if(box){box.innerHTML=`<div>${esc('Video data has not arrived yet.')}</div><div class="muted" style="font-size:11px;margin-top:6px">The stream may be waiting for its MP4 metadata. Retry the current position or go back and try again.</div><div style="display:flex;gap:6px;justify-content:center;margin-top:10px"><button class="btn sm pri" onclick="pRetry()">Retry</button><button class="btn sm" onclick="closeDBtn()">Back</button></div>`;box.style.display='block';}
  },15000);
  _P.preloadedKey=preloadForDetail&&!autoPlay?vkey(cid,mid):'';
  if(preloadForDetail)pSetPlayPauseIcon(true);
  e.playbackRate=_P.rate||1;
  pUI();
  if(wasFS){
    // A codec fallback/source replacement must not throw the user out of the
    // custom fullscreen player. Re-assert fullscreen after the new source is
    // attached; this is especially important on iPad Safari.
    setTimeout(()=>{
      if(seq!==_P.seq||!_P.keepFullscreen)return;
      if(!document.fullscreenElement&&!document.webkitFullscreenElement){
        try{if($('dtop').requestFullscreen)$('dtop').requestFullscreen({navigationUI:'hide'}).catch(()=>{});
        else if($('dtop').webkitRequestFullscreen)$('dtop').webkitRequestFullscreen();}catch(x){}
      }
      $('dtop').classList.add('fs');applyOrientationSafe();
    },80);
  }
}
// V29.4.129: tell the server the viewer left a video, so its downloads stop
// and its temporary cache is deleted. keepalive lets it finish even while the
// page navigates. Not sent when the same video is about to play again.
function pReleaseServer(cid,mid,reason){
  if(cid==null||mid==null||cid===''||Number.isNaN(Number(mid)))return;
  const playbackClaim=_P.playbackToken?{
    playback_client_id:_playbackClientId,
    playback_generation:Number(_P.playbackGeneration||0),
    playback_token:String(_P.playbackToken)
  }:{};
  try{
    return api('/api/play/release',{method:'POST',keepalive:true,body:JSON.stringify({channel_id:String(cid),message_id:Number(mid),reason:reason||'viewer_left',...playbackClaim})}).catch(()=>{});
  }catch(x){}
}
function pStop(nextKey){
  // v29.5: destroy any active smart streaming pipeline
  if(_P._smartPipeline){try{_P._smartPipeline.destroy();}catch(_){}_P._smartPipeline=null;}
  pClearPreparedSourceWatch();
  pCancelBrowserStartupBuffer();
  const abTargetKey=String(nextKey||'');
  if(abTargetKey!==String(_P.mediaKey||'')||_playerABRepeat.snapshot().mediaKey!==abTargetKey)pABReset(abTargetKey);
  _liveSeekPreviewModule?.clearRetainedFrame?.();
  const keepLocalPoster=!!(nextKey&&_snapshotGridModule?.preserveForPlayback?.(nextKey,Number(_P.pendingStartAt)||0));
  if(!keepLocalPoster)pSnapshotGridHide();
  if(_P.scrubSession){clearTimeout(_P.scrubSession.timer);_P.scrubSession=null;}
  _P.resumeAfterScrub=null;clearTimeout(_P.seekStallTimer);
  try{if(_P.firstFrameFallbackCleanup)_P.firstFrameFallbackCleanup();}catch(x){}
  _P.firstFrameFallbackCleanup=null;
  // Suppress the asynchronous pause event emitted while tearing down the old
  // media source. This avoids a false paused icon on the next video's player.
  // Suppress only the pause event caused by this teardown, not every pause
  // for an arbitrary time window. If the browser emits no pause event, expire
  // the one-shot before it can affect a later user action.
  _P.activeSrc='';
  _playbackActivity(false);
  // Never release the video an external player (VLC/MPC) is playing.
  if(_P.cid!=null&&_P.mid!=null&&_npMode!=='external'){
    const leaving=vkey(_P.cid,_P.mid);
    if(leaving&&leaving!==nextKey)pReleaseServer(_P.cid,_P.mid,nextKey?'next_video':'back');
  }

  
  _P.openTok++;
  // Clear multi-part state
  _P.parts=null;_P.partIndex=0;_P.partOffsets=null;_P.partDurations=null;_P.partTotalDur=0;_P.logicalCid=null;_P.logicalMid=null;
  const e=pv();_P.seq++;_P.activeSrc='';clearTimeout(_P.startupStallTimer);
  _P.firstPlayingAt=0;_P.firstFrameAt=0;_P.playOpenAt=0;_PStartupStillPending=false;pSetState('IDLE');
  try{e.pause();e.removeAttribute('src');e.load();e.currentTime=0;}catch(x){}
  if(!nextKey){e.removeAttribute('poster');delete e.dataset.axPosterKey;}
  stopCachePoll();_P.cacheStatus='idle';_P.pendingCacheSeek=null;_P.cid=null;_P.mid=null;_P.mediaKey='';_P.preloadedKey='';_P.mode=null;_P.off=0;_P.dur=0;_P.info=null;_P.dragging=false;_P.playbackGeneration=0;_P.playbackToken='';
  $('pplay').style.width='0%';$('pknob').style.left='0%';$('pbuf').style.width='0%';$('ptime').textContent='0:00 / –:––';$('ptcur').textContent='0:00';$('ptdur').textContent='–:––';
  $('dtop').classList.add('paused');pSetPlayPauseIcon(true);$('dtop').classList.remove('loading');
  if(!nextKey)_snapshotGridModule?.playbackStopped?.();
}
function pSaveResume(){
  const now=Date.now();if(now-_P.lastSave<5000||!_P.cid)return;_P.lastSave=now;
  const c=pCur(),d=pDur();
  try{const k=vkey(_P.cid,_P.mid);if(localStorage.getItem('ta_last_played_video')===k&&c>20&&d&&c<d-25)localStorage.setItem('ta_resume:'+k,String(Math.floor(c)));}catch(x){}
  if(now-_P.lastServerSave>=5000){_P.lastServerSave=now;const delta=Math.max(0,(c||0)-(_P.lastServerPosition||0));_P.lastServerPosition=c||0;api('/api/play/progress',{method:'POST',body:JSON.stringify({channel_id:_P.cid,message_id:_P.mid,position:c,duration:d,watch_delta:delta<=15?delta:0})}).catch(()=>{});}
}
function pClearResume(){try{localStorage.removeItem('ta_resume:'+vkey(_P.cid,_P.mid));}catch(x){}}


async function deleteLibraryVideo(channelId,messageId,title){
  const cid=String(channelId),mid=Number(messageId),name=String(title||`video ${mid}`);
  const video=findVideo(mid,cid);
  const videoName=String(title||video?.api_title||video?.title||name);
  const confirmed=await axConfirm('This permanently deletes the message from its Telegram channel and removes it from your Archive X Library. An explicitly linked Telegram thumbnail is deleted too only if it is verified as an image used exclusively by this video; shared or unverified thumbnails are kept. This cannot be undone.',{
    title:'Delete this video from Telegram?',
    okLabel:'Delete video',
    confirmLabel:'Delete video',
    danger:true,
    media:{
      title:videoName,
      meta:`${chanTitle(cid)} · Telegram message ${mid}`,
      src:video?thumbUrl(video):''
    }
  });
  if(!confirmed)return;
  try{
    const r=await api('/api/library/delete',{method:'POST',body:JSON.stringify({channel_id:Number(cid),message_id:mid,confirm_delete:true})});
    if(!r?.ok)throw new Error(r?.detail||'Video was not deleted');
    const old=String(cid);if(Array.isArray(_chanV[old]))_chanV[old]=_chanV[old].filter(v=>Number(v.id)!==mid);
    if(_dv&&String(_dv._cid)===cid&&Number(_dv.id)===mid)closeDBtn(false);
    _libStale=true;rebuildV({keepPage:true});await loadChannels();
    if(_sel.size)await loadLib([..._sel],true);
    const resultMessages=['Video deleted from Telegram and removed from the Library.'];
    if(r.thumbnail_deleted)resultMessages.push('Its associated thumbnail was deleted too.');
    if(Array.isArray(r.warnings))resultMessages.push(...r.warnings);
    toast(resultMessages.join(' '),10000);
  }catch(e){toast(e?.message||'Could not delete video from Telegram.',8000);}
}
let _videoMoveRequest=null;
function _videoMoveUniqueChannels(list){
  const seen=new Set();
  return (Array.isArray(list)?list:[]).filter(c=>{
    if(!c||c.telegram_id==null||String(c.telegram_id).trim()==='')return false;
    const key=String(c.telegram_id);if(seen.has(key))return false;seen.add(key);return true;
  });
}
function _renderVideoMoveChannels(req,channels,warning=''){
  const select=$('video-move-destination'),status=$('video-move-channel-status'),retry=$('video-move-channel-retry'),button=$('video-move-confirm');
  if(!select||!req)return;
  const sourceId=String(req.channel_id);
  const loadedChannels=_videoMoveUniqueChannels(channels);
  const options=loadedChannels.filter(c=>String(c.telegram_id)!==sourceId);
  select.innerHTML='<option value="">Choose channel…</option>'+options.map(c=>`<option value="${esc(c.telegram_id)}">${esc(channelDisplayName(c.telegram_id,c.title))}</option>`).join('');
  select.disabled=options.length===0;
  if(button)button.disabled=options.length===0;
  if(status){
    status.textContent=options.length?(warning||`${loadedChannels.length} channel${loadedChannels.length===1?'':'s'} loaded.`):(warning||'No destination channels are available. Refresh the Telegram channel list and try again.');
    status.dataset.state=options.length?'ready':'warning';
    status.style.removeProperty('color');
  }
  if(retry)retry.style.display=options.length?'none':'inline-flex';
  return options.length;
}
async function _loadVideoMoveChannels(req){
  const select=$('video-move-destination'),status=$('video-move-channel-status'),button=$('video-move-confirm');
  if(!select||!req)return;
  select.disabled=true;select.innerHTML='<option value="">Loading Telegram channels…</option>';
  if(button)button.disabled=true;
  if(status){status.textContent='Loading channels available to your Telegram account…';status.dataset.state='loading';}
  // Destination channels must come from the full Telegram channel inventory,
  // not /api/channels, which only lists channels with published Library media.
  const result=await api('/api/source-channels',{cache:'no-store'});
  if(_videoMoveRequest!==req)return;
  if(Array.isArray(result)){
    _sourceChans=_videoMoveUniqueChannels(result);
    const count=_renderVideoMoveChannels(req,_sourceChans);
    if(count)return;
    // A published channel can still be usable as a destination if the source
    // inventory is momentarily stale; keep this as a clearly secondary fallback.
    const fallback=_videoMoveUniqueChannels(_chans);
    if(fallback.length){
      const fallbackCount=_renderVideoMoveChannels(req,fallback,'Using published Library channels because the Telegram channel inventory is empty.');
      if(fallbackCount)return;
    }
    _renderVideoMoveChannels(req,[], 'Telegram returned no usable destination channels. Refresh the channel list and retry.');
    return;
  }
  const cached=_videoMoveUniqueChannels(_sourceChans);
  if(cached.length&&_renderVideoMoveChannels(req,cached,`Could not refresh Telegram channels: ${result?.detail||'request failed'}. Showing the last loaded list.`))return;
  const fallback=_videoMoveUniqueChannels(_chans);
  _renderVideoMoveChannels(req,fallback,`Could not load Telegram channels: ${result?.detail||'request failed'}.`);
}
async function retryVideoMoveChannels(){
  const req=_videoMoveRequest;if(!req)return;
  const status=$('video-move-channel-status'),retry=$('video-move-channel-retry');
  if(retry)retry.disabled=true;
  if(status){status.textContent='Refreshing the Telegram channel list…';status.dataset.state='loading';}
  try{
    const refresh=await api('/api/telegram/refresh',{method:'POST',body:JSON.stringify({})});
    if(!refresh||refresh._ok===false)throw new Error(refresh?.detail||'Telegram channel refresh failed');
    const result=await api('/api/source-channels',{cache:'no-store'});
    if(_videoMoveRequest!==req)return;
    if(!Array.isArray(result))throw new Error(result?.detail||'Could not reload Telegram channels');
    _sourceChans=_videoMoveUniqueChannels(result);
    const count=_renderVideoMoveChannels(req,_sourceChans);
    if(!count){
      const fallbackCount=_renderVideoMoveChannels(req,_videoMoveUniqueChannels(_chans),'Telegram inventory has no other channel; showing published Library channels instead.');
      if(!fallbackCount)_renderVideoMoveChannels(req,[],'No other destination channel is available besides the current source.');
    }
  }catch(e){
    if(_videoMoveRequest===req){
      const current=_videoMoveUniqueChannels(_sourceChans);
      _renderVideoMoveChannels(req,current,`Refresh failed: ${e?.message||'unknown error'}.`);
    }
  }finally{if(retry)retry.disabled=false;}
}
async function openVideoMoveDialog(channelId,messageId,title){
  const dialog=$('video-move-dialog'),select=$('video-move-destination');if(!dialog||!select)return;
  const req={channel_id:String(channelId),message_id:Number(messageId),title:String(title||'video')};
  _videoMoveRequest=req;
  const video=findVideo(req.message_id,req.channel_id)||{};
  const sourceChannel=[..._videoMoveUniqueChannels(_sourceChans),..._videoMoveUniqueChannels(_chans)].find(c=>String(c.telegram_id)===req.channel_id);
  const sourceTitle=channelDisplayName(req.channel_id,video.channel_title||sourceChannel?.title);
  const size=Number(video.size||video.file_size||video.size_bytes||0);
  const videoTitle=$('video-move-video-title'),videoMeta=$('video-move-video-meta');
  if(videoTitle)videoTitle.textContent=req.title;
  if(videoMeta)videoMeta.textContent=[sourceTitle,size>0?fmt(size):''].filter(Boolean).join(' · ');
  const art=$('video-move-art'),thumb=$('video-move-thumb');
  if(art&&thumb){
    art.classList.remove('is-unavailable');
    const thumbRecord={...video,_cid:req.channel_id,id:req.message_id};
    const thumbSrc=thumbUrl(thumbRecord);
    thumb.onload=()=>{if(_videoMoveRequest===req&&thumb.getAttribute('src')===thumbSrc)art.classList.remove('is-unavailable');};
    thumb.onerror=()=>{if(_videoMoveRequest===req&&thumb.getAttribute('src')===thumbSrc)art.classList.add('is-unavailable');};
    thumb.src=thumbSrc;
  }
  const retry=$('video-move-channel-retry');if(retry){retry.style.display='none';retry.disabled=false;}
  const status=$('video-move-channel-status');if(status){status.textContent='Loading Telegram channels…';status.dataset.state='loading';}
  dialog.classList.add('open');dialog.setAttribute('aria-hidden','false');
  await _loadVideoMoveChannels(req);
  if(_videoMoveRequest===req&&!select.disabled)select.focus();
}
function closeVideoMoveDialog(){const dialog=$('video-move-dialog');if(dialog){dialog.classList.remove('open');dialog.setAttribute('aria-hidden','true');} _videoMoveRequest=null;}
async function confirmVideoMoveDelete(){
  const req=_videoMoveRequest,select=$('video-move-destination'),button=$('video-move-confirm');
  if(!req||!select?.value){toast('Choose a destination channel first.');return;}
  const dest=[..._videoMoveUniqueChannels(_sourceChans),..._videoMoveUniqueChannels(_chans)].find(c=>String(c.telegram_id)===String(select.value));
  if(!dest){toast('Destination channel is no longer available. Refresh the channel list.');return;}
  const confirmed=await axConfirm(`Move “${req.title}” to “${channelDisplayName(dest.telegram_id,dest.title)}” and delete the original Telegram message after verification? This cannot be undone.`,{title:'Move video and delete source',okLabel:'Move and delete source',confirmLabel:'Move and delete source',danger:true});
  if(!confirmed)return;
  if(button){button.disabled=true;button.textContent='Verifying destination…';}
  try{
    const r=await api('/api/library/move-delete',{method:'POST',body:JSON.stringify({channel_id:Number(req.channel_id),message_id:req.message_id,destination_channel:String(dest.telegram_id),confirm_delete:true})});
    if(!r||r._ok===false||!r.ok)throw new Error(r?.detail||'Move was not completed');
    closeVideoMoveDialog();
    const old=String(req.channel_id);if(Array.isArray(_chanV[old]))_chanV[old]=_chanV[old].filter(v=>Number(v.id)!==req.message_id);
    _libStale=true;rebuildV({keepPage:true});await loadChannels();
    if(_sel.size)await loadLib([..._sel],true);
    const moveWarnings=[r.source_warning,r.companion_warning].filter(Boolean);
    toast(r.moved?.source_deleted===false ? 'Destination verified. Source deletion is pending and will be retried.'+(moveWarnings.length?' '+moveWarnings.join(' '):'') : moveWarnings.length ? 'Move verified with a warning: '+moveWarnings.join(' ') : 'Move verified. The source message was deleted.',7000);
  }catch(e){toast(e?.message||'Move failed; check the transfer log. ',7000);}
  finally{if(button){button.textContent='Move and delete source';button.disabled=!select||select.disabled||select.options.length<2;}}
}

// ── SECTION: detail panel ──
function toggleDetailTags(){
  const box=$('dtags'),head=$('dtag-head');
  if(!box||!head)return;
  const expanded=box.dataset.expanded==='1';
  box.dataset.expanded=expanded?'0':'1';
  head.classList.toggle('expanded',!expanded);
  box.classList.toggle('tags-collapsed',expanded);
  head.setAttribute('aria-expanded',String(!expanded));
}
function toggleDetailMarkers(){
  const box=$('v55-markers'),head=$('dmarker-head');
  if(!box||!head)return;
  const expanded=box.dataset.expanded!=='0';
  box.dataset.expanded=expanded?'0':'1';
  head.classList.toggle('expanded',!expanded);
  box.classList.toggle('markers-collapsed',expanded);
  head.setAttribute('aria-expanded',String(!expanded));
}

function openCurrentVideoQuickEdit(){
  if(!_dv||!_dv.id||_dv._cid==null)return;
  openVideoQuickEdit(Number(_dv.id),String(_dv._cid));
}

// Performer / studio pictures in the "Performers & Studio" block under the video.
// Performers already carry their id and image; studios are looked up once by name.
const _axEntityLookup=new Map();
function _axEntityAvatarHTML(kind,name,meta){
  const cls=kind==='studio'?'ax2-video-entity-avatar studio':'ax2-video-entity-avatar';
  if(kind==='performer'&&meta&&Number(meta.id)){
    const local=`/api/performers/${Number(meta.id)}/image?v=${encodeURIComponent(meta.image||'')}`;
    return `<span class="${cls}" aria-hidden="true"><img src="${esc(local)}" alt="" loading="lazy" decoding="async" data-entity-name="${esc(name)}" data-provider-src="${esc(safeHttpUrl(meta.image)||'')}" data-local-src="${esc(local)}" onload="entityImageLoaded(this)" onerror="performerImageFallback(this,'')"></span>`;
  }
  return `<span class="${cls}" aria-hidden="true" data-axent="${kind}" data-axname="${esc(name)}"><svg class="ax-icon"><use href="#ax-${kind==='studio'?'studio':'user'}"></use></svg></span>`;
}
function _axLookupEntity(kind,name){
  const key=kind+':'+String(name).trim().toLowerCase();
  if(_axEntityLookup.has(key))return _axEntityLookup.get(key);
  const p=(async()=>{
    try{
      const rows=await api((kind==='studio'?'/api/studios?q=':'/api/performers?limit=20&q=')+encodeURIComponent(name));
      if(!Array.isArray(rows))return null;
      const n=String(name).trim().toLowerCase();
      return rows.find(r=>String(r.name||'').trim().toLowerCase()===n)||null;
    }catch(e){return null;}
  })();
  _axEntityLookup.set(key,p);
  p.then(row=>{if(!row&&_axEntityLookup.get(key)===p)_axEntityLookup.delete(key);});
  return p;
}
function _axResolveEntityAvatars(root){
  if(!root)return;
  root.querySelectorAll('.ax2-video-entity-avatar[data-axent]').forEach(async av=>{
    const kind=av.dataset.axent,name=av.dataset.axname||'';
    const row=await _axLookupEntity(kind,name);
    if(!av.isConnected)return;
    const img=document.createElement('img');
    img.alt='';img.decoding='async';img.dataset.entityName=name;
    av.textContent='';av.appendChild(img);
    if(row&&Number(row.id)){
      const local=kind==='studio'?studioImageApiUrl(row):`/api/performers/${Number(row.id)}/image?v=${encodeURIComponent(row.image||'')}`;
      img.dataset.providerSrc=safeHttpUrl(row.image)||'';img.dataset.localSrc=local;
      img.onload=()=>entityImageLoaded(img);
      img.onerror=()=>(kind==='studio'?studioImageFallback:performerImageFallback)(img,'');
      img.src=local;
    }else entityImageFallback(img,'');
  });
}
function fillDetail(v){
  const cid=v._cid;
  const telegramName=v.telegram_filename||v.name||('video '+v.id);
  const apiTitle=(v.api_title||v.title||'').trim();
  $('dtitle').textContent=apiTitle||telegramName;
  $('dbar-t').textContent=apiTitle||telegramName;
  $('dfname').textContent=`Telegram filename: ${telegramName}`;
  const h=v.height||0;
  // Header metadata is intentionally limited to technical facts. The studio
  // association is displayed only in the Performers & Studio block below.
  $('dmeta').innerHTML=
    (h?`<span class="badge" style="background:var(--green2);color:var(--green)">${axResLabel(v.width,h)}</span>`:'')+
    (v.duration?`<span class="badge" style="background:var(--bg3);color:var(--text2)">${fdur(v.duration)}</span>`:'')+
    (v.size?`<span class="badge" style="background:var(--bg3);color:var(--text2)">${fmt(v.size)}</span>`:'')+
    (v.codec?`<span class="badge" title="Video codec">${esc(v.codec)}</span>`:'')+
    (v.release_date?`<span class="badge" style="background:var(--bg3);color:var(--text2)">${esc(v.release_date)}</span>`:'')+
    (()=>{const pi=(_P&&String(_P.cid)===String(cid)&&Number(_P.mid)===Number(v.id))?_P.info:null;const label=String(pi?.streamability_label||'');if(!label)return '';const ok=label==='Streamable',unknown=label==='Streamability unknown';const bg=ok?'var(--green2)':unknown?'var(--bg3)':'var(--amber2)';const fg=ok?'var(--green)':unknown?'var(--text2)':'var(--amber)';return `<span class="badge" title="${esc(label)}" style="background:${bg};color:${fg}">${esc(label)}</span>`;})();
  const relatedPerformers=Array.isArray(v.performers)?v.performers.filter(Boolean):[];
  const relatedStudio=String(v.site_name||v.studio||'').trim();
  const metaByName=new Map((Array.isArray(v.performer_meta)?v.performer_meta:[]).map(m=>[String(m.name||'').trim().toLowerCase(),m]));
  const performerMarkup=relatedPerformers.length?relatedPerformers.map(p=>`<button type="button" class="pill video-related-pill ax2-video-entity-chip" onclick="closeDBtn(true);openPerformerByName(${jsa(p)})" title="Open performer ${esc(p)}">${_axEntityAvatarHTML('performer',p,metaByName.get(String(p).trim().toLowerCase()))}<span class="ax2-video-entity-copy"><span class="ax2-video-entity-name">${esc(p)}</span><span class="ax2-video-entity-role">Performer</span></span></button>`).join(''):'<span class="muted video-related-empty">No performers linked</span>';
  const studioMarkup=relatedStudio?`<button type="button" class="pill video-related-pill studio-related-pill ax2-video-entity-chip" onclick="closeDBtn(true);openStudioByName(${jsa(relatedStudio)})" title="Open studio ${esc(relatedStudio)}">${_axEntityAvatarHTML('studio',relatedStudio,null)}<span class="ax2-video-entity-copy"><span class="ax2-video-entity-name">${esc(relatedStudio)}</span><span class="ax2-video-entity-role video-related-studio-label">Studio</span></span></button>`:'<span class="muted video-related-empty">No studio linked</span>';
  $('dperfs').innerHTML=`<div class="video-related-entities">`+
    `<div class="video-related-entity-group"><div class="video-related-entity-label">Performers</div><div class="video-related-entity-values">${performerMarkup}</div></div>`+
    `<div class="video-related-entity-group"><div class="video-related-entity-label">Studio</div><div class="video-related-entity-values">${studioMarkup}</div></div>`+
    `</div>`;
  _axResolveEntityAvatars($('dperfs'));
  _renderSmartDetailTags(v);
  const sourceUrl=safeHttpUrl(v.provider_url||'');
  const sourceSite=v.site_name||'';
  const sourceEl=$('dsource');
  if(sourceEl){
    sourceEl.innerHTML=sourceUrl?`<span class="video-source-chip"><span>${esc(sourceSite||'Source')}</span><a href="${esc(sourceUrl)}" target="_blank" rel="noopener noreferrer">Open Source Page</a></span>`:(sourceSite?`<span class="video-source-chip"><span>${esc(sourceSite)}</span><span class="muted">Source page unavailable</span></span>`:'<span class="video-source-chip"><span class="muted">Source page unavailable</span></span>');
  }
  const remote=_isRemote();
  if(remote)primeDeviceLink(cid,v.id);
  $('dact').innerHTML=`
    <button class="btn pri detail-action" onclick="sendTG(${Number(v.id)},${jsa(cid)})" title="Send to Telegram" aria-label="Send to Telegram">${ICO.tg}Send to Telegram</button>
    ${remote?`<button class="btn detail-action" onclick="playHere(${Number(v.id)},${jsa(cid)})"><svg class="ax-icon"><use href="#ax-play"></use></svg> Open in a player on this device</button>`:''}
    <button class="btn detail-action" onclick="${remote?'openVLCDevice':'sendPD'}(${remote?'':"'vlc',"}${Number(v.id)},${jsa(cid)})" title="Open in VLC" aria-label="Open in VLC">${ICO.vlc}VLC${remote?' (on this device)':''}</button>
    ${_isIOS()?'':`<button class="btn detail-action" onclick="sendPD('mpc',${Number(v.id)},${jsa(cid)})" title="Open in MPC-HC" aria-label="Open in MPC-HC">${ICO.mpc}MPC-HC${remote?' (on the PC)':''}</button>`}
    <button class="btn detail-action" onclick="identV(${Number(v.id)},${jsa(cid)})">Identify (API lookup)</button>
    <button class="btn detail-action" onclick="openVideoMapping(${Number(v.id)},${jsa(cid)})">Mapping / Report mapping</button>
    <button id="dfav" class="btn detail-action${v._fav?' on':''}" onclick="togFavD()">${v._fav?'<svg class="ax-icon" aria-hidden="true"><use href="#ax-star"></use></svg> Favourite':'<svg class="ax-icon" aria-hidden="true"><use href="#ax-star"></use></svg> Favourite'}</button>`;
  renderVideoMetaGrid(v);
  const df=$('dheadfav');if(df)df.innerHTML=v._fav?'<svg class="ax-icon" aria-hidden="true"><use href="#ax-star"></use></svg> Favourite':'<svg class="ax-icon" aria-hidden="true"><use href="#ax-star"></use></svg> Add to Favourites';
  const sc=$('suggestion-count');if(sc)sc.textContent='(0)';
}
function renderUpNext(){
  const list=_pl.length?_pl:contextList();
  let idx=list.findIndex(x=>_dv&&String(x.id)===String(_dv.id)&&String(x._cid)===String(_dv._cid));
  const out=[];
  // Keep three cards visible at once, but provide a longer horizontal track so
  // the rail arrows can navigate additional items instead of being disabled
  // merely because the initial render contained exactly three videos.
  const maxItems=Math.min(12,Math.max(0,list.length-(idx>=0?1:0)));
  const seen=new Set();
  for(let k=1;k<=maxItems;k++){
    const n=list[(Math.max(idx,0)+k)%list.length];
    if(!n||(_dv&&String(n.id)===String(_dv.id)&&String(n._cid)===String(_dv._cid)))continue;
    const key=`${n._cid}:${n.id}`;
    if(seen.has(key))continue;
    seen.add(key);out.push(n);
  }
  $('dnext').innerHTML=out.length?out.map(n=>`<div class="unx" tabindex="0" data-ax-keyboard-activate="true" role="button" aria-label="${esc('Play next: '+(n.api_title||n.title||n.name||('video '+n.id)))}" data-cid="${esc(n._cid)}" data-mid="${Number(n.id)}" data-duration="${Math.max(0,Number(n.duration)||0)}" onclick="upNext(${Number(n.id)},${jsa(n._cid)})"><div class="unx-art"><img data-src="${thumbUrl(n)}" alt=""><canvas class="vc-preview-canvas preview-a" aria-hidden="true"></canvas><canvas class="vc-preview-canvas preview-b" aria-hidden="true"></canvas>${n.duration?`<span class="ax2-video-duration-badge" aria-hidden="true">${fdur(n.duration)}</span>`:''}</div><div style="min-width:0"><div class="t">${esc((n.api_title||n.title||n.name||'video '+n.id))}</div><div class="muted" style="font-size:11px;margin-top:4px">${esc((n.performers||[]).slice(0,2).join(', '))}${n.duration?' · '+fdur(n.duration):''}</div></div></div>`).join(''):'<div class="muted">Nothing queued.</div>';
  hydrateThumbs($('dnext'));
  bindCardPreviews($('dnext'));
}
function upNext(id,cid){
  const list=_pl.length?_pl:contextList();
  const i=list.findIndex(x=>String(x.id)===String(id)&&String(x._cid)===String(cid));
  if(!_pl.length&&i>=0)_pl=[...list];
  if(i>=0)_plIdx=i;
  openD(id,cid,true);
  playB(id,cid,null,true);
}
async function nextVSmart(){
  const mode=String(localStorage.getItem('ta_autonext_mode')||'context');
  if(mode==='context'||!_dv){nextV();return;}
  try{const lookup=await api(`/api/videos/lookup?channel_id=${encodeURIComponent(_dv._cid)}&message_id=${Number(_dv.id)}`);if(!lookup?.media_id){nextV();return;}let items=[];
    if(mode==='recommended'){const r=await api(`/api/media/${Number(lookup.media_id)}/recommendations`);items=r?.items||[];}
    else {const name=(mode==='performer'?(_dv.performers||[])[0]:(mode==='studio'?(_dv.studio||_dv.site_name||''):''));if(name){const r=await api('/api/library?per=12&'+(mode==='performer'?'performer':'studio')+'='+encodeURIComponent(name));items=r?.items||[];}}
    const candidate=items.find(x=>String(x.id)!==String(_dv.id)||String(x._cid)!==String(_dv._cid));
    if(candidate){openD(candidate.id,candidate._cid,true);playB(candidate.id,candidate._cid,candidate.title||candidate.name||'',true);return;}
  }catch(e){}
  nextV();
}
function ensureUniversalBackButton(){
  const bar=$('dtopbar');
  if(!bar)return;
  let btn=bar.querySelector('.universal-back-btn');
  if(!btn){
    btn=document.createElement('button');
    btn.className='universal-back-btn';
    btn.type='button';
    btn.textContent='←';
    btn.setAttribute('aria-label','Go back');
    btn.title='Go back';
    btn.addEventListener('click',universalBack);
    bar.prepend(btn);
  }
  btn.style.display='inline-flex';
  btn.disabled=false;
}



function openVideoTelegram(){
  if(!_dv)return;
  // Use Archive X's canonical Telegram message link so public channels open in
  // the browser and private channels use Telegram's native private-post URI.
  const url=tgLink(_dv._cid,_dv.id);
  window.open(url,'_blank','noopener,noreferrer');
}



function renderVideoMetaGrid(v){
  const savedInfo={vcodec:v.codec||'',acodec:v.audio_codec||'',width:Number(v.width||0),height:Number(v.height||0),duration:Number(v.duration||0)};
  const i=_infoCache.get(vkey(v._cid,v.id))||savedInfo;
  const ch=_chans.find(c=>String(c.telegram_id)===String(v._cid));
  const rows=[
    ['Channel',channelDisplayName(v._cid,v.channel_title||ch?.title)],
    ['Message ID',String(v.id)],
    ['Filename',v.telegram_filename||v.name||'—'],
    ['File size',v.size?fmt(v.size):'—'],
    ['Duration',v.duration?fdur(v.duration):'—'],
    ['Resolution',(i.width||v.width)&&(i.height||v.height)?`${i.width||v.width} × ${i.height||v.height}`:'—'],
    ['Video codec',i.vcodec||v.codec||'—'],
    ['Audio codec',i.acodec||v.audio_codec||'—'],
    ['Release date',v.release_date||'—'],
    ['Added',String(v.date||v.upload_date||'—').slice(0,19)],
    ['Studio',v.site_name||v.studio||'—'],
    ['Performers',(v.performers||[]).join(', ')||'—'],
    ['Source',v.provider_url||'Telegram / local catalogue'],
  ];
  const box=$('video-meta-grid');if(!box)return;
  box.innerHTML=rows.map(([k,val])=>`<span class="k">${esc(k)}</span><span class="v">${esc(val)}</span>`).join('');
}

function openD(msgId,cid,noAuto,sourceCard=null){
  ensureUniversalBackButton();
  _P.pendingStartAt=null;
  const v=findVideo(msgId,cid);
  if(!v)return;
  // Do not promote a single card image into a full-size player poster. The
  // snapshot grid itself is warmed below and paints its tiles as they arrive.
  const player=pv();player?.removeAttribute('poster');if(player?.dataset)delete player.dataset.axPosterKey;
  // A saved-position prompt must show its live frame before starting any
  // detail-page warmup. Other videos keep the normal click-to-open preload.
  const deferWarmupForResume=_axResumePositionFor(cid,msgId,v,false)!==null;
  const nextKey=vkey(cid,msgId), currentKey=(_P.cid!=null&&_P.mid!=null)?vkey(_P.cid,_P.mid):'';
  if(currentKey!==nextKey || _npMode==='browser'){
    // Invalidate the old player/preview generation before the new video identity
    // is installed. This prevents the previous video's range requests and exact
    // preview callbacks from painting into the new page.
    pClearPreviewSheet();
    pStop(nextKey);
    _npMode=null;_aChan=null;_aMsg=null;
  }
  _dv=v;
  axPublishPlayerDimensions(v,cid,msgId,_infoCache.get(nextKey)||null);
  const wasOpen=$('do').classList.contains('open');
  fillDetail(v);
  const here=String(_P.cid)===String(cid)&&String(_P.mid)===String(msgId)&&pv().getAttribute('src');
  setGlobalSearchContext('library');
  $('do').classList.add('open');document.body.classList.add('noscroll');
  if(!wasOpen&&window.axPlayerFocusOpened)window.axPlayerFocusOpened();
  if(window.syncPlayerVisualState)window.syncPlayerVisualState();
  // A different video can be opened from Up Next while this page is already
  // scrolled. Always return the new video page to its top; only add history
  // when opening the player from the app for the first time.
  $('do').scrollTop=0;
  if(!wasOpen&&!_watchPushed){_watchPushed=true;history.pushState({...(history.state||{view:'library'}),watch:vkey(cid,msgId)},'');}
  renderUpNext();
  loadDetailMarkers();
  if(!here){
    $('dtop').classList.add('paused');pSetPlayPauseIcon(true);_P.cid=String(cid);_P.mid=Number(msgId);_P.mediaKey=vkey(cid,msgId);_P.dur=Number(v.duration||0);
     if(_playerABRepeat.snapshot().mediaKey!==_P.mediaKey)pABReset(_P.mediaKey);
    _P.info=_infoCache.get(nextKey)||null;
     _PStartupStillPending=false;
    pSnapshotGridHide();
      // Resume candidates do not attach a media source until the user confirms.
      // Load their snapshot grid first, then warm the saved-position byte window
      // while the user reviews the detail page, not after Resume.
     if(deferWarmupForResume){
       const resumeAt=_axResumePositionFor(cid,msgId,v,false);
       pLoadPreplaySnapshotGrid(cid,msgId,false,()=>{
         if(resumeAt!==null&&_P.mediaKey===nextKey&&_dv&&String(_dv._cid)===String(cid)&&Number(_dv.id)===Number(msgId)&&!pv().getAttribute('src'))_axPreloadInitialAndResumeWindow(cid,msgId,resumeAt);
       }).catch(()=>{});
      }else{
        // Start the local snapshot request on the card click, before waiting
        // for any previous source release. The 10-second byte warmup still
        // starts only after the new foreground source is attached.
        pLoadPreplaySnapshotGrid(cid,msgId,false).catch(()=>{});
     }
    const prepareToken=_P.openTok;
    const prepareSource=()=>{
       if(deferWarmupForResume||_P.openTok!==prepareToken||_P.mediaKey!==nextKey||pv().getAttribute('src'))return;
      _pStart(pInitialMode(_P.info),0,false,true);
       // Let the foreground media element claim the first stream request before
       // asking the background cache worker to warm a short prefix. The source
       // is already attached with preload="auto"; 10 seconds avoids unnecessary
       // cache work when a viewer only opens details and then leaves.
       if(!deferWarmupForResume)_axPreloadVideoBytes(cid,msgId,0,10);
    };
    // Attach immediately. Server-side playback claims make a late release from
    // the previous view harmless without delaying this source.
    prepareSource();
  }else{
    if(!_P.cid||String(_P.cid)!==String(cid)||Number(_P.mid)!==Number(msgId)){pStop();_P.cid=String(cid);_P.mid=Number(msgId);_P.mediaKey=vkey(cid,msgId);_P.dur=Number(v.duration||0);}
    $('dtop').classList.add('paused');pSetPlayPauseIcon(true);
  }
  const chapterKey=vkey(cid,msgId),chapterCache=_playerSceneChapterCache.get(chapterKey);
  if(chapterCache)renderPlayerSceneChapterTrack(chapterKey,chapterCache.mediaId,chapterCache.markers);else clearPlayerSceneChapterTrack();
}
async function togFavD(){
  if(!_dv)return;
  try{let mediaId=Number(_dv._media_id||0);if(!mediaId){const l=await api(`/api/videos/lookup?channel_id=${encodeURIComponent(_dv._cid)}&message_id=${Number(_dv.id)}`);mediaId=Number(l?.media_id||0);_dv._media_id=mediaId;}if(!mediaId)throw new Error('media not catalogued');const r=await api(`/api/library/${mediaId}/favorite`,{method:'POST',body:'{}'});if(!r?.ok)throw new Error(r?.detail||'failed');_dv._fav=!!r.favorite;const b=$('dfav');if(b){b.classList.toggle('on',_dv._fav);b.innerHTML=_dv._fav?axIcon('star')+' Favourite':axIcon('star')+' Favourite';}}catch(e){toast('Could not update favourite.');}
}
function closeDBtn(fromHistory, reason='close'){
  const wasPlayerOpen=!!$('do')?.classList.contains('open');
  _axResumePending=null;const resumeDlg=$('ax-resume-dialog');if(resumeDlg){resumeDlg.hidden=true;resumeDlg.setAttribute('aria-hidden','true');}
  // Closing the player page also invalidates async startup/preview work and
  // releases this video's scoped range/preload jobs. Do not stop the shared
  // PlaybackEngine globally: VLC/MPC-HC and its local feed must remain independent.
  pClearPreviewSheet();
  _stopAllCardPreviews();
  pSnapshotGridHide();
  if(wasPlayerOpen&&_npMode==='external'&&_externalPid){
    const pid=Number(_externalPid);
    stopExternalStatusPoll();_externalPid=null;_npMode=null;
    // Closing an Archive X-launched external-player page still ends that
    // explicitly launched external-player job, as before.
    api(`/api/play/external/stop/${encodeURIComponent(pid)}`,{method:'POST'}).catch(e=>axDiagRecord('playback.external.stop.error',{pid,message:String(e?.message||e)},'WARN'));
    markPlaying();
  }else if(wasPlayerOpen&&_npMode!=='external'){
    pSendTelemetry(reason||'close');
    // pStop increments the player generation, stops cache polling, detaches
    // the <video> source and asks the backend to release this video's range
    // tasks/cache. This also handles a detail page opened before Play was hit.
    pStop();_npMode=null;_aChan=null;_aMsg=null;markPlaying();
  }else if(_npMode==='browser'){
    pSendTelemetry(reason||'close');pStop();_npMode=null;_aChan=null;_aMsg=null;markPlaying();
  }
  if(document.fullscreenElement)document.exitFullscreen().catch(()=>{});
  else if(document.webkitFullscreenElement){try{document.webkitExitFullscreen&&document.webkitExitFullscreen();}catch(e){}}
  $('do').classList.remove('open');document.body.classList.remove('noscroll');_dv=null;
  if(window.axPlayerFocusClosed)window.axPlayerFocusClosed();
  if(window.setArchiveXPlayerDimensionHint)window.setArchiveXPlayerDimensionHint(null);else window.__axPlayerDimensionHint=null;
  if(_watchPushed){_watchPushed=false;if(!fromHistory)history.back();}
  // V29.4.131: changes that arrived while the player was open.
  if(_libStale)setTimeout(()=>{if(_viewOn('vl'))_softRefreshLibrary().catch(()=>{});},350);
}

// Universal back control used by the fixed Video-page navigation bar.
// It first unwinds the in-app watch state so a video opened from Performers,
// Studios, Search, or Library returns to the exact previous application view.
// When no watch history exists (for example a deep link), fall back to the
// application's Library rather than navigating outside the app.
function universalBack(){
  const wasWatch=_watchPushed;
  closeDBtn(true,'back');
  if(wasWatch){
    _watchPushed=false;
    if(history.length>1){history.back();return;}
  }
  const st=history.state||{};
  if(st.view&&st.view!=='library'&&history.length>1){history.back();return;}
  if(st.view==='library')return;
  sv('library');
}
function stopCachePoll(){clearTimeout(_P.cachePoll);_P.cachePoll=0;}


let _axResumePending=null;
function _axResumePositionFor(cid,msgId,video,fromQueue=false){
  if(fromQueue||!_resumePromptEnabled)return null;
  try{
    const key=vkey(cid,msgId);
    if(localStorage.getItem('ta_last_played_video')!==key)return null;
    const stored=Number(localStorage.getItem('ta_resume:'+key)||0);
    const playerDuration=_P.mediaKey===key?_P.dur:0;
    const duration=Number(video?.duration||playerDuration||0);
    return Number.isFinite(stored)&&stored>=30&&(!duration||stored<duration-20)?stored:null;
  }catch(e){return null;}
}
function _showAxResumePrompt(msgId,cid,name,fromQueue,stored,label,key){
  _axResumePending={msgId,cid,name,fromQueue,stored,key};
  const dlg=$('ax-resume-dialog');if(!dlg)return false;
  const video=findVideo(msgId,cid)||{id:msgId,_cid:cid};
  const duration=Math.max(0,Number(video.duration||0));
  $('ax-resume-title').textContent=String(name||'Continue video');
  $('ax-resume-position').textContent=`Resume from ${label}?`;
  const preview=$('ax-resume-preview'),status=$('ax-resume-preview-status'),thumb=$('ax-resume-thumb');
  if(preview)preview.classList.remove('has-image');
  if(status)status.textContent='Loading frame at the saved position…';
  if(thumb){
    thumb.onload=null;thumb.onerror=null;thumb.removeAttribute('src');thumb.style.display='none';
    const pending=_axResumePending,frameTime=Math.max(0,Number(stored)||0);
    const request=_liveSeekPreviewModule?.loadFrameAtTime;
    if(typeof request==='function'){
      request(cid,msgId,frameTime,{width:320}).then(image=>{
        if(_axResumePending!==pending||dlg.hidden){image.removeAttribute('src');return;}
        const current=$('ax-resume-thumb');
        image.id='ax-resume-thumb';image.alt=`${String(name||'Video')} at ${label}`;image.loading='eager';image.style.display='block';
        if(current&&current!==image)current.replaceWith(image);else if(!image.isConnected)preview?.prepend(image);
        preview?.classList.add('has-image');if(status)status.textContent='';
        // Let the decoded frame paint before the seek-position byte range starts.
        requestAnimationFrame(()=>requestAnimationFrame(()=>{
           if(_axResumePending===pending&&!dlg.hidden)_axPreloadInitialAndResumeWindow(cid,msgId,frameTime);
        }));
      }).catch(()=>{
        if(_axResumePending===pending&&status)status.textContent='Preview unavailable at the saved position';
      });
    }else if(status)status.textContent='Preview unavailable at the saved position';
  }
  const fill=$('ax-resume-progress-fill');if(fill)fill.style.width=(duration>0?Math.max(0,Math.min(100,Number(stored)/duration*100)):0)+'%';
  const time=$('ax-resume-time');if(time)time.textContent=`${label}${duration>0?' / '+(fdur(duration)||''):''}`;
  const meta=$('ax-resume-meta');if(meta)meta.textContent=video.last_watched_at?`You stopped at ${label} · ${String(video.last_watched_at)}`:`You stopped at ${label}`;
  dlg.hidden=false;dlg.setAttribute('aria-hidden','false');
  $('ax-resume-resume')?.focus();return true;
}
function chooseAxResume(choice){
  const pending=_axResumePending;_axResumePending=null;
  const dlg=$('ax-resume-dialog');if(dlg){dlg.hidden=true;dlg.setAttribute('aria-hidden','true');}
  if(!pending||choice==='cancel')return;
  playB(pending.msgId,pending.cid,pending.name,pending.fromQueue,{choice,stored:pending.stored}).catch(()=>{});
}
async function playB(msgId,cid,name,fromQueue,resumeDecision=null){
  const playClickAt=performance.now();
  const v=findVideo(msgId,cid)||{id:msgId,_cid:cid};
  const selectedKey=vkey(cid,msgId);
  const reusePreparedSource=_P.preloadedKey===selectedKey&&!!pv().getAttribute('src');
  // Keep the video detail intact behind the in-app resume dialog until the user decides.
  if(resumeDecision===null&&!fromQueue&&_resumePromptEnabled){
    const stored=_axResumePositionFor(cid,msgId,v,fromQueue);
    if(stored!==null){
      const label=fdur(stored)||`${Math.floor(stored)}s`;
      if(_showAxResumePrompt(msgId,cid,name,fromQueue,stored,label,selectedKey))return;
    }
  }
  
  pClearPreviewSheet();
  if(!reusePreparedSource)pStop(selectedKey);
  const tok=++_P.openTok;
  if(tok!==_P.openTok)return;
  _P.cid=String(cid);_P.mediaKey=vkey(cid,msgId);_P.mid=Number(msgId);_P.fell=false;_P.autoFallbackTried=false;_P.iosHlsFallbackTried=false;_liveSeekPreviewModule?.clear();_P.lastSave=0;_P.dur=0;
  _P.preloadedKey='';
  {const chapterKey=vkey(cid,msgId),chapterCache=_playerSceneChapterCache.get(chapterKey);if(chapterCache)renderPlayerSceneChapterTrack(chapterKey,chapterCache.mediaId,chapterCache.markers);else if(!_dv||vkey(_dv._cid,_dv.id)!==chapterKey)void loadPlayerSceneChapters(cid,msgId);else clearPlayerSceneChapterTrack();}
  _P.playOpenAt=playClickAt;_P.firstPlayingAt=0;_P.firstFrameAt=0;_PStartupStillPending=true;_P.rebufferCount=0;_P.seekCount=0;_P.lastWaitAt=0;_P.netRetries=[];_P.netRecovering=0;_P.resumeAfterScrub=null;pSetState('LOADING');
  
  const k=vkey(cid,msgId);
  _P.info=_infoCache.get(k)||null;
  axPublishPlayerDimensions(v,cid,msgId,_P.info);
  _P.dur=(_P.info&&_P.info.duration)||v.duration||0;
  _aChan=String(cid);_aMsg=Number(msgId);_npMode='browser';
  if(!fromQueue){
    const list=contextList();
    const pi=list.findIndex(x=>String(x.id)===String(msgId)&&String(x._cid)===String(cid));
    if(pi>=0){_pl=[...list];_plIdx=pi;}else{_pl=[];_plIdx=-1;}
  }
  markPlaying();
  $('dtop').classList.add('loading');$('dtop').classList.remove('paused');
  const items=[];
  if(_pl.length>1){
    for(let j=2;j>=1;j--){const n=_pl[(_plIdx-j+_pl.length)%_pl.length];if(n)items.push({channel_id:n._cid,message_id:n.id});}
  }
  items.push({channel_id:cid,message_id:msgId});
  if(_pl.length>1)for(let j=1;j<=2;j++){const n=_pl[(_plIdx+j)%_pl.length];if(n)items.push({channel_id:n._cid,message_id:n.id});}
  api('/api/play/upcoming',{method:'POST',body:JSON.stringify({items,current:{channel_id:cid,message_id:msgId}})});
  // Attach the media source before waiting on metadata/history. The previous
  // startup path waited on /api/play/info (up to 2.5s) and then playback history
  // (up to 0.9s) before creating the browser media source. On a slow Telegram
  // connection that made the player appear to sit on "loading" before the
  // browser had even issued its first Range request. Direct playback is safe as
  // the initial mode because pInitialMode() already defaults to direct when
  // codec information is not known; a later info result can still drive the
  // explicit compatibility fallback if the browser cannot decode the stream.
  // Resume is opt-in by prompt, never automatic. The last saved local position
  // is only an offer; declining starts at zero and removes no user data.
  let start=0;
  if(resumeDecision?.choice==='resume')start=Math.max(0,Number(resumeDecision.stored)||0);
  if(_P.pendingStartAt!==null&&_P.pendingStartAt!==undefined&&Number.isFinite(Number(_P.pendingStartAt)))start=Math.max(0,Number(_P.pendingStartAt));
  _P.pendingStartAt=null;
  _P.lastServerPosition=0;_P.lastServerSave=Date.now();
  _stopAllCardPreviews();
  if(tok!==_P.openTok)return;
  const immediateMode=pInitialMode(_P.info);
  if(reusePreparedSource&&_P.mode===immediateMode&&pv().getAttribute('src')){
    const e=pv(),seq=_P.seq;
    _snapshotGridModule?.beginPlayback?.(start);
    pWatchFirstFrame(seq);
    const playPrepared=()=>{
      if(tok!==_P.openTok||seq!==_P.seq)return;
      if(start>0.5){try{e.currentTime=Math.min(start,Math.max(0,(e.duration||start)-0.25));}catch(x){}}
      e.play().catch(err=>{
        if(tok!==_P.openTok||seq!==_P.seq)return;
        $('dtop').classList.add('paused');$('dtop').classList.remove('loading');
        if(err?.name==='NotAllowedError'&&_isIOS())pToast('Safari blocked autoplay. Tap Play to start this video.',4200);
        else if(_P.mode==='hls')pError('Safari could not start the compatibility stream. Tap Retry or open the video in VLC.');
      });
    };
    if(start>0.5&&e.readyState<1)e.addEventListener('loadedmetadata',playPrepared,{once:true});
    else playPrepared();
  }else _pStart(immediateMode,start,true);
  // No warm RPC is needed here. Card-click preload is handled separately;
  // the browser's own first Range request owns foreground playback priority.

  // Metadata and server resume are advisory. Fetch them in parallel after the
  // source is already attached so they can improve the session without gating
  // playback startup.
  const infoPromise=_P.info?Promise.resolve(_P.info):fetchInfo(cid,msgId,4);
  const [infoResult]=await Promise.allSettled([infoPromise]);
  if(tok!==_P.openTok)return;
  if(infoResult.status==='fulfilled'&&infoResult.value){
    _P.info=infoResult.value;
    axPublishPlayerDimensions(v,cid,msgId,_P.info);
    if(_P.info.duration)_P.dur=_P.info.duration;
    if(_dv&&String(_dv._cid)===String(cid)&&Number(_dv.id)===Number(msgId))fillDetail(_dv);
    // Multi-part playable split: initialise parts state and redirect to
    // first part's stream.  The initial _pStart above used the parent
    // message_id which (for playable-part groups) has no media blob.
    // Switching to the first part's real stream here is seamless because
    // the player is still in the startup-loading phase.
    if(_P.info.playable_parts&&_P.info.playable_parts.length>=2){
      _pInitParts(_P.info);
      // Switch source to first part's message_id stream
      _pPartAdvance(0,start||0);
    }
  }
  // Intentionally no resume seek here. Playback always remains at 0:00 unless
  // the user explicitly seeks after opening the video.
  if(!_P.info)fetchInfo(cid,msgId,40).then(i=>{
    if(tok!==_P.openTok||!i)return;
    _P.info=i;axPublishPlayerDimensions(v,cid,msgId,i);if(i.duration)_P.dur=i.duration;if(_dv)fillDetail(_dv);
  });
  else if(_dv)fillDetail(_dv);
}
function pClearPreviewSheet(){
  _P.previewSeq++;
  try{if(_P.previewAbort)_P.previewAbort.abort();}catch(x){}
  _P.previewAbort=null;
  try{if(_P.seekAbort)_P.seekAbort.abort();}catch(x){}
  for(const u of _P.previewCache.values()){try{URL.revokeObjectURL(u);}catch(x){}}
  _P.previewCache.clear();
  cancelAnimationFrame(_P.previewRaf);
  clearTimeout(_P.previewWarmTimer);
  _liveSeekPreviewModule?.clear();
  _P.previewCanvas=null;_P.previewCtx=null;_P.previewLastBucket=-1;_P.previewLastFrame=-1;
}

function nextV(){
  if(!_pl.length){const l=contextList();const i=_dv?l.findIndex(x=>x===_dv):-1;if(l.length<2)return;_pl=[...l];_plIdx=i;}
  _plIdx=(_plIdx+1)%_pl.length;
  const v=_pl[_plIdx];openD(v.id,v._cid,true);playB(v.id,v._cid,v.title||v.name||'video '+v.id,true);
}
function prevV(){
  if(!_pl.length){
    const l=contextList();
    const i=_dv?l.findIndex(x=>x===_dv):-1;
    if(l.length<2)return;
    _pl=[...l];_plIdx=i>=0?i:0;
  }
  _plIdx=(_plIdx-1+_pl.length)%_pl.length;
  const v=_pl[_plIdx];
  openD(v.id,v._cid,true);
  playB(v.id,v._cid,v.title||v.name||'video '+v.id,true);
}
function stopExternalStatusPoll(){
  clearTimeout(_externalPoll);
  _externalPoll=0;
}
function hideExternalNowPlaying(){
  stopExternalStatusPoll();
  _externalPid=null;
  if(_npMode==='external')_npMode=null;
  markPlaying();
}
async function pollExternalPlayer(pid){
  stopExternalStatusPoll();
  if(!pid)return;
  const tick=async()=>{
    if(_externalPid!==pid||_npMode!=='external')return;
    try{
      const r=await api(`/api/play/external/status/${encodeURIComponent(pid)}`,{cache:'no-store'});
      if(_externalPid!==pid||_npMode!=='external')return;
      if(!r?.running){hideExternalNowPlaying();return;}
    }catch(e){}
    _externalPoll=setTimeout(tick,1500);
  };
  _externalPoll=setTimeout(tick,1200);
}

async function sendPD(pl,msgId,cid){
  if(_P.playing())pv().pause();
  const r=await api('/api/play/external',{method:'POST',body:JSON.stringify({channel_id:cid,message_id:msgId,player:pl})});
  if(!r?.ok){toast('Player error: '+(r?.detail||'not found'));return r;}
  stopExternalStatusPoll();
  _externalPid=Number(r.pid)||null;
  _npMode='external';
  pollExternalPlayer(_externalPid);
  toast(`Opened in ${r.player}${_isRemote()?' on the PC':''}.`);
  return r;
}
async function downloadSupportArtifact(url, fallbackName, label){
  try{
    toast(`Preparing ${label}…`);
    // Use the browser's native attachment download path instead of buffering
    // the entire response into a Blob. This keeps large/slow support artifacts
    // out of renderer memory and prevents a diagnostic collection delay from
    // looking like a frozen download.
    const target=`${url}${String(url).includes('?')?'&':'?'}_=${Date.now()}`;
    const a=document.createElement('a');
    a.href=target;
    a.download=fallbackName;
    a.style.display='none';
    document.body.appendChild(a);
    a.click();
    a.remove();
    toast(`${label} download started.`);
  }catch(e){
    axDiagRecord('support.download.error',{message:String(e?.message||e),url},'ERROR');
    toast(`${label} failed: ${e?.message||e}`);
  }
}
function _downloadText(text,name,type){
  const a=document.createElement('a');a.href=URL.createObjectURL(new Blob([text],{type}));a.download=name;document.body.appendChild(a);a.click();
  setTimeout(()=>{URL.revokeObjectURL(a.href);a.remove();},4000);
}
const _deviceLinkCache=new Map();
function _deviceLinkKey(cid,id){return `${String(cid)}:${String(id)}`;}
async function primeDeviceLink(cid,id){
  const key=_deviceLinkKey(cid,id),hit=_deviceLinkCache.get(key);
  if(hit&&Date.now()-hit.ts<5*60*1000)return hit;
  try{
    const r=await api(`/api/play/device-link/${encodeURIComponent(cid)}/${id}`);
    if(r?.url){const v={...r,ts:Date.now()};_deviceLinkCache.set(key,v);return v;}
  }catch(e){}
  return null;
}
function _launchVLCNativeUrl(target){
  if(!target)return false;
  if(_isAndroid()){
    location.href=`intent://${target.replace(/^https?:\/\//,'')}#Intent;scheme=http;type=video/*;package=org.videolan.vlc;end`;
    return true;
  }
  if(_isIOS()){
    // Keep the custom-scheme navigation synchronous with the user click so iOS
    // does not discard the activation after an awaited network operation.
    const vlcUrl=`vlc-x-callback://x-callback-url/stream?url=${encodeURIComponent(target)}`;
    window.location.href=vlcUrl;
    return true;
  }
  return false;
}
async function openVLCDevice(msgId,cid){
  if(_P.playing())pv().pause();
  const key=_deviceLinkKey(cid,msgId);let hit=_deviceLinkCache.get(key);
  if(!hit)hit=await primeDeviceLink(cid,msgId);
  if(!hit||!hit.url){toast('VLC link is still preparing. Try again in a moment.');return;}
  if(!hit.lan){toast('VLC device playback requires LAN access to Archive X.',6000);return;}
  if((_isIOS()||_isAndroid())&&_launchVLCNativeUrl(hit.url))return;
  const v=findVideo(msgId,cid)||{};
  await _openRemoteVLCPlaylist([{channel_id:cid,message_id:msgId,name:v.title||v.name||''}],'archive-x.m3u');
}
async function playHere(msgId,cid){return openVLCDevice(msgId,cid);}
function _openVLCPlaylistUrl(url){
  if(!url){toast('Could not create the VLC playlist.');return false;}
  if(_isAndroid()){
    location.href=`intent://${url.replace(/^https?:\/\//,'')}#Intent;scheme=http;type=audio/x-mpegurl;package=org.videolan.vlc;end`;
    return true;
  }
  if(_isIOS()){
    // VLC iOS supports the x-callback-url stream action. Passing the M3U URL
    // lets VLC fetch the queue directly; its entries remain signed media URLs.
    const vlcUrl=`vlc-x-callback://x-callback-url/stream?url=${encodeURIComponent(url)}`;
    window.location.href=vlcUrl;
    return true;
  }
  return false;
}
async function _openRemoteVLCPlaylist(items,filename='archive-x-random.m3u'){
  try{
    const res=await fetch('/api/play/device-playlist-link',{method:'POST',credentials:'include',headers:{'Content-Type':'application/json'},body:JSON.stringify({items})});
    const data=await res.json().catch(()=>({}));
    if(!res.ok||!data?.url){toast(data?.detail||'Could not create the VLC playlist.');return false;}
    if(!data.lan){toast('VLC device playback requires LAN access to Archive X.',6000);return false;}
    if(_openVLCPlaylistUrl(data.url))return true;
    const a=document.createElement('a');a.href=data.url;a.download=filename;a.target='_blank';a.rel='noopener';document.body.appendChild(a);a.click();a.remove();
    toast('Open the generated VLC playlist in VLC on this device.',6000);
    return true;
  }catch(e){toast(`Could not create the VLC playlist: ${e?.message||e}`,6000);return false;}
}
async function _startLibraryExternalPlaylist(player,shuffled){
  const playlist=shuffled.map(v=>({channel_id:v._cid,message_id:v.id,name:v.title||v.name||`video ${v.id}`}));
  const r=await api('/api/play/external',{method:'POST',body:JSON.stringify({player,random:true,playlist})});
  if(!r?.ok){toast(`${String(player).toUpperCase()} shuffle could not start: ${r?.detail||'unknown error'}`,8000);return false;}
  // A new shuffle owns the external-session handle. Polling reconciles manual
  // player-window closure, while closeDBtn can explicitly stop this PID.
  stopExternalStatusPoll();_externalPid=Number(r.pid)||null;_npMode='external';pollExternalPlayer(_externalPid);
  toast(`${r.player||player} shuffled playlist started: ${r.count||shuffled.length} videos${_isRemote()?' (on the PC)':''}`,6000);
  return true;
}
async function playRandom(player){
  if(!_filtV.length){setSt('Select a published channel first.');return;}
  const shuffled=[..._filtV];
  for(let i=shuffled.length-1;i>0;i--){const j=Math.floor(Math.random()*(i+1));[shuffled[i],shuffled[j]]=[shuffled[j],shuffled[i]];}
  _pl=shuffled;_plIdx=0;
  const items=shuffled.map(v=>({channel_id:v._cid,message_id:v.id,name:v.title||v.name||`video ${v.id}`}));
  if(_isRemote()){
    if(player==='vlc' && (_isIOS()||_isAndroid())){
      // VLC on mobile can import the whole randomized queue through its native
      // URL/playlist integration; do not reduce this to a single video.
      await _openRemoteVLCPlaylist(items,'archive-x-random.m3u');
      return;
    }
    const res=await fetch('/api/play/device-playlist',{method:'POST',credentials:'include',headers:{'Content-Type':'application/json'},body:JSON.stringify({items})});
    if(!res.ok){toast('Could not create the playlist.');return;}
    _downloadText(await res.text(),'archive-x-random.m3u','audio/x-mpegurl');
    toast(`Random playlist downloaded — open it in ${player==='mpc'?'MPC-HC':player.toUpperCase()} on this device.`,6000);return;
  }
  // Both VLC and MPC-HC must receive the complete shuffled playlist. The old
  // code launched only the first VLC item and never launched MPC-HC at all.
  if(['vlc','mpc','mpc-hc'].includes(String(player).toLowerCase())){
    await _startLibraryExternalPlaylist(String(player).toLowerCase(),shuffled);
    return;
  }
  playB(shuffled[0].id,shuffled[0]._cid,shuffled[0].title||shuffled[0].name||'Random video',false);
}

// ═══════════════════════════════════════════════
// SECTION: PERFORMER PICKER (search as you type, link to an existing one)
// ═══════════════════════════════════════════════
const _pk={id:null,cid:null,items:[],sel:0,t:null,seq:0,bulk:false,bulkField:'performer'};

function pkOpenBulk(field='performer'){
  if(!_mapSel.size){toast('Select at least one video.');return;}
  if(field!=='performer'){return mapSelected(field);}
  _pk.id=null;_pk.cid=null;_pk.bulk=true;_pk.bulkField=field;_pk.seq++;
  $('pk-title').textContent=`Select performer for ${_mapSel.size} selected video${_mapSel.size===1?'':'s'}`;
  $('pk').classList.add('open');$('pk-q').value='';
  $('pk-sugg').innerHTML='';
  $('pk-cur').innerHTML=`<span class="muted">${_mapSel.size} video${_mapSel.size===1?'':'s'} selected · choose a performer below</span>`;
  pkSearch(true);
  setTimeout(()=>$('pk-q').focus(),50);
}
function pkClose(){$('pk').classList.remove('open');_pk.bulk=false;}
function pkVideo(){return findVideo(_pk.id,_pk.cid);}



function pkRenderCur(){
  const v=pkVideo()||{};
  $('pk-cur').innerHTML=(v.performers||[]).length?(v.performers||[]).map(p=>`<span class="pill">${esc(p)}<span class="x" title="Remove from this video" onclick="pkRemoveImmediate(${jsa(p)})">×</span></span>`).join(''):'<span class="muted">No performers on this video yet.</span>';
}
function pkSearch(now){clearTimeout(_pk.t);_pk.t=setTimeout(pkDoSearch,now?0:140);}
async function pkDoSearch(){
  const q=$('pk-q').value.trim(),seq=++_pk.seq;
  const localPromise=api('/api/performers?limit=20'+(q?'&q='+encodeURIComponent(q):''));
  const providerPromise=q.length>=2?api('/api/performers/provider-search?limit=8&q='+encodeURIComponent(q)).catch(()=>[]):Promise.resolve([]);
  const [localRes,providerRes]=await Promise.all([localPromise,providerPromise]);
  if(seq!==_pk.seq)return;
  const list=Array.isArray(localRes)?localRes:[];
  const providerList=Array.isArray(providerRes)?providerRes:[];
  const have=_pk.bulk?new Set():new Set(((pkVideo()||{}).performers||[]).map(x=>x.toLowerCase()));
  const localItems=list.filter(p=>!have.has(String(p.name||'').toLowerCase())).map(p=>({name:p.name,id:p.id,count:p.video_count||0,image:p.image,kind:'local'}));
  const seen=new Set(localItems.map(p=>String(p.name||'').toLowerCase()));
  const apiItems=providerList.filter(p=>p.name && p.provider_id).map(p=>({
    name:p.name,id:null,count:0,image:p.image,kind:'provider',provider:p.provider,provider_id:p.provider_id,profile:p.profile,exact:p.exact,alias_exact:p.alias_exact
  })).filter(p=>{
    const k=String(p.name).toLowerCase();
    const providerKey=`${p.provider}:${p.provider_id}`;
    if(have.has(k) || seen.has(providerKey))return false;
    seen.add(providerKey);
    return true;
  });
  _pk.items=[...localItems,...apiItems].slice(0,28);
  _pk.sel=0;pkRender();
}
function pkRender(){
  const q=$('pk-q').value.trim().toLowerCase();
  const hl=s=>{const i=q?s.toLowerCase().indexOf(q):-1;return i<0?esc(s):esc(s.slice(0,i))+'<b style="color:var(--accent)">'+esc(s.slice(i,i+q.length))+'</b>'+esc(s.slice(i+q.length));};
  $('pk-list').innerHTML=_pk.items.length?_pk.items.map((p,i)=>`<div class="pk-item${i===_pk.sel?' on':''}" onmousedown="event.preventDefault();pkPick(${i})">
    ${p.kind==='provider'?(p.image?'<img class="pk-av" src="'+esc(p.image)+'" onerror="this.style.display=\'none\'">':'<span class="pk-av">'+axIcon('user')+'</span>'):(p.image?'<img class="pk-av" src="/api/performers/'+Number(p.id)+'/image?v='+encodeURIComponent(p.image||'')+'" onerror="this.replaceWith(Object.assign(document.createElement(\'span\'),{className:\'pk-av\',innerHTML:axIcon(\'user\')}))">':'<span class="pk-av">'+axIcon('user')+'</span>')}
    <div style="min-width:0;flex:1"><div style="font-size:13px">${hl(p.name)}</div><div class="muted" style="font-size:11px">${p.kind==='provider'?'API match · '+esc(p.provider==='stashdb'?'StashDB':'ThePornDB')+(p.exact?' · exact name':(p.alias_exact?' · alias match':'')):'Local performer · '+p.count+' video'+(p.count!==1?'s':'')+' · link to this performer'}</div></div></div>`).join(''):
    '<div class="muted" style="padding:8px">Type a name to search your performers.</div>';
}
function pkKey(ev){
  if(ev.key==='ArrowDown'){ev.preventDefault();_pk.sel=Math.min(_pk.items.length-1,_pk.sel+1);pkRender();}
  else if(ev.key==='ArrowUp'){ev.preventDefault();_pk.sel=Math.max(0,_pk.sel-1);pkRender();}
  else if(ev.key==='Enter'){ev.preventDefault();if(_pk.items[_pk.sel])pkPick(_pk.sel);}
  else if(ev.key==='Escape'){ev.preventDefault();pkClose();}
}
async function pkPick(i){
  const p=_pk.items[i];if(!p)return;
  if(_pk.bulk){
    const videos=[..._mapSel.values()];
    const r=await api('/api/mapping/bulk',{method:'POST',body:JSON.stringify({videos,field:'performer',performer_id:p.id||null,target_name:String(p.name||'').trim(),provider:p.provider||null,provider_id:p.provider_id||null,image:p.image||null,profile:p.profile||null,action:'move',lock:true})});
    if(!r.ok){toast('Could not map performer: '+(r.detail||'unknown error'));return;}
    const nm=String(p.name||'').trim();
    pkClose();
    toast(`${nm} linked to ${r.changed||videos.length} selected video${(r.changed||videos.length)===1?'':'s'}.`);
    clearMapSelection();
    await _refreshAfterMapping();
    return;
  }
  const r=await api('/api/local/performer-map',{method:'POST',body:JSON.stringify({
    channel_id:_pk.cid,message_id:_pk.id,name:p.name,
    provider:p.provider||null,provider_id:p.provider_id||null,
    image:p.image||null,profile:p.profile||null
  })});
  if(!r.ok){toast('Could not save performer: '+(r.detail||'unknown error'));return;}
  const nm=r.performer.name;
  pkUpdateVideo(list=>Array.from(new Set([...(list||[]),nm])));
  toast(`${nm} linked to this video.`);
  $('pk-q').value='';pkRenderCur();pkSearch(true);$('pk-q').focus();
}
async function pkRemoveImmediate(name){
  const r=await api('/api/local/performer-unmap',{method:'POST',body:JSON.stringify({channel_id:_pk.cid,message_id:_pk.id,name})});
  if(!r.ok){toast('Could not remove: '+(r.detail||'error'));return;}
  pkUpdateVideo(list=>(list||[]).filter(x=>x!==name));
  pkRenderCur();pkSearch(true);
}
function pkUpdateVideo(fn){
  const m=x=>String(x.id)===String(_pk.id)&&String(x._cid)===String(_pk.cid);
  let t=null;
  [_chanV[String(_pk.cid)]||[],_allV,_filtV,_perfVideos,_tagVideos,_pl].forEach(l=>(l||[]).filter(m).forEach(x=>{x.performers=fn(x.performers);t=x;}));
  if(t)refreshCard(t);
  if(_dv&&m(_dv)){_dv.performers=fn(_dv.performers);fillDetail(_dv);}
}


// ── SECTION: LOCAL SITE MAPPING ──
const _sk={id:null,cid:null,items:[],sel:0,t:null,seq:0,bulk:false};

function skOpenBulk(){
  if(!_mapSel.size){toast('Select at least one video.');return;}
  _sk.id=null;_sk.cid=null;_sk.bulk=true;_sk.seq++;
  $('sk-title').textContent=`Select studio for ${_mapSel.size} selected video${_mapSel.size===1?'':'s'}`;
  $('sk').classList.add('open');$('sk-q').value='';
  $('sk-cur').innerHTML=`<span class="muted">${_mapSel.size} video${_mapSel.size===1?'':'s'} selected · choose a studio below</span>`;
  skSearch(true);setTimeout(()=>$('sk-q').focus(),50);
}
function skClose(){$('sk').classList.remove('open');_sk.bulk=false;}
function skSearch(now){clearTimeout(_sk.t);_sk.t=setTimeout(skDoSearch,now?0:140);}
async function skDoSearch(){
  const q=$('sk-q').value.trim(),seq=++_sk.seq;
  const r=await api('/api/studios?limit=20'+(q?'&q='+encodeURIComponent(q):''));
  if(seq!==_sk.seq)return;
  const list=Array.isArray(r)?r:[];
  _sk.items=list.map(x=>({name:x.name,id:x.id,count:x.video_count||0,image:x.image}));
  if(q&&!list.some(x=>String(x.name||'').toLowerCase()===q.toLowerCase()))_sk.items.push({name:q,create:true});
  _sk.sel=0;skRender();
}
function skRender(){
  const q=$('sk-q').value.trim().toLowerCase();
  const hl=s=>{const i=q?s.toLowerCase().indexOf(q):-1;return i<0?esc(s):esc(s.slice(0,i))+'<b style="color:var(--accent)">'+esc(s.slice(i,i+q.length))+'</b>'+esc(s.slice(i+q.length));};
  $('sk-list').innerHTML=_sk.items.length?_sk.items.map((p,i)=>`<div class="pk-item${i===_sk.sel?' on':''}" onmousedown="event.preventDefault();skPick(${i})">
    ${p.create?'<span class="pk-av"><svg class="ax-icon" aria-hidden="true"><use href="#ax-plus"></use></svg></span>':(p.image?`<img class="pk-av" src="/api/studios/${Number(p.id)}/image?v=${encodeURIComponent(p.image||"")}" onerror="this.replaceWith(Object.assign(document.createElement('span'),{className:'pk-av',textContent:''}))">`:'<span class="pk-av"><svg class="ax-icon" aria-hidden="true"><use href="#ax-circle"></use></svg></span>')}
    <div style="min-width:0;flex:1"><div style="font-size:13px">${p.create?'Create new studio “'+esc(p.name)+'”':hl(p.name)}</div>${p.create?'':`<div class="muted" style="font-size:11px">${p.count} video${p.count!==1?'s':''} · link to this studio</div>`}</div></div>`).join(''):'<div class="muted" style="padding:8px">Type a studio name to search your studios.</div>';
}
function skKey(ev){
  if(ev.key==='ArrowDown'){ev.preventDefault();_sk.sel=Math.min(_sk.items.length-1,_sk.sel+1);skRender();}
  else if(ev.key==='ArrowUp'){ev.preventDefault();_sk.sel=Math.max(0,_sk.sel-1);skRender();}
  else if(ev.key==='Enter'){ev.preventDefault();if(_sk.items[_sk.sel])skPick(_sk.sel);}
  else if(ev.key==='Escape'){ev.preventDefault();skClose();}
}
async function skPick(i){
  const p=_sk.items[i];if(!p)return;
  const videos=_sk.bulk?[..._mapSel.values()]:[{channel_id:_sk.cid,message_id:Number(_sk.id)}];
  const r=await api('/api/mapping/bulk',{method:'POST',body:JSON.stringify({videos,field:'site',site_id:p.id||null,target_name:String(p.name||'').trim(),action:'move',lock:true})});
  if(!r.ok){toast('Could not map studio: '+(r.detail||'unknown error'));return;}
  const nm=String(p.name||'').trim();
  const wasBulk=_sk.bulk;
  skClose();
  toast(`${nm} linked to ${r.changed||videos.length} video${(r.changed||videos.length)===1?'':'s'}.`);
  clearMapSelection();
  if(!wasBulk){closeDBtn();loadLib();return;}
  await _refreshAfterMapping();
}

// ═══════════════════════════════════════════════
// SECTION: SEND TO TELEGRAM
// ═══════════════════════════════════════════════
// ═══════════════════════════════════════════════
// SECTION: COMBINED PERFORMER + STUDIO MAPPING
// ═══════════════════════════════════════════════
const _psm={selPerf:null,selStudio:null,perfItems:[],studioItems:[],perfSeq:0,studioSeq:0,bulk:false};
function psMapClose(){const el=$('psmap');if(el)el.classList.remove('open');_psm.selPerf=null;_psm.selStudio=null;_psm.perfItems=[];_psm.studioItems=[];_psm.bulk=false;_psm.withTitle=false;}
function psMapCurrentForVideo(){const first=[..._mapSel.values()][0];if(!first)return{};return findVideo(first.message_id,first.channel_id)||{};}
function psMapRenderCurrent(){const p=_psm.selPerf,s=_psm.selStudio;$('psmap-perf-cur').innerHTML=p?`<span class="pill">${esc(p.name)} <span class="x" onclick="psMapClear('performer')">×</span></span>`:'<span class="muted">No performer selected.</span>';$('psmap-studio-cur').innerHTML=s?`<span class="pill">${esc(s.name)} <span class="x" onclick="psMapClear('studio')">×</span></span>`:'<span class="muted">No studio selected.</span>';$('psmap-summary').textContent=`${_mapSel.size} video${_mapSel.size===1?'':'s'} selected`;const b=$('psmap-save');if(b)b.disabled=!psMapCanSave();}
// V29.4.131: "Apply metadata" mode adds a title. Any of: a title, a performer +
// studio pair, or all three. The existing /api/mapping/bulk does each part.
function psMapTitle(){return _psm.withTitle?String($('psmap-title')?.value||'').trim():'';}
function psMapCanSave(){const both=!!(_psm.selPerf&&_psm.selStudio),any=!!(_psm.selPerf||_psm.selStudio);return _psm.withTitle?(!!psMapTitle()||any):both;}
function psMapClear(kind){if(kind==='performer')_psm.selPerf=null;else _psm.selStudio=null;psMapRenderCurrent();}
function psMapOpenBulk(withTitle=false){if(!_mapSel.size){toast('Select at least one video.');return;}_psm.bulk=true;_psm.withTitle=!!withTitle;
  const tr=$('psmap-title-row');if(tr)tr.style.display=_psm.withTitle?'':'none';const ti=$('psmap-title');if(ti)ti.value='';
  const hd=$('psmap-heading');if(hd)hd.textContent=_psm.withTitle?'Apply metadata':'Map performer + studio';
  const sv=$('psmap-save');if(sv)sv.textContent=_psm.withTitle?'Apply to selected videos':'Save performer + studio';
  const st=$('psmap-status');if(st)st.textContent=_psm.withTitle?'Change any of: title, performer, studio. Empty parts are left as they are.':'Choose both values, then save once.';// "Apply metadata" starts empty: only what the user picks is applied.
  const v=_psm.withTitle?{}:psMapCurrentForVideo();if(v.performers?.length)_psm.selPerf={name:String(v.performers[0]),id:null,kind:'local'};if(v.site_name||v.studio)_psm.selStudio={name:String(v.site_name||v.studio),id:v.site_id||null,kind:'local'};$('psmap-perf-q').value='';$('psmap-studio-q').value='';$('psmap-perf-list').innerHTML='';$('psmap-studio-list').innerHTML='';psMapRenderCurrent();$('psmap').classList.add('open');psMapSearch('performer',true);psMapSearch('studio',true);setTimeout(()=>$('psmap-perf-q').focus(),50);}
function psMapSearch(kind,now=false){const fn=()=>psMapDoSearch(kind);const key=kind==='performer'?'_perfT':'_studioT';clearTimeout(psMapSearch[key]);psMapSearch[key]=setTimeout(fn,now?0:140);}
async function psMapDoSearch(kind){const isPerf=kind==='performer',q=$(isPerf?'psmap-perf-q':'psmap-studio-q').value.trim(),seq=isPerf?(++_psm.perfSeq):(++_psm.studioSeq);let local=[];try{const r=await api(isPerf?('/api/performers?limit=24'+(q?'&q='+encodeURIComponent(q):'')):('/api/studios?limit=24'+(q?'&q='+encodeURIComponent(q):'')));local=Array.isArray(r)?r:[];}catch(e){local=[];}let provider=[];if(isPerf&&q.length>=2){try{const r=await api('/api/performers/provider-search?limit=8&q='+encodeURIComponent(q));provider=Array.isArray(r)?r:[];}catch(e){provider=[];}}if(isPerf&&seq!==_psm.perfSeq)return;if(!isPerf&&seq!==_psm.studioSeq)return;if(isPerf){const items=local.map(p=>({name:p.name,id:p.id,count:p.video_count||0,image:p.image,kind:'local'}));const seen=new Set(items.map(p=>String(p.name||'').toLowerCase()));provider.forEach(p=>{if(!p.name||!p.provider_id)return;const k=String(p.name).toLowerCase();if(seen.has(k))return;seen.add(k);items.push({name:p.name,id:null,count:0,image:p.image,kind:'provider',provider:p.provider,provider_id:p.provider_id,profile:p.profile});});_psm.perfItems=items.slice(0,28);psMapRenderList('performer');}else{_psm.studioItems=local.map(p=>({name:p.name,id:p.id,count:p.video_count||0,image:p.image,kind:'local',create:false}));if(q&&!local.some(x=>String(x.name||'').toLowerCase()===q.toLowerCase()))_psm.studioItems.push({name:q,id:null,count:0,image:null,kind:'local',create:true});psMapRenderList('studio');}}
function psMapRenderList(kind){const perf=kind==='performer',q=$(perf?'psmap-perf-q':'psmap-studio-q').value.trim().toLowerCase(),list=perf?_psm.perfItems:_psm.studioItems,box=$(perf?'psmap-perf-list':'psmap-studio-list');const hl=s=>{const i=q?s.toLowerCase().indexOf(q):-1;return i<0?esc(s):esc(s.slice(0,i))+'<b style="color:var(--accent)">'+esc(s.slice(i,i+q.length))+'</b>'+esc(s.slice(i+q.length));};box.innerHTML=list.length?list.map((p,i)=>`<div class="psmap-item" onclick="psMapPick('${kind}',${i})">${p.create?'<span class="psmap-avatar">'+axIcon('plus')+'</span>':(p.image?(perf&&p.kind==='local'?'<img src="/api/performers/'+Number(p.id)+'/image?v='+encodeURIComponent(p.image||'')+'" onerror="this.style.display=\'none\'">':'<img src="'+esc(perf?p.image:'/api/studios/'+Number(p.id)+'/image')+'" onerror="this.style.display=\'none\'">'):(p.kind==='provider'?'<span class="psmap-avatar">'+axIcon('user')+'</span>':'<span class="psmap-avatar">'+(perf?axIcon('user'):axIcon('circle'))+'</span>'))}<div style="min-width:0;flex:1"><div style="font-size:13px">${p.create?'Create new studio “'+esc(p.name)+'”':hl(p.name)}</div><div class="meta">${p.kind==='provider'?'API match · '+esc(p.provider==='stashdb'?'StashDB':'ThePornDB'):p.create?'New studio':'Local '+(perf?'performer':'studio')+' · '+p.count+' video'+(p.count!==1?'s':'')}</div></div></div>`).join(''):'<div class="muted" style="padding:8px">No matches.</div>';}
function psMapPick(kind,i){const p=(kind==='performer'?_psm.perfItems:_psm.studioItems)[i];if(!p)return;if(kind==='performer')_psm.selPerf=p;else _psm.selStudio=p;psMapRenderCurrent();}
async function psMapSave(){if(!_mapSel.size||!psMapCanSave()){toast(_psm.withTitle?'Enter a title, or choose a performer or a studio.':'Select both a performer and a studio.');return;}const b=$('psmap-save');if(b)b.disabled=true;$('psmap-status').textContent='Saving mapping…';const title=psMapTitle();
  if(title){const rt=await api('/api/mapping/bulk',{method:'POST',body:JSON.stringify({videos:[..._mapSel.values()],field:'title',target_name:title,action:'move',lock:true})});if(!rt.ok){$('psmap-status').textContent='Save failed';if(b)b.disabled=false;toast(rt.detail||'Title update failed.');return;}
    if(!_psm.selPerf&&!_psm.selStudio){const n=rt.changed||_mapSel.size;psMapClose();toast(`Title applied to ${n} video${n===1?'':'s'}.`);clearMapSelection();await _refreshAfterMapping();return;}}
  const p=_psm.selPerf,s=_psm.selStudio;
  // Performer + studio together, or (in "Apply metadata") just one of them.
  const field=p&&s?'performer_site':(p?'performer':'site');
  const payload={videos:[..._mapSel.values()],field,action:'move',lock:true,target_name:String((p||s).name||'').trim()};
  if(p)Object.assign(payload,{performer_id:p.id||null,performer_name:String(p.name||'').trim(),provider:p.provider||null,provider_id:p.provider_id||null,image:p.image||null,profile:p.profile||null});
  if(s)Object.assign(payload,{site_id:s.id||null,studio_name:String(s.name||'').trim()});const r=await api('/api/mapping/bulk',{method:'POST',body:JSON.stringify(payload)});if(!r.ok){$('psmap-status').textContent='Save failed';if(b)b.disabled=false;toast(r.detail||'Combined mapping failed.');return;}psMapClose();toast(`${r.changed||_mapSel.size} video${(r.changed||_mapSel.size)===1?'':'s'} ${title?'updated':'mapped to '+(field==='performer_site'?'performer + studio':field==='performer'?'performer':'studio')}.`);clearMapSelection();await _refreshAfterMapping();}

let _sendCfg=null;const _sm={id:null,cid:null,target:'',batch:null};
async function loadSendSettings(){
  const r=await api('/api/settings/send');
  if(!r||!r._ok)return;
  _sendCfg=r;
  if($('m-send-target')){$('m-send-target').value=r.target||'';$('m-send-fixed').checked=!!r.fixed;}
  if($('m-send-tpl'))$('m-send-tpl').value=r.template||'';
  if($('m-send-ph'))$('m-send-ph').textContent=(r.placeholders||[]).map(x=>'{'+x+'}').join('  ');
}


const chanTitle=id=>{const c=_chans.find(c=>String(c.telegram_id)===String(id));return channelDisplayName(id,c?.title);};
function smSetMode(batchCount=0){
  const isBatch=Number(batchCount)>0;
  const title=$('sm-title-text');if(title)title.textContent=isBatch?`Send ${batchCount} selected video${batchCount===1?'':'s'} to Telegram`:'Send to Telegram';
  const label=$('sm-cap-label');if(label)label.textContent=isBatch?`Caption preview for the first selected video. Each video gets its own caption from the template in Operations → Send to Telegram.`:'Caption that will be sent (edit the template in Operations → Send to Telegram):';
  const button=$('sm-send-btn');if(button)button.textContent=isBatch?`Send ${batchCount} video${batchCount===1?'':'s'}`:'Send';
}
async function sendTG(msgId,cid){
  const viewSeq=_topLevelViewSeq;
  if(!_sendCfg)await loadSendSettings();
  const cfg=_sendCfg||{};
  if(cfg.fixed&&cfg.target){doSend(msgId,cid,cfg.target,false,false);return;}
  if(viewSeq!==_topLevelViewSeq)return;
  _sm.id=msgId;_sm.cid=cid;_sm.batch=null;_sm.target=cfg.target||'';
  smSetMode(0);$('sm-q').value='';$('sm-remember').checked=false;smRender();
  $('sm').classList.add('open');
  const p=await api('/api/send/preview',{method:'POST',body:JSON.stringify({channel_id:cid,message_id:msgId})});
  if(!_sm.batch&&_sm.id===msgId&&String(_sm.cid)===String(cid)&&$('sm').classList.contains('open'))$('sm-cap').innerHTML=p&&p.caption?p.caption.replace(/\n/g,'<br>'):'<span class="muted">(no caption)</span>';
}
async function openBulkSendTG(){
  const viewSeq=_topLevelViewSeq;
  const batch=[..._mapSel.values()].map(v=>({channel_id:String(v.channel_id),message_id:Number(v.message_id)}));
  if(!batch.length){toast('Select at least one video.');return;}
  if(!_sendCfg)await loadSendSettings();
  const cfg=_sendCfg||{};
  // Preserve the established "Always send to this channel" setting used by single-video sends.
  if(cfg.fixed&&cfg.target){await bulkSendToTelegram(batch,cfg.target,false,null);return;}
  if(viewSeq!==_topLevelViewSeq||!_mapMode||_mapSel.size!==batch.length)return;
  _sm.id=null;_sm.cid=null;_sm.batch=batch;_sm.target=cfg.target||'';
  smSetMode(batch.length);$('sm-q').value='';$('sm-remember').checked=false;smRender();
  $('sm-cap').innerHTML='<span class="muted">Preparing caption preview…</span>';
  $('sm').classList.add('open');
  const first=batch[0];
  const p=await api('/api/send/preview',{method:'POST',body:JSON.stringify({channel_id:first.channel_id,message_id:first.message_id})});
  if(!Array.isArray(_sm.batch)||_sm.batch.length!==batch.length||Number(_sm.batch[0]?.message_id)!==Number(first.message_id)||String(_sm.batch[0]?.channel_id)!==String(first.channel_id)||!$('sm').classList.contains('open'))return;
  $('sm-cap').innerHTML=p&&p.caption?p.caption.replace(/\n/g,'<br>'):'<span class="muted">(caption preview unavailable; each video will still use its configured caption template)</span>';
}
function smRender(){
  const q=$('sm-q').value.trim().toLowerCase();
  const list=_chans.filter(c=>!q||String(c.title||'').toLowerCase().includes(q));
  $('sm-list').innerHTML=list.map(c=>`<div class="pk-item${String(c.telegram_id)===String(_sm.target)?' on':''}" onclick="_sm.target=${jsa(c.telegram_id)};smRender()"><span class="pk-av">#</span><div style="flex:1;min-width:0">${esc(channelDisplayName(c.telegram_id,c.title))}</div></div>`).join('')||'<div class="muted" style="padding:8px">No channels.</div>';
}
function smClose(){$('sm').classList.remove('open');_sm.batch=null;smSetMode(0);}
function smSend(){
  if(!_sm.target){toast('Choose a channel first.');return;}
  const target=_sm.target,remember=$('sm-remember').checked;
  const batch=Array.isArray(_sm.batch)?_sm.batch.map(v=>({...v})):null;
  const id=_sm.id,cid=_sm.cid;
  smClose();
  if(batch){void bulkSendToTelegram(batch,target,remember,null);return;}
  doSend(id,cid,target,remember,false);
}
let _bulkSendJobCounter=0;
async function bulkSendToTelegram(batch,target,remember,duplicateAction){
  if(!Array.isArray(batch)||!batch.length||!target){toast('Select videos and choose a destination channel.');return;}
  toast(`Sending ${batch.length} selected video${batch.length===1?'':'s'} to ${chanTitle(target)}. This may take a while…`,5000);
  const jobId=`bulk-${Date.now().toString(36)}-${(++_bulkSendJobCounter).toString(36)}`;
  const payload={videos:batch,target,remember,job_id:jobId};
  if(duplicateAction)payload.duplicate_action=duplicateAction;
  let polling=true;
  const progressTask=(async()=>{
    while(polling){
      await new Promise(resolve=>setTimeout(resolve,1600));
      if(!polling)break;
      const state=await api(`/api/send/bulk/status/${encodeURIComponent(jobId)}`,{cache:'no-store'});
      if(!state||state._ok===false||state.job_id!==jobId||!state.active)continue;
      const current=state.current_message_id?findVideo(Number(state.current_message_id),String(state.current_channel_id))||{}:{};
      const title=String(current.title||current.name||current.telegram_filename||'');
      const n=Number(state.done||0),total=Number(state.total||batch.length);
      toast(`Sending to ${chanTitle(target)} · ${n}/${total} complete${title?' · '+title:''}`,4000);
    }
  })();
  let r;
  try{r=await api('/api/send/bulk',{method:'POST',body:JSON.stringify(payload)});}
  finally{polling=false;}
  await progressTask;
  if(r&&r.already_sent&&Array.isArray(r.duplicate_videos)&&!duplicateAction){
    const n=r.duplicate_videos.length;
    const resend=await axConfirm(`${n} of the selected videos ${n===1?'has':'have'} already been sent to ${chanTitle(target)}. Send those again too? Choose No to skip duplicates and continue sending the remaining videos.`,{title:'Some videos were already sent',okLabel:'Send again',danger:false});
    await bulkSendToTelegram(batch,target,remember,resend?'resend':'skip');
    return;
  }
  if(!r||!r._ok||!r.ok){toast('Bulk send failed: '+(r&&r.detail||'unknown error'),8000);return;}
  if(remember)await loadSendSettings();
  await refreshAfterTelegramSend();
  const sent=Number(r.sent||0),skipped=Number(r.skipped||0),failed=Number(r.failed||0);
  let summary=`Telegram send complete: ${sent} sent, ${skipped} skipped, ${failed} failed.`;
  const firstFailure=(r.results||[]).find(x=>x.status==='failed');
  if(firstFailure){
    const v=findVideo(Number(firstFailure.message_id),String(firstFailure.channel_id))||{};
    const name=String(v.title||v.name||v.telegram_filename||`message ${firstFailure.message_id}`);
    summary+=` First failure: ${name} — ${String(firstFailure.error||'send failed')}.`;
  }
  toast(summary,failed?12000:8000);
}
async function doSend(msgId,cid,target,remember,force){
  toast(`Sending to ${chanTitle(target)}…`,15000);
  const r=await api('/api/send',{method:'POST',body:JSON.stringify({channel_id:cid,message_id:msgId,target,remember,force})});
  if(r&&r.already_sent){await refreshAfterTelegramSend();if(await axConfirm(`This video was already sent to ${chanTitle(target)}. Send it again?`,{title:'Video already sent',okLabel:'Send again',danger:false}))doSend(msgId,cid,target,false,true);else toast('Not sent.');return;}
  if(!r||!r._ok||!r.ok){toast('Send failed: '+(r&&r.detail||'unknown error'),8000);return;}
  if(remember)loadSendSettings();
  await refreshAfterTelegramSend();
  toast(`${axIcon('check')} Sent to ${chanTitle(target)}${(r.notes||[]).length?' — '+r.notes.join('; '):''}`,7000);
}
async function refreshAfterTelegramSend(){
  const selectedBefore=new Set([..._sel].map(String));
  const hadAllChannels=_chans.length>0&&_chans.every(c=>selectedBefore.has(String(c.telegram_id)));
  try{
    await loadChannels();
    if(hadAllChannels){
      _chans.forEach(c=>_sel.add(String(c.telegram_id)));
      renderChannelCards();renderChips();
    }
    if(_sel.size)await loadLib([..._sel],true);
  }catch(e){console.warn('Telegram send succeeded, but the visible Library could not be refreshed.',e);}
}

// ═══════════════════════════════════════════════
// SECTION: v18.10 — playback button icons, performer random
// ═══════════════════════════════════════════════
const ICO={
  tg:axIcon('brand-telegram','brand-icon'),
  vlc:axIcon('brand-vlc','brand-icon'),
  mpc:'<img class="bi" src="/static/icons/mpc.png" alt="" aria-hidden="true" onerror="this.remove()">',
};
const _perfRecent={};
let _perfRandomQueue=[];
let _siteRandomQueue=[];
function tgLink(cid,id){
  const ch=_chans.find(c=>String(c.telegram_id)===String(cid));
  if(ch&&ch.username)return `https://t.me/${ch.username}/${id}`;
  const raw=String(cid).replace(/^-100/,'').replace(/^-/,'');
  return `tg://privatepost?channel=${raw}&post=${id}`;
}

async function perfRandomMPC(){
  // Shuffle ALL videos linked to THIS performer and start them as one external playlist.
  const list=(_perfVideos||[]).filter(v=>v&&v.id!=null&&v._cid!=null);
  if(!list.length){toast('This performer has no videos in the local catalogue yet.');return;}
  const shuffled=[...list];
  for(let i=shuffled.length-1;i>0;i--){const j=Math.floor(Math.random()*(i+1));[shuffled[i],shuffled[j]]=[shuffled[j],shuffled[i]];}
  _perfRandomQueue=shuffled;
  const btn=$('perf-rnd');if(btn)btn.disabled=true;
  toast(`Starting random MPC-HC playlist: ${shuffled.length} videos…`,8000);
  const playlist=shuffled.map(v=>({channel_id:v._cid,message_id:v.id,name:v.title||v.name||`video ${v.id}`}));
  const r=await api('/api/play/external',{method:'POST',body:JSON.stringify({player:'mpc',random:true,playlist})});
  if(btn)btn.disabled=false;
  if(r&&r.ok){
    stopExternalStatusPoll();_externalPid=Number(r.pid)||null;_npMode='external';pollExternalPlayer(_externalPid);
    toast(`MPC-HC random playlist started: ${r.count||shuffled.length} videos${_isRemote()?' (on the PC)':''}`,6000);
  } else toast('MPC-HC could not start: '+((r&&r.detail)||'unknown error'),8000);
}


// ═══════════════════════════════════════════════
// SECTION: V5.5 — Library discovery, watch history, markers, providers
// ═══════════════════════════════════════════════
function _axSystemTheme(){try{return window.matchMedia&&window.matchMedia('(prefers-color-scheme: dark)').matches?'dark':'light';}catch(e){return 'light';}}
function _axNormalizeThemeMode(mode){return mode==='auto-time'?'system':(mode==='light'||mode==='dark'||mode==='system'?mode:'system');}
function _axThemeForMode(mode){mode=_axNormalizeThemeMode(mode);return mode==='system'?_axSystemTheme():mode;}
// Theme ids represent paired palettes. Mode is a separate preference, so each
// built-in theme has both a light and a dark variant.
const _axThemePresets={
  nord:{name:'Nord'},'nero-gold':{name:'Nero Gold'},'black-essence':{name:'Black Essence'},
  'pastel-lavender':{name:'Pastel Lavender'},'pastel-mint':{name:'Pastel Mint'},'minimal-slate':{name:'Minimal Slate'},'minimal-sand':{name:'Minimal Sand'}
};
function _axNormalizeThemePreset(preset,legacyMode){
  const aliases={'nobuf-dark':'nobuf','nobuf-light':'nobuf','solarized-light':'solarized'};
  preset=aliases[preset]||preset;
  if(preset==='custom')return 'custom';
  if(Object.prototype.hasOwnProperty.call(_axThemePresets,preset))return preset;
  return 'nord';
}
function _axNormalizeGridDensity(density){return density==='compact'||density==='spacious'||density==='default'?density:'default';}
function _axNormalizeUiScale(value){
  const n=Number(value);
  if(!Number.isFinite(n))return 100;
  return Math.max(70,Math.min(130,Math.round(n)));
}
function _axScaleFromLegacyDensity(density){return density==='compact'?80:density==='spacious'?120:100;}
function _axSyncUiScaleControls(scale){
  scale=_axNormalizeUiScale(scale);
  const slider=$('ax-ui-scale'),value=$('ax-ui-scale-value');
  if(slider){slider.value=String(scale);slider.setAttribute('aria-valuetext',`${scale} percent`);}
  if(value)value.textContent=`${scale}%`;
  // Presets are exact values. Intermediate slider values are a custom scale,
  // not a reason to unexpectedly change the user's card density or layout.
  const group=scale===70?'dense':scale===100?'default':scale===130?'spacious':'';
  document.querySelectorAll('[data-ui-scale-preset]').forEach(button=>{
    const active=!!group&&button.dataset.uiScalePreset===group;
    button.classList.toggle('active',active);button.setAttribute('aria-pressed',active?'true':'false');
  });
}
function applyArchiveUiScale(value){
  const scale=_axNormalizeUiScale(value),root=document.documentElement,zoom=scale/100;
  root.dataset.uiScale=String(scale);
  root.style.setProperty('--ax-ui-zoom',String(zoom));
  // Older builds used an inverse width to counteract zoom. That changed the
  // effective canvas width and caused grids/panels to stretch or collapse.
  root.style.removeProperty('--ax-ui-width');
  const scaleRoot=$('ax-ui-scale-root')||document.body;
  if(scaleRoot){scaleRoot.style.zoom=`${scale}%`;scaleRoot.style.width='100%';scaleRoot.style.maxWidth='100%';scaleRoot.style.minWidth='0';}
  _axSyncUiScaleControls(scale);
  // Viewport-unit based fixed headers, dialogs and controls must be compensated
  // whenever the application zoom changes; otherwise they shrink/grow against
  // the unzoomed browser viewport and create blank gutters or horizontal shifts.
  if(typeof window.axZoomCompensate==='function')window.axZoomCompensate();
  return scale;
}
function previewArchiveUiScale(value){
  const scale=applyArchiveUiScale(value);
  const status=$('ax-ui-scale-status');if(status)status.textContent=`Previewing ${scale}% across the whole application. Release to save.`;
  return scale;
}
async function selectArchiveUiScale(value,options={}){
  const scale=applyArchiveUiScale(value),density=options&&options.density?_axNormalizeGridDensity(options.density):null;
  if(density)applyArchiveGridDensity(density);
  try{localStorage.setItem('ax_ui_scale',String(scale));if(density)localStorage.setItem('ax_grid_density',density);}catch(e){}
  const status=$('ax-ui-scale-status');if(status)status.textContent=`Saving ${scale}% application zoom…`;
  try{
    await _axPersistAppearance(density?{ui_scale:scale,grid_density:density}:{ui_scale:scale});
    if(status)status.textContent=`Application zoom saved at ${scale}%.`;
    _axSetAppearanceStatus(`Application zoom: ${scale}%. Saved.`);
  }catch(e){
    if(status)status.textContent=`${scale}% is applied locally; server save failed.`;
    _axSetAppearanceStatus(`Application zoom applied locally; server save failed.`,true);
  }
  return scale;
}
function setArchiveUiScalePreset(value){
  const scale=_axNormalizeUiScale(value),density=scale===70?'compact':scale===130?'spacious':'default';
  return selectArchiveUiScale(scale,{density});
}
function _axBindUiScaleShortcuts(){
  if(window.__axUiScaleShortcutsBound)return;window.__axUiScaleShortcutsBound=true;
  window.addEventListener('keydown',event=>{
    if(!event.ctrlKey||event.altKey||event.metaKey)return;
    const target=event.target;
    if(target&&((target.matches&&target.matches('input,textarea,select,[contenteditable="true"]'))||target.isContentEditable))return;
    const key=String(event.key||''),code=String(event.code||'');
    let next=null;
    if(key==='-'||code==='NumpadSubtract')next=_axNormalizeUiScale(document.documentElement.dataset.uiScale||100)-10;
    else if(key==='+'||key==='='||code==='NumpadAdd')next=_axNormalizeUiScale(document.documentElement.dataset.uiScale||100)+10;
    else if(key==='0'||code==='Numpad0')next=100;
    if(next===null)return;
    event.preventDefault();event.stopPropagation();
    void selectArchiveUiScale(Math.max(70,Math.min(130,next)));
  },true);
}
function _axThemeTransition(){
  const root=document.documentElement;
  if(root.dataset.axReducedMotion==='true')return;
  root.classList.add('ax-theme-transition');
  window.clearTimeout(window.__axThemeTransitionTimer);
  window.__axThemeTransitionTimer=window.setTimeout(()=>root.classList.remove('ax-theme-transition'),180);
}
function _axSyncBrowserThemeColor(){
  const meta=document.querySelector('meta[name="theme-color"]');
  if(!meta)return;
  const rootStyle=getComputedStyle(document.documentElement);
  const bodyStyle=document.body?getComputedStyle(document.body):null;
  const color=[
    rootStyle.getPropertyValue('--ax2-bg').trim(),
    rootStyle.getPropertyValue('--ax-design-bg').trim(),
    bodyStyle?.backgroundColor||''
  ].find(value=>value&&(!window.CSS?.supports||window.CSS.supports('color',value)));
  if(color)meta.content=color;
}
function _axInstallBrowserThemeColorSync(){
  const root=document.documentElement;
  const schedule=()=>window.requestAnimationFrame(_axSyncBrowserThemeColor);
  schedule();
  new MutationObserver(schedule).observe(root,{
    attributes:true,
    attributeFilter:['data-theme','data-theme-mode','data-theme-preset','data-ax-custom-theme','style']
  });
}
function _axSetAppearanceStatus(message,isError){
  const node=$('ax-appearance-status');
  if(node){node.textContent=message||'';node.classList.toggle('error',!!isError);}
}
function applyArchiveThemePreset(preset,modeOverride){
  preset=_axNormalizeThemePreset(preset,localStorage.getItem('ax_theme_mode')||'dark');
  const root=document.documentElement;
  const requested=_axNormalizeThemeMode(modeOverride||root.dataset.themeMode||root.dataset.theme||localStorage.getItem('ax_theme_mode')||'dark');
  const theme=_axThemeForMode(requested);
  if(preset==='custom'){
    if(typeof window.axApplyCustomThemeMode==='function'&&window.axApplyCustomThemeMode(theme)){
      try{localStorage.setItem('ax_theme_preset','custom');localStorage.setItem('ax_theme_mode',requested);}catch(e){}
      const legacy=$('v55-theme-mode');if(legacy)legacy.value=requested;
      if(typeof window.axSyncThemeModeToggle==='function')window.axSyncThemeModeToggle();
      return 'custom';
    }
    preset='nord';
  }
  const presetInfo=_axThemePresets[preset];
  _axThemeTransition();
  root.dataset.theme=theme;
  root.dataset.themeMode=theme;
  root.dataset.themePreset=preset;
  root.style.colorScheme=theme;
  try{localStorage.setItem('ax_theme_preset',preset);localStorage.setItem('ax_theme_mode',requested);}catch(e){}
  const legacy=$('v55-theme-mode');if(legacy)legacy.value=requested;
  document.querySelectorAll('[data-theme-option]').forEach(button=>{
    const active=button.dataset.themeOption===preset;
    button.classList.toggle('active',active);button.setAttribute('aria-pressed',active?'true':'false');
  });
  const st=$('v55-theme-status');if(st)st.textContent=requested==='system'?`System: ${theme==='light'?'Light':'Dark'} right now`:`Current: ${presetInfo.name} · ${theme==='light'?'Light':'Dark'}`;
  if(typeof window.axSyncThemeModeToggle==='function')window.axSyncThemeModeToggle();
  return preset;
}
function applyArchiveTheme(mode){
  mode=_axNormalizeThemeMode(mode);
  const theme=_axThemeForMode(mode),root=document.documentElement;
  if(root.dataset.themePreset==='custom'&&typeof window.axApplyCustomThemeMode==='function'&&window.axApplyCustomThemeMode(theme)){
    try{localStorage.setItem('ax_theme_mode',mode);localStorage.setItem('ax_theme_preset','custom');}catch(e){}
    const legacy=$('v55-theme-mode');if(legacy)legacy.value=mode;
    const st=$('v55-theme-status');if(st)st.textContent=mode==='system'?`System: ${theme==='light'?'Light':'Dark'} right now`:`Custom theme · ${theme==='light'?'Light':'Dark'}`;
    if(typeof window.axSyncThemeModeToggle==='function')window.axSyncThemeModeToggle();
    return 'custom';
  }
  const preset=_axNormalizeThemePreset(root.dataset.themePreset||localStorage.getItem('ax_theme_preset')||'nord',mode);
  const applied=applyArchiveThemePreset(preset,mode);
  const st=$('v55-theme-status');if(st)st.textContent=mode==='system'?`System: ${theme==='light'?'Light':'Dark'} right now`:`Current: ${_axThemePresets[applied].name} · ${theme==='light'?'Light':'Dark'}`;
  if(typeof window.axSyncThemeModeToggle==='function')window.axSyncThemeModeToggle();
  return applied;
}
let _axThemeModeSaveTimer=0;
function _axWarnThemePersistOnce(error){if(window.__axThemePersistWarned)return;window.__axThemePersistWarned=true;console.warn('Archive X could not persist the theme preference; the local choice remains active.',error);}
function _axQueueThemeModeSave(){clearTimeout(_axThemeModeSaveTimer);_axThemeModeSaveTimer=setTimeout(async()=>{try{const root=document.documentElement;const chosen=_axNormalizeThemeMode($('v55-theme-mode')?.value||localStorage.getItem('ax_theme_mode')||root.dataset.themeMode||'dark');const mode=chosen==='system'?'system':_axThemeForMode(root.dataset.themeMode||root.dataset.theme||chosen);const preset=_axNormalizeThemePreset(root.dataset.themePreset||localStorage.getItem('ax_theme_preset')||'nord',mode);await _axPersistAppearance({theme_mode:mode,theme_preset:preset});}catch(e){_axWarnThemePersistOnce(e);}},220);}
function onThemeModeChange(){const mode=$('v55-theme-mode')?.value||'system';applyArchiveTheme(mode);_axQueueThemeModeSave();}
function applyArchiveGridDensity(density,{persist=true}={}){
  density=_axNormalizeGridDensity(density);
  document.documentElement.dataset.gridDensity=density;
  if(persist)try{localStorage.setItem('ax_grid_density',density);}catch(e){}
  document.querySelectorAll('[data-density-option]').forEach(button=>{
    const active=button.dataset.densityOption===density;
    button.classList.toggle('active',active);button.setAttribute('aria-pressed',active?'true':'false');
  });
  return density;
}
let _axAppearanceSaveChain=Promise.resolve();
function _axPersistAppearance(partial){
  _axAppearanceSaveChain=_axAppearanceSaveChain.catch(()=>{}).then(async()=>{
    const response=await api('/api/settings/experience',{method:'POST',body:JSON.stringify(partial)});
    if(!response||response._ok===false)throw new Error(response?.detail||'Could not save appearance preference');
    return response;
  });
  return _axAppearanceSaveChain;
}
async function selectArchiveThemePreset(preset){
  preset=_axNormalizeThemePreset(preset);
  const mode=document.documentElement.dataset.theme==='light'?'light':'dark';
  const preference=localStorage.getItem('ax_theme_mode')==='system'?'system':mode;
  applyArchiveThemePreset(preset,preference);
  if(preference==='system')try{localStorage.setItem('ax_theme_mode','system');}catch(e){}
  _axSetAppearanceStatus(`Applying ${_axThemePresets[preset].name} (${mode})…`);
  try{
    await _axPersistAppearance({theme_preset:preset,theme_mode:preference});
    _axSetAppearanceStatus(`${_axThemePresets[preset].name} · ${mode} saved.`);
  }catch(e){_axWarnThemePersistOnce(e);_axSetAppearanceStatus(`${_axThemePresets[preset].name} applied locally; server save failed.`,true);}
}

_axBindUiScaleShortcuts();
function _axBindSystemTheme(){
  try{
    const mq=window.matchMedia('(prefers-color-scheme: dark)');
    const update=()=>{if(_axNormalizeThemeMode(localStorage.getItem('ax_theme_mode')||'system')==='system')applyArchiveTheme('system');};
    if(mq.addEventListener)mq.addEventListener('change',update);else if(mq.addListener)mq.addListener(update);
  }catch(e){}
}
_axBindSystemTheme();
async function setResumePromptPreference(enabled){
  const checkbox=$('v55-resume-prompt');
  const previous=_resumePromptEnabled;
  const next=!!enabled;
  _resumePromptEnabled=next;
  // If the preference is switched off while a resume prompt is pending, dismiss it.
  if(!next){
    _axResumePending=null;
    const dlg=$('ax-resume-dialog');
    if(dlg){dlg.style.display='none';dlg.setAttribute('aria-hidden','true');}
  }
  const msg=$('v55-exp-msg');
  if(msg)msg.textContent='Saving resume preference…';
  try{
    const r=await api('/api/settings/experience',{method:'POST',body:JSON.stringify({resume_prompt:next})});
    if(!r||r._ok===false)throw new Error(r?.detail||'save failed');
    const status=next?'Resume prompt enabled and saved.':'Resume prompt disabled and saved.';
    if(msg){msg.textContent=status;setTimeout(()=>{if(msg.textContent===status)msg.textContent='';},2400);}
  }catch(e){
    _resumePromptEnabled=previous;
    if(checkbox)checkbox.checked=previous;
    if(msg)msg.textContent=`Could not save resume preference: ${e?.message||'save failed'}`;
  }
}
async function loadExperienceSettings(){
  try{const r=await api('/api/settings/experience');if(r&&r._ok===false)throw new Error(r.detail||'failed');
    $('v55-autoplay').checked=!!r.autoplay;$('v55-autonext').checked=!!r.autonext;if($('v55-resume-prompt'))$('v55-resume-prompt').checked=r.resume_prompt!==false;_resumePromptEnabled=r.resume_prompt!==false;if($('v55-live-seek-preview'))$('v55-live-seek-preview').checked=r.live_seek_preview!==false;if($('v55-partial-cache-prompt'))$('v55-partial-cache-prompt').checked=r.partial_cache_prompt===true;window.__AX_PARTIAL_CACHE_PROMPT=r.partial_cache_prompt===true;window.AXNoBufPlayback?.setPartialCachePrompt?.(window.__AX_PARTIAL_CACHE_PROMPT);$('v55-autonext-mode').value=r.autonext_mode||'context'; _liveSeekPreviewEnabled=r.live_seek_preview!==false; try{localStorage.setItem('ta_live_seek_preview',_liveSeekPreviewEnabled?'1':'0');}catch(e){}
    // V29.4.131: keep playback preferences in step with saved account-wide settings.
    setOpt('ta_autoplay',!!r.autoplay);setOpt('ta_autonext',!!r.autonext);try{localStorage.setItem('ta_autonext_mode',r.autonext_mode||'context');}catch(e){}
    const root=document.documentElement;
    const hasBootstrappedTheme=!!(root.dataset.themeMode||root.dataset.theme);
    const localPreference=_axNormalizeThemeMode(localStorage.getItem('ax_theme_mode')||(!hasBootstrappedTheme?r.theme_mode:'')||root.dataset.themeMode||root.dataset.theme||'dark');
    const preset=_axNormalizeThemePreset(root.dataset.themePreset||localStorage.getItem('ax_theme_preset')||r.theme_preset||'nord',localPreference);
    const savedMode=hasBootstrappedTheme ? _axThemeForMode(root.dataset.themeMode||root.dataset.theme) : _axThemeForMode(localPreference);
    if($('v55-theme-mode'))$('v55-theme-mode').value=localPreference;
    if(!hasBootstrappedTheme)applyArchiveThemePreset(preset,localPreference);
    else {
      document.querySelectorAll('[data-theme-option]').forEach(button=>{const active=button.dataset.themeOption===preset;button.classList.toggle('active',active);button.setAttribute('aria-pressed',active?'true':'false');});
      const info=_axThemePresets[preset];const status=$('v55-theme-status');if(status&&info)status.textContent=`Current: ${info.name} · ${savedMode==='light'?'Light':'Dark'}`;
      if(typeof window.axSyncThemeModeToggle==='function')window.axSyncThemeModeToggle();
    }
    const scale = _axNormalizeUiScale(r.ui_scale ?? localStorage.getItem('ax_ui_scale') ?? _axScaleFromLegacyDensity(r.grid_density||localStorage.getItem('ax_grid_density')||'default'));
    applyArchiveUiScale(scale);
    applyArchiveGridDensity(_axNormalizeGridDensity(r.grid_density||localStorage.getItem('ax_grid_density')||'default'),{persist:false});
    try{localStorage.setItem('ax_ui_scale',String(scale));localStorage.setItem('ax_grid_density',_axNormalizeGridDensity(r.grid_density||localStorage.getItem('ax_grid_density')||'default'));}catch(e){}
    _axSetAppearanceStatus('Appearance preferences are saved automatically.');
  }catch(e){
    const root=document.documentElement;
    const preset=_axNormalizeThemePreset(root.dataset.themePreset||localStorage.getItem('ax_theme_preset')||'nord',localStorage.getItem('ax_theme_mode')||'system');
    if(!root.dataset.themeMode&&!root.dataset.theme)applyArchiveThemePreset(preset,_axThemeForMode(localStorage.getItem('ax_theme_mode')||'dark'));
    else {if($('v55-theme-mode'))$('v55-theme-mode').value=localStorage.getItem('ax_theme_mode')==='system'?'system':_axThemeForMode(root.dataset.themeMode||root.dataset.theme);document.querySelectorAll('[data-theme-option]').forEach(button=>{const active=button.dataset.themeOption===preset;button.classList.toggle('active',active);button.setAttribute('aria-pressed',active?'true':'false');});}
    const legacyScale=localStorage.getItem('ax_ui_scale')||_axScaleFromLegacyDensity(localStorage.getItem('ax_grid_density')||'default');
    applyArchiveUiScale(legacyScale);
    applyArchiveGridDensity(_axNormalizeGridDensity(localStorage.getItem('ax_grid_density')||'default'),{persist:false});
    _axSetAppearanceStatus('Using saved local appearance preferences.');
  }
}
async function saveExperienceSettings(){
  const msg=$('v55-exp-msg');try{
    const themePreset=_axNormalizeThemePreset(document.documentElement.dataset.themePreset||localStorage.getItem('ax_theme_preset'));
    const themePreference=_axNormalizeThemeMode($('v55-theme-mode')?.value||localStorage.getItem('ax_theme_mode')||document.documentElement.dataset.themeMode||'dark');
    const themeMode=_axThemeForMode(themePreference);
    const uiScale=_axNormalizeUiScale(document.documentElement.dataset.uiScale||localStorage.getItem('ax_ui_scale')||100);
    const density=_axNormalizeGridDensity(document.documentElement.dataset.gridDensity||localStorage.getItem('ax_grid_density')||'default');
    const r=await api('/api/settings/experience',{method:'POST',body:JSON.stringify({autoplay:$('v55-autoplay').checked,autonext:$('v55-autonext').checked,resume_prompt:$('v55-resume-prompt')?.checked!==false,live_seek_preview:$('v55-live-seek-preview')?.checked!==false,partial_cache_prompt:$('v55-partial-cache-prompt')?.checked===true,autonext_mode:$('v55-autonext-mode').value,theme_mode:themePreference,theme_preset:themePreset,grid_density:density,ui_scale:uiScale})});
    if(!r||r._ok===false)throw new Error(r?.detail||'save failed');
    setOpt('ta_autoplay',$('v55-autoplay').checked);setOpt('ta_autonext',$('v55-autonext').checked);_resumePromptEnabled=$('v55-resume-prompt')?.checked!==false;_liveSeekPreviewEnabled=$('v55-live-seek-preview')?.checked!==false;window.__AX_PARTIAL_CACHE_PROMPT=$('v55-partial-cache-prompt')?.checked===true;window.AXNoBufPlayback?.setPartialCachePrompt?.(window.__AX_PARTIAL_CACHE_PROMPT);
    try{localStorage.setItem('ta_autonext_mode',$('v55-autonext-mode').value);localStorage.setItem('ta_live_seek_preview',_liveSeekPreviewEnabled?'1':'0');}catch(e){}
    applyArchiveThemePreset(themePreset,themePreference);if(themePreference==='system')try{localStorage.setItem('ax_theme_mode','system');}catch(e){}applyArchiveUiScale(uiScale);applyArchiveGridDensity(density,{persist:false});msg.textContent='Saved.';setTimeout(()=>{if(msg)msg.textContent='';},1800);
  }catch(e){if(msg)msg.textContent=e?.message||'Could not save.';}
}
function _fmtBytes(n){n=Number(n||0);if(!n)return '0 B';const u=['B','KB','MB','GB','TB','PB'];let i=0;while(n>=1024&&i<u.length-1){n/=1024;i++;}return `${n.toFixed(i>=3?2:i?1:0)} ${u[i]}`;}
const _AX_SIDEBAR_STORAGE_CACHE_KEY='ax_sidebar_storage_summary_v1';
let _axSidebarStorageFetch=null;
function _axRenderSidebarStorageSummary(r,{cache=true}={}){
  if(!r||r._ok===false)return false;
  const card=$('ax-sidebar-storage-card'),pctEl=$('ax-storage-pct'),bar=$('ax-storage-bar'),fill=$('ax-storage-fill'),data=$('ax-storage-data'),freeLine=$('ax-storage-free');
  if(!card||!pctEl||!bar||!fill||!data||!freeLine)return false;
  const d=r.disk||{},total=Math.max(0,Number(d.total)||0),free=Math.max(0,Number(d.free)||0);
  const used=Math.max(0,Number(d.used)||(total-free)),pct=total?Math.max(0,Math.min(100,Math.round(used/total*100))):0;
  const files=Math.max(0,Number(r.files)||0),bytes=Math.max(0,Number(r.bytes)||0);
  data.textContent=`App data: ${_fmtBytes(bytes)} · ${files.toLocaleString()} files`;
  freeLine.textContent=total?`${_fmtBytes(free)} free of ${_fmtBytes(total)}`:'Drive capacity unavailable';
  pctEl.textContent=total?`${pct}% used`:'—';
  fill.style.width=`${pct}%`;
  bar.setAttribute('aria-valuenow',String(pct));
  bar.setAttribute('aria-valuetext',total?`${pct}% of drive space used; ${_fmtBytes(free)} free`:'Drive space unavailable');
  if(r.current_path){card.title=`Current data folder: ${r.current_path}`;card.dataset.currentPath=String(r.current_path);}
  card.dataset.loaded='true';
  if(cache){try{localStorage.setItem(_AX_SIDEBAR_STORAGE_CACHE_KEY,JSON.stringify({current_path:r.current_path||'',files,bytes,disk:{total,free,used},saved_at:Date.now()}));}catch(_){} }
  return true;
}
function _axLoadCachedSidebarStorageSummary(){
  try{const raw=localStorage.getItem(_AX_SIDEBAR_STORAGE_CACHE_KEY);if(!raw)return false;const r=JSON.parse(raw);if(!r||!r.disk||!Number.isFinite(Number(r.bytes))||!Number.isFinite(Number(r.files)))return false;return _axRenderSidebarStorageSummary(r,{cache:false});}catch(_){return false;}
}
function _axShowSidebarStorageUnavailable(){
  if($('ax-storage-data'))$('ax-storage-data').textContent='App data size unavailable';
  if($('ax-storage-free'))$('ax-storage-free').textContent='Drive space unavailable';
  if($('ax-storage-pct'))$('ax-storage-pct').textContent='—';
  if($('ax-storage-fill'))$('ax-storage-fill').style.width='0%';
  if($('ax-storage-bar')){$('ax-storage-bar').setAttribute('aria-valuenow','0');$('ax-storage-bar').setAttribute('aria-valuetext','Drive space unavailable');}
}
async function loadSidebarStorageSummary(){
  if(_axSidebarStorageFetch)return _axSidebarStorageFetch;
  const hasCache=_axLoadCachedSidebarStorageSummary();
  _axSidebarStorageFetch=(async()=>{
    try{
      const r=await api('/api/settings/storage',{cache:'no-store'});
      if(!r||r._ok===false)throw new Error(r?.detail||'Storage status unavailable');
      _axRenderSidebarStorageSummary(r);
      return r;
    }catch(e){if(!hasCache)_axShowSidebarStorageUnavailable();return null;}
  })();
  try{return await _axSidebarStorageFetch;}finally{_axSidebarStorageFetch=null;}
}
function initSidebarStorageSummary(){loadSidebarStorageSummary().catch(()=>_axShowSidebarStorageUnavailable());}
if(document.readyState==='loading')document.addEventListener('DOMContentLoaded',initSidebarStorageSummary,{once:true});else initSidebarStorageSummary();
async function loadStorageSettings(){
  try{
    const r=await api('/api/settings/storage'); if(!r||r._ok===false)throw new Error(r?.detail||'Storage status unavailable');
    _axRenderSidebarStorageSummary(r);
    const cur=$('v55-storage-current'),size=$('v55-storage-size'),disk=$('v55-storage-disk'),pending=$('v55-storage-pending'),warn=$('v55-storage-warning'),dest=$('v55-storage-destination');
    if(cur)cur.textContent=r.current_path||'—';
    if(size)size.textContent=`Archive X data: ${_fmtBytes(r.bytes)} · ${Number(r.files||0).toLocaleString()} files`;
    if(disk){const d=r.disk||{};disk.textContent=d.total?`Drive: ${_fmtBytes(d.free)} free of ${_fmtBytes(d.total)}`:'Drive capacity unavailable';}
    if(dest&&!dest.value&&r.pending_path)dest.value=r.pending_path;
    if(pending)pending.textContent=r.pending_path?`Pending move: ${r.pending_path} · restart Archive X to complete it.`:'';
    if(warn){
      if(r.environment_override){warn.style.display='block';warn.textContent='ARCHIVE_X_DATA_DIR (or the legacy TELEGRAM_ARCHIVE_DATA_DIR) is set in the environment, so the environment path takes precedence over this setting.';}
      else if(r.blocking_jobs){warn.style.display='block';warn.textContent=`${r.blocking_jobs} running/queued job(s) must be paused or stopped before moving the data folder. Paused jobs can remain paused.`;}
      else if(r.pending_path){warn.style.display='block';warn.textContent='The move is staged safely. On the next clean startup Archive X will copy the complete data tree, verify the copy, then remove the old folder.';}
      else {warn.style.display='none';}
    }
  }catch(e){const m=$('v55-storage-msg');if(m)m.textContent=e?.message||'Could not read storage settings.';}
}
async function browseStorageFolder(){
  const msg=$('v55-storage-msg');if(msg)msg.textContent='Opening folder picker…';
  try{const r=await api('/api/settings/storage/browse',{method:'POST',body:'{}'});if(r?._ok===false)throw new Error(r.detail||'Folder picker unavailable');if(r.selected_path&&$('v55-storage-destination'))$('v55-storage-destination').value=r.selected_path;if(msg)msg.textContent=r.selected_path?'Folder selected.':'No folder selected.';}catch(e){if(msg)msg.textContent=e?.message||'Folder picker unavailable.';}
}
async function moveArchiveData(){
  const msg=$('v55-storage-msg'),dest=$('v55-storage-destination')?.value?.trim();
  if(!dest){if(msg)msg.textContent='Select a destination folder first.';return;}
  if(!(await axConfirm(`Move all Archive X app data to:\n\n${dest}\n\nThe current data will be copied and verified on the next restart, then the old folder will be removed. Continue?`,{title:'Move Archive X data folder',okLabel:'Prepare move',confirmLabel:'Prepare move',danger:true})))return;
  if(msg)msg.textContent='Preparing data move…';
  try{const r=await api('/api/settings/storage/move',{method:'POST',body:JSON.stringify({destination:dest})});if(!r||r._ok===false)throw new Error(r?.detail||'Could not prepare data move');if(msg)msg.textContent='Move prepared. Restart Archive X to complete it.';loadStorageSettings();}catch(e){if(msg)msg.textContent=e?.message||'Could not prepare data move.';}
}
async function cancelStorageMove(){
  const msg=$('v55-storage-msg');try{const r=await api('/api/settings/storage/cancel-move',{method:'POST',body:'{}'});if(!r||r._ok===false)throw new Error(r?.detail||'Could not cancel move');if($('v55-storage-destination'))$('v55-storage-destination').value='';if(msg)msg.textContent='Pending move cancelled.';loadStorageSettings();}catch(e){if(msg)msg.textContent=e?.message||'Could not cancel move.';}
}
async function loadBackupStatus(){
  const box=$('backup-history'),msg=$('backup-status-msg'); if(!box)return;
  try{const r=await api('/api/settings/backup'); const s=r.settings||{};
    if($('backup-repository'))$('backup-repository').value=s.repository||'';
    if($('backup-enabled'))$('backup-enabled').checked=!!s.enabled; if($('backup-schedule-time'))$('backup-schedule-time').value=s.schedule_time||'02:00';
    ['daily','weekly','monthly'].forEach(k=>{const e=$('backup-retention-'+k);if(e)e.value=s['retention_'+k]??(k==='daily'?7:k==='weekly'?4:3);});
    if(msg)msg.textContent=s.last_error?('Last backup error: '+s.last_error):(s.last_backup?('Last verified backup: '+s.last_backup):'No verified backup has been created yet.');
    const rows=r.backups||[];
    box.innerHTML=rows.length?rows.map(x=>`<div class="backup-history-row"><div><b>${esc(x.name)}</b><span class="muted">${esc(x.created_at||'')} · ${Number(x.size||0).toLocaleString()} bytes · ${Number(x.files||0).toLocaleString()} files</span></div><div class="backup-history-actions"><span class="${x.verified?'v55-ok':'v55-warn'}">${x.verified?'VERIFIED':'CHECK'}</span><button class="btn xs" onclick='verifyArchiveBackup(${JSON.stringify(x.path)})'>Verify</button><button class="btn xs danger" onclick='deleteArchiveBackup(${JSON.stringify(x.path)})'>Delete</button></div></div>`).join(''):'<div class="muted">No Archive X backups found.</div>';
  }catch(e){if(box)box.innerHTML='<div class="muted">Backup status unavailable.</div>';if(msg)msg.textContent=e?.message||'Backup status unavailable.';}
}
async function saveBackupSettings(){
  const msg=$('backup-status-msg'); try{const r=await api('/api/settings/backup',{method:'POST',body:JSON.stringify({enabled:$('backup-enabled')?.checked,repository:$('backup-repository')?.value,daily:Number($('backup-retention-daily')?.value||7),weekly:Number($('backup-retention-weekly')?.value||4),monthly:Number($('backup-retention-monthly')?.value||3),schedule_time:$('backup-schedule-time')?.value||'02:00'})}); if(msg)msg.textContent='Backup settings saved.'; await loadBackupStatus();}catch(e){if(msg)msg.textContent=e?.message||'Could not save backup settings.';}
}
async function createArchiveBackup(){
  const password=$('backup-password')?.value||''; const msg=$('backup-status-msg');
  if(password && password.length<8){if(msg)msg.textContent='Backup password must be at least 8 characters.';return;}
  try{const r=await api('/api/backup/create',{method:'POST',body:JSON.stringify({backup_type:$('backup-type')?.value||'full_application',password,repository:$('backup-repository')?.value||''})}); if(msg)msg.textContent=`Backup queued: ${r.id}`; toast('Backup queued in Operations.'); $('backup-password').value='';}catch(e){if(msg)msg.textContent=e?.message||'Could not create backup.';}
}
async function verifyArchiveBackup(path){
  try{const password=await axPrompt('Enter the backup password, or leave blank if the backup is not encrypted.','',{title:'Verify backup',okLabel:'Verify backup',placeholder:'Backup password',inputLabel:'Backup password'});if(password===null)return;const r=await api('/api/backup/verify',{method:'POST',body:JSON.stringify({path,password})}); toast(`Backup verified: ${Number(r.manifest?.files?.length||0).toLocaleString()} files.`);await loadBackupStatus();}catch(e){toast(e?.message||'Backup verification failed.');}
}
async function deleteArchiveBackup(path){
  if(!(await axConfirm('Delete this Archive X backup? This action cannot be undone.',{title:'Delete backup',okLabel:'Delete backup',confirmLabel:'Delete backup',danger:true})))return; try{await api('/api/backup/delete',{method:'POST',body:JSON.stringify({path})});await loadBackupStatus();}catch(e){toast(e?.message||'Could not delete backup.');}
}
async function previewArchiveRestore(){
  const box=$('restore-preview'); try{const r=await api('/api/backup/restore/preview',{method:'POST',body:JSON.stringify({path:$('restore-path')?.value||'',password:$('restore-password')?.value||''})}); const m=r.manifest||{}; if(box)box.innerHTML=`<b>Valid backup</b> · ${esc(m.application_version||'')} · ${Number(m.files?.length||0).toLocaleString()} files · ${m.encrypted?'encrypted':'not encrypted'} · restart required. ${r.active_jobs?'Stop active jobs before restore.':'Ready to stage.'}`;}catch(e){if(box)box.textContent=e?.message||'Backup validation failed.';}
}
async function restoreArchiveBackup(){
  const box=$('restore-preview'); if(!(await axConfirm('Stage this backup for restore? Archive X will first create a safety backup and apply the restore on the next restart.',{title:'Stage backup restore',okLabel:'Stage restore',confirmLabel:'Stage restore',danger:true})))return;
  try{const r=await api('/api/backup/restore',{method:'POST',body:JSON.stringify({path:$('restore-path')?.value||'',password:$('restore-password')?.value||''})}); if(box)box.textContent='Restore staged successfully. Restart Archive X to apply it.';toast('Restore staged. Restart Archive X to apply.');}catch(e){if(box)box.textContent=e?.message||'Could not stage restore.';}
}

function _updateProviderStatusRail(){
  const track=$('v55-provider-track');if(!track)return;
  const slides=Array.from(track.children),trackRect=track.getBoundingClientRect();
  const visible=slides.filter(slide=>{const rect=slide.getBoundingClientRect();return rect.right>trackRect.left+1&&rect.left<trackRect.right-1;}).slice(0,4);
  const range=$('v55-provider-range');
  if(range)range.textContent=visible.length?`${visible[0].dataset.providerIndex}–${visible[visible.length-1].dataset.providerIndex} of ${slides.length}`:`0 of ${slides.length}`;
  const previous=$('v55-provider-prev'),next=$('v55-provider-next');
  if(previous)previous.disabled=track.scrollLeft<=16;
  if(next)next.disabled=track.scrollLeft+track.clientWidth>=track.scrollWidth-1;
}
function _scrollProviderStatusRail(direction){
  const track=$('v55-provider-track'),first=track?.querySelector('.v55-provider-slide');if(!track||!first)return;
  const gap=parseFloat(getComputedStyle(track).columnGap)||10;
  const stepSize=first.getBoundingClientRect().width+gap;
  const visibleCount=Math.max(1,Math.floor((track.clientWidth+gap)/stepSize));
  const steps=Math.max(1,visibleCount-1);
  track.scrollBy({left:Math.sign(Number(direction)||1)*steps*stepSize,behavior:'smooth'});
}
async function loadProviderDashboard(){
  const box=$('v55-provider-grid');if(!box)return;
  box.classList.add('v55-provider-status-rail');
  box.innerHTML=window.AXSkeleton?window.AXSkeleton.tiles(4):'<div class="muted">Loading provider status…</div>';
  try{
    const r=await api('/api/settings/providers'),cards=[];
    (r.providers||[]).forEach(p=>cards.push(`<div class="v55-data-card"><span class="k">${esc((p.provider||'provider').toUpperCase())} · ${esc(p.entity_type||'scene')}</span><div class="v">${esc(p.status||'IDLE')}</div><div class="muted" style="font-size:11px;margin-top:4px">Last sync: ${esc(p.last_sync_at||'Never')} · Requests: ${Number(p.request_count||0)}${p.error?`<br><span class="v55-prov-err" title="${esc(p.error)}" style="color:var(--amber)">${esc(p.error)}</span>`:''}</div></div>`));
    Object.entries(r.broker||{}).forEach(([k,p])=>cards.push(`<div class="v55-data-card"><span class="k">${esc(k==='tpdb'?'THEPORNDB':'STASHDB')} · API broker</span><div class="v">${Number(p.active_slots||0)}/${Number(p.max_concurrency||0)} active</div><div class="muted" style="font-size:11px;margin-top:4px">Requests: ${Number(p.requests||0)} · Deduplicated: ${Number(p.deduplicated||0)} · Retries: ${Number(p.retries||0)} · Cooldown: ${Number(p.cooldown_seconds||0).toFixed(1)}s</div></div>`));
    cards.push(`<div class="v55-data-card"><span class="k">Local metadata cache</span><div class="v">${Number(r.metadata_cache_entries||0).toLocaleString()}</div><div class="muted" style="font-size:11px;margin-top:4px">Provider responses kept locally to avoid repeat lookups.</div></div>`);
    cards.push(`<div class="v55-data-card"><span class="k">Artwork cache</span><div class="v">${Number(r.artwork_cache_entries||0).toLocaleString()}</div><div class="muted" style="font-size:11px;margin-top:4px">Performer and studio images are served locally when cached.</div></div>`);
    if(!cards.length){box.innerHTML='<div class="muted">No provider sync state has been recorded yet.</div>';return;}
    box.innerHTML=`<div class="v55-provider-rail-controls"><span class="v55-provider-range" id="v55-provider-range" role="status" aria-live="polite"></span><button class="btn xs" type="button" id="v55-provider-prev" onclick="_scrollProviderStatusRail(-1)" aria-label="Previous provider status cards" title="Previous cards">‹</button><button class="btn xs" type="button" id="v55-provider-next" onclick="_scrollProviderStatusRail(1)" aria-label="Next provider status cards" title="Next cards">›</button></div><div class="v55-provider-track" id="v55-provider-track" role="region" aria-label="Provider status cards" aria-roledescription="carousel" tabindex="0">${cards.map((card,index)=>`<div class="v55-provider-slide" data-provider-index="${index+1}" role="group" aria-label="Status ${index+1} of ${cards.length}">${card}</div>`).join('')}</div>`;
    const track=$('v55-provider-track');
    track?.addEventListener('scroll',_updateProviderStatusRail,{passive:true});
    track?.addEventListener('keydown',event=>{if(event.key==='ArrowRight'||event.key==='ArrowLeft'){event.preventDefault();_scrollProviderStatusRail(event.key==='ArrowRight'?1:-1);}});
    requestAnimationFrame(_updateProviderStatusRail);
  }catch(e){box.innerHTML='<div class="muted">Provider status unavailable.</div>';}
}
// V29.4.131: the favourite button shows its state (filled star, "Favourite"
// vs "Add to favourites"); both states used to draw the same outline star.
function entityFavButton(kind,id,on,iconOnly=false){
  const label=on?'Remove from favourites':'Add to favourites';
  return `<button type="button" class="btn sm entity-fav-btn${on?' on':''}${iconOnly?' icon-only':''}" aria-pressed="${on?'true':'false'}" aria-label="${label}" title="${label}" onclick="toggleEntityPageFavorite('${kind}',${Number(id)},this)">${entityFavInner(kind,on,iconOnly)}</button>`;
}
function entityFavInner(kind,on,iconOnly=false){return iconOnly?axIcon('star',on?'fill':''):`${axIcon('star')} <span>${on?'Favourite':'Add to favourites'}</span>`;}
async function toggleEntityPageFavorite(kind,id,btn){try{const r=await api(`/api/entities/${kind}/${Number(id)}/favorite`,{method:'POST',body:'{}'});if(r?.ok){const on=!!r.favorite;const label=on?'Remove from favourites':'Add to favourites';btn.classList.toggle('on',on);btn.setAttribute('aria-pressed',on?'true':'false');btn.setAttribute('aria-label',label);btn.title=label;btn.innerHTML=entityFavInner(kind,on,btn.classList.contains('icon-only'));}}catch(e){toast('Could not update favourite.');}}
const _v55MarkerSyncRequests = new Map();
async function syncV55ProviderMarkers(mediaId, force=false){
  const id=Number(mediaId||0);if(!id)return {_ok:false,detail:'Video is not indexed.'};
  const key=String(id);
  if(_v55MarkerSyncRequests.has(key))return _v55MarkerSyncRequests.get(key);
  const pending=api(`/api/media/${id}/markers/sync`,{method:'POST',body:JSON.stringify({force:!!force})});
  _v55MarkerSyncRequests.set(key,pending);
  try{return await pending;}finally{if(_v55MarkerSyncRequests.get(key)===pending)_v55MarkerSyncRequests.delete(key);}
}
function clearPlayerSceneChapterTrack(){
  const holder=$('pchapter-segments'), seek=$('pseek');
  if(holder)holder.replaceChildren();
  if(seek)seek.classList.remove('has-chapters');
  const tip=$('pprevt');if(tip)tip.title='';
}
function _normalisePlayerSceneChapters(rows,duration){
  const limit=Number(duration)||0, seen=new Set();
  if(!Array.isArray(rows)||limit<=0)return [];
  return rows.map(m=>({position:Number(m?.position),title:String(m?.title||'Scene').trim()||'Scene',note:String(m?.note||'').trim()}))
    .filter(m=>Number.isFinite(m.position)&&m.position>=0&&m.position<limit)
    .sort((a,b)=>a.position-b.position)
    .filter(m=>{const k=m.position.toFixed(3);if(seen.has(k))return false;seen.add(k);return true;});
}
function renderPlayerSceneChapterTrack(key,mediaId,rows){
  if(key!==_P.mediaKey)return;
  const holder=$('pchapter-segments'),seek=$('pseek');if(!holder||!seek)return;
  const duration=Number(pDur()||_P.dur||_dv?.duration||0);
  const chapters=_normalisePlayerSceneChapters(rows,duration);
  holder.replaceChildren();
  holder.dataset.mediaKey=key;holder.dataset.mediaId=String(Number(mediaId||0));
  if(!duration||!chapters.length){seek.classList.remove('has-chapters');return;}
  const segments=[];
  if(chapters[0].position>0.15)segments.push({start:0,end:chapters[0].position,title:'Opening',intro:true});
  chapters.forEach((chapter,index)=>{
    const next=chapters[index+1];
    segments.push({start:chapter.position,end:next?next.position:duration,title:chapter.title,note:chapter.note,intro:false});
  });
  segments.forEach((segment,index)=>{
    const start=Math.max(0,Math.min(duration,segment.start)),end=Math.max(start,Math.min(duration,segment.end));
    if(end-start<0.05)return;
    const el=document.createElement('span');el.className='pchapter-segment'+(segment.intro?' is-opening':'');
    el.dataset.index=String(index);el.dataset.start=String(start);el.dataset.end=String(end);el.dataset.title=segment.title;
    el.style.left=`${(start/duration)*100}%`;el.style.width=`calc(${((end-start)/duration)*100}% - 2px)`;
    holder.appendChild(el);
  });
  chapters.forEach(chapter=>{
    if(chapter.position<=0.05)return;
    const marker=document.createElement('span');marker.className='pchapter-divider';
    marker.style.left=`${(chapter.position/duration)*100}%`;
    marker.setAttribute('aria-label',`${fdur(chapter.position)||'0:00'} · ${chapter.title}`);
    holder.appendChild(marker);
  });
  seek.classList.toggle('has-chapters',chapters.length>0);
  updatePlayerSceneChapterHover(pCur());
}
// Hover label only appears when the pointer is on a marker dot (not along the whole scene).
function getPlayerSceneMarkerTitleNear(time){
  const record=_playerSceneChapterCache.get(_P.mediaKey);if(!record||!Array.isArray(record.markers))return '';
  const dur=Number(pDur()||_P.dur||_dv?.duration||0),seek=$('pseek');if(!dur||!seek)return '';
  const width=Math.max(120,seek.getBoundingClientRect().width||0),tol=Math.max(0.5,(8/width)*dur),t=Math.max(0,Number(time)||0);
  let best=null,bestD=Infinity;
  for(const m of _normalisePlayerSceneChapters(record.markers,dur)){
    if(m.position<=0.05)continue;
    const d=Math.abs(m.position-t);if(d<=tol&&d<bestD){best=m;bestD=d;}
  }
  return best?best.title:'';
}
function getPlayerSceneChapterTitle(time){
  const key=_P.mediaKey,record=_playerSceneChapterCache.get(key);if(!record||!Array.isArray(record.markers))return '';
  const t=Math.max(0,Number(time)||0),chapters=_normalisePlayerSceneChapters(record.markers,pDur()||_P.dur||_dv?.duration||0);
  if(chapters.length&&chapters[0].position>0.15&&t<chapters[0].position)return 'Opening';
  let found='';for(const marker of chapters){if(marker.position>t+0.001)break;found=marker.title;}
  return found;
}
function updatePlayerSceneChapterHover(time){
  const holder=$('pchapter-segments');if(!holder)return;
  const t=Math.max(0,Number(time)||0);let match=null;
  holder.querySelectorAll('.pchapter-segment').forEach(el=>{
    const start=Number(el.dataset.start||0),end=Number(el.dataset.end||0),isCurrent=t>=start&&t<end;
    el.classList.toggle('is-hovered',isCurrent);if(isCurrent)match=el;
  });
  const label=$('pprevt');if(label){const title=match?.dataset?.title||getPlayerSceneChapterTitle(t);label.title=title?`${fdur(t)||'0:00'} · ${title}`:'';}
}
function clearPlayerSceneChapterHover(){const holder=$('pchapter-segments');holder?.querySelectorAll('.is-hovered').forEach(el=>el.classList.remove('is-hovered'));}
async function loadPlayerSceneChapters(cid,mid){
  const key=vkey(cid,mid),cached=_playerSceneChapterCache.get(key);
  if(cached){if(key===_P.mediaKey)renderPlayerSceneChapterTrack(key,cached.mediaId,cached.markers);return cached;}
  if(_playerSceneChapterLoads.has(key))return _playerSceneChapterLoads.get(key);
  if(key===_P.mediaKey)clearPlayerSceneChapterTrack();
  const promise=(async()=>{
    try{
      const lookup=await api(`/api/videos/lookup?channel_id=${encodeURIComponent(cid)}&message_id=${Number(mid)}`,{cache:'no-store'});
      const mediaId=Number(lookup?.media_id||0);if(!mediaId){const empty={mediaId:0,markers:[]};_playerSceneChapterCache.set(key,empty);return empty;}
      const result=await api(`/api/media/${mediaId}/markers`,{cache:'no-store'});
      let rows=Array.isArray(result)?result:(result?.markers||[]);
      if(!rows.length){
        const sync=await syncV55ProviderMarkers(mediaId,false);
        if(sync?._ok!==false)rows=Array.isArray(sync?.markers)?sync.markers:rows;
      }
      const value={mediaId,markers:Array.isArray(rows)?rows.map(m=>({...m,position:Number(m.position)||0,title:String(m.title||'').trim()})):[]};
      _playerSceneChapterCache.set(key,value);
      while(_playerSceneChapterCache.size>100)_playerSceneChapterCache.delete(_playerSceneChapterCache.keys().next().value);
      if(key===_P.mediaKey)renderPlayerSceneChapterTrack(key,mediaId,value.markers);
      return value;
    }catch(e){
      const value={mediaId:0,markers:[],error:String(e?.message||e)};
      _playerSceneChapterCache.set(key,value);
      if(key===_P.mediaKey)clearPlayerSceneChapterTrack();
      return value;
    }
  })();
  _playerSceneChapterLoads.set(key,promise);
  try{return await promise;}finally{if(_playerSceneChapterLoads.get(key)===promise)_playerSceneChapterLoads.delete(key);}
}

function renderDetailMarkers(box,rows,mediaId,status='',error=''){
  const markers=Array.isArray(rows)?rows:[];
  if(_dv?.id!=null){const detailKey=vkey(_dv._cid,_dv.id);_playerSceneChapterCache.set(detailKey,{mediaId:Number(mediaId||0),markers:markers.map(m=>({...m,position:Number(m.position)||0,title:String(m.title||'').trim()}))});if(detailKey===_P.mediaKey)renderPlayerSceneChapterTrack(detailKey,mediaId,markers);}
  if(markers.length){
    box.innerHTML=markers.map(m=>`<div class="v55-marker" title="Go to ${esc(fdur(m.position))}${m.note?` · ${esc(m.note)}`:''}"><button type="button" class="v55-marker-chip" onclick="event.stopPropagation();pSeek(${Number(m.position)})" aria-label="Go to ${esc(fdur(m.position))}: ${esc(m.title||'Marker')}"><span class="v55-marker-time">${esc(fdur(m.position))}</span><b>${esc(m.title||'Marker')}</b>${m.tag?`<span class="v55-chip">#${esc(m.tag)}</span>`:''}</button></div>`).join('');
    return;
  }
  let message=error||'No markers yet.';
  if(!error&&status==='NO_PROVIDER_TOKEN')message='No markers yet. Configure the ThePornDB provider token to check provider markers.';
  else if(!error&&status==='NO_TPDB_SCENE')message='No matching provider scene was found. You can still add local markers.';
  else if(!error&&status==='PROVIDER_ERROR')message='Provider markers could not be loaded. Try refreshing them.';
  box.innerHTML=`<div class="muted">${esc(message)}</div><button type="button" class="btn sm v55-marker-refresh" onclick="syncV55MarkerButton(${Number(mediaId)},this)" style="margin-top:5px">Refresh provider markers</button>`;
}
async function syncV55MarkerButton(mediaId,button){
  const box=$('v55-markers');if(!box||Number(box.dataset.mediaId||0)!==Number(mediaId))return;
  const oldText=button?.textContent||'Refresh provider markers';
  if(button){button.disabled=true;button.textContent='Checking provider…';}
  try{
    const result=await syncV55ProviderMarkers(mediaId,true);
    if(box!==$('v55-markers')||Number(box.dataset.mediaId||0)!==Number(mediaId))return;
    if(result?._ok===false)renderDetailMarkers(box,[],mediaId,'PROVIDER_ERROR',result.detail||'Provider marker refresh failed.');
    else renderDetailMarkers(box,result?.markers||[],mediaId,result?.status||'');
  }catch(e){if(box=== $('v55-markers'))renderDetailMarkers(box,[],mediaId,'PROVIDER_ERROR',e?.message||'Provider marker refresh failed.');}
  finally{if(button){button.disabled=false;button.textContent=oldText;}}
}
async function loadDetailMarkers(){
  const box=$('v55-markers'),video=_dv;if(!box||!video?.id)return;
  const videoKey=`${String(video._cid)}:${Number(video.id)}`;
  box.dataset.detailVideoKey=videoKey;box.dataset.mediaId='';box.innerHTML=window.AXSkeleton?window.AXSkeleton.markers(3):'<div class="muted">Loading scene markers…</div>';
  const isCurrent=()=>box.dataset.detailVideoKey===videoKey&&_dv&&String(_dv._cid)===String(video._cid)&&Number(_dv.id)===Number(video.id);
  try{
    const lookup=await api(`/api/videos/lookup?channel_id=${encodeURIComponent(video._cid)}&message_id=${Number(video.id)}`,{cache:'no-store'});
    if(!isCurrent())return;
    const mediaId=Number(lookup?.media_id||0);
    if(!mediaId){box.innerHTML='<div class="muted">Markers are available after catalogue indexing.</div>';return;}
    box.dataset.mediaId=String(mediaId);
    const result=await api(`/api/media/${mediaId}/markers`,{cache:'no-store'});
    if(!isCurrent())return;
    let rows=Array.isArray(result)?result:(result?.markers||[]),status='';
    if(!rows.length){
      // Provider markers are stored locally. If the list is empty, lazily invoke
      // the idempotent provider-sync path instead of silently showing an empty
      // section forever. A user-forced refresh remains available in the UI.
      const synced=await syncV55ProviderMarkers(mediaId,false);
      if(!isCurrent())return;
      if(synced?._ok===false){renderDetailMarkers(box,[],mediaId,'PROVIDER_ERROR','Saved markers are empty and provider sync failed. Use Refresh provider markers to retry.');return;}
      rows=Array.isArray(synced?.markers)?synced.markers:rows;status=String(synced?.status||'');
    }
    if(!isCurrent())return;
    renderDetailMarkers(box,rows,mediaId,status);
  }catch(e){if(isCurrent())box.innerHTML='<div class="muted">Markers unavailable. Check the Operations log or retry.</div>';}
}

async function deleteV55Marker(id,mediaId){await api(`/api/media/${mediaId}/markers/${id}`,{method:'DELETE',body:'{}'});loadDetailMarkers();}

// ═══════════════════════════════════════════════
// SECTION: V5.4 — Operations / Settings control plane
// ═══════════════════════════════════════════════
function _opsFmtRate(n){n=Number(n||0);if(!n)return '0 B/s';const u=['B/s','KB/s','MB/s','GB/s'];let i=0;while(n>=1024&&i<u.length-1){n/=1024;i++;}return `${n.toFixed(i?1:0)} ${u[i]}`;}
function _opsSet(id,text,cls=''){const el=$(id);if(!el)return;_axWriteTextIfChanged(el,text);const className='value '+cls;if(el.className!==className)el.className=className;}
let _diagRefreshAt=0;
function formatLibraryMaintenanceDate(value){
  const date=new Date(value);
  if(!Number.isFinite(date.getTime()))return String(value||'Never');
  return date.toLocaleString(undefined,{year:'numeric',month:'short',day:'numeric',hour:'numeric',minute:'2-digit'});
}
function restoreLibraryHealthLastChecked(){
  const label=$('ops-library-last-checked');if(!label)return;
  try{const value=localStorage.getItem('ax-library-health-last-checked');if(value)label.textContent=formatLibraryMaintenanceDate(value);}catch(e){}
}
let _libraryMaintenanceSummarySeq=0;
async function loadLibraryMaintenanceSummary(){
  const seq=++_libraryMaintenanceSummarySeq;
  const fields={videos:'ops-library-count-videos',performers:'ops-library-count-performers',studios:'ops-library-count-studios'};
  try{
    const result=await api('/api/operations/library-summary',{cache:'no-store'});
    if(seq!==_libraryMaintenanceSummarySeq)return;
    if(!result||result.detail)throw new Error(result?.detail||'Library totals are unavailable.');
    Object.entries(fields).forEach(([key,id])=>{const el=$(id);if(!el)return;el.textContent=Number(result[key]||0).toLocaleString();el.dataset.countState='ready';});
  }catch(e){
    if(seq!==_libraryMaintenanceSummarySeq)return;
    Object.values(fields).forEach(id=>{const el=$(id);if(el){el.textContent='—';el.dataset.countState='unavailable';}});
  }
}
async function runLibraryHealth(){
  const box=$('ops-library-health-result'),button=$('ops-library-full-check');
  const status=$('ops-library-health-status'),overview=$('ops-library-health-overview');
  if(button?.disabled)return;
  if(button){button.disabled=true;button.setAttribute('aria-busy','true');}
  if(status){status.textContent='Checking…';status.dataset.health='checking';}
  if(overview)overview.textContent='Checking local previews, artwork caches and background jobs…';
  if(box)box.textContent='Checking library health…';
  try{
    const r=await api('/api/operations/library-health?limit=100&stale_job_hours=24',{cache:'no-store'});
    if(!r||r.detail)throw new Error(r?.detail||'Library health endpoint did not return a report.');
    const s=r.summary||{},findings=Array.isArray(r.findings)?r.findings:[];
    const lines=[`Checked ${r.checked_at||'now'}.`,`${Number(s.active_media||0)} active media item(s).`,
      `${Number(s.missing_snapshot_cache||0)} missing representative snapshot(s).`,
      `${Number(s.empty_snapshot_blobs||0)} empty snapshot-cache row(s).`,
      `${Number(s.missing_preview_files||0)} incomplete preview cache(s).`,
      `${Number(s.stale_jobs||0)} stale background job(s).`,
      `Artwork cache misses: ${Object.values(s.artwork_cache_missing_bytes||{}).reduce((a,b)=>a+Number(b||0),0)}.`,
      ...findings.map(f=>`${f.severity||'info'} · ${f.code}: ${f.count} — ${f.message}`)];
    if(box)box.textContent=lines.join('\n');
    const checkedAt=r.checked_at||new Date().toISOString(),healthy=Number(s.finding_count??findings.length)===0;
    if(status){status.textContent=healthy?'Healthy':`${findings.length||Number(s.finding_count)||1} finding${(findings.length||Number(s.finding_count)||1)===1?'':'s'}`;status.dataset.health=healthy?'healthy':'review';}
    if(overview)overview.textContent=healthy?'Metadata, previews and background jobs look good. No issues were found.':'Review the reported findings below to repair missing previews, artwork or stalled jobs.';
    const lastChecked=$('ops-library-last-checked');
    if(lastChecked)lastChecked.textContent=formatLibraryMaintenanceDate(checkedAt);
    try{localStorage.setItem('ax-library-health-last-checked',checkedAt);}catch(e){}
    loadLibraryMaintenanceSummary().catch(()=>{});
  }catch(e){
    if(box)box.textContent='Library health check failed: '+String(e?.message||e);
    if(status){status.textContent='Check failed';status.dataset.health='error';}
    if(overview)overview.textContent='The check could not finish. Open the details for the error and try again.';
  }finally{
    if(button){button.disabled=false;button.removeAttribute('aria-busy');}
  }
}
async function repairLibraryArtwork(){
  const box=$('ops-library-health-result');
  if(box)box.textContent='Repairing artwork cache from matching local files only…';
  try{
    const r=await api('/api/operations/library-health/repair-artwork',{method:'POST',body:JSON.stringify({limit:100})});
    if(!r||!r.ok){if(box)box.textContent=r?.detail||'Artwork cache repair failed.';return;}
    toast(`Artwork cache: ${r.repaired||0} repaired, ${r.skipped||0} unavailable locally, ${r.failed||0} errors.`);
    await runLibraryHealth();
  }catch(e){if(box)box.textContent='Artwork cache repair failed: '+String(e?.message||e);}
}
async function exportHttpTelemetry(){
  try{
    const r=await fetch('/api/diagnostics/metrics/export',{credentials:'same-origin',cache:'no-store'});
    if(!r.ok)throw new Error('Could not export HTTP telemetry.');
    const blob=await r.blob();
    const url=URL.createObjectURL(blob);
    const a=document.createElement('a');
    a.href=url;
    a.download=(r.headers.get('content-disposition')||'').match(/filename="?([^";]+)"?/i)?.[1]||'archive-x-http-telemetry.json';
    document.body.appendChild(a);a.click();a.remove();URL.revokeObjectURL(url);
    toast('HTTP telemetry window exported.');
  }catch(e){toast(e?.message||'Could not export HTTP telemetry.');}
}
async function resetHttpTelemetry(){
  if(!(await axConfirm('Reset the bounded HTTP telemetry window? This clears only the in-memory HTTP request counters and latency samples.',{title:'Reset HTTP telemetry',okLabel:'Reset telemetry',confirmLabel:'Reset telemetry',danger:true})))return;
  try{
    await api('/api/diagnostics/metrics/reset',{method:'POST',body:'{}'});
    _diagRefreshAt=0;
    await refreshDiagnosticsStrip(true);
    toast('HTTP telemetry window reset.');
  }catch(e){toast(e?.message||'Could not reset HTTP telemetry.');}
}
async function refreshDiagnosticsStrip(force=false){
  const now=performance.now();
  if(!force && now-_diagRefreshAt<4500)return;
  _diagRefreshAt=now;
  const r=await api('/api/diagnostics/summary',{cache:'no-store'});
  if(!r||r._ok===false)return;
  const levels=r.levels||{};
  const errors=(Number(levels.ERROR||0)+Number(levels.CRITICAL||0));
  const watchdogSnapshot=(r.watchdogs&&typeof r.watchdogs==='object')?r.watchdogs:{};
  const watchdogs=Array.isArray(watchdogSnapshot.jobs)?watchdogSnapshot.jobs:[];
  const bad=watchdogs.filter(x=>['DEGRADED','SUSPECTED','STALLED','FAILED','RECOVERING'].includes(String(x.state||'').toUpperCase())).length;
  const guardian=watchdogSnapshot.guardian||{};
  const latest=(r.recent_errors||[]).slice(-1)[0];
  const age=document.getElementById('ops-h-diag-age');
  const ev=document.getElementById('ops-h-diag-events');
  const er=document.getElementById('ops-h-diag-errors');
  const wd=document.getElementById('ops-h-diag-watchdogs');
  const guardianNode=document.getElementById('ops-h-diag-guardian');
  const metadata=document.getElementById('ops-h-diag-metadata');
  const transfer=document.getElementById('ops-h-diag-transfer');
  const automation=document.getElementById('ops-h-diag-automation');
  const last=document.getElementById('ops-h-diag-last');
  const p95=document.getElementById('ops-h-diag-p95');
  const health=document.getElementById('ops-h-diag-health');
  const incidents=document.getElementById('ops-h-diag-incidents');
  const duplicates=document.getElementById('ops-h-diag-duplicates');
  const hs=String(r.diagnostic_health?.state||'UNKNOWN');
  if(health){health.textContent=hs;health.className='value '+(hs==='HEALTHY'?'v54-ok':hs==='ATTENTION'?'v54-warn':'v54-bad');}
  if(incidents)incidents.textContent=String(r.incident_analysis?.active_incident_count||0);
  if(duplicates)duplicates.textContent=String(r.duplicate_events||0);
  if(ev)ev.textContent=String(Object.values(r.events||{}).reduce((a,b)=>a+Number(b||0),0));
  if(er){er.textContent=String(errors);er.className='value '+(errors?'v54-warn':'v54-ok');}
  if(wd){wd.textContent=bad?`${bad} attention`:'Healthy';wd.className='value '+(bad?'v54-warn':'v54-ok');}
  if(guardianNode){const gs=String(guardian.state||'UNKNOWN').toUpperCase();const ga=guardian.heartbeat_age!=null?` · ${_fmtAgeSeconds(guardian.heartbeat_age)}`:'';guardianNode.textContent=`${gs}${ga}`;guardianNode.title=`Independent guardian: ${guardian.process_alive?'running':'not running'}; loop age=${guardian.loop_heartbeat_age??'—'}s; last successful watchdog cycle age=${guardian.watchdog_success_age??'—'}s; watchdog errors=${guardian.watchdog_error_count||0}; monitor errors=${guardian.independent_monitor_error_count||0}; enforcement=${guardian.enforcement||'unknown'}; restart limit=${guardian.restart_limit??'—'} per ${guardian.restart_window_seconds??'—'}s`;guardianNode.className='value '+(gs==='HEALTHY'?'v54-ok':gs==='DISABLED'||gs==='STARTING'?'':'v54-bad');}
  if(metadata){const ma=r.metadata_assets||{};const active=Array.isArray(ma.active)?ma.active:[];const issues=Array.isArray(ma.issues)?ma.issues:[];const j=active[0];metadata.textContent=j?`${j.progress?.completed_channels||0}/${j.progress?.channel_count||0} source channels · ${Number(j.progress?.overall_progress||0).toFixed(1)}% · ready ${Number(j.progress?.published_ready||0).toLocaleString()} · review ${Number(j.progress?.needs_review||0).toLocaleString()}${Number(j.progress?.needs_snapshots||0)?` · need snapshots ${Number(j.progress.needs_snapshots).toLocaleString()}`:''}${issues.length?` · ${issues.length} issue(s)`:''}`:(issues.length?`${issues.length} issue(s) · no active job`:'Idle');metadata.className='value '+(issues.length?'v54-warn':'v54-ok');}
  if(transfer){const tr=r.telegram_transfer||{};const active=Array.isArray(tr.active)?tr.active:[];const issues=Array.isArray(tr.issues)?tr.issues:[];const j=active[0];transfer.textContent=j?`${j.status||'RUNNING'} · ${j.completed||0}/${j.total||'?'}${issues.length?` · ${issues.length} issue(s)`:''}`:(issues.length?`${issues.length} issue(s) · idle`:`${Number(tr.verified_transfers||0).toLocaleString()} verified`);transfer.className='value '+(issues.length?'v54-warn':'v54-ok');}
  if(automation){const ca=r.content_automation||{};const active=Array.isArray(ca.active)?ca.active:[];const issues=Array.isArray(ca.issues)?ca.issues:[];const counts=ca.counts||{};const j=active[0];automation.textContent=j?`${j.status||'RUNNING'} · ${j.current_operation||j.current_item||''}${issues.length?` · ${issues.length} issue(s)`:''}`:(issues.length?`${issues.length} issue(s) · idle`:`${Number(counts.PUBLISHED||0).toLocaleString()} published · ${Number(ca.cleaned||0).toLocaleString()} cleaned`);automation.className='value '+(issues.length?'v54-warn':'v54-ok');}
  if(last)last.textContent=latest?`${latest.error_type||'Error'} · ${latest.message||'—'}`:'None';
  if(p95)p95.textContent=`${Number(r.http?.latency_ms?.p95||0).toFixed(0)} ms`;
  if(age)age.textContent='Live';
}
function renderOpsHealth(s){
  if(!s)return;
  const jobs=Array.isArray(s.jobs)?s.jobs:[];
  const running=jobs.filter(j=>j.status==='RUNNING').length;
  const queued=jobs.filter(j=>j.status==='QUEUED').length;
  const failed=Number(s.stats?.failed||0);
  const tg=!!s.telegram_connected;
  const gov=s.governor||{};
  const auto=s.content_automation||{};
  const autoRuntime=auto.runtime||{};
  _opsSet('ops-h-telegram',tg?'Connected':'Offline',tg?'v54-ok':'v54-bad');
  const orchestration=s.job_orchestration||{};
  const waiting=Number(orchestration.states?.WAITING||0)+Number(orchestration.states?.BLOCKED||0)+Number(orchestration.states?.DELAYED||0);
  const retrying=Number(orchestration.retrying||0), leases=Number(orchestration.active_leases||0), expired=Number(orchestration.expired_leases||0);
  _opsSet('ops-h-workers',`${running} active · ${queued} queued · ${waiting} waiting · ${retrying} retrying`,running||queued?'v54-ok':'');
  _opsSet('ops-h-db','Healthy',s.stats?'v54-ok':'v54-warn');
  const flood=Math.max(Number(gov.flood_wait_remaining||0),Number(gov.download_flood_wait_remaining||0));
  _opsSet('ops-h-rate',flood>0?`FloodWait ${Math.ceil(flood)}s`:`${Number(gov.max_ranges||0)} range slots`,flood>0?'v54-warn':'v54-ok');
  _opsSet('ops-h-playback',(s.telegram_connected?'Ready':'Waiting for Telegram'),s.telegram_connected?'v54-ok':'v54-warn');
  _opsSet('ops-h-auto',autoRuntime.paused?'Paused':(auto.running||auto.active_count?'Running':'Ready'),autoRuntime.paused?'v54-warn':'v54-ok');
  const attention=[];
  if(!tg)attention.push({key:'telegram-offline',bad:true,text:'Telegram is offline. Login or reconnect before starting Telegram-heavy jobs.'});
  if(failed>0)attention.push({key:'failed-jobs',bad:true,text:`${failed.toLocaleString()} failed job${failed===1?'':'s'} require review.`});
  if(flood>0)attention.push({key:'telegram-flood-wait',bad:false,text:String(gov.download_flood_wait_message||`Telegram FloodWait active for about ${Math.ceil(flood)}s. Download concurrency is reduced.`)});
  if(autoRuntime.restart_pending)attention.push({key:'automation-restart',bad:false,text:'Content Automation has an interrupted task waiting for an operator decision.'});
  // V29.4.133: earlier filename auto-matches waiting for a decision.
  if(_amrCount>0)attention.push({key:'automatic-matches-review',bad:false,action:'amrOpen()',text:`${_amrCount.toLocaleString()} earlier automatic match${_amrCount===1?'':'es'} to review — open`});
  if(!attention.length)attention.push({key:'all-clear',ok:true,text:'No immediate operator action required.'});
  const box=$('ops-attention');
  if(box)_axPatchLiveMarkup(box,attention.slice(0,5).map(a=>a.action?`<button data-ax-live-key="ops-attention:${esc(a.key||a.action)}" type="button" class="v54-attention v54-attention-action" onclick="${a.action}"><span class="dot"></span><span>${esc(a.text)}</span></button>`:`<div data-ax-live-key="ops-attention:${esc(a.key||a.text)}" class="v54-attention ${a.bad?'bad':a.ok?'ok':''}"><span class="dot"></span><span>${esc(a.text)}</span></div>`).join(''));
  const throughput=Number(gov.bytes_per_second||0);
  const th=$('ops-h-throughput');if(th)th.textContent=_opsFmtRate(throughput);
  _opsSet('ops-h-ranges',`${Number(gov.active_ranges||0)} / ${Number(gov.max_ranges||0)}`);
  _opsSet('ops-h-rpc',`${Number(gov.active_rpc||0)} / ${Number(gov.max_rpc||0)}`);
  _opsSet('ops-h-flood',flood>0?`${Math.ceil(flood)}s`:'None',flood>0?'v54-warn':'v54-ok');
  _opsSet('ops-h-governor',gov.last_error?'Degraded':'Healthy',gov.last_error?'v54-warn':'v54-ok');
  const stamp=$('ops-health-stamp');if(stamp)stamp.textContent=`Updated ${new Date().toLocaleTimeString([], {hour:'2-digit',minute:'2-digit',hour12:false})}`;
}
function _opsResourceData(s){return s?.resources||{};}
function _opsResourceFmtMode(c){return c?.mode_label||'Automatic';}
// Manual mode is an editable draft until the operator explicitly saves and applies it.
// Keep that draft selected during the five-second live telemetry refresh loop.
let _opsResourceModeDraft = null;
function renderOpsResourceDashboard(s){
  const r=_opsResourceData(s), c=r.control||{}, gov=s?.governor||{}, jobs=Array.isArray(s?.jobs)?s.jobs:[];
  const mode=c.mode||'automatic';
  const play=$('ops-res-playback'), bg=$('ops-res-background'), tg=$('ops-res-telegram'), jm=$('ops-res-jobs');
  if(play){_axWriteTextIfChanged(play,r.playback_active?'ACTIVE':'IDLE');const className=r.playback_active?'warn':'';if(play.className!==className)play.className=className;}
  _axWriteTextIfChanged($('ops-res-playback-sub'),r.playback_active?`${Number(r.playback_streams||0)} stream${Number(r.playback_streams||0)===1?'':'s'}`:'No active stream');
  _axWriteTextIfChanged(bg,`${Number(r.background_active||0)} / ${Number(r.background_capacity||0)}`);
  _axWriteTextIfChanged($('ops-res-background-sub'),`${Number(r.background_waiting||0)} waiting · metadata ${Number(r.metadata_active||0)}/${Number(r.metadata_capacity||0)} · other ${Number(r.non_metadata_active||0)}/${Number(r.non_metadata_capacity||0)}`);
  _axWriteTextIfChanged(tg,gov.traffic_mode||'NORMAL');
  _axWriteTextIfChanged($('ops-res-telegram-sub'),`RPC ${Number(gov.active_rpc||0)} / ${Number(gov.max_rpc||0)} · Range ${Number(gov.active_ranges||0)} / ${Number(gov.max_ranges||0)} · Upload X${Number(gov.upload_x||4)}/Y${Number(gov.upload_y||1)} (${Number(gov.upload_active_parts||0)}/${Number(gov.upload_max_parts||4)} parts)`);
  if($('ops-res-telegram-detail')){
    const dcActive=Object.entries(gov.telegram_file_active_by_dc||{}).map(([k,v])=>`${k}=${v}`).join(', ')||'idle';
    const scans=(Array.isArray(s?.channel_scans)?s.channel_scans:[]).map(x=>`${channelDisplayName(x.channel_id,x.channel_title)}: ${x.status||'UNKNOWN'} · scanned ${Number(x.messages_scanned||0).toLocaleString()} · new ${Number(x.new_videos_found||0).toLocaleString()} · FloodWaits ${Number(x.floodwaits||0)} · ${x.last_scan_at||'never'}`).join(' ; ');
    _axWriteTextIfChanged($('ops-res-telegram-detail'),`API ${Number(gov.rpc_interval_seconds||0).toFixed(2)}s/request · ${Number(gov.total_rpc_requests||0).toLocaleString()} requests · API FloodWait ${Number(gov.total_rpc_floodwaits||0)} · Download waits ${Number(gov.download_range_floodwaits||0)+Number(gov.download_playback_floodwaits||0)}${Number(gov.download_flood_wait_remaining||0)>0?` (${String(gov.download_flood_wait_message||'resuming shortly')})`:''} · File-reference refreshes ${Number(gov.file_reference_refreshes||0)} · Per-DC active ${dcActive} · Playback reservation ${gov.playback_range_reserved?'ACTIVE':'idle'} · Last admission ${Number(gov.last_playback_range_wait_ms||0).toFixed(0)}ms${scans?` · Channel scans: ${scans}`:''}`);
  }
  const orchestration=s?.job_orchestration||{};
  const laneSnap=orchestration.execution_lanes||{};
  const durableLeases=s?.resource_leases||orchestration.resource_leases||{};
  const waiting=Number(orchestration.states?.WAITING||0)+Number(orchestration.states?.BLOCKED||0)+Number(orchestration.states?.DELAYED||0);
  _axWriteTextIfChanged(jm,`${jobs.filter(j=>j.status==='RUNNING').length} active`);
  const laneText=Object.entries(laneSnap.lanes||{}).filter(([,v])=>v).map(([k,v])=>`${k}: ${v.active}/${v.capacity}`).join(' · ');
  _axWriteTextIfChanged($('ops-res-jobs-sub'),`${jobs.filter(j=>j.status==='QUEUED').length} queued · ${waiting} waiting · ${Number(orchestration.retrying||0)} retrying · ${Number(orchestration.active_leases||0)} executions · ${Number(durableLeases.active||0)} resource leases${Number(durableLeases.expired||0)?` · ${Number(durableLeases.expired)} expired`:''}${laneText?` · Lanes ${laneText}`:''}`);
  const modeSel=$('ops-resource-mode');
  const modeDraft=Boolean(_opsResourceModeDraft);
  const manualDraft=_opsResourceModeDraft==='manual';
  if(modeSel&&!modeDraft&&modeSel.value!==mode)modeSel.value=mode;
  const visibleMode=modeDraft?_opsResourceModeDraft:mode;
  const controls=$('ops-resource-controls');if(controls){const display=visibleMode==='manual'?'block':'none';if(controls.style.display!==display)controls.style.display=display;}
  const modeHint=$('ops-resource-mode-hint');if(modeHint){_axWriteTextIfChanged(modeHint,'Manual mode is not active yet. Review the limits, then choose “Save & use Manual mode”.');const display=manualDraft?'block':'none';if(modeHint.style.display!==display)modeHint.style.display=display;}
  // A manual draft belongs to the operator; live polling must not overwrite edits.
  if(!manualDraft){
    const lim=c.manual_limits||{};['metadata','thumbnail','preview','conversion','upload'].forEach(k=>{const e=$('ops-limit-'+k);if(e&&lim[k]!=null&&String(e.value)!==String(lim[k]))e.value=lim[k];});
    const gate=$('ops-playback-gate');if(gate)gate.checked=!!c.playback_gate;
  }
  const lanes=$('ops-resource-lanes');
  const labels={metadata:'Metadata',thumbnail:'Thumbnail',preview:'Preview',conversion:'Conversion',upload:'Upload'};
  if(lanes){_axPatchLiveMarkup(lanes,Object.keys(labels).map(k=>{const active=Number(r.category_active?.[k]||0),limit=Number(c.effective_limits?.[k]||r.media_policy?.[k+'_workers']||0),pct=limit?Math.min(100,Math.round(active*100/limit)):0;return `<div data-ax-live-key="resource-lane:${k}" class="ax-lane"><div><b>${labels[k]}</b><span data-ax-live-key="resource-lane-count">${active} / ${limit}</span></div><div class="ax-lane-bar"><i data-ax-live-key="resource-lane-fill" style="width:${pct}%"></i></div></div>`;}).join(''));}
  const decision=$('ops-resource-decision');
  const pauseBtn=$('ops-resource-pause'); if(pauseBtn){const paused=!!(r.background_paused||r.control?.background_paused);_axWriteTextIfChanged(pauseBtn,paused?'Resume background controller':'Pause background controller');_axToggleClassIfChanged(pauseBtn,'danger',paused);}
  if(decision){let text=`Policy: ${_opsResourceFmtMode(c)} · Durable resource leases: ${Number(durableLeases.active||0)} active`;if(Number(durableLeases.expired||0))text+=` · ${Number(durableLeases.expired)} expired`;if(c.override_seconds_remaining)text+=` · expires in ${Math.ceil(c.override_seconds_remaining/60)} min`;const gates=c.playback_category_gates||{};if(r.playback_active){const blocked=Object.keys(gates).filter(k=>gates[k]);const running=Object.keys(gates).filter(k=>!gates[k]);if(blocked.length)text+=` · Playback active: ${running.length?running.join(', ')+' continue · ':''}${blocked.join(', ')} wait`;else text+=' · Playback active: background work continues within limits';}if(c.override_label)text+=` · ${c.override_label}`;_axWriteTextIfChanged(decision,text);}
}

let _opsResourceRefreshPromise=null;
async function refreshOpsResourceDashboard(force=false,baseStatus=null){
  const root=$('ops-resource-dashboard');
  if(!root||(!force&&!$('va')?.classList.contains('on')))return null;
  if(_opsResourceRefreshPromise)return _opsResourceRefreshPromise;
  _opsResourceRefreshPromise=(async()=>{
    const live=await api('/api/operations/resources',{cache:'no-store'});
    const resourceData=live?.resources;
    if(!live||live._ok===false||live.ok===false||!resourceData||!Object.keys(resourceData).length){
      const detail=String(live?.detail||'The resource-status endpoint returned no resource data.');
      root.dataset.resourceStatus='unavailable';
      const decision=$('ops-resource-decision');
      if(decision)decision.textContent=root.dataset.resourceLoaded
        ? `Live resource refresh failed — showing last known values. ${detail}`
        : `Resource status unavailable — ${detail} Retrying automatically.`;
      if(!root.dataset.resourceLoaded){
        ['ops-res-playback','ops-res-background','ops-res-telegram','ops-res-jobs'].forEach(id=>{const el=$(id);if(el)el.textContent='Unavailable';});
        ['ops-res-playback-sub','ops-res-background-sub','ops-res-telegram-sub','ops-res-telegram-detail','ops-res-jobs-sub'].forEach(id=>{const el=$(id);if(el)el.textContent='Waiting for live resource data';});
        const lanes=$('ops-resource-lanes');if(lanes)lanes.textContent='Resource lane details are unavailable until the status endpoint responds.';
      }
      root.title=detail;
      return null;
    }
    const state=baseStatus||_opsState||{};
    const executionLanes=live.execution_lanes||state.execution_lanes||{};
    const orchestration={...(state.job_orchestration||{})};
    if(!orchestration.execution_lanes)orchestration.execution_lanes=executionLanes;
    const snapshot={
      ...state,
      resources:resourceData,
      resource_leases:live.resource_leases||state.resource_leases||{},
      execution_lanes:executionLanes,
      governor:live.governor||state.governor||{},
      job_orchestration:orchestration,
      jobs:Array.isArray(state.jobs)?state.jobs:[],
    };
    renderOpsResourceDashboard(snapshot);
    const updated=new Date();
    root.dataset.resourceLoaded='true';
    root.dataset.resourceStatus='live';
    root.dataset.updatedAt=updated.toISOString();
    root.removeAttribute('aria-busy');
    const decision=$('ops-resource-decision');
    if(decision)decision.textContent+=` · Updated ${updated.toLocaleTimeString([], {hour:'2-digit',minute:'2-digit',hour12:false})}`;
    root.title=`Resource status updated ${updated.toLocaleString()}`;
    return snapshot;
  })().catch(error=>{
    const root=$('ops-resource-dashboard');
    if(root){root.dataset.resourceStatus='error';root.title=String(error?.message||error||'Resource status refresh failed');}
    if(root&&!root.dataset.resourceLoaded){const decision=$('ops-resource-decision');if(decision)decision.textContent='Resource status could not be loaded. It will retry automatically.';}
    return null;
  }).finally(()=>{_opsResourceRefreshPromise=null;});
  return _opsResourceRefreshPromise;
}

async function toggleOpsBackgroundPause(){
  const current=await api('/api/operations/resources',{cache:'no-store'});
  const res=current?.resources||{}; const paused=!!res.background_paused;
  const r=await api('/api/operations/resources',{method:'POST',body:JSON.stringify({background_paused:!paused,label:!paused?'Background controller paused by operator':'Background controller resumed'})});
  if(!r.ok){toast(r.detail||'Could not change background controller.');return;}
  toast(!paused?'Background controller paused. Running work will stop at safe checkpoints.':'Background controller resumed.'); refresh();
}
async function opsResourceModeChanged(selectedMode){
  const mode=String(selectedMode||$('ops-resource-mode')?.value||'automatic').toLowerCase();
  const controls=$('ops-resource-controls');
  if(mode==='manual'){
    // Do not call the API yet: manual values must be explicitly saved. Keep this
    // draft visible while the normal live-status poll continues to run.
    _opsResourceModeDraft='manual';
    if(controls)controls.style.display='block';
    const hint=$('ops-resource-mode-hint');if(hint){hint.textContent='Manual mode is not active yet. Review the limits, then choose “Save & use Manual mode”.';hint.style.display='block';}
    return;
  }
  _opsResourceModeDraft=null;
  if(controls)controls.style.display='none';
  await setOpsResourceMode(mode,0);
}
async function setOpsResourceMode(mode,expires=0){
  mode=String(mode||'automatic').toLowerCase();
  if(mode==='manual'){
    _opsResourceModeDraft='manual';
    const controls=$('ops-resource-controls');if(controls)controls.style.display='block';
    return;
  }
  _opsResourceModeDraft=mode;
  const labels={automatic:'Operator selected Automatic',playback:'Operator selected Playback Priority',balanced:'Operator selected Balanced',background:'Operator selected Background Priority'};
  const r=await api('/api/operations/resources',{method:'POST',body:JSON.stringify({mode,expires_seconds:expires,label:labels[mode]||'Operator selected resource policy'})});
  if(!r||r.ok===false||r._ok===false){_opsResourceModeDraft=null;toast(r?.detail||'Could not change resource policy.');await refreshOpsResourceDashboard(true);return;}
  _opsResourceModeDraft=null;
  toast(`Resource policy: ${r.resources?.control?.mode_label||mode}`);
  await refreshOpsResourceDashboard(true);
  refresh();
}
async function cancelOpsResourceModeDraft(){
  _opsResourceModeDraft=null;
  await refreshOpsResourceDashboard(true);
}
async function applyOpsResourceControls(){
  const limits={};
  ['metadata','thumbnail','preview','conversion','upload'].forEach(k=>limits[k]=Math.max(1,Math.min(8,Number($('ops-limit-'+k)?.value||1))));
  const expires=Number($('ops-resource-expiry')?.value||0),gate=!!$('ops-playback-gate')?.checked;
  const btn=$('ops-resource-apply');if(btn){btn.disabled=true;btn.textContent='Saving…';}
  try{
    const r=await api('/api/operations/resources',{method:'POST',body:JSON.stringify({mode:'manual',limits,playback_gate:gate,expires_seconds:expires,label:'Manual resource limits'})});
    if(!r||r.ok===false||r._ok===false){toast(r?.detail||'Could not apply resource limits. Manual edits have been kept.');return;}
    _opsResourceModeDraft=null;
    toast(expires?`Manual limits saved and active for ${Math.round(expires/60)} minutes.`:'Manual limits saved and active until changed.');
    await refreshOpsResourceDashboard(true);
    refresh();
  }finally{
    if(btn){btn.disabled=false;btn.textContent='Save & use Manual mode';}
  }
}
function filterOpsModules(filter,btn){
  document.querySelectorAll('#ops-module-filter .btn').forEach(b=>b.classList.toggle('on',b===btn));
  let visible=0,total=0;
  document.querySelectorAll('#va [data-ops-module]').forEach(card=>{
    total++;
    const show=filter==='all'||card.dataset.opsModule===filter;
    card.style.display=show?'':'none';if(show)visible++;
  });
  const info=$('ops-filter-info');if(info)info.textContent=filter==='all'?`Showing all ${total} modules`:`Showing ${visible} ${filter} module${visible===1?'':'s'}`;
}
function focusSettings(key){
  const map={overview:'settings-v54-overview',telegram:'settings-v54-telegram',metadata:'settings-v54-metadata',experience:'settings-v55-experience', 'storage-backup':'settings-v55-storage',storage:'settings-v55-storage',backup:'settings-v55-backup',security:'settings-v54-security'};
  const target=document.getElementById(map[key]||'settings-v54-status');
  if(target)target.scrollIntoView({behavior:'smooth',block:'start'});
}
window._axJobActivityState=window._axJobActivityState||{jobId:'',latest:new Map(),older:new Map(),hasOlder:false,loadingOlder:false};
function _jobActivityCombined(id){
  const st=window._axJobActivityState;if(!st||st.jobId!==String(id))return [];
  const items=new Map();for(const [k,v] of st.older)items.set(k,v);for(const [k,v] of st.latest)items.set(k,v);
  return [...items.values()].sort((a,b)=>Number(a.id||0)-Number(b.id||0));
}
function _jobActivityHtml(items,hasOlder=false,loadingOlder=false){
  const html=(!Array.isArray(items)||!items.length)?'<div data-ax-live-key="job-activity-empty" class="muted" style="font-size:11px;padding:9px 0">No detailed activity events yet. This panel updates while the job runs.</div>':[...items].reverse().map(x=>{
    const msg=String(x.message||x.operation||x.event||'Activity');
    const op=String(x.operation||'');
    const child=String(x.job_id||'');
    const level=String(x.level||'INFO').toUpperCase();
    let detail='';try{detail=JSON.stringify(x.details||{},null,2);}catch(_){detail=String(x.details||'');}
    const time=String(x.created_at||'');
    const activityId=String(x.id??x.activity_id??`${time}|${child}|${level}|${msg}`);
    const safeKey=esc(activityId);
    return `<div data-ax-live-key="job-activity:${safeKey}" style="padding:8px 0;border-bottom:1px solid var(--line);min-width:0"><div style="display:flex;gap:8px;align-items:flex-start"><span data-ax-live-key="activity-level" style="font:10px var(--mono,monospace);color:${level==='ERROR'?'var(--bad)':level==='WARN'?'var(--warn)':'var(--text3)'};min-width:42px">${esc(level)}</span><div style="min-width:0;flex:1"><div data-ax-live-key="activity-message" style="font-size:11px;line-height:1.45;overflow-wrap:anywhere">${esc(msg)}</div>${op&&op!==msg?`<div data-ax-live-key="activity-operation" class="muted" style="font-size:11px;margin-top:2px">${esc(op)}</div>`:''}<div data-ax-live-key="activity-time" class="muted" style="font-size:10.5px;margin-top:3px">${esc(time?_fmtTime(time):'')} ${child?`· job ${esc(child)}`:''} ${x.event?`· ${esc(String(x.event))}`:''}</div></div></div><details data-ax-live-key="activity-details:${safeKey}" data-activity-id="${esc(String(x.id||''))}" style="margin:5px 0 0 50px"><summary class="muted" style="font-size:11px;cursor:pointer">Metadata / paths / frame details</summary><pre data-ax-live-key="activity-details-content" style="margin:5px 0 0;white-space:pre-wrap;overflow-wrap:anywhere;font-size:11px;color:var(--text2)">${esc(detail)}</pre></details></div>`;
  }).join('');
  return html+(hasOlder?`<div data-ax-live-key="job-activity-load-older" style="display:flex;justify-content:center;padding:10px"><button data-ax-live-key="job-activity-load-older-button" class="btn sm" ${loadingOlder?'disabled':''} onclick="loadOlderJobActivity()">${loadingOlder?'Loading older activity…':'Load older activity'}</button></div>`:'');
}
function _renderJobActivity(id){
  const st=window._axJobActivityState,list=$('v54-job-activity-list');
  if(!st||st.jobId!==String(id)||!list)return;
  const top=list.scrollTop;
  _axPatchLiveMarkup(list,_jobActivityHtml(_jobActivityCombined(id),st.hasOlder,st.loadingOlder));
  list.scrollTop=top;
}
function _jobProgressSummary(j){
  const rawTotal=Number(j?.total||0),rawCompleted=Number(j?.completed||0),rawProgress=Number(j?.progress||0);
  const total=Number.isFinite(rawTotal)&&rawTotal>0?rawTotal:0;
  const completed=Number.isFinite(rawCompleted)?Math.max(0,rawCompleted):0;
  const percentValue=Number.isFinite(rawProgress)?rawProgress:0;
  const raw=total>0?completed*100/total:percentValue;
  const percent=Number.isFinite(raw)?Math.max(0,Math.min(100,raw)):0;
  return {percent,label:total>0?`${completed.toLocaleString()} / ${total.toLocaleString()}`:`${percentValue.toFixed(1)}%`};
}
function _jobProgressColor(status){
  if(status==='COMPLETED')return 'var(--ax2-ok)';
  if(['FAILED','ERROR'].includes(status))return 'var(--ax2-bad)';
  if(['PAUSED','PAUSING'].includes(status))return 'var(--ax2-warn)';
  if(['RUNNING','RECOVERING'].includes(status))return 'var(--ax2-accent)';
  return 'var(--ax2-text-3)';
}
function _jobPipelinePresentation(j,status,percent){
  const raw=[j.current_operation,j.current_step,j.progress_label].filter(Boolean).map(String);
  const operation=raw.join(' ').toLowerCase();
  const type=String(j.job_type||j.type||'').toUpperCase();
  let detail={};try{detail=typeof j.detail==='string'?JSON.parse(j.detail||'{}'):(j.detail||{});}catch(_){}
  const localUpload=type==='UPLOAD_LOCAL'||(type==='TELEGRAM_TRANSFER'&&/^(?:local|local_files|upload_local)$/i.test(String(j.source||detail.source||detail.mode||'')));
  const photoUpload=localUpload&&String(detail.media_type||'').toUpperCase()==='PHOTOS';
  const base=photoUpload
    ?['Scan photos','Upload photos','Complete']
    :localUpload
      ?['Inventory','Duplicate check','Metadata saved','Upload','Verify delivery','Clean up source']
      :['Queued','Dispatched','Processing','Verification','Cleanup'];
  const stagePattern=label=>{
    if(label==='Scan photos')return /\bscan(?:ning)?\b.*\bphoto/i;
    if(label==='Upload photos')return /\bupload(?:ing)?\b.*\bphoto/i;
    if(label==='Complete')return /\bcomplete(?:d)?\b|\bfinished\b|\bprocessed photo/i;
    if(label==='Inventory')return /\binventory\b|\bcollect(?:ing)? files\b|\bscan(?:ning)? input\b/i;
    if(label==='Duplicate check')return /\bduplicate\b|\bdedup(?:e|lication)?\b|\bfingerprint/i;
    if(label==='Metadata saved')return /\bmetadata\b|\bscene match/i;
    if(label==='Upload')return /\bupload(?:ing)?\b|\btransfer(?:ring)?\b|\bsend(?:ing)?(?: media)?\b/i;
    if(label==='Verify delivery')return /\bverif(?:y|ying|ied|ication)\b|\bdelivery\b|\bconfirm(?:ing)? message\b/i;
    if(label==='Clean up source')return /\bclean(?:up|ing up)\b|\bdelete source\b|\bremove source\b/i;
    if(label==='Queued')return /\bqueued\b|\bwaiting\b/i;
    if(label==='Dispatched')return /\bdispatch(?:ed|ing)?\b|\bstarting\b/i;
    if(label==='Processing')return /\bprocess(?:ing|ed)?\b|\bcollect(?:ing)?\b|\bscan(?:ning)?\b/i;
    if(label==='Verification')return /\bverif(?:y|ying|ied|ication)\b|\bdelivery\b/i;
    if(label==='Cleanup')return /\bclean(?:up|ing up)\b|\bcleanup\b/i;
    return new RegExp(label.replace(/[.*+?^${}()|[\]\\]/g,'\\$&'),'i');
  };
  let activeIndex=base.findIndex(label=>stagePattern(label).test(operation));
  if(activeIndex<0&&['RUNNING','PAUSED','PAUSING','RECOVERING','RETRYING','RESUMING','STARTING'].includes(status)){
    activeIndex=Math.min(base.length-1,Math.max(0,Math.floor((Number(percent)||0)/100*(base.length-1))));
    if(activeIndex===0&&base.length>1)activeIndex=1;
  }
  const activeStepColor=_jobProgressColor(status);
  const stages=base.map((label,index)=>{
    const active=index===activeIndex&&['RUNNING','PAUSED','PAUSING','RECOVERING','RETRYING','RESUMING','STARTING'].includes(status);
    const done=status==='COMPLETED'||(index<activeIndex&&activeIndex>=0);
    const failed=['FAILED','ERROR'].includes(status)&&index===Math.max(0,activeIndex);
    return {label,active,done,failed,html:`<div class="v54-job-step ${failed?'failed':done?'done':active?'active':''}" ${active?`style="--ax-fb-status-color:${activeStepColor}"`:''}><span class="s">${failed?axIcon('alert'):done?axIcon('check'):active?'<span class="ax-live-dot"></span>':index+1}</span><span>${label}</span></div>`};
  });
  return {html:stages.map(stage=>stage.html).join(''),completed:stages.filter(stage=>stage.done).length,total:stages.length};
}
function _jobDetailActionFooterHtml(id,status){
  const st=String(status||'UNKNOWN').toUpperCase();
  if(['COMPLETED','COMPLETED_WITH_ERRORS','FAILED','ERROR','CANCELLED'].includes(st))return '';
  const controls=[];
  if(['INTERRUPTED','RECOVERING'].includes(st))controls.push(`<button class="btn sm pri" onclick="jobRecover(${jsa(id)});closeJobDetails()">${st==='RECOVERING'?'Retry recovery':'Recover & Resume'}</button>`);
  else if(st==='PAUSED')controls.push(`<button class="btn sm pri" onclick="jobResume(${jsa(id)});closeJobDetails()">Resume</button>`);
  else if(st==='RUNNING')controls.push(`<button class="btn sm" onclick="jobPause(${jsa(id)});closeJobDetails()">Pause</button>`);
  if(st==='CANCEL_REQUESTED')controls.push('<button class="btn sm danger" type="button" disabled>Cancelling…</button>');
  else controls.push(`<button class="btn sm danger" onclick="jobCancel(${jsa(id)});closeJobDetails()">Cancel job</button>`);
  return `<footer id="v54-job-actions" data-status="${esc(st)}" class="settings-v54-actions v54-job-actions">${controls.join('')}</footer>`;
}
function _jobRunInformationHtml(j,parsed,shortId){
  const items=[];
  const addInfo=(label,value)=>{
    if(value===undefined||value===null||String(value).trim()==='')return;
    items.push(`<div><span class="k">${esc(label)}</span><span class="v">${esc(String(value))}</span></div>`);
  };
  const type=String(j.job_type||j.type||'—');
  const transfer=/TELEGRAM_TRANSFER|UPLOAD_LOCAL|DOWNLOAD/i.test(type);
  const partsInFlight=j.parts_in_flight??parsed?.parts_in_flight??parsed?.transfer?.parts_in_flight;
  const retries=String(j.retry_count??j.retries??0);
  const jobId=`#${shortId}`;
  if(transfer){
    if(partsInFlight!==undefined)addInfo('Parts in flight',partsInFlight);
    const floodWait=j.upload_flood_wait_remaining??j.flood_wait_remaining??j.flood_wait??parsed?.upload_flood_wait_remaining??parsed?.flood_wait_remaining??parsed?.flood_wait;
    const floodWaitText=floodWait===undefined||floodWait===null||floodWait===''||Number(floodWait)===0
      ?'None'
      :Number.isFinite(Number(floodWait))?`${Math.ceil(Number(floodWait))}s`:String(floodWait);
    addInfo('FloodWait',floodWaitText);
    addInfo('Retries',retries);
    addInfo('Job ID',jobId);
    return items.join('');
  }
  const admission=`${j.admission_state||'—'}${j.admission_reason?` · ${j.admission_reason}`:''}`;
  const worker=`${j.worker_id||'—'} · ${j.worker_state||'—'}`;
  const watchdog=`${j.watchdog_state||'UNKNOWN'}${j.watchdog_reason?` · ${j.watchdog_reason}`:''}`;
  const age=value=>value==null?'—':_fmtAgeSeconds(value);
  const watchdogAges=`HB ${age(j.watchdog_heartbeat_age)} · Activity ${age(j.watchdog_activity_age)} · Progress ${age(j.watchdog_progress_age)}`;
  const dependencies=Array.isArray(j.dependencies)&&j.dependencies.length
    ?j.dependencies.map(d=>`${d.depends_on_job_id}:${d.dependency_status||'UNKNOWN'}`).join(', ')
    :'None';
  addInfo('Type',type);
  addInfo('Retries',retries);
  addInfo('Admission',admission);
  addInfo('Attempt',String(j.attempt??'—'));
  addInfo('Worker',worker);
  addInfo('Execution',String(j.execution_id||'—'));
  addInfo('Lease',String(j.lease_id||'—'));
  addInfo('Failure class',String(j.failure_class||'—'));
  addInfo('Watchdog',watchdog);
  addInfo('Watchdog ages',watchdogAges);
  addInfo('Dependencies',dependencies);
  addInfo('Job ID',jobId);
  return items.join('');
}
async function loadOlderJobActivity(){
  const st=window._axJobActivityState;if(!st||!st.jobId||st.loadingOlder||!st.hasOlder)return;
  const requestSeq=Number(window._axJobDetailLoadSeq)||0;
  const combined=_jobActivityCombined(st.jobId);const first=combined.reduce((min,x)=>Math.min(min,Number(x.id||Infinity)),Infinity);
  if(!Number.isFinite(first))return;
  st.loadingOlder=true;_renderJobActivity(st.jobId);
  try{
    const j=await api('/api/jobs/'+encodeURIComponent(st.jobId)+'?activity_before_id='+encodeURIComponent(String(first))+'&activity_limit=500');
    if(requestSeq!==(Number(window._axJobDetailLoadSeq)||0)||window._axJobActivityState!==st||window._axJobDetailCurrentId!==st.jobId||!$('v54-job-detail')?.classList.contains('open'))return;
    if(j&&j._ok!==false){for(const x of (j.activity||[]))st.older.set(String(x.id),x);st.hasOlder=!!j.activity_has_older;}
  }catch(_){}finally{st.loadingOlder=false;if(requestSeq===(Number(window._axJobDetailLoadSeq)||0)&&window._axJobActivityState===st&&$('v54-job-detail')?.classList.contains('open'))_renderJobActivity(st.jobId);}
}
function _startJobDetailPolling(id){
  if(window._axJobDetailTimer)clearInterval(window._axJobDetailTimer);
  window._axJobDetailCurrentId=String(id);
  const requestSeq=Number(window._axJobDetailLoadSeq)||0;
  const tick=async()=>{
    if(requestSeq!==(Number(window._axJobDetailLoadSeq)||0)||window._axJobDetailCurrentId!==String(id)||!$('v54-job-detail')?.classList.contains('open'))return;
    try{
      const j=await api('/api/jobs/'+encodeURIComponent(id)+'?activity_limit=40');
      if(requestSeq!==(Number(window._axJobDetailLoadSeq)||0)||window._axJobDetailCurrentId!==String(id)||!$('v54-job-detail')?.classList.contains('open')||!j||j._ok===false)return;
      let st=window._axJobActivityState;
      if(!st||st.jobId!==String(id)){st=window._axJobActivityState={jobId:String(id),latest:new Map(),older:new Map(),hasOlder:false,loadingOlder:false};}
      st.latest=new Map((j.activity||[]).map(x=>[String(x.id),x]));
      // Once an older page is loaded, only that page's cursor determines if
      // more history remains. The newest page will naturally have older rows.
      if(!st.older.size)st.hasOlder=!!j.activity_has_older;
      _renderJobActivity(id);
       const status=String(j.status||'UNKNOWN').toUpperCase();
        const actionFooter=$('v54-job-actions');
        const nextActionFooter=_jobDetailActionFooterHtml(id,status);
        if(actionFooter&&actionFooter.dataset.status!==status){
          if(nextActionFooter)actionFooter.outerHTML=nextActionFooter;else actionFooter.remove();
        }else if(!actionFooter&&nextActionFooter){
          $('v54-job-body')?.insertAdjacentHTML('beforeend',nextActionFooter);
        }
       const writeText=(element,value)=>{if(element&&element.textContent!==value)element.textContent=value;};
       const progress=_jobProgressSummary(j),progressColor=_jobProgressColor(status);
       const current=$('v54-job-current-operation');writeText(current,j.current_operation||j.operation||j.current_step||'—');
       const item=$('v54-job-current-item');writeText(item,j.current_item||j.item||'—');
       const progressLabel=$('v54-job-progress-value');writeText(progressLabel,progress.label);
       const progressTrack=$('v54-job-progress-track');
       if(progressTrack){
         const nextPercent=String(Math.round(progress.percent));
         if(progressTrack.getAttribute('aria-valuenow')!==nextPercent)progressTrack.setAttribute('aria-valuenow',nextPercent);
         const fill=progressTrack.querySelector('[data-job-progress-fill]');
         if(fill){const width=`${progress.percent}%`;if(fill.style.width!==width)fill.style.width=width;if(fill.style.getPropertyValue('--ax-fb-status-color')!==progressColor)fill.style.setProperty('--ax-fb-status-color',progressColor);}
       }
       const updated=$('v54-job-updated');writeText(updated,`Updated ${j.updated_at?_fmtTime(j.updated_at):'—'}`);
       const pipeline=_jobPipelinePresentation(j,status,progress.percent);
       const stages=$('v54-job-steps');if(stages)_axPatchLiveMarkup(stages,pipeline.html);
       const stageCount=$('v54-job-stage-count');writeText(stageCount,`${pipeline.completed} / ${pipeline.total} stages`);
      const wd=$('v54-job-watchdog');
      if(wd){
        const watchdogState=String(j.watchdog_state||'UNKNOWN');
        writeText(wd,`${watchdogState}${j.watchdog_reason?' · '+j.watchdog_reason:''}`);
        if(wd.dataset.axWatchdogState!==watchdogState){window.AXLoadingFeedback?.setStatusIndicator?.(wd,watchdogState);wd.dataset.axWatchdogState=watchdogState;}
      }
       const watchdogAges=$('v54-job-watchdog-ages');
       writeText(watchdogAges,`HB ${j.watchdog_heartbeat_age!=null?_fmtAgeSeconds(j.watchdog_heartbeat_age):'—'} · Activity ${j.watchdog_activity_age!=null?_fmtAgeSeconds(j.watchdog_activity_age):'—'} · Progress ${j.watchdog_progress_age!=null?_fmtAgeSeconds(j.watchdog_progress_age):'—'}`);
       const sub=$('v54-job-sub'),statusText=$('v54-job-status-label');
       if(sub){
         const statusKey=status.toLowerCase();
         if(sub.dataset.status!==statusKey)sub.dataset.status=statusKey;
         if(sub.dataset.axStatusIndicatorStatus!==status){window.AXLoadingFeedback?.setStatusIndicator?.(sub,status);sub.dataset.axStatusIndicatorStatus=status;}
       }
       writeText(statusText,status);
    }catch(_){ }
  };
  tick();window._axJobDetailTimer=setInterval(tick,2500);
}
async function openJobDetails(id){
  const wrap=$('v54-job-detail'),body=$('v54-job-body');if(!wrap||!body)return;
  const requestSeq=(Number(window._axJobDetailLoadSeq)||0)+1,requestId=String(id);
  window._axJobDetailLoadSeq=requestSeq;
  window._axJobDetailCurrentId=requestId;
  wrap.classList.add('open');body.innerHTML=window.AXSkeleton?`<div style="padding:15px 0;display:grid;gap:18px">${window.AXSkeleton.keyValues(5)}${window.AXSkeleton.lines(4,[96,82,90,60])}</div>`:'<div class="muted" style="padding:15px 0">Loading job details…</div>';
  window._axJobActivityState={jobId:String(id),latest:new Map(),older:new Map(),hasOlder:false,loadingOlder:false};
  try{
    const j=await api('/api/jobs/'+encodeURIComponent(id)+'?activity_limit=40');
    if(requestSeq!==window._axJobDetailLoadSeq||requestId!==window._axJobDetailCurrentId||!wrap.classList.contains('open'))return;
    if(!j||j._ok===false)throw new Error(j?.detail||'Job not found');
    const st=String(j.status||'UNKNOWN').toUpperCase();
    const started=j.created_at||j.started_at||j.started||'';
    const updated=j.updated_at||'';
    const detail=j.detail||j.operation_detail||'';
    let parsed=null;try{parsed=typeof detail==='string'&&detail.trim().startsWith('{')?JSON.parse(detail):null;}catch(_){ }
    const progress=_jobProgressSummary(j),jobOverallPct=progress.percent;
    const pipeline=_jobPipelinePresentation(j,st,jobOverallPct);
    const jobType=String(j.job_type||j.type||'');
    const jobKind=_axTransferLaneName(jobType,parsed?.source||j.source,parsed?.workflow||j.workflow);
    const rawName=String(j.name||'').trim();
    const displayName=rawName&&rawName.toUpperCase()!==jobType.toUpperCase()?rawName:jobKind;
    const detailHtml=parsed?`<div class="v54-job-input"><h4>Job input</h4><pre>${esc(JSON.stringify(parsed,null,2))}</pre></div>`:'';
    const fullId=String(j.id||id),shortId=fullId.length>10?`${fullId.slice(0,8)}…`:fullId;
    const runInfoHtml=_jobRunInformationHtml(j,parsed,shortId);
    const technicalHtml=detailHtml?`<details class="v54-job-details-collapse v54-job-technical"><summary><span>Technical details</span><span class="ax2-disclosure-chevron" aria-hidden="true">⌄</span></summary>${detailHtml}</details>`:'';
    body.innerHTML=`<header class="v54-job-header">
      <div class="v54-job-ident">
        <span class="v54-job-mark" aria-hidden="true">${axIcon('activity')}</span>
        <div class="v54-job-heading"><div class="v54-job-overline">Job details</div><h2 id="v54-job-title">${esc(displayName||`Job ${id}`)}</h2><div class="v54-job-id" title="${esc(fullId)}">${esc(`${jobKind||jobType||'Job'} · #${shortId}`)}</div></div>
      </div>
      <button class="v54-job-close" type="button" aria-label="Close job details" onclick="closeJobDetails()">×</button>
    </header>
    <section class="v54-job-summary" aria-label="Job status and progress">
      <div class="v54-job-status-line"><span class="v54-job-status" id="v54-job-sub" data-status="${esc(st.toLowerCase())}"><span class="ax-live-dot" aria-hidden="true"></span><span id="v54-job-status-label">${esc(st)}</span></span><span class="v54-job-started">Started ${esc(started?_fmtTime(started):'—')}</span></div>
      <div class="v54-job-progress-copy"><span id="v54-job-current-operation">${esc(j.current_operation||j.operation||'Waiting for work')}</span><strong id="v54-job-progress-value">${esc(progress.label)}</strong></div>
      <div class="v54-job-progress-track" id="v54-job-progress-track" role="progressbar" aria-label="Job progress" aria-valuemin="0" aria-valuemax="100" aria-valuenow="${Math.round(jobOverallPct)}"><span data-job-progress-fill style="width:${jobOverallPct}%;--ax-fb-status-color:${_jobProgressColor(st)}"></span></div>
      <div class="v54-job-progress-foot"><span id="v54-job-current-item">${esc(j.current_item||j.item||'No current item')}</span><span id="v54-job-updated">Updated ${esc(updated?_fmtTime(updated):'—')}</span></div>
    </section>
    <section class="v54-job-section v54-job-pipeline"><div class="v54-job-section-heading"><h3 class="v54-job-section-title">Pipeline</h3><span id="v54-job-stage-count">${pipeline.completed} / ${pipeline.total} stages</span></div><div class="v54-job-steps" id="v54-job-steps">${pipeline.html}</div></section>
    ${runInfoHtml?`<section class="v54-job-section"><div class="v54-job-section-heading"><h3 class="v54-job-section-title">Run information</h3></div><div class="v54-job-grid">${runInfoHtml}</div></section>`:''}
    ${j.error?`<section class="v54-job-section v54-job-error"><h3 class="v54-job-section-title">Error</h3><div>${esc(j.error)}</div></section>`:''}
    <section class="v54-job-section v54-job-activity"><details class="v54-job-details-collapse"><summary><span>Activity</span><span class="v54-job-live-label"><span class="ax-live-dot"></span>Live</span><span class="ax2-disclosure-chevron" aria-hidden="true">⌄</span></summary><p class="v54-job-hint">Recent steps and important updates</p><div id="v54-job-activity-list">${_jobActivityHtml(j.activity||[])}</div></details></section>
    ${technicalHtml}
    ${_jobDetailActionFooterHtml(id,st)}`;
    window.AXLoadingFeedback?.setStatusIndicator?.($('v54-job-sub'),st);
    _startJobDetailPolling(id);
  }catch(e){if(requestSeq===window._axJobDetailLoadSeq&&requestId===window._axJobDetailCurrentId&&wrap.classList.contains('open'))body.innerHTML=`<div class="v54-bad" style="padding:12px">Could not load job details: ${esc(e.message||e)}</div>`;}
}
function closeJobDetails(){window._axJobDetailLoadSeq=(Number(window._axJobDetailLoadSeq)||0)+1;window._axJobDetailCurrentId='';if(window._axJobDetailTimer){clearInterval(window._axJobDetailTimer);window._axJobDetailTimer=null;}$('v54-job-detail')?.classList.remove('open');}
window.addEventListener('keydown',e=>{if(e.key==='Escape'&&$('ax-confirm')?.classList.contains('open')){axConfirmClose(false);return;}if(e.key==='Escape')closeJobDetails();});

function _browserCompatSnapshot(){
  const e=pv?.();
  const ua=navigator.userAgent||'';
  const isChromium=/Chrome|Chromium|Edg\//.test(ua)&&!/OPR\//.test(ua);
  const isSafari=/Safari\//.test(ua)&&!isChromium&&!/Firefox\//.test(ua);
  const isFirefox=/Firefox\//.test(ua);
  return {browser:isChromium?(/Edg\//.test(ua)?'Edge':'Chromium'):isFirefox?'Firefox':isSafari?'Safari':'Other',os:_isIOS()?'iOS/iPadOS':_isAndroid()?'Android':/Windows/i.test(ua)?'Windows':/Mac OS X/i.test(ua)?'macOS':/Linux/i.test(ua)?'Linux':'Other',mp4:!!e?.canPlayType?.('video/mp4'),webm:!!e?.canPlayType?.('video/webm'),hls:!!e?.canPlayType?.('application/vnd.apple.mpegurl'),fetch:typeof fetch==='function',abort:typeof AbortController==='function',pointer:typeof PointerEvent!=='undefined'};
}
function renderBrowserCompat(){
  const box=$('browser-compat-grid');if(!box)return;const s=_browserCompatSnapshot();
  const rows=[['Browser',s.browser],['OS',s.os],['MP4 / H.264',s.mp4?'Supported':'Fallback'],['WebM',s.webm?'Supported':'Fallback'],['HLS',s.hls?'Native':'App fallback'],['Fetch',s.fetch?'Available':'Limited'],['Abort',s.abort?'Available':'Limited'],['Pointer input',s.pointer?'Available':'Fallback']];
  box.innerHTML=rows.map(([k,v])=>`<div class="browser-compat-item"><span class="k">${esc(k)}</span><span class="v">${esc(v)}</span></div>`).join('');
}

async function validateSettingsV54(){
  const [tg,meta,send,web,pre,icons]=await Promise.allSettled([
    api('/api/settings/telegram'),api('/api/settings/metadata'),api('/api/settings/send'),api('/api/settings/weblogin'),api('/api/media-manager/preflight'),api('/api/icons/status')
  ]);
  const ok=(x)=>x.status==='fulfilled'&&x.value&&x.value._ok!==false;
  const tgOk=ok(tg)&&!!tg.value.configured, metaOk=ok(meta)&&!!(meta.value.tpdb_token||meta.value.stashdb_api_key), webOk=ok(web)&&!web.value.default_login;
  const msgs=[];
  msgs.push(tgOk?'Telegram configuration is valid.':'Telegram API credentials are incomplete or unverified.');
  msgs.push(metaOk?'At least one metadata provider is configured.':'No metadata provider key is configured.');
  msgs.push(webOk?'Web login is customized.':'Web login still uses the default password or is unavailable.');
  const depOk=ok(pre)&&!!pre.value.telethon?.ok&&!!pre.value.ffmpeg?.ok;
  msgs.push(depOk?'Media dependencies are ready.':'Media dependency checks need attention.');
  toast(msgs.join(' '),9000);
  updateSettingsPills({tg:tgOk,meta:metaOk,web:webOk});
}
function updateSettingsPills(x){
  const set=(id,label,good)=>{const e=$(id);if(e){e.innerHTML=`${axIcon(good?'check':'alert')} <span>${label}</span>`;e.className='settings-v54-pill '+(good?'v54-ok':'v54-warn');}};
  set('settings-pill-telegram','Telegram',!!x.tg);set('settings-pill-metadata','Metadata',!!x.meta);set('settings-pill-web','Web login',!!x.web);
}

async function loadSettingsStatus(force=false){
  loadBackupStatus();
  const [a,d,ic,meta,web]=await Promise.all([api('/api/telegram/auth-status'),api('/api/media-manager/preflight'),api('/api/icons/status'),api('/api/settings/metadata'),api('/api/settings/weblogin')]);
  const t=$('set-tg-status'),ts=$('tg-sess');
  const txt=a&&a._ok?(a.connected?`Telegram connected (saved session) — ${esc(a.status||'')}`:`Telegram not connected — ${esc(a.status||'log in below')}`):'Telegram status unavailable';
  if(t){t.innerHTML=`${axIcon(a&&a.connected?'check':'close')} <span>${txt}</span>`;t.style.color=a&&a.connected?'var(--green)':'var(--amber)';}
  if(ts)ts.textContent=a&&a.connected?'Session saved and connected. Use the steps below only to log in again or switch account.':'Not logged in: enter the phone number, then the code Telegram sends, then the 2FA password if asked.';
  if(d&&d._ok&&$('set-deps')){
    const labels={telethon:'Telethon',av:'PyAV',pillow:'Pillow',ffmpeg:'FFmpeg',ffprobe:'FFprobe'};
    const required=Object.keys(labels),missing=required.filter(k=>!d[k]?.ok).map(k=>labels[k]);
    const summary=missing.length?`${missing.length} missing: ${missing.join(', ')}`:'All found';
    const details=JSON.stringify(d,null,2);
    $('set-deps').innerHTML=`<span class="ax2-diagnostic-summary">${esc(summary)}</span><details class="ax2-diagnostic-details"><summary>Details</summary><pre>${esc(details)}</pre></details>`;
  }
  if(ic&&ic._ok&&$('set-icons')){
    const entries=Object.entries(ic.icons||{}),missing=entries.filter(([,value])=>!value).map(([name])=>({'vlc':'VLC','mpc':'MPC-HC','mpc-hc':'MPC-HC','telegram':'Telegram'}[name.toLowerCase()]||name));
    const summary=!missing.length?'Icons OK':`${entries.length-missing.length} of ${entries.length} icons OK · ${missing.join(', ')} missing`;
    const details=JSON.stringify({icons:ic.icons||{},folder:ic.folder||''},null,2);
    $('set-icons').innerHTML=`<span class="ax2-diagnostic-summary">${esc(summary)}</span><details class="ax2-diagnostic-details"><summary>Details</summary><pre>${esc(details)}</pre></details>`;
  }
  updateSettingsPills({tg:!!(a&&a.connected),meta:!!(meta&&(meta.tpdb_token||meta.stashdb_api_key)),web:!!(web&&!web.default_login)});
}

pInit();

// ═══════════════════════════════════════════════
// SECTION: MANUAL METADATA MAPPING / CORRECTION
// ═══════════════════════════════════════════════
const _mapSel=new Map();
let _mapMode=false;
let _mapContext='';
function mappingKey(cid,mid){return vkey(cid,mid);}
function dismissAx2LayerForMapping(){
  const closeTopLayer=window.ax2?.closeTopLayer;
  if(typeof closeTopLayer==='function')closeTopLayer({immediate:true});
}
function syncMapCardState(){
  document.querySelectorAll('.vc[data-cid][data-mid]').forEach(card=>{
    const cid=String(card.dataset.cid||'');
    const mid=Number(card.dataset.mid||0);
    const picked=_mapSel.has(mappingKey(cid,mid));
    card.classList.toggle('map-picked',picked);
    const cb=card.querySelector('.map-select');
    if(cb)cb.checked=picked;
  });
}
function toggleMapSelection(cb,cid,mid){
  const k=mappingKey(cid,mid);
  if(cb.checked)_mapSel.set(k,{channel_id:cid,message_id:Number(mid)});
  else _mapSel.delete(k);
  updateMapBar();
}
function mappingCardClick(event,card,cid,mid){
  if(!_mapMode){
    // A card opens details and warms the paused source; it is not a Play action.
    // Explicitly pause any active element first, including when the same video
    // is reopened and openD can reuse its existing source.
    const player=pv();
    if(player&&!player.paused){_P.userPaused=true;player.pause();}
    openD(mid,cid,false,card);return;
  }
  if(event?.target?.closest?.('.map-select,button,a,.tag'))return;
  const k=mappingKey(cid,mid);
  const cb=card?.querySelector?.('.map-select');
  const picked=!_mapSel.has(k);
  if(cb)cb.checked=picked;
  if(picked)_mapSel.set(k,{channel_id:cid,message_id:Number(mid)});
  else _mapSel.delete(k);
  updateMapBar();
}
function refreshMapMode(){
  document.querySelectorAll('.map-select').forEach(x=>{x.style.display=_mapMode?'block':'none';});
  syncMapCardState();
}
function enterMappingMode(context=''){
  dismissAx2LayerForMapping();
  closeMapMenus();
  _mapMode=true;
  _mapContext=String(context||_mapContext||'');
  const b=$('mapbar');if(b)b.style.display='flex';
  updateMapBar();
  refreshMapMode();
  toast('Pick videos, then choose what to change.');
  setTimeout(()=>document.querySelector('.map-select')?.focus(),0);
}
function exitMappingMode(){
  _mapMode=false;
  _mapContext='';
  _mapSel.clear();
  document.querySelectorAll('.map-select').forEach(x=>{x.checked=false;x.style.display='none';});
  document.querySelectorAll('.vc.map-picked').forEach(x=>x.classList.remove('map-picked'));
  const b=$('mapbar');if(b)b.style.display='none';
  _syncBulkButtons();
  refreshMapMode();
}
function updateMapBar(){
  const b=$('mapbar');if(!b)return;
  b.style.display=_mapMode?'flex':'none';
  $('mapcount').textContent=`${_mapSel.size} selected`;
  const sendButton=$('map-send-telegram');if(sendButton)sendButton.disabled=_mapSel.size===0;
  _syncBulkButtons();
  refreshMapMode();
}
// The "Edit multiple videos" buttons (Library, Performer, Studio) show the mode.
function _syncBulkButtons(){
  document.querySelectorAll('.entity-bulk-btn').forEach(btn=>{
    btn.classList.toggle('on',_mapMode);btn.setAttribute('aria-pressed',_mapMode?'true':'false');
    const lbl=btn.querySelector('.lbl');if(lbl)lbl.textContent=_mapMode?`Selecting · ${_mapSel.size}`:'Edit multiple videos';
  });
}
function clearMapSelection(){
  _mapSel.clear();
  document.querySelectorAll('.map-select').forEach(x=>x.checked=false);
  syncMapCardState();
  updateMapBar();
}
async function mapSelected(field,action='move'){
  if(!_mapSel.size){toast('Select at least one video.');return;}
  if(action==='move'&&field==='performer'){pkOpenBulk('performer');return;}
  if(action==='move'&&field==='site'){skOpenBulk('site');return;}
  if(action==='move'&&field==='performer_site'){psMapOpenBulk();return;}
  // V29.4.131: one prompt used to write the same text into title, performer
  // and studio. "Apply All Metadata" now asks for each value separately.
  if(action==='move'&&field==='all'){psMapOpenBulk(true);return;}
  let target=''; if(action!=='unknown'){target=await axPrompt(field==='title'?'Enter the corrected video title.':'Enter the corrected performer or studio value.','',{title:field==='title'?'Correct video title':'Correct metadata value',okLabel:'Apply value',placeholder:field==='title'?'Video title':'Performer or studio'});if(target===null)return;}
  if(action!=='unknown'&&!String(target||'').trim())return;
  const r=await api('/api/mapping/bulk',{method:'POST',body:JSON.stringify({videos:[..._mapSel.values()],field,target_name:String(target||'').trim(),action,lock:true})});
  if(!r.ok){toast(r.detail||'Mapping failed');return;} toast(`${r.changed||0} video(s) updated and locked.`);clearMapSelection();await _refreshAfterMapping();
}
function toggleMapMenu(id){dismissAx2LayerForMapping();closeMapMenus(id);const el=$(id);if(el)el.classList.toggle('open');}
function closeMapMenus(except=''){document.querySelectorAll('.map-menu-panel.open').forEach(x=>{if(x.id!==except)x.classList.remove('open');});}
document.addEventListener('click',ev=>{if(!ev.target.closest('.map-menu'))closeMapMenus();});
async function recheckSelected(){if(!_mapSel.size){toast('Select at least one video.');return;}const videos=[..._mapSel.values()];const r=await api('/api/mapping/recheck',{method:'POST',body:JSON.stringify({videos})});toast(r.ok?`Metadata recheck queued for ${r.count||videos.length} video(s).`:(r.detail||'Recheck failed'));}
async function unlockSelected(){if(!_mapSel.size)return;const videos=[..._mapSel.values()];const r=await api('/api/mapping/unlock',{method:'POST',body:JSON.stringify({videos,field:'all'})});toast(r.ok?'Mapping locks removed.':(r.detail||'Unlock failed'));}
const _vmr={id:null,cid:null,items:[],sel:0,seq:0,site:null};
function vmrClose(){const el=$('vmr');if(el)el.classList.remove('open');_vmr.id=null;_vmr.cid=null;_vmr.items=[];_vmr.site=null;}
function vmrOpen(mid,cid){
  const v=findVideo(mid,cid)||{};
  _vmr.id=Number(mid);_vmr.cid=String(cid);_vmr.items=[];_vmr.sel=0;_vmr.seq++;
  _vmr.site=(v.site_name||v.studio)?{name:String(v.site_name||v.studio),id:v.site_id||null}:null;
  $('vmr-title').textContent='Correct video metadata';
  $('vmr-name').value=String(v.api_title||v.title||v.name||v.telegram_filename||'');
  $('vmr-site-q').value='';
  vmrRenderSiteCurrent();
  $('vmr-site-list').innerHTML='<div class="muted" style="padding:8px">Type a studio name to search.</div>';
  $('vmr').classList.add('open');
  setTimeout(()=>$('vmr-name').focus(),50);
}
function vmrRenderSiteCurrent(){
  const box=$('vmr-site-cur');if(!box)return;
  box.innerHTML=_vmr.site?`<span class="pill">${esc(_vmr.site.name)} <span class="x" title="Remove studio" onclick="vmrClearSite()">×</span></span>`:'<span class="muted">No studio selected.</span>';
}
function vmrClearSite(){_vmr.site=null;vmrRenderSiteCurrent();}
function vmrSiteSearch(){clearTimeout(vmrSiteSearch._t);vmrSiteSearch._t=setTimeout(vmrSiteDoSearch,120);}
async function vmrSiteDoSearch(){
  const q=$('vmr-site-q').value.trim(),seq=++_vmr.seq;
  if(!q){$('vmr-site-list').innerHTML='<div class="muted" style="padding:8px">Type a studio name to search.</div>';return;}
  const r=await api('/api/studios?limit=20&q='+encodeURIComponent(q));
  if(seq!==_vmr.seq)return;
  const list=Array.isArray(r)?r:[];
  _vmr.items=list.map(x=>({name:x.name,id:x.id,count:x.video_count||0,image:x.image}));
  if(!list.some(x=>String(x.name||'').toLowerCase()===q.toLowerCase()))_vmr.items.push({name:q,create:true});
  _vmr.sel=0;vmrRenderSiteList();
}
function vmrRenderSiteList(){
  const q=$('vmr-site-q').value.trim().toLowerCase();
  const hl=s=>{const i=q?s.toLowerCase().indexOf(q):-1;return i<0?esc(s):esc(s.slice(0,i))+'<b style="color:var(--accent)">'+esc(s.slice(i,i+q.length))+'</b>'+esc(s.slice(i+q.length));};
  $('vmr-site-list').innerHTML=_vmr.items.length?_vmr.items.map((p,i)=>`<div class="pk-item${i===_vmr.sel?' on':''}" onmousedown="event.preventDefault();vmrSitePick(${i})">
    ${p.create?'<span class="pk-av"><svg class="ax-icon" aria-hidden="true"><use href="#ax-plus"></use></svg></span>':(p.image?`<img class="pk-av" src="/api/studios/${Number(p.id)}/image?v=${encodeURIComponent(p.image||"")}" onerror="this.replaceWith(Object.assign(document.createElement('span'),{className:'pk-av',textContent:''}))">`:'<span class="pk-av"><svg class="ax-icon" aria-hidden="true"><use href="#ax-circle"></use></svg></span>')}
    <div style="min-width:0;flex:1"><div style="font-size:13px">${p.create?'Create new studio “'+esc(p.name)+'”':hl(p.name)}</div>${p.create?'':`<div class="muted" style="font-size:11px">${p.count} video${p.count!==1?'s':''} · link to this studio</div>`}</div></div>`).join(''):'<div class="muted" style="padding:8px">No matching studios.</div>';
}
function vmrSiteKey(ev){
  if(ev.key==='ArrowDown'){ev.preventDefault();_vmr.sel=Math.min(_vmr.items.length-1,_vmr.sel+1);vmrRenderSiteList();}
  else if(ev.key==='ArrowUp'){ev.preventDefault();_vmr.sel=Math.max(0,_vmr.sel-1);vmrRenderSiteList();}
  else if(ev.key==='Enter'){ev.preventDefault();if(_vmr.items[_vmr.sel])vmrSitePick(_vmr.sel);}
  else if(ev.key==='Escape'){ev.preventDefault();vmrClose();}
}
function vmrSitePick(i){const p=_vmr.items[i];if(!p)return;_vmr.site={name:String(p.name||'').trim(),id:p.id||null,create:!!p.create};vmrRenderSiteCurrent();}
async function vmrSave(){
  if(!_vmr.id||!_vmr.cid){vmrClose();return;}
  const v=findVideo(_vmr.id,_vmr.cid)||{};
  const title=String($('vmr-name').value||'').trim();
  const orig=String(v.api_title||v.title||v.name||v.telegram_filename||'').trim();
  const tasks=[];
  if(title&&title!==orig){tasks.push({field:'title',target_name:title,action:'move',lock:true});}
  const origSite=String(v.site_name||v.studio||'').trim();
  const picked=_vmr.site?String(_vmr.site.name||'').trim():'';
  if(picked!==origSite){tasks.push({field:'site',site_id:_vmr.site?.id||null,target_name:picked,action:'move',lock:true});}
  if(!tasks.length){toast('No changes to save.');return;}
  for(const task of tasks){
    const r=await api('/api/mapping/bulk',{method:'POST',body:JSON.stringify({videos:[{channel_id:_vmr.cid,message_id:_vmr.id}],...task})});
    if(!r.ok){toast(r.detail||'Could not save mapping.');return;}
  }
  vmrClose();
  toast('Video title/studio mapping updated.');
  await _refreshAfterMapping();
}
async function openVideoMapping(mid,cid){vmrOpen(mid,cid);}

async function perfBulkMap(){if(_mapMode)return;if(!_perfVideos.length){toast('No videos on this performer.');return;}_mapSel.clear();enterMappingMode('performer');document.querySelector('#perf-video-grid')?.scrollIntoView({behavior:'smooth',block:'start'});}
// V29.4.131: the Library uses the same multi-video editor.
function libraryBulkEdit(){
  if(_mapMode){exitMappingMode();return;}
  if(!_filtV.length){toast('Pick a channel first, then choose the videos to edit.');return;}
  _mapSel.clear();enterMappingMode('library');
}
async function entityBulkEdit(kind){
  // Open the Videos tab first so the cards to pick from are on screen.
  try{
    const root=activeEntityDetailRoot();
    const active=root?.querySelector('.entity-detail-tab.active')?.dataset?.entityTab;
    if(active!=='videos')await switchEntityTab('videos');
  }catch(e){}
  if(_mapMode){document.querySelector(kind==='performer'?'#perf-video-grid':'#site-video-grid')?.scrollIntoView({behavior:'smooth',block:'start'});return;}
  if(kind==='performer')perfBulkMap();else siteBulkMap();
}
function selectAllMapVisible(){
  document.querySelectorAll('.vc[data-cid][data-mid]').forEach(card=>{
    if(!card.offsetParent)return;
    _mapSel.set(mappingKey(card.dataset.cid,card.dataset.mid),{channel_id:card.dataset.cid,message_id:Number(card.dataset.mid)});
  });
  updateMapBar();
}
async function refreshPerformerAfterMapping(){
  const id=Number(_perfCurId),detail=$('perf-detail');
  if(!id||!detail?.classList.contains('open'))return;
  const refreshSeq=++_perfMappingRefreshSeq,detailSeq=_perfSeq,navSeq=_perfNavSeq;
  const relationStudioId=Number(window._perfRelationStudioId)||0;
  const relationQuery=relationStudioId>0?`?studio_id=${relationStudioId}`:'';
  const r=await api(`/api/performers/${id}/videos${relationQuery}`,{cache:'no-store'});
  if(refreshSeq!==_perfMappingRefreshSeq||detailSeq!==_perfSeq||navSeq!==_perfNavSeq||id!==Number(_perfCurId)||!detail.classList.contains('open'))return;
  if(!r||!r.performer){setSt('Performer API failed: '+(r?.detail||'unknown error'));return;}
  const p=r.performer,videos=Array.isArray(r.videos)?r.videos:[];
  _perfVideos=videos;window._currentPerfEntity=p;
  const count=videos.length,countLabel=`${count.toLocaleString()} video${count===1?'':'s'}`;
  const heroCount=detail.querySelector('[data-performer-video-count]');if(heroCount)heroCount.textContent=countLabel;
  const setTabCount=(tab,value)=>{const badge=detail.querySelector(`.entity-detail-tab[data-entity-tab="${tab}"] .tab-count`);if(badge)badge.textContent=Number(value).toLocaleString();};
  setTabCount('videos',count);
  const studios=new Set(videos.flatMap(video=>Array.isArray(video.studios)?video.studios:[]).map(value=>String(value||'').trim()).filter(Boolean));
  setTabCount('studios',studios.size);
  const detailsCount=[...detail.querySelectorAll('.performer-overview-detail')].find(row=>row.querySelector('span')?.textContent.trim()==='Videos in Archive X')?.querySelector('strong');
  if(detailsCount)detailsCount.textContent=count.toLocaleString();
  if(!count&&_mapMode&&_mapContext==='performer')exitMappingMode();
  const videosPanel=detail.querySelector('.entity-tab-panel[data-tab-panel="videos"]');
  if(videosPanel?.classList.contains('active')){
    videosPanel.classList.add('ax2-quiet-refresh');
    await renderPerfTab('videos');
  }
}
async function _refreshAfterMapping(){
  if(_mapContext==='library'||(!_mapContext&&_curView()==='library')){await loadLib(null,false,true,true);return;}
  if(_mapContext==='performer'&&_perfCurId)await refreshPerformerAfterMapping();
  else if(_mapContext==='site'&&_siteCurId)await openSite(_siteCurId);
  else if(_perfCurId&&_curView()==='performers'&&$('perf-detail')?.classList.contains('open'))await refreshPerformerAfterMapping();
  else if(_siteCurId&&_curView()==='sites'&&$('site-detail')?.classList.contains('open'))await openSite(_siteCurId);
  else await loadLib();
}
function _curView(){
  const on=[['library','vl'],['performers','vp'],['sites','vsite']].find(([,id])=>{const el=$(id);return el&&el.offsetParent!==null;});
  return on?on[0]:'';
}
async function siteBulkMap(){if(_mapMode)return;if(!_siteVideos.length){toast('No videos on this studio.');return;}_mapSel.clear();enterMappingMode('site');document.querySelector('#site-video-grid')?.scrollIntoView({behavior:'smooth',block:'start'});}
// ═══════════════════════════════════════════════
// LOCAL AI ASSISTANCE
let _aiPageStatus=null,_aiPageTab='assistant';
function aiPageTab(tab){_aiPageTab=String(tab||'assistant');document.querySelectorAll('[data-ai-tab]').forEach(b=>{const active=b.dataset.aiTab===_aiPageTab;b.classList.toggle('on',active);if(active&&window.innerWidth<600)b.scrollIntoView({block:'nearest',inline:'nearest',behavior:matchMedia('(prefers-reduced-motion: reduce)').matches?'auto':'smooth'});});document.querySelectorAll('.ai-page-tab').forEach(e=>e.classList.toggle('on',e.id==='ai-tab-'+_aiPageTab));if(_aiPageTab==='models')aiRefreshModel();if(_aiPageTab==='diagnostics')aiExplainDiagnostics();if(_aiPageTab==='jobs')aiExplainJobs();}
function aiPageInputKeydown(e){if(e.key==='Enter'&&(e.ctrlKey||e.metaKey)){e.preventDefault();aiPageSend();}}
function aiPageRender(messages){const box=$('ai-page-history');if(!box)return;const rows=Array.isArray(messages)?messages:[];if(!rows.length){box.innerHTML='<div class="ai-page-empty">Ask about your library, jobs, diagnostics, metadata, playback or the current Archive X state.</div>';return;}box.innerHTML=rows.map(m=>`<div class="ai-page-msg ${m.role==='user'?'user':'assistant'}"><div class="ai-page-msg-meta">${m.role==='user'?'You':'Archive X Local AI'} · ${esc(m.created_at||'')}</div><div>${esc(m.content||'')}</div></div>`).join('');box.scrollTop=box.scrollHeight;}
function aiPageRenderStatus(r){if(!r)return;_aiPageStatus=r;const ready=!!r.configured,dot=$('ai-page-dot'),state=$('ai-page-state'),model=$('ai-page-model');if(dot)dot.className='ai-page-dot '+(ready?'ok':'warn');if(state)state.textContent=ready?'Local model ready':(r.runtime?.model?.exists?'Runtime configured — server not ready':'Local model not configured');if(model)model.textContent=(r.provider||'llama.cpp')+' · '+(r.model||'—');const m=r.runtime||{},runtime=m.runtime||{},mdl=m.model||{},server=m.server||{};const set=(id,v)=>{const e=$(id);if(e&&v!==undefined)e.value=v;};set('ai-runtime-path',runtime.path||'');set('ai-model-path',mdl.path||'');set('ai-model-id',r.model||'Qwen3-4B-Instruct-2507-Q4_K_M');set('ai-endpoint',server.endpoint||'http://127.0.0.1:8080/v1');set('ai-context',server.context||4096);set('ai-gpu-layers',server.gpu_layers||0);const rg=$('ai-readiness-grid');if(rg)rg.innerHTML=[['llama-server',runtime.exists?'Ready':'Missing'],['GGUF model',mdl.exists?'Ready':'Missing'],['Local endpoint',server.reachable?'Reachable':'Not running'],['Inference',m.ready?'Ready':'Not ready'],['Mode','CPU-first'],['Cloud API','Disabled']].map(x=>`<div><span>${esc(x[0])}</span><b>${esc(x[1])}</b></div>`).join('');}
async function aiRefreshModel(){const r=await api('/api/ai/status',{cache:'no-store'});if(r&&r._ok!==false)aiPageRenderStatus(r);return r;}
async function aiPageLoad(){const [st,h,t]=await Promise.all([api('/api/ai/status',{cache:'no-store'}),api('/api/ai/history',{cache:'no-store'}),api('/api/ai/tools',{cache:'no-store'})]);if(st&&st._ok!==false)aiPageRenderStatus(st);if(h&&h._ok!==false)aiPageRender(h.messages||[]);const tool=$('ai-page-tool-status');if(tool)tool.textContent=t&&t.local_only?'Read-only Archive X tools enabled.':'Tool registry unavailable.';}
async function aiPageSend(textOverride){const input=$('ai-page-input');const message=String(textOverride ?? input?.value ?? '').trim();if(!message)return;if(input&&!textOverride)input.value='';const box=$('ai-page-history');if(box){const empty=box.querySelector('.ai-page-empty');if(empty)empty.remove();box.insertAdjacentHTML('beforeend',`<div class="ai-page-msg user"><div class="ai-page-msg-meta">You · just now</div><div>${esc(message)}</div></div><div class="ai-page-msg assistant"><div class="ai-page-msg-meta">Archive X Local AI · working…</div><div>Analyzing local Archive X data…</div></div>`);box.scrollTop=box.scrollHeight;}const r=await api('/api/ai/chat',{method:'POST',body:JSON.stringify({message,page:location.pathname+location.search,selected:null}),cache:'no-store'});if(box)box.lastElementChild?.remove();if(!r||r._ok===false){if(box)box.insertAdjacentHTML('beforeend',`<div class="ai-page-msg assistant"><div class="ai-page-msg-meta">Archive X Local AI</div><div>${esc(r?.detail||'Local AI request failed.')}</div></div>`);return null;}if(box)box.insertAdjacentHTML('beforeend',`<div class="ai-page-msg assistant"><div class="ai-page-msg-meta">Archive X Local AI · ${esc(r.model||'')}</div><div>${esc(r.text||'')}</div></div>`);box.scrollTop=box.scrollHeight;return r;}
function aiQuick(prompt){aiPageTab('assistant');return aiPageSend(prompt);}
async function aiLibrarySearch(){const q=String($('ai-library-query')?.value||'').trim();if(!q){toast('Enter a library question.');return;}const r=await aiPageSend(`Search the published Archive X library for: ${q}. Return the most useful matching videos, performers, studios or tags and explain the result.`);const out=$('ai-library-result');if(out)out.innerHTML=`<div class="ai-analysis-result">${esc(r?.text||'No result')}</div>`;aiPageTab('library');}
async function aiExplainDiagnostics(){const out=$('ai-diagnostics-result');if(!out)return;out.textContent='Analyzing current diagnostics…';const r=await aiPageSend('Use the diagnostics tool to summarize current Archive X health, identify the highest-value issue, and explain the next verification step. Do not expose raw secrets or unrelated log dumps.');out.textContent=r?.text||'No diagnostics analysis available.';aiPageTab('diagnostics');}
async function aiExplainJobs(){const out=$('ai-jobs-result');if(!out)return;out.textContent='Analyzing jobs…';const r=await aiPageSend('Use the jobs tool to summarize active and recent Archive X jobs, their states, dependencies or blockers, and what should be checked next.');out.textContent=r?.text||'No job analysis available.';aiPageTab('jobs');}
async function aiExplainRecommendations(){const out=$('ai-recommendations-result');if(!out)return;out.textContent='Generating recommendations…';const r=await aiPageSend('Based only on current local Archive X state, give up to five useful recommendations for library quality, metadata review, diagnostics, or operational cleanup. Do not make changes and do not recommend overwriting protected manual overrides.');out.textContent=r?.text||'No recommendations available.';aiPageTab('recommendations');}
async function aiSaveModelSettings(){const body={runtime_path:String($('ai-runtime-path')?.value||'').trim(),model_path:String($('ai-model-path')?.value||'').trim(),model_id:String($('ai-model-id')?.value||'').trim(),endpoint:String($('ai-endpoint')?.value||'').trim(),context:Number($('ai-context')?.value||4096),gpu_layers:Number($('ai-gpu-layers')?.value||0)};const r=await api('/api/ai/config',{method:'POST',body:JSON.stringify(body)});const m=$('ai-model-message');if(m)m.textContent=r&&r.ok?'Local AI settings saved.':(r?.detail||'Could not save local AI settings.');if(r&&r.ok)aiPageRenderStatus(r.runtime||r);}
async function aiStartRuntime(){const m=$('ai-model-message');if(m)m.textContent='Starting llama-server…';const r=await api('/api/ai/runtime/start',{method:'POST',body:JSON.stringify({})});if(m)m.textContent=r&&r.ok?'Local model server is ready.':(r?.detail||'Could not start local model.');await aiRefreshModel();}
async function aiStopRuntime(){const r=await api('/api/ai/runtime/stop',{method:'POST',body:JSON.stringify({})});const m=$('ai-model-message');if(m)m.textContent=r&&r.ok?'Local model server stopped.':(r?.detail||'Could not stop local model.');await aiRefreshModel();}
async function aiTestRuntime(){const m=$('ai-model-message');if(m)m.textContent='Running local inference test…';const r=await api('/api/ai/test',{method:'POST',body:JSON.stringify({})});if(m)m.textContent=r&&r.ok?`Model test passed in ${r.latency_ms} ms: ${r.text}`:(r?.detail||'Local model test failed.');await aiRefreshModel();}
// AI Assistance page is intentionally dormant. Keep its implementation for a later release.

// V29.4.129: closing or reloading the tab releases the playing video, so its
// downloads stop and its temporary cache is deleted.
window.addEventListener('pagehide',()=>{
  try{if(_P&&_P.cid!=null&&_P.mid!=null&&_npMode!=='external')pReleaseServer(_P.cid,_P.mid,'tab_closed');}catch(x){}
});

// ═══════════════════════════════════════════════
// SECTION: V29.4.133 REVIEW OF EARLIER FILENAME AUTO-MATCHES
// ═══════════════════════════════════════════════
let _amrCount=0,_amrItems=[],_amrOpenSeq=0,_amrBulkBusy=false;
async function amrRefreshCount(requestSeq=null){
  const r=await api('/api/metadata/auto-match-review',{cache:'no-store'});
  if(requestSeq!==null&&(requestSeq!==_amrOpenSeq||!$('amr')?.classList.contains('open')))return _amrItems;
  if(r&&Array.isArray(r.items)){_amrItems=r.items;_amrCount=r.items.length;}
  return _amrItems;
}
async function amrOpen(){
  const requestSeq=++_amrOpenSeq,dialog=$('amr');
  dialog.classList.add('open');
  $('amr-list').innerHTML=window.AXSkeleton?window.AXSkeleton.amrRows(4):'<div class="muted">Loading…</div>';
  await amrRefreshCount(requestSeq);
  if(requestSeq!==_amrOpenSeq||!dialog.classList.contains('open'))return;
  amrRender();
}
function amrClose(){_amrOpenSeq++;$('amr').classList.remove('open');}
function amrSelectedIds(){return [...document.querySelectorAll('#amr-list .amr-select:checked')].map(x=>Number(x.value)).filter(x=>Number.isInteger(x)&&x>0);}
function amrSelectionChanged(){const ids=amrSelectedIds(),label=$('amr-selected-count');if(label)label.textContent=`${ids.length} selected`;const all=$('amr-select-all');if(all)all.checked=_amrItems.length>0&&_amrItems.slice(0,100).every(it=>ids.includes(Number(it.video_id)));}
function amrToggleFirst100(checked){const allowed=new Set((checked?_amrItems.slice(0,100):[]).map(it=>Number(it.video_id)));document.querySelectorAll('#amr-list .amr-select').forEach(x=>x.checked=allowed.has(Number(x.value)));amrSelectionChanged();if(checked&&_amrItems.length>100)toast('Selected the first 100 items. Review bulk actions are limited to 100 at a time.');}
function amrRender(){
  const box=$('amr-list');if(!box)return;
  if(!_amrItems.length){box.innerHTML='<div class="muted">Nothing left to review.</div>';amrSelectionChanged();return;}
  box.innerHTML=_amrItems.map(it=>`<div class="amr-item" data-video="${Number(it.video_id)}">
    <img class="amr-thumb" src="/api/thumb/${encodeURIComponent(it.channel_id)}/${Number(it.message_id)}?r=f3" alt="" loading="lazy" onerror="this.style.visibility='hidden'">
    <div class="amr-body">
      <div class="amr-file-row"><input class="amr-select" type="checkbox" value="${Number(it.video_id)}" aria-label="Select ${esc(it.filename||'video')} for bulk review" onchange="amrSelectionChanged()"><div class="amr-file" title="${esc(it.filename)}">${esc(it.filename||'(no filename)')}</div></div>
      <div class="amr-applied">Applied: <b>${esc(it.applied_title||'—')}</b>${it.applied_studio?` · ${esc(it.applied_studio)}`:''}${(it.applied_performers||[]).length?` · ${esc(it.applied_performers.join(', '))}`:''}</div>
      <div class="muted amr-why">${esc(it.reason||'')}</div>
    </div>
    <div class="amr-actions">
      <button class="btn sm" onclick="amrAct(${Number(it.video_id)},'keep',this)">Keep</button>
      <button class="btn sm pri" onclick="amrAct(${Number(it.video_id)},'undo',this)">Undo</button>
    </div>
  </div>`).join('');
  amrSelectionChanged();
}
async function amrAct(videoId,action,btn){
  const requestSeq=_amrOpenSeq;
  const row=btn?.closest('.amr-item');row?.querySelectorAll('button').forEach(b=>b.disabled=true);
  const r=await api(`/api/metadata/auto-match-review/${Number(videoId)}`,{method:'POST',body:JSON.stringify({action})});
  if(!r||!r.ok){toast(r?.detail||'Could not update this video');row?.querySelectorAll('button').forEach(b=>b.disabled=false);return;}
  _amrItems=_amrItems.filter(x=>Number(x.video_id)!==Number(videoId));_amrCount=_amrItems.length;
  toast(action==='keep'?'Match kept.':'Match removed; it is now a suggestion.');
  if(requestSeq===_amrOpenSeq&&$('amr')?.classList.contains('open'))amrRender();
}
async function amrBulkAct(action){
  if(_amrBulkBusy)return;
  const requestSeq=_amrOpenSeq;
  const ids=amrSelectedIds();if(!ids.length){toast('Select at least one video first.');return;}if(ids.length>100){toast('Review no more than 100 videos per bulk action.');return;}
  _amrBulkBusy=true;
  const controls=document.querySelectorAll('#amr .amr-bulk button');controls.forEach(b=>b.disabled=true);
  try{
    const r=await api('/api/metadata/auto-match-review/bulk',{method:'POST',body:JSON.stringify({action,video_ids:ids})});
    if(!r||!r.ok){toast(r?.detail||'Bulk review failed.');return;}
    if(requestSeq===_amrOpenSeq&&$('amr')?.classList.contains('open')){await amrRefreshCount(requestSeq);if(requestSeq===_amrOpenSeq&&$('amr')?.classList.contains('open'))amrRender();}
    toast(`Bulk ${action}: ${Number(r.processed||0)} updated, ${Number(r.skipped||0)} skipped, ${Number(r.failed||0)} failed.`);
  }catch(e){toast('Bulk review failed: '+String(e?.message||e));}
  finally{_amrBulkBusy=false;controls.forEach(b=>b.disabled=false);}
}

_axInstallBrowserThemeColorSync();
void initializeSessionGate(boot);
