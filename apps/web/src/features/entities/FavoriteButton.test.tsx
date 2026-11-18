import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { fireEvent, render, screen, waitFor } from '@testing-library/react'
import { afterEach, describe, expect, it, vi } from 'vitest'
import { FavoriteButton } from './FavoriteButton'

afterEach(() => vi.unstubAllGlobals())
const mount = () => render(<QueryClientProvider client={new QueryClient()}><FavoriteButton kind="performer" id={7} initial={false} /></QueryClientProvider>)

describe('FavoriteButton', () => {
  it('turns on at once, without waiting for the server', async () => {
    let answer: (r: Response) => void = () => undefined
    vi.stubGlobal('fetch', vi.fn(() => new Promise<Response>((r) => { answer = r })))
    mount()
    const btn = screen.getByRole('button', { name: /favourite/i })
    expect(btn).toHaveAttribute('aria-pressed', 'false')
    fireEvent.click(btn)
    expect(screen.getByRole('button', { name: /favourite/i })).toHaveAttribute('aria-pressed', 'true')   // before the answer
    answer(new Response(JSON.stringify({ ok: true, favorite: true }), { status: 200 }))
    await waitFor(() => expect(screen.getByRole('button', { name: /favourite/i })).toHaveAttribute('aria-pressed', 'true'))
  })
  it('goes back when the server refuses', async () => {
    vi.stubGlobal('fetch', vi.fn(async () => new Response(JSON.stringify({ detail: 'no' }), { status: 500 })))
    mount()
    fireEvent.click(screen.getByRole('button', { name: /favourite/i }))
    await waitFor(() => expect(screen.getByRole('button', { name: /favourite/i })).toHaveAttribute('aria-pressed', 'false'))
  })
  it('ignores a second press while the first is still being answered', () => {
    const f = vi.fn(() => new Promise<Response>(() => undefined))
    vi.stubGlobal('fetch', f)
    mount()
    const btn = screen.getByRole('button', { name: /favourite/i })
    fireEvent.click(btn); fireEvent.click(btn)
    expect(f).toHaveBeenCalledTimes(1)
  })
})
