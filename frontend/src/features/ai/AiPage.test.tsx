import { fireEvent, screen, waitFor } from '@testing-library/react'
import { afterEach, describe, expect, it, vi } from 'vitest'
import { withRouter } from '@/test/withRouter'
import { AiPage } from './AiPage'

vi.mock('sonner', () => ({ toast: Object.assign(vi.fn(), { error: vi.fn() }), Toaster: () => null }))
afterEach(() => vi.unstubAllGlobals())
const json = (b: unknown, code = 200) => new Response(JSON.stringify(b), { status: code })
const STATUS = { configured: true, provider: 'llama.cpp', model: 'Qwen3', runtime: { runtime: { path: '/rt', exists: true }, model: { path: '/m.gguf', exists: true }, server: { endpoint: 'http://x/v1', reachable: true, context: 8192, gpu_layers: 2 }, ready: true } }
type Call = { url: string; method: string; body?: Record<string, unknown> }
const mount = (over: (u: string, i?: RequestInit) => Response | undefined = () => undefined, status: unknown = STATUS, messages: unknown[] = []) => {
  const calls: Call[] = []
  vi.stubGlobal('fetch', vi.fn(async (url: string, init?: RequestInit) => {
    calls.push({ url, method: init?.method ?? 'GET', body: init?.body ? JSON.parse(String(init.body)) : undefined })
    const o = over(url, init); if (o) return o
    if (url === '/api/ai/status') return json(status)
    if (url === '/api/ai/history') return json({ messages })
    if (url === '/api/ai/tools') return json({ local_only: true })
    if (url === '/api/ai/chat') return json({ text: 'Answer text', model: 'Qwen3' })
    return json({ ok: true })
  }))
  withRouter(<AiPage />)
  return calls
}
const posts = (c: Call[], url: string) => c.filter((x) => x.method === 'POST' && x.url === url)

