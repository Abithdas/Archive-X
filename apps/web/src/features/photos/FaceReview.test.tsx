import { fireEvent, screen, waitFor, within } from '@testing-library/react'
import { afterEach, beforeAll, describe, expect, it, vi } from 'vitest'
import { toast } from 'sonner'
import { withRouter } from '@/test/withRouter'
import { FaceReview, differentFaces, reviewUrl, withLearned } from './FaceReview'

vi.mock('sonner', () => ({ toast: Object.assign(vi.fn(), { error: vi.fn() }), Toaster: () => null }))
beforeAll(() => { HTMLDialogElement.prototype.showModal = function () { this.setAttribute('open', '') }; HTMLDialogElement.prototype.close = function () { this.removeAttribute('open') } })
afterEach(() => vi.unstubAllGlobals())

const json = (b: unknown) => new Response(JSON.stringify(b), { status: 200 })
const FACES = Array.from({ length: 24 }, (_, i) => ({ detection_id: i + 1, photo_id: i + 1, group_id: 1, crop_url: `/c/${i + 1}`, similarity: i < 4 ? 0.2 + i * 0.02 : 0.6 + i * 0.01 }))
type Call = { url: string; method: string; body?: Record<string, unknown> }
const mount = (onClose = () => {}) => {
  const calls: Call[] = []
  vi.stubGlobal('fetch', vi.fn(async (url: string, init?: RequestInit) => {
    calls.push({ url, method: init?.method ?? 'GET', body: init?.body ? JSON.parse(String(init.body)) : undefined })
    if (url.startsWith('/api/photos/faces/review')) return json({ ok: true, total: FACES.length, items: FACES })
    if (url.startsWith('/api/photos/faces/performers/provider-search')) return json({ ok: true, items: [], configured: true })
    if (url.startsWith('/api/photos/faces/performers')) return json({ items: [{ id: 9, name: 'Zoe Ray' }] })
    return json({ ok: true, moved: 10, group_id: 99 })
  }))
  withRouter(<FaceReview performerId={3} label="Ava Stone" onClose={onClose} />)
  return calls
}

describe('review helpers', () => {
  it('builds the review address for a performer or for face groups', () => {
    expect(reviewUrl(3, [])).toBe('/api/photos/faces/review?performer_id=3'); expect(reviewUrl(null, [4, 5])).toBe('/api/photos/faces/review?group_ids=4,5')
  })
  it('flags faces that look different from the rest', () => { expect(differentFaces(FACES).length).toBe(4) })
})

describe('Check faces', () => {
  it('shows every face and takes the wrong ones out together, in one request', async () => {
    const calls = mount()
    const grid = await screen.findByRole('group', { name: 'Faces' })
    expect(within(grid).getAllByRole('checkbox')).toHaveLength(24)
    fireEvent.click(screen.getByRole('button', { name: /Tick the 10 least alike/ }))
    expect(screen.getByText('10 ticked')).toBeInTheDocument()
    fireEvent.click(screen.getByRole('button', { name: 'Not Ava Stone (10)' }))
    await waitFor(() => expect(calls.find((c) => c.url === '/api/photos/faces/detections/move')?.body).toEqual({ detection_ids: [1, 2, 3, 4, 5, 6, 7, 8, 9, 10], performer_id: null }))
    await waitFor(() => expect(vi.mocked(toast)).toHaveBeenCalledWith(expect.stringContaining('Took 10 faces out of Ava Stone')))
  })
  it('ticks only the faces that look different with one click', async () => {
    mount()
    await screen.findByRole('group', { name: 'Faces' })
    fireEvent.click(screen.getByRole('button', { name: /Tick all that look different \(4\)/ }))
    expect(screen.getByText('4 ticked')).toBeInTheDocument()
  })
  it('can move the ticked faces straight to the right person', async () => {
    const calls = mount()
    await screen.findByRole('group', { name: 'Faces' })
    fireEvent.click(screen.getByRole('checkbox', { name: 'Face 2, looks different' })); fireEvent.click(screen.getByRole('checkbox', { name: 'Face 3, looks different' }))
    fireEvent.click(screen.getByRole('button', { name: 'Move to someone else…' }))
    fireEvent.change(screen.getByRole('searchbox', { name: 'Performer name' }), { target: { value: 'zoe' } })
    fireEvent.click(await screen.findByRole('option', { name: /Zoe Ray/ }))
    await waitFor(() => expect(calls.find((c) => c.url === '/api/photos/faces/detections/move')?.body).toEqual({ detection_ids: [2, 3], performer_id: 9 }))
  })
})

