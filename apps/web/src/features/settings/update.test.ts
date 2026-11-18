import { describe, expect, it } from 'vitest'
import type { UpdateState } from '@/features/watch/nativeBridge'
import { agoText, updateView } from './update'

const base: UpdateState = { phase: 'idle', current: '30.4.3', version: null, notes: null, received: 0, total: 0, percent: null, error: null, checkedAt: null }
const NOW = 1_000_000

describe('updateView', () => {
  it('says it has not looked yet, and offers to', () => {
    const v = updateView(base, NOW)
    expect(v.title).toBe('Not checked yet')
    expect(v.actions).toEqual(['check'])
  })
  it('says it is up to date and when it looked', () => {
    const v = updateView({ ...base, phase: 'uptodate', checkedAt: NOW - 150 }, NOW)
    expect([v.title, v.tone, v.detail]).toEqual(['Archive X is up to date', 'ok', 'Version 30.4.3 · checked 2 min ago'])
  })
  it('offers to update when a version is ready, with its notes', () => {
    const v = updateView({ ...base, phase: 'available', version: '30.5.0', notes: 'Faster captions.' }, NOW)
    expect(v.title).toBe('Version 30.5.0 is ready')
    expect(v.detail).toContain('Faster captions.')
    expect(v.actions).toEqual(['install', 'check'])
  })
  it('shows the download with its size and percent, and offers nothing while it runs', () => {
    const v = updateView({ ...base, phase: 'downloading', version: '30.5.0', received: 5_000_000, total: 20_000_000, percent: 25 }, NOW)
    expect(v).toMatchObject({ title: 'Downloading version 30.5.0', bar: true, pct: 25, actions: [], tone: 'busy' })
    expect(v.detail).toBe('4.77 MB of 19.1 MB · 25%')
  })
  it('shows a bar that waits, not a made-up percent, while the size is not known', () => {
    const v = updateView({ ...base, phase: 'downloading', version: '30.5.0', received: 0 }, NOW)
    expect([v.pct, v.detail]).toEqual([0, 'Starting the download…'])
    expect(updateView({ ...base, phase: 'downloading', received: 2_000_000 }, NOW).detail).toBe('1.91 MB so far')
  })
  it('says the app is about to restart while installing', () => {
    const v = updateView({ ...base, phase: 'installing', percent: 100 }, NOW)
    expect([v.title, v.bar, v.pct]).toEqual(['Installing the update', true, 100])
  })
  it('gives the reason when it failed and lets the person try again (the download again when a version is known)', () => {
    expect(updateView({ ...base, phase: 'error', error: 'no network' }, NOW)).toMatchObject({ tone: 'bad', detail: 'no network', actions: ['check'] })
    expect(updateView({ ...base, phase: 'error', error: 'bad signature', version: '30.5.0' }, NOW).actions).toEqual(['retry', 'check'])
  })
})

describe('agoText', () => {
  it('reads naturally', () => {
    expect([agoText(null, NOW), agoText(NOW - 5, NOW), agoText(NOW - 600, NOW), agoText(NOW - 7300, NOW), agoText(NOW - 200_000, NOW)]).toEqual(['', 'just now', '10 min ago', '2 h ago', '2 d ago'])
  })
})
