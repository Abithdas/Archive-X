/* ============================================================================
 * FILE: js/modules/api-client.js
 * AREA: General
 * LOAD: Classic script. Order is set by index.html, the HTML partials and
 *       _LAYERS in web/html.py. Later scripts can wrap earlier globals,
 *       so check docs/CODE_MAP.md before moving code between files.
 * ============================================================================ */
// Shared API transport, read caching, and request diagnostics.
const _apiCache=new Map(), _apiInflight=new Map();
let _apiCacheEpoch=0;
const _apiCacheTTL=2200;
const _apiReadTimeoutMs=15000;
function _apiCacheable(url,method){
  if(method!=='GET')return false;
  // Job state drives Pause/Resume/Stop buttons. A 2.2s browser cache here
  // made controls look inert immediately after a successful lifecycle action.
  if(/\/api\/(play|thumb|status|events|logs|jobs)(?:\/|$)/.test(String(url).split('?')[0]))return false;
  return true;
}
function _apiReadDeadline(url,method){
  if(method!=='GET')return 0;
  const path=String(url).split('?')[0];
  // These reads are part of user-started jobs or deliver potentially large
  // streams/exports. They must not inherit the ordinary page-read deadline.
  if(/^\/api\/(?:play|thumb|events|logs|jobs)(?:\/|$)/.test(path))return 0;
  if(/^\/api\/channels\/[^/]+\/index-status$/.test(path))return 0;
  if(/(?:^|\/)(?:export|download|stream)(?:\/|$)/i.test(path))return 0;
  if(/^\/api\/status$/.test(path)||/^\/api\/operations\/resources$/.test(path))return 10000;
  return _apiReadTimeoutMs;
}
function _apiInvalidate(){_apiCacheEpoch++;_apiCache.clear();_apiInflight.clear();}
function clearApiResponseCache(){_apiInvalidate();}
function resetApiClientState(){_apiInvalidate();}
function _apiSeed(url,value,ttl=_apiCacheTTL){
  _apiCacheEpoch++;
  _apiInflight.clear();
  _apiCache.set(url,{at:performance.now(),value,ttl});
  while(_apiCache.size>200)_apiCache.delete(_apiCache.keys().next().value);
}
async function api(url,opts={}){
  const method=String(opts.method||'GET').toUpperCase();
  const readTimeout=_apiReadDeadline(url,method);
  const cacheable=_apiCacheable(url,method)&&opts.cache!=='no-store';
  const inflightRead=method==='GET'&&readTimeout===_apiReadTimeoutMs;
  const cacheEpoch=_apiCacheEpoch;
  const now=performance.now();
  if(cacheable){
    const hit=_apiCache.get(url);
    if(hit&&now-hit.at<(hit.ttl||_apiCacheTTL))return hit.value;
    if(hit)_apiCache.delete(url);
  }
  const pending=inflightRead?_apiInflight.get(url):null;
  if(pending)return pending;
  const run=async()=>{
    const startedAt=performance.now();
    const options={credentials:'include',...opts,headers:{...(opts.headers||{})}};
    const liveRead=readTimeout===10000&&(String(url).split('?')[0]==='/api/status'||String(url).split('?')[0]==='/api/operations/resources');
    const controller=readTimeout?new AbortController():null;
    const callerSignal=options.signal;
    let timedOut=false,forwardAbort=null,rejectDeadline=null;
    if(controller)options.signal=controller.signal;
    const deadlineFailure=controller?new Promise((_,reject)=>{rejectDeadline=reject;}):null;
    const bounded=promise=>controller?Promise.race([promise,deadlineFailure]):promise;
    if(controller&&callerSignal){
      forwardAbort=()=>{controller.abort();rejectDeadline(new Error('Request canceled'));};
      if(callerSignal.aborted)forwardAbort();
      else callerSignal.addEventListener('abort',forwardAbort,{once:true});
    }
    const deadline=controller?setTimeout(()=>{
      timedOut=true;
      controller.abort();
      rejectDeadline(new Error(`Request timed out after ${readTimeout}ms`));
    },readTimeout):0;
    if(method!=='GET')_apiInvalidate();
    if(options.body!==undefined && !options.headers['Content-Type']) options.headers['Content-Type']='application/json';
    try{
      const r=await bounded(fetch(url,options));
      if(r.ok)axDiagSessionRecovered();
      const duration=performance.now()-startedAt;
      const ct=r.headers.get('content-type')||'';
      const responseContext={request_id:r.headers.get('x-request-id')||'',traceparent:r.headers.get('traceparent')||''};
      if(liveRead&&r.ok&&!ct.includes('application/json')){
        axDiagRecord('api.invalid_response',{message:'Live endpoint returned non-JSON data',status:r.status,method,data:{...responseContext,request_path:String(url).split('?')[0],content_type:ct}},'ERROR');
        return {_ok:false,_status:r.status,detail:'Unexpected live-status response. Check the app service and API routing.'};
      }
      // Live-read body/parse failures must release the poll lock as failures,
      // not become an apparently successful empty status response.
      let body=ct.includes('application/json')
        ? await bounded(r.json()).catch(e=>{if(liveRead||timedOut)throw e;return {};})
        : {text:await bounded(r.text()).catch(e=>{if(liveRead||timedOut)throw e;return '';})};
      if(r.status===401)showLP();
      if(method!=='GET')_apiInvalidate();
      if(!r.ok){
        const detail=body&&typeof body==='object' ? (body.detail||body.error||body.message) : '';
        axDiagRecord('api.error',{message:detail||`HTTP ${r.status}`,status:r.status,method,duration_ms:Math.round(duration),data:{...responseContext,request_path:String(url).split('?')[0]}},'ERROR');
        console.error('API request failed',r.status,url,detail||body);
        if(Array.isArray(body))return body;
        return {...(body||{}),detail:detail||`HTTP ${r.status}`,_status:r.status,_ok:false};
      }
      if(duration>1500)axDiagRecord('api.slow',{message:`Slow API request: ${method} ${String(url).split('?')[0]}`,status:r.status,method,url:String(url).split('?')[0],duration_ms:Math.round(duration),data:responseContext},'WARN');
      if(Array.isArray(body)){
        if(cacheable&&cacheEpoch===_apiCacheEpoch){_apiCache.set(url,{at:performance.now(),value:body,ttl:_apiCacheTTL});while(_apiCache.size>200)_apiCache.delete(_apiCache.keys().next().value);}
        return body;
      }
      const value={...(body||{}),_status:r.status,_ok:true};
      if(cacheable&&cacheEpoch===_apiCacheEpoch){_apiCache.set(url,{at:performance.now(),value,ttl:_apiCacheTTL});while(_apiCache.size>200)_apiCache.delete(_apiCache.keys().next().value);}
      return value;
    }catch(e){
      const timedOutRequest=timedOut||(liveRead&&controller?.signal.aborted);
      const detail=timedOutRequest?`Request timed out after ${Math.round(readTimeout/1000)} seconds. Try again.`:`API connection failed: ${e&&e.message?e.message:String(e)}`;
      axDiagRecord(timedOutRequest?'api.timeout':'api.network_error',{message:detail,method,duration_ms:Math.round(performance.now()-startedAt),data:{request_path:String(url).split('?')[0],online:navigator.onLine}},'ERROR');
      console.error('API network error',url,e);
      return {_ok:false,_status:0,detail};
    }finally{
      clearTimeout(deadline);
      if(callerSignal&&forwardAbort)callerSignal.removeEventListener('abort',forwardAbort);
    }
  };
  const promise=run();
  if(inflightRead)_apiInflight.set(url,promise);
  try{return await promise;}finally{if(inflightRead&&_apiInflight.get(url)===promise)_apiInflight.delete(url);}
}
