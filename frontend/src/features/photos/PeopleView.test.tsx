import { fireEvent, screen, waitFor, within } from '@testing-library/react'
import { afterEach, beforeAll, beforeEach, describe, expect, it, vi } from 'vitest'
import { withRouter } from '@/test/withRouter'
import { PhotosPage } from './PhotosPage'
import { peopleSections } from './faces'
import { usePhotoStore } from './store'

vi.mock('sonner', () => ({ toast: Object.assign(vi.fn(), { error: vi.fn() }), Toaster: () => null }))
beforeAll(() => { HTMLDialogElement.prototype.showModal = function () { this.setAttribute('open', '') }; HTMLDialogElement.prototype.close = function () { this.removeAttribute('open') } })
beforeEach(() => usePhotoStore.setState({ selected: [], tab: 'faces', face: null }))
afterEach(() => vi.unstubAllGlobals())

const json = (b: unknown, code = 200) => new Response(JSON.stringify(b), { status: code })
const G = (id: number, o = {}) => ({ id, label: `Face group ${id}`, performer_id: null, performer_name: '', photo_count: 5, face_count: 5, cluster_ids: [id], detection_ids: [id * 10], channel_ids: ['-1001'], representative_url: `/c/${id}`, ...o })
const GROUPS = [
  G(1, { performer_id: 3, performer_name: 'Ava Stone' }),
  G(2, { profile_image_suggestions: [{ id: 5, name: 'Mia Rivera', source: 'profile_image', similarity_score: 0.8, basis: 'profile picture' }] }),
  G(3),
  G(4, { photo_count: 1 }),
]
type Call = { url: string; method: string; body?: Record<string, unknown> }
const mount = () => {
  const calls: Call[] = []
  vi.stubGlobal('fetch', vi.fn(async (url: string, init?: RequestInit) => {
    calls.push({ url, method: init?.method ?? 'GET', body: init?.body ? JSON.parse(String(init.body)) : undefined })
    if (url === '/api/source-channels') return json([{ telegram_id: '-1001', title: 'Main' }])
    if (url === '/api/photos/channels') return json([{ telegram_id: '-1001', photo_count: 9, photo_scan_status: 'COMPLETE' }])
    if (url.startsWith('/api/photos/faces/groups') && !init?.method) return json({ ok: true, groups: GROUPS, analysis: { total_photos: 9, analyzed_photos: 9, failed_photos: 0 } })
    if (url.startsWith('/api/photos/faces/performers')) return json({ items: [{ id: 7, name: 'Zoe Ray' }] })
    return json({ ok: true })
  }))
  withRouter(<PhotosPage />)
  return calls
}

describe('peopleSections', () => {
  it('splits into named, guessed, to-name and rare faces', () => {
    const s = peopleSections(GROUPS as never)
    expect([s.named.length, s.guesses.length, s.toName.length, s.rare.length]).toEqual([1, 1, 1, 1])
  })
})

describe('People inside Photos', () => {
  it('shows people in plain groups, with one clear question per unknown face', async () => {
    mount()
    const pv = await screen.findByRole('region', { name: 'Is this the right person?' })
    expect(within(pv).getByText('Is this Mia Rivera?')).toBeInTheDocument()
    expect(within(screen.getByRole('region', { name: 'Named people' })).getByText('Ava Stone')).toBeInTheDocument()
    expect(within(screen.getByRole('region', { name: 'Faces to name' })).getByText('Who is this?')).toBeInTheDocument()
    expect(screen.getByRole('button', { name: /Show 1 faces seen only once or twice/ })).toBeInTheDocument()
  })
  it('Yes names the person with the evidence; No rejects the guess', async () => {
    const calls = mount()
    fireEvent.click(await screen.findByRole('button', { name: 'Yes, this is Mia Rivera' }))
    await waitFor(() => expect(calls.find((c) => c.method === 'POST')).toMatchObject({ url: '/api/photos/faces/groups/2/assign', body: { performer_id: 5, suggestion_source: 'profile_image' } }))
    fireEvent.click(screen.getByRole('button', { name: 'No, this is not Mia Rivera' }))
    await waitFor(() => expect(calls.some((c) => c.url === '/api/photos/faces/suggestions/reject')).toBe(true))
  })
  it('Name lets you search a performer and saves it', async () => {
    const calls = mount()
    const card = (await screen.findByRole('region', { name: 'Faces to name' })).querySelector('article')!
    fireEvent.click(within(card as HTMLElement).getByRole('button', { name: 'Name' }))
    fireEvent.change(screen.getByRole('searchbox', { name: 'Performer name' }), { target: { value: 'zoe' } })
    fireEvent.click(await screen.findByRole('option', { name: /Zoe Ray/ }))
    await waitFor(() => expect(calls.find((c) => c.method === 'POST' && c.url.endsWith('/assign'))).toMatchObject({ url: '/api/photos/faces/groups/3/assign', body: { performer_id: 7 } }))
  })
  it('clicking a person opens their photos in the gallery', async () => {
    mount()
    fireEvent.click(await screen.findByRole('button', { name: 'Show photos of Ava Stone' }))
    expect(usePhotoStore.getState().tab).toBe('library')
    expect(usePhotoStore.getState().face?.performerId).toBe(3)
  })
  it('Find people asks first, then starts the job that downloads pictures and matches', async () => {
    const calls = mount()
    fireEvent.click(await screen.findByRole('button', { name: 'Find people' }))
    fireEvent.click(within(await screen.findByRole('dialog', { name: 'Find people?' })).getByRole('button', { name: 'Find people' }))
    await waitFor(() => expect(calls.find((c) => c.url === '/api/photos/faces/analyze')?.body).toEqual({ channel_ids: [], match_performers: true }))
  })
})
