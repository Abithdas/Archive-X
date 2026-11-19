import { describe, expect, it } from 'vitest'
import { AUTONEXT_MODES, NATIVE_MODES, PANELS, depsSummary, experienceFrom, iconsSummary, isPanel, pills, statusRows, telegramText, validationMessage } from './logic'

const fmt = (n: number) => `${n} B`
describe('panels', () => {
  it('lists the panels in order', () => expect(PANELS.map((p) => p.id)).toEqual(['overview', 'telegram', 'local', 'metadata', 'addons', 'experience', 'appearance', 'storage', 'security', 'system']))
  it('recognises panel ids only', () => { expect(isPanel('security')).toBe(true); expect(isPanel('nope')).toBe(false) })
})

describe('summaries', () => {
  it('names missing dependencies', () => {
    expect(depsSummary({ telethon: { ok: true }, av: { ok: true }, pillow: { ok: true }, ffmpeg: { ok: true }, ffprobe: { ok: true } })).toBe('All found')
    expect(depsSummary({ telethon: { ok: true }, av: { ok: false }, pillow: { ok: true }, ffmpeg: { ok: false }, ffprobe: { ok: true } })).toBe('2 missing: PyAV, FFmpeg')
    expect(depsSummary(undefined)).toBe('')
  })
  it('counts icons and names missing ones', () => {
    expect(iconsSummary({ icons: { VLC: '/a', Telegram: '' } })).toBe('1 of 2 icons OK · Telegram missing')
    expect(iconsSummary({ icons: { VLC: '/a' } })).toBe('Icons OK')
  })
  it('words the Telegram line', () => {
    expect(telegramText({ connected: false, status: 'CONFIG REQUIRED' })).toBe('Telegram not connected — CONFIG REQUIRED')
    expect(telegramText({ connected: true, status: 'ok' })).toBe('Telegram connected (saved session) — ok')
    expect(telegramText(undefined)).toBe('Telegram status unavailable')
  })
})

describe('statusRows', () => {
  const all = { auth: { connected: false, status: 'CONFIG REQUIRED: api_id/api_hash' }, preflight: { telethon: { ok: true }, av: { ok: true }, pillow: { ok: true }, ffmpeg: { ok: true }, ffprobe: { ok: true } }, icons: { icons: { VLC: '/a', Telegram: '' } }, web: { default_login: false }, storage: { bytes: 2048, files: 84 } }
  it('reproduces the classic Overview rows', () => {
    const r = statusRows(all, fmt)
    expect(r.map((x) => [x.title, x.label, x.tone])).toEqual([['Telegram', 'Check', 'warn'], ['Dependencies', 'OK', 'ok'], ['UI assets', 'Check', 'warn'], ['Database', 'OK', 'ok'], ['Web login', 'OK', 'ok']])
    expect(r[0]!.sub).toBe('not connected — CONFIG REQUIRED: api_id/api_hash')
    expect(r[3]!.sub).toBe('2048 B · 84 files')
    expect(r[0]!.go).toBe('telegram')
  })
  it('shows a loading state before the checks arrive', () => {
    const r = statusRows({}, fmt)
    expect(r[1]).toMatchObject({ sub: 'Checking…', label: '…', tone: '' })
    expect(r[3]!.sub).toBe('Local SQLite database')
  })
  it('flags the default web password and links to Security', () => {
    const w = statusRows({ web: { default_login: true } }, fmt)[4]!
    expect(w).toMatchObject({ sub: 'Still using the default password', label: 'Change', tone: 'warn', go: 'security' })
  })
})

describe('pills and validation', () => {
  it('summarises the three connections', () => {
    expect(pills({ auth: { connected: false }, metadata: { tpdb_token: '' }, web: { default_login: false } }).map((p) => [p.label, p.ok])).toEqual([['Telegram · Not connected', false], ['Metadata · No providers', false], ['Web login · Custom password', true]])
    expect(pills({ auth: { connected: true }, metadata: { stashdb_configured: true }, web: { default_login: true } }).map((p) => p.ok)).toEqual([true, true, false])
  })
  it('joins the four validation results', () => {
    expect(validationMessage({ telegram: { configured: false }, metadata: {}, web: { default_login: true }, preflight: { telethon: { ok: true }, ffmpeg: { ok: false } } })).toBe(
      'Telegram API credentials are incomplete or unverified. No metadata provider key is configured. Web login still uses the default password or is unavailable. Media dependency checks need attention.',
    )
    expect(validationMessage({ telegram: { configured: true }, metadata: { tpdb_token: 'x' }, web: { default_login: false }, preflight: { telethon: { ok: true }, ffmpeg: { ok: true } } })).toBe(
      'Telegram configuration is valid. At least one metadata provider is configured. Web login is customized. Media dependencies are ready.',
    )
  })
})

describe('experienceFrom', () => {
  it('reads the native player mode and falls back to "only when the browser cannot play it"', () => {
    expect(experienceFrom({ native_player: 'always' }).native_player).toBe('always')
    expect(experienceFrom({ native_player: 'off' }).native_player).toBe('off')
    expect(experienceFrom({ native_player: 'sometimes' }).native_player).toBe('fallback')
    expect(NATIVE_MODES.map(([k]) => k)).toEqual(['fallback', 'always', 'off'])
  })
  it('applies the classic defaults', () => {
    expect(experienceFrom(undefined)).toEqual({ native_player: 'fallback', autoplay: false, autonext: false, resume_prompt: true, live_seek_preview: true, partial_cache_prompt: false, autonext_mode: 'context' })
  })
  it('reads saved values and rejects an unknown autoplay source', () => {
    expect(experienceFrom({ autoplay: true, resume_prompt: false, partial_cache_prompt: true, autonext_mode: 'studio' })).toMatchObject({ autoplay: true, resume_prompt: false, partial_cache_prompt: true, autonext_mode: 'studio' })
    expect(experienceFrom({ autonext_mode: 'bogus' }).autonext_mode).toBe('context')
    expect(AUTONEXT_MODES).toHaveLength(5)
  })
})
