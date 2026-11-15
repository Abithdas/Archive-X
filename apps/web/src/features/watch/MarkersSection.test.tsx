import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { fireEvent, render, screen } from '@testing-library/react'
import { afterEach, describe, expect, it, vi } from 'vitest'
import { MarkersSection } from './MarkersSection'

afterEach(() => vi.unstubAllGlobals())
const json = (b: unknown, code = 200) => new Response(JSON.stringify(b), { status: code })
type Call = { url: string; method: string; body: Record<string, unknown> | undefined }
const mount = (saved: unknown, sync: (n: number) => Response = () => json({ ok: true, status: 'NO_MARKERS', markers: [] }), mediaId: number | undefined = 5) => {
  const calls: Call[] = []
  let n = 0
  vi.stubGlobal('fetch', vi.fn(async (url: string, init?: RequestInit) => {
    calls.push({ url, method: init?.method ?? 'GET', body: init?.body ? (JSON.parse(String(init.body)) as Record<string, unknown>) : undefined })
    if (url.endsWith('/markers/sync')) return sync(++n)
    if (url.endsWith('/markers')) return typeof saved === 'number' ? json({ detail: 'x' }, saved) : json(saved)
    return json({})
  }))
  const onSeek = vi.fn()
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } })
  render(<QueryClientProvider client={qc}><MarkersSection mediaId={mediaId} onSeek={onSeek} /></QueryClientProvider>)
  return { calls, onSeek, qc }
}
const syncs = (c: Call[]) => c.filter((x) => x.url.endsWith('/markers/sync'))

describe('Scene markers', () => {
  it('shows a chip per marker, sorted, with its tag, and jumps to it on click', async () => {
    const { onSeek, calls } = mount([{ id: 2, position: 125, title: 'Second', tag: 'pov', note: 'n' }, { id: 1, position: 5, title: 'First' }])
    const first = await screen.findByRole('button', { name: 'Go to 0:05: First' })
    const second = screen.getByRole('button', { name: 'Go to 2:05: Second' })
    expect(second).toHaveAttribute('title', 'Go to 2:05 · n')
    expect(second).toHaveTextContent('#pov')
    expect(first.compareDocumentPosition(second) & Node.DOCUMENT_POSITION_FOLLOWING).toBeTruthy()   // First comes before Second
    fireEvent.click(second)
    expect(onSeek).toHaveBeenCalledWith(125)
    expect(screen.getByRole('heading', { name: 'Scene markers (2)' })).toBeInTheDocument()
    expect(syncs(calls)).toHaveLength(0)   // saved markers: never bother the provider
  })

  it('collapses and expands', async () => {
    mount([{ id: 1, position: 5, title: 'First' }])
    await screen.findByRole('button', { name: /Go to 0:05/ })
    const head = screen.getByRole('button', { name: /Scene markers/ })
    fireEvent.click(head)
    expect(head).toHaveAttribute('aria-expanded', 'false')
    // folded: the chips stay in the page for the fold animation but can no longer be reached
    expect(screen.getByRole('button', { name: /Go to 0:05/, hidden: true }).closest('[inert]')).not.toBeNull()
    fireEvent.click(head)
    expect(head).toHaveAttribute('aria-expanded', 'true')
    expect(screen.getByRole('button', { name: /Go to 0:05/ }).closest('[inert]')).toBeNull()
  })

  it('with nothing saved, asks the provider once without forcing, and says why there are no markers', async () => {
    const { calls } = mount([], () => json({ ok: true, status: 'NO_PROVIDER_TOKEN', markers: [] }))
    expect(await screen.findByText(/Configure the ThePornDB provider token/)).toBeInTheDocument()
    expect(syncs(calls)).toHaveLength(1)
    expect(syncs(calls)[0]!.body).toEqual({ force: false })
    expect(screen.getByRole('heading', { name: 'Scene markers' })).toBeInTheDocument()
  })

  it('shows markers the provider returned and updates the shared list the timeline reads', async () => {
    const { qc } = mount([], () => json({ ok: true, status: 'SYNCED', markers: [{ id: 9, position: 60, title: 'From provider' }] }))
    expect(await screen.findByRole('button', { name: 'Go to 1:00: From provider' })).toBeInTheDocument()
    expect(qc.getQueryData(['markers', 5])).toEqual([{ id: 9, position: 60, title: 'From provider' }])
  })

  it('Refresh provider markers forces a re-check, shows progress, then the result', async () => {
    let release: (r: Response) => void = () => {}
    const { calls } = mount([], (n) => (n === 1 ? json({ ok: true, status: 'NO_TPDB_SCENE', markers: [] }) : (undefined as never)))
    await screen.findByText('No matching provider scene was found.')
    vi.stubGlobal('fetch', vi.fn(async (url: string, init?: RequestInit) => {
      calls.push({ url, method: init?.method ?? 'GET', body: init?.body ? (JSON.parse(String(init.body)) as Record<string, unknown>) : undefined })
      return new Promise<Response>((r) => { release = r })
    }))
    fireEvent.click(screen.getByRole('button', { name: 'Refresh provider markers' }))
    expect(await screen.findByRole('button', { name: 'Checking provider…' })).toBeDisabled()
    expect(syncs(calls).at(-1)!.body).toEqual({ force: true })
    release(json({ ok: true, status: 'SYNCED', markers: [{ id: 1, position: 30, title: 'Found' }] }))
    expect(await screen.findByRole('button', { name: 'Go to 0:30: Found' })).toBeInTheDocument()
  })

  it('says so when the quiet first check fails, and still offers a retry', async () => {
    mount([], () => json({ detail: 'Provider marker sync failed' }, 502))
    expect(await screen.findByText('Saved markers are empty and provider sync failed. Use Refresh provider markers to retry.')).toBeInTheDocument()
    expect(screen.getByRole('button', { name: 'Refresh provider markers' })).toBeInTheDocument()
  })

  it('shows the server reason when a forced refresh fails', async () => {
    const { calls } = mount([], (n) => (n === 1 ? json({ ok: true, status: 'NO_MARKERS', markers: [] }) : json({ detail: 'Provider marker sync failed' }, 502)))
    await screen.findByText('No markers yet.')
    fireEvent.click(screen.getByRole('button', { name: 'Refresh provider markers' }))
    expect(await screen.findByText('Provider marker sync failed')).toBeInTheDocument()
    expect(syncs(calls)).toHaveLength(2)
  })

  it('does not ask the provider again for the same video after a result', async () => {
    const { calls } = mount([], () => json({ ok: true, status: 'NO_MARKERS', markers: [] }))
    await screen.findByText('No markers yet.')
    await new Promise((r) => setTimeout(r, 50))
    expect(syncs(calls)).toHaveLength(1)
  })

  it('explains when the video is not indexed yet', async () => {
    mount([], undefined, 0)
    expect(await screen.findByText('Markers are available after catalogue indexing.')).toBeInTheDocument()
  })
  it('says when markers cannot be loaded', async () => {
    mount(500)
    expect(await screen.findByText('Markers unavailable. Check the Operations log or retry.')).toBeInTheDocument()
  })
})
