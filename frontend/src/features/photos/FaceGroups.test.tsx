import { fireEvent, screen, waitFor, within } from '@testing-library/react'
import { afterEach, beforeAll, beforeEach, describe, expect, it, vi } from 'vitest'
import { withRouter } from '@/test/withRouter'
import { PhotosPage } from './PhotosPage'
import { usePhotoStore } from './store'

vi.mock('sonner', () => ({ toast: Object.assign(vi.fn(), { error: vi.fn() }), Toaster: () => null }))
beforeAll(() => {
  HTMLDialogElement.prototype.showModal = function () { this.setAttribute('open', '') }
  HTMLDialogElement.prototype.close = function () { this.removeAttribute('open') }
})
beforeEach(() => { usePhotoStore.setState({ selected: [], tab: 'library', face: null, sort: 'date_desc', favoritesOnly: false, favorites: new Set() }) })
afterEach(() => vi.unstubAllGlobals())

const json = (b: unknown, code = 200) => new Response(JSON.stringify(b), { status: code })
const G = (id: number, o = {}) => ({ id, label: `Face group ${id}`, performer_id: null, performer_name: '', photo_count: 3, face_count: 4, cluster_ids: [id], detection_ids: [id * 10], channel_ids: ['-1001'], representative_url: `/crop/${id}`, metadata_suggestions: [], confirmed_assignment_suggestions: [], ...o })
const GROUPS = [G(1), G(2, { metadata_suggestions: [{ id: 5, name: 'Mia Rivera', basis: 'caption', reason: 'Exact text match in caption', image_url: '/p/5' }] }), G(-3, { performer_id: 3, performer_name: 'Ava Stone', cluster_ids: [3] })]
type Call = { url: string; method: string; body?: Record<string, unknown> }
const mount = (over: (u: string, i?: RequestInit) => Response | undefined = () => undefined, groups: unknown[] = GROUPS, analysis = { total_photos: 9, analyzed_photos: 5, failed_photos: 0 }) => {
  const calls: Call[] = []
  vi.stubGlobal('fetch', vi.fn(async (url: string, init?: RequestInit) => {
    calls.push({ url, method: init?.method ?? 'GET', body: init?.body ? JSON.parse(String(init.body)) : undefined })
    const o = over(url, init); if (o) return o
    if (url === '/api/source-channels') return json([{ telegram_id: '-1001', title: 'Main' }])
    if (url === '/api/photos/channels') return json([{ telegram_id: '-1001', photo_count: 9, photo_scan_status: 'COMPLETE' }])
    if (url.startsWith('/api/photos/faces/groups') && !init?.method) return json({ ok: true, groups, analysis })
    if (url.startsWith('/api/photos/faces/performers')) return json({ items: [{ id: 7, name: 'Zoe Ray' }] })
    if (url.startsWith('/api/photos?')) return json({ ok: true, items: [{ id: 1, channel_id: '-1001', message_id: 1, filename: 'a.jpg', size: 1, width: 10, height: 10, thumbnail_url: '/t', preview_url: '/p', image_url: '/i' }], total: 1, page: 0, per: 48 })
    return json({ ok: true })
  }))
  withRouter(<PhotosPage />)
  return calls
}
const posts = (c: Call[]) => c.filter((x) => x.method === 'POST')
const openTab = async () => fireEvent.click(await screen.findByRole('tab', { name: /Image Analysis/ }))

