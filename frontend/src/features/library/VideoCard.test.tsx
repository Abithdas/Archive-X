import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { fireEvent, render as rtlRender, screen, waitFor } from '@testing-library/react'
import type { ReactElement } from 'react'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { useSelection } from './selection'
import { withRouter } from '@/test/withRouter'
import { SkeletonGroup } from '@/design/Skeleton'
import type { LibraryItem } from '@/api/types'
import { VideoCard, VideoCardSkeleton } from './VideoCard'

const render = (ui: ReactElement) => rtlRender(<QueryClientProvider client={new QueryClient()}>{ui}</QueryClientProvider>)
const item = (over: Partial<LibraryItem> = {}): LibraryItem => ({
  id: 1001, _cid: '-1001', media_id: 1, name: 'A medium length title', filename: 'f.mp4', size: 1, duration: 3725, width: 1920, height: 1080,
  resolution_category: '1080p', thumbnail_id: 1001, favorite: 0, upload_date: null, channel_title: 'Main Archive',
  performers: ['Ava Stone'], studios: ['Northlight'], tags: ['a', 'b', 'c', 'd', 'e'], studio: 'Northlight', ...over,
})

describe('VideoCard', () => {
  it('shows title, performers, studio and channel, duration and resolution', async () => {
    withRouter(<VideoCard item={item()} />)
    expect(await screen.findByRole('article', { name: 'A medium length title' })).toBeInTheDocument()
    expect(screen.getByText('Ava Stone')).toBeInTheDocument()
    expect(screen.getByText('Northlight · Main Archive')).toBeInTheDocument()
    expect(screen.getByText('1:02:05')).toBeInTheDocument()
    expect(screen.getByText('1080p')).toBeInTheDocument()
  })
  it('limits tags to two plus a count', async () => {
    withRouter(<VideoCard item={item()} />)
    expect(await screen.findByText('+3')).toBeInTheDocument()
    expect(screen.queryByText('e')).not.toBeInTheDocument()
  })
  it('falls back for missing performers and clamps long titles to two lines', async () => {
    withRouter(<VideoCard item={item({ performers: [], name: 'x '.repeat(200) })} />)
    expect(await screen.findByText('Unknown performer')).toBeInTheDocument()
    expect((await screen.findByRole('heading', { level: 3 })).className).toContain('line-clamp-2')
  })
  it('drops a broken thumbnail instead of showing a broken image', async () => {
    const { container } = withRouter(<VideoCard item={item()} />)
    await screen.findByRole('article')
    const img = container.querySelector('img')!
    expect(img.getAttribute('src')).toBe('/api/thumb/-1001/1001?r=f3')
    fireEvent.error(img)
    expect(container.querySelector('img')).toBeNull()
  })
})

describe('VideoCardSkeleton', () => {
  it('uses the same card shell and thumbnail aspect as the real card (cannot drift)', async () => {
    withRouter(<VideoCard item={item()} />)
    const real = (await screen.findByRole('article'))
    const sk = render(<VideoCardSkeleton />).container.firstElementChild!
    for (const cls of ['rounded-card', 'border-line', 'bg-surface']) {
      expect(real.className).toContain(cls)
      expect(sk.className).toContain(cls)
    }
    expect(real.querySelector('.aspect-video')).not.toBeNull()
    expect(sk.querySelector('.aspect-video')).not.toBeNull()
  })
  it('is hidden from assistive tech and announced once by its group', () => {
    render(
      <SkeletonGroup label="Loading videos">
        <VideoCardSkeleton />
        <VideoCardSkeleton index={1} />
      </SkeletonGroup>,
    )
    expect(screen.getByRole('status')).toHaveTextContent('Loading videos')
    expect(screen.getByRole('status')).toHaveAttribute('aria-busy', 'true')
  })
  it('animates with a transform-only sweep and honours reduced motion', () => {
    const { container } = render(<VideoCardSkeleton />)
    const block = container.querySelector('.after\\:animate-sk-sweep')!
    expect(block.className).toContain('motion-reduce:after:hidden')
    expect(block.className).toContain('after:-translate-x-full')
  })
})

describe('VideoCard in select mode', () => {
  beforeEach(() => useSelection.getState().exit())
  it('has no checkbox outside select mode', async () => {
    withRouter(<VideoCard item={item()} />)
    await screen.findByRole('article')
    expect(screen.queryByRole('checkbox')).toBeNull()
  })
  it('the checkbox picks exactly once per click (no double toggle)', async () => {
    useSelection.getState().enter()
    withRouter(<VideoCard item={item()} />)
    const box = await screen.findByRole('checkbox', { name: 'Select A medium length title' })
    fireEvent.click(box)
    expect(Object.keys(useSelection.getState().picked)).toEqual(['-1001:1001'])
    expect(box).toBeChecked()
    fireEvent.click(box)
    expect(useSelection.getState().picked).toEqual({})
  })
  it('clicking the card picks it instead of opening it', async () => {
    useSelection.getState().enter()
    withRouter(<VideoCard item={item()} />)
    const link = await screen.findByRole('link', { name: 'A medium length title' })
    fireEvent.click(link)
    expect(Object.keys(useSelection.getState().picked)).toEqual(['-1001:1001'])
    expect(screen.getByRole('article')).toHaveAttribute('data-picked', 'true')
    fireEvent.click(link)
    expect(useSelection.getState().picked).toEqual({})
  })
  it('the favourite button does not pick the card', async () => {
    useSelection.getState().enter()
    withRouter(<VideoCard item={item()} />)
    fireEvent.click(await screen.findByRole('button', { name: 'Add to favourites' }))
    expect(useSelection.getState().picked).toEqual({})
  })
})


