import { screen, fireEvent } from '@testing-library/react'
import { afterEach, describe, expect, it, vi } from 'vitest'
import { PerformerPage } from './PerformerPage'
import { withRouter } from '@/test/withRouter'

const video = { id: 5, source_channel: '-100', title: 'Scene 5', filename: 'f.mp4', size: 1, duration: 60, width: 1920, height: 1080, resolution_category: '1080p', thumbnail_id: 5, favorite: 0, upload_date: null, channel_title: 'Main', performers: ['Mia'], studios: [], tags: [], studio: '' }
const detail = (bio: string) => ({ performer: { id: 2, name: 'Mia Rivera', image: null, bio, profile: 'Born 1995', gender: 'female', favorite: false }, videos: [video] })
afterEach(() => vi.unstubAllGlobals())

describe('PerformerPage', () => {
  it('shows the profile and her videos, and collapses a long bio', async () => {
    vi.stubGlobal('fetch', vi.fn(async () => new Response(JSON.stringify(detail('x'.repeat(400))), { status: 200 })))
    withRouter(<PerformerPage id={2} />)
    expect(await screen.findByRole('heading', { level: 1, name: 'Mia Rivera' })).toBeInTheDocument()
    expect(screen.getByRole('article', { name: 'Scene 5' })).toBeInTheDocument()
    expect(screen.getByText('1 video')).toBeInTheDocument()
    fireEvent.click(screen.getByRole('button', { name: 'Read more' }))
    expect(screen.getByRole('button', { name: 'Show less' })).toBeInTheDocument()
  })
  it('says so when the performer does not exist', async () => {
    vi.stubGlobal('fetch', vi.fn(async () => new Response('{"detail":"Not found"}', { status: 404 })))
    withRouter(<PerformerPage id={9} />)
    expect(await screen.findByText('Performer not found.')).toBeInTheDocument()
    expect(screen.queryByRole('button', { name: 'Retry' })).not.toBeInTheDocument()
  })
})
