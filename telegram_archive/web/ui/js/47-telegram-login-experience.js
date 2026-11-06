/* ============================================================================
 * FILE: js/47-telegram-login-experience.js
 * AREA: Telegram login
 * LOAD: Classic script. Order is set by index.html, the HTML partials and
 *       _LAYERS in web/html.py. Later scripts can wrap earlier globals,
 *       so check docs/CODE_MAP.md before moving code between files.
 * SECTIONS (search for "SECTION:"):
 *   - State and wrapped originals
 *   - Proxy settings
 *   - QR login
 *   - Login method flow
 *   - Phone entry
 *   - Integration and invalid sessions
 * ============================================================================ */
/* Telegram login experience: QR, phone-code, shared 2FA and passkey availability.
   Uses the existing /api/telegram authentication routes and Telethon session. */
(function(){
  'use strict';

  // ------------------------------------------------------------------
  // SECTION: State and wrapped originals
  // Login state and the original app handlers.
  // ------------------------------------------------------------------
  const $ = id => document.getElementById(id);
  const countries = [
    ['IN','India','91'],['US','United States','1'],['GB','United Kingdom','44'],['CA','Canada','1'],['AU','Australia','61'],
    ['BD','Bangladesh','880'],['PK','Pakistan','92'],['LK','Sri Lanka','94'],['NP','Nepal','977'],['AE','United Arab Emirates','971'],
    ['SA','Saudi Arabia','966'],['SG','Singapore','65'],['MY','Malaysia','60'],['ID','Indonesia','62'],['PH','Philippines','63'],
    ['TH','Thailand','66'],['VN','Vietnam','84'],['JP','Japan','81'],['KR','South Korea','82'],['CN','China','86'],
    ['HK','Hong Kong','852'],['TW','Taiwan','886'],['DE','Germany','49'],['FR','France','33'],['IT','Italy','39'],
    ['ES','Spain','34'],['PT','Portugal','351'],['NL','Netherlands','31'],['BE','Belgium','32'],['CH','Switzerland','41'],
    ['AT','Austria','43'],['SE','Sweden','46'],['NO','Norway','47'],['DK','Denmark','45'],['FI','Finland','358'],
    ['PL','Poland','48'],['UA','Ukraine','380'],['RU','Russia','7'],['TR','Türkiye','90'],['IL','Israel','972'],
    ['EG','Egypt','20'],['NG','Nigeria','234'],['KE','Kenya','254'],['ZA','South Africa','27'],['BR','Brazil','55'],
    ['MX','Mexico','52'],['AR','Argentina','54'],['CL','Chile','56'],['CO','Colombia','57'],['NZ','New Zealand','64'],
    ['IE','Ireland','353'],['GR','Greece','30'],['CZ','Czechia','420'],['RO','Romania','40'],['HU','Hungary','36'],
    ['IR','Iran','98'],['IQ','Iraq','964'],['QA','Qatar','974'],['KW','Kuwait','965'],['OM','Oman','968'],['BH','Bahrain','973']
  ].map(([iso,name,dial])=>({iso,name,dial}));
  let selectedCountry = countries[0];
  let qrPollTimer = null;
  let qrPollBusy = false;
  let qrStatus = 'IDLE';
  let lastQrImage = '';
  let passwordReturnMethod = 'phone';
  let countryFiltered = countries.slice();
  let originalSendCode = null;
  let originalVerifyCode = null;
  let originalVerify2fa = null;
  let originalLoadTgSettings = null;
  let originalSaveTg = null;


  // ------------------------------------------------------------------
  // SECTION: Proxy settings
  // Telegram proxy form.
  // ------------------------------------------------------------------
  function setText(id, text){ const el=$(id); if(el) el.textContent = text || ''; }
  function applyTelegramProxyType(){
    const type=$('ax-tg-proxy-type')?.value||'direct', direct=type==='direct';
    const host=$('ax-tg-proxy-host-wrap'),port=$('ax-tg-proxy-port-wrap'),auth=$('ax-tg-proxy-auth'),mtproto=$('ax-tg-proxy-mtproto');
    const panel=$('ax-tg-proxy-settings');
    if(panel)panel.classList.toggle('is-direct',direct);
    if(host)host.hidden=direct;
    if(port)port.hidden=direct;
    if(auth)auth.hidden=!['socks4','socks5','http'].includes(type);
    if(mtproto)mtproto.hidden=type!=='mtproto';
    const portInput=$('ax-tg-proxy-port');
    if(portInput)portInput.placeholder={http:'8080',mtproto:'443',socks4:'1080',socks5:'1080'}[type]||'1080';
  }
  function updateTelegramProxyStatus(settings){
    const data=typeof settings==='string'?{status:settings,type:'direct'}:(settings||{});
    const type=data.type||'direct';
    const typeLabel={direct:'Direct',socks4:'SOCKS4',socks5:'SOCKS5',http:'HTTP',mtproto:'MTProto'}[type]||'Proxy';
    let label='Status unavailable',help='Archive X could not read the proxy configuration. Reload Settings or check the app connection.';
    if(data.restart_required){
      label='Restart required';
      help='Proxy settings were saved. Restart Archive X to apply them to Telegram connections.';
    }else if(data.source==='environment'&&data.status==='configured'){
      label='Managed by environment';
      help='Archive X is using the runtime proxy setting. The endpoint and credentials are hidden.';
    }else if(data.status==='configured'){
      label=typeLabel+' configured';
      help='Archive X will use this proxy for Telegram connections. Provider reachability is not tested here.';
    }else if(data.status==='direct'){
      label='Direct connection';
      help='No proxy is configured. Telegram uses this runtime’s default network route.';
    }else if(data.status==='invalid'){
      label='Proxy setup needs attention';
      help='The saved proxy configuration is invalid. Review the selected type, server, port and required credentials.';
    }
    setText('ax-tg-proxy-status',label);
    setText('ax-tg-proxy-help',help);
    const typeSelect=$('ax-tg-proxy-type'),host=$('ax-tg-proxy-host'),port=$('ax-tg-proxy-port');
    if(typeSelect&&['direct','socks4','socks5','http','mtproto'].includes(type))typeSelect.value=type;
    if(host)host.value=data.host||'';
    if(port)port.value=data.port||'';
    const username=$('ax-tg-proxy-username'),password=$('ax-tg-proxy-password'),secret=$('ax-tg-proxy-secret');
    if(username)username.placeholder=data.username_saved?'Saved username — leave blank to keep':'Username (optional)';
    if(password)password.placeholder=data.password_saved?'Saved password — leave blank to keep':'Password (optional)';
    if(secret)secret.placeholder=data.secret_saved?'Saved secret — leave blank to keep':'Paste the MTProto secret';
    const envManaged=data.source==='environment';
    const form=$('ax-tg-proxy-settings');
    if(form)form.querySelectorAll('input,select').forEach(el=>{el.disabled=envManaged;});
    const save=$('ax-tg-proxy-save');if(save)save.disabled=envManaged;
    const envNote=$('ax-tg-proxy-env-note');if(envNote)envNote.hidden=!envManaged;
    applyTelegramProxyType();
  }
  async function saveTelegramProxySettings(){
    const button=$('ax-tg-proxy-save'),message=$('ax-tg-proxy-save-msg');
    if(button)button.disabled=true;
    setText('ax-tg-proxy-save-msg','Saving protected settings…');
    const payload={
      type:$('ax-tg-proxy-type')?.value||'direct',
      host:$('ax-tg-proxy-host')?.value||'',
      port:$('ax-tg-proxy-port')?.value||'',
      username:$('ax-tg-proxy-username')?.value||'',
      password:$('ax-tg-proxy-password')?.value||'',
      secret:$('ax-tg-proxy-secret')?.value||'',
      clear_auth:!!$('ax-tg-proxy-clear-auth')?.checked,
      clear_secret:!!$('ax-tg-proxy-clear-secret')?.checked
    };
    try{
      const result=await api('/api/settings/telegram/proxy',{method:'POST',body:JSON.stringify(payload),cache:'no-store'});
      if(!result||result._ok===false||result.ok===false){
        setText('ax-tg-proxy-save-msg','Error: '+(result?.detail||'Could not save proxy settings.'));
        return;
      }
      updateTelegramProxyStatus(result.proxy_settings);
      if($('ax-tg-proxy-password'))$('ax-tg-proxy-password').value='';
      if($('ax-tg-proxy-secret'))$('ax-tg-proxy-secret').value='';
      if($('ax-tg-proxy-username'))$('ax-tg-proxy-username').value='';
      if($('ax-tg-proxy-clear-auth'))$('ax-tg-proxy-clear-auth').checked=false;
      if($('ax-tg-proxy-clear-secret'))$('ax-tg-proxy-clear-secret').checked=false;
      setText('ax-tg-proxy-save-msg',result.proxy_settings?.restart_required?'Saved. Restart Archive X to apply.':'Saved.');
    }catch(error){
      setText('ax-tg-proxy-save-msg','Error: '+(error?.message||'Could not save proxy settings.'));
    }finally{
      if(button)button.disabled=false;
      if($('ax-tg-proxy-type')?.disabled&&button)button.disabled=true;
    }
  }

  // ------------------------------------------------------------------
  // SECTION: QR login
  // QR state and polling.
  // ------------------------------------------------------------------
  function setQrActions(active){
    const generate=$('ax-tg-generate-qr'),refresh=$('ax-tg-refresh-qr'),cancel=$('ax-tg-cancel-qr');
    if(generate)generate.hidden=!!active;
    if(refresh)refresh.hidden=!active;
    if(cancel)cancel.hidden=!active;
  }
  function setStatus(text, state){
    const el=$('ax-tg-qr-status'); if(!el)return;
    el.textContent=text||'';
    el.dataset.state=state||'info';
  }
  function setQrEmpty(message){
    const empty=$('ax-tg-qr-empty'), img=$('ax-tg-qr-image');
    if(img){img.hidden=true;img.removeAttribute('src');}
    if(empty){empty.hidden=false;const span=empty.querySelector('span:not(.ax-tg-qr-mark)');if(span&&message)span.textContent=message;}
    lastQrImage='';
  }
  function applyQrState(state){
    if(!state)return;
    qrStatus=String(state.status||'UNKNOWN');
    if(state.connected || qrStatus==='AUTHORIZED'){
      stopQrPolling();
      setStatus(state.message||'Telegram connected.','success');
      authComplete();
      return;
    }
    if(qrStatus==='WAITING_FOR_SCAN'){
      const img=$('ax-tg-qr-image'), empty=$('ax-tg-qr-empty');
      if(img && state.qr_image && state.qr_image!==lastQrImage){img.src=state.qr_image;img.hidden=false;lastQrImage=state.qr_image;}
      if(empty)empty.hidden=!!state.qr_image;
      setQrActions(true);
      setStatus(state.message||'Waiting for scan…','waiting');
      startQrPolling();
      return;
    }
    if(qrStatus==='PASSWORD_REQUIRED' || state.two_factor_required){
      setQrActions(false);
      stopQrPolling();
      passwordReturnMethod='qr';
      showCloudPassword('qr');
      setStatus(state.message||'QR accepted. Enter your Telegram Cloud Password to finish sign-in.','waiting');
      return;
    }
    if(['CANCELLED','IDLE','CONFIG_REQUIRED'].includes(qrStatus)){
      stopQrPolling();
      setQrActions(false);
      setQrEmpty(state.message||'Generate a short-lived code to connect your account.');
      if(qrStatus!=='IDLE')setStatus(state.message||'QR login is not active.',qrStatus==='CANCELLED'?'info':'warning');
      return;
    }
    if(qrStatus==='ERROR'){
      setQrActions(false);
      stopQrPolling();setQrEmpty('QR login could not start. Check your API credentials and connection.');
      setStatus(state.message||'QR login failed. Try again.','error');return;
    }
    if(state.message)setStatus(state.message,'info');
  }
  function stopQrPolling(){if(qrPollTimer){clearInterval(qrPollTimer);qrPollTimer=null;}qrPollBusy=false;}
  function startQrPolling(){if(qrPollTimer)return;qrPollTimer=setInterval(()=>{void pollQrStatus();},1400);}
  async function pollQrStatus(){
    if(qrPollBusy)return;qrPollBusy=true;
    try{
      const state=await api('/api/telegram/qr-login/status',{cache:'no-store'});
      if(state && state._ok!==false)applyQrState(state);
      else if(state&&state.detail)setStatus(state.detail,'error');
    }catch(e){setStatus('Connection issue while checking QR login. Retrying…','warning');}
    finally{qrPollBusy=false;}
  }
  async function startQr(refresh){
    const start=$('ax-tg-qr-panel');if(start && start.hidden) return;
    const apiId=($('api-id')?.value||'').trim();
    const hash=($('api-hash')?.value||'').trim();
    const configured=!!apiId && (!!hash || !!($('api-hash')?.placeholder||'').match(/…|set/i));
    if(!configured){setStatus('Enter and save your Telegram API ID and API Hash first.','warning');return;}
    stopQrPolling();setStatus(refresh?'Refreshing QR code…':'Generating a secure QR code…','waiting');
    const r=await api('/api/telegram/qr-login/'+(refresh?'refresh':'start'),{method:'POST',body:'{}',cache:'no-store'});
    if(!r||r._ok===false){setStatus(r?.detail||'Could not start QR login. Save your API credentials and retry.','error');void refreshSessionInvalidation();return;}
    applyQrState(r);
  }
  async function cancelQr(silent){
    stopQrPolling();
    try{await api('/api/telegram/qr-login/cancel',{method:'POST',body:'{}',cache:'no-store'});}catch{}
    qrStatus='CANCELLED';setQrActions(false);setQrEmpty('Generate a short-lived code using the button below.');
    if(!silent)setStatus('QR login cancelled.','info');
  }

  // ------------------------------------------------------------------
  // SECTION: Login method flow
  // Completion, cloud password and method switching.
  // ------------------------------------------------------------------
  function authComplete(){
    stopQrPolling();
    const panel=$('login-pan');if(panel)panel.style.display='none';
    renderSessionInvalidated(false);
    const logoutButton=$('tg-logout-btn');if(logoutButton)logoutButton.hidden=false;
    setText('login-msg','Telegram login successful. Session saved locally.');
    try{if(typeof refresh==='function')refresh();}catch{}
    try{if(typeof doRefreshChannels==='function')doRefreshChannels();}catch{}
    try{if(typeof loadSettingsStatus==='function')loadSettingsStatus(true);}catch{}
  }
  function showCloudPassword(method){
    passwordReturnMethod=method||'phone';
    const tabs=$('ax-tg-method-tabs'),qr=$('ax-tg-qr-panel'),phone=$('ax-tg-phone-panel'),pw=$('ax-tg-password-panel'),field=$('ph-2fa');
    if(tabs)tabs.hidden=true;if(qr)qr.hidden=true;if(phone)phone.hidden=true;if(pw)pw.hidden=false;if(field)field.style.display='flex';
    const back=$('ax-tg-password-panel')?.querySelector('.ax-tg-back-link');if(back)back.hidden=passwordReturnMethod==='qr';
    setText('login-msg','Telegram requires your Cloud Password (Two-Step Verification) to finish signing in.');
    const input=$('tg-2fa');if(input)input.focus();
  }
  function showMethod(method){
    const useQr=method!=='phone';
    const tabs=$('ax-tg-method-tabs'),qr=$('ax-tg-qr-panel'),phone=$('ax-tg-phone-panel'),pw=$('ax-tg-password-panel');
    if(qr)qr.hidden=!useQr;if(phone)phone.hidden=useQr;if(pw)pw.hidden=true;if(tabs)tabs.hidden=false;
    ['qr','phone'].forEach(key=>{const b=$('ax-tg-tab-'+key);if(b){b.classList.toggle('on',(key==='qr')===useQr);b.setAttribute('aria-selected',String((key==='qr')===useQr));}});
    const passkey=$('ax-tg-passkey-note');if(passkey)passkey.hidden=true;
    setText('login-msg','');
    if(useQr){
      if(!['WAITING_FOR_SCAN','PASSWORD_REQUIRED','AUTHORIZED'].includes(qrStatus))void startQr(false);
      else if(qrStatus==='WAITING_FOR_SCAN')startQrPolling();
    } else {
      if(qrStatus==='WAITING_FOR_SCAN')void cancelQr(true);
      const phoneInput=$('ax-tg-phone-local');if(phoneInput)phoneInput.focus();
    }
  }
  function explainPasskey(){const note=$('ax-tg-passkey-note');if(note)note.hidden=!note.hidden;}
  function openCredentials(){
    setText('tg-save-msg','Copy the API ID and API Hash from the Telegram page into the fields below.');
  }

  // ------------------------------------------------------------------
  // SECTION: Phone entry
  // Country picker and phone composition.
  // ------------------------------------------------------------------
  function renderCountryOptions(filter){
    const list=$('ax-tg-country-list');if(!list)return;
    const q=String(filter||'').trim().toLowerCase();
    countryFiltered=countries.filter(c=>!q||c.name.toLowerCase().includes(q)||c.iso.toLowerCase().includes(q)||('+'+c.dial).includes(q));
    list.innerHTML=countryFiltered.map(c=>`<button type="button" class="ax-tg-country-option" role="option" aria-selected="${c.iso===selectedCountry.iso&&c.dial===selectedCountry.dial}" data-iso="${c.iso}" data-dial="${c.dial}"><span class="iso">${c.iso}</span><span>${c.name}</span><span class="dial">+${c.dial}</span></button>`).join('')||'<div class="muted" style="padding:12px;font-size:12px">No countries found.</div>';
    list.querySelectorAll('.ax-tg-country-option').forEach(btn=>btn.addEventListener('click',()=>{
      selectedCountry=countryFiltered.find(c=>c.iso===btn.dataset.iso&&c.dial===btn.dataset.dial)||selectedCountry;
      const iso=$('ax-tg-country-iso'),dial=$('ax-tg-country-dial'),picker=$('ax-tg-country-picker'),toggle=$('ax-tg-country-toggle');
      if(iso)iso.textContent=selectedCountry.iso;if(dial)dial.textContent='+'+selectedCountry.dial;if(picker)picker.hidden=true;if(toggle)toggle.setAttribute('aria-expanded','false');
      const phone=$('ax-tg-phone-local');if(phone)phone.focus();
    }));
  }
  function toggleCountryPicker(){
    const picker=$('ax-tg-country-picker'),toggle=$('ax-tg-country-toggle');if(!picker||!toggle)return;
    picker.hidden=!picker.hidden;
    toggle.setAttribute('aria-expanded',String(!picker.hidden));
    if(!picker.hidden){
      // Open upward when the form is near the bottom of the viewport, rather
      // than letting the searchable country list get clipped below the panel.
      const rect=toggle.getBoundingClientRect();
      const roomBelow=window.innerHeight-rect.bottom-12;
      const roomAbove=rect.top-12;
      const desired=Math.min(320,window.innerHeight-40);
      picker.classList.toggle('opens-up',roomBelow<Math.min(240,desired)&&roomAbove>roomBelow);
      const search=$('ax-tg-country-search');
      if(search){search.value='';renderCountryOptions('');search.focus();}
    } else {
      picker.classList.remove('opens-up');
    }
  }
  function composePhone(){
    const input=$('ax-tg-phone-local');
    const typed=String(input?.value||'').trim();
    let local=typed.replace(/\D/g,'');
    // Accept a full international number pasted into the local field too.
    // If its prefix matches a known calling code, split it into the selector
    // and national number instead of duplicating the country prefix.
    if(/^\+|^00/.test(typed)){
      const international=typed.startsWith('00')?typed.slice(2).replace(/\D/g,''):typed.slice(1).replace(/\D/g,'');
      const match=countries.slice().sort((a,b)=>b.dial.length-a.dial.length).find(c=>international.startsWith(c.dial));
      if(match && international.length>match.dial.length){
        selectedCountry=match;local=international.slice(match.dial.length);
        const iso=$('ax-tg-country-iso'),dial=$('ax-tg-country-dial');if(iso)iso.textContent=match.iso;if(dial)dial.textContent='+'+match.dial;
        if(input)input.value=local;
      }
    }
    const full=local?('+'+selectedCountry.dial+local):'';
    const hidden=$('tg-phone');if(hidden)hidden.value=full;
    return full;
  }
  function loadPhoneFromFull(full){
    const raw=String(full||'').trim();
    const hidden=$('tg-phone');if(hidden)hidden.value=raw;
    if(!raw){renderCountryOptions('');return;}
    const digits=raw.replace(/\D/g,'');
    const match=countries.slice().sort((a,b)=>b.dial.length-a.dial.length).find(c=>digits.startsWith(c.dial));
    if(match){selectedCountry=match;const iso=$('ax-tg-country-iso'),dial=$('ax-tg-country-dial');if(iso)iso.textContent=match.iso;if(dial)dial.textContent='+'+match.dial;const local=$('ax-tg-phone-local');if(local)local.value=digits.slice(match.dial.length);}
    renderCountryOptions('');
  }
  function backToMethods(){
    const field=$('ph-2fa');if(field){field.style.display='none';const input=$('tg-2fa');if(input)input.value='';}
    showMethod(passwordReturnMethod==='qr'?'qr':'phone');
  }

  // ------------------------------------------------------------------
  // SECTION: Integration and invalid sessions
  // Wraps legacy handlers and recovers sessions.
  // ------------------------------------------------------------------
  function wrapLegacyHandlers(){
    if(typeof window.loadTgSettings==='function'&&!window.loadTgSettings.__axTelegramWrapped){
      originalLoadTgSettings=window.loadTgSettings;
      const wrapped=async function(){const r=await originalLoadTgSettings.apply(this,arguments);try{loadPhoneFromFull($('tg-phone')?.value||'');}catch{}return r;};
      wrapped.__axTelegramWrapped=true;window.loadTgSettings=wrapped;
    }
    if(typeof window.sendCode==='function'&&!window.sendCode.__axTelegramWrapped){
      originalSendCode=window.sendCode;
      const wrapped=async function(){const phone=composePhone();if(!phone){setText('login-msg','Enter your phone number.');$('ax-tg-phone-local')?.focus();return;}const r=await originalSendCode.apply(this,arguments);if($('ph-code')&&$('ph-code').style.display!=='none'){const pw=$('ax-tg-password-panel');if(pw)pw.hidden=true;setText('login-msg','Code sent. Check Telegram on your logged-in devices.');}return r;};
      wrapped.__axTelegramWrapped=true;window.sendCode=wrapped;
    }
    if(typeof window.verifyCode==='function'&&!window.verifyCode.__axTelegramWrapped){
      originalVerifyCode=window.verifyCode;
      const wrapped=async function(){const r=await originalVerifyCode.apply(this,arguments);if($('ph-2fa')&&$('ph-2fa').style.display!=='none')showCloudPassword('phone');else if($('login-pan')?.style.display==='none')authComplete();return r;};
      wrapped.__axTelegramWrapped=true;window.verifyCode=wrapped;
    }
    if(typeof window.verify2fa==='function'&&!window.verify2fa.__axTelegramWrapped){
      originalVerify2fa=window.verify2fa;
      const wrapped=async function(){const r=await originalVerify2fa.apply(this,arguments);if($('login-pan')?.style.display==='none')authComplete();return r;};
      wrapped.__axTelegramWrapped=true;window.verify2fa=wrapped;
    }
    if(typeof window.saveTg==='function'&&!window.saveTg.__axTelegramWrapped){
      originalSaveTg=window.saveTg;
      const wrapped=async function(){const r=await originalSaveTg.apply(this,arguments);const id=($('api-id')?.value||'').trim(),hash=($('api-hash')?.value||'').trim();if(id&&(hash||($('api-hash')?.placeholder||'').match(/…|set/i))){setTimeout(()=>{if($('login-pan')?.style.display!=='none')void startQr(false);},120);}return r;};
      wrapped.__axTelegramWrapped=true;window.saveTg=wrapped;
    }
  }
  function renderSessionInvalidated(invalidated){
    const panel=$('ax-tg-session-recovery');
    if(panel)panel.open=!!invalidated;
    if(invalidated){const loginPanel=$('login-pan');if(loginPanel)loginPanel.style.display='';}
  }
  async function refreshSessionInvalidation(){
    if(typeof window.api!=='function')return;
    try{
      const r=await api('/api/telegram/auth-status',{cache:'no-store'});
      if(r&&r._ok!==false)renderSessionInvalidated(!!r.session_invalidated);
    }catch{}
  }
  async function resetInvalidTelegramSession(){
    const explanation='Only replace the session if Telegram reported AuthKeyDuplicatedError or you know this authorization was used from two IP addresses; a network timeout alone is not a reason. Stop other Archive X instances using this session first. This preserves the old encrypted session as a backup and requires a new Telegram sign-in; API credentials and library data remain unchanged. Configure a working fixed-egress proxy before signing in if you need a stable IP.';
    let confirmed=false;
    if(typeof window.axConfirm==='function'){
      confirmed=!!(await window.axConfirm(explanation,{title:'Start a fresh Telegram session?',okLabel:'Archive session',confirmLabel:'Archive session',danger:true}));
    }else confirmed=window.confirm(explanation);
    if(!confirmed)return;
    const button=$('ax-tg-reset-session-button'),status=$('ax-tg-session-recovery-status');
    if(button)button.disabled=true;
    if(status)status.textContent='Archiving the invalid session…';
    try{
      const r=await api('/api/telegram/reset-invalid-session',{method:'POST',body:'{}',cache:'no-store'});
      if(!r||r._ok===false){if(status)status.textContent=r?.detail||'Could not archive the invalid session.';return;}
      renderSessionInvalidated(false);
      const recovery=$('ax-tg-session-recovery');if(recovery)recovery.open=true;
      const loginPanel=$('login-pan');if(loginPanel)loginPanel.style.display='';
      setText('login-msg','The invalid session was preserved as a backup. Sign in to Telegram again.');
      if(status)status.textContent='Fresh session ready. Choose QR or phone sign-in.';
    }catch(e){
      if(status)status.textContent=e?.message||'Could not archive the invalid session.';
    }finally{
      if(button)button.disabled=false;
    }
  }
  function init(){
    renderCountryOptions('');
    wrapLegacyHandlers();
    const local=$('ax-tg-phone-local');if(local)local.addEventListener('input',composePhone);
    const list=$('ax-tg-country-list');if(list)list.addEventListener('keydown',e=>{if(e.key==='Escape'){const p=$('ax-tg-country-picker'),t=$('ax-tg-country-toggle');if(p)p.hidden=true;if(t)t.setAttribute('aria-expanded','false');t?.focus();}});
    document.addEventListener('click',e=>{const p=$('ax-tg-country-picker'),wrap=document.querySelector('#settings-v54-telegram .ax-tg-country-wrap');if(p&&!p.hidden&&wrap&&!wrap.contains(e.target)){p.hidden=true;$('ax-tg-country-toggle')?.setAttribute('aria-expanded','false');}});
    document.addEventListener('keydown',e=>{if(e.key==='Escape'){const picker=$('ax-tg-country-picker');if(picker&&!picker.hidden){picker.hidden=true;$('ax-tg-country-toggle')?.setAttribute('aria-expanded','false');}}});
    // pInit may have started the legacy config fetch before this late layer ran;
    // reload once through the same API to prefill the country/phone controls.
    if(typeof window.api==='function')api('/api/settings/telegram',{cache:'no-store'}).then(r=>{if(r&&r._ok!==false){updateTelegramProxyStatus(r.proxy_settings||r.proxy_status||'unavailable');renderSessionInvalidated(!!r.session_invalidated);if(r.api_id&&!$('api-id')?.value)$('api-id').value=r.api_id;if(r.api_hash_masked&&$('api-hash'))$('api-hash').placeholder=r.api_hash_masked;if(r.phone)loadPhoneFromFull(r.phone);if(r.connected){const lp=$('login-pan');if(lp)lp.style.display='none';}}else updateTelegramProxyStatus('unavailable');}).catch(()=>updateTelegramProxyStatus('unavailable'));
  }
  window.axTelegramCredentialsOpened=openCredentials;
  window.axTelegramShowMethod=showMethod;
  window.axStartQrLogin=()=>startQr(false);
  window.axRefreshQrLogin=()=>startQr(true);
  window.axCancelQrLogin=()=>cancelQr(false);
  window.axExplainPasskey=explainPasskey;
  window.axToggleCountryPicker=toggleCountryPicker;
  window.axFilterCountries=renderCountryOptions;
  window.axTgComposePhone=composePhone;
  window.axTgLoadPhoneFromFull=loadPhoneFromFull;
  window.axTelegramBackToMethods=backToMethods;
  window.axTelegramProxyTypeChanged=applyTelegramProxyType;
  window.axSaveTelegramProxySettings=saveTelegramProxySettings;
  window.axTelegramResetInvalidSession=resetInvalidTelegramSession;
  if(document.readyState==='loading')document.addEventListener('DOMContentLoaded',init,{once:true});else init();
})();
