import { describe, expect, it } from 'vitest'
import { activityRows, displayName, footerFor, laneName, mergeActivity, parseDetail, pipeline, progressSummary, runInfo, shortId, toneOf, type Activity } from './jobdetail'

const age = (n: number) => `${n}s`

describe('progress', () => {
  it('shows counts when the total is known, else the percentage', () => {
    expect(progressSummary({ total: 200, completed: 50 })).toEqual({ percent: 25, label: '50 / 200' })
    expect(progressSummary({ progress: 37.25 })).toEqual({ percent: 37.25, label: '37.3%' })
    expect(progressSummary({ total: 10, completed: 99 }).percent).toBe(100)
    expect(progressSummary({ total: 'x', completed: -5, progress: 'nope' })).toEqual({ percent: 0, label: '0.0%' })
  })
  it('colours by status', () => {
    expect(toneOf('COMPLETED')).toBe('good'); expect(toneOf('FAILED')).toBe('bad'); expect(toneOf('PAUSED')).toBe('warn'); expect(toneOf('RECOVERING')).toBe('accent'); expect(toneOf('QUEUED')).toBe('muted')
  })
})

describe('names', () => {
  it('names the kind of work', () => {
    expect(laneName('TELEGRAM_TRANSFER', 'local')).toBe('Local → channel upload')
    expect(laneName('TELEGRAM_TRANSFER', 'telegram')).toBe('Telegram → Telegram transfer')
    expect(laneName('TELEGRAM_TRANSFER', '', 'content_automation')).toBe('Content Automation · delivery')
    expect(laneName('METADATA_ASSETS')).toBe('Metadata & Assets')
    expect(laneName('SOMETHING')).toBe('SOMETHING')
    expect(laneName('')).toBe('Background work')
  })
  it('uses the job name unless it is only its type', () => {
    expect(displayName({ name: 'Nightly scan', type: 'METADATA_ASSETS' }, '1').name).toBe('Nightly scan')
    expect(displayName({ name: 'metadata_assets', type: 'METADATA_ASSETS' }, '1').name).toBe('Metadata & Assets')
    expect(displayName({ type: '' }, '7').name).toBe('Background work')
    expect(displayName({ name: 'x', detail: '{"source":"local_files"}', type: 'TELEGRAM_TRANSFER' }, '1').kind).toBe('Local → channel upload')
  })
  it('shortens long ids', () => {
    expect(shortId('abc')).toBe('abc'); expect(shortId('0123456789abcdef')).toBe('01234567…')
  })
  it('reads JSON detail only when it is JSON', () => {
    expect(parseDetail({ detail: '{"a":1}' })).toEqual({ a: 1 })
    expect(parseDetail({ detail: 'plain text' })).toBeNull()
    expect(parseDetail({ detail: '{broken' })).toBeNull()
    expect(parseDetail({})).toBeNull()
  })
})

describe('pipeline', () => {
  const states = (r: ReturnType<typeof pipeline>) => r.stages.map((x) => x.state)
  it('uses the stage the job says it is in', () => {
    const r = pipeline({ current_operation: 'Verifying delivery of file 3' }, 'RUNNING', 10)
    expect(r.stages.map((x) => x.label)).toEqual(['Queued', 'Dispatched', 'Processing', 'Verification', 'Cleanup'])
    expect(states(r)).toEqual(['done', 'done', 'done', 'active', 'todo'])
    expect(r).toMatchObject({ completed: 3, total: 5 })
  })
  it('falls back to how far along the job is, never staying on the first stage while running', () => {
    expect(states(pipeline({ current_operation: '' }, 'RUNNING', 0))[1]).toBe('active')
    expect(states(pipeline({}, 'RUNNING', 100))[4]).toBe('active')
  })
  it('marks everything done when completed, and the current stage failed when failed', () => {
    expect(states(pipeline({}, 'COMPLETED', 100))).toEqual(['done', 'done', 'done', 'done', 'done'])
    expect(states(pipeline({ current_operation: 'processing' }, 'FAILED', 40))).toEqual(['done', 'done', 'failed', 'todo', 'todo'])
  })
  it('has nothing active when the job is waiting in the queue and says nothing', () => {
    expect(states(pipeline({}, 'QUEUED', 0))).toEqual(['todo', 'todo', 'todo', 'todo', 'todo'])
  })
  it('uses the local-upload stages, and the photo stages for photos', () => {
    expect(pipeline({ type: 'UPLOAD_LOCAL' }, 'RUNNING', 50).stages.map((x) => x.label)).toEqual(['Inventory', 'Duplicate check', 'Metadata saved', 'Upload', 'Verify delivery', 'Clean up source'])
    expect(pipeline({ type: 'UPLOAD_LOCAL', detail: '{"media_type":"PHOTOS"}' }, 'RUNNING', 50).stages.map((x) => x.label)).toEqual(['Scan photos', 'Upload photos', 'Complete'])
    expect(pipeline({ type: 'TELEGRAM_TRANSFER', source: 'local' }, 'RUNNING', 50).total).toBe(6)
  })
})

