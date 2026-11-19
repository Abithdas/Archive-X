import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { fireEvent, render, screen, waitFor, within } from '@testing-library/react'
import { afterEach, beforeAll, describe, expect, it, vi } from 'vitest'
import { SceneMatchCard } from './SceneMatchCard'
import { SceneMatchReview } from './SceneMatchReview'

const toast = vi.hoisted(() => Object.assign(vi.fn(), { error: vi.fn() }))
vi.mock('sonner', () => ({ toast, Toaster: () => null }))
beforeAll(() => {
  HTMLDialogElement.prototype.showModal = function () { this.setAttribute('open', '') }
  HTMLDialogElement.prototype.close = function () { this.removeAttribute('open') }
})
afterEach(() => { vi.unstubAllGlobals(); toast.mockClear() })
const json = (b: unknown, code = 200) => new Response(JSON.stringify(b), { status: code })
const sug = (id: number, extra = {}) => ({ suggestion_id: id, score: 96.5, channel_id: '-1001', channel_title: 'Main', message_id: 1000 + id, filename: `f${id}.mp4`, candidate_title: `Scene ${id}`, provider: 'StashDB', performers: ['Ava'], release_date: '2024-01-02', signals: 'title match', missing_fields: ['performer'], provider_site: 'Northlight', provider_url: 'https://stashdb.example/s', ...extra })
const unr = (id: number) => ({ media_id: id, channel_id: '-1001', channel_title: 'Main', message_id: 2000 + id, filename: `u${id}.mp4`, title: `Unresolved ${id}`, missing_fields: ['studio'], performers: [] })
const page = (o = {}) => ({ items: [sug(1), sug(2)], count: 2, has_more: false, unresolved_items: [unr(1)], unresolved_count: 1, unresolved_has_more: false, unresolved_channels: [{ channel_id: '-1001', title: 'Main' }], ...o })
type Call = { url: string; method: string; body?: Record<string, unknown> }
const mount = (over: (u: string, i?: RequestInit) => Response | undefined = () => undefined, body: (u: string) => unknown = () => page(), ui = <SceneMatchReview onClose={() => {}} />) => {
  const calls: Call[] = []
  vi.stubGlobal('fetch', vi.fn(async (url: string, init?: RequestInit) => {
    calls.push({ url, method: init?.method ?? 'GET', body: init?.body ? JSON.parse(String(init.body)) : undefined })
    const o = over(url, init); if (o) return o
    if (url.startsWith('/api/metadata/scene-match-review?')) return json(body(url))
    if (url.startsWith('/api/play/preview-manifest')) return json({ ready: false, frame_times: [] })
    return json({ ok: true })
  }))
  render(<QueryClientProvider client={new QueryClient({ defaultOptions: { queries: { retry: false } } })}>{ui}</QueryClientProvider>)
  return calls
}
const posts = (c: Call[]) => c.filter((x) => x.method === 'POST')

