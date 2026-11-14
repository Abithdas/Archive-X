/** Move / delete source rules, ported from the classic card menu. */
import type { Channel } from '@/api/types'
import { isDisplayName } from './send'

export const uniqueChannels = (list: unknown): Channel[] => {
  const seen = new Set<string>()
  return (Array.isArray(list) ? (list as Channel[]) : []).filter((c) => {
    if (!c || c.telegram_id == null || String(c.telegram_id).trim() === '') return false
    const key = String(c.telegram_id)
    if (seen.has(key)) return false
    seen.add(key)
    return true
  })
}

export interface Destinations { options: Channel[]; message: string; state: 'ready' | 'warning' }

/**
 * Destinations must come from the full Telegram inventory, not just channels that already have Library media.
 * The published channels are only a clearly-labelled fallback. The source channel is never offered.
 */
export function resolveDestinations(sourceId: string, inventory: unknown, published: unknown, failure = ''): Destinations {
  const without = (list: Channel[]) => list.filter((c) => String(c.telegram_id) !== String(sourceId))
  const inv = uniqueChannels(inventory)
  const fromInventory = without(inv)
  if (fromInventory.length) {
    return { options: fromInventory, state: 'ready', message: failure ? `Could not refresh Telegram channels: ${failure}. Showing the last loaded list.` : `${inv.length} channel${inv.length === 1 ? '' : 's'} loaded.` }
  }
  const pub = uniqueChannels(published)
  const fromPublished = without(pub)
  if (fromPublished.length) {
    return { options: fromPublished, state: 'warning', message: failure ? `Could not load Telegram channels: ${failure}.` : 'Using published Library channels because the Telegram channel inventory is empty.' }
  }
  return { options: [], state: 'warning', message: failure ? `Could not load Telegram channels: ${failure}.` : 'Telegram returned no usable destination channels. Refresh the channel list and retry.' }
}

export const optionLabel = (c: Channel) => (isDisplayName(c.title) ? c.title.trim() : `Channel ${c.telegram_id}`)

export const moveMeta = (sourceTitle: string, size: number, fmt: (n: number) => string) => [sourceTitle, size > 0 ? fmt(size) : ''].filter(Boolean).join(' · ')

/** The server refuses a delete without the explicit flag, and wants a numeric source channel id. */
export const moveBody = (item: { _cid: string; id: number }, destination: string) => ({ channel_id: Number(item._cid), message_id: item.id, destination_channel: String(destination), confirm_delete: true })

export const moveConfirmText = (title: string, destination: string) => `Move “${title}” to “${destination}” and delete the original Telegram message after verification? This cannot be undone.`

export interface MoveResult { ok?: boolean; moved?: { source_deleted?: boolean }; source_warning?: string; companion_warning?: string }
export function moveResultMessage(r: MoveResult): string {
  const warnings = [r.source_warning, r.companion_warning].filter(Boolean).join(' ')
  if (r.moved?.source_deleted === false) return `Destination verified. Source deletion is pending and will be retried.${warnings ? ` ${warnings}` : ''}`
  return warnings ? `Move verified with a warning: ${warnings}` : 'Move verified. The source message was deleted.'
}
