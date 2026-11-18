import { act, fireEvent, screen, waitFor, within } from '@testing-library/react'
import { afterEach, describe, expect, it, vi } from 'vitest'
import { withRouter } from '@/test/withRouter'
import { AiPage } from './AiPage'
import type { ChatEvent } from './stream'

vi.mock('sonner', () => ({ toast: Object.assign(vi.fn(), { error: vi.fn() }), Toaster: () => null }))
afterEach(() => vi.unstubAllGlobals())
const json = (b: unknown, code = 200) => new Response(JSON.stringify(b), { status: code })
const ndjson = (events: ChatEvent[]) => new Response(events.map((e) => JSON.stringify(e)).join('\n') + '\n', { status: 200, headers: { 'Content-Type': 'application/x-ndjson' } })
const reply = (text: string): ChatEvent[] => [{ t: 'status', phase: 'thinking', text: 'Thinking…' }, ...text.split(' ').map((w, i) => ({ t: 'delta', text: (i ? ' ' : '') + w }) as ChatEvent), { t: 'done', text, model: 'Qwen3', latency_ms: 5, tool_calls: 0 }]
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
    if (url === '/api/ai/chat/stream') return ndjson(reply('Answer text'))
    return json({ ok: true })
  }))
  withRouter(<AiPage />)
  return calls
}
const posts = (c: Call[], url: string) => c.filter((x) => x.method === 'POST' && x.url === url)
const answer = (text: string) => screen.findByText((_, el) => el?.tagName === 'SPAN' && String(el.className).includes('whitespace-pre-wrap') && el.textContent === text)

