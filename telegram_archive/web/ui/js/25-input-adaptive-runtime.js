/* ============================================================================
 * FILE: js/25-input-adaptive-runtime.js
 * PURPOSE: Command palette and input-adaptive helpers.
 * AREA: Responsive and input
 * LOAD: Classic script. Order is set by index.html, the HTML partials and
 *       _LAYERS in web/html.py. Later scripts can wrap earlier globals,
 *       so check docs/CODE_MAP.md before moving code between files.
 * ============================================================================ */

(function(){
  'use strict';
  const root=document.documentElement;
  const coarse=()=>window.matchMedia?.('(pointer: coarse)')?.matches===true;
  const fine=()=>window.matchMedia?.('(pointer: fine)')?.matches===true;
  const setInput=(mode)=>{
    if(mode!=='touch'&&mode!=='pointer')return;
    root.dataset.axInput=mode;
    root.classList.toggle('ax-input-touch',mode==='touch');
    root.classList.toggle('ax-input-pointer',mode==='pointer');
    window.dispatchEvent(new CustomEvent('archivexinputchange',{detail:{mode}}));
  };
  // Initial capability is only a default. Pointer events below can switch a hybrid device.
  setInput(coarse()&&!fine()?'touch':'pointer');
  const onMedia=()=>{
    if(coarse()&&!fine())setInput('touch');
    else if(!coarse()&&fine())setInput('pointer');
  };
  try{
    const c=window.matchMedia('(pointer: coarse)');
    const f=window.matchMedia('(pointer: fine)');
    c.addEventListener?.('change',onMedia);
    f.addEventListener?.('change',onMedia);
  }catch(_){}
  document.addEventListener('pointerdown',(ev)=>{
    if(ev.pointerType==='touch'||ev.pointerType==='pen')setInput('touch');
    else if(ev.pointerType==='mouse')setInput('pointer');
  },{passive:true,capture:true});
  document.addEventListener('keydown',(ev)=>{
    if(ev.key==='Tab'||ev.key==='Enter'||ev.key===' '){
      root.dataset.axKeyboard='true';
      root.classList.add('ax-keyboard-user');
    }
  },{passive:true});
  document.addEventListener('pointerdown',()=>{
    root.dataset.axKeyboard='false';
    root.classList.remove('ax-keyboard-user');
  },{passive:true,capture:true});
})();

