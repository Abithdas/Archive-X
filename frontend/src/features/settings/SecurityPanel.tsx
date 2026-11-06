import { useQueryClient } from '@tanstack/react-query'
import { useEffect, useRef, useState } from 'react'
import { toast } from 'sonner'
import { ApiError, api } from '@/api/client'
import { useDesktopInfo } from '@/api/hooks'
import { useWebLogin } from './queries'
import { EMPTY_LOGIN, loginBody, loginProblem, savedMessage, type LoginForm } from './security'

const INPUT = 'h-12 w-full min-w-0 rounded-xl border border-line-2 bg-bg px-3.5 text-[15px] text-fg outline-none placeholder:text-faint focus:border-accent/60'

/** Dashboard authentication: username and password for the web login. */
export function SecurityPanel() {
  const web = useWebLogin()
  const desktop = useDesktopInfo()
  const qc = useQueryClient()
  const [f, setF] = useState<LoginForm>(EMPTY_LOGIN)
  const [msg, setMsg] = useState('')
  const [busy, setBusy] = useState(false)
  const loaded = useRef(false)
  const timer = useRef<ReturnType<typeof setTimeout>>(undefined)
  useEffect(() => { if (web.data && !loaded.current) { loaded.current = true; setF((x) => ({ ...x, username: String(web.data!.username ?? '') })) } }, [web.data])
  useEffect(() => () => clearTimeout(timer.current), [])
  const set = <K extends keyof LoginForm>(k: K, v: string) => { setF((x) => ({ ...x, [k]: v })); setMsg('') }
  const save = async () => {
    const p = loginProblem(f)
    if (p) return void setMsg(p)
    setBusy(true)
    try {
      const r = await api<{ ok?: boolean; detail?: string }>('/api/settings/weblogin', { method: 'POST', json: loginBody(f) })
      if (r.ok === false) throw new ApiError(0, r.detail || 'Could not save')
      setMsg(savedMessage(Boolean(f.password)))
      toast(savedMessage(Boolean(f.password)))
      setF((x) => ({ ...x, current: '', password: '', repeat: '' }))
      loaded.current = false
      await qc.invalidateQueries({ queryKey: ['settings'] })
      clearTimeout(timer.current)
      timer.current = setTimeout(() => setMsg(''), 5000)
    } catch (e) {
      setMsg(`Error: ${e instanceof ApiError ? e.detail || '' : 'Could not save'}`)
    } finally {
      setBusy(false)
    }
  }
  return (
    <section aria-label="Dashboard authentication" className="rounded-[18px] border border-line bg-surface p-5">
      <div className="mb-4 flex items-center justify-between"><h3 className="text-xl font-extrabold">Dashboard authentication</h3><span className="text-xs text-faint">07 / 08</span></div>
      {desktop.data ? <p role="note" className="mb-4 rounded-xl bg-accent/10 p-3 text-sm text-muted">In the desktop app Archive X signs you in automatically with a password that is generated for this computer and never shown. These fields only matter if you open Archive X from a web browser; changing them needs the current password.</p> : null}
      {web.data?.default_login ? <p role="alert" className="mb-4 rounded-xl bg-warn/10 p-3 text-sm text-warn">You are using the default login (the default administrator credential). Set your own password below.</p> : null}
      <form className="grid gap-4" onSubmit={(e) => { e.preventDefault(); void save() }}>
        <label className="grid gap-1.5 text-xs font-semibold text-muted">Username<input value={f.username} onChange={(e) => set('username', e.target.value)} placeholder="admin" autoComplete="username" aria-label="Username" className={INPUT} /></label>
        <label className="grid gap-1.5 text-xs font-semibold text-muted">Current password<input type="password" value={f.current} onChange={(e) => set('current', e.target.value)} autoComplete="current-password" aria-label="Current password" className={INPUT} /></label>
        <div className="grid gap-4 sm:grid-cols-2">
          <label className="grid gap-1.5 text-xs font-semibold text-muted">New password<input type="password" value={f.password} onChange={(e) => set('password', e.target.value)} placeholder="blank = keep" autoComplete="new-password" aria-label="New password" className={INPUT} /></label>
          <label className="grid gap-1.5 text-xs font-semibold text-muted">Repeat new password<input type="password" value={f.repeat} onChange={(e) => set('repeat', e.target.value)} placeholder="Repeat" autoComplete="new-password" aria-label="Repeat new password" className={INPUT} /></label>
        </div>
        <div className="flex items-center gap-3"><button type="submit" disabled={busy} className="h-11 rounded-xl bg-accent px-6 text-sm font-bold text-accent-fg disabled:opacity-60">{busy ? 'Saving…' : 'Save'}</button><span role="status" aria-live="polite" className="text-sm text-muted">{msg}</span></div>
      </form>
    </section>
  )
}
