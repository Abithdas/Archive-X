import { fireEvent, render, screen, waitFor } from '@testing-library/react'
import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { afterEach, describe, expect, it, vi } from 'vitest'
import { Issues, lookedAt, type Digest } from './Issues'

vi.mock('sonner', () => ({ toast: Object.assign(vi.fn(), { error: vi.fn() }), Toaster: () => null }))
afterEach(() => vi.unstubAllGlobals())

const digest: Digest = {
  generated_at: '2026-11-18T10:00:00Z', looked_at: { log_events: 1200, errors: 3, warnings: 41 }, ok: false,
  issues: [{ id: 'ffmpeg', area: 'Media tools', severity: 'high', title: 'ffmpeg did not run', count: 7, ago: '5 min ago', cause: 'ffmpeg is missing from the install.', check: 'Settings, Overview, Dependencies.', evidence: ['ffmpeg not found on PATH'] }],
}
const mount = (d: Digest = digest, text = 'Archive X issue digest …', onExplain?: () => void) => {
  vi.stubGlobal('fetch', vi.fn(async () => new Response(JSON.stringify({ ok: true, digest: d, text }), { status: 200 })))
  render(<QueryClientProvider client={new QueryClient()}><Issues onExplain={onExplain} /></QueryClientProvider>)
}

describe('Issues', () => {
  it('lists what is wrong, worst first, with the cause, what to check and the real log line', async () => {
    mount()
    const list = await screen.findByRole('list', { name: 'Issues' })
    expect(list).toHaveTextContent('ffmpeg did not run')
    expect(list).toHaveTextContent('×7 · 5 min ago')
    expect(list).toHaveTextContent('ffmpeg is missing from the install.')
    expect(list).toHaveTextContent('Check: Settings, Overview, Dependencies.')
    expect(list).toHaveTextContent('ffmpeg not found on PATH')
    expect(screen.getByText(lookedAt(digest))).toBeInTheDocument()
  })
  it('says so when nothing is wrong', async () => {
    mount({ ...digest, ok: true, issues: [] })
    expect(await screen.findByText(/No problems found/)).toBeInTheDocument()
  })
  it('copies the text for a model, and offers the local model only when there is one', async () => {
    const write = vi.fn(async () => undefined)
    Object.defineProperty(navigator, 'clipboard', { value: { writeText: write }, configurable: true })
    const explain = vi.fn()
    mount(digest, 'THE TEXT', explain)
    await screen.findByRole('list', { name: 'Issues' })
    fireEvent.click(screen.getByRole('button', { name: 'Copy as text' }))
    await waitFor(() => expect(write).toHaveBeenCalledWith('THE TEXT'))
    fireEvent.click(screen.getByRole('button', { name: 'Explain with the local model' }))
    expect(explain).toHaveBeenCalled()
  })
  it('has no model button without a model', async () => {
    mount()
    await screen.findByRole('list', { name: 'Issues' })
    expect(screen.queryByRole('button', { name: 'Explain with the local model' })).toBeNull()
  })
})
