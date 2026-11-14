import { fireEvent, screen, waitFor, within } from '@testing-library/react'
import { afterEach, beforeAll, beforeEach, describe, expect, it, vi } from 'vitest'
import { withRouter } from '@/test/withRouter'
import { TelegramPanel } from './TelegramPanel'

const json = (b: unknown, code = 200) => new Response(JSON.stringify(b), { status: code })
type Handler = (url: string, init?: RequestInit) => Response | undefined
const SETTINGS = { configured: true, api_verified: false, api_id: '123456', api_hash_masked: 'ab…yz', connected: false, proxy_settings: { type: 'direct', status: 'direct' } }
const mount = (settings: Record<string, unknown> = SETTINGS, extra: Handler = () => undefined) => {
  const calls: Array<{ url: string; method: string; body: Record<string, unknown> | undefined }> = []
  vi.stubGlobal('fetch', vi.fn(async (url: string, init?: RequestInit) => {
    calls.push({ url, method: init?.method ?? 'GET', body: init?.body ? (JSON.parse(String(init.body)) as Record<string, unknown>) : undefined })
    const x = extra(url, init)
    if (x) return x
    if (url === '/api/settings/telegram' && !init?.method) return json(settings)
    if (url === '/api/telegram/auth-status') return json({ connected: Boolean(settings.connected), status: 'ok' })
    return json({ ok: true })
  }))
  withRouter(<TelegramPanel />)
  return calls
}
const posted = (calls: ReturnType<typeof mount>, url: string) => calls.filter((c) => c.url === url && c.method === 'POST')
const ready = () => waitFor(() => expect(screen.getByLabelText('API ID')).toHaveValue('123456'))
beforeAll(() => {
  HTMLDialogElement.prototype.showModal = function () { this.setAttribute('open', '') }
  HTMLDialogElement.prototype.close = function () { this.removeAttribute('open') }
})
beforeEach(() => localStorage.clear())
afterEach(() => vi.unstubAllGlobals())

describe('API credentials', () => {
  it('loads the saved ID and shows the hash only as a masked placeholder', async () => {
    mount()
    await ready()
    expect(screen.getByLabelText('API Hash')).toHaveValue('')
    expect(screen.getByLabelText('API Hash')).toHaveAttribute('placeholder', 'ab…yz')
    expect(screen.getByText(/API credentials saved/)).toBeInTheDocument()
    expect(screen.getByRole('link', { name: /Login with Telegram/ })).toHaveAttribute('href', 'https://my.telegram.org/apps')
  })
  it('saves exactly what was typed, and clears the hash field afterwards', async () => {
    const calls = mount()
    await ready()
    fireEvent.change(screen.getByLabelText('API Hash'), { target: { value: ' secrethash ' } })
    fireEvent.click(screen.getByRole('button', { name: 'Save credentials' }))
    expect(await screen.findByText('Saved.')).toBeInTheDocument()
    expect(posted(calls, '/api/settings/telegram')[0]!.body).toEqual({ api_id: '123456', api_hash: 'secrethash' })
    expect(screen.getByLabelText('API Hash')).toHaveValue('')
  })
  it('reports the API test result in plain words', async () => {
    mount(SETTINGS, (url) => (url === '/api/telegram/self-test' ? json({ ok: true, authorized: false }) : undefined))
    await ready()
    fireEvent.click(screen.getByRole('button', { name: 'Test API' }))
    expect(await screen.findByText('API reachable; login is not required for this test.')).toBeInTheDocument()
  })
  it('shows the failure reason from the server', async () => {
    mount(SETTINGS, (url) => (url === '/api/telegram/self-test' ? json({ detail: 'API_ID_INVALID' }, 400) : undefined))
    await ready()
    fireEvent.click(screen.getByRole('button', { name: 'Test API' }))
    expect(await screen.findByText('Test failed: API_ID_INVALID')).toBeInTheDocument()
  })
})

