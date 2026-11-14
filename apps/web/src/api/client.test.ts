import { afterEach, describe, expect, it, vi } from 'vitest'
import { ApiError, SESSION_LOST_EVENT, api, libraryUrl, thumbUrl } from './client'

const respond = (status: number, body: unknown, ok = status < 400) =>
  vi.spyOn(globalThis, 'fetch').mockResolvedValue(new Response(JSON.stringify(body), { status, headers: { 'Content-Type': 'application/json' } }) as Response & { ok: boolean }) && ok

afterEach(() => vi.restoreAllMocks())

describe('api()', () => {
  it('adds filters only when set, using the API parameter names', () => {
    const base = { channels: ['1'], page: 0, per: 24, sort: 'date_desc' }
    const url = libraryUrl({ ...base, studio: 'Northlight', performer: 'Ava Stone', tag: 'pov', dateFrom: '2026-01-01', dateTo: '2026-02-01', resolution: '1080' })
    expect(url).toContain('studio=Northlight')
    expect(url).toContain('performer=Ava+Stone')
    expect(url).toContain('tag=pov')
    expect(url).toContain('date_from=2026-01-01')
    expect(url).toContain('date_to=2026-02-01')
    expect(url).toContain('resolution=1080')
    expect(libraryUrl(base)).not.toMatch(/studio=|performer=|tag=|date_from|date_to|resolution=/)
  })
  it('returns parsed JSON and sends the cookie session', async () => {
    const spy = vi.spyOn(globalThis, 'fetch').mockResolvedValue(new Response(JSON.stringify({ ok: 1 }), { status: 200 }))
    await expect(api('/api/status')).resolves.toEqual({ ok: 1 })
    expect(spy.mock.calls[0]?.[1]).toMatchObject({ credentials: 'same-origin', cache: 'no-store' })
  })

  it('throws ApiError carrying the server detail', async () => {
    respond(429, { detail: 'Too many login attempts. Try again in 30s.' })
    const err = await api('/login', { method: 'POST', json: {} }).catch((e: unknown) => e)
    expect(err).toBeInstanceOf(ApiError)
    expect((err as ApiError).status).toBe(429)
    expect((err as ApiError).detail).toContain('Too many')
  })

  it('announces a lost session on 401, except for the login request itself', async () => {
    const lost = vi.fn()
    window.addEventListener(SESSION_LOST_EVENT, lost)
    respond(401, { detail: 'Authentication required' })
    await api('/api/library').catch(() => {})
    expect(lost).toHaveBeenCalledTimes(1)
    respond(401, { detail: 'Invalid username or password' })
    await api('/login', { method: 'POST', json: {} }).catch(() => {})
    expect(lost).toHaveBeenCalledTimes(1)
    window.removeEventListener(SESSION_LOST_EVENT, lost)
  })

  it('falls back to text when the error body is not JSON', async () => {
    vi.spyOn(globalThis, 'fetch').mockResolvedValue(new Response('Bad gateway', { status: 502 }))
    const err = (await api('/api/x').catch((e: unknown) => e)) as ApiError
    expect(err.status).toBe(502)
    expect(err.detail).toBe('Bad gateway')
  })
})

describe('urls', () => {
  it('builds the library query the backend expects', () => {
    const url = libraryUrl({ channels: ['-1001', '-1002'], page: 2, per: 24, sort: 'date_desc', q: 'a b', favorites: true })
    const p = new URL(url, 'http://x').searchParams
    expect(p.get('channels')).toBe('-1001,-1002')
    expect(p.get('page')).toBe('2')
    expect(p.get('per')).toBe('24')
    expect(p.get('q')).toBe('a b')
    expect(p.get('favorites')).toBe('1')
  })
  it('encodes negative channel ids in thumbnail urls', () => {
    expect(thumbUrl('-1001', 7)).toBe('/api/thumb/-1001/7?r=f3')
  })
})
