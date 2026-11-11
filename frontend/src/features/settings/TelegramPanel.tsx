import { useQueryClient } from '@tanstack/react-query'
import { useEffect, useRef, useState, type ReactNode } from 'react'
import { ApiError, api } from '@/api/client'
import { Dialog } from '@/design/Dialog'
import { Icon } from '@/design/Icon'
import { Segmented } from '@/design/Segmented'
import { cn } from '@/design/cn'
import { CountryPhone } from './CountryPhone'
import { DEFAULT_COUNTRY, NEED_PHONE, composePhone, splitSaved, type Country } from './countries'
import { useAuthStatus, useTelegramSettings } from './queries'
import { PORT_HINT, PROXY_TYPES, accountLine, apiStatus, codeSent, credentialsReady, proxyFields, proxyForm, proxyPayload, proxyPlaceholders, proxyStatus, qrView, verifyOutcome, type ProxyForm, type ProxyType, type QrView } from './telegram'

type Obj = Record<string, unknown>
const BOX = 'rounded-[18px] border border-line bg-surface p-5'
const INPUT = 'h-12 w-full min-w-0 rounded-xl border border-line-2 bg-bg px-3.5 text-[15px] text-fg outline-none placeholder:text-faint focus:border-accent/60 disabled:opacity-50'
const BTN = 'h-11 rounded-xl border border-line-2 px-4 text-sm font-semibold hover:bg-fg/5 disabled:opacity-50'
const PRIMARY = 'h-11 rounded-xl bg-accent px-5 text-sm font-bold text-accent-fg disabled:opacity-60'
const err = (e: unknown, fallback: string) => (e instanceof ApiError ? e.detail || fallback : fallback)
const post = <T = Obj,>(path: string, body: object = {}) => api<T>(path, { method: 'POST', json: body })

function Card({ title, pill, children, label }: { title: string; pill?: ReactNode; children: ReactNode; label?: string }) {
  return (
    <section aria-label={label ?? title} className={BOX}>
      <div className="mb-3 flex items-center justify-between gap-3"><h3 className="text-xl font-extrabold">{title}</h3>{pill}</div>
      {children}
    </section>
  )
}
const Pill = ({ children, tone = 'muted' }: { children: ReactNode; tone?: 'ok' | 'warn' | 'muted' }) => <span className={cn('shrink-0 rounded-full px-3 py-1 text-xs font-bold', tone === 'ok' ? 'bg-good/15 text-good' : tone === 'warn' ? 'bg-warn/15 text-warn' : 'bg-surface-3 text-muted')}>{children}</span>
const Label = ({ children }: { children: ReactNode }) => <span className="text-[11px] font-bold uppercase tracking-wide text-muted">{children}</span>

