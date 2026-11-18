import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { act, fireEvent, render } from '@testing-library/react'
import { afterEach, describe, expect, it, vi } from 'vitest'
import { queryKeys } from '@/api/hooks'
import { AccountAvatar } from './ProfileEditor'

afterEach(() => vi.unstubAllGlobals())

describe('AccountAvatar', () => {
  it('shows the photo again once Telegram is connected, after it could not be loaded while it was not', async () => {
    vi.stubGlobal('fetch', vi.fn(async (url: string) => new Response(JSON.stringify(String(url).includes('profile') ? { name: 'Ada', image_version: '0' } : { telegram_connected: false }), { status: 200 })))
    const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } })
    const { container } = render(<QueryClientProvider client={qc}><AccountAvatar /></QueryClientProvider>)
    const img = async () => { await act(async () => { await new Promise((r) => setTimeout(r, 20)) }); return container.querySelector('img') }
    const first = await img()
    expect(first).not.toBeNull()
    fireEvent.error(first!)                                               // the photo cannot be fetched: Telegram is not connected yet
    expect(container.querySelector('img')).toBeNull()                      // the initials show instead
    await act(async () => { qc.setQueryData(queryKeys.status, { telegram_connected: true }); await new Promise((r) => setTimeout(r, 20)) })
    expect(container.querySelector('img')).not.toBeNull()                  // Telegram is up: it is tried again
    expect(container.querySelector('img')!.getAttribute('src')).toContain('/api/telegram/me/photo')
  })
})