describe('AiPage', () => {
  it('shows the model state, tools state and previous history', async () => {
    mount(() => undefined, STATUS, [{ role: 'user', content: 'Earlier question', created_at: 't1' }, { role: 'assistant', content: 'Earlier answer', created_at: 't2' }])
    expect(await screen.findByText('Assistant ready')).toBeInTheDocument()
    expect(screen.getByText('llama.cpp · Qwen3')).toBeInTheDocument()
    expect(await screen.findByText('Read-only Archive X tools enabled.')).toBeInTheDocument()
    expect(await answer('Earlier answer')).toBeInTheDocument()
  })
  it('says the assistant is asleep, not broken, when it is set up but stopped', async () => {
    mount(() => undefined, { ...STATUS, runtime: { ...STATUS.runtime, server: { reachable: false } } })
    expect(await screen.findByText('Asleep — starts when you ask')).toBeInTheDocument()
    expect(await screen.findByLabelText('Message')).toBeInTheDocument()   // the chat is there: asking starts it
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
    expect(await answer('Answer text')).toBeInTheDocument()
    expect(screen.getByText('How many videos?')).toBeInTheDocument()
    expect(posts(calls, '/api/ai/chat/stream')[0]!.body).toEqual({ message: 'How many videos?', page: '/', selected: null })
    fireEvent.change(box, { target: { value: 'Again' } }); fireEvent.keyDown(box, { key: 'Enter', ctrlKey: true })
    await waitFor(() => expect(posts(calls, '/api/ai/chat/stream')).toHaveLength(2))
    expect(box).toHaveValue('')
  })
  it('shows the reason when a question fails, whether the server refuses it or the stream reports it', async () => {
    mount((u) => (u === '/api/ai/chat/stream' ? json({ detail: 'Local model is not running' }, 503) : undefined))
    fireEvent.change(await screen.findByLabelText('Message'), { target: { value: 'Hi' } })
    fireEvent.click(screen.getByRole('button', { name: 'Ask' }))
    expect(await screen.findByText('Local model is not running')).toBeInTheDocument()
  })
  it('shows an error that arrives as a stream event', async () => {
    mount((u) => (u === '/api/ai/chat/stream' ? ndjson([{ t: 'error', detail: 'The assistant engine stopped while starting (exit code 3).' }]) : undefined))
    fireEvent.change(await screen.findByLabelText('Message'), { target: { value: 'Hi' } })
    fireEvent.click(screen.getByRole('button', { name: 'Ask' }))
    expect(await screen.findByText(/stopped while starting \(exit code 3\)/)).toBeInTheDocument()
  })
  it('shows what the assistant is doing while it works: the starting line, each tool as a step that turns into a check, then the answer', async () => {
    let push!: (e: ChatEvent) => void
    let end!: () => void
    const enc = new TextEncoder()
    const stream = new ReadableStream<Uint8Array>({ start(c) { push = (e) => c.enqueue(enc.encode(JSON.stringify(e) + '\n')); end = () => c.close() } })
    mount((u) => (u === '/api/ai/chat/stream' ? new Response(stream, { status: 200 }) : undefined))
    fireEvent.change(await screen.findByLabelText('Message'), { target: { value: 'Any problems?' } })
    fireEvent.click(screen.getByRole('button', { name: 'Ask' }))
    await act(async () => push({ t: 'status', phase: 'starting', text: 'Starting the local model…' }))
    expect((await screen.findAllByText('Starting the local model…')).length).toBeGreaterThan(0)   // the line, plus the hidden copy that keeps its width steady
    expect(screen.getByRole('button', { name: 'Ask' })).toBeDisabled()                       // one question at a time
    await act(async () => push({ t: 'tool', name: 'get_diagnostics_summary', label: 'Checking diagnostics', state: 'start' }))
    const steps = await screen.findByRole('list', { name: 'Steps the assistant took' })
    expect(within(steps).getByRole('img', { name: 'Working' })).toBeInTheDocument()
    await act(async () => push({ t: 'tool', name: 'get_diagnostics_summary', label: 'Checking diagnostics', state: 'done' }))
    await waitFor(() => expect(within(steps).getByRole('img', { name: 'Done' })).toBeInTheDocument())
    await act(async () => { push({ t: 'delta', text: 'All ' }); push({ t: 'delta', text: 'good.' }); push({ t: 'done', text: 'All good.', model: 'Qwen3', latency_ms: 9, tool_calls: 1 }); end() })
    expect(await answer('All good.')).toBeInTheDocument()
    await waitFor(() => expect(screen.getByRole('button', { name: 'Ask' })).toBeDisabled())  // the box is empty again
    fireEvent.change(screen.getByLabelText('Message'), { target: { value: 'More' } })
    expect(screen.getByRole('button', { name: 'Ask' })).toBeEnabled()
  })
  it('shows a breathing dot and the shimmering status while waiting, the reasoning as it streams, and the dot after the last word until done', async () => {
    let push!: (e: ChatEvent) => void
    let end!: () => void
    const enc = new TextEncoder()
    const stream = new ReadableStream<Uint8Array>({ start(c) { push = (e) => c.enqueue(enc.encode(JSON.stringify(e) + '\n')); end = () => c.close() } })
    mount((u) => (u === '/api/ai/chat/stream' ? new Response(stream, { status: 200 }) : undefined))
    fireEvent.change(await screen.findByLabelText('Message'), { target: { value: 'Why?' } })
    fireEvent.click(screen.getByRole('button', { name: 'Ask' }))
    await act(async () => push({ t: 'status', phase: 'thinking', text: 'Thinking…' }))
    const log = screen.getByRole('log', { name: 'Assistant conversation' })
    expect(within(log).getByRole('status', { name: 'Working' })).toHaveClass('ax-chat-dot')        // the dot beside the status line
    expect(log.querySelector('.t-think-text')).not.toBeNull()                                       // the shimmering line
    await act(async () => { push({ t: 'reasoning', text: 'First, ' }); push({ t: 'reasoning', text: 'check the jobs.' }) })
    expect(await within(log).findByText('Reasoning…')).toBeInTheDocument()                           // shimmering title while it thinks
    await act(async () => push({ t: 'delta', text: 'Because.' }))
    expect(within(log).queryByRole('status', { name: 'Working' })).toBeNull()                       // the status line gives way to the words
    expect(log.querySelector('.ax-chat-dot')).not.toBeNull()                                        // ... and the dot keeps breathing after them
    expect(within(log).getByText('Reasoning')).toBeInTheDocument()                                  // the title stops shimmering once the answer starts
    await act(async () => { push({ t: 'done', text: 'Because.', model: 'Qwen3', latency_ms: 9, tool_calls: 0 }); end() })
    await waitFor(() => expect(log.querySelector('.ax-chat-dot')).toBeNull())
  })
  it('has no matrix dot loader anywhere on the page', async () => {
    mount()
    await screen.findByText('Assistant ready')
    expect(document.querySelector('.t-matrix')).toBeNull()
  })
  it('quick actions switch to the assistant and ask their prompt', async () => {
    const calls = mount()
    fireEvent.click(await screen.findByRole('button', { name: /Find duplicates/ }))
    await waitFor(() => expect(posts(calls, '/api/ai/chat/stream')[0]!.body!.message).toBe('Find duplicate video fingerprints and summarize the largest duplicate groups.'))
  })
  it('opening Diagnostics and Jobs runs the analysis and shows the answer', async () => {
    const calls = mount()
    fireEvent.click(await screen.findByRole('button', { name: /^Diagnostics/ }))
    expect(await answer('Answer text')).toBeInTheDocument()
    expect(String(posts(calls, '/api/ai/chat/stream')[0]!.body!.message)).toMatch(/^Use the diagnostics tool/)
    fireEvent.click(screen.getByRole('button', { name: /^Jobs/ }))
    await waitFor(() => expect(String(posts(calls, '/api/ai/chat/stream')[1]!.body!.message)).toMatch(/^Use the jobs tool/))
  })
  it('library search needs a question, then asks with the classic prompt', async () => {
    const calls = mount()
    fireEvent.click(await screen.findByRole('button', { name: /^Library Intelligence/ }))
    fireEvent.click(screen.getByRole('button', { name: 'Search with AI' })); expect(posts(calls, '/api/ai/chat/stream')).toHaveLength(0)
    fireEvent.change(screen.getByLabelText('Library question'), { target: { value: 'cats' } })
    fireEvent.click(screen.getByRole('button', { name: 'Search with AI' }))
    expect(await answer('Answer text')).toBeInTheDocument()
    expect(String(posts(calls, '/api/ai/chat/stream')[0]!.body!.message)).toMatch(/^Search the published Archive X library for: cats\./)
  })
  it('models tab: starts and tests the engine, and keeps the manual paths under Advanced', async () => {
    const calls = mount((u, i) => (u === '/api/ai/test' && i?.method === 'POST' ? json({ ok: true, latency_ms: 321, text: 'pong' }) : u === '/api/ai/runtime/start' ? json({ detail: 'No runtime found' }, 400) : undefined))
    fireEvent.click(await screen.findByRole('button', { name: /^Models/ }))
    fireEvent.click(await screen.findByRole('button', { name: 'Advanced: use your own engine and model file' }))
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

describe('AiPage when the assistant is not set up', () => {
  const NOT_SET_UP = { configured: false, provider: 'llama.cpp', model: 'Qwen3', runtime: { runtime: { exists: false }, model: { exists: false }, server: { reachable: false } } }
  const SETUP = (run: object = { state: 'idle', steps: [], log: [], bytes_done: 0, bytes_total: 0, speed_bps: 0, error: '', model: '', engine: '' }) => ({
    ok: true, supported: true, ram_gb: 16, recommended: 'balanced', engine_tag: 'b12400', run,
    engines: [{ id: 'win-cpu', label: 'Processor', size_mb: 24, gpu: false }, { id: 'win-gpu', label: 'Graphics card (Vulkan)', size_mb: 37, gpu: true }],
    models: [
      { id: 'small', label: 'Small', model: 'Qwen3-1.7B-Q4_K_M', size_mb: 1223, min_ram_gb: 4, blurb: 'Quick and light.', recommended: false, fits: true },
      { id: 'balanced', label: 'Balanced', model: 'Qwen3-4B-Instruct-2507-Q4_K_M', size_mb: 2381, min_ram_gb: 8, blurb: 'The recommended one.', recommended: true, fits: true },
      { id: 'strong', label: 'Strong', model: 'Qwen3-8B-Q4_K_M', size_mb: 4794, min_ram_gb: 32, blurb: 'Best answers.', recommended: false, fits: false },
    ],
  })
  const STEPS = [{ id: 'engine', label: 'Download the engine', state: 'done', detail: 'llama.cpp b12400' }, { id: 'model', label: 'Download the model', state: 'active', detail: 'Balanced' }, { id: 'check', label: 'Check the files', state: 'pending', detail: '' }, { id: 'start', label: 'Start the assistant', state: 'pending', detail: '' }, { id: 'test', label: 'Ask it a test question', state: 'pending', detail: '' }]

  it('offers the setup instead of an empty chat, with the recommended size picked', async () => {
    mount((u) => (u === '/api/ai/setup' ? json(SETUP()) : undefined), NOT_SET_UP)
    expect(await screen.findByText('Not set up yet')).toBeInTheDocument()
    const group = await screen.findByRole('radiogroup', { name: 'Model size' })
    expect(within(group).getByRole('radio', { name: /Balanced/ })).toHaveAttribute('aria-checked', 'true')
    expect(within(group).getByText('RECOMMENDED')).toBeInTheDocument()
    expect(within(group).getByText('Wants 32 GB of memory; this PC has 16 GB.')).toBeInTheDocument()
    expect(screen.queryByLabelText('Message')).toBeNull()
    expect(screen.getByRole('button', { name: /^Set up · 2\.3 GB$/ })).toBeInTheDocument()
  })
  it('starts the setup with the chosen size and engine', async () => {
    const calls = mount((u) => (u === '/api/ai/setup' ? json(SETUP()) : undefined), NOT_SET_UP)
    const group = await screen.findByRole('radiogroup', { name: 'Model size' })
    fireEvent.click(within(group).getByRole('radio', { name: /Small/ }))
    fireEvent.click(screen.getByRole('button', { name: 'Graphics card (Vulkan)' }))
    expect(screen.getByRole('button', { name: /^Set up · 1\.2 GB$/ })).toBeInTheDocument()
    fireEvent.click(screen.getByRole('button', { name: /^Set up/ }))
    await waitFor(() => expect(posts(calls, '/api/ai/setup/start')[0]!.body).toEqual({ model: 'small', engine: 'win-gpu' }))
  })
  it('shows the real steps, the download progress and the log while it runs, and can be stopped', async () => {
    const run = { state: 'running', model: 'balanced', engine: 'win-cpu', steps: STEPS, bytes_done: 1200 * 1024 ** 2, bytes_total: 2381 * 1024 ** 2, speed_bps: 23 * 1024 ** 2, log: ['Downloading llama.cpp b12400 (processor)…', 'Unpacking the engine…', 'Downloading Qwen3-4B-Instruct-2507-Q4_K_M…'], error: '' }
    const calls = mount((u) => (u === '/api/ai/setup' ? json(SETUP(run)) : undefined), NOT_SET_UP)
    const steps = await screen.findByRole('list', { name: 'Setup steps' })
    expect(within(steps).getAllByRole('img').map((i) => i.getAttribute('aria-label'))).toEqual(['Done', 'Working', 'Waiting', 'Waiting', 'Waiting'])
    expect(within(steps).getByText('Download the model')).toBeInTheDocument()
    expect(screen.getByRole('progressbar', { name: 'Download progress' })).toHaveAttribute('aria-valuenow', '50')
    expect(screen.getByText(/1\.2 GB of 2\.3 GB · 23 MB\/s/)).toBeInTheDocument()
    expect(within(screen.getByRole('list', { name: 'What it is doing' })).getByText('Unpacking the engine…')).toBeInTheDocument()
    fireEvent.click(screen.getByRole('button', { name: 'Stop' }))
    await waitFor(() => expect(posts(calls, '/api/ai/setup/cancel')).toHaveLength(1))
  })
  it('says what went wrong and lets you try again', async () => {
    const run = { state: 'error', model: 'balanced', engine: 'win-cpu', steps: STEPS.map((s) => (s.id === 'model' ? { ...s, state: 'error', detail: 'x' } : s)), bytes_done: 0, bytes_total: 0, speed_bps: 0, log: ['Failed: connection lost'], error: 'The assistant engine stopped while starting (exit code 3).' }
    mount((u) => (u === '/api/ai/setup' ? json(SETUP(run)) : undefined), NOT_SET_UP)
    expect(await screen.findByRole('alert')).toHaveTextContent('exit code 3')
    expect(screen.getByRole('button', { name: /^Try again/ })).toBeInTheDocument()
  })
  it('on a computer the one-click setup is not made for, points to the manual way', async () => {
    mount((u) => (u === '/api/ai/setup' ? json({ ...SETUP(), supported: false, engines: [] }) : undefined), NOT_SET_UP)
    expect(await screen.findByText(/made for 64-bit Windows/)).toBeInTheDocument()
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
