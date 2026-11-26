import { QueryClient, QueryObserver } from '@tanstack/react-query'
import { afterEach, describe, expect, it } from 'vitest'
import { forgetModeData, swapModeData } from './modeSwap'

const client = () => new QueryClient({ defaultOptions: { queries: { retry: false, gcTime: Infinity } } })

/** An active query (something on screen is using it) whose answer is `answers[n]`; every fetch is counted. */
function watch(qc: QueryClient, key: readonly unknown[], answers: unknown[]) {
  const calls = { n: 0 }
  const obs = new QueryObserver(qc, { queryKey: key, queryFn: async () => answers[Math.min(calls.n++, answers.length - 1)], staleTime: Infinity })
  const stop = obs.subscribe(() => undefined)
  return { calls, obs, stop }
}
const settle = () => new Promise((r) => setTimeout(r, 20))

afterEach(() => forgetModeData())

describe('swapModeData', () => {
  it('drops the old mode data at once when nothing is remembered for the new mode, and fetches fresh', async () => {
    const qc = client()
    const lib = watch(qc, ['library', { page: 0 }], ['nsfw cards', 'sfw cards'])
    await settle()
    expect(lib.obs.getCurrentResult().data).toBe('nsfw cards')
    swapModeData(qc, 'nsfw', 'sfw')
    expect(lib.obs.getCurrentResult().data).toBeUndefined()            // never the other mode's content, not even for a moment
    await settle()
    expect(lib.obs.getCurrentResult().data).toBe('sfw cards')
    lib.stop()
  })

  it('puts back what was last seen in the mode being entered, at once, and refreshes it behind', async () => {
    const qc = client()
    const lib = watch(qc, ['library', { page: 0 }], ['nsfw 1', 'sfw 1', 'nsfw 2'])
    await settle()
    swapModeData(qc, 'nsfw', 'sfw'); await settle()                      // now showing 'sfw 1'
    swapModeData(qc, 'sfw', 'nsfw')                                      // back: no waiting
    expect(lib.obs.getCurrentResult().data).toBe('nsfw 1')
    await settle()
    expect(lib.obs.getCurrentResult().data).toBe('nsfw 2')               // refreshed behind the screen
    lib.stop()
  })

  it('leaves queries that do not depend on the mode alone', async () => {
    const qc = client()
    const profile = watch(qc, ['profile'], ['me'])
    const settings = watch(qc, ['settings', 'experience'], ['density'])
    const channels = watch(qc, ['channels'], ['a', 'b'])
    await settle()
    swapModeData(qc, 'nsfw', 'sfw'); await settle()
    expect(profile.calls.n).toBe(1)
    expect(settings.calls.n).toBe(1)
    expect(channels.calls.n).toBe(2)                                      // the one that depends on the mode was fetched again
    expect(profile.obs.getCurrentResult().data).toBe('me')
    for (const w of [profile, settings, channels]) w.stop()
  })

  it('does nothing when the mode did not change', async () => {
    const qc = client()
    const lib = watch(qc, ['library'], ['x', 'y'])
    await settle()
    swapModeData(qc, 'sfw', 'sfw'); await settle()
    expect(lib.calls.n).toBe(1)
    lib.stop()
  })

  it('does not show a remembered screen that is too old', async () => {
    const qc = client()
    const lib = watch(qc, ['library'], ['nsfw old', 'sfw', 'nsfw new'])
    await settle()
    const t0 = 1_000_000
    swapModeData(qc, 'nsfw', 'sfw', t0); await settle()
    swapModeData(qc, 'sfw', 'nsfw', t0 + 11 * 60_000)                    // the NSFW screen was remembered 11 minutes ago
    expect(lib.obs.getCurrentResult().data).toBeUndefined()
    await settle()
    expect(lib.obs.getCurrentResult().data).toBe('nsfw new')
    lib.stop()
  })

  it('forgets everything remembered when asked (signing out)', async () => {
    const qc = client()
    const lib = watch(qc, ['library'], ['nsfw', 'sfw', 'nsfw again'])
    await settle()
    swapModeData(qc, 'nsfw', 'sfw'); await settle()
    forgetModeData()
    swapModeData(qc, 'sfw', 'nsfw')
    expect(lib.obs.getCurrentResult().data).toBeUndefined()
    lib.stop()
  })
})
