import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { fireEvent, render, screen } from '@testing-library/react'
import { afterEach, describe, expect, it, vi } from 'vitest'
import { PeopleStatus } from './PeopleStatus'

function mount(nameList: unknown, calls: string[] = []) {
  vi.stubGlobal('fetch', vi.fn(async (url: string, init?: RequestInit) => {
    calls.push(`${init?.method ?? 'GET'} ${url}`)
    const body = url.includes('name-list') ? nameList : { faces: 4, confirmed: 3, removed: 1 }
    return new Response(JSON.stringify(body), { status: 200, headers: { 'content-type': 'application/json' } })
  }))
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } })
  return render(<QueryClientProvider client={qc}><PeopleStatus headline="12 people · 300 photos checked" /></QueryClientProvider>)
}
afterEach(() => vi.unstubAllGlobals())

describe('PeopleStatus', () => {
  it('shows the headline and one detail line, with no warning when all is well', async () => {
    mount({ ok: true, total: 500, ready: true, providers: { stashdb: { configured: true, status: 'idle', count: 500, last_finished: '', last_error: '', page: 0 } } })
    expect(screen.getByRole('status')).toHaveTextContent('12 people')
    expect(await screen.findByText(/500 names on this PC · learning from 3 confirmed and 1 removed face/)).toBeInTheDocument()
    expect(screen.queryByRole('alert')).toBeNull()
  })
  it('explains a failure in words and offers Try again', async () => {
    const calls: string[] = []
    mount({ ok: true, total: 0, ready: false, providers: { stashdb: { configured: true, status: 'error', count: 0, last_finished: '', last_error: 'HTTP 401: Unauthenticated', page: 0 } } }, calls)
    expect(await screen.findByRole('alert')).toHaveTextContent('StashDB names are not updating: the key was not accepted, check it in Settings.')
    fireEvent.click(screen.getByRole('button', { name: 'Try again' }))
    expect(calls).toContain('POST /api/photos/faces/performers/name-list/sync')
  })
})
