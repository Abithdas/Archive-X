import { fireEvent, screen, waitFor, within } from '@testing-library/react'
import { afterEach, beforeAll, beforeEach, describe, expect, it, vi } from 'vitest'
import { withRouter } from '@/test/withRouter'
import { PhotosPage } from './PhotosPage'
import { parseReview } from './useFaceReview'
import { usePhotoStore } from './store'

vi.mock('sonner', () => ({ toast: Object.assign(vi.fn(), { error: vi.fn() }), Toaster: () => null }))
beforeAll(() => { HTMLDialogElement.prototype.showModal = function () { this.setAttribute('open', '') }; HTMLDialogElement.prototype.close = function () { this.removeAttribute('open') } })
beforeEach(() => usePhotoStore.setState({ selected: [], tab: 'faces', face: null }))
afterEach(() => vi.unstubAllGlobals())

const json = (b: unknown) => new Response(JSON.stringify(b), { status: 200 })
const base = { label: '', performer_id: null, performer_name: '', photo_count: 5, face_count: 5, representative_url: '', channel_ids: ['-1001'], updated_at: '' }
const guess = (id: number, who: string) => ({ ...base, id, label: `Face group ${id}`, cluster_ids: [id], detection_ids: [id * 10], profile_image_suggestions: [{ id: 50 + id, name: who, source: 'profile_image', similarity_score: 0.8, basis: 'profile picture' }] })
const ava = { ...base, id: 1, label: 'Ava Stone', performer_id: 3, performer_name: 'Ava Stone', cluster_ids: [1], detection_ids: [10] }

function mount(opts: { groups: unknown[]; review?: unknown; hold?: (url: string) => boolean }) {
  const calls: string[] = []
  const held: Array<() => void> = []
  vi.stubGlobal('fetch', vi.fn(async (url: string, init?: RequestInit) => {
    calls.push(`${init?.method ?? 'GET'} ${url}`)
    if (url === '/api/source-channels') return json([{ telegram_id: '-1001', title: 'Main' }])
    if (url === '/api/photos/channels') return json([{ telegram_id: '-1001', photo_count: 9, photo_scan_status: 'COMPLETE' }])
    if (url === '/api/photos/faces/learning/review') return opts.review ? json(opts.review) : new Response('{}', { status: 404 })
    if (url.startsWith('/api/photos/faces/groups') && !init?.method) return json({ ok: true, groups: opts.groups, photo_scopes: {}, analysis: { total_photos: 9, analyzed: 9 } })
    if (url.startsWith('/api/photos?')) return json({ ok: true, items: [], total: 0, page: 0, per: 60 })
    if (init?.method === 'POST' && opts.hold?.(url)) await new Promise<void>((res) => held.push(res))
    return json({ ok: true })
  }))
  withRouter(<PhotosPage />)
  return { calls, release: () => held.splice(0).forEach((r) => r()) }
}

describe('answering "Is this the right person?"', () => {
  it('a slow answer on one card does not hold up the others: it steps aside at once and the next can be answered straight away', async () => {
    const { calls, release } = mount({ groups: [guess(2, 'Mia Rivera'), guess(3, 'Nova Lane')], hold: (u) => u.endsWith('/groups/2/assign') })
    const region = () => screen.queryByRole('region', { name: 'Is this the right person?' })   // the whole section is replaced when its last card steps aside
    fireEvent.click(await screen.findByRole('button', { name: 'Yes, this is Mia Rivera' }))
    const next = await screen.findByRole('button', { name: 'Yes, this is Nova Lane' })
    expect(next).toBeEnabled()                                  // no button waits for Mia's answer
    fireEvent.click(next)
    await waitFor(() => expect(calls).toContain('POST /api/photos/faces/groups/3/assign'))   // sent while the first is still on its way
    expect(calls).toContain('POST /api/photos/faces/groups/2/assign')
    await waitFor(() => expect(region() && within(region()!).queryByRole('button', { name: 'Yes, this is Mia Rivera' })).toBeFalsy(), { timeout: 3000 })
    release()
  })
})

describe('new matches to check', () => {
  it('reads the learning answer, ignoring anything odd', () => {
    expect(parseReview({ people: [{ performer_id: 3, suggestions: 4, strong: 2 }, { performer_id: 0, suggestions: 9 }, { performer_id: 5, suggestions: 0 }, { performer_id: 'x', suggestions: 2 }] })).toEqual({ 3: { suggestions: 4, strong: 2 } })
    expect(parseReview(null)).toEqual({})
    expect(parseReview({ people: 'no' })).toEqual({})
  })
  it('marks the person who has some, on the card and on the People tab, and offers to review them', async () => {
    mount({ groups: [ava], review: { ok: true, people: [{ performer_id: 3, name: 'Ava Stone', suggestions: 4, strong: 3 }] } })
    const card = (await screen.findByRole('region', { name: 'Named people' })).querySelector('article') as HTMLElement
    expect(await within(card).findByRole('button', { name: 'Check faces of Ava Stone, 4 new matches to check' })).toHaveTextContent('Review new matches')
    expect(screen.getByRole('tab', { name: /People/ })).toHaveTextContent('4')
    expect(screen.getAllByLabelText('4 new matches to check')).toHaveLength(2)   // the People tab, and the number on the person's face in the row
  })
  it('shows nothing extra when there are none, or when face learning is not available', async () => {
    mount({ groups: [ava] })
    const card = (await screen.findByRole('region', { name: 'Named people' })).querySelector('article') as HTMLElement
    expect(within(card).getByRole('button', { name: 'Check faces of Ava Stone' })).toHaveTextContent('Check faces')
    expect(screen.queryByLabelText(/new matches? to check/)).toBeNull()
  })
})

describe('opening People', () => {
  it('shows the people at once, without waiting for the slow work of guessing who the unnamed faces are', async () => {
    vi.stubGlobal('fetch', vi.fn(async (url: string, init?: RequestInit) => {
      if (url === '/api/source-channels') return json([{ telegram_id: '-1001', title: 'Main' }])
      if (url === '/api/photos/channels') return json([{ telegram_id: '-1001', photo_count: 9, photo_scan_status: 'COMPLETE' }])
      if (url.startsWith('/api/photos/faces/groups') && !init?.method) {
        if (!url.includes('suggestions=0')) return new Promise<Response>(() => undefined)   // the guesses never arrive
        return json({ ok: true, groups: [ava], photo_scopes: {}, analysis: { total_photos: 9, analyzed: 9 } })
      }
      if (url.startsWith('/api/photos?')) return json({ ok: true, items: [], total: 0, page: 0, per: 60 })
      return new Response('{}', { status: 404 })
    }))
    withRouter(<PhotosPage />)
    expect(await screen.findByRole('region', { name: 'Named people' })).toBeInTheDocument()
    expect(screen.queryByRole('status', { name: 'Loading people' })).toBeNull()
  })
})
