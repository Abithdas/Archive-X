import { useState, type FormEvent, type ReactNode } from 'react'
import { useQuery, useQueryClient } from '@tanstack/react-query'
import { ApiError, api } from '@/api/client'
import { queryKeys, useStatus } from '@/api/hooks'
import { Button } from '@/design/Button'
import { Skeleton } from '@/design/Skeleton'
import { DEFAULT_ADMIN, MIN_PASSWORD, NO_SESSION, SETUP_DONE, SETUP_FAILED, SETUP_HINT, SETUP_SUBTITLE, SIGN_IN_SUBTITLE, loginMessage, setupBody, setupProblem, type LoginHint } from './auth'

/** Renders children once a session exists; otherwise the sign-in form. In the desktop app the shell hands over a
 *  session before the page loads, so this form is only ever seen in browser/LAN mode. */
export function LoginGate({ children }: { children: ReactNode }) {
  const status = useStatus()
  if (status.isPending) {
    return (
      <div className="grid h-full place-items-center">
        <Skeleton className="h-10 w-48" />
      </div>
    )
  }
  if (status.error instanceof ApiError && status.error.status === 401) return <LoginForm />
  return <>{children}</>
}

function LoginForm() {
  const client = useQueryClient()
  const hint = useQuery({ queryKey: ['login-hint'] as const, queryFn: () => api<LoginHint>('/api/login-hint'), retry: false, staleTime: 0 })
  const [setupDone, setSetupDone] = useState(false)
  const [forced, setForced] = useState(false)   // a sign-in answered "setup required" before the hint said so
  const setup = !setupDone && (Boolean(hint.data?.setup_required) || forced)
  const [error, setError] = useState('')
  const [info, setInfo] = useState('')
  const [busy, setBusy] = useState(false)

  async function submit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault()
    const form = new FormData(event.currentTarget)
    const username = String(form.get('username') ?? '')
    setBusy(true); setError(''); setInfo('')
    try {
      if (setup) {
        const password = String(form.get('new-password') ?? ''), confirmation = String(form.get('confirm-password') ?? '')
        const problem = setupProblem(username, password, confirmation)
        if (problem) return void setError(problem)
        try {
          await api('/api/auth/setup', { method: 'POST', json: setupBody(username, password, confirmation) })
        } catch (e) { return void setError(e instanceof ApiError && e.detail ? e.detail : SETUP_FAILED) }
        setSetupDone(true); setForced(false); setInfo(SETUP_DONE); void hint.refetch()
        return
      }
      try {
        await api('/login', { method: 'POST', json: { username: username.trim(), password: String(form.get('password') ?? '') } })
      } catch (e) {
        const m = loginMessage(e instanceof ApiError ? e.status : null, e instanceof ApiError ? e.detail : '')
        if (m.needsSetup) setForced(true)
        return void setError(m.text)
      }
      try { await api('/api/status') } catch { return void setError(NO_SESSION) }
      await client.invalidateQueries({ queryKey: queryKeys.status })
    } finally { setBusy(false) }
  }

  return (
    <main className="grid h-full place-items-center p-4">
      <form onSubmit={(e) => void submit(e)} className="grid w-full max-w-sm gap-3 rounded-card border border-line bg-surface p-6">
        <div className="grid justify-items-center gap-1 text-center">
          <img src="/static/icons/archive-x.png" alt="" className="size-14 rounded-2xl" onError={(e) => (e.currentTarget.style.display = 'none')} />
          <h1 className="text-2xl font-extrabold">Archive X</h1>
          <p className="text-sm text-muted">{setup ? SETUP_SUBTITLE : SIGN_IN_SUBTITLE}</p>
        </div>
        <label className="grid gap-1 text-sm text-muted">
          Username
          <input name="username" autoComplete="username" required defaultValue={setup ? DEFAULT_ADMIN : undefined} className="min-h-11 rounded-xl border border-line bg-bg px-3 text-fg" />
        </label>
        {setup ? (
          <>
            <p className="text-xs text-muted">{SETUP_HINT}</p>
            <label className="grid gap-1 text-sm text-muted">
              New password
              <input name="new-password" type="password" autoComplete="new-password" minLength={MIN_PASSWORD} required className="min-h-11 rounded-xl border border-line bg-bg px-3 text-fg" />
            </label>
            <label className="grid gap-1 text-sm text-muted">
              Confirm password
              <input name="confirm-password" type="password" autoComplete="new-password" minLength={MIN_PASSWORD} required className="min-h-11 rounded-xl border border-line bg-bg px-3 text-fg" />
            </label>
          </>
        ) : (
          <label className="grid gap-1 text-sm text-muted">
            Password
            <input name="password" type="password" autoComplete="current-password" required className="min-h-11 rounded-xl border border-line bg-bg px-3 text-fg" />
          </label>
        )}
        <p role="alert" className="min-h-5 text-sm text-bad">{error}</p>
        {info ? <p role="status" className="text-sm text-good">{info}</p> : null}
        <Button type="submit" variant="primary" disabled={busy}>
          {setup ? (busy ? 'Creating…' : 'Create administrator') : busy ? 'Signing in…' : 'Sign in'}
        </Button>
      </form>
    </main>
  )
}
