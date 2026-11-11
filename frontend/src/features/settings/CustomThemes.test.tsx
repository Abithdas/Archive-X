import { fireEvent, screen, waitFor, within } from '@testing-library/react'
import { afterEach, beforeAll, beforeEach, describe, expect, it, vi } from 'vitest'
import { withRouter } from '@/test/withRouter'
import { ACTIVE_KEY, DEFAULT_PALETTE, STORE_KEY, createTheme, deriveVariant } from '@/theme/custom'
import { applyTheme, storedTheme } from '@/theme/themes'
import { AppearancePanel } from './panels'

vi.mock('sonner', () => ({ toast: Object.assign(vi.fn(), { error: vi.fn() }), Toaster: () => null }))
beforeAll(() => {
  HTMLDialogElement.prototype.showModal = function () { this.setAttribute('open', '') }
  HTMLDialogElement.prototype.close = function () { this.removeAttribute('open') }
})
const posts: Array<Record<string, unknown>> = []
beforeEach(() => {
  localStorage.clear(); posts.length = 0
  applyTheme('nord', 'dark')
  vi.stubGlobal('fetch', vi.fn(async (url: string, init?: RequestInit) => {
    if (init?.method === 'POST' && url === '/api/settings/experience') posts.push(JSON.parse(String(init.body)))
    return new Response('{"ok":true}', { status: 200 })
  }))
})
afterEach(() => vi.unstubAllGlobals())
const accent = () => document.documentElement.style.getPropertyValue('--color-accent')
const saved = () => JSON.parse(localStorage.getItem(STORE_KEY) ?? '[]') as Array<{ id: string; name: string; palettes: { dark: Record<string, string>; light: Record<string, string> } }>
const seed = (name = 'Ocean') => { const t = { ...createTheme({ ...DEFAULT_PALETTE, primary: '#112233' }, 'dark', 'ax-custom-seed1'), name }; localStorage.setItem(STORE_KEY, JSON.stringify([t])); return t }
const mount = () => withRouter(<AppearancePanel />)