describe('Image Analysis tab', () => {
  it('lists the groups with their status and counts in the tab', async () => {
    mount(); await openTab()
    expect(await screen.findByRole('article', { name: 'Face group 1' })).toBeInTheDocument()
    expect(screen.getByRole('article', { name: 'Ava Stone' })).toHaveTextContent('Assigned · Ava Stone')
    expect(screen.getByRole('article', { name: 'Face group 1' })).toHaveTextContent('Needs performer review')
    expect(screen.getByText(/5 \/ 9 photos analyzed in the full indexed library · 3 visual groups/)).toBeInTheDocument()
    expect(screen.getByRole('tab', { name: /Image Analysis/ })).toHaveTextContent('3')
  })
  it('explains an empty analysis', async () => {
    mount(() => undefined, [], { total_photos: 0, analyzed_photos: 0, failed_photos: 0 }); await openTab()
    expect(await screen.findByText('No face groups yet')).toBeInTheDocument()
    expect(screen.getByText(/No indexed photos yet/)).toBeInTheDocument()
    expect(screen.getByRole('button', { name: /Analyze images for faces/ })).toBeDisabled()
  })
  it('asks before analysing and sends the selected channels', async () => {
    const calls = mount((u, i) => (u === '/api/photos/faces/analyze' && i?.method === 'POST' ? json({ id: 'job1' }) : u.startsWith('/api/photos/scan-status/') ? json({ status: 'COMPLETED', progress: 100 }) : undefined))
    usePhotoStore.setState({ selected: ['-1001'] }); await openTab()
    fireEvent.click(await screen.findByRole('button', { name: 'Analyze images for faces' }))
    const ask = await screen.findByRole('dialog', { name: 'Analyze photos for faces?' })
    expect(ask).toHaveTextContent('1 selected channel'); expect(posts(calls)).toHaveLength(0)
    fireEvent.click(within(ask).getByRole('button', { name: 'Analyze images' }))
    await waitFor(() => expect(posts(calls)[0]).toMatchObject({ url: '/api/photos/faces/analyze', body: { channel_ids: ['-1001'] } }))
  })
  it('merges two or more clusters only after confirmation', async () => {
    const calls = mount(); await openTab()
    const merge = await screen.findByRole('button', { name: 'Merge selected clusters' })
    expect(merge).toBeDisabled()
    fireEvent.click(screen.getByRole('checkbox', { name: 'Select cluster Face group 1 to merge' })); expect(merge).toBeDisabled()
    fireEvent.click(screen.getByRole('checkbox', { name: 'Select cluster Face group 2 to merge' })); expect(merge).toBeEnabled()
    expect(screen.getByText('2 clusters selected')).toBeInTheDocument()
    fireEvent.click(merge)
    const ask = await screen.findByRole('dialog', { name: 'Merge face clusters?' })
    fireEvent.click(within(ask).getByRole('button', { name: 'Cancel' })); expect(posts(calls)).toHaveLength(0)
    fireEvent.click(merge); fireEvent.click(within(await screen.findByRole('dialog', { name: 'Merge face clusters?' })).getByRole('button', { name: 'Merge clusters' }))
    await waitFor(() => expect(posts(calls)[0]).toMatchObject({ url: '/api/photos/faces/groups/merge', body: { group_ids: [1, 2] } }))
  })
  it('separates one face after confirmation', async () => {
    const calls = mount(); await openTab()
    fireEvent.click(await screen.findByRole('button', { name: 'Separate one face from Face group 1' }))
    fireEvent.click(within(await screen.findByRole('dialog', { name: 'Separate face detection?' })).getByRole('button', { name: 'Separate face' }))
    await waitFor(() => expect(posts(calls)[0]).toMatchObject({ url: '/api/photos/faces/detections/10/split' }))
  })
  it('assigns a searched performer, refusing without one, and clears an assignment', async () => {
    const calls = mount(); await openTab()
    const save = await screen.findByRole('button', { name: 'Save assignment for Face group 1' })
    fireEvent.click(save)
    expect(await screen.findByText('Search and select a local performer before saving this assignment.')).toBeInTheDocument(); expect(posts(calls)).toHaveLength(0)
    const card = screen.getByRole('article', { name: 'Face group 1' })
    fireEvent.change(within(card).getByLabelText('Assign to performer'), { target: { value: 'zo' } })
    fireEvent.click(await within(card).findByRole('option', { name: /Zoe Ray/ }))
    fireEvent.click(save)
    await waitFor(() => expect(posts(calls)[0]).toMatchObject({ url: '/api/photos/faces/groups/1/assign', body: { performer_id: 7, group_ids: [1] } }))
    fireEvent.click(screen.getByRole('button', { name: 'Clear assignment for Ava Stone' }))
    await waitFor(() => expect(posts(calls).at(-1)).toMatchObject({ url: '/api/photos/faces/groups/-3/assign', body: { performer_id: null, group_ids: [3] } }))
  })
  it('confirms or rejects a source-text suggestion with the right evidence', async () => {
    const calls = mount(); await openTab()
    fireEvent.click(await screen.findByRole('button', { name: 'Confirm Mia Rivera for Face group 2' }))
    await waitFor(() => expect(posts(calls)[0]).toMatchObject({ url: '/api/photos/faces/groups/2/assign', body: { performer_id: 5, suggestion_source: 'metadata', suggestion_reason: 'Confirmed from source metadata: caption' } }))
    fireEvent.click(await screen.findByRole('button', { name: 'Not Mia Rivera for Face group 2' }))
    await waitFor(() => expect(posts(calls).at(-1)).toMatchObject({ url: '/api/photos/faces/suggestions/reject', body: { group_ids: [2], performer_id: 5, source: 'metadata' } }))
    expect(screen.queryByText('Suggestions from your previous confirmations')).not.toBeInTheDocument()
  })
  it('opens a group\'s photos filtered by it, offers Different person there, and can clear the filter', async () => {
    const calls = mount(); await openTab()
    fireEvent.click(await screen.findByRole('button', { name: 'View photos in Face group 2' }))
    expect(await screen.findByText('Face group · Face group 2')).toBeInTheDocument()
    await waitFor(() => expect(calls.some((c) => c.url.startsWith('/api/photos?') && c.url.includes('face_group_ids=2'))).toBe(true))
    expect(usePhotoStore.getState().selected).toEqual(['-1001'])
    const card = await screen.findByRole('article', { name: 'a.jpg' })
    fireEvent.click(within(card).getByRole('menuitem', { name: /Different person/, hidden: true }))
    fireEvent.click(within(await screen.findByRole('dialog', { name: 'Different person?' })).getByRole('button', { name: 'Separate photo' }))
    await waitFor(() => expect(posts(calls)[0]).toMatchObject({ url: '/api/photos/faces/different-person', body: { photo_id: 1, group_ids: [2] } }))
    fireEvent.click(screen.getByRole('button', { name: 'Clear group filter' }))
    await waitFor(() => expect(screen.queryByText('Face group · Face group 2')).not.toBeInTheDocument())
  })
  it('shows the server reason when groups cannot load', async () => {
    mount((u) => (u.startsWith('/api/photos/faces/groups') ? json({ detail: 'Face tables locked' }, 500) : undefined)); await openTab()
    expect(await screen.findByText('Face tables locked')).toBeInTheDocument()
  })
})
