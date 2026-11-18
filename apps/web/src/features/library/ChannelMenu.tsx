import { Icon } from '@/design/Icon'
import { ChannelAvatar } from '@/features/channels/ChannelAvatar'
import { LocalMark } from '@/features/channels/LocalMark'
import { isLocalChannel } from '@/features/channels/local'
import { Popover } from '@/features/photos/Popover'
import type { Channel } from '@/api/types'
import { SubtleNumber } from '@/design/SubtleNumber'

const Tick = () => <span aria-label="selected" role="img" className="grid size-5 place-items-center rounded-full bg-good text-[11px] font-bold text-white">✓</span>

/** The channel picker as a dropdown: no selection means every channel, a green tick marks the chosen ones. */
export function ChannelMenu({ channels, selected, onToggle, onAll, onEdit }: { channels: readonly Channel[]; selected: readonly string[]; onToggle: (id: string) => void; onAll: () => void; onEdit: (id: string) => void }) {
  const label = !selected.length ? 'All channels' : selected.length === 1 ? (channels.find((c) => c.telegram_id === selected[0])?.title ?? '1 channel') : `${selected.length} channels`
  return (
    <Popover label={label} icon={<Icon name="grid" className="size-4" />} active={selected.length > 0}>
      <div role="group" aria-label="Channels" className="grid gap-0.5">
        <button type="button" disabled={!channels.length} onClick={onAll} aria-pressed={!selected.length} className="flex items-center gap-3 rounded-xl px-2 py-1.5 text-left text-sm font-semibold hover:bg-fg/5">
          <span className="grid size-8 place-items-center rounded-full bg-surface-3"><Icon name="grid" className="size-4" /></span><span className="flex-1">All channels</span>
          <span className="text-xs text-muted"><SubtleNumber value={channels.reduce((n, c) => n + c.video_count, 0)} /></span>{!selected.length ? <Tick /> : <span className="size-5" />}
        </button>
        {channels.map((c) => { const on = selected.includes(c.telegram_id); return (
          <button key={c.telegram_id} type="button" aria-pressed={on} onClick={() => onToggle(c.telegram_id)} className="flex items-center gap-3 rounded-xl px-2 py-1.5 text-left text-sm hover:bg-fg/5">
            <span className="grid size-8 shrink-0 place-items-center overflow-hidden rounded-full bg-surface-3 text-xs font-bold">{isLocalChannel(c) ? <Icon name="folders" className="size-4 text-accent" /> : <ChannelAvatar id={c.telegram_id} title={c.title} image={c.image} />}</span>
            <span className="min-w-0 flex-1 truncate">{c.title}</span>{isLocalChannel(c) ? <LocalMark withText /> : null}<span className="text-xs text-muted"><SubtleNumber value={c.video_count} /></span>{on ? <Tick /> : <span className="size-5" />}
          </button>) })}
      </div>
      {selected.length === 1 ? <button type="button" onClick={() => onEdit(selected[0]!)} aria-label="Edit selected channel" className="mt-2 inline-flex h-8 items-center gap-1.5 rounded-full border border-line-2 px-3 text-xs font-semibold hover:bg-fg/5"><Icon name="edit" className="size-3.5" />Edit channel</button> : null}
    </Popover>
  )
}