describe('Custom themes', () => {
  it('starts empty and opens a draft from the colours on screen, previewing edits without saving', async () => {
    mount()
    expect(await screen.findByText('No custom themes yet. Create one to customize the entire Archive X interface.')).toBeInTheDocument()
    fireEvent.click(screen.getByRole('button', { name: /Create Theme/ }))
    const ed = await screen.findByRole('region', { name: 'Create Theme' })
    expect(within(ed).getByLabelText('Primary color')).toHaveValue('#5fd3e0')
    fireEvent.change(within(ed).getByLabelText('Primary color'), { target: { value: '#ff0000' } })
    expect(accent()).toBe('#ff0000')
    expect(localStorage.getItem(STORE_KEY)).toBeNull()
  })
  it('flags an invalid colour and will not save until it is fixed', async () => {
    mount(); fireEvent.click(await screen.findByRole('button', { name: /Create Theme/ }))
    const ed = await screen.findByRole('region', { name: 'Create Theme' })
    const bg = within(ed).getByLabelText('Background color')
    fireEvent.change(bg, { target: { value: 'not a colour' } })
    expect(bg).toHaveAttribute('aria-invalid', 'true'); expect(within(ed).getByText('Enter a valid CSS color for Background.')).toBeInTheDocument()
    expect(within(ed).getByRole('button', { name: 'Save and Apply' })).toBeDisabled()
    fireEvent.change(bg, { target: { value: '#101010' } })
    expect(within(ed).getByRole('button', { name: 'Save and Apply' })).toBeEnabled()
  })
  it('saves, applies, remembers and syncs the custom theme', async () => {
    mount(); fireEvent.click(await screen.findByRole('button', { name: /Create Theme/ }))
    const ed = await screen.findByRole('region', { name: 'Create Theme' })
    fireEvent.change(within(ed).getByLabelText('Name'), { target: { value: 'Sunset' } })
    fireEvent.change(within(ed).getByLabelText('Primary color'), { target: { value: '#ff8800' } })
    fireEvent.click(within(ed).getByRole('button', { name: 'Save and Apply' }))
    await waitFor(() => expect(saved()).toHaveLength(1))
    expect(saved()[0]!.name).toBe('Sunset'); expect(saved()[0]!.palettes.dark.primary).toBe('#ff8800'); expect(saved()[0]!.palettes.light.text).toBe('#17221c')
    expect(localStorage.getItem(ACTIVE_KEY)).toBe(saved()[0]!.id); expect(storedTheme().preset).toBe('custom'); expect(accent()).toBe('#ff8800')
    await waitFor(() => expect(posts).toContainEqual({ theme_preset: 'custom', theme_mode: 'dark' }))
    expect(screen.getByText('1 saved')).toBeInTheDocument(); expect(screen.getByRole('button', { name: 'Sunset' })).toHaveAttribute('aria-pressed', 'true')
    expect(screen.getByRole('region', { name: 'Edit Theme' })).toBeInTheDocument()
  })
  it('cancel puts the previous theme back and saves nothing', async () => {
    mount(); fireEvent.click(await screen.findByRole('button', { name: /Create Theme/ }))
    const ed = await screen.findByRole('region', { name: 'Create Theme' })
    fireEvent.change(within(ed).getByLabelText('Primary color'), { target: { value: '#ff0000' } })
    fireEvent.click(within(ed).getByRole('button', { name: 'Cancel' }))
    expect(accent()).toBe('#5fd3e0'); expect(localStorage.getItem(STORE_KEY)).toBeNull(); expect(screen.queryByRole('region', { name: 'Create Theme' })).not.toBeInTheDocument()
  })
  it('edits the light variant separately', async () => {
    seed(); mount()
    fireEvent.click(await screen.findByRole('button', { name: 'Ocean' }))
    const ed = await screen.findByRole('region', { name: 'Edit Theme' })
    fireEvent.click(within(ed).getByRole('button', { name: 'Light' }))
    expect(document.documentElement.dataset.theme).toBe('light')
    expect(within(ed).getByLabelText('Text color')).toHaveValue('#17221c')
    fireEvent.change(within(ed).getByLabelText('Background color'), { target: { value: '#fafafa' } })
    fireEvent.click(within(ed).getByRole('button', { name: 'Save and Apply' }))
    await waitFor(() => expect(saved()[0]!.palettes.light.bg).toBe('#fafafa'))
    expect(saved()[0]!.palettes.dark.primary).toBe('#112233')
  })
  it('selecting a saved theme applies it; picking a built-in theme leaves custom mode', async () => {
    seed(); mount()
    fireEvent.click(await screen.findByRole('button', { name: 'Ocean' }))
    expect(accent()).toBe('#112233'); expect(localStorage.getItem(ACTIVE_KEY)).toBe('ax-custom-seed1')
    fireEvent.click(screen.getByRole('button', { name: 'Nero Gold' }))
    await waitFor(() => expect(localStorage.getItem(ACTIVE_KEY)).toBeNull())
    expect(storedTheme().preset).toBe('nero-gold'); expect(accent()).not.toBe('#112233'); expect(saved()).toHaveLength(1)
  })
  it('deletes only after confirmation, falling back to Nord when it was in use', async () => {
    seed(); mount()
    fireEvent.click(await screen.findByRole('button', { name: 'Ocean' }))
    fireEvent.click(screen.getByRole('button', { name: 'Delete Ocean' }))
    const ask = await screen.findByRole('dialog', { name: 'Delete theme?' })
    fireEvent.click(within(ask).getByRole('button', { name: 'Cancel' })); expect(saved()).toHaveLength(1)
    fireEvent.click(screen.getByRole('button', { name: 'Delete Ocean' }))
    fireEvent.click(within(await screen.findByRole('dialog', { name: 'Delete theme?' })).getByRole('button', { name: 'Delete theme' }))
    await waitFor(() => expect(saved()).toHaveLength(0))
    expect(localStorage.getItem(ACTIVE_KEY)).toBeNull(); expect(storedTheme().preset).toBe('nord'); expect(accent()).toBe('#5fd3e0')
    await waitFor(() => expect(posts).toContainEqual({ theme_preset: 'nord', theme_mode: 'dark' }))
  })
})

describe('stored custom theme at startup', () => {
  it('is restored, and an unknown one falls back to Nord', () => {
    const t = seed(); localStorage.setItem(ACTIVE_KEY, t.id); localStorage.setItem('ax_theme_preset', 'custom'); localStorage.setItem('ax_theme_mode', 'light')
    const s = storedTheme(); expect(s).toEqual({ preset: 'custom', mode: 'light' })
    applyTheme(s.preset, s.mode); expect(accent()).toBe(deriveVariant({ ...DEFAULT_PALETTE, primary: '#112233' }, 'light').primary)
    localStorage.setItem(ACTIVE_KEY, 'ax-custom-gone'); expect(storedTheme().preset).toBe('nord')
  })
})
