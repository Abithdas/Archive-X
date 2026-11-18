import { screen, within } from '@testing-library/react'
import { afterEach, describe, expect, it, vi } from 'vitest'
import { withRouter } from '@/test/withRouter'
import { OverviewPanel } from './panels'

const json = (b: unknown) => new Response(JSON.stringify(b), { status: 200 })
afterEach(() => vi.unstubAllGlobals())

function mount(jobs: unknown[]) {
  vi.stubGlobal('fetch', vi.fn(async (url: string) => (url === '/api/telegram-transfer/lanes' ? json({ ok: true, jobs, resources: {}, governor: {} }) : json({ ok: true }))))
  withRouter(<OverviewPanel go={() => undefined} />)
}

describe('Overview, uploads and downloads', () => {
  it('shows how fast files go up and come down, and each file moving', async () => {
    mount([
      { id: 'a', type: 'TELEGRAM_TRANSFER', status: 'RUNNING', progress: 50, metrics: { direction: 'upload', rate_bytes_per_second: 2_097_152, bytes_done: 5, bytes_total: 10, filename: 'film.mp4', eta_seconds: 90 } },
      { id: 'b', type: 'MEDIA_MANAGER', status: 'RUNNING', progress: 10, metrics: { direction: 'download', rate_bytes_per_second: 1_048_576, bytes_done: 1, bytes_total: 10, filename: 'clip.mp4' } },
    ])
    const up = await screen.findByTestId('traffic-upload')
    expect(await within(up).findByText('2.0 MB/s')).toBeInTheDocument()
    expect(within(up).getByText('1 file moving')).toBeInTheDocument()
    expect(await within(screen.getByTestId('traffic-download')).findByText('1.0 MB/s')).toBeInTheDocument()
    const list = await screen.findByRole('list', { name: 'Files moving now' })
    expect(within(list).getByText('film.mp4')).toBeInTheDocument()
    expect(within(list).getByText(/about 1 min 30 s left/)).toBeInTheDocument()
  })
  it('says nothing is moving when nothing is', async () => {
    mount([])
    const up = await screen.findByTestId('traffic-upload')
    expect(within(up).getByText('Nothing moving')).toBeInTheDocument()
    expect(screen.queryByRole('list', { name: 'Files moving now' })).toBeNull()
  })
})
