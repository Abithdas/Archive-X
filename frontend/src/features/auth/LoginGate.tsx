import { useState, type FormEvent, type ReactNode } from 'react'
import { useQueryClient } from '@tanstack/react-query'
import { ApiError, api } from '@/api/client'
import { queryKeys, useStatus } from '@/api/hooks'
import { Button } from '@/design/Button'
import { Skeleton } from '@/design/Skeleton'

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
  const [error, setError] = useState('')
  const [busy, setBusy] = useState(false)

  async function submit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault()
    const form = new FormData(event.currentTarget)
    setBusy(true)
    setError('')
    try {
      await api('/login', { method: 'POST', json: { username: String(form.get('username') ?? ''), password: String(form.get('password') ?? '') } })
      await client.invalidateQueries({ queryKey: queryKeys.status })
    } catch (e) {
      if (e instanceof ApiError && e.status === 428) setError('First-run setup is required. Open the classic interface on the PC to create the administrator.')
      else setError(e instanceof ApiError ? e.detail : 'Could not reach the server.')
    } finally {
      setBusy(false)
    }
  }

  return (
    <main className="grid h-full place-items-center p-4">
      <form onSubmit={submit} className="grid w-full max-w-sm gap-3 rounded-card border border-line bg-surface p-6">
        <h1 className="text-2xl font-extrabold">Archive X</h1>
        <label className="grid gap-1 text-sm text-muted">
          Username
          <input name="username" autoComplete="username" required className="min-h-11 rounded-xl border border-line bg-bg px-3 text-fg" />
        </label>
        <label className="grid gap-1 text-sm text-muted">
          Password
          <input name="password" type="password" autoComplete="current-password" required className="min-h-11 rounded-xl border border-line bg-bg px-3 text-fg" />
        </label>
        <p role="alert" className="min-h-5 text-sm text-bad">
          {error}
        </p>
        <Button type="submit" variant="primary" disabled={busy}>
          {busy ? 'Signing in…' : 'Sign in'}
        </Button>
      </form>
    </main>
  )
}