describe('AiPage', () => {
  it('shows the model state, tools state and previous history', async () => {
    mount(() => undefined, STATUS, [{ role: 'user', content: 'Earlier question', created_at: 't1' }, { role: 'assistant', content: 'Earlier answer', created_at: 't2' }])
    expect(await screen.findByText('Local model ready')).toBeInTheDocument()
    expect(screen.getByText('llama.cpp · Qwen3')).toBeInTheDocument()
    expect(await screen.findByText('Read-only Archive X tools enabled.')).toBeInTheDocument()
    expect(await screen.findByText('Earlier answer')).toBeInTheDocument()
  })
  it('says when the local model is not configured, and when the service is unreachable', async () => {
    mount(() => undefined, { configured: false, runtime: { model: { exists: false } } })
    expect(await screen.findByText('Local model not configured')).toBeInTheDocument()
  })
  it('reports an unreachable AI service', async () => {
    mount((u) => (u === '/api/ai/status' ? json({ detail: 'down' }, 500) : undefined))
    expect(await screen.findByText('AI status unavailable')).toBeInTheDocument()
  })
  it('asks with the page path, shows the exchange, and Ctrl+Enter also sends', async () => {
    const calls = mount()
    const box = await screen.findByLabelText('Message')
    fireEvent.change(box, { target: { value: 'How many videos?' } })
    fireEvent.click(screen.getByRole('button', { name: 'Ask' }))
    expect(await screen.findByText('Answer text')).toBeInTheDocument()
    expect(screen.getByText('How many videos?')).toBeInTheDocument()
    expect(posts(calls, '/api/ai/chat')[0]!.body).toEqual({ message: 'How many videos?', page: '/', selected: null })
    fireEvent.change(box, { target: { value: 'Again' } }); fireEvent.keyDown(box, { key: 'Enter', ctrlKey: true })
    await waitFor(() => expect(posts(calls, '/api/ai/chat')).toHaveLength(2))
    expect(box).toHaveValue('')
  })
  it('shows the server reason when a question fails', async () => {
    mount((u) => (u === '/api/ai/chat' ? json({ detail: 'Local model is not running' }, 503) : undefined))
    fireEvent.change(await screen.findByLabelText('Message'), { target: { value: 'Hi' } })
    fireEvent.click(screen.getByRole('button', { name: 'Ask' }))
    expect(await screen.findByText('Local model is not running')).toBeInTheDocument()
  })
  it('quick actions switch to the assistant and ask their prompt', async () => {
    const calls = mount()
    fireEvent.click(await screen.findByRole('button', { name: /Find duplicates/ }))
    await waitFor(() => expect(posts(calls, '/api/ai/chat')[0]!.body!.message).toBe('Find duplicate video fingerprints and summarize the largest duplicate groups.'))
  })
  it('opening Diagnostics and Jobs runs the analysis and shows the answer', async () => {
    const calls = mount()
    fireEvent.click(await screen.findByRole('button', { name: /^Diagnostics/ }))
    expect(await screen.findByText('Answer text')).toBeInTheDocument()
    expect(String(posts(calls, '/api/ai/chat')[0]!.body!.message)).toMatch(/^Use the diagnostics tool/)
    fireEvent.click(screen.getByRole('button', { name: /^Jobs/ }))
    await waitFor(() => expect(String(posts(calls, '/api/ai/chat')[1]!.body!.message)).toMatch(/^Use the jobs tool/))
  })
  it('library search needs a question, then asks with the classic prompt', async () => {
    const calls = mount()
    fireEvent.click(await screen.findByRole('button', { name: /^Library Intelligence/ }))
    fireEvent.click(screen.getByRole('button', { name: 'Search with AI' })); expect(posts(calls, '/api/ai/chat')).toHaveLength(0)
    fireEvent.change(screen.getByLabelText('Library question'), { target: { value: 'cats' } })
    fireEvent.click(screen.getByRole('button', { name: 'Search with AI' }))
    expect(await screen.findByText('Answer text')).toBeInTheDocument()
    expect(String(posts(calls, '/api/ai/chat')[0]!.body!.message)).toMatch(/^Search the published Archive X library for: cats\./)
  })
  it('models tab fills the form from the server, clamps and saves, and reports start/test results', async () => {
    const calls = mount((u, i) => (u === '/api/ai/test' && i?.method === 'POST' ? json({ ok: true, latency_ms: 321, text: 'pong' }) : u === '/api/ai/runtime/start' ? json({ detail: 'No runtime found' }, 400) : undefined))
    fireEvent.click(await screen.findByRole('button', { name: /^Models/ }))
    const ctx = await screen.findByLabelText('Context tokens')
    await waitFor(() => expect(ctx).toHaveValue(8192))
    expect(screen.getByLabelText('llama-server executable')).toHaveValue('/rt')
    fireEvent.change(ctx, { target: { value: '999999' } })
    fireEvent.click(screen.getByRole('button', { name: 'Save' }))
    await waitFor(() => expect(posts(calls, '/api/ai/config')[0]!.body).toMatchObject({ runtime_path: '/rt', model_path: '/m.gguf', context: 16384, gpu_layers: 2 }))
    expect(await screen.findByText('Local AI settings saved.')).toBeInTheDocument()
    fireEvent.click(screen.getByRole('button', { name: 'Start local model' }))
    expect(await screen.findByText('No runtime found')).toBeInTheDocument()
    fireEvent.click(screen.getByRole('button', { name: 'Test model' }))
    expect(await screen.findByText('Model test passed in 321 ms: pong')).toBeInTheDocument()
    expect(screen.getByText('Reachable')).toBeInTheDocument()
  })
})

describe('AiPage while it loads', () => {
  it('says the conversation is loading instead of showing an empty page', async () => {
    vi.stubGlobal('fetch', vi.fn((url: string) => (url === '/api/ai/history' ? new Promise<Response>(() => {}) : Promise.resolve(json({ ok: true })))))
    withRouter(<AiPage />)
    expect(await screen.findByText('Loading the conversation…')).toBeInTheDocument()
    expect(screen.getByText('Checking local model…')).toBeInTheDocument()
  })
})
