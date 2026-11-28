import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { render, screen, waitFor } from '@testing-library/react'
import { beforeEach, describe, expect, it, vi } from 'vitest'

const api = vi.hoisted(() => vi.fn())
const player = vi.hoisted(() => vi.fn())
vi.mock('@/api/client', () => ({ api }))
vi.mock('@tanstack/react-router', () => ({ useRouter: () => ({ history: { back: () => undefined } }) }))
vi.mock('@/features/watch/Player', () => ({ Player: (p: Record<string, unknown>) => { player(p); return <div data-testid="player" /> } }))
import { AddonPlayPage, worthResuming } from './AddonPlayPage'

const play = { src: '/api/screen/stream/abcdefgh', name: 'Pilot', kind: 'series' as const, ident: 'tt0903747', season: 1, episode: 2 }
const wrap = () => render(<QueryClientProvider client={new QueryClient()}><AddonPlayPage play={play} /></QueryClientProvider>)

beforeEach(() => { api.mockReset(); player.mockReset() })

describe('worthResuming', () => {
  it('skips the first moments and anything that is not a number', () => { expect(worthResuming(19)).toBe(false); expect(worthResuming(20)).toBe(true); expect(worthResuming(Number.NaN)).toBe(false) })
})

describe('AddonPlayPage', () => {
  it('starts where the title stopped, and reports the position as it plays', async () => {
    api.mockImplementation((url: string) => (url.startsWith('/api/screen/addon-progress?') ? Promise.resolve({ position: 300 }) : Promise.resolve({ ok: true })))
    wrap()
    await screen.findByTestId('player')
    expect(api.mock.calls.find((c) => String(c[0]).startsWith('/api/screen/addon-progress?'))![0]).toBe('/api/screen/addon-progress?kind=series&ident=tt0903747&season=1&episode=2')
    const props = player.mock.calls.at(-1)![0] as { startAt: number; onTime: (t: number, d: number) => void; onEnd: () => void }
    expect(props.startAt).toBe(300)
    props.onTime(310, 1000)
    await waitFor(() => expect(api).toHaveBeenCalledWith('/api/screen/addon-progress', { method: 'POST', json: { kind: 'series', ident: 'tt0903747', season: 1, episode: 2, name: 'Pilot', position: 310, duration: 1000, src: '/api/screen/stream/abcdefgh' } }))
    props.onTime(320, 1000)                                                       // within ten seconds of the last save: not sent again
    expect(api.mock.calls.filter((c) => c[0] === '/api/screen/addon-progress')).toHaveLength(1)
    props.onEnd()                                                                 // the end is always sent, as the whole duration
    await waitFor(() => expect(api).toHaveBeenLastCalledWith('/api/screen/addon-progress', { method: 'POST', json: expect.objectContaining({ position: 1000, duration: 1000 }) }))
  })
  it('starts from the beginning when nothing was kept', async () => {
    api.mockResolvedValue({ position: 0 })
    wrap()
    await screen.findByTestId('player')
    expect((player.mock.calls.at(-1)![0] as { startAt: number }).startAt).toBe(0)
  })
})
