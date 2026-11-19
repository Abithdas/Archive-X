import { fireEvent, screen, waitFor, within } from '@testing-library/react'
import { afterEach, beforeAll, describe, expect, it, vi } from 'vitest'
import { withRouter } from '@/test/withRouter'
import { AddonsPanel, type AddonsView } from './AddonsPanel'

beforeAll(() => { HTMLDialogElement.prototype.showModal = function () { this.setAttribute('open', '') }; HTMLDialogElement.prototype.close = function () { this.removeAttribute('open') } })
afterEach(() => vi.unstubAllGlobals())
const json = (b: unknown, code = 200) => new Response(JSON.stringify(b), { status: code, headers: { 'content-type': 'application/json' } })
const BUILTIN = [{ name: 'Cinemeta', what: 'Popular' }, { name: 'Streaming Catalogs', what: 'Netflix' }, { name: 'Anime Catalogs', what: 'MyAnimeList' }]
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
})