describe('Logout and recovery', () => {
  const connected = { ...SETTINGS, connected: true }
  it('offers logout only when connected, and asks before logging out', async () => {
    const calls = mount(connected, (url) => (url === '/api/telegram/logout-account' ? json({ ok: true }) : undefined))
    fireEvent.click(await screen.findByRole('button', { name: 'Log out of Telegram' }))
    expect(posted(calls, '/api/telegram/logout-account')).toHaveLength(0)
    fireEvent.click(screen.getByRole('button', { name: 'Keep session' }))
    expect(posted(calls, '/api/telegram/logout-account')).toHaveLength(0)
    fireEvent.click(screen.getAllByRole('button', { name: 'Log out of Telegram' })[0]!)
    fireEvent.click(within(screen.getByRole('dialog')).getByRole('button', { name: 'Log out of Telegram' }))
    await waitFor(() => expect(posted(calls, '/api/telegram/logout-account')).toHaveLength(1))
    expect(await screen.findByText('Logged out of Telegram. API credentials remain saved.')).toBeInTheDocument()
  })
  it('hides logout and the sign-in card shows nothing to log out when disconnected', async () => {
    mount()
    await ready()
    expect(screen.queryByRole('button', { name: 'Log out of Telegram' })).toBeNull()
  })
  it('archives a rejected session only after confirmation', async () => {
    const calls = mount(SETTINGS, (url) => (url === '/api/telegram/reset-invalid-session' ? json({ ok: true }) : undefined))
    await ready()
    fireEvent.click(screen.getByText('Telegram session recovery'))
    fireEvent.click(screen.getByRole('button', { name: 'Archive saved session and sign in again' }))
    expect(posted(calls, '/api/telegram/reset-invalid-session')).toHaveLength(0)
    fireEvent.click(screen.getByRole('button', { name: 'Archive session' }))
    expect(await screen.findByText('Saved session archived. Sign in again below.')).toBeInTheDocument()
  })
})

describe('Proxy', () => {
  it('shows only the fields the chosen type needs', async () => {
    mount()
    await ready()
    expect(screen.queryByLabelText('Proxy server')).toBeNull()
    fireEvent.change(screen.getByLabelText('Proxy type'), { target: { value: 'socks5' } })
    expect(screen.getByLabelText('Proxy server')).toBeInTheDocument()
    expect(screen.getByLabelText('Proxy password')).toBeInTheDocument()
    expect(screen.queryByLabelText('MTProto secret')).toBeNull()
    fireEvent.change(screen.getByLabelText('Proxy type'), { target: { value: 'mtproto' } })
    expect(screen.getByLabelText('MTProto secret')).toBeInTheDocument()
    expect(screen.queryByLabelText('Proxy password')).toBeNull()
  })
  it('saves the proxy, says a restart is needed, and clears typed secrets', async () => {
    const calls = mount(SETTINGS, (url) => (url === '/api/settings/telegram/proxy' ? json({ ok: true, proxy_settings: { restart_required: true, type: 'socks5' } }) : undefined))
    await ready()
    fireEvent.change(screen.getByLabelText('Proxy type'), { target: { value: 'socks5' } })
    fireEvent.change(screen.getByLabelText('Proxy server'), { target: { value: 'p.example.com' } })
    fireEvent.change(screen.getByLabelText('Proxy port'), { target: { value: '1080' } })
    fireEvent.change(screen.getByLabelText('Proxy password'), { target: { value: 'pw' } })
    fireEvent.click(screen.getByRole('button', { name: 'Save proxy settings' }))
    expect(await screen.findByText('Saved. Restart Archive X to apply.')).toBeInTheDocument()
    expect(posted(calls, '/api/settings/telegram/proxy')[0]!.body).toMatchObject({ type: 'socks5', host: 'p.example.com', port: '1080', password: 'pw', clear_auth: false })
    expect(screen.getByLabelText('Proxy password')).toHaveValue('')
  })
  it('locks the form when the proxy is managed by the environment', async () => {
    mount({ ...SETTINGS, proxy_settings: { type: 'socks5', status: 'configured', source: 'environment', host: 'x', port: 1 } })
    await waitFor(() => expect(screen.getByText('Managed by environment')).toBeInTheDocument())
    expect(screen.getByLabelText('Proxy type')).toBeDisabled()
    expect(screen.getByRole('button', { name: 'Save proxy settings' })).toBeDisabled()
  })
})

