import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { act, renderHook, waitFor } from '@testing-library/react'
import type { ReactNode } from 'react'
import { afterEach, describe, expect, it, vi } from 'vitest'
import { useFavorite } from './hooks'

afterEach(() => vi.unstubAllGlobals())
const setup = () => {
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false, gcTime: Infinity } } })
  qc.setQueryData(['library', 'a'], { total: 1, items: [{ media_id: 10, favorite: 0 }] })
  qc.setQueryData(['video', 10], { media_id: 10, favorite: 0 })
  const wrapper = ({ children }: { children: ReactNode }) => <QueryClientProvider client={qc}>{children}</QueryClientProvider>
  return { qc, ...renderHook(() => useFavorite(), { wrapper }) }
}
const heart = (qc: QueryClient) => ({ lib: (qc.getQueryData(['library', 'a']) as { items: Array<{ favorite: unknown }> }).items[0]!.favorite, video: (qc.getQueryData(['video', 10]) as { favorite: unknown }).favorite })

describe('useFavorite', () => {
  it('changes the heart in every cached copy before the server answers', async () => {
    vi.stubGlobal('fetch', vi.fn(() => new Promise<Response>(() => undefined)))
    const { qc, result } = setup()
    act(() => { result.current.mutate({ mediaId: 10, favorite: true }) })
    await waitFor(() => expect(heart(qc)).toEqual({ lib: true, video: true }))
  })
  it('puts the heart back when the request fails', async () => {
    vi.stubGlobal('fetch', vi.fn(async () => new Response('{"detail":"no"}', { status: 500 })))
    const { qc, result } = setup()
    act(() => { result.current.mutate({ mediaId: 10, favorite: true }) })
    await waitFor(() => expect(result.current.isError).toBe(true))
    expect(heart(qc)).toEqual({ lib: 0, video: 0 })
  })
})