describe('Check faces with what the app has learned', () => {
  const LEARNED = { items: FACES.map((f) => ({ detection_id: f.detection_id, score: f.detection_id === 20 ? 0.1 : 0.7, different: f.detection_id === 20 })) }
  const LIKE = { items: [{ detection_id: 101, photo_id: 5, group_id: 8, crop_url: '/c/101', score: 0.7, strong: true }, { detection_id: 102, photo_id: 6, group_id: 9, crop_url: '/c/102', score: 0.5, strong: false }] }
  const mountLearned = (learned: unknown, like: unknown) => {
    const calls: Call[] = []
    const turnedDown = new Set<number>()   // what the server remembers once told "not this person"
    vi.stubGlobal('fetch', vi.fn(async (url: string, init?: RequestInit) => {
      calls.push({ url, method: init?.method ?? 'GET', body: init?.body ? JSON.parse(String(init.body)) : undefined })
      if (url === '/api/photos/faces/learning/not-this-person') { const ids = (JSON.parse(String(init?.body)) as { detection_ids: number[] }).detection_ids; ids.forEach((i) => turnedDown.add(i)); return json({ ok: true, marked: ids.length, skipped: 0 }) }
      if (url.startsWith('/api/photos/faces/review')) return json({ ok: true, total: FACES.length, items: FACES })
      if (url.startsWith('/api/photos/faces/learning/outliers')) return learned ? json(learned) : new Response('{}', { status: 404 })
      if (url.startsWith('/api/photos/faces/learning/suggestions')) return like ? json({ ...(like as { items: Array<{ detection_id: number }> }), items: (like as { items: Array<{ detection_id: number }> }).items.filter((f) => !turnedDown.has(f.detection_id)) }) : new Response('{}', { status: 404 })
      return json({ ok: true, moved: 1, group_id: 99 })
    }))
    withRouter(<FaceReview performerId={3} label="Ava Stone" onClose={() => {}} />)
    return calls
  }
  it('puts the learned scores over the plain ones, so the face the app doubts comes first and is the one flagged', () => {
    const merged = withLearned(FACES, LEARNED)
    expect(merged[0]?.detection_id).toBe(20)
    expect(differentFaces(merged).map((f) => f.detection_id)).toEqual([20])
    expect(withLearned(FACES, null)).toEqual(FACES)
    expect(withLearned(FACES, {})).toEqual(FACES)
  })
  it('flags the faces the app doubts, and keeps the plain review when nothing was learned', async () => {
    mountLearned(LEARNED, null)
    await screen.findByRole('checkbox', { name: 'Face 20, looks different' })
    expect(screen.getByRole('button', { name: /Tick all that look different \(1\)/ })).toBeInTheDocument()
    expect(screen.queryByRole('group', { name: 'Look-alike faces' })).toBeNull()
  })
  it('works as before when the learning routes are not there', async () => {
    mountLearned(null, null)
    await screen.findByRole('button', { name: /Tick all that look different \(4\)/ })
  })
  it('offers faces from other groups that look like the person and adds the ticked ones to them', async () => {
    const calls = mountLearned(LEARNED, LIKE)
    const group = await screen.findByRole('group', { name: 'Look-alike faces' })
    expect(screen.getByText(/2 new faces look like Ava Stone/)).toBeInTheDocument()
    fireEvent.click(screen.getByRole('button', { name: /Tick the strong matches \(1\)/ }))
    expect(within(group).getByRole('checkbox', { name: 'Look-alike 101, strong match' })).toHaveAttribute('aria-checked', 'true')
    expect(within(group).getByRole('checkbox', { name: 'Look-alike 102' })).toHaveAttribute('aria-checked', 'false')
    fireEvent.click(screen.getByRole('button', { name: 'Add 1 to Ava Stone' }))
    await waitFor(() => expect(calls.find((c) => c.url === '/api/photos/faces/detections/move')?.body).toEqual({ detection_ids: [101], performer_id: 3 }))
  })
  it('turning down look-alikes tells the model they are not this person, moves nothing, and drops them from the list', async () => {
    const calls = mountLearned(LEARNED, LIKE)
    const group = await screen.findByRole('group', { name: 'Look-alike faces' })
    expect(screen.getByRole('button', { name: 'Say the ticked look-alikes are not Ava Stone' })).toBeDisabled()
    fireEvent.click(within(group).getByRole('checkbox', { name: 'Look-alike 102' }))
    fireEvent.click(screen.getByRole('button', { name: 'Say the ticked look-alikes are not Ava Stone' }))
    await waitFor(() => expect(calls.find((c) => c.url === '/api/photos/faces/learning/not-this-person')?.body).toEqual({ performer_id: 3, detection_ids: [102] }))
    expect(calls.some((c) => c.url === '/api/photos/faces/detections/move')).toBe(false)
    await waitFor(() => expect(within(screen.getByRole('group', { name: 'Look-alike faces' })).queryByRole('checkbox', { name: 'Look-alike 102' })).toBeNull())
  })
})