describe('Sign in by QR code', () => {
  it('refuses to start without saved credentials', async () => {
    const calls = mount({ configured: false, proxy_settings: {} })
    fireEvent.click(await screen.findByRole('button', { name: 'Generate QR Code' }))
    expect(await screen.findByText('Enter and save your Telegram API ID and API Hash first.')).toBeInTheDocument()
    expect(posted(calls, '/api/telegram/qr-login/start')).toHaveLength(0)
  })
  it('shows the QR image, polls, and finishes when the phone confirms', async () => {
    let polls = 0
    const calls = mount(SETTINGS, (url) => {
      if (url === '/api/telegram/qr-login/start') return json({ status: 'WAITING_FOR_SCAN', qr_image: 'data:image/png;base64,AAAA', message: 'Waiting for scan…' })
      if (url === '/api/telegram/qr-login/status') return json(++polls < 2 ? { status: 'WAITING_FOR_SCAN', qr_image: 'data:image/png;base64,AAAA' } : { status: 'AUTHORIZED', connected: true })
      return undefined
    })
    await ready()
    fireEvent.click(screen.getByRole('button', { name: 'Generate QR Code' }))
    expect(await screen.findByAltText('Telegram login QR code')).toHaveAttribute('src', 'data:image/png;base64,AAAA')
    expect(screen.getByRole('button', { name: 'Refresh QR Code' })).toBeInTheDocument()
    expect(await screen.findByText('Telegram login successful. Session saved locally.', undefined, { timeout: 6000 })).toBeInTheDocument()
    expect(calls.filter((c) => c.url === '/api/telegram/qr-login/status').length).toBeGreaterThanOrEqual(2)
  })
  it('asks for the cloud password when the QR needs it', async () => {
    const calls = mount(SETTINGS, (url) => (url === '/api/telegram/qr-login/start' ? json({ status: 'PASSWORD_REQUIRED' }) : url === '/api/telegram/verify_2fa' ? json({ ok: true }) : undefined))
    await ready()
    fireEvent.click(screen.getByRole('button', { name: 'Generate QR Code' }))
    const field = await screen.findByLabelText('Telegram cloud password')
    fireEvent.change(field, { target: { value: 'hunter2' } })
    fireEvent.click(screen.getByRole('button', { name: 'Verify password' }))
    await waitFor(() => expect(posted(calls, '/api/telegram/verify_2fa')[0]?.body).toEqual({ password: 'hunter2' }))
  })
  it('explains that passkeys are not available', async () => {
    mount()
    await ready()
    fireEvent.click(screen.getByRole('button', { name: 'Log in with Passkey' }))
    expect(screen.getByText(/restricts native passkey login/)).toBeInTheDocument()
  })
})

