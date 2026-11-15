import { describe, expect, it } from 'vitest'
import { activityByPage, runningLabel } from './activity'

const job = (type: string, status = 'RUNNING') => ({ type, status })

describe('activityByPage', () => {
  it('counts running jobs on the page they belong to, and all of them under Operations', () => {
    const a = activityByPage([job('TELEGRAM_TRANSFER'), job('MEDIA_MANAGER'), job('METADATA_CHANNEL_PIPELINE'), job('BACKUP_CREATE')])
    expect(a).toEqual({ '/transfers': 2, '/videos': 1, '/settings': 1, '/operations': 4 })
  })
  it('ignores paused, queued and interrupted jobs', () => {
    expect(activityByPage([job('TELEGRAM_TRANSFER', 'PAUSED'), job('METADATA_SYNC', 'QUEUED'), job('UPLOAD_LOCAL', 'INTERRUPTED')])).toEqual({})
  })
  it('counts pausing and cancelling jobs, which are still working', () => {
    expect(activityByPage([job('MEDIA_MANAGER', 'PAUSING'), job('MEDIA_MANAGER', 'CANCEL_REQUESTED')])['/transfers']).toBe(2)
  })
  it('puts entity data under both Performers and Studios', () => {
    expect(activityByPage([job('ENTITY_IMAGE_SYNC')])).toEqual({ '/performers': 1, '/studios': 1, '/operations': 1 })
  })
  it('shows playback on Videos only, not under Operations', () => {
    expect(activityByPage([{ type: 'PLAYBACK', status: 'BUFFERING' }])).toEqual({ '/videos': 1 })
  })
  it('leaves out job types it does not know and survives odd input', () => {
    expect(activityByPage([job('SOMETHING_NEW')])).toEqual({ '/operations': 1 })
    expect(activityByPage(undefined)).toEqual({})
    expect(activityByPage([null, 3, {}])).toEqual({})
  })
  it('words the count', () => expect(runningLabel(3)).toBe('3 running'))
})
