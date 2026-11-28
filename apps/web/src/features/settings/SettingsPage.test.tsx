import { fireEvent, screen, waitFor, within } from '@testing-library/react'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { toast } from 'sonner'
import { withRouter } from '@/test/withRouter'
import { SettingsPage } from './SettingsPage'

vi.mock('sonner', () => ({ toast: Object.assign(vi.fn(), { error: vi.fn() }), Toaster: () => null }))
const json = (b: unknown, code = 200) => new Response(JSON.stringify(b), { status: code })
type Handler = (url: string, init?: RequestInit) => Response | undefined
const mount = (extra: Handler = () => undefined) => {
  const calls: Array<{ url: string; method: string; body: Record<string, unknown> | undefined }> = []
  vi.stubGlobal('fetch', vi.fn(async (url: string, init?: RequestInit) => {
    calls.push({ url, method: init?.method ?? 'GET', body: init?.body ? (JSON.parse(String(init.body)) as Record<string, unknown>) : undefined })
    const x = extra(url, init)
    if (x) return x
    if (url === '/api/screen/status') return json({ progress: { running: false }, titles: 0, waiting: 0 })
    if (url === '/api/content-mode') return json({ ok: true, mode: 'sfw', pin_set: false, locked_for: 0 })
    if (url === '/api/telegram/auth-status') return json({ connected: false, status: 'CONFIG REQUIRED' })
    if (url === '/api/media-manager/preflight') return json({ telethon: { ok: true }, av: { ok: true }, pillow: { ok: true }, ffmpeg: { ok: true }, ffprobe: { ok: true } })
    if (url === '/api/icons/status') return json({ icons: { VLC: '/x' } })
    if (url === '/api/settings/weblogin') return json({ default_login: false })
    if (url === '/api/settings/metadata') return json({})
    if (url === '/api/settings/storage') return json({ files: 5, bytes: 1024, disk: { total: 100, free: 50, used: 50 } })
    if (url === '/api/settings/experience') return json({ autoplay: true, autonext: false, autonext_mode: 'studio', theme_preset: 'nord', theme_mode: 'dark', ui_scale: 100 })
    return json({})
  }))
  withRouter(<SettingsPage />)
  return calls
}
beforeEach(() => { localStorage.clear(); document.documentElement.removeAttribute('style') })
afterEach(() => vi.unstubAllGlobals())

