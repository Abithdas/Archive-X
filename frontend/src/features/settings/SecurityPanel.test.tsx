import { fireEvent, screen, waitFor } from '@testing-library/react'
import { afterEach, describe, expect, it, vi } from 'vitest'
import { withRouter } from '@/test/withRouter'
import { SecurityPanel } from './SecurityPanel'

vi.mock('sonner', () => ({ toast: Object.assign(vi.fn(), { error: vi.fn() }), Toaster: () => null }))
const json = (b: unknown, code = 200) => new Response(JSON.stringify(b), { status: code })
type Handler = (url: string, init?: RequestInit) => Response | undefined
const mount = (login: Record<string, unknown> = { username: 'admin', default_login: false }, extra: Handler = () => undefined, desktop = false) => {
  const calls: Array<{ url: string; method: string; body: Record<string, unknown> | undefined }> = []
  vi.stubGlobal('fetch', vi.fn(async (url: string, init?: RequestInit) => {
    calls.push({ url, method: init?.method ?? 'GET', body: init?.body ? (JSON.parse(String(init.body)) as Record<string, unknown>) : undefined })
    const x = extra(url, init)
    if (x) return x
    if (url === '/api/settings/weblogin' && !init?.method) return json(login)
    if (url === '/desktop/info') return desktop ? json({ desktop: true }) : json({ detail: 'x' }, 404)
    return json({ ok: true })
  }))
  withRouter(<SecurityPanel />)
  return calls
}
const posts = (c: ReturnType<typeof mount>) => c.filter((x) => x.method === 'POST')
const ready = () => waitFor(() => expect(screen.getByLabelText('Username')).toHaveValue('admin'))
afterEach(() => vi.unstubAllGlobals())

describe('Dashboard authentication', () => {
  it('loads the username and warns while the default login is in use', async () => {
    mount({ username: 'admin', default_login: true })
    await ready()
    expect(await screen.findByRole('alert')).toHaveTextContent('default login')
  })
  it('sends nothing without the current password', async () => {
    const calls = mount()
    await ready()
    fireEvent.click(screen.getByRole('button', { name: 'Save' }))
    expect(await screen.findByText('Enter your current password to change login details.')).toBeInTheDocument()
    expect(posts(calls)).toHaveLength(0)
  })
  it('checks that the new passwords match and are long enough before sending', async () => {
    const calls = mount()
    await ready()
    fireEvent.change(screen.getByLabelText('Current password'), { target: { value: 'old' } })
    fireEvent.change(screen.getByLabelText('New password'), { target: { value: 'a-long-new-password' } })
    fireEvent.change(screen.getByLabelText('Repeat new password'), { target: { value: 'other' } })
    fireEvent.click(screen.getByRole('button', { name: 'Save' }))
    expect(await screen.findByText('The two passwords do not match.')).toBeInTheDocument()
    fireEvent.change(screen.getByLabelText('New password'), { target: { value: 'short' } })
    fireEvent.change(screen.getByLabelText('Repeat new password'), { target: { value: 'short' } })
    fireEvent.click(screen.getByRole('button', { name: 'Save' }))
    expect(await screen.findByText('Use at least 12 characters.')).toBeInTheDocument()
    expect(posts(calls)).toHaveLength(0)
  })
  it('changes the password, says other devices were signed out, and clears the fields', async () => {
    const calls = mount()
    await ready()
    fireEvent.change(screen.getByLabelText('Current password'), { target: { value: 'old-password' } })
    fireEvent.change(screen.getByLabelText('New password'), { target: { value: 'a-long-new-password' } })
    fireEvent.change(screen.getByLabelText('Repeat new password'), { target: { value: 'a-long-new-password' } })
    fireEvent.click(screen.getByRole('button', { name: 'Save' }))
    expect(await screen.findByText('Saved. Other devices were signed out.')).toBeInTheDocument()
    expect(posts(calls)[0]!.body).toEqual({ username: 'admin', password: 'a-long-new-password', current_password: 'old-password' })
    expect(screen.getByLabelText('Current password')).toHaveValue('')
    expect(screen.getByLabelText('New password')).toHaveValue('')
  })
  it('can change just the username, keeping the password (blank = keep)', async () => {
    const calls = mount()
    await ready()
    fireEvent.change(screen.getByLabelText('Username'), { target: { value: ' sam ' } })
    fireEvent.change(screen.getByLabelText('Current password'), { target: { value: 'old-password' } })
    fireEvent.click(screen.getByRole('button', { name: 'Save' }))
    expect(await screen.findByText('Saved.')).toBeInTheDocument()
    expect(posts(calls)[0]!.body).toEqual({ username: 'sam', password: '', current_password: 'old-password' })
  })
  it('shows the server reason when the current password is wrong', async () => {
    mount({ username: 'admin' }, (url, init) => (url === '/api/settings/weblogin' && init?.method === 'POST' ? json({ detail: 'Current password is incorrect' }, 403) : undefined))
    await ready()
    fireEvent.change(screen.getByLabelText('Current password'), { target: { value: 'nope' } })
    fireEvent.click(screen.getByRole('button', { name: 'Save' }))
    expect(await screen.findByText('Error: Current password is incorrect')).toBeInTheDocument()
  })
  it('explains the generated password inside the desktop app', async () => {
    mount({ username: 'admin' }, () => undefined, true)
    expect(await screen.findByRole('note')).toHaveTextContent('generated for this computer')
  })
})
