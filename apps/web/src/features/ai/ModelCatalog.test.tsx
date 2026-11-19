import { fireEvent, screen, waitFor, within } from '@testing-library/react'
import { afterEach, describe, expect, it, vi } from 'vitest'
import { withRouter } from '@/test/withRouter'
import { ModelCatalog } from './ModelCatalog'
import type { ModelVariant } from './ai'
import { anyBusy, canDelete, modelsSummary, sizeText, stateTone, testLine, variantBadges } from './ai'

afterEach(() => vi.unstubAllGlobals())
const json = (b: unknown, code = 200) => new Response(JSON.stringify(b), { status: code })
const M = (id: string, o = {}) => ({ id, name: `Model ${id}`, detail: 'what it does', size_mb: 39, licence: 'MIT', state: 'ready', installable: false, powers: ['People'], hint: '', ...o })
const LIST = { ok: true, ready: 2, total: 3, models: [M('faces'), M('looks', { state: 'not_downloaded', installable: true, size_mb: 155, hint: 'Downloads about 155 MB once.' }), M('content', { state: 'needs_package', hint: 'Needs the nudenet package.' })] }
type Call = { url: string; method: string }
const mount = (over: (u: string) => Response | undefined = () => undefined) => {
  const calls: Call[] = []
  vi.stubGlobal('fetch', vi.fn(async (url: string, init?: RequestInit) => { calls.push({ url, method: init?.method ?? 'GET' }); return over(url) ?? (url === '/api/ai/models' ? json(LIST) : json({ ok: true, ms: 41, detail: 'Loaded and ran.' })) }))
  withRouter(<ModelCatalog />)
  return calls
}

describe('model catalog helpers', () => {
  it('words states, sizes and test results', () => {
    expect(sizeText(39)).toBe('39 MB'); expect(sizeText(2500)).toBe('2.5 GB')
    expect(modelsSummary({ ready: 2, total: 5 })).toBe('2 of 5 local models ready'); expect(modelsSummary(undefined)).toBe('Checking local models…')
    expect(stateTone('ready')).toBe('good'); expect(stateTone('downloading')).toBe('busy'); expect(stateTone('needs_package')).toBe('warn')
    expect(testLine({ ok: true, ms: 41, detail: 'Loaded.' })).toBe('Working · 41 ms. Loaded.'); expect(testLine({ ok: false, detail: 'Not downloaded.' })).toBe('Not working: Not downloaded.')
    expect(anyBusy({ ...LIST, models: [M('a', { busy: true }) as never] })).toBe(true); expect(anyBusy(LIST as never)).toBe(false)
  })
})

describe('Local AI models', () => {
  it('lists every model with its state, what it powers, size and a hint when it is not ready', async () => {
    mount()
    expect(await screen.findByText('2 of 3 local models ready')).toBeInTheDocument()
    const looks = screen.getByRole('article', { name: 'Model looks' })
    expect(within(looks).getByText('Not downloaded')).toBeInTheDocument(); expect(within(looks).getByText('Downloads about 155 MB once.')).toBeInTheDocument()
    expect(within(screen.getByRole('article', { name: 'Model faces' })).getByText('Ready')).toBeInTheDocument()
    expect(within(screen.getByRole('article', { name: 'Model content' })).getByText('Needs install')).toBeInTheDocument()
  })
  it('Test runs the self-test and says whether it works, with the time', async () => {
    const calls = mount()
    fireEvent.click(within(await screen.findByRole('article', { name: 'Model faces' })).getByRole('button', { name: 'Test' }))
    expect(await screen.findByText('Working · 41 ms. Loaded and ran.')).toBeInTheDocument()
    expect(calls.some((c) => c.url === '/api/ai/models/faces/test' && c.method === 'POST')).toBe(true)
  })
  it('a failing test says why', async () => {
    mount((u) => (u.endsWith('/test') ? json({ ok: false, detail: 'The look model is not downloaded yet.' }) : undefined))
    fireEvent.click(within(await screen.findByRole('article', { name: 'Model looks' })).getByRole('button', { name: 'Test' }))
    expect(await screen.findByText('Not working: The look model is not downloaded yet.')).toBeInTheDocument()
  })
  it('only models that can be downloaded from here offer Download, and it starts the download', async () => {
    const calls = mount()
    expect(within(await screen.findByRole('article', { name: 'Model faces' })).queryByRole('button', { name: 'Download' })).toBeNull()
    fireEvent.click(within(screen.getByRole('article', { name: 'Model looks' })).getByRole('button', { name: 'Download' }))
    await waitFor(() => expect(calls.some((c) => c.url === '/api/ai/models/looks/install' && c.method === 'POST')).toBe(true))
  })
  it('offers Retry when the list cannot be loaded', async () => {
    mount((u) => (u === '/api/ai/models' ? json({ detail: 'x' }, 500) : undefined))
    expect(await screen.findByRole('button', { name: 'Retry' })).toBeInTheDocument()
  })
})

