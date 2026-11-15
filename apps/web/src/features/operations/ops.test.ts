import { describe, expect, it } from 'vitest'
import { attention, diagnosticTiles, evtModule, filterJobs, filterLog, fmtAge, fmtRate, healthTiles, jobCounts, laneRows, policyLine, resourceCards, telemetryTiles } from './ops'

describe('formatting', () => {
  it('formats rates and ages like the classic page', () => {
    expect([fmtRate(0), fmtRate(1536), fmtRate(5 * 1024 ** 2)]).toEqual(['0 B/s', '1.5 KB/s', '5.0 MB/s'])
    expect([fmtAge(7), fmtAge(95), fmtAge(3700)]).toEqual(['7s', '1m 35s', '1h 1m'])
  })
})

describe('jobs', () => {
  const jobs = [{ status: 'RUNNING' }, { status: 'PAUSED' }, { status: 'QUEUED' }, { status: 'FAILED' }, { status: 'CANCELLED' }, { status: 'COMPLETED' }, { status: 'COMPLETED_WITH_ERRORS' }]
  it('counts by status', () => expect(jobCounts(jobs)).toEqual({ running: 1, queued: 1, failed: 1, completed: 1, withErrors: 1 }))
  it('filters into the five tabs', () => {
    expect(filterJobs(jobs, 'all')).toHaveLength(7)
    expect(filterJobs(jobs, 'running')).toHaveLength(2)
    expect(filterJobs(jobs, 'failed')).toHaveLength(2)
    expect(filterJobs(jobs, 'completed')).toHaveLength(2)
    expect(filterJobs(jobs, 'queued')).toHaveLength(1)
  })
})

describe('health and attention', () => {
  it('reports offline Telegram as bad and asks the operator to reconnect', () => {
    const st = { telegram_connected: false, stats: {}, governor: { max_ranges: 8 }, jobs: [] }
    const t = Object.fromEntries(healthTiles(st).map((x) => [x.label, x]))
    expect(t.Telegram).toMatchObject({ value: 'Offline', tone: 'bad' })
    expect(t.Playback).toMatchObject({ value: 'Waiting for Telegram', tone: 'warn' })
    expect(t['Rate control']!.value).toBe('8 range slots')
    expect(attention(st)[0]).toMatchObject({ key: 'telegram-offline', tone: 'bad' })
  })
  it('shows a FloodWait in rate control and as a warning', () => {
    const st = { telegram_connected: true, governor: { flood_wait_remaining: 41.2 } }
    expect(healthTiles(st).find((x) => x.label === 'Rate control')).toMatchObject({ value: 'FloodWait 42s', tone: 'warn' })
    expect(attention(st).map((x) => x.key)).toEqual(['flood-wait'])
  })
  it('counts failed jobs with correct plural, and says all clear otherwise', () => {
    expect(attention({ telegram_connected: true, stats: { failed: 1 } })[0]!.text).toBe('1 failed job require review.')
    expect(attention({ telegram_connected: true, stats: { failed: 2 } })[0]!.text).toBe('2 failed jobs require review.')
    expect(attention({ telegram_connected: true })[0]).toMatchObject({ key: 'all-clear', tone: 'ok' })
  })
  it('counts waiting and retrying workers', () => {
    const st = { telegram_connected: true, jobs: [{ status: 'RUNNING' }, { status: 'QUEUED' }], job_orchestration: { states: { WAITING: 2, BLOCKED: 1 }, retrying: 3 } }
    expect(healthTiles(st).find((x) => x.label === 'Workers')!.value).toBe('1 active · 1 queued · 3 waiting · 3 retrying')
  })
})

describe('telemetry', () => {
  it('shows slots, rate, and a degraded governor', () => {
    const t = telemetryTiles({ governor: { bytes_per_second: 2048, active_ranges: 1, max_ranges: 8, active_rpc: 0, max_rpc: 1, last_error: 'x' } })
    expect(t.rate).toBe('2.0 KB/s')
    expect(t.tiles.map((x) => x.value)).toEqual(['1 / 8', '0 / 1', 'None', 'Degraded'])
  })
})

