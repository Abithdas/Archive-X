import { act, fireEvent, screen, waitFor, within } from '@testing-library/react'
import { afterEach, beforeAll, beforeEach, describe, expect, it, vi } from 'vitest'
import { withRouter } from '@/test/withRouter'
import { PhotosPage } from './PhotosPage'
import { usePhotoStore } from './store'
import { clearImageCache } from './imageCache'

const toast = vi.hoisted(() => Object.assign(vi.fn(), { error: vi.fn() }))
vi.mock('sonner', () => ({ toast, Toaster: () => null }))
// Images "load" at once in these tests (jsdom never loads any), so the viewer can show a photo.
beforeAll(() => {
  globalThis.Image = class { onload?: () => void; onerror?: () => void; decoding = ''; _src = ''
    set src(v: string) { this._src = v; queueMicrotask(() => this.onload?.()) }
    get src() { return this._src }
    decode() { return Promise.resolve() } } as unknown as typeof Image
  HTMLDialogElement.prototype.showModal = function () { this.setAttribute('open', '') }
  HTMLDialogElement.prototype.close = function () { this.removeAttribute('open') }
})
beforeEach(() => { usePhotoStore.setState({ selected: [], sort: 'date_desc', favoritesOnly: false, favorites: new Set() }) })
afterEach(() => { vi.unstubAllGlobals(); toast.mockClear(); toast.error.mockClear() })

const json = (b: unknown, code = 200) => new Response(JSON.stringify(b), { status: code })
const photo = (id: number, extra: Partial<Record<string, unknown>> = {}) => ({ id, channel_id: '-1001', message_id: id, filename: `IMG_${id}.jpg`, size: 2048, width: 1200, height: 800, message_date: '2026-01-05T10:00:00', channel_title: 'Main', thumbnail_url: `/t/${id}`, preview_url: `/p/${id}`, image_url: `/i/${id}`, ...extra })
const CH = [{ telegram_id: '-1001', title: 'Main' }, { telegram_id: '-1002', title: 'Alpha' }, { telegram_id: '-1003', title: 'Never scanned' }]
const PH = [{ telegram_id: '-1001', photo_count: 3, photo_scan_status: 'COMPLETE' }, { telegram_id: '-1002', photo_count: 1, photo_scan_status: 'COMPLETE' }, { telegram_id: '-1003', photo_count: 0, photo_scan_status: 'NEVER' }]
type Call = { url: string; method: string; body?: Record<string, unknown> }
const mount = (over: (u: string, i?: RequestInit) => Response | undefined = () => undefined, items = [photo(1), photo(2), photo(3)]) => {
  const calls: Call[] = []
  vi.stubGlobal('fetch', vi.fn(async (url: string, init?: RequestInit) => {
    calls.push({ url, method: init?.method ?? 'GET', body: init?.body ? JSON.parse(String(init.body)) : undefined })
    const o = over(url, init); if (o) return o
    if (url === '/api/source-channels') return json(CH)
    if (url === '/api/photos/channels') return json(PH)
    if (url.startsWith('/api/photos/faces/groups')) return json({ ok: true, groups: [], analysis: { total_photos: 0 } })
    if (url.startsWith('/api/photos?')) return json({ ok: true, items, total: items.length, page: 0, per: 48 })
    if (url.endsWith('/delete')) return json({ ok: true })
    return json({})
  }))
  withRouter(<PhotosPage />)
  return calls
}
const photoCalls = (c: Call[]) => c.filter((x) => x.url.startsWith('/api/photos?'))
const pickMain = async () => fireEvent.click(await screen.findByRole('button', { name: /^Main/ }))

