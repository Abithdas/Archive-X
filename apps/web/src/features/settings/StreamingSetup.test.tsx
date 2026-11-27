import { fireEvent, screen, waitFor } from '@testing-library/react'
import { afterEach, describe, expect, it, vi } from 'vitest'
import { StreamingActions } from './StreamingSetup'
import { withRouter } from '@/test/withRouter'

vi.mock('sonner', () => ({ toast: Object.assign(vi.fn(), { error: vi.fn() }), Toaster: () => null }))
const json = (b: unknown) => new Response(JSON.stringify(b), { status: 200, headers: { 'content-type': 'application/json' } })
afterEach(() => vi.unstubAllGlobals())

function serve(info: Record<string, unknown>) {
  const calls: string[] = []
  vi.stubGlobal('fetch', vi.fn(async (url: string, init?: RequestInit) => {
    calls.push(`${init?.method ?? 'GET'} ${url}`)
    if (url === '/api/screen/streaming-server') return json({ url: 'http://127.0.0.1:11470', up: false, installed: false, ready: false, download: 'https://example.test/get', setup: { phase: 'idle' }, ...info })
    return json({ ok: true, up: true })
  }))
  return calls
}

describe('StreamingActions', () => {
  it('offers Set it up when nothing is installed and starts the background setup', async () => {
    const calls = serve({})
    withRouter(<StreamingActions />)
    fireEvent.click(await screen.findByRole('button', { name: 'Set it up' }))
    await waitFor(() => expect(calls).toContain('POST /api/screen/streaming-server/setup'))
    expect(screen.getByRole('link', { name: "Or install Stremio's own" })).toHaveAttribute('href', 'https://example.test/get')
  })
  it('shows the progress while the setup runs, and the reason when it failed', async () => {
    serve({ setup: { phase: 'working', message: 'Downloading Node.js…', percent: 40 } })
    withRouter(<StreamingActions />)
    expect(await screen.findByText(/Downloading Node.js… 40%/)).toBeInTheDocument()
    expect(screen.getByRole('button', { name: 'Setting up…' })).toBeDisabled()
  })
  it('shows why a setup failed and offers to try again', async () => {
    serve({ setup: { phase: 'failed', error: 'The Node.js download did not match its published checksum, so it was not used.' } })
    withRouter(<StreamingActions />)
    expect(await screen.findByRole('alert')).toHaveTextContent('did not match its published checksum')
    expect(screen.getByRole('button', { name: 'Try the setup again' })).toBeInTheDocument()
  })
  it('offers Start it once it is set up, and nothing at all when the server is running', async () => {
    const calls = serve({ ready: true })
    const { unmount } = withRouter(<StreamingActions />) as unknown as { unmount: () => void }
    fireEvent.click(await screen.findByRole('button', { name: 'Start it' }))
    await waitFor(() => expect(calls).toContain('POST /api/screen/streaming-server/start'))
    unmount?.()
  })
  it('renders nothing while the server answers', async () => {
    serve({ up: true })
    withRouter(<StreamingActions />)
    await waitFor(() => expect(screen.queryByRole('button')).toBeNull())
  })
})