describe('Settings', () => {
  it('lists the eleven sections and opens on Overview with the live checks', async () => {
    mount()
    const nav = await screen.findByRole('navigation', { name: 'Settings sections' })
    await within(nav).findByRole('button', { name: 'Addons' })   // the Addons section appears once the mode is known to be SFW
    expect(within(nav).getAllByRole('button').slice(0, 10).map((b) => b.textContent)).toEqual(['Overview', 'Telegram', 'Local files', 'Metadata', 'Addons', 'Experience', 'Appearance', 'Storage & Backup', 'Security', 'System & network'])
    const status = await screen.findByRole('region', { name: 'Status' })
    await waitFor(() => expect(within(status).getByText('All found')).toBeInTheDocument())
    expect(within(status).getByText('not connected — CONFIG REQUIRED')).toBeInTheDocument()
    expect(within(status).getByText('1 KB · 5 files')).toBeInTheDocument()
    expect(screen.getByText('Telegram · Not connected')).toBeInTheDocument()
  })
  it('has no Addons section in NSFW mode, and a remembered Addons section falls back to Overview', async () => {
    localStorage.setItem('ax_next_settings_panel', 'addons')
    mount((url) => (url === '/api/content-mode' ? json({ ok: true, mode: 'nsfw', pin_set: false, locked_for: 0 }) : undefined))
    const nav = await screen.findByRole('navigation', { name: 'Settings sections' })
    await waitFor(() => expect(within(nav).getAllByRole('button').map((b) => b.textContent)).not.toContain('Addons'))
    expect(within(nav).getByRole('button', { name: 'Overview' })).toHaveAttribute('aria-current', 'page')
    expect(screen.queryByRole('region', { name: 'Built in' })).not.toBeInTheDocument()
  })
  it("has a switch for matching each title's colours in Appearance: on by default, kept on this computer", async () => {
    mount()
    fireEvent.click(await screen.findByRole('button', { name: 'Appearance' }))
    const sw = await screen.findByRole('switch', { name: "Match each title's colours" })
    expect(sw).toBeChecked()
    fireEvent.click(sw)
    expect(localStorage.getItem('ax_next_art_colours')).toBe('off')
    await waitFor(() => expect(screen.getByRole('switch', { name: "Match each title's colours" })).not.toBeChecked())
    fireEvent.click(screen.getByRole('switch', { name: "Match each title's colours" }))
    expect(localStorage.getItem('ax_next_art_colours')).toBe('on')
  })
  it('Overview ends with an About note that says it is a personal project built with Claude Code', async () => {
    mount()
    const about = await screen.findByRole('region', { name: 'About' })
    expect(within(about).getByText(/personal project/)).toBeInTheDocument()
    expect(within(about).getByRole('link', { name: 'Claude Code' })).toHaveAttribute('href', 'https://www.anthropic.com/claude-code')
    expect(within(about).getByText(/isn't affiliated with Telegram, Anthropic/)).toBeInTheDocument()
  })
  it('remembers the selected section', async () => {
    mount()
    fireEvent.click(await screen.findByRole('button', { name: 'Experience' }))
    expect(localStorage.getItem('ax_next_settings_panel')).toBe('experience')
  })
  it('every section renders its own panel (none falls back to the classic page)', async () => {
    mount()
    for (const name of ['Telegram', 'Metadata', 'Experience', 'Appearance', 'Storage & Backup', 'Security', 'System & network']) {
      fireEvent.click(await screen.findByRole('button', { name }))
      await waitFor(() => expect(screen.queryByText(/has not been migrated yet/)).not.toBeInTheDocument())
    }
    expect(await screen.findByRole('region', { name: 'Browser compatibility' })).toBeInTheDocument()
  })
  it('Validate configuration reports all four checks in one message', async () => {
    mount((url) => (url === '/api/settings/telegram' ? json({ configured: false }) : undefined))
    fireEvent.click(await screen.findByRole('button', { name: 'Validate configuration' }))
    await waitFor(() => expect(toast).toHaveBeenCalled())
    expect(vi.mocked(toast).mock.calls.at(-1)?.[0]).toBe('Telegram API credentials are incomplete or unverified. No metadata provider key is configured. Web login is customized. Media dependencies are ready.')
  })
})

describe('Experience', () => {
  it('shows the saved toggles and saves exactly what is shown', async () => {
    const calls = mount()
    fireEvent.click(await screen.findByRole('button', { name: 'Experience' }))
    await waitFor(() => expect(screen.getByRole('switch', { name: 'Autoplay' })).toBeChecked())
    expect(screen.getByRole('switch', { name: 'Autoplay next' })).not.toBeChecked()
    expect(screen.getByRole('switch', { name: 'Ask to resume playback' })).toBeChecked()
    expect(screen.getByLabelText('Autoplay source')).toHaveValue('studio')
    fireEvent.click(screen.getByRole('switch', { name: 'Autoplay next' }))
    fireEvent.click(screen.getByRole('button', { name: 'Save experience settings' }))
    await waitFor(() => expect(calls.some((c) => c.method === 'POST' && c.url === '/api/settings/experience')).toBe(true))
    expect(calls.find((c) => c.method === 'POST')!.body).toEqual({ autoplay: true, autonext: true, resume_prompt: true, live_seek_preview: true, hover_trailers: true, partial_cache_prompt: false, autonext_mode: 'studio' })
  })
})

describe('Appearance', () => {
  it('applies a theme at once, remembers it, and saves it to the server', async () => {
    const calls = mount()
    fireEvent.click(await screen.findByRole('button', { name: 'Appearance' }))
    fireEvent.click(await screen.findByRole('button', { name: 'Nero Gold' }))
    expect(document.documentElement.style.getPropertyValue('--color-accent')).toBe('#efd395')
    expect(localStorage.getItem('ax_theme_preset')).toBe('nero-gold')
    await waitFor(() => expect(calls.some((c) => c.method === 'POST' && c.body?.theme_preset === 'nero-gold')).toBe(true))
    expect(await screen.findByText('Theme: Nero Gold. Saved.')).toBeInTheDocument()
    expect(screen.getByRole('button', { name: 'Nero Gold' })).toHaveAttribute('aria-pressed', 'true')
  })
  it('switches between dark and light variants of the same theme', async () => {
    mount()
    fireEvent.click(await screen.findByRole('button', { name: 'Appearance' }))
    fireEvent.click(await screen.findByRole('button', { name: /^Dark theme\. Activate to switch to light/ }))   // the sun and moon switch, as it was in the profile menu
    expect(document.documentElement.dataset.theme).toBe('light')
    expect(document.documentElement.style.getPropertyValue('--color-bg')).toBe('#eceff4')
  })
  it('zoom presets and the slider change the interface scale', async () => {
    const calls = mount()
    fireEvent.click(await screen.findByRole('button', { name: 'Appearance' }))
    fireEvent.click(await screen.findByRole('button', { name: /Spacious/ }))
    expect(document.documentElement.dataset.uiScale).toBe('130')
    await waitFor(() => expect(calls.some((c) => c.method === 'POST' && c.body?.ui_scale === 130 && c.body?.grid_density === 'spacious')).toBe(true))
    expect(screen.getByRole('slider', { name: 'Interface scale' })).toHaveValue('130')
    fireEvent.change(screen.getByRole('slider', { name: 'Interface scale' }), { target: { value: '85' } })
    expect(document.documentElement.dataset.uiScale).toBe('85')
  })
})
