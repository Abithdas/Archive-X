import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { fireEvent, render, screen, waitFor } from '@testing-library/react'
import { afterEach, describe, expect, it, vi } from 'vitest'
import { Findings } from './Findings'

afterEach(() => vi.unstubAllGlobals())
const f = (id: number, status: string, title: string) => ({ id, kind: 'issue', title, detail: 'Some detail', status, created_at: 't', updated_at: 't' })
const COUNTS = { OPEN: 2, ACCEPTED: 1, FIXED: 0, REJECTED: 0 }

function mount() {
  const calls: Array<{ url: string; method: string; body?: unknown }> = []
  vi.stubGlobal('fetch', vi.fn(async (url: string, init?: RequestInit) => {
    calls.push({ url, method: init?.method ?? 'GET', body: init?.body ? JSON.parse(String(init.body)) : undefined })
    if (url.startsWith('/api/ai/findings?')) {
      const status = new URL(url, 'http://x').searchParams.get('status')
      const all = [f(1, 'OPEN', 'Scan is slow at night'), f(2, 'OPEN', 'Thumbnails repeat'), f(3, 'ACCEPTED', 'Gallery retries too often')]
      return new Response(JSON.stringify({ findings: all.filter((x) => x.status === status), counts: COUNTS, knowledge: { changelog: 5, docs: 2, code: 40 } }), { status: 200 })
    }
    return new Response('{"ok":true}', { status: 200 })
  }))
  render(<QueryClientProvider client={new QueryClient({ defaultOptions: { queries: { retry: false } } })}><Findings /></QueryClientProvider>)
  return calls
}

describe('Findings tab', () => {
  it('lists the notes to review first, with counts, and what it has read', async () => {
    mount()
    expect(await screen.findByText('Scan is slow at night')).toBeInTheDocument()
    expect(screen.getByRole('tab', { name: 'To review · 2' })).toHaveAttribute('aria-selected', 'true')
    expect(screen.getByRole('tab', { name: 'Accepted · 1' })).toBeInTheDocument()
    expect(screen.getByText('It has read 5 changelog notes, 2 docs sections, 40 code descriptions.')).toBeInTheDocument()
  })
  it('accepts and rejects a note, and the buttons change with the state', async () => {
    const calls = mount()
    fireEvent.click(await screen.findByRole('button', { name: 'Accept: Scan is slow at night' }))
    await waitFor(() => expect(calls.find((c) => c.method === 'POST' && c.url === '/api/ai/findings/1')?.body).toEqual({ status: 'ACCEPTED' }))
    fireEvent.click(screen.getByRole('button', { name: 'Reject: Thumbnails repeat' }))
    await waitFor(() => expect(calls.find((c) => c.url === '/api/ai/findings/2')?.body).toEqual({ status: 'REJECTED' }))
    fireEvent.click(screen.getByRole('tab', { name: 'Accepted · 1' }))
    expect(await screen.findByRole('button', { name: 'Mark fixed: Gallery retries too often' })).toBeInTheDocument()
  })
  it('can read the changelog, docs and code again', async () => {
    const calls = mount()
    fireEvent.click(await screen.findByRole('button', { name: 'Read it again' }))
    await waitFor(() => expect(calls.some((c) => c.method === 'POST' && c.url === '/api/ai/knowledge/rebuild')).toBe(true))
  })
  it('says so when there is nothing to review', async () => {
    mount()
    fireEvent.click(await screen.findByRole('tab', { name: 'Fixed · 0' }))
    expect(await screen.findByText('Nothing here yet.')).toBeInTheDocument()
  })
})
