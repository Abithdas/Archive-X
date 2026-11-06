/* ============================================================================
 * FILE: js/171-nobuf-buffer-cache.js
 * AREA: Player and watch page
 * LOAD: Classic script. Order is set by index.html, the HTML partials and
 *       _LAYERS in web/html.py. Later scripts can wrap earlier globals,
 *       so check docs/CODE_MAP.md before moving code between files.
 * SECTIONS (search for "SECTION:"):
 *   - Constants and shared state
 *   - Helpers and preferences
 *   - Playback drawer
 *   - Cache timeline
 *   - Close dialog and download tray
 *   - Keep-or-discard decision and badges
 *   - Close flow
 *   - Polling, timeline interaction and speed badge
 *   - Initialization
 * ============================================================================ */
/* NoBuf-inspired direct-file cache timeline, playback drawer and resumable buffer UX. */
(() => {
  'use strict';

  // ------------------------------------------------------------------
  // SECTION: Constants and shared state
  // Storage keys, defaults and module state.
  // ------------------------------------------------------------------
  const $ = id => document.getElementById(id);
  const PREF_KEY = 'ax_nobuf_playback_preferences_v1';
  const KEPT_KEY = 'ax_nobuf_kept_buffers_v1';
  const DECISION_KEY = 'ax_nobuf_buffer_decisions_v1';
  const KIB = 1024, MIB = 1024 * 1024;
  const DEFAULTS = {
    skipForward: 5, skipBackward: 5, fit: 'fit', rotation: 0, brightness: 1,
    pinned: false, prebufferBps: 512 * KIB, downloadBps: 0,
    customPrebuffer: 768, customPrebufferUnit: 'kb', customDownload: 0, customDownloadUnit: 'mb'
  };
  let partialCachePromptEnabled = window.__AX_PARTIAL_CACHE_PROMPT === true;
  let prefs = loadPrefs();
  let currentStatus = null, currentStatusAt = 0, currentKey = '', pollTimer = 0;
  let previewCoverageKey = '', previewCoverageExpiresAt = 0, previewCoverageTimes = [], previewCoverageReady = false, previewCoveragePending = null;
  let closeCheck = false, bypassClose = false, pendingClose = null, dialogBusy = false;
  let drawer = null, scrim = null, closeDialog = null, downloadTray = null, originalClose = null, originalPSkip = null, originalPAutoHide = null;
  const downloadCards = new Set();

  // ------------------------------------------------------------------
  // SECTION: Helpers and preferences
  // Formatting, preference storage and range status.
  // ------------------------------------------------------------------
  const formatBytes = n => {
    let v = Math.max(0, Number(n) || 0);
    for (const unit of ['B','KB','MB','GB','TB']) { if (v < 1024 || unit === 'TB') return `${v.toFixed(unit === 'B' ? 0 : 1)} ${unit}`; v /= 1024; }
    return `${v.toFixed(1)} TB`;
  };
  const formatRate = bps => !Number(bps) ? '∞' : `${Number(bps) >= MIB ? (Number(bps)/MIB).toFixed(Number(bps)%MIB===0?0:1)+' MB/s' : Math.round(Number(bps)/KIB)+' KB/s'}`;
  const escapeHtml = value => String(value ?? '').replace(/[&<>"']/g, ch => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[ch]));
  function loadPrefs() {
    try { return { ...DEFAULTS, ...JSON.parse(localStorage.getItem(PREF_KEY) || '{}') }; }
    catch { return { ...DEFAULTS }; }
  }
  function savePrefs() { try { localStorage.setItem(PREF_KEY, JSON.stringify(prefs)); } catch {} applyPreferences(); }
  function preferences() { return { ...prefs }; }
  window.AXNoBufPlayback = {
    preferences,
    setPartialCachePrompt(enabled) {
      partialCachePromptEnabled = !!enabled;
      window.__AX_PARTIAL_CACHE_PROMPT = partialCachePromptEnabled;
    }
  };
  const videoKey = (cid, mid) => `${String(cid)}:${Number(mid)}`;
  function loadObject(key) { try { const v = JSON.parse(localStorage.getItem(key) || '{}'); return v && typeof v === 'object' ? v : {}; } catch { return {}; } }
  function saveObject(key, value) { try { localStorage.setItem(key, JSON.stringify(value)); } catch {} }
  function activeVideo() {
    try { if (typeof _P !== 'undefined' && _P.cid != null && _P.mid != null) return { cid: String(_P.cid), mid: Number(_P.mid), key: videoKey(_P.cid,_P.mid) }; } catch {}
    return null;
  }
  async function getRangeStatus(cid, mid) {
    if (typeof api !== 'function') return null;
    const r = await api(`/api/play/range-cache/${encodeURIComponent(cid)}/${encodeURIComponent(mid)}`, { cache: 'no-store' });
    return r && r._ok !== false && r.ok !== false ? r : null;
  }

  // ------------------------------------------------------------------
  // SECTION: Playback drawer
  // Builds, places and handles the options drawer.
  // ------------------------------------------------------------------
  function makeButton(label, value, group) {
    const b = document.createElement('button'); b.type = 'button'; b.className = 'ax-nobuf-option'; b.dataset.group = group; b.dataset.value = String(value); b.textContent = label;
    b.classList.toggle('is-selected', String(prefs[group]) === String(value));
    return b;
  }
  function optionRow(items, group) {
    const row = document.createElement('div'); row.className = 'ax-nobuf-option-row';
    items.forEach(([label,value]) => row.appendChild(makeButton(label,value,group)));
    return row;
  }
  function buildDrawer() {
    const top = $('dtop'), right = $('pright');
    if (!top || !right) return;
    top.classList.add('ax-nobuf-enabled');
    let toggle = $('ax-nobuf-settings-toggle');
    if (!toggle) {
      toggle = document.createElement('button'); toggle.type = 'button'; toggle.id = 'ax-nobuf-settings-toggle';
      toggle.title = 'Advanced playback settings'; toggle.setAttribute('aria-label','Advanced playback settings'); toggle.setAttribute('aria-expanded','false'); toggle.textContent = '⚙';
      toggle.addEventListener('click', openDrawer);
      const full = $('pfull'); right.insertBefore(toggle, full || null);
    }
    if (!scrim) {
      scrim = document.createElement('div'); scrim.className = 'ax-nobuf-settings-scrim'; scrim.hidden = true;
      scrim.addEventListener('click', closeDrawer); top.appendChild(scrim);
    }
    if (!drawer) {
      drawer = document.createElement('aside'); drawer.id = 'ax-nobuf-settings-drawer'; drawer.className = 'ax-nobuf-settings-drawer';
      drawer.setAttribute('aria-label','Advanced playback settings'); drawer.hidden = true;
      drawer.innerHTML = `
        <div class="ax-nobuf-settings-head"><strong>⚙ &nbsp;Settings</strong><button class="ax-nobuf-close" type="button" data-action="close" aria-label="Close settings">×</button></div>
        <section class="ax-nobuf-group"><div class="ax-nobuf-group-title">Playback</div>
          <div class="ax-nobuf-setting-row"><label class="ax-nobuf-setting-label">Skip forward</label><div id="ax-nobuf-skip-forward" class="ax-nobuf-option-row"></div><div class="ax-nobuf-number-row"><input id="ax-nobuf-skip-forward-custom" type="number" min="1" max="600" step="1" aria-label="Custom skip forward seconds"><span>seconds</span><button class="ax-nobuf-option" type="button" data-action="custom-skip" data-direction="skipForward">Set</button></div></div>
          <div class="ax-nobuf-setting-row"><label class="ax-nobuf-setting-label">Skip backward</label><div id="ax-nobuf-skip-backward" class="ax-nobuf-option-row"></div><div class="ax-nobuf-number-row"><input id="ax-nobuf-skip-backward-custom" type="number" min="1" max="600" step="1" aria-label="Custom skip backward seconds"><span>seconds</span><button class="ax-nobuf-option" type="button" data-action="custom-skip" data-direction="skipBackward">Set</button></div></div>
        </section>
        <section class="ax-nobuf-group"><div class="ax-nobuf-group-title">Display</div>
          <div class="ax-nobuf-setting-row"><label class="ax-nobuf-setting-label">Video fit</label><div id="ax-nobuf-fit" class="ax-nobuf-option-row"></div></div>
          <div class="ax-nobuf-setting-row"><label class="ax-nobuf-setting-label">Rotation</label><div id="ax-nobuf-rotation" class="ax-nobuf-option-row"></div></div>
          <div class="ax-nobuf-setting-row"><label class="ax-nobuf-setting-label">Brightness <span id="ax-nobuf-brightness-value" style="float:right">1.0</span></label><input id="ax-nobuf-brightness" class="ax-nobuf-range" type="range" min="0.5" max="1.8" step="0.1" value="1" aria-label="Video brightness"></div>
        </section>
        <section class="ax-nobuf-group"><div class="ax-nobuf-group-title">Behavior</div><label class="ax-nobuf-check"><input id="ax-nobuf-pin" type="checkbox"> Show pin button / keep player controls visible</label></section>
        <section class="ax-nobuf-group"><div class="ax-nobuf-group-title">Bandwidth</div>
          <div class="ax-nobuf-setting-row"><label class="ax-nobuf-setting-label">Prebuffer speed</label><div id="ax-nobuf-prebuffer" class="ax-nobuf-option-row"></div><div class="ax-nobuf-number-row"><input id="ax-nobuf-prebuffer-custom" type="number" min="0" max="20480" step="1" aria-label="Custom prebuffer rate"><select id="ax-nobuf-prebuffer-unit" aria-label="Custom prebuffer speed unit"><option value="kb">KB/s</option><option value="mb">MB/s</option></select><button class="ax-nobuf-option" type="button" data-action="custom-rate" data-rate="prebuffer">Set</button></div><div id="ax-nobuf-prebuffer-active" class="ax-nobuf-active-pill" style="margin-top:7px">Active: —</div></div>
          <div class="ax-nobuf-setting-row"><label class="ax-nobuf-setting-label">Download speed</label><div id="ax-nobuf-download" class="ax-nobuf-option-row"></div><div class="ax-nobuf-number-row"><input id="ax-nobuf-download-custom" type="number" min="0" max="20480" step="1" aria-label="Custom download rate"><select id="ax-nobuf-download-unit" aria-label="Custom download speed unit"><option value="kb">KB/s</option><option value="mb" selected>MB/s</option></select><button class="ax-nobuf-option" type="button" data-action="custom-rate" data-rate="download">Set</button></div><div id="ax-nobuf-download-active" class="ax-nobuf-active-pill" style="margin-top:7px">Active: —</div></div>
          <div class="ax-nobuf-cache-legend"><span class="ax-nobuf-legend-item"><i class="ax-nobuf-legend-swatch" style="background:#20dc89"></i>Disk cache</span><span class="ax-nobuf-legend-item"><i class="ax-nobuf-legend-swatch" style="background:#ffc107"></i>Buffered</span></div>
          <div class="ax-nobuf-muted">Prebuffer speed limits speculative background reads only. Download speed applies to “Continue Download”; normal playback keeps priority. Cached timeline coverage uses saved keyframe anchors when available; files without a usable index show an approximate byte-to-time map.</div>
        </section>
        <section class="ax-nobuf-group"><div class="ax-nobuf-group-title">Video info</div><div class="ax-nobuf-video-stats"><span>Resolution</span><strong id="ax-nobuf-info-resolution">—</strong><span>Duration</span><strong id="ax-nobuf-info-duration">—</strong><span>File size</span><strong id="ax-nobuf-info-size">—</strong><span>Cache</span><strong id="ax-nobuf-info-cache">—</strong></div><div id="ax-nobuf-info-note" class="ax-nobuf-muted">Range cache is local to this Archive X instance.</div></section>`;
      top.appendChild(drawer);
      drawer.addEventListener('click', handleDrawerClick);
      drawer.addEventListener('input', handleDrawerInput);
      drawer.addEventListener('change', handleDrawerChange);
    }
    renderOptions(); applyPreferences();
  }
  const rateChoices = [['∞',0],['256 KB/s',256*KIB],['512 KB/s',512*KIB],['1 MB/s',MIB],['2 MB/s',2*MIB],['5 MB/s',5*MIB],['10 MB/s',10*MIB],['20 MB/s',20*MIB]];
  function renderOptions() {
    if (!drawer) return;
    $('ax-nobuf-skip-forward').replaceChildren(...[5,10,15,30].map(v=>makeButton(`${v}s`,v,'skipForward')));
    $('ax-nobuf-skip-backward').replaceChildren(...[5,10,15,30].map(v=>makeButton(`${v}s`,v,'skipBackward')));
    $('ax-nobuf-fit').replaceChildren(...[['Original','original'],['Fit','fit'],['Fill','fill']].map(([l,v])=>makeButton(l,v,'fit')));
    $('ax-nobuf-rotation').replaceChildren(...[0,90,180,270].map(v=>makeButton(`${v}°`,v,'rotation')));
    $('ax-nobuf-prebuffer').replaceChildren(...rateChoices.map(([l,v])=>makeButton(l,v,'prebufferBps')));
    $('ax-nobuf-download').replaceChildren(...rateChoices.map(([l,v])=>makeButton(l,v,'downloadBps')));
    $('ax-nobuf-skip-forward-custom').value = [5,10,15,30].includes(Number(prefs.skipForward)) ? '' : String(prefs.skipForward);
    $('ax-nobuf-skip-backward-custom').value = [5,10,15,30].includes(Number(prefs.skipBackward)) ? '' : String(prefs.skipBackward);
    $('ax-nobuf-prebuffer-custom').value = prefs.customPrebuffer ?? '';
    $('ax-nobuf-prebuffer-unit').value = prefs.customPrebufferUnit || 'kb';
    $('ax-nobuf-prebuffer-custom').max = prefs.customPrebufferUnit === 'mb' ? '20' : '20480';
    $('ax-nobuf-download-custom').value = prefs.customDownload ?? '';
    $('ax-nobuf-download-unit').value = prefs.customDownloadUnit || 'mb';
    $('ax-nobuf-download-custom').max = prefs.customDownloadUnit === 'mb' ? '20' : '20480';
    $('ax-nobuf-brightness').value = prefs.brightness;
    $('ax-nobuf-pin').checked = !!prefs.pinned;
    $('ax-nobuf-prebuffer-active').textContent = `Active: ${formatRate(prefs.prebufferBps)}`;
    $('ax-nobuf-download-active').textContent = `Active: ${formatRate(prefs.downloadBps)}`;
  }
  // Settings live inside the player whenever it is big enough (and always in
  // fullscreen, where only the player subtree is visible). On a small player
  // (phones in portrait) they become a full-height side sheet on the page.
  function placeDrawer() {
    const top = $('dtop'); if (!top || !drawer || !scrim) return;
    const fs = document.fullscreenElement || document.webkitFullscreenElement;
    const r = top.getBoundingClientRect();
    const inPlayer = !!fs || (r.height >= 330 && r.width >= 460);
    const host = inPlayer ? top : document.body;
    drawer.classList.toggle('ax-nobuf-in-player', inPlayer); scrim.classList.toggle('ax-nobuf-in-player', inPlayer);
    if (drawer.parentElement !== host) host.appendChild(drawer);
    if (scrim.parentElement !== host) host.insertBefore(scrim, drawer);
  }
  function openDrawer() {
    buildDrawer(); if (!drawer || !scrim) return;
    placeDrawer();
    drawer.hidden = false; scrim.hidden = false;
    $('ax-nobuf-settings-toggle')?.setAttribute('aria-expanded','true');
    document.body.classList.add('ax-nobuf-settings-open');
    updateVideoInfo();
    if (typeof pShowUI === 'function') pShowUI();
  }
  function closeDrawer() {
    if (drawer) drawer.hidden = true; if (scrim) scrim.hidden = true;
    $('ax-nobuf-settings-toggle')?.setAttribute('aria-expanded','false');
    document.body.classList.remove('ax-nobuf-settings-open');
  }
  function applyPreferences() {
    const v = $('pv'), top = $('dtop');
    if (v) {
      // The existing player theme uses several !important media rules; use
      // inline important overrides so these preferences win consistently.
      v.style.setProperty('object-fit', prefs.fit === 'fill' ? 'cover' : (prefs.fit === 'original' ? 'none' : 'contain'), 'important');
      v.style.setProperty('width', '100%', 'important');
      v.style.setProperty('height', '100%', 'important');
      v.style.setProperty('max-width', '100%', 'important');
      v.style.setProperty('max-height', '100%', 'important');
      v.style.setProperty('transform', `rotate(${Number(prefs.rotation)||0}deg)`, 'important');
      v.style.setProperty('filter', `brightness(${Math.max(.5,Math.min(1.8,Number(prefs.brightness)||1))})`, 'important');
    }
    if (top) top.classList.toggle('ax-nobuf-pinned', !!prefs.pinned);
    const f = $('pf10')?.querySelector('.skip-seconds'), b = $('pb10')?.querySelector('.skip-seconds');
    if (f) f.textContent = String(prefs.skipForward); if (b) b.textContent = String(prefs.skipBackward);
    if (f && $('pf10')) $('pf10').title = `Forward ${prefs.skipForward} seconds`;
    if (b && $('pb10')) $('pb10').title = `Back ${prefs.skipBackward} seconds`;
    if (drawer) {
      drawer.querySelectorAll('.ax-nobuf-option[data-group]').forEach(btn=>btn.classList.toggle('is-selected',String(btn.dataset.value)===String(prefs[btn.dataset.group])));
      $('ax-nobuf-brightness').value = prefs.brightness;
      $('ax-nobuf-brightness-value').textContent = Number(prefs.brightness).toFixed(1);
      $('ax-nobuf-pin').checked = !!prefs.pinned;
      $('ax-nobuf-prebuffer-active').textContent = `Active: ${formatRate(prefs.prebufferBps)}`;
      $('ax-nobuf-download-active').textContent = `Active: ${formatRate(prefs.downloadBps)}`;
    }
  }
  function handleDrawerInput(event) {
    if (event.target?.id === 'ax-nobuf-brightness') { prefs.brightness=Number(event.target.value)||1; savePrefs(); }
  }
  function handleDrawerChange(event) {
    if (event.target?.id === 'ax-nobuf-pin') { prefs.pinned=!!event.target.checked; savePrefs(); }
    if (event.target?.id === 'ax-nobuf-prebuffer-unit') { prefs.customPrebufferUnit=event.target.value; $('ax-nobuf-prebuffer-custom').max=event.target.value==='mb'?'20':'20480'; }
    if (event.target?.id === 'ax-nobuf-download-unit') { prefs.customDownloadUnit=event.target.value; $('ax-nobuf-download-custom').max=event.target.value==='mb'?'20':'20480'; }
  }
  function handleDrawerClick(event) {
    const btn = event.target.closest('button'); if (!btn) return;
    if (btn.dataset.action === 'close') { closeDrawer(); return; }
    const group = btn.dataset.group;
    if (group) {
      prefs[group] = group === 'fit' ? btn.dataset.value : group === 'rotation' ? Number(btn.dataset.value) : Number(btn.dataset.value);
      savePrefs(); renderOptions(); return;
    }
    if (btn.dataset.action === 'custom-skip') {
      const dir=btn.dataset.direction, input=$(dir==='skipForward'?'ax-nobuf-skip-forward-custom':'ax-nobuf-skip-backward-custom');
      const parsed=Number(input.value);
      if(!String(input.value).trim()||!Number.isFinite(parsed)||parsed<=0)return;
      const value=Math.max(1,Math.min(600,Math.round(parsed)));
      prefs[dir]=value; savePrefs(); renderOptions(); return;
    }
    if (btn.dataset.action === 'custom-rate') {
      const dir=btn.dataset.rate, input=$(dir==='prebuffer'?'ax-nobuf-prebuffer-custom':'ax-nobuf-download-custom'), unit=$(dir==='prebuffer'?'ax-nobuf-prebuffer-unit':'ax-nobuf-download-unit');
      const max=unit.value==='mb'?20:20480;
      const raw=Math.max(0,Math.min(max,Number(input.value)||0));
      const bps=raw * (unit.value === 'mb' ? MIB : KIB);
      if (dir==='prebuffer') { prefs.prebufferBps=bps; prefs.customPrebuffer=raw; prefs.customPrebufferUnit=unit.value; }
      else { prefs.downloadBps=bps; prefs.customDownload=raw; prefs.customDownloadUnit=unit.value; }
      savePrefs(); renderOptions(); return;
    }
  }

  // ------------------------------------------------------------------
  // SECTION: Cache timeline
  // Timeline ranges, video info and cache status.
  // ------------------------------------------------------------------
  function formatDuration(s) {
    s=Math.max(0,Math.floor(Number(s)||0)); const h=Math.floor(s/3600),m=Math.floor((s%3600)/60),sec=s%60;
    return h ? `${h}:${String(m).padStart(2,'0')}:${String(sec).padStart(2,'0')}` : `${m}:${String(sec).padStart(2,'0')}`;
  }
  function nativeRanges() {
    const v=$('pv'), ranges=[];
    if (!v) return ranges;
    let duration=0, offset=0;
    try { duration=typeof pDur==='function'?Number(pDur())||0:Number(v.duration)||0; } catch {}
    try { offset=typeof _P!=='undefined'?Number(_P.off)||0:0; } catch {}
    if (!(duration>0)) duration=Number(v.duration)||0;
    if (!(duration>0)) return ranges;
    try {
      for (let i=0;i<v.buffered.length;i++) {
        const start=Math.max(0,Math.min(100,(offset+v.buffered.start(i))/duration*100));
        const end=Math.max(start,Math.min(100,(offset+v.buffered.end(i))/duration*100));
        ranges.push({start,end});
      }
    } catch {}
    return ranges;
  }
  function ensurePreviewCoverage(active) {
    if (!active) return;
    const now=Date.now();
    if (previewCoverageKey===active.key && now<previewCoverageExpiresAt) return;
    if (previewCoveragePending?.key===active.key) return;
    previewCoverageKey=active.key; previewCoverageTimes=[]; previewCoverageReady=false; previewCoverageExpiresAt=now+20000;
    const flight={key:active.key,promise:null};
    flight.promise=(async()=>{
      try {
        const response=await fetch(`/api/play/preview-manifest/${encodeURIComponent(active.cid)}/${encodeURIComponent(active.mid)}`,{credentials:'include',cache:'no-store'});
        if(!response.ok)throw new Error(`preview manifest ${response.status}`);
        const manifest=await response.json();
        if(activeVideo()?.key!==active.key || previewCoverageKey!==active.key)return;
        const ready=manifest?.frames_ready===true && Number(manifest?.frame_count||0)>=36 && Array.isArray(manifest?.frame_times);
        previewCoverageReady=!!ready;
        previewCoverageTimes=ready?manifest.frame_times.map(Number).filter(t=>Number.isFinite(t)&&t>=0):[];
        previewCoverageExpiresAt=Date.now()+(ready?120000:20000);
      } catch {
        if(previewCoverageKey===active.key) { previewCoverageReady=false; previewCoverageTimes=[]; previewCoverageExpiresAt=Date.now()+20000; }
      } finally {
        if(previewCoveragePending===flight)previewCoveragePending=null;
        if(activeVideo()?.key===active.key)renderTimeline(currentStatus);
      }
    })();
    previewCoveragePending=flight;
  }
  function ensureTimeline() {
    const seek=$('pseek'), track=seek?.querySelector('.trk'); if (!seek || !track) return null;
    // Remove the previous overlay implementation if hot-reloaded; played,
    // disk-cached, and browser-buffered ranges occupy separate lanes inside
    // the existing seek rail.
    track.querySelectorAll('.ax-nobuf-cache-track,.ax-nobuf-browser-track').forEach(node=>node.remove());
    let rows=track.querySelector('.ax-nobuf-hover-rows');
    if(!rows || !rows.querySelector('[data-ax-timeline-row="played"]')){
      rows?.remove();
      rows=document.createElement('div'); rows.className='ax-nobuf-hover-rows'; rows.setAttribute('aria-hidden','true');
      rows.innerHTML=[
        ['played','Played'],
        ['disk','Verified disk cache'],
        ['preview','Buffered']
      ].map(([key,label])=>`<div class="ax-nobuf-timeline-row ax-nobuf-timeline-row-${key}" data-ax-timeline-row="${key}" title="${label}"><span class="ax-nobuf-row-baseline"></span><span class="ax-nobuf-row-ranges"></span></div>`).join('');
      track.appendChild(rows);
    }
    return {
      track, rows,
      played:rows.querySelector('[data-ax-timeline-row="played"] .ax-nobuf-row-ranges'),
      disk:rows.querySelector('[data-ax-timeline-row="disk"] .ax-nobuf-row-ranges'),
      preview:rows.querySelector('[data-ax-timeline-row="preview"] .ax-nobuf-row-ranges')
    };
  }
  function paintTimelineRanges(target,ranges) {
    if(!target)return;
    const normalized=[];
    (Array.isArray(ranges)?ranges:[]).forEach(range=>{
      let start=Math.max(0,Math.min(100,Number(range?.start)||0));
      let end=Math.max(start,Math.min(100,Number(range?.end)||0));
      if(end-start<0.08)return;
      normalized.push({left:`${start}%`,width:`${Math.max(.08,end-start)}%`});
    });
    const existing=Array.from(target.children);
    if(existing.length===normalized.length&&existing.every(el=>el.classList.contains('ax-nobuf-timeline-range'))){
      existing.forEach((el,index)=>{
        const next=normalized[index];
        if(el.style.left!==next.left)el.style.left=next.left;
        if(el.style.width!==next.width)el.style.width=next.width;
      });
      return;
    }
    target.replaceChildren(...normalized.map(range=>{
      const el=document.createElement('i');
      el.className='ax-nobuf-timeline-range';
      el.style.left=range.left;el.style.width=range.width;
      return el;
    }));
  }
  function renderTimeline(status=currentStatus) {
    const parts=ensureTimeline(); if (!parts) return;
    const statusKey=status ? videoKey(status.channel_id,status.message_id) : '';
    const active=activeVideo();
    const matches=!!active && statusKey===active.key;
    const size=Number(status?.size)||Number(status?._size)||0;
    const info=(()=>{try{return typeof _P!=='undefined'?_P.info:null;}catch{return null;}})();
    let duration=Number(info?.duration)||Number(status?.duration)||0;
    try { if(typeof pDur==='function')duration=Number(pDur())||duration; } catch {}
    if(!(duration>0))duration=Number($('pv')?.duration)||0;
    if(active)ensurePreviewCoverage(active);

    const diskRanges=[];
    const mapped=matches && duration>0 && status?.time_mapping==='keyframes' && Array.isArray(status.time_ranges) && status.time_ranges.length;
    if(mapped){
      status.time_ranges.forEach(range=>{
        const start=Math.max(0,Math.min(100,(Number(range.start_seconds)||0)/duration*100));
        const end=Math.max(start,Math.min(100,(Number(range.end_seconds)||0)/duration*100));
        diskRanges.push({start,end});
      });
    } else {
      const ranges=matches && size>0 && Array.isArray(status?.ranges) ? status.ranges : [];
      ranges.forEach(range=>{
        const start=Math.max(0,Math.min(100,(Number(range.start_byte)||0)/size*100));
        const end=Math.max(start,Math.min(100,((Number(range.end_byte)||0)+1)/size*100));
        diskRanges.push({start,end});
      });
    }

    let currentSeconds=0;
    try { currentSeconds=typeof pCur==='function'?Number(pCur())||0:Number($('pv')?.currentTime)||0; } catch {}
    const playedPct=duration>0?Math.max(0,Math.min(100,currentSeconds/duration*100)):0;
    paintTimelineRanges(parts.played,playedPct>0?[{start:0,end:playedPct}]:[]);
    paintTimelineRanges(parts.disk,diskRanges);
    paintTimelineRanges(parts.preview,nativeRanges());

    const pct=Number(status?.percent)||0, cached=Number(status?.cached_bytes)||0, total=Number(status?.size)||0;
    const infoCache=$('ax-nobuf-info-cache');
    if(infoCache && drawer && !drawer.hidden){const next=total?`${pct.toFixed(1)}% · ${formatBytes(cached)}${status?.complete?' · Cached locally':' · Streaming from Telegram'}`:'—';if(infoCache.textContent!==next)infoCache.textContent=next;}
    const desc=$('ax-nobuf-info-note');
    const mapLabel=status?.time_mapping==='keyframes'?'keyframe-calibrated': 'approximate';
    if(desc && drawer && !drawer.hidden){const next=total?`${Number(status.segments_cached)||0} of ${Number(status.total_segments)||0} byte segments cached. Timeline uses ${mapLabel} byte-to-time mapping.`:'Range cache is local to this Archive X instance.';if(desc.textContent!==next)desc.textContent=next;}
  }
  function updateVideoInfo() {
    if (!drawer) return;
    const v=$('pv'), info=(()=>{try{return typeof _P!=='undefined'?_P.info:null;}catch{return null;}})();
    const w=Number(v?.videoWidth||info?.width||0),h=Number(v?.videoHeight||info?.height||0);
    $('ax-nobuf-info-resolution').textContent=w&&h?`${w}×${h}`:'—';
    const d=Number(v?.duration||info?.duration||0); $('ax-nobuf-info-duration').textContent=d?formatDuration(d):'—';
    $('ax-nobuf-info-size').textContent=formatBytes(Number(info?.size||currentStatus?.size||0)) || '—';
    renderTimeline(currentStatus);
  }
  async function refreshCacheStatus(force=false) {
    const active=activeVideo(); if (!active || !$('do')?.classList.contains('open')) return;
    const now=Date.now();
    if (!force && currentKey===active.key && now-currentStatusAt<1100) { renderTimeline(currentStatus); return; }
    currentKey=active.key;
    const status=await getRangeStatus(active.cid,active.mid);
    if (activeVideo()?.key!==active.key) return;
    currentStatus=status; currentStatusAt=Date.now();
    renderTimeline(status); updateVideoInfo();
  }

  // ------------------------------------------------------------------
  // SECTION: Close dialog and download tray
  // Dialog and download cards shown when closing a partial video.
  // ------------------------------------------------------------------
  function ensureCloseDialog() {
    if (closeDialog) return closeDialog;
    closeDialog=document.createElement('div'); closeDialog.id='ax-nobuf-close-dialog'; closeDialog.className='ax-nobuf-close-overlay'; closeDialog.hidden=true;
    closeDialog.setAttribute('role','dialog'); closeDialog.setAttribute('aria-modal','true'); closeDialog.setAttribute('aria-labelledby','ax-nobuf-close-title');
    closeDialog.innerHTML=`<section class="ax-nobuf-close-card"><div class="ax-nobuf-close-head"><div><h2 id="ax-nobuf-close-title" class="ax-nobuf-close-title">Video partially cached</h2><p id="ax-nobuf-close-desc" class="ax-nobuf-close-desc">Some of this video's bytes are stored locally. Choose what to do with the saved buffer.</p></div><button type="button" class="ax-nobuf-close-x" data-cache-action="keep" aria-label="Close and keep current buffers">×</button></div><div class="ax-nobuf-close-progress"><span id="ax-nobuf-close-progress-fill"></span></div><p id="ax-nobuf-close-stat" class="ax-nobuf-close-stat">—</p><div id="ax-nobuf-close-status" class="ax-nobuf-close-stat" role="status" aria-live="polite"></div><div class="ax-nobuf-close-actions"><button class="ax-nobuf-close-action is-download" id="ax-nobuf-continue-download" type="button" data-cache-action="download"><strong><span class="ico">↓</span>Continue Download</strong><small>Save the original file; cached ranges will be reused.</small></button><button class="ax-nobuf-close-action is-keep" type="button" data-cache-action="keep"><strong><span class="ico">✓</span>Keep Current Buffers</strong><small>Retain cached bytes for faster playback next time.</small></button><button class="ax-nobuf-close-action is-discard" type="button" data-cache-action="discard"><strong><span class="ico">▤</span>Close &amp; Discard Cache</strong><small>Delete this video's cached byte ranges.</small></button></div></section>`;
    closeDialog.addEventListener('click',event=>{
      const btn=event.target.closest('[data-cache-action]'); if (!btn || dialogBusy) return;
      const action=btn.dataset.cacheAction;
      if(action==='download')continueDownload();
      else if(action==='keep')finishClose('keep');
      else if(action==='discard')finishClose('discard');
    });
    (document.getElementById('ax-global-overlay-root')||document.body).appendChild(closeDialog);
    return closeDialog;
  }
  function ensureDownloadTray() {
    if (downloadTray && downloadTray.isConnected) return downloadTray;
    downloadTray = $('ax-nobuf-download-tray');
    if (!downloadTray) {
      downloadTray = document.createElement('div');
      downloadTray.id = 'ax-nobuf-download-tray';
      downloadTray.className = 'ax-nobuf-download-tray';
      downloadTray.setAttribute('aria-label', 'Media downloads');
      downloadTray.hidden = true;
      (document.getElementById('ax-global-overlay-root') || document.body).appendChild(downloadTray);
    }
    return downloadTray;
  }
  function createDownloadCard(name) {
    const tray = ensureDownloadTray();
    const card = document.createElement('article');
    card.className = 'ax-nobuf-download-item';
    card.innerHTML = `<div class="ax-nobuf-download-top"><span class="ax-nobuf-download-file-icon" aria-hidden="true">⇩</span><div class="ax-nobuf-download-copy"><strong>${escapeHtml(name || 'Video')}</strong><span class="ax-nobuf-download-status" role="status" aria-live="polite">Preparing download…</span></div><span class="ax-nobuf-download-percent">—</span><button type="button" class="ax-nobuf-download-dismiss" aria-label="Hide download status">×</button></div><div class="ax-nobuf-download-track"><span></span></div><div class="ax-nobuf-download-foot"><span class="ax-nobuf-download-bytes">Waiting for Telegram…</span><span class="ax-nobuf-download-origin">Archive X</span></div>`;
    card.querySelector('.ax-nobuf-download-dismiss')?.addEventListener('click', () => {
      downloadCards.delete(card); card.remove(); tray.hidden = tray.children.length === 0;
    });
    tray.appendChild(card); tray.hidden = false; downloadCards.add(card);
    return card;
  }
  function updateDownloadCard(card, { status, percent = null, bytes = '', total = '', indeterminate = false, complete = false, failed = false } = {}) {
    if (!card) return;
    const statusNode = card.querySelector('.ax-nobuf-download-status');
    const pctNode = card.querySelector('.ax-nobuf-download-percent');
    const bar = card.querySelector('.ax-nobuf-download-track span');
    const bytesNode = card.querySelector('.ax-nobuf-download-bytes');
    if (statusNode && status !== undefined) statusNode.textContent = status;
    if (pctNode) pctNode.textContent = percent === null ? (indeterminate ? '…' : '') : `${Math.max(0, Math.min(100, percent)).toFixed(0)}%`;
    if (bar) {
      bar.style.width = percent === null ? (complete ? '100%' : '0%') : `${Math.max(0, Math.min(100, percent))}%`;
      bar.classList.toggle('is-indeterminate', !!indeterminate);
      bar.classList.toggle('is-complete', !!complete);
      bar.classList.toggle('is-failed', !!failed);
    }
    if (bytesNode && bytes !== undefined) bytesNode.textContent = total ? `${bytes} / ${total}` : (bytes || (indeterminate ? 'Progress is managed by the browser' : ''));
    card.classList.toggle('is-complete', !!complete);
    card.classList.toggle('is-failed', !!failed);
  }

  // ------------------------------------------------------------------
  // SECTION: Keep-or-discard decision and badges
  // Saved decisions, kept buffers and server badges.
  // ------------------------------------------------------------------
  function decisionBytes(key) { return Number(loadObject(DECISION_KEY)[key]?.cachedBytes)||0; }
  function shouldAsk(status,key) {
    const cached=Number(status?.cached_bytes)||0, total=Number(status?.size)||0;
    if (!cached || !total || status?.complete || cached>=total) return false;
    const seen=decisionBytes(key);
    return cached-seen>=Math.max(512*KIB,Math.floor(Number(status.segment_bytes)||512*KIB));
  }
  function storeDecision(key,status) {
    const o=loadObject(DECISION_KEY); o[key]={cachedBytes:Number(status?.cached_bytes)||0,updatedAt:Date.now()}; saveObject(DECISION_KEY,o);
  }
  function rememberKeptBuffer(key,status,name) {
    const o=loadObject(KEPT_KEY); o[key]={cid:Number(status?.channel_id),mid:Number(status?.message_id),name:String(name||status?.name||'Video'),percent:Number(status?.percent)||0,cachedBytes:Number(status?.cached_bytes)||0,updatedAt:Date.now()};
    const entries=Object.entries(o).sort((a,b)=>Number(b[1]?.updatedAt||0)-Number(a[1]?.updatedAt||0)).slice(0,100);
    saveObject(KEPT_KEY,Object.fromEntries(entries)); updateKeptBadges(key);
  }
  // Cache percentages come from the server (the saved byte ranges), so cards show
  // them after a restart or in another browser; local "kept" records fill any gap.
  let serverBadges={};
  function badgeRecords() {
    const merged={...loadObject(KEPT_KEY)};
    for(const [key,v] of Object.entries(serverBadges)) merged[key]={...(merged[key]||{}),percent:Number(v.percent)||0,cachedBytes:Number(v.bytes)||0,complete:!!v.complete,kept:!!v.kept};
    return merged;
  }
  let badgeFetchAt=0;
  async function refreshServerBadges(force=false) {
    const now=Date.now(); if(!force&&now-badgeFetchAt<8000)return; badgeFetchAt=now;
    try {
      const r=await api('/api/play/range-cache-badges',{cache:'no-store'});
      if(r&&r._ok!==false&&r.items&&typeof r.items==='object'){serverBadges=r.items;updateKeptBadges();}
    } catch {}
  }
  function applyKeptBadge(card,record) {
    if(!card?.matches?.('.vc[data-cid][data-mid]'))return;
    let badge=card.querySelector('.ax-nobuf-kept-badge');
    if(!record || !(Number(record.percent)>0)){badge?.remove();return;}
    if(!badge){badge=document.createElement('span');badge.className='ax-nobuf-kept-badge';badge.setAttribute('aria-label','Partially buffered locally');card.appendChild(badge);}
    badge.textContent=record.complete?'Cached':`Buffer ${Math.min(100,Number(record.percent)||0).toFixed(0)}%`;
    badge.title=`${formatBytes(record.cachedBytes)} cached locally. Open the video to reuse this buffer.`;
  }
  function selectorForVideoKey(key) {
    const [cid,mid]=String(key||'').split(':');
    if(!/^-?\d+$/.test(cid||'')||!/^\d+$/.test(mid||''))return '';
    return `.vc[data-cid="${cid}"][data-mid="${mid}"]`;
  }
  let badgeRefreshScheduled=false;
  function scheduleBadgeBatch(cards,kept) {
    let index=0;
    const run=deadline=>{
      let processed=0;
      while(index<cards.length&&processed<100&&(deadline?.timeRemaining?.()??0)>1){
        const card=cards[index++];applyKeptBadge(card,kept[videoKey(card.dataset.cid,card.dataset.mid)]);processed++;
      }
      if(index<cards.length){
        if(typeof window.requestIdleCallback==='function')window.requestIdleCallback(run,{timeout:120});
        else setTimeout(()=>run({timeRemaining:()=>8}),0);
      }
    };
    if(typeof window.requestIdleCallback==='function')window.requestIdleCallback(run,{timeout:120});
    else setTimeout(()=>run({timeRemaining:()=>8}),0);
  }
  function updateKeptBadges(key=null) {
    const kept=badgeRecords();
    if(key!==null&&key!==undefined){
      const selector=selectorForVideoKey(key);if(!selector)return;
      document.querySelectorAll(selector).forEach(card=>applyKeptBadge(card,kept[String(key)]));
      return;
    }
    if(badgeRefreshScheduled)return;
    badgeRefreshScheduled=true;
    setTimeout(()=>{
      badgeRefreshScheduled=false;
      // One collection pass may cover a large virtualized library; all DOM
      // updates happen in idle slices so Continue Download/Keep stays responsive.
      const cards=Array.from((document.getElementById('app')||document.body).querySelectorAll('.vc[data-cid][data-mid]'));
      scheduleBadgeBatch(cards,kept);
    },0);
  }

  // ------------------------------------------------------------------
  // SECTION: Close flow
  // Intercepts player close and keeps or continues the buffer.
  // ------------------------------------------------------------------
  function describePending(status) {
    const name=String(status?.name||activeVideo()?.key||'this video');
    $('ax-nobuf-close-desc').textContent=`${(Number(status?.percent)||0).toFixed(1)}% of “${name}” is cached locally. Choose what to do with this data.`;
    $('ax-nobuf-close-stat').textContent=`${formatBytes(status?.cached_bytes||0)} of ${formatBytes(status?.size||0)} · ${Number(status?.segments_cached)||0}/${Number(status?.total_segments)||0} segments`;
    $('ax-nobuf-close-progress-fill').style.width=`${Math.max(0,Math.min(100,Number(status?.percent)||0))}%`;
    $('ax-nobuf-close-status').textContent='';
  }
  function showCloseDialog(status,args) {
    const active=activeVideo(); if(!active) { callNativeClose(args); return; }
    pendingClose={...args,key:active.key,cid:active.cid,mid:active.mid,status,name:String(status?.name||$('dtitle')?.textContent||'video')};
    ensureCloseDialog(); describePending(status); closeDialog.hidden=false; dialogBusy=false;
    $('ax-nobuf-continue-download').disabled=false;
    $('ax-nobuf-continue-download')?.focus({preventScroll:true});
  }
  function callNativeClose(args) {
    if(typeof originalClose!=='function') return;
    bypassClose=true;
    try { return originalClose.call(window,args?.fromHistory,args?.reason); }
    finally { bypassClose=false; pendingClose=null; setTimeout(()=>refreshServerBadges(true),1800); }
  }
  async function interceptClose(fromHistory, reason='close') {
    if (bypassClose || !originalClose) return originalClose?.call(window,fromHistory,reason);
    if (closeCheck) return;
    const active=activeVideo();
    if (!active || !$('do')?.classList.contains('open')) return originalClose.call(window,fromHistory,reason);
    const args={fromHistory,reason}; closeCheck=true;
    try {
      let status=(active.key===currentKey && Date.now()-currentStatusAt<2200)?currentStatus:await getRangeStatus(active.cid,active.mid);
      if (activeVideo()?.key!==active.key) return;
      currentStatus=status;currentStatusAt=Date.now();renderTimeline(status);
      if (shouldAsk(status,active.key)) {
        if (partialCachePromptEnabled) { showCloseDialog(status,args); return; }
        rememberKeptBuffer(active.key,status,status?.name||$('dtitle')?.textContent||'video');
        storeDecision(active.key,status);
        const keepRequest=keepOnServer(active.cid,active.mid,true);
        callNativeClose(args);
        await keepRequest;
        return;
      }
      callNativeClose(args);
    } catch { callNativeClose(args); }
    finally { closeCheck=false; }
  }
  // Tell the server to exempt this video's cached ranges from idle expiry so the
  // buffer is still there after a restart, like a saved NoBuf session.
  async function keepOnServer(cid,mid,kept) {
    try { await api(`/api/play/range-cache/${encodeURIComponent(cid)}/${encodeURIComponent(mid)}/keep`,{method:'POST',body:JSON.stringify({kept:!!kept})}); } catch {}
  }
  async function finishClose(action) {
    if (dialogBusy || !pendingClose) return;
    dialogBusy=true;
    const pending=pendingClose, status=pending.status, key=pending.key;
    closeDialog.hidden=true;
    if (action==='keep') {
      rememberKeptBuffer(key,status,pending.name);storeDecision(key,status);
      await keepOnServer(pending.cid,pending.mid,true);
      callNativeClose(pending);dialogBusy=false;
      return;
    }
    if (action==='discard') {
      const active = activeVideo();
      const name=pending.name;
      callNativeClose(pending);
      await new Promise(resolve=>setTimeout(resolve,450));
      const result=await api(`/api/play/range-cache/${encodeURIComponent(pending.cid)}/${encodeURIComponent(pending.mid)}/clear`,{method:'POST',body:JSON.stringify({})});
      const kept=loadObject(KEPT_KEY);delete kept[key];saveObject(KEPT_KEY,kept);updateKeptBadges(key);
      const decisions=loadObject(DECISION_KEY);delete decisions[key];saveObject(DECISION_KEY,decisions);
      if(result?._ok===false||result?.ok===false) { window.toast?.(result?.detail||'Could not discard the cached ranges. Try again after playback stops.'); }
      else { window.toast?.(`Cached buffer for ${name} discarded.`); }
      dialogBusy=false;
      return;
    }
    dialogBusy=false;
  }
  async function continueDownload() {
    if (dialogBusy || !pendingClose) return;
    const pending=pendingClose, status=pending.status;
    const rate=Math.max(0,Number(prefs.downloadBps)||0);
    const url=`/api/play/download/${encodeURIComponent(pending.cid)}/${encodeURIComponent(pending.mid)}?speed_limit_bps=${rate}`;
    const suggested=String(status?.name||pending.name||`video-${pending.mid}.mkv`).replace(/[\\/:*?"<>|\x00-\x1f]/g,'_').slice(0,220)||`video-${pending.mid}.mkv`;
    dialogBusy=true;
    closeDialog.querySelectorAll('button').forEach(btn=>btn.disabled=true);
    const stat=$('ax-nobuf-close-status'); if(stat)stat.textContent='Preparing the original file download…';
    if (typeof window.showSaveFilePicker !== 'function') {
      const a=document.createElement('a'); a.href=url; a.download=suggested; a.style.display='none'; document.body.appendChild(a); a.click(); a.remove();
      const card=createDownloadCard(suggested);
      updateDownloadCard(card,{status:'Download started in your browser',percent:null,indeterminate:true,bytes:'Progress is managed by the browser'});
      rememberKeptBuffer(pending.key,status,pending.name);storeDecision(pending.key,status);
      await keepOnServer(pending.cid,pending.mid,true);
      closeDialog.hidden=true;callNativeClose(pending);dialogBusy=false;
      window.toast?.('Download started. Check the browser download manager for progress; Archive X is reusing cached ranges.');
      return;
    }
    let handle;
    try {
      handle=await window.showSaveFilePicker({suggestedName:suggested,types:[{description:'Original media file',accept:{'application/octet-stream':['.mp4','.mkv','.mov','.avi','.webm','.m4v','.ts','.mpg','.mpeg','.wmv','.flv','.3gp']}}]});
    } catch (err) {
      dialogBusy=false;closeDialog.querySelectorAll('button').forEach(btn=>btn.disabled=false);
      if(err?.name==='AbortError') { if(stat)stat.textContent='Save cancelled. The current buffer is still available.'; return; }
      if(stat)stat.textContent=err?.message||'Could not open the save dialog.';
      return;
    }
    let writer=null, reader=null, taskCard=null, downloadStarted=false;
    try {
      const response=await fetch(url,{method:'GET',credentials:'include',cache:'no-store'});
      if(!response.ok)throw new Error(`Download failed (HTTP ${response.status})`);
      const total=Number(response.headers.get('content-length'))||Number(status?.size)||0;
      reader=response.body.getReader(); writer=await handle.createWritable();
      taskCard=createDownloadCard(suggested);
      updateDownloadCard(taskCard,{status:'Downloading original file from Telegram…',percent:0,bytes:'0 B',total:formatBytes(total)});
      rememberKeptBuffer(pending.key,status,pending.name);storeDecision(pending.key,status);keepOnServer(pending.cid,pending.mid,true);
      closeDialog.hidden=true;callNativeClose(pending);downloadStarted=true;
      const statCard=taskCard;
      let written=0,lastPaint=0;
      while(true) {
        const {done,value}=await reader.read(); if(done)break;
        await writer.write(value); written+=value.byteLength;
        const now=performance.now();
        if(now-lastPaint>180) {
          const pct=total?Math.min(100,written/total*100):0;
          updateDownloadCard(statCard,{status:'Downloading original file from Telegram…',percent:pct,bytes:formatBytes(written),total:formatBytes(total)});
          lastPaint=now;
        }
      }
      await writer.close(); writer=null;
      storeDecision(pending.key,{...status,cached_bytes:Number(status?.size)||Number(status?.cached_bytes)||0,percent:100,complete:true});
      const kept=loadObject(KEPT_KEY);delete kept[pending.key];saveObject(KEPT_KEY,kept);updateKeptBadges(pending.key);
      updateDownloadCard(statCard,{status:'Saved successfully',percent:100,bytes:formatBytes(total||written),total:formatBytes(total||written),complete:true});
      window.toast?.('Original video saved successfully.');
    } catch(err) {
      try { await reader?.cancel(); } catch {}
      try { await writer?.abort(); } catch {}
      if (downloadStarted && taskCard) {
        updateDownloadCard(taskCard,{status:`Download paused: ${String(err?.message||err)}. Cached ranges remain available.`,percent:null,bytes:'Reopen the video to retry',failed:true});
        window.toast?.('Download did not finish. The verified cached ranges were retained.');
      } else {
        dialogBusy=false;closeDialog.querySelectorAll('button').forEach(btn=>btn.disabled=false);
        if(stat)stat.textContent=`Download could not start: ${String(err?.message||err)}. The cached buffer remains available.`;
        $('ax-nobuf-close-desc').textContent='Download could not start. Keep the current buffer or retry Continue Download.';
        return;
      }
    } finally {
      dialogBusy=false;
      if (closeDialog && !closeDialog.hidden) closeDialog.querySelectorAll('button').forEach(btn=>btn.disabled=false);
    }
  }

  // ------------------------------------------------------------------
  // SECTION: Polling, timeline interaction and speed badge
  // Player patches, seek rail and live speed.
  // ------------------------------------------------------------------
  function patchSkipAndAutoHide() {
    if (typeof window.pSkip === 'function' && !window.pSkip.__axNoBufPatched) {
      originalPSkip=window.pSkip;
      const patched=function(delta) { const n=Number(delta)||0; const target=n<0?-Math.abs(Number(prefs.skipBackward)||5):Math.abs(Number(prefs.skipForward)||5); return originalPSkip.call(this,target); };
      patched.__axNoBufPatched=true; window.pSkip=patched;
    }
    if (typeof window.pAutoHide === 'function' && !window.pAutoHide.__axNoBufPatched) {
      originalPAutoHide=window.pAutoHide;
      const patched=function(...args) {
        if (prefs.pinned) { try { clearTimeout(_P.hideT); $('dtop')?.classList.remove('ui-idle','hidecur');$('dtop')?.classList.add('uivis'); } catch {} return; }
        return originalPAutoHide.apply(this,args);
      };
      patched.__axNoBufPatched=true;window.pAutoHide=patched;
    }
    if (typeof window.closeDBtn === 'function' && !window.closeDBtn.__axNoBufPatched) {
      originalClose=window.closeDBtn;
      const patched=function(fromHistory,reason='close') { return interceptClose(fromHistory,reason); };
      patched.__axNoBufPatched=true;window.closeDBtn=patched;
    }
  }
  function poll() {
    // Build the drawer once; rebuilding its controls every poll would clear a
    // custom rate value or steal focus while the user is typing.
    if (!drawer || !drawer.isConnected) buildDrawer();
    patchSkipAndAutoHide();
    const active=activeVideo();
    if (active && $('do')?.classList.contains('open')) {
      if (active.key!==currentKey) { currentKey=active.key;currentStatus=null;currentStatusAt=0;speedPrevBytes=0;speedPrevTime=0;speedSmooth=0; }
      void refreshCacheStatus(false);
      updateSpeedBadge();
    } else {
      currentKey='';currentStatus=null;currentStatusAt=0;
      const badge=$('ax-speed-badge');if(badge)badge.hidden=true;
    }
  }
  document.addEventListener('keydown',event=>{
    if(event.key==='Escape'&&drawer&&!drawer.hidden){event.preventDefault();event.stopPropagation();closeDrawer();}
    if(closeDialog&&!closeDialog.hidden&&event.key==='Escape'&&!dialogBusy){event.preventDefault();event.stopPropagation();finishClose('keep');}
  },true);
  // Touch users do not have hover: briefly expose the detailed rows while they
  // scrub and for a short period after release. Desktop keeps the simple idle bar.
  let timelineExpandTimer=0;
  const seekTimeline=$('pseek');
  seekTimeline?.addEventListener('pointerdown',()=>{
    clearTimeout(timelineExpandTimer);
    seekTimeline.classList.add('ax-nobuf-timeline-expanded');
  });
  const collapseTimelineRows=()=>{
    clearTimeout(timelineExpandTimer);
    timelineExpandTimer=setTimeout(()=>{
      if(seekTimeline && !seekTimeline.matches(':hover,:focus-within'))seekTimeline.classList.remove('ax-nobuf-timeline-expanded');
    },1100);
  };
  seekTimeline?.addEventListener('pointerup',collapseTimelineRows);
  seekTimeline?.addEventListener('pointercancel',collapseTimelineRows);
  seekTimeline?.addEventListener('pointerleave',()=>{
    if(!seekTimeline.matches(':focus-within') && !(typeof _P!=='undefined'&&_P.dragging)) {
      clearTimeout(timelineExpandTimer);
      seekTimeline.classList.remove('ax-nobuf-timeline-expanded');
    }
  });
  document.addEventListener('timeupdate',()=>renderTimeline(currentStatus),true);
  /* ── Live speed + cache badge in player controls ── */
  let speedPrevBytes = 0, speedPrevTime = 0, speedSmooth = 0;
  function bufferedBytes(v) {
    if (!v || !v.buffered || !v.buffered.length || !v.duration) return 0;
    const info = (()=>{try{return typeof _P!=='undefined'?_P.info:null;}catch{return null;}})();
    const fileSize = Number(info?.size || currentStatus?.size || 0);
    const dur = Number(v.duration) || 1;
    if (!fileSize) return 0;
    let total = 0;
    for (let i = 0; i < v.buffered.length; i++) {
      total += (v.buffered.end(i) - v.buffered.start(i));
    }
    return Math.round(total / dur * fileSize);
  }
  function updateSpeedBadge() {
    const badge = $('ax-speed-badge');
    if (!badge) return;
    const active = activeVideo();
    const v = $('pv');
    const isOpen = !!$('do')?.classList.contains('open');
    if (!active || !isOpen || !v) { badge.hidden = true; return; }
    const now = performance.now();
    const bb = bufferedBytes(v);
    const dt = (now - speedPrevTime) / 1000;
    if (speedPrevTime && dt > 0.3 && dt < 10) {
      const raw = Math.max(0, bb - speedPrevBytes) / dt;
      // Exponential moving average for smooth display
      speedSmooth = speedSmooth ? speedSmooth * 0.6 + raw * 0.4 : raw;
    }
    speedPrevBytes = bb; speedPrevTime = now;
    const pct = Number(currentStatus?.percent) || 0;
    const isBuffering = speedSmooth > 20 * KIB;
    const speedStr = speedSmooth >= MIB
      ? (speedSmooth / MIB).toFixed(1) + ' MB/s'
      : Math.round(speedSmooth / KIB) + ' KB/s';
    badge.hidden = false;
    badge.classList.toggle('is-idle', !isBuffering);
    if (isBuffering) {
      if(badge.dataset.axSpeedMode!=='speed'){
        badge.innerHTML='<span class="ax-speed-arrow">↓</span><span class="ax-speed-value"></span><span class="ax-speed-sep">·</span><span class="ax-speed-pct"></span>';
        badge.dataset.axSpeedMode='speed';
      }
      const speedNode=badge.querySelector('.ax-speed-value'),percentNode=badge.querySelector('.ax-speed-pct');
      const percentText=`${pct.toFixed(0)}%`;
      if(speedNode&&speedNode.textContent!==speedStr)speedNode.textContent=speedStr;
      if(percentNode&&percentNode.textContent!==percentText)percentNode.textContent=percentText;
    } else if (pct > 0) {
      if(badge.dataset.axSpeedMode!=='cached'){
        badge.innerHTML='<span class="ax-speed-pct"></span>';
        badge.dataset.axSpeedMode='cached';
      }
      const percentNode=badge.querySelector('.ax-speed-pct'),percentText=`⬤ ${pct.toFixed(0)}% cached`;
      if(percentNode&&percentNode.textContent!==percentText)percentNode.textContent=percentText;
    } else {
      badge.hidden = true;
    }
  }
  document.addEventListener('progress',()=>{renderTimeline(currentStatus);updateSpeedBadge();},true);
  document.addEventListener('loadedmetadata',updateVideoInfo,true);
  document.addEventListener('fullscreenchange',()=>{setTimeout(()=>{if(drawer&&!drawer.hidden)placeDrawer();renderTimeline(currentStatus);},100);});

  // ------------------------------------------------------------------
  // SECTION: Initialization
  // Observer and init.
  // ------------------------------------------------------------------
  // Update only newly inserted card nodes; a whole-library sweep per DOM change
  // was the main thread hang when a partial-buffer dialog closed on large libraries.
  const appRoot=document.getElementById('app')||document.body;
  const observer=new MutationObserver(records=>{
    const added=[];
    for(const record of records)for(const node of record.addedNodes){
      if(node?.nodeType!==1)continue;
      if(node.matches?.('.vc[data-cid][data-mid]'))added.push(node);
      else if(node.querySelector?.('.vc[data-cid][data-mid]'))added.push(...node.querySelectorAll('.vc[data-cid][data-mid]'));
    }
    if(added.length)scheduleBadgeBatch(added,badgeRecords());
  });
  observer.observe(appRoot,{subtree:true,childList:true});
  function init() {
    buildDrawer();patchSkipAndAutoHide();applyPreferences();updateKeptBadges();refreshServerBadges(true);setInterval(()=>{if(!document.hidden)refreshServerBadges();},45000);document.addEventListener('visibilitychange',()=>{if(!document.hidden)refreshServerBadges();});
    pollTimer=setInterval(poll,1400);
    ensureCloseDialog();
    // Buffers kept before this version were still subject to the idle expiry;
    // mark them kept on the server once per session so they survive restarts.
    setTimeout(()=>{try{Object.values(loadObject(KEPT_KEY)).slice(0,100).forEach(r=>{if(Number(r?.cid)&&Number(r?.mid)&&Number(r?.percent)>0)keepOnServer(r.cid,r.mid,true);});}catch{}},6000);
  }
  if(document.readyState==='loading')document.addEventListener('DOMContentLoaded',init,{once:true});else init();
})();
