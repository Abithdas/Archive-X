import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { act, fireEvent, render, screen, waitFor } from '@testing-library/react'
import { afterEach, describe, expect, it, vi } from 'vitest'
import { LoginGate, OPEN_ANYWAY_AFTER_MS, SLOW_AFTER_MS } from './LoginGate'

afterEach(() => vi.unstubAllGlobals())
const json = (b: unknown, code = 200) => new Response(JSON.stringify(b), { status: code })
type Call = { url: string; method: string; body?: Record<string, unknown> }
const mount = (hint: unknown, over: (u: string, i?: RequestInit) => Response | undefined = () => undefined) => {
  const calls: Call[] = []
  let signedIn = false
  vi.stubGlobal('fetch', vi.fn(async (url: string, init?: RequestInit) => {
    calls.push({ url, method: init?.method ?? 'GET', body: init?.body ? JSON.parse(String(init.body)) : undefined })
    const o = over(url, init); if (o) { if (url === '/login' && o.ok) signedIn = true; return o }
    if (url === '/api/status') return signedIn ? json({ connected: false }) : json({ detail: 'Not signed in' }, 401)
    if (url === '/api/login-hint') return json(hint)
    if (url === '/login') { signedIn = true; return json({ ok: true }) }
    return json({ ok: true })
  }))
  render(<QueryClientProvider client={new QueryClient({ defaultOptions: { queries: { retry: false } } })}><LoginGate><p>The app</p></LoginGate></QueryClientProvider>)
  return calls
}
const fill = (label: string, value: string) => fireEvent.change(screen.getByLabelText(label), { target: { value } })

