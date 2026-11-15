import { describe, expect, it } from 'vitest'
import { withoutCopy, ALL_INDEXED, BATCH_LIMIT, candidateKey, channelName, confirmCopy, deleteBody, deletePlan, findKeeper, formatDuration, formatSize, frameUrl, resultWarnings, scanSummary, scanTitles, scanUrl, scopeOptions, selectionState, sharesIdentity, validIds } from './dupes'

const row = (cid: number, mid: number, extra = {}) => ({ channel_id: cid, message_id: mid, title: `V${mid}`, size: 100, width: 1920, height: 1080, document_id: 'D1', ...extra })
const group = { candidates: [row(1, 1), row(1, 2), row(2, 3)] }

describe('dupes', () => {
  it('formats sizes, durations and names', () => {
    expect(formatSize(0)).toBe('0 B'); expect(formatSize(1536)).toBe('1.5 KB'); expect(formatSize(5 * 1024 ** 2)).toBe('5.0 MB'); expect(formatSize(3 * 1024 ** 3)).toBe('3.00 GB'); expect(formatSize('x')).toBe('0 B')
    expect(formatDuration(125)).toBe('2:05'); expect(formatDuration(0)).toBe('Unknown')
    expect(channelName('-1', 'Real')).toBe('Real'); expect(channelName('-1', 'Channel -1', [{ telegram_id: '-1', title: 'Saved' }])).toBe('Saved'); expect(channelName('-9', '')).toBe('Unknown channel'); expect(channelName('Some name', '')).toBe('Some name')
  })
  it('builds unique, sorted scope options with the cross-channel choice first', () => {
    const o = scopeOptions([{ telegram_id: '-2', title: 'beta' }, { telegram_id: '-1', title: 'Alpha' }, { telegram_id: '-1', title: 'dup' }, { telegram_id: 'abc', title: 'bad' }])
    expect(o.map((x) => x.value)).toEqual(['', ALL_INDEXED, '-1', '-2']); expect(o[2]!.label).toBe('Alpha')
  })
  it('identifies copies by document id, or hash plus equal non-zero size only', () => {
    expect(sharesIdentity({ document_id: 'a' }, { registry_key: 'doc:a' })).toBe(true)
    expect(sharesIdentity({ oshash: 'AB', size: 5 }, { registry_oshash: 'ab', size: 5 })).toBe(true)
    expect(sharesIdentity({ oshash: 'ab', size: 5 }, { oshash: 'ab', size: 6 })).toBe(false)
    expect(sharesIdentity({ oshash: 'ab', size: 0 }, { oshash: 'ab', size: 0 })).toBe(false)
    expect(sharesIdentity({ title: 'same' }, { title: 'same' })).toBe(false)
  })
  it('needs an unselected directly matching keeper', () => {
    expect(findKeeper(group, group.candidates[0]!, new Set([candidateKey(group.candidates[0]!)]))?.message_id).toBe(2)
    expect(findKeeper(group, group.candidates[0]!, new Set(['1:2', '2:3']))).toBeNull()
  })
  it('tracks the selection and its limits', () => {
    expect(selectionState([group], new Set()).canDelete).toBe(false)
    const one = selectionState([group], new Set(['1:1']))
    expect(one).toMatchObject({ count: 1, channels: 1, canDelete: true, countText: '1 selected · 1 channel' })
    const all = selectionState([group], new Set(['1:1', '1:2', '2:3']))
    expect(all.missingKeeper).toBe(true); expect(all.canDelete).toBe(false); expect(all.hint).toMatch(/Leave one/); expect(all.countText).toBe('3 selected · 2 channels')
    expect(selectionState([group], new Set(['1:1']), true).canDelete).toBe(false)
    const big = { candidates: Array.from({ length: BATCH_LIMIT + 2 }, (_, i) => row(1, i + 1)) }
    expect(selectionState([big], new Set(big.candidates.slice(0, BATCH_LIMIT + 1).map(candidateKey))).hint).toMatch(/no more than 20/)
    expect(selectionState([big], new Set(big.candidates.slice(0, BATCH_LIMIT).map(candidateKey))).canSelectMore).toBe(false)
  })
  it('plans deletes with keepers, refusing when one is missing', () => {
    const p = deletePlan([group], new Set(['1:1', '2:3']))!
    expect(p.map((x) => x.keeper.message_id)).toEqual([2, 2])
    expect(p[0]!.label).toBe('• V1 — Unknown channel, message 1, 1920×1080, 100 B')
    expect(deletePlan([group], new Set(['1:1', '1:2', '2:3']))).toBeNull()
    expect(deleteBody(p[0]!)).toEqual({ confirm_delete: true, channel_id: 1, message_id: 1, keep_channel_id: 1, keep_message_id: 2 })
    expect(validIds(deleteBody({ ...p[0]!, keeper: { channel_id: 0, message_id: 2 } }))).toBe(false)
    const c = confirmCopy(p); expect(c.title).toBe('Delete 2 selected videos?'); expect(c.ok).toBe('Delete 2 videos'); expect(c.text).toMatch(/from 2 Telegram channels/)
  })
  it('summarises scans and titles', () => {
    const s = scanSummary({ scope: 'all_indexed', indexed_video_count: 72, indexed_channel_count: 4, complete_channel_count: 0, groups: [group], warning: 'w' })
    expect(s).toMatchObject({ groups: 1, copies: 3, all: true, state: 'Scan complete · check coverage' }); expect(s.detail).toContain('72 indexed videos across 4 channels')
    expect(scanSummary({ indexed_video_count: 0, groups: [] }).emptyNote).toMatch(/No active indexed videos/)
    expect(scanTitles({ scope: 'all_indexed' }).title).toBe('Duplicate videos across channels'); expect(scanTitles({ channel_id: '-1', channel_title: 'Main' }).title).toBe('Duplicate videos in Main')
    expect(scanUrl(ALL_INDEXED)).toBe('/api/channel-maintenance/duplicates/all'); expect(scanUrl('-1001')).toBe('/api/channel-maintenance/duplicates?channel_id=-1001')
    expect(frameUrl('-1', 5, 11, 'r 1')).toBe('/api/play/preview-frame/-1/5/11?rev=r%201')
    expect(resultWarnings({ thumbnail_deleted: true, thumbnail_message_id: 9, warnings: ['x'] })).toEqual(['Thumbnail 9 deleted', 'x'])
  })
})

describe('withoutCopy', () => {
  const c = (mid: number) => ({ channel_id: '-1', message_id: mid })
  const scan = { ok: true, groups: [{ _id: 0, candidates: [c(1), c(2), c(3)], duplicate_count: 3 }, { _id: 1, candidates: [c(4), c(5)], duplicate_count: 2 }] }
  it('takes one deleted copy out and keeps the count right', () => {
    const next = withoutCopy(scan, '-1:2')
    expect(next.groups![0]!.candidates!.map((x) => x.message_id)).toEqual([1, 3]); expect(next.groups![0]!.duplicate_count).toBe(2); expect(next.groups![0]!._id).toBe(0)
  })
  it('drops a group once it is no longer a duplicate, and leaves the others (and their ids) alone', () => {
    const next = withoutCopy(scan, '-1:4')
    expect(next.groups!.map((g) => g._id)).toEqual([0]); expect(scan.groups).toHaveLength(2)   // the original is untouched
  })
  it('ignores a key that is not there', () => { expect(withoutCopy(scan, '-1:99').groups).toHaveLength(2) })
})
