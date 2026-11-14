import { screen, waitFor } from '@testing-library/react'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { withRouter } from '@/test/withRouter'
import { useLibraryStore } from './store'
import { LibraryPage } from './LibraryPage'

vi.mock('sonner', () => ({ toast: Object.assign(vi.fn(), { error: vi.fn(), loading: vi.fn(), dismiss: vi.fn() }), Toaster: () => null }))
afterEach(() => vi.unstubAllGlobals())
beforeEach(() => { try { localStorage.clear() } catch { /* no storage */ } useLibraryStore.setState({ channels: [], page: 0 }) })

const json = (b: unknown) => new Response(JSON.stringify(b), { status: 200 })
const CHANNELS = [
  { id: 1, telegram_id: '-1001', title: 'Main', username: null, image: null, role: null, video_count: 1, known_count: 1 },
  { id: 2, telegram_id: '-1002', title: 'Second', username: null, image: null, role: null, video_count: 1, known_count: 1 },
]
const item = { id: 70, _cid: '-1001', media_id: 7, name: 'First scene', filename: 'f.mp4', size: 1, duration: 60, width: 1920, height: 1080, resolution_category: '1080p', thumbnail_id: 0, favorite: 0, upload_date: '2026-01-01', channel_title: 'Main', performers: [], studios: [], tags: [], studio: '' }

describe('LibraryPage', () => {
  it('shows the videos of every channel on a fresh start, without picking a channel first', async () => {
    const urls: string[] = []
    vi.stubGlobal('fetch', vi.fn(async (url: string) => {
      urls.push(url)
      if (url === '/api/channels') return json(CHANNELS)
      if (url.startsWith('/api/library?')) return json({ total: 1, page: 0, per: 24, items: [item] })
      return json({})
    }))
    withRouter(<LibraryPage />)
    expect(await screen.findByRole('article', { name: 'First scene' })).toBeInTheDocument()
    const asked = urls.find((u) => u.startsWith('/api/library?')) ?? ''
    expect(new URLSearchParams(asked.split('?')[1]).get('channels')).toBe('-1001,-1002')
    await waitFor(() => expect(screen.getByText(/1 videos? · 2 channels/)).toBeInTheDocument())
  })
})
