import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { fireEvent, render, screen, within } from '@testing-library/react'
import { afterEach, describe, expect, it, vi } from 'vitest'
import { PHOTOS_EMPTY, PerformerPhotos, photosHeading } from './PerformerPhotos'

vi.mock('@/features/photos/imageCache', () => ({
  loadImage: () => Promise.resolve({ naturalWidth: 800, naturalHeight: 600 }),
  firstLoaded: (urls: string[]) => Promise.resolve({ url: urls[0], img: { naturalWidth: 800, naturalHeight: 600 } }),
}))
HTMLDialogElement.prototype.showModal ||= function (this: HTMLDialogElement) { this.setAttribute('open', '') }
afterEach(() => vi.unstubAllGlobals())

const photo = (i: number) => ({ id: i, channel_id: '-100', message_id: i, filename: `p${i}.jpg`, size: 1000, width: 800, height: 600, thumbnail_url: `/t${i}.jpg`, preview_url: `/p${i}.jpg`, image_url: `/f${i}.jpg` })
function mount(items: unknown[], total = items.length, calls: string[] = []) {
  vi.stubGlobal('fetch', vi.fn(async (url: string) => {
    calls.push(String(url))
    return new Response(JSON.stringify({ ok: true, items, total, page: 0, per: 48 }), { status: 200, headers: { 'content-type': 'application/json' } })
  }))
  return render(<QueryClientProvider client={new QueryClient()}><PerformerPhotos id={7} name="Mia" /></QueryClientProvider>)
}

describe('photosHeading', () => {
  it('says how many, and how many are shown when there are more', () => {
    expect(photosHeading(0, 0)).toBe('In your photos')
    expect(photosHeading(3, 3)).toBe('In your photos · 3')
    expect(photosHeading(48, 1200)).toBe('In your photos · 1,200 (48 shown)')
  })
})

describe('PerformerPhotos', () => {
  it('lists the photos in which this performer was recognised, asking for exactly that', async () => {
    const calls: string[] = []
    mount([photo(1), photo(2)], 2, calls)
    expect(await screen.findByRole('heading', { name: 'In your photos · 2' })).toBeInTheDocument()
    expect(screen.getAllByRole('button', { name: /Open photo/ })).toHaveLength(2)
    expect(calls[0]).toContain('/api/photos?')
    expect(calls[0]).toContain('face_performer_id=7')
  })
  it('opens a photo in the photo viewer', async () => {
    mount([photo(1), photo(2)])
    fireEvent.click(await screen.findByRole('button', { name: 'Open photo: p2.jpg' }))
    const dlg = await screen.findByRole('dialog', { name: 'Photo viewer' })
    expect(within(dlg).getByText('2 / 2')).toBeInTheDocument()
  })
  it('explains what to do when none are matched yet', async () => {
    mount([])
    expect(await screen.findByText(PHOTOS_EMPTY)).toBeInTheDocument()
  })
})