describe('VideoCard hover preview', () => {
  const RealImage = globalThis.Image
  afterEach(() => { vi.unstubAllGlobals(); globalThis.Image = RealImage })
  const setup = (hover: boolean, manifest: Record<string, unknown>) => {
    const calls: string[] = []
    vi.stubGlobal('matchMedia', () => ({ matches: hover, addEventListener() {}, removeEventListener() {} }))
    vi.stubGlobal('fetch', vi.fn(async (url: string) => { calls.push(url); return new Response(JSON.stringify(url.includes('preview-manifest') ? manifest : { position: 1000, duration: 4000 }), { status: 200 }) }))
    globalThis.Image = class { onload?: () => void; decoding = ''; _s = ''; set src(v: string) { this._s = v; queueMicrotask(() => this.onload?.()) } get src() { return this._s } decode() { return Promise.resolve() } } as unknown as typeof Image
    withRouter(<VideoCard item={item({ duration: 3600 })} />)
    return calls
  }
  const READY = { frames_ready: true, frame_count: 36, revision: 'r1', frame_times: Array.from({ length: 36 }, (_, i) => 60 + i * 100) }
  const thumb = async () => {
    const card = await screen.findByRole('article', { name: 'A medium length title' })
    const t = card.querySelector('div.aspect-video') as HTMLElement
    t.getBoundingClientRect = () => ({ left: 0, top: 0, right: 360, bottom: 202, width: 360, height: 202, x: 0, y: 0, toJSON: () => ({}) })
    return { card, t }
  }
  // jsdom has no PointerEvent, so build the events by hand and give them a pointerType.
  const pointer = (el: Element, type: string, init: MouseEventInit = {}) => { const ev = new MouseEvent(type, { bubbles: true, ...init }); Object.defineProperty(ev, 'pointerType', { value: 'mouse' }); fireEvent(el, ev) }
  const mouse = (el: Element, x: number, y = 100) => pointer(el, 'pointermove', { clientX: x, clientY: y })

  it('plays the cached snapshots on hover and shows each one\'s time', async () => {
    setup(true, READY)
    const { card } = await thumb()
    mouse(card, 50)
    const frame = () => card.querySelector('img[aria-hidden="true"]')?.getAttribute('src')
    await waitFor(() => expect(frame()).toBe('/api/play/preview-frame/-1001/1001/0?v=r1'))
    expect(screen.getByText('1:00')).toBeInTheDocument()
    await waitFor(() => expect(frame()).toBe('/api/play/preview-frame/-1001/1001/1?v=r1'), { timeout: 1500 })
    expect(screen.getByText('2:40')).toBeInTheDocument()
  })
  it('scrubs with the pointer and stops when it leaves', async () => {
    setup(true, READY)
    const { card } = await thumb()
    mouse(card, 50)
    await waitFor(() => expect(card.querySelector('img[aria-hidden="true"]')).not.toBeNull())
    mouse(card, 300)
    await waitFor(() => expect(card.querySelector('img[aria-hidden="true"]')?.getAttribute('src')).toBe('/api/play/preview-frame/-1001/1001/30?v=r1'))
    expect(screen.getByRole('progressbar', { name: 'Preview frame position' })).toHaveAttribute('aria-valuenow', '83')
    pointer(card, 'pointerout', { relatedTarget: null })
    await waitFor(() => expect(card.querySelector('img[aria-hidden="true"]')).toBeNull())
    expect(screen.getByText('1:00:00')).toBeInTheDocument()
  })
  it('keeps the normal thumbnail when the snapshots are not all cached, and asks Telegram for nothing', async () => {
    const calls = setup(true, { frames_ready: false, frame_count: 12 })
    const { card } = await thumb()
    mouse(card, 50)
    await waitFor(() => expect(calls.some((u) => u.includes('preview-manifest'))).toBe(true))
    await new Promise((r) => setTimeout(r, 400))
    expect(card.querySelector('img[aria-hidden="true"]')).toBeNull()
    expect(calls.some((u) => u.includes('preview-frame'))).toBe(false)
  })
  it('does nothing on a touch screen (no hover)', async () => {
    const calls = setup(false, READY)
    const { card } = await thumb()
    mouse(card, 50)
    await new Promise((r) => setTimeout(r, 200))
    expect(calls).toHaveLength(0)
  })
  it('shows how far you have watched, from the saved position', async () => {
    setup(true, READY)
    const { card } = await thumb()
    mouse(card, 50)
    await waitFor(() => expect(card.querySelector('[role="progressbar"]')).not.toBeNull())
  })
})
