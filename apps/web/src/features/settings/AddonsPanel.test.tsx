import { fireEvent, screen, waitFor, within } from '@testing-library/react'
import { afterEach, beforeAll, describe, expect, it, vi } from 'vitest'
import { withRouter } from '@/test/withRouter'
import { AddonsPanel, type AddonsView } from './AddonsPanel'

beforeAll(() => { HTMLDialogElement.prototype.showModal = function () { this.setAttribute('open', '') }; HTMLDialogElement.prototype.close = function () { this.removeAttribute('open') } })
afterEach(() => vi.unstubAllGlobals())
const json = (b: unknown, code = 200) => new Response(JSON.stringify(b), { status: code, headers: { 'content-type': 'application/json' } })
const BUILTIN = [{ id: 'cinemeta', name: 'Cinemeta', what: 'Popular' }, { id: 'streaming', name: 'Streaming Catalogs', what: 'Netflix' }, { id: 'anime', name: 'Anime Catalogs', what: 'MyAnimeList' }]
const mine: AddonsView['items'][number] = { id: 'abcdef0123', url: 'https://good.example/cfg', name: 'My Catalogs', description: 'Test lists.', version: '1.2.0', lists: [{ type: 'movie', id: 'top', name: 'Top films' }, { type: 'series', id: 'all', name: 'All shows' }] }