describe('PhotosPage', () => {
  it('lists only scanned channels and asks for a selection first', async () => {
    const calls = mount()
    expect(await screen.findByRole('button', { name: /^Main/ })).toBeInTheDocument()
    expect(screen.getByRole('button', { name: /^Alpha/ })).toBeInTheDocument()
    expect(screen.queryByRole('button', { name: /Never scanned/ })).not.toBeInTheDocument()
    expect(screen.getByText('2 scanned channels')).toBeInTheDocument()
    expect(screen.getByText(/Select channel cards to browse indexed photos/)).toBeInTheDocument()
    expect(photoCalls(calls)).toHaveLength(0)
  })

  it('loads the selected channels\' photos with the sort and search the server expects', async () => {
    const calls = mount()
    await pickMain()
    expect(await screen.findByRole('article', { name: 'IMG_1.jpg' })).toBeInTheDocument()
    expect(photoCalls(calls)[0]!.url).toBe('/api/photos?page=0&per=48&channels=-1001&q=&sort=date_desc')
    expect(screen.getByText('3 images · 1 channel')).toBeInTheDocument()
    fireEvent.change(screen.getByLabelText('Sort photos'), { target: { value: 'size_desc' } })
    await waitFor(() => expect(photoCalls(calls).at(-1)!.url).toContain('sort=size_desc'))
    fireEvent.click(screen.getByRole('button', { name: /^All/ }))
    await waitFor(() => expect(photoCalls(calls).map((c) => c.url)).toContain('/api/photos?page=0&per=48&channels=-1002%2C-1001&q=&sort=size_desc'))
  })

  it('shows the server\'s reason when photos cannot be loaded, and can retry', async () => {
    mount((u) => (u.startsWith('/api/photos?') ? json({ detail: 'Library locked' }, 500) : undefined))
    await pickMain()
    expect(await screen.findByText('Library locked')).toBeInTheDocument()
    expect(screen.getByRole('button', { name: 'Retry' })).toBeInTheDocument()
  })

  it('explains an empty index', async () => {
    mount(() => undefined, [])
    await pickMain()
    expect(await screen.findByText('No photos indexed in selected channels yet.')).toBeInTheDocument()
  })

  it('keeps favourites in this browser and filters by them using photo ids', async () => {
    const calls = mount()
    await pickMain()
    fireEvent.click(await screen.findByRole('button', { name: 'Open IMG_2.jpg' }))
    const dlg = await screen.findByRole('dialog', { name: 'Photo viewer' })
    fireEvent.click(within(dlg).getByRole('button', { name: 'Add to favourites' }))
    expect(JSON.parse(localStorage.getItem('archive-x-photo-favorites-v1') ?? '[]')).toEqual(['2'])
    expect(within(dlg).getByRole('button', { name: 'Remove from favourites' })).toHaveAttribute('aria-pressed', 'true')
    fireEvent.click(within(dlg).getByRole('button', { name: 'Close photo viewer' }))
    fireEvent.click(screen.getByRole('button', { name: /Favourites/ }))
    await waitFor(() => expect(photoCalls(calls).at(-1)!.url).toContain('favorites=1&favorite_ids=2'))
  })

  it('removes a photo only after confirmation, with the right scope', async () => {
    const calls = mount()
    await pickMain()
    const card = await screen.findByRole('article', { name: 'IMG_1.jpg' })
    fireEvent.click(within(card).getByRole('menuitem', { name: 'Remove from Archive X', hidden: true }))
    const ask = await screen.findByRole('dialog', { name: 'Remove photo from Archive X?' })
    fireEvent.click(within(ask).getByRole('button', { name: 'Cancel' }))
    expect(calls.filter((c) => c.method === 'POST')).toHaveLength(0)
    fireEvent.click(within(card).getByRole('menuitem', { name: 'Delete from Archive X and Telegram', hidden: true }))
    fireEvent.click(within(await screen.findByRole('dialog', { name: 'Delete photo and Telegram message?' })).getByRole('button', { name: 'Delete from both' }))
    await waitFor(() => expect(calls.filter((c) => c.method === 'POST')).toEqual([{ url: '/api/photos/-1001/1/delete', method: 'POST', body: { scope: 'archive_and_telegram' } }]))
    await waitFor(() => expect(toast).toHaveBeenCalledWith('Photo deleted from Archive X and Telegram.'))
  })
})