/** Telegram: account, API credentials, proxy and sign-in (QR or phone), as in the classic Settings. */
export function TelegramPanel() {
  const settings = useTelegramSettings()
  const auth = useAuthStatus()
  const qc = useQueryClient()
  const s = (settings.data ?? {}) as Obj
  const connected = Boolean(auth.data?.connected ?? s.connected)
  const refresh = () => { void qc.invalidateQueries({ queryKey: ['settings'] }); void qc.invalidateQueries({ queryKey: ['status'] }); void qc.invalidateQueries({ queryKey: ['channels'] }) }

  // ---- API credentials
  const [apiId, setApiId] = useState('')
  const [hash, setHash] = useState('')
  const [credMsg, setCredMsg] = useState('')
  const [testMsg, setTestMsg] = useState<{ text: string; ok: boolean } | null>(null)
  const [busy, setBusy] = useState('')
  const loaded = useRef(false)
  useEffect(() => { if (settings.data && !loaded.current) { loaded.current = true; setApiId(String(s.api_id ?? '')) } }, [settings.data, s.api_id])
  const maskedSaved = Boolean(s.api_hash_masked)
  const saveCreds = async () => {
    setBusy('save')
    try {
      const r = await post<Obj>('/api/settings/telegram', { api_id: apiId.trim(), api_hash: hash.trim() })
      setCredMsg(r.ok ? 'Saved.' : `Error: ${String(r.detail ?? 'Could not save Telegram settings.')}`)
      if (r.ok) setHash('')
      refresh()
      setTimeout(() => setCredMsg(''), 2500)
    } catch (e) { setCredMsg(`Error: ${err(e, 'Could not save Telegram settings.')}`) } finally { setBusy('') }
  }
  const testApi = async () => {
    setBusy('test'); setTestMsg({ text: 'Testing Telegram API…', ok: true })
    try {
      const r = await post<Obj>('/api/telegram/self-test')
      setTestMsg(r.ok ? { text: r.authorized ? 'API reachable; account already authorized.' : 'API reachable; login is not required for this test.', ok: true } : { text: `Test failed: ${String(r.detail ?? 'Telegram API connection failed.')}`, ok: false })
    } catch (e) { setTestMsg({ text: `Test failed: ${err(e, 'Telegram API connection failed.')}`, ok: false }) } finally { setBusy('') }
  }
  const [confirm, setConfirm] = useState<null | 'logout' | 'reset'>(null)
  const [recoveryMsg, setRecoveryMsg] = useState('')
  const doConfirmed = async () => {
    const kind = confirm
    setConfirm(null)
    if (kind === 'logout') {
      setBusy('logout'); setCredMsg('Logging out of Telegram…')
      try {
        const r = await post<Obj>('/api/telegram/logout-account')
        setCredMsg(r.ok ? 'Logged out of Telegram. API credentials remain saved.' : `Error: ${String(r.detail ?? 'Could not log out of Telegram.')}`)
        refresh()
      } catch (e) { setCredMsg(`Error: ${err(e, 'Could not log out of Telegram.')}`) } finally { setBusy('') }
    } else if (kind === 'reset') {
      setBusy('reset'); setRecoveryMsg('Archiving the saved session…')
      try {
        const r = await post<Obj>('/api/telegram/reset-invalid-session')
        setRecoveryMsg(r.ok === false ? String(r.detail ?? 'Could not archive the session.') : 'Saved session archived. Sign in again below.')
        refresh()
      } catch (e) { setRecoveryMsg(err(e, 'Could not archive the saved session.')) } finally { setBusy('') }
    }
  }

  // ---- proxy
  const proxy = (s.proxy_settings ?? {}) as Obj
  const [pf, setPf] = useState<ProxyForm>(proxyForm(undefined))
  const [proxyMsg, setProxyMsg] = useState('')
  const proxyLoaded = useRef(false)
  useEffect(() => { if (settings.data && !proxyLoaded.current) { proxyLoaded.current = true; setPf(proxyForm(proxy)) } }, [settings.data]) // eslint-disable-line react-hooks/exhaustive-deps
  const ps = proxyStatus(proxy)
  const fields = proxyFields(pf.type)
  const ph = proxyPlaceholders(proxy)
  const setP = <K extends keyof ProxyForm>(k: K, v: ProxyForm[K]) => setPf((f) => ({ ...f, [k]: v }))
  const saveProxy = async () => {
    setBusy('proxy'); setProxyMsg('Saving protected settings…')
    try {
      const r = await post<Obj & { proxy_settings?: Obj }>('/api/settings/telegram/proxy', proxyPayload(pf))
      if (r.ok === false) return void setProxyMsg(`Error: ${String(r.detail ?? 'Could not save proxy settings.')}`)
      setProxyMsg(r.proxy_settings?.restart_required ? 'Saved. Restart Archive X to apply.' : 'Saved.')
      setPf((f) => ({ ...f, password: '', secret: '', username: '', clearAuth: false, clearSecret: false }))
      refresh()
    } catch (e) { setProxyMsg(`Error: ${err(e, 'Could not save proxy settings.')}`) } finally { setBusy('') }
  }

  // ---- sign in
  const [method, setMethod] = useState<'qr' | 'phone'>('qr')
  const [needPassword, setNeedPassword] = useState<null | 'qr' | 'phone'>(null)
  const [qr, setQr] = useState<QrView | null>(null)
  const [qrActive, setQrActive] = useState(false)
  const [loginMsg, setLoginMsg] = useState('')
  const [country, setCountry] = useState<Country>(DEFAULT_COUNTRY)
  const [local, setLocal] = useState('')
  const phoneLoaded = useRef(false)
  useEffect(() => { if (settings.data && !phoneLoaded.current) { phoneLoaded.current = true; const sp = splitSaved(String(s.phone ?? '')); setCountry(sp.country); setLocal(sp.local) } }, [settings.data, s.phone])
  const fullPhone = () => composePhone(local, country).full
  const [code, setCode] = useState('')
  const [codeAsked, setCodeAsked] = useState(false)
  const [pw, setPw] = useState('')
  const [passkey, setPasskey] = useState(false)
  const done = () => { setQrActive(false); setNeedPassword(null); setLoginMsg('Telegram login successful. Session saved locally.'); refresh(); void api('/api/telegram/refresh', { method: 'POST' }).catch(() => undefined) }
  const apply = (state: Obj) => {
    const v = qrView(state)
    setQr(v)
    if (v.kind === 'connected') return done()
    setQrActive(v.kind === 'waiting')
    if (v.kind === 'password') { setNeedPassword('qr'); setLoginMsg(v.message) }
    else setLoginMsg(v.message)
  }
  useEffect(() => {
    if (!qrActive) return
    let busyPoll = false
    const t = setInterval(async () => {
      if (busyPoll) return
      busyPoll = true
      try { apply(await api<Obj>('/api/telegram/qr-login/status')) } catch { setLoginMsg('Connection issue while checking QR login. Retrying…') } finally { busyPoll = false }
    }, 1400)
    return () => clearInterval(t)
  }, [qrActive]) // eslint-disable-line react-hooks/exhaustive-deps
  const qrStart = async (again: boolean) => {
    if (!credentialsReady(apiId, hash, maskedSaved)) return void setLoginMsg('Enter and save your Telegram API ID and API Hash first.')
    setBusy('qr'); setLoginMsg(again ? 'Refreshing QR code…' : 'Generating a secure QR code…')
    try { apply(await post<Obj>(`/api/telegram/qr-login/${again ? 'refresh' : 'start'}`)) } catch (e) { setLoginMsg(err(e, 'Could not start QR login. Save your API credentials and retry.')) } finally { setBusy('') }
  }
  const qrCancel = async () => { setQrActive(false); try { await post('/api/telegram/qr-login/cancel') } catch { /* ignore */ } setQr({ kind: 'idle', message: 'Generate a short-lived code using the button below.', show: false, tone: 'info' }); setLoginMsg('QR login cancelled.') }
  const sendCode = async () => {
    const full = fullPhone()
    if (!full) return void setLoginMsg(NEED_PHONE)
    setBusy('code'); setLoginMsg('Sending code…')
    try {
      const r = await post<Obj>('/api/telegram/send_code', { phone: full })
      if (codeSent(r)) { setCodeAsked(true); setLoginMsg('Code sent — check Telegram.') } else setLoginMsg(`Error: ${String(r.detail ?? r.error ?? 'Could not send code')}`)
    } catch (e) { setLoginMsg(`Error: ${err(e, 'Could not send code')}`) } finally { setBusy('') }
  }
  const verify = async () => {
    setBusy('verify'); setLoginMsg('Verifying…')
    try {
      const o = verifyOutcome(await post<Obj>('/api/telegram/verify_code', { phone: fullPhone(), code: code.trim() }))
      if (o.kind === 'done') done()
      else if (o.kind === 'password') { setNeedPassword('phone'); setLoginMsg('Two-factor password required.') }
      else setLoginMsg(`Error: ${o.message}`)
    } catch (e) { setLoginMsg(`Error: ${err(e, 'Verification failed')}`) } finally { setBusy('') }
  }
  const verifyPw = async () => {
    setBusy('pw'); setLoginMsg('Verifying 2FA password…')
    try {
      const r = await post<Obj>('/api/telegram/verify_2fa', { password: pw })
      if (r.ok && !r.two_factor_required) { setPw(''); done() } else setLoginMsg(`Error: ${String(r.detail ?? r.error ?? 'Invalid 2FA password')}`)
    } catch (e) { setLoginMsg(`Error: ${err(e, 'Invalid 2FA password')}`) } finally { setBusy('') }
  }

  const st = apiStatus(s)
  const showLogin = !connected
  return (
    <div className="grid gap-4">
      <Card title="Account" pill={<Pill tone={connected ? 'ok' : 'warn'}>{connected ? 'Connected' : 'Not connected'}</Pill>}>
        <div className="flex items-center gap-4"><span className="grid size-12 place-items-center rounded-full bg-surface-3 text-lg font-bold">{connected ? 'T' : 'A'}</span>
          <div><b className="block text-lg">Telegram account</b><small className="text-sm text-muted">{accountLine({ connected, configured: s.configured })}</small></div></div>
      </Card>

      <Card title="API credentials" pill={<Pill tone={st.tone === 'ok' ? 'ok' : 'muted'}>{s.api_verified ? 'Verified' : s.configured ? 'Saved' : 'Not verified'}</Pill>}>
        <a href="https://my.telegram.org/apps" target="_blank" rel="noreferrer" className="flex h-12 items-center justify-center gap-2 rounded-xl bg-accent text-[15px] font-bold text-accent-fg hover:brightness-105"><Icon name="send" />Login with Telegram<Icon name="external" className="size-4" /></a>
        <p className="mb-4 mt-2 text-xs text-muted">Opens Telegram’s official API page. Copy your API ID and API Hash below; Archive X cannot read credentials from another website automatically.</p>
        <p className="mb-3 text-center text-xs text-faint">or enter manually</p>
        <div className="grid gap-3">
          <label className="grid gap-1.5"><Label>API ID</Label><input value={apiId} onChange={(e) => setApiId(e.target.value)} inputMode="numeric" placeholder="12345678" aria-label="API ID" autoComplete="off" className={INPUT} /></label>
          <label className="grid gap-1.5"><Label>API Hash</Label><input type="password" value={hash} onChange={(e) => setHash(e.target.value)} placeholder={maskedSaved ? String(s.api_hash_masked) : 'Paste API Hash'} aria-label="API Hash" autoComplete="off" className={INPUT} /></label>
        </div>
        <div className="mt-4 flex flex-wrap items-center gap-2.5">
          <button type="button" disabled={!!busy || !apiId.trim()} onClick={() => void saveCreds()} className={PRIMARY}>{busy === 'save' ? 'Saving…' : 'Save credentials'}</button>
          <button type="button" disabled={!!busy} onClick={() => void testApi()} className={BTN}>{busy === 'test' ? 'Testing…' : 'Test API'}</button>
          {connected ? <button type="button" disabled={!!busy} onClick={() => setConfirm('logout')} className={cn(BTN, 'text-bad')}>Log out of Telegram</button> : null}
          <span className={cn('flex items-center gap-1.5 text-xs', st.tone === 'ok' ? 'text-good' : 'text-warn')}><Icon name={st.tone === 'ok' ? 'check' : 'close'} className="size-3.5" />{st.text}</span>
        </div>
        <p role="status" className="mt-2 min-h-4 text-xs text-muted">{credMsg}</p>
        {testMsg ? <p role="status" className={cn('text-xs', testMsg.ok ? 'text-good' : 'text-bad')}>{testMsg.text}</p> : null}
        <details className="mt-3 rounded-xl border border-line px-3 py-2"><summary className="cursor-pointer text-sm font-bold">Telegram session recovery</summary>
          <p className="mt-2 text-xs text-muted">Use this after Telegram reports AuthKeyDuplicatedError or says the session was used from two IP addresses. A network timeout alone does not mean the session is invalid. First stop other Archive X instances and configure a working fixed-egress proxy if needed.</p>
          <button type="button" disabled={!!busy} onClick={() => setConfirm('reset')} className={cn(BTN, 'mt-3 h-10 text-bad')}>Archive saved session and sign in again</button>
          <span role="status" className="ml-3 text-xs text-muted">{recoveryMsg}</span></details>
      </Card>

      <Card title="Telegram proxy" pill={<Pill>{ps.label}</Pill>}>
        <p className="mb-1 text-sm text-muted">Route Archive X’s Telegram connection through a proxy.</p>
        <p className="mb-4 text-xs text-muted">{ps.help}</p>
        <div className="grid gap-3 sm:grid-cols-2">
          <label className="grid gap-1.5 sm:col-span-2"><Label>Proxy type</Label>
            <select value={pf.type} disabled={ps.locked} onChange={(e) => setP('type', e.target.value as ProxyType)} aria-label="Proxy type" className={cn(INPUT, 'sm:max-w-xs')}>{PROXY_TYPES.map(([k, l]) => <option key={k} value={k}>{l}</option>)}</select></label>
          {fields.host ? <label className="grid gap-1.5"><Label>Server</Label><input value={pf.host} disabled={ps.locked} onChange={(e) => setP('host', e.target.value)} placeholder="proxy.example.com" aria-label="Proxy server" className={INPUT} /></label> : null}
          {fields.port ? <label className="grid gap-1.5"><Label>Port</Label><input value={pf.port} disabled={ps.locked} inputMode="numeric" onChange={(e) => setP('port', e.target.value)} placeholder={PORT_HINT[pf.type]} aria-label="Proxy port" className={INPUT} /></label> : null}
          {fields.auth ? (
            <>
              <label className="grid gap-1.5"><Label>Username</Label><input value={pf.username} disabled={ps.locked} onChange={(e) => setP('username', e.target.value)} placeholder={ph.username} aria-label="Proxy username" autoComplete="off" className={INPUT} /></label>
              <label className="grid gap-1.5"><Label>Password</Label><input type="password" value={pf.password} disabled={ps.locked} onChange={(e) => setP('password', e.target.value)} placeholder={ph.password} aria-label="Proxy password" autoComplete="off" className={INPUT} /></label>
              <label className="flex items-center gap-2 text-xs text-muted sm:col-span-2"><input type="checkbox" checked={pf.clearAuth} disabled={ps.locked} onChange={(e) => setP('clearAuth', e.target.checked)} className="accent-[var(--color-accent)]" />Remove the saved username and password</label>
            </>
          ) : null}
          {fields.secret ? (
            <>
              <label className="grid gap-1.5 sm:col-span-2"><Label>MTProto secret</Label><input type="password" value={pf.secret} disabled={ps.locked} onChange={(e) => setP('secret', e.target.value)} placeholder={ph.secret} aria-label="MTProto secret" autoComplete="off" className={INPUT} /></label>
              <label className="flex items-center gap-2 text-xs text-muted sm:col-span-2"><input type="checkbox" checked={pf.clearSecret} disabled={ps.locked} onChange={(e) => setP('clearSecret', e.target.checked)} className="accent-[var(--color-accent)]" />Remove the saved secret</label>
            </>
          ) : null}
        </div>
        <div className="mt-4 flex flex-wrap items-center gap-3"><button type="button" disabled={ps.locked || !!busy} onClick={() => void saveProxy()} className={PRIMARY}>{busy === 'proxy' ? 'Saving…' : 'Save proxy settings'}</button><span role="status" className="text-xs text-muted">{proxyMsg}</span></div>
        <p className="mt-3 text-xs text-faint">Proxy credentials are encrypted in Archive X’s protected settings and never returned to the browser. Restart Archive X to apply changes. For a stable source IP, use a provider with fixed egress; SOCKS5 or HTTP is recommended. MTProto uses Telethon’s experimental transport.</p>
      </Card>

      {showLogin ? (
        <Card title="Sign in to Telegram" pill={<Pill><Icon name="lock" className="mr-1 inline size-3" />Local session</Pill>}>
          <p className="mb-3 text-sm text-muted">Not logged in: choose a method. You may be asked for the code Telegram sends, then your cloud password if two-step verification is on.</p>
          {needPassword ? (
            <div className="grid max-w-md gap-3">
              <h4 className="text-lg font-extrabold">Enter Cloud Password</h4>
              <p className="text-sm text-muted">Two-Step Verification is enabled on your Telegram account. Enter your Telegram cloud password to finish signing in.</p>
              <input type="password" value={pw} onChange={(e) => setPw(e.target.value)} onKeyDown={(e) => e.key === 'Enter' && void verifyPw()} placeholder="Telegram cloud password" aria-label="Telegram cloud password" autoComplete="current-password" autoFocus className={INPUT} />
              <div className="flex gap-2"><button type="button" disabled={!!busy || !pw} onClick={() => void verifyPw()} className={PRIMARY}>Verify password</button>
                {needPassword === 'phone' ? <button type="button" onClick={() => { setNeedPassword(null); setLoginMsg('') }} className={BTN}>Back</button> : null}</div>
            </div>
          ) : (
            <>
              <Segmented<'qr' | 'phone'> label="Sign-in method" value={method} onChange={(m) => { setMethod(m); setLoginMsg(''); setPasskey(false) }} options={[{ value: 'qr', label: 'QR Code' }, { value: 'phone', label: 'Phone Number' }]} />
              {method === 'qr' ? (
                <div className="mt-4 grid gap-5 md:grid-cols-[minmax(0,1fr)_260px]">
                  <div className="grid content-start gap-3">
                    <h4 className="text-lg font-extrabold">Log in to Telegram by QR Code</h4>
                    <ol className="grid gap-2.5 text-sm">{['Open Telegram on your phone', <>Go to <b>Settings → Devices → Link Desktop Device</b></>, 'Point your phone at this screen and confirm the login'].map((t, i) => <li key={i} className="flex items-center gap-3"><span className="grid size-7 place-items-center rounded-full bg-accent/15 text-xs font-bold text-accent">{i + 1}</span>{t}</li>)}</ol>
                    {!credentialsReady(apiId, hash, maskedSaved) ? <p className="text-xs text-muted">Save API credentials first, then generate a QR code.</p> : null}
                    <div className="flex flex-wrap gap-2">
                      {!qrActive ? <button type="button" disabled={!!busy} onClick={() => void qrStart(false)} className={PRIMARY}>Generate QR Code</button> : <><button type="button" disabled={!!busy} onClick={() => void qrStart(true)} className={BTN}>Refresh QR Code</button><button type="button" onClick={() => void qrCancel()} className={BTN}>Cancel</button></>}
                    </div>
                    <button type="button" onClick={() => setPasskey(!passkey)} className="justify-self-start text-sm font-bold text-accent">Log in with Passkey</button>
                    {passkey ? <p className="rounded-xl bg-bg p-3 text-xs text-muted">Telegram currently restricts native passkey login to supported first-party clients. Archive X cannot complete Telegram passkey authentication directly yet. Use QR Code or Phone Number instead.</p> : null}
                  </div>
                  <div className="grid aspect-square w-full max-w-[260px] place-items-center justify-self-center rounded-2xl bg-white p-3">
                    {qr?.kind === 'waiting' && qr.image ? <img src={qr.image} alt="Telegram login QR code" className="size-full object-contain" /> : <div className="grid place-items-center gap-2 rounded-xl bg-slate-900 p-4 text-center text-xs text-slate-300"><Icon name="grid" className="size-8" /><b>QR code appears here</b><span>Generate a short-lived code using the button.</span></div>}
                  </div>
                </div>
              ) : (
                <div className="mt-4 grid max-w-md gap-3">
                  <h4 className="text-lg font-extrabold">Log in with phone number</h4>
                  <div className="grid gap-1.5"><Label>Phone number</Label><CountryPhone country={country} local={local} onChange={(c, l) => { setCountry(c); setLocal(l) }} inputClass={INPUT} /></div>
                  <div><button type="button" disabled={!!busy} onClick={() => void sendCode()} className={PRIMARY}>{busy === 'code' ? 'Sending…' : 'Continue'}</button></div>
                  {codeAsked ? (
                    <label className="grid gap-1.5"><Label>Telegram login code</Label><span className="flex gap-2"><input value={code} onChange={(e) => setCode(e.target.value)} onKeyDown={(e) => e.key === 'Enter' && void verify()} inputMode="numeric" autoComplete="one-time-code" placeholder="Enter the code" aria-label="Telegram login code" className={INPUT} /><button type="button" disabled={!!busy || !code.trim()} onClick={() => void verify()} className={PRIMARY}>Verify</button></span></label>
                  ) : null}
                </div>
              )}
              <p role="status" aria-live="polite" className="mt-3 min-h-5 text-sm text-muted">{loginMsg}</p>
            </>
          )}
        </Card>
      ) : (
        <Card title="Signed in" pill={<Pill tone="ok">Connected</Pill>}><p className="text-sm text-muted">Session saved and connected. Use “Log out of Telegram” above only to sign in again or switch account.</p></Card>
      )}

      {confirm ? (
        <Dialog open onClose={() => setConfirm(null)} title={confirm === 'logout' ? 'Log out of Telegram?' : 'Archive the saved session?'}>
          <div className="grid grid-cols-[minmax(0,1fr)] gap-4"><p className="text-sm text-muted">{confirm === 'logout' ? 'Log out of the Telegram account on this Archive X installation? This revokes the current Telegram session. Your API ID and API Hash will remain saved. Telegram-backed operations may need you to sign in again.' : 'The saved Telegram session will be archived and a fresh authorization will be needed. Your API ID and API Hash stay saved.'}</p>
            <div className="flex flex-wrap justify-end gap-2"><button type="button" onClick={() => setConfirm(null)} className={BTN}>Keep session</button><button type="button" onClick={() => void doConfirmed()} className="h-11 rounded-xl bg-bad px-4 text-sm font-bold text-white">{confirm === 'logout' ? 'Log out of Telegram' : 'Archive session'}</button></div></div>
        </Dialog>
      ) : null}
    </div>
  )
}