describe('LoginGate', () => {
  it('with the login switched off, this PC opens a session by itself and the form never shows', async () => {
    let opened = false
    const calls = mount({ setup_required: false, login_required: false }, (u) => {
      if (u === '/api/auth/open') { opened = true; return json({ ok: true }) }
      if (u === '/api/status') return opened ? json({ connected: false }) : json({ detail: 'Not signed in' }, 401)
      return undefined
    })
    expect(await screen.findByText('The app')).toBeInTheDocument()
    expect(calls.some((c) => c.url === '/api/auth/open' && c.method === 'POST')).toBe(true)
    expect(screen.queryByText('Sign in to continue')).toBeNull()
  })
  it('signs in and shows the app', async () => {
    const calls = mount({ setup_required: false })
    expect(await screen.findByText('Sign in to continue')).toBeInTheDocument()
    fill('Username', ' admin '); fill('Password', 'pw')
    fireEvent.click(screen.getByRole('button', { name: 'Sign in' }))
    expect(await screen.findByText('The app')).toBeInTheDocument()
    expect(calls.find((c) => c.url === '/login')!.body).toEqual({ username: 'admin', password: 'pw' })
  })
  it('says why a sign-in failed', async () => {
    mount({ setup_required: false }, (u) => (u === '/login' ? json({ detail: 'Wrong' }, 401) : undefined))
    const go = await screen.findByRole('button', { name: 'Sign in' }); fill('Username', 'a'); fill('Password', 'b'); fireEvent.click(go)
    expect(await screen.findByText('Wrong username or password.')).toBeInTheDocument()
  })
  it('shakes and rings the password field on a wrong password, and calms down when you type again', async () => {
    mount({ setup_required: false }, (u) => (u === '/login' ? json({ detail: 'Wrong' }, 401) : undefined))
    const go = await screen.findByRole('button', { name: 'Sign in' }); fill('Username', 'a'); fill('Password', 'b'); fireEvent.click(go)
    await screen.findByText('Wrong username or password.')
    const field = screen.getByLabelText('Password').closest('.mo-field') as HTMLElement
    expect(field.classList.contains('is-error')).toBe(true)
    await waitFor(() => expect(field.classList.contains('is-shaking')).toBe(true))
    fireEvent.input(screen.getByLabelText('Password'), { target: { value: 'bc' } })
    await waitFor(() => expect(field.classList.contains('is-error')).toBe(false))
  })
  it('shows the server\'s wait message after too many attempts', async () => {
    mount({ setup_required: false }, (u) => (u === '/login' ? json({ detail: 'Try again in 4 minutes' }, 429) : undefined))
    const go = await screen.findByRole('button', { name: 'Sign in' }); fill('Username', 'a'); fill('Password', 'b'); fireEvent.click(go)
    expect(await screen.findByText('Try again in 4 minutes')).toBeInTheDocument()
  })
  it('offers first-run setup when it is required, checks the form, creates the administrator, then asks to sign in', async () => {
    const calls = mount({ setup_required: true })
    expect(await screen.findByText('Create your administrator account')).toBeInTheDocument()
    expect(screen.getByLabelText('Username')).toHaveValue('admin'); expect(screen.queryByLabelText('Password')).not.toBeInTheDocument()
    fill('New password', 'short'); fill('Confirm password', 'short')
    fireEvent.click(screen.getByRole('button', { name: 'Create administrator' }))
    expect(await screen.findByText('Password must be at least 12 characters')).toBeInTheDocument()
    fill('New password', 'a-long-password'); fill('Confirm password', 'a-long-passwore')
    fireEvent.click(screen.getByRole('button', { name: 'Create administrator' }))
    expect(await screen.findByText('Passwords do not match')).toBeInTheDocument(); expect(calls.some((c) => c.url === '/api/auth/setup')).toBe(false)
    fill('Confirm password', 'a-long-password')
    fireEvent.click(screen.getByRole('button', { name: 'Create administrator' }))
    await waitFor(() => expect(calls.find((c) => c.url === '/api/auth/setup')!.body).toEqual({ username: 'admin', password: 'a-long-password', confirmation: 'a-long-password' }))
    expect(await screen.findByText('Administrator created. Sign in with your new credentials.')).toBeInTheDocument()
  })
  it('shows the server\'s reason when setup is refused (for example from another device)', async () => {
    mount({ setup_required: true }, (u) => (u === '/api/auth/setup' ? json({ detail: 'Finish first-run setup on the PC running Archive X' }, 403) : undefined))
    await screen.findByText('Create your administrator account')
    fill('New password', 'a-long-password'); fill('Confirm password', 'a-long-password')
    fireEvent.click(screen.getByRole('button', { name: 'Create administrator' }))
    expect(await screen.findByText('Finish first-run setup on the PC running Archive X')).toBeInTheDocument()
  })
  it('switches to setup when a sign-in answers that setup is required', async () => {
    mount({ setup_required: false }, (u) => (u === '/login' ? json({ detail: 'Administrator credential setup is required' }, 428) : undefined))
    const go = await screen.findByRole('button', { name: 'Sign in' }); fill('Username', 'a'); fill('Password', 'b'); fireEvent.click(go)
    expect(await screen.findByText('Create your administrator account')).toBeInTheDocument()
  })
})

describe('LoginGate while the status is slow', () => {
  const slowStatus = () => {
    vi.useFakeTimers()
    vi.stubGlobal('fetch', vi.fn((url: string) => (url === '/api/status' ? new Promise<Response>(() => undefined) : Promise.resolve(json({ ok: true })))))
    render(<QueryClientProvider client={new QueryClient({ defaultOptions: { queries: { retry: false } } })}><LoginGate><p>The app</p></LoginGate></QueryClientProvider>)
  }
  afterEach(() => vi.useRealTimers())
  it('says it is still starting after a few seconds, and opens the app after a few more instead of waiting for ever', async () => {
    slowStatus()
    expect(screen.queryByText(/Still starting/)).toBeNull()
    await act(async () => { await vi.advanceTimersByTimeAsync(SLOW_AFTER_MS + 100) })
    expect(screen.getByText(/Still starting/)).toBeInTheDocument()
    expect(screen.queryByText('The app')).toBeNull()
    await act(async () => { await vi.advanceTimersByTimeAsync(OPEN_ANYWAY_AFTER_MS - SLOW_AFTER_MS) })
    expect(screen.getByText('The app')).toBeInTheDocument()
  })
})
