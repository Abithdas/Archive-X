import { describe, expect, it } from 'vitest'
import type { Channel } from '@/api/types'
import { channelMatches, channelName, directTarget, isAlreadySent, isDisplayName, sendBody, sentMessage } from './send'

const ch = (telegram_id: string, title: string) => ({ id: 1, telegram_id, title }) as Channel

describe('send to Telegram', () => {
  it('skips the dialog only when a fixed target is saved', () => {
    expect(directTarget({ fixed: true, target: '-100' })).toBe('-100')
    expect(directTarget({ fixed: true, target: '' })).toBe('')
    expect(directTarget({ fixed: false, target: '-100' })).toBe('')
    expect(directTarget(undefined)).toBe('')
  })
  it('does not treat placeholder titles as names', () => {
    expect(isDisplayName('Movies')).toBe(true)
    for (const t of ['', 'Unknown channel', 'channel -1001', '-1001', null]) expect(isDisplayName(t)).toBe(false)
    expect(channelName([ch('-1', 'Movies'), ch('-2', 'channel -2')], '-1')).toBe('Movies')
    expect(channelName([ch('-2', 'channel -2')], '-2')).toBe('Channel -2')
    expect(channelName([], '-9')).toBe('Channel -9')
  })
  it('filters channels by title, case-insensitively', () => {
    const list = [ch('1', 'Alpha Videos'), ch('2', 'Beta')]
    expect(channelMatches(list, ' ALPHA ').map((c) => c.telegram_id)).toEqual(['1'])
    expect(channelMatches(list, '')).toHaveLength(2)
    expect(channelMatches(list, 'zzz')).toEqual([])
  })
  it('builds the request body the server expects', () => {
    expect(sendBody({ _cid: '-100', id: 7 }, '-200', true, false)).toEqual({ channel_id: '-100', message_id: 7, target: '-200', remember: true, force: false })
  })
  it('recognises the already-sent response and words the success note', () => {
    expect(isAlreadySent(409)).toBe(true)
    expect(isAlreadySent(502)).toBe(false)
    expect(sentMessage('Movies', [])).toBe('Sent to Movies')
    expect(sentMessage('Movies', ['no cover', 'trimmed'])).toBe('Sent to Movies — no cover; trimmed')
    expect(sentMessage('Movies', undefined)).toBe('Sent to Movies')
  })
})

describe('caption markup', () => {
  it('turns the default template into bold text with decoded values', async () => {
    const { parseCaption } = await import('./send')
    expect(parseCaption('🎬 <b>A &amp; B</b>\n🏢 Studio')).toEqual(['🎬 ', { tag: 'b', children: ['A & B'] }, '\n🏢 Studio'])
  })
  it('never produces elements from escaped or unknown markup', async () => {
    const { parseCaption } = await import('./send')
    expect(parseCaption('x &lt;script&gt;alert(1)&lt;/script&gt;')).toEqual(['x <script>alert(1)</script>'])
    expect(parseCaption('<a href="javascript:alert(1)">link</a> <span onclick="x">t</span>')).toEqual(['link', ' ', 't'])
    expect(JSON.stringify(parseCaption('<script>x</script>'))).not.toContain('script"')
  })
  it('nests tags and tolerates stray or unclosed ones', async () => {
    const { parseCaption } = await import('./send')
    expect(parseCaption('<b>a <i>b</i></b>c')).toEqual([{ tag: 'b', children: ['a ', { tag: 'i', children: ['b'] }] }, 'c'])
    expect(parseCaption('</b>x<b>y')).toEqual(['x', { tag: 'b', children: ['y'] }])
    expect(parseCaption('')).toEqual([])
  })
  it('decodes numeric entities and leaves unknown ones alone', async () => {
    const { parseCaption } = await import('./send')
    expect(parseCaption('&#65;&#x42; &bogus; &#0;')).toEqual(['AB &bogus; &#0;'])
  })
})