const V = (id: string, o = {}) => ({ id, label: id.toUpperCase(), size_mb: 100, note: `note ${id}`, recommended: false, installed: false, selected: false, ...o })
const SIZE_ROWS = [V('b32', { recommended: true, installed: true, selected: true }), V('b16', { size_mb: 154 }), V('b8', { installed: true })]
const SIZES = { ok: true, ready: 1, total: 1, models: [M('looks', { name: 'Look suggestions', variants: SIZE_ROWS })] }

describe('model sizes', () => {
  it('words the badges and says which sizes may be deleted', () => {
    const [a, b, c] = SIZE_ROWS as [ModelVariant, ModelVariant, ModelVariant]
    expect(variantBadges(a)).toEqual(['In use', 'Recommended']); expect(variantBadges(b)).toEqual([]); expect(variantBadges(c)).toEqual(['Downloaded'])
    expect(canDelete(a, [a, b, c])).toBe(false)                       // in use, and another size is downloaded to switch to
    expect(canDelete(c, [a, b, c])).toBe(true); expect(canDelete(b, [a, b, c])).toBe(false)   // not downloaded: nothing to delete
    expect(canDelete(a, [a, b])).toBe(true)                           // the only one downloaded
  })
  const mountSizes = () => mount((u) => (u === '/api/ai/models' ? json(SIZES) : u.includes('/variants/') ? json({ ok: true, freed_mb: 154 }) : undefined))
  it('lists every size with its note, badges and the right buttons', async () => {
    mountSizes()
    const list = within(await screen.findByRole('list', { name: 'Look suggestions sizes' }))
    expect(list.getAllByRole('listitem')).toHaveLength(3)
    expect(list.getByText('Recommended')).toBeInTheDocument(); expect(list.getByText('In use')).toBeInTheDocument(); expect(list.getByText('note b16')).toBeInTheDocument()
    expect(list.getAllByRole('button', { name: 'Download' })).toHaveLength(1)                 // only the size that is not here
    expect(list.getAllByRole('button', { name: 'Use this' })).toHaveLength(1)                 // only the downloaded size that is not in use
    expect(screen.queryByRole('button', { name: /Delete the b32 size/ })).toBeNull()          // the one in use stays
  })
  it('Download starts that size, Use this switches, and both call the size routes', async () => {
    const calls = mountSizes()
    const list = within(await screen.findByRole('list', { name: 'Look suggestions sizes' }))
    fireEvent.click(list.getByRole('button', { name: 'Download' }))
    await waitFor(() => expect(calls.some((c) => c.url === '/api/ai/models/looks/variants/b16/install' && c.method === 'POST')).toBe(true))
    fireEvent.click(list.getByRole('button', { name: 'Use this' }))
    await waitFor(() => expect(calls.some((c) => c.url === '/api/ai/models/looks/variants/b8/select' && c.method === 'POST')).toBe(true))
    expect(await screen.findByText('Now using the b8 size.')).toBeInTheDocument()
  })
  it('Delete asks once more before anything is removed, and says how much space came back', async () => {
    const calls = mountSizes()
    fireEvent.click(await screen.findByRole('button', { name: 'Delete the b8 size of Look suggestions' }))
    expect(calls.some((c) => c.method === 'DELETE')).toBe(false)
    fireEvent.click(screen.getByRole('button', { name: 'Keep' }))
    expect(calls.some((c) => c.method === 'DELETE')).toBe(false)
    fireEvent.click(screen.getByRole('button', { name: 'Delete the b8 size of Look suggestions' }))
    fireEvent.click(screen.getByRole('button', { name: 'Delete for good' }))
    await waitFor(() => expect(calls.some((c) => c.url === '/api/ai/models/looks/variants/b8' && c.method === 'DELETE')).toBe(true))
    expect(await screen.findByText('Deleted. 154 MB freed.')).toBeInTheDocument()
  })
  it('a size that is downloading shows its percentage and progress bar and nothing else to press', async () => {
    const busy = { ...SIZES, models: [{ ...SIZES.models[0], state: 'downloading', busy: true, variants: [V('b32', { recommended: true, installed: true, selected: true }), V('b16', { busy: true, progress: 42 })] }] }
    mount((u) => (u === '/api/ai/models' ? json(busy) : undefined))
    expect(await screen.findByText('Downloading 42%')).toBeInTheDocument()
    expect(screen.getByRole('progressbar')).toHaveAttribute('aria-valuenow', '42')
    expect(screen.queryByRole('button', { name: 'Download' })).toBeNull()
  })
  it('shows why a switch or delete was refused', async () => {
    mount((u) => (u === '/api/ai/models' ? json(SIZES) : u.includes('/select') ? json({ detail: 'Download it first.' }, 409) : undefined))
    fireEvent.click(await screen.findByRole('button', { name: 'Use this' }))
    expect(await screen.findByText('Download it first.')).toBeInTheDocument()
  })
})
