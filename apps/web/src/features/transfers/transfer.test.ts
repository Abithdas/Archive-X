import { describe, expect, it } from 'vitest'
import { DEFAULT_LOCAL, DEFAULT_TELEGRAM, localPayload, planPhotoScan, resolveAsk, startMessage, telegramPayload, validateLocal, validateTelegram } from './transfer'

describe('validation', () => {
  it('local needs a folder and a destination', () => {
    expect(validateLocal(DEFAULT_LOCAL)).toBe('Enter a source folder.')
    expect(validateLocal({ ...DEFAULT_LOCAL, folder: '  ' })).toBe('Enter a source folder.')
    expect(validateLocal({ ...DEFAULT_LOCAL, folder: 'C:\\v' })).toBe('Choose a destination channel.')
    expect(validateLocal({ ...DEFAULT_LOCAL, folder: 'C:\\v', destination: '-1' })).toBe('')
  })
  it('telegram checks sources, destination, overlap and trash', () => {
    expect(validateTelegram(DEFAULT_TELEGRAM)).toBe('Select at least one source channel.')
    expect(validateTelegram({ ...DEFAULT_TELEGRAM, channels: ['1'] })).toBe('Choose a destination channel.')
    expect(validateTelegram({ ...DEFAULT_TELEGRAM, channels: ['1', '2'], destination: '2' })).toBe('Destination cannot be one of the selected source channels.')
    expect(validateTelegram({ ...DEFAULT_TELEGRAM, channels: ['1'], destination: '2', cleanup: true })).toBe('Choose a trash channel when cleanup is enabled.')
    expect(validateTelegram({ ...DEFAULT_TELEGRAM, channels: ['1'], destination: '2', cleanup: true, trash: '3' })).toBe('')
  })
})

describe('payloads', () => {
  it('local video upload carries the video filters', () => {
    expect(localPayload({ ...DEFAULT_LOCAL, folder: ' D:\\clips ', destination: '-5', resolution: '1080P', orientation: 'VERTICAL', oversized: 'skip', subfolders: true, deleteSource: false })).toEqual({
      source: 'local', media_type: 'VIDEO', folder: 'D:\\clips', destination_channel: '-5', include_subfolders: true, delete_local_after_delivery: false, resolution_filter: '1080P', orientation: 'VERTICAL', oversized_file_policy: 'skip',
    })
  })
  it('image uploads send PHOTOS and no video filters', () => {
    const p = localPayload({ ...DEFAULT_LOCAL, folder: 'D:\\p', destination: '-5', images: true }) as Record<string, unknown>
    expect(p.media_type).toBe('PHOTOS')
    expect('resolution_filter' in p || 'orientation' in p || 'oversized_file_policy' in p).toBe(false)
  })
  it('an include-subfolders answer overrides the checkbox', () => expect(localPayload({ ...DEFAULT_LOCAL, folder: 'x', destination: '1' }, true).include_subfolders).toBe(true))
  it('telegram payload nests duplicate checks and cleanup', () => {
    expect(telegramPayload({ ...DEFAULT_TELEGRAM, channels: ['1', '2'], destination: '3', dupDb: false, cleanup: true, trash: '9' })).toEqual({
      source: 'telegram', source_channels: ['1', '2'], destination_channel: '3', source_mode: 'VIDEO', destination_mode: 'VIDEO', orientation: 'BOTH', resolution_filter: 'ANY',
      duplicate_check: { source: true, destination: true, database: false }, cleanup: { enabled: true, trash_channel: '9' },
    })
  })
})

describe('photo folder scan', () => {
  it('refuses a failed or malformed scan', () => {
    expect(planPhotoScan(null, 'D:\\p', false)).toEqual({ kind: 'error', message: 'Could not scan the selected photo folder: folder scan failed. No upload was queued.' })
    expect(planPhotoScan({ detail: 'denied' }, 'D:\\p', false)).toMatchObject({ kind: 'error', message: expect.stringContaining('denied') })
  })
  it('asks about subfolders when only some photos are direct', () => {
    const p = planPhotoScan({ direct_count: 3, nested_count: 5, recursive_count: 8 }, 'D:\\p', false)
    expect(p).toMatchObject({ kind: 'ask', directCount: 3, nestedCount: 5 })
    expect(p.kind === 'ask' && p.message).toContain('3 supported photo(s) directly')
  })
  it('uses a different question when nothing is direct', () => {
    const p = planPhotoScan({ direct_count: 0, nested_count: 4, recursive_count: 4 }, 'D:\\p', false)
    expect(p.kind === 'ask' && p.message).toContain('No supported photos were found directly')
  })
  it('goes straight ahead when subfolders are already included or none exist', () => {
    expect(planPhotoScan({ direct_count: 2, nested_count: 3, recursive_count: 5 }, 'x', true)).toEqual({ kind: 'go', includeSubfolders: true, count: 5 })
    expect(planPhotoScan({ direct_count: 2, nested_count: 0, recursive_count: 2 }, 'x', false)).toEqual({ kind: 'go', includeSubfolders: false, count: 2 })
  })
  it('after the answer: include → recursive count; decline with direct photos → direct only; decline with none → stop', () => {
    const scan = { direct_count: 3, nested_count: 5, recursive_count: 8 }
    expect(resolveAsk(scan, 'x', true)).toEqual({ kind: 'go', includeSubfolders: true, count: 8 })
    expect(resolveAsk(scan, 'x', false)).toEqual({ kind: 'go', includeSubfolders: false, count: 3 })
    expect(resolveAsk({ direct_count: 0, nested_count: 5, recursive_count: 5 }, 'x', false)).toMatchObject({ kind: 'error', message: expect.stringContaining('enable Include subfolders') })
  })
  it('reports unsupported content with the extensions seen', () => {
    const p = planPhotoScan({ direct_count: 0, nested_count: 0, recursive_count: 0, recursive_file_count: 12, recursive_extensions: { '.txt': 9, '.mp4': 3 } }, 'D:\\p', false)
    expect(p).toMatchObject({ kind: 'error', message: expect.stringContaining('inspected 12 file(s). Extensions seen: .txt: 9, .mp4: 3') })
  })
})

describe('start messages', () => {
  it('echoes the backend reason on failure', () => expect(startMessage('local', { detail: 'Telegram is offline' }, false, null)).toBe('Could not start: Telegram is offline'))
  it('describes each kind of queued job', () => {
    expect(startMessage('local', { id: 'j' }, false, null)).toBe('Local preparation/transfer queued.')
    expect(startMessage('local', { id: 'j' }, true, 7)).toContain('7 supported photo(s) detected')
    expect(startMessage('telegram', { id: 'j' }, false, null)).toBe('Telegram media inspection/preparation queued.')
  })
})
