import { fireEvent, screen, waitFor } from '@testing-library/react'
import { afterEach, describe, expect, it, vi } from 'vitest'
import { PerformerGallery } from './PerformerGallery'
import { withRouter } from '@/test/withRouter'

HTMLDialogElement.prototype.showModal ||= function (this: HTMLDialogElement) { this.setAttribute('open', '') }
HTMLDialogElement.prototype.close ||= function (this: HTMLDialogElement) { this.removeAttribute('open') }

const pic = (id: number, provider = 'stashdb', reference = true) => ({ id, provider, width: 300, height: 400, faces: 1, reference, source: 'provider', url: `/api/performers/7/gallery/${id}/image` })
const state = (over = {}) => ({ ok: true, items: [pic(1), pic(2, 'theporndb', false)], removed: 1, syncing: false, last_synced_at: '', last_result: '', references: 1, ...over })
afterEach(() => vi.unstubAllGlobals())

function mockApi(initial: object) {
  const calls: Array<{ url: string; method: string }> = []
  let current = initial as ReturnType<typeof state>
  vi.stubGlobal('fetch', vi.fn(async (url: string, init?: RequestInit) => {
    const method = init?.method ?? 'GET'
    calls.push({ url, method })
    if (method === 'DELETE') { current = { ...current, items: current.items.filter((i) => !url.endsWith(`/${i.id}`)), removed: current.removed + 1 }; return new Response('{"ok":true}', { status: 200 }) }
    if (method === 'POST') { current = { ...current, syncing: true }; return new Response('{"ok":true,"started":true,"syncing":true}', { status: 200 }) }
    if (url.startsWith('/api/photos')) return new Response('{"ok":true,"items":[],"total":0,"page":0,"per":48}', { status: 200 })
    return new Response(JSON.stringify(current), { status: 200 })
  }))
  return calls
}

describe('PerformerGallery', () => {
  it('shows the kept pictures with where they came from and which are used for matching', async () => {
    mockApi(state())
    withRouter(<PerformerGallery id={7} name="Mia Rivera" />)
    expect(await screen.findByText('2 pictures · 1 used for face matching · 1 removed')).toBeInTheDocument()
    expect(screen.getAllByTitle('Used for face matching').length).toBe(1)
    expect(screen.getByTitle('Not read yet')).toBeInTheDocument()
    expect(screen.getByRole('button', { name: 'Open picture: StashDB' })).toBeInTheDocument()
    expect(screen.getByRole('button', { name: 'Open picture: ThePornDB' })).toBeInTheDocument()
    expect(screen.getByText('Not fetched from the providers yet.')).toBeInTheDocument()
  })

  it('says how to get pictures when there are none', async () => {
    mockApi(state({ items: [], references: 0, removed: 0 }))
    withRouter(<PerformerGallery id={7} name="Mia Rivera" />)
    expect(await screen.findByText(/No gallery pictures yet/)).toBeInTheDocument()
    expect(screen.getByRole('button', { name: 'Get pictures from the providers' })).toBeEnabled()
  })

  it('asks the providers for more and shows that a download is running', async () => {
    const calls = mockApi(state())
    withRouter(<PerformerGallery id={7} name="Mia Rivera" />)
    fireEvent.click(await screen.findByRole('button', { name: 'Get new pictures' }))
    await waitFor(() => expect(calls.some((c) => c.method === 'POST' && c.url === '/api/performers/7/gallery/refresh')).toBe(true))
    expect(await screen.findByRole('button', { name: 'Downloading…' })).toBeDisabled()
    expect(screen.getByText('Downloading from the providers…')).toBeInTheDocument()
  })

  it('removes a picture for good when it is not this performer', async () => {
    const calls = mockApi(state())
    withRouter(<PerformerGallery id={7} name="Mia Rivera" />)
    fireEvent.click(await screen.findByRole('button', { name: 'Open picture: StashDB' }))
    fireEvent.click(await screen.findByRole('button', { name: 'Remove this picture: not Mia Rivera' }))
    await waitFor(() => expect(calls.some((c) => c.method === 'DELETE' && c.url === '/api/performers/7/gallery/1')).toBe(true))
    expect(await screen.findByText('1 picture · 1 used for face matching · 2 removed')).toBeInTheDocument()
  })

  it('reports a gallery that cannot be loaded', async () => {
    vi.stubGlobal('fetch', vi.fn(async () => new Response('{"detail":"nope"}', { status: 500 })))
    withRouter(<PerformerGallery id={7} name="Mia Rivera" />)
    expect(await screen.findByText('The gallery could not be loaded.')).toBeInTheDocument()
  })

  it('finds pictures in the performer’s videos, labels them, and can use one as the profile picture', async () => {
    const calls = mockApi(state({ items: [{ ...pic(3, 'snapshot'), source: 'snapshot' }], references: 1, removed: 0 }))
    withRouter(<PerformerGallery id={7} name="Mia Rivera" />)
    fireEvent.click(await screen.findByRole('button', { name: 'Open picture: From a video' }))
    fireEvent.click(await screen.findByRole('button', { name: 'Use this picture as the profile picture of Mia Rivera' }))
    await waitFor(() => expect(calls.some((c) => c.method === 'POST' && c.url === '/api/performers/7/gallery/3/use-as-profile')).toBe(true))
    fireEvent.click(screen.getByRole('button', { name: 'Find pictures in videos' }))
    await waitFor(() => expect(calls.some((c) => c.method === 'POST' && c.url === '/api/performers/7/gallery/from-videos')).toBe(true))
  })
})