describe('SceneMatchReview', () => {
  it('shows every provider picture (up to six, no repeats) and says when there is none', async () => {
    mount(undefined, () => page({ items: [sug(1, { provider_image: 'https://p/a.jpg', provider_images: ['https://p/a.jpg', 'https://p/b.jpg', 'https://p/c.jpg', 'javascript:alert(1)'] }), sug(2, { provider_image: '', provider_images: [] })] }))
    const one = await screen.findByRole('article', { name: 'Scene 1' })
    expect(within(one).getAllByRole('button', { name: /Enlarge: Provider picture \d of 3/ })).toHaveLength(3)
    expect(within(screen.getByRole('article', { name: 'Scene 2' })).getByText('The provider has no picture for this scene')).toBeInTheDocument()
  })
  it('names the provider in the link to its page', async () => {
    mount(undefined, () => page({ items: [sug(1, { provider_name: 'ThePornDB', provider_url: 'https://theporndb.net/scenes/abc' })] }))
    expect(await screen.findByRole('link', { name: 'Open on ThePornDB' })).toHaveAttribute('href', 'https://theporndb.net/scenes/abc')
  })
  it('shows ranked suggestions with score, provider and details, and the videos needing metadata', async () => {
    mount()
    const card = await screen.findByRole('article', { name: 'Scene 1' })
    expect(within(card).getByText('96.5%')).toBeInTheDocument(); expect(within(card).getByText('StashDB')).toBeInTheDocument(); expect(within(card).getByRole('link', { name: 'Open on StashDB' })).toHaveAttribute('href', 'https://stashdb.example/s')
    expect(within(card).getByText('performer')).toBeInTheDocument(); expect(screen.getByRole('article', { name: 'Unresolved 1' })).toHaveTextContent('Missing: studio')
    expect(screen.getByText('2 suggestions · 1 need metadata')).toBeInTheDocument()
  })
  it('approves and rejects one suggestion, then reloads', async () => {
    const calls = mount()
    fireEvent.click(await screen.findByRole('button', { name: 'Approve Scene 1' }))
    await waitFor(() => expect(posts(calls)[0]).toMatchObject({ url: '/api/suggestions/1/confirm' })); expect(toast).toHaveBeenCalledWith('Scene match approved.')
    fireEvent.click(await screen.findByRole('button', { name: 'Reject Scene 2' }))
    await waitFor(() => expect(posts(calls)[1]).toMatchObject({ url: '/api/suggestions/2/reject' })); expect(toast).toHaveBeenCalledWith('Scene suggestion rejected.')
    await waitFor(() => expect(calls.filter((c) => c.url.startsWith('/api/metadata/scene-match-review?')).length).toBeGreaterThan(1))
  })
  it('rejects the selected suggestions in one request and reports the counts', async () => {
    const calls = mount((u) => (u === '/api/metadata/scene-match-review/reject-selected' ? json({ ok: true, rejected: 2, not_pending: 0, failed: 0, rejected_ids: [1, 2] }) : undefined))
    fireEvent.click(await screen.findByLabelText('Select Scene 1 for rejection')); fireEvent.click(screen.getByLabelText('Select Scene 2 for rejection'))
    fireEvent.click(screen.getByRole('button', { name: 'Reject selected (2)' }))
    await waitFor(() => expect(posts(calls)[0]).toMatchObject({ url: '/api/metadata/scene-match-review/reject-selected', body: { suggestion_ids: [1, 2] } }))
    await waitFor(() => expect(toast).toHaveBeenCalledWith('Rejected 2 selected suggestion(s); 0 were no longer pending; 0 failed.'))
  })
  it('bulk-approves above a threshold only after confirmation', async () => {
    const calls = mount((u) => (u.endsWith('/approve-above') ? json({ ok: true, approved: 5, ambiguous_skipped: 1, website_variants_skipped: 0, performer_conflicts: 2 }) : undefined))
    const box = await screen.findByLabelText('Scene match approval confidence threshold')
    fireEvent.change(box, { target: { value: '90' } }); fireEvent.click(screen.getByRole('button', { name: 'Approve above' }))
    const ask = await screen.findByRole('dialog', { name: 'Approve high-confidence scenes' }); expect(ask).toHaveTextContent('scored 90.0% or higher'); expect(posts(calls)).toHaveLength(0)
    fireEvent.click(within(ask).getByRole('button', { name: 'Cancel' })); expect(posts(calls)).toHaveLength(0)
    fireEvent.click(screen.getByRole('button', { name: 'Approve above' })); fireEvent.click(within(await screen.findByRole('dialog', { name: 'Approve high-confidence scenes' })).getByRole('button', { name: 'Approve candidates' }))
    await waitFor(() => expect(posts(calls)[0]).toMatchObject({ url: '/api/metadata/scene-match-review/approve-above', body: { threshold: 90 } }))
    await waitFor(() => expect(toast).toHaveBeenCalledWith('Approved 5; 1 ambiguous video(s) left for review; 0 cross-site variant(s) need individual review; 2 blocked by performer conflicts.'))
  })
  it('filters the metadata list by channel and missing field, and clears them', async () => {
    const calls = mount()
    await screen.findByRole('article', { name: 'Scene 1' })
    fireEvent.change(screen.getByLabelText('Filter videos by missing field'), { target: { value: 'studio' } })
    await waitFor(() => expect(calls.some((c) => c.url.includes('unresolved_missing_field=studio'))).toBe(true))
    expect(await screen.findByText(/filtered/)).toBeInTheDocument()
    fireEvent.click(screen.getByRole('button', { name: 'Clear filters' }))
    await waitFor(() => expect(calls.at(-1)!.url).toContain('unresolved_missing_field=all'))
  })
  it('loads more with the offsets of what is already shown', async () => {
    const calls = mount(undefined, (u) => (u.includes('offset=2&') ? page({ items: [sug(3)], count: 3, has_more: false, unresolved_items: [], unresolved_has_more: false }) : page({ has_more: true })))
    fireEvent.click(await screen.findByRole('button', { name: 'Load more' }))
    expect(await screen.findByRole('article', { name: 'Scene 3' })).toBeInTheDocument()
    expect(calls.some((c) => c.url.includes('offset=2&unresolved_limit=24&unresolved_offset=1'))).toBe(true)
  })
  it('opens the quick editor for a video that needs metadata', async () => {
    mount((u) => (u.startsWith('/api/suggestions?') ? json([]) : undefined))
    fireEvent.click(await screen.findByRole('button', { name: 'Edit video Unresolved 1' }))
    expect(await screen.findByRole('dialog', { name: /Edit video/i })).toBeInTheDocument()
  })
  it('shows why loading failed and can retry', async () => {
    mount((u) => (u.startsWith('/api/metadata/scene-match-review?') ? json({ detail: 'Catalogue locked' }, 503) : undefined))
    expect(await screen.findByText('Catalogue locked')).toBeInTheDocument(); expect(screen.getByRole('button', { name: 'Retry' })).toBeInTheDocument()
  })
})

describe('SceneMatchCard', () => {
  it('opens the review without scanning, and Retry & review queues a recheck first', async () => {
    const calls = mount((u, i) => (u === '/api/metadata/scene-match-review/retry-unmatched' && i?.method === 'POST' ? json({ ok: true, id: 'job1', count: 12 }) : undefined), () => page(), <SceneMatchCard />)
    fireEvent.click(screen.getByRole('button', { name: 'Open review' }))
    expect(await screen.findByRole('dialog', { name: 'Scene Match Review' })).toBeInTheDocument(); expect(posts(calls)).toHaveLength(0)
  })
  it('queues one manual recheck and says so', async () => {
    const calls = mount((u, i) => (u === '/api/metadata/scene-match-review/retry-unmatched' && i?.method === 'POST' ? json({ ok: true, id: 'job1', count: 12 }) : undefined), () => page(), <SceneMatchCard />)
    fireEvent.click(screen.getByRole('button', { name: /Retry & review/ }))
    await waitFor(() => expect(posts(calls)[0]).toMatchObject({ url: '/api/metadata/scene-match-review/retry-unmatched' }))
    expect(await screen.findByText('Queued 12 unmatched/suggested video(s) for one manual recheck.')).toBeInTheDocument(); expect(toast).toHaveBeenCalledWith('Retry queued for 12 video(s).')
    expect(await screen.findByRole('dialog', { name: 'Scene Match Review' })).toBeInTheDocument()
  })
})
