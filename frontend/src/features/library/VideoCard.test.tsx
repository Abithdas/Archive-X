import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { fireEvent, render as rtlRender, screen } from '@testing-library/react'
import type { ReactElement } from 'react'
import { beforeEach, describe, expect, it } from 'vitest'
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