/* Archive X UX Architecture v1 */
const UX_COMMANDS=[
 {label:'Browse videos',hint:'Open the primary media workspace',keys:'G',run:()=>sv('library')},
 {label:'Search library',hint:'Focus the global search field',keys:'/',run:()=>{$('gs')?.focus();$('gs')?.select();}},
 {label:'Open performers',hint:'Browse people and relationships',run:()=>sv('performers')},
 {label:'Open studios',hint:'Browse studio catalogue',run:()=>sv('sites')},
 {label:'Open Operations',hint:'Running work, queues and automation',run:()=>sv('admin')},
 {label:'Open Settings',hint:'Application configuration',run:()=>sv('settings')},
 {label:'Open Notifications',hint:'Important updates from your library',run:()=>uxOpenActivity()},
 {label:'Clear library filters',hint:'Reset search and filters',run:()=>{sv('library');resetF();}},
 {label:'Refresh library',hint:'Reload the selected channels',run:()=>{sv('library');loadLib();}},
];
let _uxCommandIndex=0;
function uxRenderCommands(){const q=($('ux-command-input')?.value||'').trim().toLowerCase();const list=$('ux-command-list');if(!list)return;const rows=UX_COMMANDS.filter(x=>!q||x.label.toLowerCase().includes(q)||x.hint.toLowerCase().includes(q));_uxCommandIndex=Math.min(_uxCommandIndex,Math.max(0,rows.length-1));list.innerHTML=rows.map((x,i)=>`<button class="ax-ux-command-item ${i===_uxCommandIndex?'active':''}" type="button" data-ux-cmd="${i}"><span><b>${esc(x.label)}</b><small>${esc(x.hint)}</small></span>${x.keys?`<span class="ax-ux-kbd">${esc(x.keys)}</span>`:''}</button>`).join('')||'<div class="muted" style="padding:18px">No matching commands.</div>';[...list.querySelectorAll('[data-ux-cmd]')].forEach((b,i)=>b.onclick=()=>{const x=rows[i];uxCloseCommand();x.run();});}
function uxOpenCommand(){const m=$('ux-command');if(!m)return;m.classList.add('open');_uxCommandIndex=0;$('ux-command-input').value='';uxRenderCommands();requestAnimationFrame(()=>$('ux-command-input')?.focus());}
function uxCloseCommand(){const m=$('ux-command');m?.classList.remove('open');}
function uxFilterCommands(){_uxCommandIndex=0;uxRenderCommands();}
function uxCommandKeydown(e){const items=[...document.querySelectorAll('.ax-ux-command-item')];if(e.key==='Escape'){e.preventDefault();uxCloseCommand();return}if(e.key==='ArrowDown'){e.preventDefault();_uxCommandIndex=Math.min(items.length-1,_uxCommandIndex+1);uxRenderCommands();items[_uxCommandIndex]?.scrollIntoView({block:'nearest'});return}if(e.key==='ArrowUp'){e.preventDefault();_uxCommandIndex=Math.max(0,_uxCommandIndex-1);uxRenderCommands();return}if(e.key==='Enter'&&items[_uxCommandIndex]){e.preventDefault();items[_uxCommandIndex].click();}}
function uxOpenActivity(){const m=$('ux-activity');if(!m)return;m.classList.add('open');uxRenderActivity();}
function uxCloseActivity(){$('ux-activity')?.classList.remove('open');}
function uxNotificationTime(value){
 const numeric=typeof value==='number'?value:Number(value);
 const stamp=typeof value==='number'||(String(value||'').trim()&&Number.isFinite(numeric))?(numeric<1e12?numeric*1000:numeric):value?Date.parse(value):NaN;
 if(!Number.isFinite(stamp))return {date:new Date(),time:''};
 const date=new Date(stamp);
 return {date,time:date.toLocaleTimeString([],{hour:'2-digit',minute:'2-digit'})};
}
function uxNotificationSafeText(value,max=112){
 const original=String(value??'').split(/\r?\n/,1)[0].trim();
 if(/^\s*[\[{]\s*(?:"|\{|\[)/.test(original)){try{JSON.parse(original);return '';}catch(_){}}
 if(/^\s*(?:traceback|at\s+\S+\s*\(|[A-Za-z_$][\w.$]*Error:)/i.test(original))return '';
 let text=original.replace(/https?:\/\/\S+/gi,'').replace(/(?:[A-Z]:\\|\/)(?:[^\s"'<>]+[\\/])*[^\s"'<>]*/g,'')
  .replace(/\b(?:id|(?:job|event|task)[-_ ]?id)\s*[:=#]\s*[\w-]+/gi,'')
  .replace(/\b[0-9a-f]{8}-[0-9a-f-]{27,}\b/gi,'')
  .replace(/\b(?:traceback|stack trace|exception stack)\b[\s\S]*/i,'')
  .replace(/\{[^{}]*\}|\[[^\[\]]*\]/g,'').replace(/\s+/g,' ').trim();
 if(/^\s*(?:\{|\[|at\s+\S+|file\s+"|traceback)/i.test(text))return '';
 return text.length>max?`${text.slice(0,max-1).trimEnd()}…`:text;
}
function uxNotificationModule(module,message){
 const source=String(module||'').trim()||String(message||'');
 if(/backup|restore/i.test(source))return 'Backup & Restore';
 if(/transfer|upload|telegram/i.test(source))return 'Transfers';
 if(/metadata|asset/i.test(source))return 'Metadata';
 if(/scene|match/i.test(source))return 'Scene matching';
 if(/playback|player|stream|vlc|mpc/i.test(source))return 'Playback';
 if(/automation/i.test(source))return 'Automation';
 return uxNotificationSafeText(source,32)||'Archive X';
}
function uxNotificationEvent(e){
 if(!e||typeof e!=='object')return null;
 const level=String(e.level||'INFO').toUpperCase();
 if(level==='DEBUG'||level==='TRACE')return null;
 const raw=typeof e.message==='string'?e.message:String(e.message||'');
 const eventCode=String(e.event||'').toLowerCase();
  const source=`${raw} ${eventCode.replace(/[._]+/g,' ')}`.trim();
  const module=String(e.module||'').toLowerCase();
  const diagnostic=/^(?:browser\.(?:telemetry\.snapshot|longtask|resource\.load_error)|api\.slow|http\.request\.exception|playback\.(?:engine\.started|first_frame|debug|range\.completed)|api\.(?:invalid_response|network_error|timeout|error)|js\.(?:exception|unhandled_rejection))$/.test(eventCode)||
    /^diagnostics\.report\./.test(eventCode)||/^watchdog\.guardian_(?:started|restart_backoff|start_exited)$/.test(eventCode)||
    /resource load failed|main-thread long task|slow api request|playback engine started/i.test(raw);
  if(diagnostic)return null;
  const routineEvent=/\.(?:started|progress|checkpoint|cache_hit|cache_ready|message_lookup_started|message_lookup_completed|fingerprint_started|fingerprint_completed|fingerprint_failed|item_started|item_checkpoint|item_failed|stage_started|stage_completed|frame_completed|validation_started)$/i.test(eventCode);
  if(routineEvent)return null;
  const lowSignal=/\b(heartbeat|telemetry|cache hit|cache miss|progress(?: update)?|polling|keepalive|health check|retrying request|websocket ping)\b/i.test(source);
  if(lowSignal)return null;
  const floodWait=/flood[\s_-]?wait/i.test(source);
  const rangeTimeout=/range request/i.test(source)&&/\b(fail(?:ed|ure)?|error|timeout|timed out)\b/i.test(source);
   const telegramSendSuccess=level==='SUCCESS'&&(
     eventCode==='telegram.send.completed'||
     /\btelegram video sent to\b|\bvideos? sent to telegram channel\b|\bpreviously sent video is now available in\b/i.test(source)
   );
   const telegramLibraryPending=(level==='WARN'||level==='ERROR')&&
     /\btelegram send succeeded but (?:library registration is pending|the destination library preview is still pending)\b/i.test(source);
  const userFacingFailure=(level==='WARN'||level==='ERROR')&&(
    floodWait||rangeTimeout||
     telegramLibraryPending||
     (/\btelegram send\b|\bsend to telegram\b|\bbulk send\b/i.test(source)&&/\b(fail(?:ed|ure)?|error|timeout|timed out|blocked|incomplete)\b/i.test(source))||
    (/\b(backup|restore|upload|transfer|download|metadata|scene match|job)\b/i.test(source)&&/\b(fail(?:ed|ure)?|error|timeout|timed out|blocked|incomplete)\b/i.test(source))
  );
  const playbackFailure=!rangeTimeout&&/\b(playback|stream)\b/i.test(source)&&/\b(fail(?:ed|ure)?|error|timeout|timed out)\b/i.test(source);
 const milestone=
   telegramSendSuccess||
   (/\b(backup|restore)\b/i.test(source)&&/\b(verified|complete(?:d)?|success(?:ful)?|fail(?:ed|ure)?|restored)\b/i.test(source))||
  (/\b(upload(?:ed|ing)?|transfer(?:red|ring)?)\b/i.test(source)&&/\b(started|uploading|uploaded|complete(?:d)?|finished|fail(?:ed|ure)?|error|timeout|timed out)\b/i.test(source))||
   /\bmatched\s+\d+\s+scenes\b|\bscene match(?:ed|es)?\b|\bjob (?:failed|blocked|recovered|retry scheduled)\b/i.test(source)||
  (/\bmetadata\b/i.test(source)&&/\b(saved|matched|updated|linked|complete(?:d)?|fail(?:ed|ure)?|error)\b/i.test(source))||
    playbackFailure||
    (/\b(range request|flood[\s_-]?wait)\b/i.test(source)&&/\b(fail(?:ed|ure)?|error|timeout|timed out|flood[\s_-]?wait)\b/i.test(source));
  if(!milestone&&!userFacingFailure)return null;
 const safe=uxNotificationSafeText(raw||e.event||'');
 if(!safe)return null;
 const time=uxNotificationTime(e.created_at||e.ts||e.time);
 if(Date.now()-time.date.getTime()>7*86400000)return null;
 let title=safe;
   if(telegramSendSuccess)title='Sent to Telegram';
   else if(telegramLibraryPending)title='Sent, Library update pending';
   else if(/backup/i.test(source))title=/fail|error/i.test(source)?'Backup needs attention':/verif|complete|success/i.test(source)?'Backup verified':'Backup update';
 else if(/restore/i.test(source))title=/fail|error/i.test(source)?'Restore needs attention':/complete|success/i.test(source)?'Restore completed':'Restore update';
 else if(/upload|transfer/i.test(source))title=/fail|error|timeout|incomplete/i.test(source)?'Transfer needs attention':/download/i.test(source)&&/complete|success|finished/i.test(source)?'Download complete':/upload/i.test(source)&&/complete|success|uploaded|finished/i.test(source)?'Upload complete':/complete|success|finished/i.test(source)?'Transfer complete':safe;
 else if(/matched \d+ scenes/i.test(source))title=source.match(/\bmatched\s+(\d+)\s+scenes\b/i)?.[0]?.replace(/^matched/i,'Matched')||'Scene matches found';
 else if(/scene match/i.test(source))title='Scene matches found';
 else if(/metadata/i.test(source))title=/fail|error/i.test(source)?'Metadata needs attention':'Metadata results ready';
  else if(floodWait){const seconds=source.match(/flood[\s_-]?wait(?:\s|:|_)*(?:of\s*)?(\d+)\s*s?/i)?.[1];title=seconds?`FloodWait ${seconds}s`:'Telegram rate limit';}
  else if(rangeTimeout)title='Range request timed out';
   else if(playbackFailure)title='Playback failed';
  else if(level==='ERROR'||level==='WARN')title=safe;
   const backupVerified=/\bbackup\b/i.test(source)&&/\bverified|complete(?:d)?|success(?:ful)?\b/i.test(source);
   const sceneMatch=/\bmatched\s+\d+\s+scenes\b|\bscene match(?:ed|es)?\b/i.test(source);
    const tone=playbackFailure||level==='ERROR'&&!telegramLibraryPending?'error':floodWait||rangeTimeout||telegramLibraryPending||level==='WARN'?'warning':backupVerified||telegramSendSuccess?'success':sceneMatch?'match':'info';
    const icon=tone==='error'||tone==='warning'?'ax-alert':telegramSendSuccess||/backup/i.test(source)?'ax-check':/upload|transfer/i.test(source)?'ax-upload':sceneMatch||/metadata/i.test(source)?'ax-database':'ax-check';
   const mediaIdentity=String(e.playback_id||e.playbackId||e.media_id||e.mediaId||e.message_id||e.messageId||'').trim().toLowerCase();
   const key=playbackFailure
    ?`playback.failed|${mediaIdentity||Math.floor(time.date.getTime()/120000)}`
    :`${title.toLowerCase()}|${uxNotificationSafeText(raw,80).toLowerCase()}`;
  return {kind:'event',icon,tone,title,meta:time.time,date:time.date,
     jobId:String(e.job_id||e.jobId||''),sourceText:safe.toLowerCase(),key};
}
function uxBuildNotifications(){
 const eventsByKey=new Map();
 const events=Array.isArray(_evts)?_evts.slice(-100):[];
 events.forEach(e=>{
  const row=uxNotificationEvent(e);if(!row)return;
  const previous=eventsByKey.get(row.key);
  if(!previous||row.date>previous.date)eventsByKey.set(row.key,row);
 });
 const rows=[...eventsByKey.values()];
 const jobs=Array.isArray(_opsState?.jobs)?_opsState.jobs:[];
 const active=/^(RUNNING|ACTIVE|PAUSED|RECOVERING|RETRYING|RESUMING|STARTING)$/i;
 const failed=/^(FAILED|ERROR|CANCELLED)$/i;
 const candidates=jobs.map(j=>{
  const status=String(j?.status||'').toUpperCase();
  if(!active.test(status)&&!failed.test(status))return null;
  const time=uxNotificationTime(j.updated_at||j.finished_at||j.created_at||j.started_at||j.ts);
  if(failed.test(status)&&Date.now()-time.date.getTime()>7*86400000)return null;
  const name=uxNotificationSafeText(j.name||j.type||j.operation||'Library task',48)||'Library task';
   if(/playback engine|player engine|stream engine/i.test(name))return null;
  const progress=Number(j.progress);
  const progressText=Number.isFinite(progress)&&progress>0?`${Math.round(progress<=1?progress*100:progress)}%`:'';
  const label=status==='FAILED'||status==='ERROR'?'Failed':status==='CANCELLED'?'Stopped':status.charAt(0)+status.slice(1).toLowerCase();
  return {kind:'job',icon:failed.test(status)?'ax-alert':'ax-transfer',tone:failed.test(status)?'error':'info',title:name,sourceName:name,jobId:String(j.id||j.job_id||''),
    meta:[label,progressText,time.time].filter(Boolean).join(' · '),date:time.date,
   key:`job|${String(j.id||j.job_id||name).toLowerCase()}|${status}`};
 }).filter(Boolean);
 candidates.sort((a,b)=>b.date-a.date);
 const eventRows=rows.filter(row=>!candidates.some(job=>
  (row.jobId&&job.jobId&&row.jobId===job.jobId)||
  (job.sourceName.length>=5&&row.sourceText?.includes(job.sourceName.toLowerCase()))
 ));
 const combined=eventRows.slice(),combinedSeen=new Set(combined.map(row=>row.key));
 const activeRows=candidates.filter(r=>!/(?:failed|stopped)/i.test(r.meta)).slice(0,4);
 const failedRows=candidates.filter(r=>/(?:failed|stopped)/i.test(r.meta)).slice(0,3);
 [...activeRows,...failedRows].forEach(row=>{if(!combinedSeen.has(row.key)){combinedSeen.add(row.key);combined.push(row);}});
 combined.sort((a,b)=>b.date-a.date);
  return combined.slice(0,8);
}
function uxRenderActivity(){
 const body=$('ux-activity-body');if(!body)return;
 const rows=uxBuildNotifications();
 const scrollTop=body.scrollTop;
 const now=new Date(), today=new Date(now.getFullYear(),now.getMonth(),now.getDate()), yesterday=new Date(today);yesterday.setDate(today.getDate()-1);
 const groups=[['Today',[]],['Yesterday',[]],['Earlier',[]]];
 rows.forEach(r=>{const day=new Date(r.date.getFullYear(),r.date.getMonth(),r.date.getDate());groups[day>=today?0:day>=yesterday?1:2][1].push(r);});
 body.innerHTML=rows.length?groups.filter(g=>g[1].length).map(([label,items])=>`<section class="ax-ux-notification-group" aria-label="${label}"><h3>${label}</h3>${items.map(r=>`<div class="ax-ux-activity-row is-${r.tone}"><span><svg class="ax-icon" aria-hidden="true"><use href="#${r.icon}"></use></svg></span><div><b>${esc(r.title)}</b><small>${esc(r.meta)}</small></div></div>`).join('')}</section>`).join(''):'<div class="ax-ux-notification-empty"><span class="ax2-empty-mark" aria-hidden="true"></span><b>You’re all caught up</b><small>Important library and system updates will appear here.</small></div>';
 body.scrollTop=scrollTop;
 uxUpdateNotificationBadge(rows.length);
}
function uxUpdateNotificationBadge(count){
 const badge=document.querySelector('[data-ax2-activity-badge]');
 if(!badge)return;
 const total=Number.isFinite(count)?count:uxBuildNotifications().length;
 badge.textContent=String(total);badge.hidden=total===0;
 const button=$('global-activity-btn');
 if(button)button.setAttribute('aria-label',total?`Notifications, ${total} shown`:'Notifications');
}
function uxRefreshNotifications(){const rows=uxBuildNotifications();uxUpdateNotificationBadge(rows.length);if($('ux-activity')?.classList.contains('open'))uxRenderActivity();}
uxRefreshNotifications();
function uxOpenFilters(){const m=$('ux-filter-drawer');if(!m)return;const map=[['ux-f-studio','f-studio'],['ux-f-perf','f-perf'],['ux-f-tag','f-tag'],['ux-f-d1','f-d1'],['ux-f-d2','f-d2'],['ux-f-res','tb-res']];map.forEach(([a,b])=>{if($(a)&&$(b))$(a).value=$(b).value||''});m.classList.add('open');requestAnimationFrame(()=>m.querySelector('input')?.focus());}
function uxCloseFilters(){$('ux-filter-drawer')?.classList.remove('open');}
function uxApplyFilters(){const map=[['ux-f-studio','f-studio'],['ux-f-perf','f-perf'],['ux-f-tag','f-tag'],['ux-f-d1','f-d1'],['ux-f-d2','f-d2'],['ux-f-res','tb-res']];map.forEach(([a,b])=>{if($(a)&&$(b))$(b).value=$(a).value||''});applyF();uxCloseFilters();}
function uxResetFilters(){['ux-f-studio','ux-f-perf','ux-f-tag','ux-f-d1','ux-f-d2'].forEach(id=>{if($(id))$(id).value=''});if($('ux-f-res'))$('ux-f-res').value='';}

function uxSearchKeydown(e){if((e.metaKey||e.ctrlKey)&&e.key.toLowerCase()==='k'){e.preventDefault();uxOpenCommand();return}if(e.key==='Escape'){$('gs').value='';if(typeof onSearch==='function')onSearch();else applyF();$('gs').blur();}}
document.addEventListener('keydown',e=>{if((e.metaKey||e.ctrlKey)&&e.key.toLowerCase()==='k'){e.preventDefault();uxOpenCommand();return}if(e.key==='Escape'){if($('ux-command')?.classList.contains('open'))uxCloseCommand();else if($('ux-activity')?.classList.contains('open'))uxCloseActivity();else if($('ux-filter-drawer')?.classList.contains('open'))uxCloseFilters();}});
document.addEventListener('click',e=>{if(e.target===$('ux-command'))uxCloseCommand();if(e.target===$('ux-activity'))uxCloseActivity();if(e.target===$('ux-filter-drawer'))uxCloseFilters();});
const _uxShowView=showView;showView=function(v){_uxShowView(v);document.querySelectorAll('[data-ux-bottom]').forEach(b=>b.classList.toggle('on',b.dataset.uxBottom===v));};

