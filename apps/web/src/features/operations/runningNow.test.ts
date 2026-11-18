import { describe, expect, it } from 'vitest'
import { clock, runningNow } from './runningNow'

describe('runningNow', () => {
  it('flags a transfer thread that has no job row', () => {
    const r = runningNow({ media_manager: { running: true, job_id: 'j9', live: 'Converting to MP4 · 12%', heartbeat_age: 3 }, processes: [] }, [{ id: 'other' }])
    expect(r.orphanTransfer).toEqual({ operation: 'Converting to MP4 · 12%', quietSeconds: 3 })
  })
  it('is quiet when the job is listed', () => {
    expect(runningNow({ media_manager: { running: true, job_id: 'j9' } }, [{ id: 'j9' }]).orphanTransfer).toBeNull()
  })
  it('maps child processes', () => {
    const r = runningNow({ processes: [{ pid: 4, label: 'FFmpeg · CONVERT', kind: 'ffmpeg', running_seconds: 61, progress: 40, alive: true }] }, [])
    expect(r.processes[0]).toMatchObject({ pid: 4, seconds: 61, progress: 40 })
  })
  it('formats time', () => {
    expect(clock(61)).toBe('1m 01s')
    expect(clock(3700)).toBe('1h 01m')
  })
})