describe('diagnostics', () => {
  it('summarises levels, watchdogs and the last error', () => {
    const r = { levels: { ERROR: 2, CRITICAL: 1 }, diagnostic_health: { state: 'ATTENTION' }, events: { INFO: 5, WARN: 2 }, watchdogs: { jobs: [{ state: 'STALLED' }, { state: 'OK' }] }, recent_errors: [{ error_type: 'ValueError', message: 'boom' }], http: { latency_ms: { p95: 123.4 } } }
    const t = Object.fromEntries(diagnosticTiles(r).map((x) => [x.label, x]))
    expect(t.Health).toMatchObject({ value: 'ATTENTION', tone: 'warn' })
    expect(t.Errors).toMatchObject({ value: '3', tone: 'warn' })
    expect(t.Events!.value).toBe('7')
    expect(t.Watchdogs!.value).toBe('1 attention')
    expect(t['Watchdog guardian']).toBeUndefined()
    expect(t['Last issue']!.value).toBe('ValueError · boom')
    expect(t['HTTP p95']!.value).toBe('123 ms')
  })
  it('has sensible empty values', () => {
    const t = Object.fromEntries(diagnosticTiles({}).map((x) => [x.label, x.value]))
    expect(t).toMatchObject({ Health: 'UNKNOWN', Errors: '0', Watchdogs: 'Healthy', 'Last issue': 'None', 'Telegram Transfer': 'Idle' })
  })
})

describe('resources', () => {
  const res = { playback_active: true, playback_streams: 2, background_active: 1, background_capacity: 2, background_waiting: 3, metadata_active: 1, metadata_capacity: 1, non_metadata_active: 0, non_metadata_capacity: 1, category_active: { metadata: 1, upload: 0 }, control: { mode_label: 'Automatic', effective_limits: { metadata: 2, upload: 2 } }, media_policy: { preview_workers: 1 } }
  it('builds the four cards', () => {
    const c = resourceCards(res, { governor: { traffic_mode: 'NORMAL', max_rpc: 1, max_ranges: 8 }, jobs: [{ status: 'RUNNING' }], job_orchestration: { retrying: 1, active_leases: 2 }, resource_leases: { active: 4, expired: 1 } })
    expect(c.map((x) => x.value)).toEqual(['ACTIVE', '1 / 2', 'NORMAL', '1 active'])
    expect(c[0]!.sub).toBe('2 streams')
    expect(c[1]!.sub).toBe('3 waiting · metadata 1/1 · other 0/1')
    expect(c[3]!.sub).toContain('4 resource leases · 1 expired')
  })
  it('computes lane fill, falling back to the media policy limit', () => {
    const rows = laneRows(res)
    expect(rows.find((r) => r.key === 'metadata')).toMatchObject({ active: 1, limit: 2, pct: 50 })
    expect(rows.find((r) => r.key === 'preview')).toMatchObject({ limit: 1, pct: 0 })
  })
  it('writes the policy line', () => expect(policyLine(res, { resource_leases: { active: 0 } }, '18:42')).toBe('Policy: Automatic · Durable resource leases: 0 active · Updated 18:42'))
})

describe('log', () => {
  const ev = [
    { level: 'INFO', message: 'Telegram Transfer Manager started', outcome: 'INFO' },
    { level: 'SUCCESS', message: 'Backup & Restore done', outcome: 'SUCCESS' },
    { level: 'WARN', message: 'Playback stalled', outcome: 'DEGRADED' },
    { level: 'ERROR', message: 'Something failed', outcome: 'FAILURE', event: 'x.failed' },
  ]
  const base = { level: '' as const, module: '', outcome: '', query: '' }
  it('classifies modules', () => expect([evtModule('Content Automation: x'), evtModule('VLC opened'), evtModule('hello')]).toEqual(['Content Automation', 'playback', 'other']))
  it('Info includes Success; other levels are exact', () => {
    expect(filterLog(ev, { ...base, level: 'INFO' })).toHaveLength(2)
    expect(filterLog(ev, { ...base, level: 'WARN' })).toHaveLength(1)
  })
  it('filters by module, outcome and free text', () => {
    expect(filterLog(ev, { ...base, module: 'playback' })).toHaveLength(1)
    expect(filterLog(ev, { ...base, outcome: 'FAILURE' })).toHaveLength(1)
    expect(filterLog(ev, { ...base, query: 'x.failed' })).toHaveLength(1)
  })
  it('keeps only the newest entries', () => expect(filterLog(Array.from({ length: 200 }, (_, i) => ({ message: String(i) })), base)).toHaveLength(80))
})