describe('PhotoViewer', () => {
  const open = async (i = 1) => {
    mount()
    await pickMain()
    fireEvent.click(await screen.findByRole('button', { name: `Open IMG_${i}.jpg` }))
    return screen.findByRole('dialog', { name: 'Photo viewer' })
  }
  it('shows the details, counter and preview, and steps with buttons and keys, stopping at the ends', async () => {
    const dlg = await open(1)
    expect(within(dlg).getByText('1200 × 800')).toBeInTheDocument(); expect(within(dlg).getByText('1.0 MP')).toBeInTheDocument(); expect(within(dlg).getByText('2 KB')).toBeInTheDocument()
    expect(within(dlg).getByText('1 / 3')).toBeInTheDocument()
    expect(await within(dlg).findByAltText('IMG_1.jpg')).toHaveAttribute('src', '/i/1')
    expect(within(dlg).getByRole('button', { name: 'Previous photo' })).toBeDisabled()
    fireEvent.click(within(dlg).getByRole('button', { name: 'Next photo' }))
    expect(within(dlg).getByText('2 / 3')).toBeInTheDocument()
    fireEvent.keyDown(document, { key: 'ArrowRight' }); expect(within(dlg).getByText('3 / 3')).toBeInTheDocument()
    fireEvent.keyDown(document, { key: 'ArrowRight' }); expect(within(dlg).getByText('3 / 3')).toBeInTheDocument()
    fireEvent.keyDown(document, { key: 'ArrowLeft' }); expect(within(dlg).getByText('2 / 3')).toBeInTheDocument()
  })
  it('keeps showing the current photo until the next one is loaded, with no stand-in image, then swaps', async () => {
    const dlg = await open(1)
    expect(await within(dlg).findByAltText('IMG_1.jpg')).toHaveAttribute('src', '/i/1')
    clearImageCache()   // the neighbours were fetched ahead; forget them so this test controls when they load
    const held: Array<() => void> = []
    const Loads = globalThis.Image
    globalThis.Image = class { onload?: () => void; onerror?: () => void; decoding = ''; _src = ''
      set src(v: string) { this._src = v; if (/\/(i|p)\/2$/.test(v)) held.push(() => this.onload?.()); else queueMicrotask(() => this.onload?.()) }
      get src() { return this._src }
      decode() { return Promise.resolve() } } as unknown as typeof Image
    try {
      fireEvent.click(within(dlg).getByRole('button', { name: 'Next photo' }))
      expect(within(dlg).getByText('2 / 3')).toBeInTheDocument()
      expect(within(dlg).getByAltText('IMG_1.jpg')).toHaveAttribute('src', '/i/1')
      expect([...dlg.querySelectorAll('[role="img"] img')].map((i) => i.getAttribute('src'))).toEqual(['/i/1'])
      expect(within(dlg).queryByRole('status', { name: 'Loading selected photo' })).not.toBeInTheDocument()
      await waitFor(() => expect(held.length).toBeGreaterThan(0))
      held.forEach((release) => release())
      expect(await within(dlg).findByAltText('IMG_2.jpg')).toBeInTheDocument()
    } finally { globalThis.Image = Loads }
  })
  it('jumps from the filmstrip', async () => {
    const dlg = await open(1)
    fireEvent.click(within(dlg).getByRole('button', { name: 'Photo 3: IMG_3.jpg' }))
    expect(within(dlg).getByText('3 / 3')).toBeInTheDocument()
    expect(within(dlg).getByRole('button', { name: 'Photo 3: IMG_3.jpg' })).toHaveAttribute('aria-current', 'true')
  })
  it('zooms in steps, fits, and rotates', async () => {
    const dlg = await open(1)
    await within(dlg).findByAltText('IMG_1.jpg')
    const level = () => within(dlg).getByRole('status', { name: 'Zoom level' }).textContent
    expect(level()).toBe('100%')
    fireEvent.click(within(dlg).getByRole('button', { name: 'Zoom in' })); expect(level()).toBe('125%')
    fireEvent.keyDown(document, { key: '+' }); expect(level()).toBe('156%')
    fireEvent.keyDown(document, { key: '-' }); fireEvent.keyDown(document, { key: '-' }); fireEvent.keyDown(document, { key: '-' }); expect(level()).toBe('100%')
    fireEvent.click(within(dlg).getByRole('button', { name: 'Zoom in' })); fireEvent.click(within(dlg).getByRole('button', { name: 'Fit image' })); expect(level()).toBe('100%')
    fireEvent.click(within(dlg).getByRole('button', { name: 'Rotate clockwise' }))
    expect(within(dlg).getByAltText('IMG_1.jpg').getAttribute('style')).toContain('rotate(90deg)')
    fireEvent.keyDown(document, { key: 'R', shiftKey: true }); expect(within(dlg).getByAltText('IMG_1.jpg').getAttribute('style')).toContain('rotate(0deg)')
  })
  it('plays a slideshow that wraps around, and closes with its button', async () => {
    vi.useFakeTimers()
    try {
      mount(); 
      await act(async () => { await vi.advanceTimersByTimeAsync(10) })
      await act(async () => { fireEvent.click(screen.getByRole('button', { name: /^Main/ })); await vi.advanceTimersByTimeAsync(50) })
      await act(async () => { fireEvent.click(screen.getByRole('button', { name: 'Open IMG_3.jpg' })) })
      const dlg = screen.getByRole('dialog', { name: 'Photo viewer' })
      fireEvent.click(within(dlg).getByRole('button', { name: 'Play slideshow' }))
      await act(async () => { await vi.advanceTimersByTimeAsync(3100) })
      expect(within(dlg).getByText('1 / 3')).toBeInTheDocument()
      fireEvent.keyDown(document, { key: ' ' })
      await act(async () => { await vi.advanceTimersByTimeAsync(6200) })
      expect(within(dlg).getByText('1 / 3')).toBeInTheDocument()
      fireEvent.click(within(dlg).getByRole('button', { name: 'Close photo viewer' }))
      expect(screen.queryByRole('dialog', { name: 'Photo viewer' })).not.toBeInTheDocument()
    } finally { vi.useRealTimers() }
  })
})
