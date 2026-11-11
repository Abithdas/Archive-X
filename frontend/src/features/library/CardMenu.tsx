import { useQueryClient } from '@tanstack/react-query'
import { useId, useRef, useState } from 'react'
import { toast } from 'sonner'
import { ApiError, api, thumbUrl } from '@/api/client'
import type { LibraryItem } from '@/api/types'
import { Dialog } from '@/design/Dialog'
import { Icon } from '@/design/Icon'
import { cn } from '@/design/cn'
import { SendToTelegram } from './SendToTelegram'

type Player = 'vlc' | 'mpc'
const MSG = (e: unknown, fallback: string) => (e instanceof ApiError ? e.detail || fallback : fallback)

/** Asks the backend to open the video in a desktop player on this PC. */
export async function openExternal(item: Pick<LibraryItem, '_cid' | 'id'>, player: Player) {
  const r = await api<{ ok?: boolean; player?: string; detail?: string }>('/api/play/external', { method: 'POST', json: { channel_id: item._cid, message_id: item.id, player } })
  if (!r?.ok) throw new ApiError(0, r?.detail || 'Player not found')
  return r
}

/** Permanently deletes the Telegram message and its library entry (the backend verifies a linked thumbnail before removing it). */
export const deleteVideo = (item: Pick<LibraryItem, '_cid' | 'id'>) =>
  api<{ ok?: boolean; detail?: string }>('/api/library/delete', { method: 'POST', json: { channel_id: Number(item._cid), message_id: item.id, confirm_delete: true } }).then((r) => {
    if (!r?.ok) throw new ApiError(0, r?.detail || 'Video was not deleted')
  })

const OPTION = 'flex h-11 w-full items-center gap-3 rounded-xl px-3 text-left text-[15px] font-medium hover:bg-fg/5 focus-visible:bg-fg/5 focus-visible:outline-none'

/** The card's ⋯ menu. Light-dismiss, Esc and top-layer come from the native popover attribute. */
export function CardMenu({ item, className }: { item: LibraryItem; className?: string }) {
  const id = useId()
  const pop = useRef<HTMLDivElement>(null)
  const btn = useRef<HTMLButtonElement>(null)
  const [confirm, setConfirm] = useState(false)
  const [busy, setBusy] = useState(false)
  const qc = useQueryClient()
  const [sending, setSending] = useState(false)

  const place = () => {
    const el = pop.current
    const b = btn.current?.getBoundingClientRect()
    if (!el || !b) return
    const w = 232
    const h = el.offsetHeight || 260
    el.style.left = `${Math.max(8, Math.min(window.innerWidth - w - 8, b.right - w))}px`
    el.style.top = `${b.bottom + h + 12 <= window.innerHeight ? b.bottom + 4 : Math.max(8, b.top - h - 4)}px`
  }
  const close = () => pop.current?.hidePopover?.()
  const play = async (p: Player) => {
    close()
    try {
      const r = await openExternal(item, p)
      toast(`Opened in ${r.player ?? (p === 'vlc' ? 'VLC' : 'MPC-HC')}`)
    } catch (e) {
      toast.error(`Player error: ${MSG(e, 'not found')}`)
    }
  }
  const remove = async () => {
    setBusy(true)
    try {
      await deleteVideo(item)
      setConfirm(false)
      toast.success('Video deleted')
      await Promise.all([qc.invalidateQueries({ queryKey: ['library'] }), qc.invalidateQueries({ queryKey: ['channels'] })])
    } catch (e) {
      toast.error(MSG(e, 'Video was not deleted'))
    } finally {
      setBusy(false)
    }
  }

  return (
    <>
      <button
        ref={btn}
        type="button"
        popoverTarget={id}
        aria-label="More actions"
        aria-haspopup="menu"
        onClick={place}
        className={cn('grid h-9 w-7 place-items-center rounded-lg text-muted hover:bg-fg/5 hover:text-fg', className)}
      >
        <span className="flex gap-[3px]" aria-hidden="true">
          <i className="size-1 rounded-full bg-current" />
          <i className="size-1 rounded-full bg-current" />
          <i className="size-1 rounded-full bg-current" />
        </span>
      </button>
      <div ref={pop} id={id} popover="auto" role="menu" aria-label="Video actions" onToggle={(e) => (e.nativeEvent as ToggleEvent).newState === 'open' && pop.current?.querySelector<HTMLElement>('button')?.focus()} className="m-0 w-[232px] rounded-[16px] border border-line-2 bg-surface p-2 text-fg shadow-[0_18px_50px_rgb(0_0_0/0.5)]">
        <div className="px-3 pb-1 pt-1.5 text-xs font-semibold text-faint" aria-hidden="true">Video actions</div>
        <button type="button" role="menuitem" className={OPTION} onClick={() => { close(); setSending(true) }}>
          <Icon name="brand-telegram" className="text-muted" />Send to Telegram
        </button>
        <button type="button" role="menuitem" className={OPTION} onClick={() => void play('vlc')}>
          <Icon name="brand-vlc" className="text-muted" />Open in VLC
        </button>
        <button type="button" role="menuitem" className={OPTION} onClick={() => void play('mpc')}>
          <Icon name="play" className="text-muted" />Open in MPC-HC
        </button>
        <div role="separator" className="my-1.5 h-px bg-line" />
        <button type="button" role="menuitem" className={cn(OPTION, 'text-bad')} title="Permanently delete from Telegram and remove from the Archive X Library" onClick={() => { close(); setConfirm(true) }}>
          <Icon name="trash" />Delete
        </button>
      </div>
      {sending ? <SendToTelegram item={item} onDone={() => setSending(false)} /> : null}
      {confirm ? (
      <Dialog open onClose={() => !busy && setConfirm(false)} title="Delete this video from Telegram?">
        <div className="grid grid-cols-[minmax(0,1fr)] gap-4">
          <div className="flex gap-3 rounded-xl border border-line bg-bg p-2.5">
            <img src={thumbUrl(item._cid, item.thumbnail_id)} alt="" className="h-14 w-24 shrink-0 rounded-lg bg-surface-2 object-cover" onError={(e) => (e.currentTarget.style.visibility = 'hidden')} />
            <div className="min-w-0 text-sm">
              <p className="line-clamp-2 font-bold [overflow-wrap:anywhere]">{item.name}</p>
              <p className="truncate text-muted">{item.channel_title ?? 'Channel'} · Telegram message {item.id}</p>
            </div>
          </div>
          <p className="text-sm text-muted">
            This permanently deletes the message from its Telegram channel and removes it from your Archive X Library. A linked Telegram thumbnail is deleted too only if it is verified as an image used exclusively by this video.
          </p>
          <div className="flex flex-wrap justify-end gap-2">
            <button type="button" disabled={busy} onClick={() => setConfirm(false)} className="h-11 rounded-xl border border-line-2 px-4 text-sm font-semibold">Cancel</button>
            <button type="button" disabled={busy} onClick={() => void remove()} className="h-11 rounded-xl bg-bad px-4 text-sm font-bold text-white disabled:opacity-60">
              {busy ? 'Deleting…' : 'Delete video'}
            </button>
          </div>
        </div>
      </Dialog>
      ) : null}
    </>
  )
}