describe('Sign in by phone', () => {
  const toPhone = async () => { await ready(); fireEvent.click(screen.getByRole('button', { name: 'Phone Number' })) }
  it('asks for a number before sending anything', async () => {
    const calls = mount()
    await toPhone()
    fireEvent.click(screen.getByRole('button', { name: 'Continue' }))
    expect(await screen.findByText('Enter your phone number.')).toBeInTheDocument()
    expect(posted(calls, '/api/telegram/send_code')).toHaveLength(0)
  })
  it('adds the selected country code, searchable, to a national number', async () => {
    const calls = mount(SETTINGS, (url) => (url === '/api/telegram/send_code' ? json({ ok: true }) : undefined))
    await toPhone()
    expect(screen.getByRole('button', { name: 'Country code India +91' })).toBeInTheDocument()
    fireEvent.change(screen.getByLabelText('Phone number'), { target: { value: '98765 43210' } })
    fireEvent.click(screen.getByRole('button', { name: 'Continue' }))
    await waitFor(() => expect(posted(calls, '/api/telegram/send_code')[0]!.body).toEqual({ phone: '+919876543210' }))
    fireEvent.click(screen.getByRole('button', { name: 'Country code India +91' }))
    fireEvent.change(screen.getByLabelText('Search country'), { target: { value: 'germ' } })
    expect(within(screen.getByRole('listbox', { name: 'Country calling codes' })).getAllByRole('option')).toHaveLength(1)
    fireEvent.click(screen.getByRole('option', { name: /Germany/ }))
    expect(screen.getByRole('button', { name: 'Country code Germany +49' })).toBeInTheDocument()
    fireEvent.click(screen.getByRole('button', { name: 'Continue' }))
    await waitFor(() => expect(posted(calls, '/api/telegram/send_code')[1]!.body).toEqual({ phone: '+499876543210' }))
  })
  it('splits a pasted international number onto its own country', async () => {
    mount(); await toPhone()
    fireEvent.change(screen.getByLabelText('Phone number'), { target: { value: '+44 20 7946 0958' } })
    expect(screen.getByRole('button', { name: 'Country code United Kingdom +44' })).toBeInTheDocument()
    expect(screen.getByLabelText('Phone number')).toHaveValue('2079460958')
  })
  it('sends the cleaned number, then the code, and finishes', async () => {
    const calls = mount(SETTINGS, (url) => (url === '/api/telegram/send_code' ? json({ ok: true }) : url === '/api/telegram/verify_code' ? json({ ok: true }) : undefined))
    await toPhone()
    fireEvent.change(screen.getByLabelText('Phone number'), { target: { value: '+1 (415) 555-0123' } })
    fireEvent.click(screen.getByRole('button', { name: 'Continue' }))
    expect(await screen.findByText('Code sent — check Telegram.')).toBeInTheDocument()
    expect(posted(calls, '/api/telegram/send_code')[0]!.body).toEqual({ phone: '+14155550123' })
    fireEvent.change(screen.getByLabelText('Telegram login code'), { target: { value: ' 12345 ' } })
    fireEvent.click(screen.getByRole('button', { name: 'Verify' }))
    expect(await screen.findByText('Telegram login successful. Session saved locally.')).toBeInTheDocument()
    expect(posted(calls, '/api/telegram/verify_code')[0]!.body).toEqual({ phone: '+14155550123', code: '12345' })
  })
  it('goes on to the cloud password when two-step verification is on', async () => {
    mount(SETTINGS, (url) => (url === '/api/telegram/send_code' ? json({ ok: true }) : url === '/api/telegram/verify_code' ? json({ ok: true, two_factor_required: true }) : undefined))
    await toPhone()
    fireEvent.change(screen.getByLabelText('Phone number'), { target: { value: '+14155550123' } })
    fireEvent.click(screen.getByRole('button', { name: 'Continue' }))
    fireEvent.change(await screen.findByLabelText('Telegram login code'), { target: { value: '1' } })
    fireEvent.click(screen.getByRole('button', { name: 'Verify' }))
    expect(await screen.findByLabelText('Telegram cloud password')).toBeInTheDocument()
  })
  it('shows the server reason when the code is wrong', async () => {
    mount(SETTINGS, (url) => (url === '/api/telegram/send_code' ? json({ ok: true }) : url === '/api/telegram/verify_code' ? json({ detail: 'The code is invalid' }, 400) : undefined))
    await toPhone()
    fireEvent.change(screen.getByLabelText('Phone number'), { target: { value: '+14155550123' } })
    fireEvent.click(screen.getByRole('button', { name: 'Continue' }))
    fireEvent.change(await screen.findByLabelText('Telegram login code'), { target: { value: '9' } })
    fireEvent.click(screen.getByRole('button', { name: 'Verify' }))
    expect(await screen.findByText('Error: The code is invalid')).toBeInTheDocument()
  })
})
