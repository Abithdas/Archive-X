import { fireEvent, screen, waitFor, within } from '@testing-library/react'
import { afterEach, beforeAll, beforeEach, describe, expect, it, vi } from 'vitest'
import { withRouter } from '@/test/withRouter'
import { ContentTags, tagLine, toggleTag } from './ContentTags'
import { photoParams } from './photos'
import { usePhotoStore } from './store'

vi.mock('sonner', () => ({ toast: Object.assign(vi.fn(), { error: vi.fn() }), Toaster: () => null }))
beforeAll(() => { HTMLDialogElement.prototype.showModal = function () { this.setAttribute('open', '') }; HTMLDialogElement.prototype.close = function () { this.removeAttribute('open') } })
beforeEach(() => usePhotoStore.setState({ contentTags: [] }))
afterEach(() => vi.unstubAllGlobals())
const json = (b: unknown) => new Response(JSON.stringify(b), { status: 200 })
const SUMMARY = { ok: true, available: true, total_photos: 100, analysed_photos: 60, tags: [{ tag: 'breasts', label: 'Breasts', count: 21 }, { tag: 'feet', label: 'Feet', count: 5 }],
  looks: { available: true, total_photos: 100, analysed_photos: 40, tags: [{ tag: 'hair_red', label: 'Redhead', group: 'Hair colour', count: 7 }, { tag: 'hair_long', label: 'Long hair', group: 'Hair length', count: 12 }] } }
const mount = (summary: unknown = SUMMARY) => {
  const calls: Array<{ url: string; body?: unknown }> = []
  vi.stubGlobal('fetch', vi.fn(async (url: string, init?: RequestInit) => {
    calls.push({ url, body: init?.body ? JSON.parse(String(init.body)) : undefined })
    if (url === '/api/photos/content-tags') return json(summary)
    if (url === '/api/photos/content-tags/analyze') return json({ ok: true, id: 'j1' })
    return json({ ok: true })
  }))
  withRouter(<ContentTags />)
  return calls
}

describe('content tag helpers', () => {
  it('toggles tags and words the progress', () => {
    expect(toggleTag(['a'], 'b')).toEqual(['a', 'b']); expect(toggleTag(['a', 'b'], 'a')).toEqual(['b'])
    expect(tagLine(SUMMARY)).toBe('60 of 100 photos are tagged.'); expect(tagLine({ ...SUMMARY, analysed_photos: 100 })).toBe('All 100 photos are tagged.')
  })
  it('adds the chosen tags to the photo query', () => {
    expect(photoParams({ channels: ['-1'], q: '', sort: 'date_desc', favorites: false, favoriteIds: new Set(), page: 0, contentTags: ['breasts', 'feet'] }).get('content_tags')).toBe('breasts,feet')
  })
})

describe('Content tags on Photos', () => {
  it('shows each tag with its count and filters when ticked', async () => {
    mount()
    const grp = await screen.findByRole('group', { name: 'Filter by content' })
    expect(within(grp).getByRole('button', { name: /Breasts\s*21/ })).toBeInTheDocument()
    fireEvent.click(within(grp).getByRole('button', { name: /Feet/ }))
    expect(usePhotoStore.getState().contentTags).toEqual(['feet'])
    fireEvent.click(within(grp).getByRole('button', { name: 'Clear' }))
    expect(usePhotoStore.getState().contentTags).toEqual([])
  })
  it('explains what the detector does before starting, then starts the job', async () => {
    const calls = mount()
    fireEvent.click(await screen.findByRole('button', { name: 'Tag photos by content' }))
    const dlg = await screen.findByRole('dialog', { name: 'Tag photos by content?' })
    expect(dlg).toHaveTextContent('does not identify anyone')
    fireEvent.click(within(dlg).getByRole('button', { name: 'Tag photos' }))
    await waitFor(() => expect(calls.some((c) => c.url === '/api/photos/content-tags/analyze')).toBe(true))
  })
  it('says how to install the detector when it is missing', async () => {
    mount({ ...SUMMARY, available: false })
    expect(await screen.findByText(/pip install nudenet/)).toBeInTheDocument()
  })
  it('shows nothing when there are no photos', async () => {
    const { container } = (mount({ ...SUMMARY, total_photos: 0, analysed_photos: 0, tags: [] }), { container: document.body })
    await waitFor(() => expect(container.querySelector('[aria-label="Content tags"]')).toBeNull())
  })
  it('shows suggested looks in their groups, filterable like the other tags, and says they can be wrong', async () => {
    mount()
    const colour = await screen.findByRole('group', { name: 'Hair colour' })
    expect(screen.getByText(/suggested, can be wrong/)).toBeInTheDocument()
    expect(within(screen.getByRole('group', { name: 'Hair length' })).getByRole('button', { name: /Long hair\s*12/ })).toBeInTheDocument()
    fireEvent.click(within(colour).getByRole('button', { name: /Redhead/ }))
    expect(usePhotoStore.getState().contentTags).toEqual(['hair_red'])
  })
  it('explains the look model first (size, what it guesses) and starts the looks job', async () => {
    const calls = mount()
    fireEvent.click(await screen.findByRole('button', { name: 'Suggest looks' }))
    const dlg = await screen.findByRole('dialog', { name: 'Suggest looks?' })
    expect(dlg).toHaveTextContent('155 MB'); expect(dlg).toHaveTextContent('hair colour is the weakest')
    fireEvent.click(within(dlg).getByRole('button', { name: 'Suggest looks' }))
    await waitFor(() => expect(calls.find((c) => c.url === '/api/photos/content-tags/analyze')?.body).toEqual({ kind: 'looks' }))
  })
})