describe('run information', () => {
  it('shows transfer details for transfers', () => {
    const r = runInfo({ type: 'TELEGRAM_TRANSFER', parts_in_flight: 2, flood_wait: 12.2, retry_count: 1 }, null, '0123…', age)
    expect(r).toEqual([{ label: 'Parts in flight', value: '2' }, { label: 'FloodWait', value: '13s' }, { label: 'Retries', value: '1' }, { label: 'Job ID', value: '#0123…' }])
    expect(runInfo({ type: 'UPLOAD_LOCAL' }, null, 'x', age).find((i) => i.label === 'FloodWait')!.value).toBe('None')
    expect(runInfo({ type: 'UPLOAD_LOCAL' }, { transfer: { parts_in_flight: 4 } }, 'x', age)[0]).toEqual({ label: 'Parts in flight', value: '4' })
  })
  it('shows worker and watchdog detail for other jobs', () => {
    const r = runInfo({ type: 'METADATA_ASSETS', worker_id: 'w1', worker_state: 'BUSY', attempt: 2, watchdog_state: 'HEALTHY', watchdog_heartbeat_age: 3, dependencies: [{ depends_on_job_id: 'a', dependency_status: 'COMPLETED' }] }, null, 's', age)
    const by = Object.fromEntries(r.map((i) => [i.label, i.value]))
    expect(by).toMatchObject({ Type: 'METADATA_ASSETS', Worker: 'w1 · BUSY', Attempt: '2', Watchdog: 'HEALTHY', 'Watchdog ages': 'HB 3s · Activity — · Progress —', Dependencies: 'a:COMPLETED', Admission: '—', 'Job ID': '#s' })
    expect(runInfo({}, null, 's', age).find((i) => i.label === 'Dependencies')!.value).toBe('None')
  })
})

describe('activity', () => {
  const ev = (id: number, message: string, over: Partial<Activity> = {}): Activity => ({ id, message, ...over })
  it('merges older and newest pages without duplicates, oldest first', () => {
    const older = new Map([['1', ev(1, 'a')], ['2', ev(2, 'b')]])
    const latest = new Map([['2', ev(2, 'b2')], ['3', ev(3, 'c')]])
    expect(mergeActivity(older, latest).map((x) => [x.id, x.message])).toEqual([[1, 'a'], [2, 'b2'], [3, 'c']])
  })
  it('shows newest first with level, operation, when and details', () => {
    const rows = activityRows([ev(1, 'first'), ev(2, 'second', { level: 'warn', operation: 'Uploading', created_at: '2024-01-02T03:04:05Z', job_id: 'child', event: 'x.y', details: { a: 1 } })], () => 'Jan 2, 03:04')
    expect(rows.map((r) => r.message)).toEqual(['second', 'first'])
    expect(rows[0]).toMatchObject({ level: 'WARN', operation: 'Uploading', meta: 'Jan 2, 03:04 · job child · x.y' })
    expect(rows[0]!.details).toContain('"a": 1')
    expect(rows[1]).toMatchObject({ level: 'INFO', operation: '' })
  })
  it('does not repeat an operation that is the message, and names an empty event', () => {
    expect(activityRows([ev(1, '', { operation: 'Scanning' })], () => '')[0]).toMatchObject({ message: 'Scanning', operation: '' })
    expect(activityRows([{ id: 1 }], () => '')[0]!.message).toBe('Activity')
  })
})

describe('footer', () => {
  it('offers nothing for finished jobs', () => {
    for (const st of ['COMPLETED', 'COMPLETED_WITH_ERRORS', 'FAILED', 'ERROR', 'CANCELLED']) expect(footerFor(st)).toBeNull()
  })
  it('picks one primary action by status, and always cancel', () => {
    expect(footerFor('RUNNING')).toEqual({ primary: 'pause', cancel: 'Cancel job' })
    expect(footerFor('PAUSED')).toEqual({ primary: 'resume', cancel: 'Cancel job' })
    expect(footerFor('INTERRUPTED')).toEqual({ primary: 'recover', cancel: 'Cancel job' })
    expect(footerFor('RECOVERING')).toEqual({ primary: 'retry-recovery', cancel: 'Cancel job' })
    expect(footerFor('QUEUED')).toEqual({ primary: null, cancel: 'Cancel job' })
    expect(footerFor('PAUSING')).toEqual({ primary: null, cancel: 'Cancel job' })
  })
  it('shows a disabled "Cancelling…" while a cancel is under way', () => {
    expect(footerFor('CANCEL_REQUESTED')).toEqual({ primary: null, cancel: 'Cancelling…' })
  })
})
