import { describe, expect, it } from 'vitest'
import { buildNotifications, eventToNotice, groupByDay, safeText } from './notifications'

const NOW = Date.parse('2026-11-06T12:00:00Z')
const ev = (message: string, extra: Record<string, unknown> = {}) => ({ level: 'INFO', message, created_at: '2026-11-06T11:00:00Z', ...extra })

describe('safeText', () => {
  it('drops paths, urls, ids and bracketed detail', () => {
    expect(safeText('Backup verified at /home/me/backups/x.zip https://example.com id=abc123 [secret]')).toBe('Backup verified at')
  })
  it('refuses json and stack traces', () => {
    expect(safeText('{"a": 1}')).toBe('')
    expect(safeText('Traceback (most recent call last)')).toBe('')
    expect(safeText('ValueError: boom')).toBe('')
  })
  it('keeps only the first line and clamps length', () => expect(safeText('x'.repeat(200) + '\nsecond', 20)).toHaveLength(20))
})

describe('eventToNotice', () => {
  it('hides routine startup noise', () => {
    expect(eventToNotice(ev('Startup ready in 1.1s', { event: 'startup.timing' }), NOW)).toBeNull()
    expect(eventToNotice({ ...ev('Telegram API credentials are missing from config.json'), level: 'WARN' }, NOW)).toBeNull()
  })
  it('shows a verified backup as a success', () => {
    const n = eventToNotice(ev('Backup verified'), NOW)
    expect(n).toMatchObject({ title: 'Backup verified', tone: 'success', icon: 'check' })
  })
  it('turns a flood wait into a short warning', () => {
    const n = eventToNotice({ ...ev('Telegram FloodWait of 42s while sending'), level: 'WARN' }, NOW)
    expect(n).toMatchObject({ title: 'FloodWait 42s', tone: 'warning', icon: 'alert' })
  })
  it('shows send success and failure with plain titles', () => {
    expect(eventToNotice({ ...ev('Telegram video sent to Main'), level: 'SUCCESS' }, NOW)?.title).toBe('Sent to Telegram')
    expect(eventToNotice({ ...ev('Transfer failed for upload'), level: 'ERROR' }, NOW)).toMatchObject({ title: 'Transfer needs attention', tone: 'error' })
  })
  it('ignores events older than a week', () => expect(eventToNotice(ev('Backup verified', { created_at: '2026-10-01T00:00:00Z' }), NOW)).toBeNull())
  it('ignores diagnostics', () => expect(eventToNotice({ ...ev('Slow API request'), level: 'WARN', event: 'api.slow' }, NOW)).toBeNull())
})

describe('buildNotifications', () => {
  it('de-duplicates repeats and lists newest first', () => {
    const rows = buildNotifications([ev('Backup verified', { created_at: '2026-11-06T09:00:00Z' }), ev('Backup verified', { created_at: '2026-11-06T10:00:00Z' }), { ...ev('Telegram video sent to Main'), level: 'SUCCESS' }], [], NOW)
    expect(rows.map((r) => r.title)).toEqual(['Sent to Telegram', 'Backup verified'])
  })
  it('includes active and recently failed jobs, and hides an event that a job already covers', () => {
    const rows = buildNotifications(
      [{ ...ev('Upload failed for job 77'), level: 'ERROR', job_id: '77' }],
      [{ id: '77', name: 'Upload batch', status: 'FAILED', updated_at: '2026-11-06T11:30:00Z' }, { id: '78', name: 'Index channel', status: 'RUNNING', progress: 0.4, updated_at: '2026-11-06T11:40:00Z' }, { id: '79', name: 'Done job', status: 'COMPLETED' }],
      NOW,
    )
    expect(rows.map((r) => r.title)).toEqual(['Index channel', 'Upload batch'])
    expect(rows[0]!.meta).toContain('40%')
  })
  it('caps the list at eight', () => {
    const many = Array.from({ length: 20 }, (_, i) => ev(`Backup verified ${i}`))
    expect(buildNotifications(many, [], NOW)).toHaveLength(8)
  })
})

describe('groupByDay', () => {
  it('groups into Today, Yesterday and Earlier', () => {
    const mk = (d: string) => ({ date: new Date(d) }) as never
    const g = groupByDay([mk('2026-11-06T08:00:00'), mk('2026-11-05T08:00:00'), mk('2026-11-01T08:00:00')], new Date('2026-11-06T12:00:00'))
    expect(g.map(([l, r]) => `${l}:${r.length}`)).toEqual(['Today:1', 'Yesterday:1', 'Earlier:1'])
  })
})

describe('categories', () => {
  it('sorts what a notice is about, for the filter chips on the Notifications page', async () => {
    const { categoryOf, matchesFilter } = await import('./notifications')
    expect(categoryOf('backup verified')).toBe('backups')
    expect(categoryOf('playback failed')).toBe('playback')
    expect(categoryOf('range request timed out')).toBe('playback')
    expect(categoryOf('matched 4 scenes')).toBe('metadata')
    expect(categoryOf('telegram send failed')).toBe('transfers')
    expect(categoryOf('something else')).toBe('other')
    expect(matchesFilter({ tone: 'error', category: 'transfers' }, 'attention')).toBe(true)
    expect(matchesFilter({ tone: 'success', category: 'transfers' }, 'attention')).toBe(false)
    expect(matchesFilter({ tone: 'success', category: 'backups' }, 'backups')).toBe(true)
    expect(matchesFilter({ tone: 'info', category: 'jobs' }, 'all')).toBe(true)
  })
})
