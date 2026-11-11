import { describe, expect, it } from 'vitest'
import { FAVORITES_KEY, SORTS, allSelected, channelAvatar, channelTitle, clampPan, counterText, deleteBody, deleteCopy, deletePath, emptyText, fitScale, formatBytes, formatDate, hud, initials, photoParams, photosUrl, pruneSelection, rotate, readFavorites, scannedChannels, stepIndex, stripRange, toggleAll, toggleFavorite, toggleId, writeFavorites, zoomBy, zoomPercent, type Photo } from './photos'

const photo = (o: Partial<Photo> = {}): Photo => ({ id: 7, channel_id: '-1001', message_id: 3, filename: 'a.jpg', size: 2048, width: 4000, height: 3000, thumbnail_url: 't', preview_url: 'p', image_url: 'i', channel_title: 'Main', ...o })

describe('channels', () => {
  it('shows only completed scans, merged and sorted', () => {
    const saved = [{ telegram_id: '-2', title: 'beta' }, { telegram_id: '-1', title: 'Alpha' }, { telegram_id: '-3', title: 'Never' }]
    const photoRows = [{ telegram_id: '-1', photo_count: 5, photo_scan_status: 'complete' }, { telegram_id: '-2', photo_count: 2, photo_scan_status: 'COMPLETE' }, { telegram_id: '-9', title: 'Removed', photo_scan_status: 'COMPLETE', photo_count: 1 }, { telegram_id: '-3', photo_scan_status: 'RUNNING' }]
    const r = scannedChannels(saved, photoRows)
    expect(r.map((c) => String(c.telegram_id))).toEqual(['-1', '-2', '-9'])
    expect(r[0]!.photo_count).toBe(5)
  })
  it('names, initials, avatars', () => {
    expect(channelTitle({ telegram_id: 1, title: 'Channel -5' })).toBe('Unknown channel'); expect(channelTitle({ telegram_id: 1, title: ' Real ' })).toBe('Real')
    expect(initials('Main Archive')).toBe('MA'); expect(initials('')).toBe('TG'); expect(initials('Zed')).toBe('Z')
    expect(channelAvatar({ telegram_id: '-1', updated_at: 'x y' })).toBe('/api/channels/-1/telegram-photo?v=x%20y'); expect(channelAvatar({ telegram_id: '-1', image: 'manual-upload:a' })).toBe('/api/channels/-1/image?v=0&w=160')
  })
  it('selects, toggles all and prunes', () => {
    const ch = [{ telegram_id: 'a' }, { telegram_id: 'b' }]
    expect(toggleId(['a'], 'b')).toEqual(['a', 'b']); expect(toggleId(['a', 'b'], 'a')).toEqual(['b'])
    expect(toggleAll(ch, [])).toEqual(['a', 'b']); expect(toggleAll(ch, ['a', 'b'])).toEqual([]); expect(allSelected([], [])).toBe(false)
    expect(pruneSelection(['a', 'z'], ch)).toEqual(['a'])
  })
})

describe('query and favourites', () => {
  it('builds the server query', () => {
    const q = { channels: ['-1', '-2'], q: 'cat', sort: 'size_desc' as const, favorites: false, favoriteIds: new Set<string>(), page: 2 }
    expect(photosUrl(q)).toBe('/api/photos?page=2&per=48&channels=-1%2C-2&q=cat&sort=size_desc')
    const p = photoParams({ ...q, favorites: true, favoriteIds: new Set(['5', '9']) })
    expect(p.get('favorites')).toBe('1'); expect(p.get('favorite_ids')).toBe('5,9')
    expect(SORTS.map((s) => s.value)).toEqual(['date_desc', 'date_asc', 'name_asc', 'size_desc'])
  })
  it('stores favourites safely', () => {
    const mem = new Map<string, string>()
    const st = { getItem: (k: string) => mem.get(k) ?? null, setItem: (k: string, v: string) => void mem.set(k, v) }
    const next = toggleFavorite(new Set(), photo()); expect([...next]).toEqual(['7'])
    writeFavorites(st, next); expect(mem.get(FAVORITES_KEY)).toBe('["7"]'); expect([...readFavorites(st)]).toEqual(['7'])
    expect([...toggleFavorite(next, photo())]).toEqual([])
    expect([...readFavorites({ getItem: () => '{bad' })]).toEqual([]); expect([...readFavorites(null)]).toEqual([])
    expect(() => writeFavorites({ setItem: () => { throw new Error('x') } }, next)).not.toThrow()
    expect([...toggleFavorite(new Set(), photo({ id: 0 }))]).toEqual([])
  })
  it('explains an empty result', () => {
    expect(emptyText('x', false).title).toBe('No photos match this search.'); expect(emptyText('', true).title).toMatch(/favourite/); expect(emptyText('', false).title).toMatch(/indexed/)
  })
})

