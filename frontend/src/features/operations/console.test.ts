import { describe, expect, it } from 'vitest'
import { MAX_CHARS, answerText, appendLines, consoleTitle, countsText, hasConsole, jobLine, liveOperation, nextDelay, panelFor, percent, pollUrl } from './console'

describe('transfer console', () => {
  it('knows which jobs have one', () => { expect(hasConsole({ type: 'MEDIA_MANAGER' })).toBe(true); expect(hasConsole({ type: 'TELEGRAM_TRANSFER' })).toBe(true); expect(hasConsole({ type: 'AUTOMATION' })).toBe(false); expect(hasConsole({})).toBe(false) })
  it('titles and summarises', () => {
    expect(consoleTitle({})).toBe('Telegram Job Manager'); expect(consoleTitle({ mode_name: 'Transfer job' })).toBe('Telegram Job Manager — Transfer job')
    expect(countsText({ total: 2000, completed: 500 })).toBe('500 / 2,000 — 25%'); expect(countsText({})).toBe(''); expect(countsText({ total: 3 })).toBe('0 / 3 — 0%')
    expect(jobLine({ running: true, total: 10, completed: 5 })).toBe('Running · 5 / 10 — 50%'); expect(jobLine({ running: true })).toBe('Running')
    expect(jobLine({ error: 'boom' })).toBe('Stopped with an error: boom'); expect(jobLine({ seq: 4 })).toBe('Finished.'); expect(jobLine({})).toBe('Choose a workflow and press Start.')
    expect(percent({ total: 4, completed: 9 })).toBe(100); expect(percent({})).toBeNull(); expect(liveOperation({ live: 'x' })).toBe('x'); expect(liveOperation({})).toBe('Processing')
  })
  it('appends new lines and handles restarts and the size cap', () => {
    expect(appendLines('', 0, { seq: 2, lines: [{ n: 1, t: 'a' }, { n: 2, t: 'b' }] })).toEqual({ text: 'a\nb', seq: 2 })
    expect(appendLines('a\nb', 2, { seq: 3, lines: [{ n: 3, t: 'c' }] })).toEqual({ text: 'a\nb\nc', seq: 3 })
    expect(appendLines('a\n', 2, { seq: 3, lines: [{ n: 3, t: 'c' }] }).text).toBe('a\nc')
    expect(appendLines('old', 9, { seq: 1, lines: [{ n: 1, t: 'new' }] })).toEqual({ text: 'new', seq: 1 })
    expect(appendLines('keep', 5, { seq: 5, lines: [] })).toEqual({ text: 'keep', seq: 5 })
    expect(appendLines('x'.repeat(MAX_CHARS), 1, { seq: 2, lines: [{ n: 2, t: 'tail' }] }).text.length).toBe(MAX_CHARS)
    expect(appendLines('x'.repeat(MAX_CHARS), 1, { seq: 2, lines: [{ n: 2, t: 'tail' }] }).text.endsWith('tail')).toBe(true)
  })
  it('chooses the panel', () => {
    expect(panelFor({ running: true, prompt: ' Pick one ' })).toEqual({ kind: 'prompt', body: 'Pick one', placeholder: 'Enter your answer…', canAnswer: true })
    expect(panelFor({ running: true, live: 'Copying' })).toEqual({ kind: 'prompt', body: 'Copying', placeholder: 'Waiting…', canAnswer: false })
    expect(panelFor({ running: true })).toMatchObject({ body: 'Processing…' })
    expect(panelFor({ error: 'bad' })).toEqual({ kind: 'finished', title: 'Media Manager stopped', help: 'bad' })
    expect(panelFor({})).toEqual({ kind: 'finished', title: 'Media Manager finished', help: 'The CLI workflow is no longer active.' })
  })
  it('small helpers', () => { expect(answerText('  y ')).toBe('y'); expect(nextDelay(true)).toBe(700); expect(nextDelay(false)).toBe(2500); expect(pollUrl(5)).toBe('/api/telegram-transfer?since=5') })
})
