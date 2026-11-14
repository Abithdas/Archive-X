import { beforeEach, describe, expect, it } from 'vitest'
import { bulkBody, countLabel, recheckMessage, titleProblem, unknownConfirm, unknownOk, unlockBody, updatedMessage, videos } from './bulk'
import { pickKey, pickedList, useSelection } from './selection'

beforeEach(() => useSelection.getState().exit())

describe('selection store', () => {
  it('starts inactive and empty', () => {
    expect(useSelection.getState()).toMatchObject({ active: false, picked: {} })
  })
  it('toggles a video on and off by channel:message', () => {
    const s = useSelection.getState()
    s.enter()
    s.toggle('-100', 5)
    expect(useSelection.getState().picked).toEqual({ '-100:5': { channel_id: '-100', message_id: 5 } })
    s.toggle('-100', 5)
    expect(useSelection.getState().picked).toEqual({})
    expect(pickKey('-100', 5)).toBe('-100:5')
  })
  it('select-many adds without duplicating, and keeps earlier picks', () => {
    const s = useSelection.getState()
    s.toggle('-1', 1)
    s.selectMany([{ _cid: '-1', id: 1 }, { _cid: '-1', id: 2 }, { _cid: '-2', id: 1 }])
    expect(pickedList(useSelection.getState().picked)).toHaveLength(3)
  })
  it('clear keeps the mode on; exit turns it off and forgets the picks', () => {
    const s = useSelection.getState()
    s.enter(); s.toggle('-1', 1); s.clear()
    expect(useSelection.getState()).toMatchObject({ active: true, picked: {} })
    s.toggle('-1', 1); s.exit()
    expect(useSelection.getState()).toMatchObject({ active: false, picked: {} })
  })
})

describe('bulk rules', () => {
  const two = [{ channel_id: '-1', message_id: 1 }, { channel_id: '-1', message_id: 2 }]
  it('always locks, trims the value, and blanks it for Unknown', () => {
    expect(bulkBody(two, 'title', 'move', '  New  ')).toEqual({ videos: two, field: 'title', target_name: 'New', action: 'move', lock: true })
    expect(bulkBody(two, 'performer', 'unknown', 'ignored')).toMatchObject({ target_name: '', action: 'unknown', lock: true })
  })
  it('words counts and results as the classic page does', () => {
    expect(countLabel(3)).toBe('3 selected')
    expect(videos(1)).toBe('1 video')
    expect(videos(2)).toBe('2 videos')
    expect(updatedMessage(4, 9)).toBe('4 video(s) updated and locked.')
    expect(updatedMessage(undefined, 9)).toBe('9 video(s) updated and locked.')
    expect(recheckMessage({ ok: true, count: 2 }, 5)).toBe('Metadata recheck queued for 2 video(s).')
    expect(recheckMessage({ ok: true }, 5)).toBe('Metadata recheck queued for 5 video(s).')
    expect(recheckMessage({ ok: false, detail: 'nope' }, 5)).toBe('nope')
    expect(recheckMessage({ ok: false }, 5)).toBe('Recheck failed')
    expect(unlockBody(two)).toEqual({ videos: two, field: 'all' })
  })
  it('asks before clearing, naming the field and the count', () => {
    expect(unknownConfirm('performer', 3)).toContain('performer of 3 videos to Unknown')
    expect(unknownConfirm('site', 1)).toContain('studio of 1 video to Unknown')
    expect(unknownOk('all')).toBe('Move all to Unknown')
    expect(unknownOk('site')).toBe('Clear studio')
  })
  it('requires a title', () => {
    expect(titleProblem('  ')).toBe('Enter the corrected video title.')
    expect(titleProblem('x')).toBe('')
  })
})
