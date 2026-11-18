import type { Channel } from '@/api/types'
import { cn } from '@/design/cn'
import { channelMatches, channelName, isDisplayName, parseCaption, type CaptionNode } from './send'
import { ClearField } from '@/design/ClearField'
import { isLocalChannel } from '@/features/channels/local'

function CaptionText({ nodes }: { nodes: CaptionNode[] }) {
  return <>{nodes.map((n, i) => {
    if (typeof n === 'string') return n
    const kids = <CaptionText nodes={n.children} />
    return n.tag === 'b' ? <b key={i}>{kids}</b> : n.tag === 'i' ? <i key={i}>{kids}</i> : n.tag === 'u' ? <u key={i}>{kids}</u> : n.tag === 's' ? <s key={i}>{kids}</s> : n.tag === 'code' ? <code key={i} className="rounded bg-surface-2 px-1">{kids}</code> : <pre key={i} className="whitespace-pre-wrap">{kids}</pre>
  })}</>
}

export const SINGLE_CAPTION_LABEL = 'Caption that will be sent (edit the template in Operations → Send to Telegram):'
export const BULK_CAPTION_LABEL = 'Caption preview for the first selected video. Each video gets its own caption from the template in Operations → Send to Telegram.'

/** Destination channel list, caption preview and "always send here" checkbox, shared by single and bulk send. */
export function ChannelPicker({ channels, q, onQ, target, onTarget, caption, captionLabel, remember, onRemember }: {
  channels: readonly Channel[]; q: string; onQ: (v: string) => void; target: string; onTarget: (id: string) => void
  caption: string | null; captionLabel: string; remember: boolean; onRemember: (v: boolean) => void
}) {
  const shown = channelMatches(channels, q).filter((c) => !isLocalChannel(c))   // a folder on this PC is a source, never a place to send to
  return (
    <>
      <ClearField value={q} onChange={onQ} placeholder="Search channel…" label="Search channel" clearLabel="Clear channel search" className="h-11 w-full min-w-0 rounded-xl border border-line-2 bg-bg px-3.5" />
      <div role="listbox" aria-label="Destination channel" className="max-h-[30vh] overflow-y-auto rounded-xl border border-line">
        {shown.length ? shown.map((c) => {
          const on = String(c.telegram_id) === target
          return (
            <button key={c.telegram_id} type="button" role="option" aria-selected={on} onClick={() => onTarget(String(c.telegram_id))} className={cn('flex w-full items-center gap-3 px-3 py-2.5 text-left text-sm hover:bg-fg/5', on && 'bg-accent/15 font-bold')}>
              <span className="grid size-7 shrink-0 place-items-center rounded-full bg-surface-2 text-xs text-muted" aria-hidden="true">#</span>
              <span className="min-w-0 truncate">{isDisplayName(c.title) ? c.title : channelName(channels, c.telegram_id)}</span>
            </button>
          )
        }) : <p className="p-3 text-sm text-muted">No channels.</p>}
      </div>
      <div>
        <p className="mb-1 text-[11px] text-muted">{captionLabel}</p>
        <p className="max-h-32 min-h-9 overflow-y-auto whitespace-pre-line rounded-xl bg-bg p-3 text-sm [overflow-wrap:anywhere]" aria-label="Caption preview">{caption === null ? 'Loading…' : caption ? <CaptionText nodes={parseCaption(caption)} /> : <span className="text-muted">(no caption)</span>}</p>
      </div>
      <label className="flex items-center gap-2 text-xs text-muted"><input type="checkbox" checked={remember} onChange={(e) => onRemember(e.target.checked)} className="size-4" />Always send to this channel (no more asking)</label>
    </>
  )
}
