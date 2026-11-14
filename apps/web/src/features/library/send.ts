/** Send to Telegram rules, ported from the classic card menu. */
import type { Channel } from '@/api/types'

export interface SendSettings { target?: string; fixed?: boolean; template?: string }

/** "Always send to this channel" skips the dialog entirely. */
export const directTarget = (cfg: SendSettings | undefined): string => (cfg?.fixed && cfg.target ? String(cfg.target) : '')

/** Real channel titles only: placeholders such as "Unknown channel", "channel -100…" or a bare id are not names. */
export const isDisplayName = (t: unknown) => {
  const s = String(t ?? '').trim()
  return Boolean(s) && !/^unknown channel$/i.test(s) && !/^channel\s+-?\d+$/i.test(s) && !/^-?\d+$/.test(s)
}
export const channelName = (channels: readonly Channel[], id: string): string => {
  const c = channels.find((x) => String(x.telegram_id) === String(id))
  return c && isDisplayName(c.title) ? c.title.trim() : `Channel ${id}`
}
export const channelMatches = (channels: readonly Channel[], q: string): Channel[] => {
  const needle = q.trim().toLowerCase()
  return channels.filter((c) => !needle || String(c.title ?? '').toLowerCase().includes(needle))
}

export interface SendTarget { _cid: string; id: number }
export const sendBody = (item: SendTarget, target: string, remember: boolean, force: boolean) => ({ channel_id: item._cid, message_id: item.id, target, remember, force })

/** The server answers 409 when this video was already sent to that channel. */
export const isAlreadySent = (status: number) => status === 409

export const sentMessage = (title: string, notes: unknown) => {
  const list = Array.isArray(notes) ? notes.map(String).filter(Boolean) : []
  return `Sent to ${title}${list.length ? ` — ${list.join('; ')}` : ''}`
}
export const noChannelMessage = 'Choose a channel first.'

/** A tiny, safe renderer for Telegram caption markup (the server HTML-escapes values and keeps the template's own tags). */
export type CaptionNode = string | { tag: 'b' | 'i' | 'u' | 's' | 'code' | 'pre'; children: CaptionNode[] }
const TAGS: Record<string, 'b' | 'i' | 'u' | 's' | 'code' | 'pre'> = { b: 'b', strong: 'b', i: 'i', em: 'i', u: 'u', ins: 'u', s: 's', strike: 's', del: 's', code: 'code', pre: 'pre' }
const ENTITIES: Record<string, string> = { amp: '&', lt: '<', gt: '>', quot: '"', apos: "'", nbsp: ' ' }
const decode = (t: string) => t.replace(/&(#x[0-9a-f]+|#\d+|[a-z]+);/gi, (m, e: string) => {
  if (e[0] === '#') {
    const n = e[1]!.toLowerCase() === 'x' ? parseInt(e.slice(2), 16) : parseInt(e.slice(1), 10)
    return Number.isFinite(n) && n > 0 && n <= 0x10ffff ? String.fromCodePoint(n) : m
  }
  return ENTITIES[e.toLowerCase()] ?? m
})
export function parseCaption(html: string): CaptionNode[] {
  const root: CaptionNode[] = []
  const stack: Array<{ tag: string; children: CaptionNode[] }> = []
  const top = () => (stack.length ? stack[stack.length - 1]!.children : root)
  const re = /<(\/?)([a-z][a-z0-9]*)\b[^>]*>/gi
  let last = 0
  for (let m = re.exec(html); m; m = re.exec(html)) {
    if (m.index > last) top().push(decode(html.slice(last, m.index)))
    last = m.index + m[0].length
    const name = m[2]!.toLowerCase(), tag = TAGS[name]
    if (!tag) continue                                   // unknown tag (a, span, …): drop the markup, keep its text
    if (m[1]) {
      const at = stack.map((x) => x.tag).lastIndexOf(tag)
      if (at >= 0) stack.length = at                     // close it (and anything left open inside it)
    } else {
      const node = { tag, children: [] as CaptionNode[] }
      top().push(node as CaptionNode)
      stack.push(node)
    }
  }
  if (last < html.length) top().push(decode(html.slice(last)))
  return root
}

// ── bulk send (the selected videos, one job) ──
export interface BulkVideo { channel_id: string; message_id: number }
export type DuplicateAction = 'resend' | 'skip'
export const bulkJobId = (now: number, n: number) => `bulk-${now.toString(36)}-${n.toString(36)}`
export const bulkSendBody = (videos: BulkVideo[], target: string, remember: boolean, jobId: string, duplicateAction?: DuplicateAction) =>
  ({ videos, target, remember, job_id: jobId, ...(duplicateAction ? { duplicate_action: duplicateAction } : {}) })
export const bulkTitle = (n: number) => `Send ${n} selected video${n === 1 ? '' : 's'} to Telegram`
export const bulkSendLabel = (n: number) => `Send ${n} video${n === 1 ? '' : 's'}`
export const bulkStarted = (n: number, dest: string) => `Sending ${n} selected video${n === 1 ? '' : 's'} to ${dest}. This may take a while…`
export const bulkProgress = (dest: string, done: number, total: number, title: string) => `Sending to ${dest} · ${done}/${total} complete${title ? ` · ${title}` : ''}`
export const duplicatesPrompt = (n: number, dest: string) => `${n} of the selected videos ${n === 1 ? 'has' : 'have'} already been sent to ${dest}. Send those again too, or skip them and send the rest?`

export interface BulkResult { ok?: boolean; sent?: number; skipped?: number; failed?: number; already_sent?: boolean; duplicate_videos?: unknown[]; results?: Array<{ status?: string; message_id?: number; error?: string }> }
export const hasDuplicates = (r: BulkResult) => Boolean(r.already_sent && Array.isArray(r.duplicate_videos) && r.duplicate_videos.length)
export function bulkSummary(r: BulkResult, nameOf: (messageId: number) => string): { text: string; failed: boolean } {
  const sent = Number(r.sent || 0), skipped = Number(r.skipped || 0), failed = Number(r.failed || 0)
  let text = `Telegram send complete: ${sent} sent, ${skipped} skipped, ${failed} failed.`
  const first = (r.results ?? []).find((x) => x.status === 'failed')
  if (first) text += ` First failure: ${nameOf(Number(first.message_id))} — ${String(first.error || 'send failed')}.`
  return { text, failed: failed > 0 }
}