describe('viewer', () => {
  it('formats the HUD', () => {
    expect(formatBytes(0)).toBe(''); expect(formatBytes(500)).toBe('500 B'); expect(formatBytes(2048)).toBe('2 KB'); expect(formatBytes(3 * 1048576)).toBe('3.0 MB')
    expect(formatDate('')).toBe(''); expect(formatDate('2026-01-05T10:00:00')).toMatch(/2026/); expect(formatDate('garbage-value')).toBe('garbage-value')
    expect(hud(photo({ message_date: '2026-01-05T10:00:00' }))).toMatchObject({ title: 'a.jpg', dims: '4000 × 3000', mp: '12.0 MP', size: '2 KB', channel: 'Main' })
    expect(hud(photo({ width: 0, height: 0, size: 0, filename: '', channel_title: '' }))).toMatchObject({ title: 'Photo 3', dims: '', mp: '', size: '', date: '', channel: '' })
    expect(counterText(0, 10)).toBe('1 / 10')
  })
  it('fits, zooms, rotates and pans', () => {
    const stage = { w: 1000, h: 500 }
    expect(fitScale({ w: 2000, h: 1000 }, stage, 0)).toBe(0.5); expect(fitScale({ w: 100, h: 100 }, stage, 0)).toBe(1)
    expect(fitScale({ w: 2000, h: 1000 }, stage, 90)).toBe(0.25); expect(fitScale({ w: 0, h: 0 }, stage, 0)).toBe(1)
    expect(zoomBy(0.5, 1, 0.5)).toBeCloseTo(0.625); expect(zoomBy(0.5, -1, 0.5)).toBe(0.5); expect(zoomBy(7.9, 1, 0.5)).toBe(8)
    expect(zoomPercent(1, 0.5)).toBe('200%'); expect(zoomPercent(0.5, 0.5)).toBe('100%')
    expect(rotate(0, -90)).toBe(270); expect(rotate(270, 90)).toBe(0)
    expect(clampPan(9999, 9999, { w: 2000, h: 1000 }, stage, 1, 0)).toEqual({ x: 500, y: 250 })
    expect(clampPan(50, 50, { w: 100, h: 100 }, stage, 1, 0)).toEqual({ x: 0, y: 0 })
  })
  it('steps with stops at the ends, and wraps for slideshow', () => {
    expect(stepIndex(0, -1, 5)).toBeNull(); expect(stepIndex(4, 1, 5)).toBeNull(); expect(stepIndex(2, 1, 5)).toBe(3)
    expect(stepIndex(4, 1, 5, true)).toBe(0); expect(stepIndex(0, -1, 5, true)).toBe(4); expect(stepIndex(0, 1, 0)).toBeNull()
  })
  it('windows the filmstrip', () => {
    expect(stripRange(0, 100)).toEqual([0, 24]); expect(stripRange(50, 100)).toEqual([38, 62]); expect(stripRange(99, 100)).toEqual([76, 100]); expect(stripRange(1, 5)).toEqual([0, 5])
  })
})

describe('delete', () => {
  it('uses the classic wording and request', () => {
    expect(deleteCopy(false, 'a.jpg', 'Main').text).toMatch(/Remove "a.jpg" from the Archive X Photos library/)
    expect(deleteCopy(true, 'a.jpg', 'Main').text).toMatch(/its Telegram message in "Main"/)
    expect(deleteBody(true)).toEqual({ scope: 'archive_and_telegram' }); expect(deleteBody(false)).toEqual({ scope: 'archive_only' })
    expect(deletePath({ channel_id: '-1001', message_id: 3 })).toBe('/api/photos/-1001/3/delete')
  })
})