describe('Settings, Addons', () => {
  it('lists what is built in and what was added, adds an add-on by its address, says why one is refused, and removes one after asking', async () => {
    let view: AddonsView = { builtin: BUILTIN, items: [] }
    const calls: Array<{ url: string; method: string; body?: string }> = []
    vi.stubGlobal('fetch', vi.fn(async (url: string, init?: RequestInit) => {
      calls.push({ url, method: init?.method ?? 'GET', body: init?.body as string | undefined })
      if (url === '/api/screen/addons' && init?.method === 'POST') {
        const asked = JSON.parse(String(init.body)).url as string
        if (asked.startsWith('http:')) return json({ detail: 'The address must start with https:// (or stremio://).' }, 400)
        view = { builtin: BUILTIN, items: [mine] }
        return json(view)
      }
      if (url.startsWith('/api/screen/addons/') && init?.method === 'DELETE') { view = { builtin: BUILTIN, items: [] }; return json(view) }
      if (url === '/api/screen/addons') return json(view)
      return json({})
    }))
    withRouter(<AddonsPanel />)
    const built = await screen.findByRole('region', { name: 'Built in' })
    for (const n of ['Cinemeta', 'Streaming Catalogs', 'Anime Catalogs']) expect(await within(built).findByText(n)).toBeInTheDocument()
    expect(screen.getByText('None added yet.')).toBeInTheDocument()

    fireEvent.change(screen.getByLabelText('Add-on address'), { target: { value: 'http://good.example/manifest.json' } })
    fireEvent.click(screen.getByRole('button', { name: 'Add' }))
    expect(await screen.findByText('The address must start with https:// (or stremio://).')).toBeInTheDocument()   // the reason is the server's own

    fireEvent.change(screen.getByLabelText('Add-on address'), { target: { value: 'stremio://good.example/cfg/manifest.json' } })
    fireEvent.click(screen.getByRole('button', { name: 'Add' }))
    const lists = await screen.findByRole('list', { name: 'Lists of My Catalogs' })
    expect(within(lists).getByText(/Top films/)).toBeInTheDocument()
    expect(within(lists).getByText(/All shows/)).toBeInTheDocument()
    expect(screen.getByLabelText('Add-on address')).toHaveValue('')

    fireEvent.click(screen.getByRole('button', { name: 'Remove My Catalogs' }))
    fireEvent.click(within(await screen.findByRole('dialog')).getByRole('button', { name: 'Remove' }))
    await waitFor(() => expect(screen.getByText('None added yet.')).toBeInTheDocument())
    expect(calls.some((c) => c.method === 'DELETE' && c.url.endsWith('/abcdef0123'))).toBe(true)
  })

  it('says what is wrong with a saved add-on that stopped answering, instead of hiding it', async () => {
    vi.stubGlobal('fetch', vi.fn(async () => json({ builtin: BUILTIN, items: [{ ...mine, lists: [], error: 'It could not be reached.' }] })))
    withRouter(<AddonsPanel />)
    expect(await screen.findByText('It could not be reached.')).toBeInTheDocument()
    expect(screen.getByRole('button', { name: 'Remove My Catalogs' })).toBeInTheDocument()
  })

  it('shows what an add-on provides and the switch for add-ons on this PC, which is saved when turned on', async () => {
    const streams: AddonsView['items'][number] = { id: '0123456789', url: 'http://127.0.0.1:7000', name: 'Local files', description: '', version: '', lists: [], provides: ['Streams', 'Details'], note: 'It has no lists to browse, so nothing from it shows in Discover. Archive X never asks an add-on for streams.' }
    let view: AddonsView = { builtin: BUILTIN, items: [streams], allow_local: false }
    const bodies: string[] = []
    vi.stubGlobal('fetch', vi.fn(async (url: string, init?: RequestInit) => {
      if (url === '/api/screen/addons/settings') { bodies.push(String(init?.body)); view = { ...view, allow_local: JSON.parse(String(init?.body)).allow_local }; return json(view) }
      if (url === '/api/screen/addons') return json(view)
      return json({})
    }))
    withRouter(<AddonsPanel />)
    expect(await screen.findByText('Provides: Streams, Details')).toBeInTheDocument()
    expect(screen.getByText(/never asks an add-on for streams/)).toBeInTheDocument()
    const sw = screen.getByRole('switch', { name: 'Allow add-ons on this PC or network' })
    expect(sw).toHaveAttribute('aria-checked', 'false')
    fireEvent.click(sw)
    await waitFor(() => expect(screen.getByRole('switch', { name: 'Allow add-ons on this PC or network' })).toHaveAttribute('aria-checked', 'true'))
    expect(bodies).toEqual(['{"allow_local":true}'])
  })

  it('removes a built-in add-on after asking, lists it under Removed, and adds it back', async () => {
    let view: AddonsView = { builtin: BUILTIN, removed: [], items: [] }
    const calls: Array<{ url: string; method: string }> = []
    vi.stubGlobal('fetch', vi.fn(async (url: string, init?: RequestInit) => {
      calls.push({ url, method: init?.method ?? 'GET' })
      if (url === '/api/screen/addons/anime' && init?.method === 'DELETE') { view = { builtin: BUILTIN.filter((b) => b.id !== 'anime'), removed: [BUILTIN[2]!], items: [] }; return json(view) }
      if (url === '/api/screen/addons/anime/restore' && init?.method === 'POST') { view = { builtin: BUILTIN, removed: [], items: [] }; return json(view) }
      if (url === '/api/screen/addons') return json(view)
      return json({})
    }))
    withRouter(<AddonsPanel />)
    const built = await screen.findByRole('region', { name: 'Built in' })
    fireEvent.click(await within(built).findByRole('button', { name: 'Remove Anime Catalogs' }))
    expect(within(await screen.findByRole('dialog')).getByText(/add it back here at any time/)).toBeInTheDocument()
    fireEvent.click(within(screen.getByRole('dialog')).getByRole('button', { name: 'Remove' }))
    const gone = await screen.findByRole('list', { name: 'Removed built-in add-ons' })
    expect(within(gone).getByText('Anime Catalogs')).toBeInTheDocument()
    expect(within(built).queryByRole('button', { name: 'Remove Anime Catalogs' })).not.toBeInTheDocument()
    expect(within(built).getByRole('button', { name: 'Remove Cinemeta' })).toBeInTheDocument()   // the others stay
    fireEvent.click(within(gone).getByRole('button', { name: 'Add back Anime Catalogs' }))
    await waitFor(() => expect(screen.queryByRole('list', { name: 'Removed built-in add-ons' })).not.toBeInTheDocument())
    expect(within(built).getByRole('button', { name: 'Remove Anime Catalogs' })).toBeInTheDocument()
    expect(calls.filter((c) => c.method !== 'GET').map((c) => `${c.method} ${c.url}`)).toEqual(['DELETE /api/screen/addons/anime', 'POST /api/screen/addons/anime/restore'])
  })

  it('says so when every built-in add-on is removed', async () => {
    vi.stubGlobal('fetch', vi.fn(async () => json({ builtin: [], removed: BUILTIN, items: [] })))
    withRouter(<AddonsPanel />)
    expect(await screen.findByText('All built-in add-ons are removed.')).toBeInTheDocument()
    expect(screen.getAllByRole('button', { name: /^Add back / })).toHaveLength(3)
  })
})
