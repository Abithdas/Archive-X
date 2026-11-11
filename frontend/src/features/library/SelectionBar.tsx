import { useQueryClient } from '@tanstack/react-query'
import { useId, useRef, useState, type ReactNode } from 'react'
import { toast } from 'sonner'
import { ApiError, api } from '@/api/client'
import type { LibraryItem } from '@/api/types'
import { Dialog } from '@/design/Dialog'
import { Icon } from '@/design/Icon'
import { cn } from '@/design/cn'
import { BulkSend } from './BulkSend'
import { NONE_PICKED, UNLOCKED, bulkBody, countLabel, recheckMessage, titleProblem, unknownConfirm, unknownOk, unlockBody, updatedMessage, type BulkField } from './bulk'
import { pickedList, useSelection } from './selection'

const BAR_BTN = 'h-9 shrink-0 rounded-lg border border-line-2 bg-surface px-3 text-[13px] font-semibold hover:bg-fg/5 disabled:opacity-50'
const ITEM = 'flex h-10 w-full items-center gap-2 rounded-lg px-3 text-left text-sm font-medium hover:bg-fg/5 disabled:opacity-50'

/** A small dropdown that opens upward, since the bar sits at the bottom of the screen. */
function BarMenu({ label, children, disabled }: { label: string; children: (close: () => void) => ReactNode; disabled?: boolean }) {
  const id = useId()
  const pop = useRef<HTMLDivElement>(null)
  const btn = useRef<HTMLButtonElement>(null)
  const place = () => {
    const el = pop.current, b = btn.current?.getBoundingClientRect()
    if (!el || !b) return
    const w = 224
    el.style.left = `${Math.max(8, Math.min(window.innerWidth - w - 8, b.left))}px`
    el.style.bottom = `${window.innerHeight - b.top + 6}px`
  }
  return (
    <>
      <button ref={btn} type="button" popoverTarget={id} aria-haspopup="menu" disabled={disabled} onClick={place} className={cn(BAR_BTN, 'inline-flex items-center gap-1.5')}>{label}<Icon name="chevron-down" className="size-3.5" /></button>
      <div ref={pop} id={id} popover="auto" role="menu" aria-label={label} className="fixed m-0 w-56 rounded-2xl border border-line-2 bg-surface-2 p-1.5 text-fg shadow-[0_18px_40px_-8px_rgb(0_0_0/0.6)] [inset:auto]">
        {children(() => pop.current?.hidePopover?.())}
      </div>
    </>
  )
}

type Ask = null | { kind: 'title' } | { kind: 'unknown'; field: BulkField }

/** The classic "mapping mode" bar: what to do with the picked videos. */
export function SelectionBar({ visible }: { visible: readonly LibraryItem[] }) {
  const qc = useQueryClient()
  const { picked, clear, exit, selectMany } = useSelection()
  const list = pickedList(picked)
  const [ask, setAsk] = useState<Ask>(null)
  const [title, setTitle] = useState('')
  const [titleMsg, setTitleMsg] = useState('')
  const [busy, setBusy] = useState(false)
  const [sending, setSending] = useState(false)

  const refresh = () => { void qc.invalidateQueries({ queryKey: ['library'] }); void qc.invalidateQueries({ queryKey: ['entities'] }); void qc.invalidateQueries({ queryKey: ['channels'] }) }
  const need = (fn: () => void) => () => (list.length ? fn() : void toast(NONE_PICKED))
  const detail = (e: unknown, fallback: string) => (e instanceof ApiError ? e.detail || fallback : fallback)

  const applyBulk = async (field: BulkField, action: 'move' | 'unknown', target = '') => {
    setBusy(true)
    try {
      const r = await api<{ ok?: boolean; changed?: number; detail?: string }>('/api/mapping/bulk', { method: 'POST', json: bulkBody(list, field, action, target) })
      if (!r?.ok) throw new ApiError(0, r?.detail || 'Mapping failed')
      toast(updatedMessage(r.changed, list.length))
      clear()
      refresh()
    } catch (e) { toast.error(detail(e, 'Mapping failed')) } finally { setBusy(false) }
  }
  const recheck = async () => {
    try { toast(recheckMessage(await api('/api/mapping/recheck', { method: 'POST', json: { videos: list } }), list.length)) } catch (e) { toast.error(detail(e, 'Recheck failed')) }
  }
  const unlock = async () => {
    try { await api('/api/mapping/unlock', { method: 'POST', json: unlockBody(list) }); toast(UNLOCKED) } catch (e) { toast.error(detail(e, 'Unlock failed')) }
  }
  const submitTitle = () => {
    const p = titleProblem(title)
    if (p) return void setTitleMsg(p)
    setAsk(null)
    void applyBulk('title', 'move', title)
    setTitle('')
  }

  return (
    <>
      <div role="toolbar" aria-label="Edit multiple videos" className="sticky bottom-[84px] z-30 mt-4 flex flex-wrap items-center gap-2 rounded-2xl border border-line-2 bg-surface-2/95 p-2.5 shadow-[0_18px_40px_-8px_rgb(0_0_0/0.6)] backdrop-blur md:bottom-3 md:flex-nowrap">
        <div className="order-1 flex w-full items-center gap-2 md:order-2 md:ml-auto md:w-auto">
          <span role="status" aria-live="polite" className="rounded-lg bg-accent/15 px-2.5 py-1.5 text-[13px] font-bold text-accent">{countLabel(list.length)}</span>
          <span className="ml-auto md:hidden" />
          <button type="button" onClick={() => selectMany(visible)} className={BAR_BTN}>Select all</button>
          <button type="button" onClick={clear} className={BAR_BTN}>Deselect</button>
          <button type="button" onClick={exit} className="h-9 shrink-0 rounded-lg bg-accent px-4 text-[13px] font-bold text-accent-fg">Done</button>
        </div>
        <div className="order-2 flex w-full min-w-0 items-center gap-2 overflow-x-auto md:order-1 md:w-auto md:overflow-visible">
          <span className="hidden text-[11px] text-muted lg:inline">Pick videos, then choose what to change</span>
          <BarMenu label="Performer / Studio" disabled={busy}>{(close) => (<>
            <button type="button" role="menuitem" className={ITEM} onClick={() => { close(); need(() => setAsk({ kind: 'unknown', field: 'performer' }))() }}>Clear Performer</button>
            <button type="button" role="menuitem" className={ITEM} onClick={() => { close(); need(() => setAsk({ kind: 'unknown', field: 'site' }))() }}>Clear Studio</button>
          </>)}</BarMenu>
          <BarMenu label="Metadata" disabled={busy}>{(close) => (<>
            <button type="button" role="menuitem" className={ITEM} onClick={() => { close(); need(() => void recheck())() }}><Icon name="refresh" className="size-4 text-muted" />Recheck Metadata</button>
            <button type="button" role="menuitem" className={ITEM} onClick={() => { close(); need(() => { setTitleMsg(''); setAsk({ kind: 'title' }) })() }}>Edit Title</button>
          </>)}</BarMenu>
          <BarMenu label="More" disabled={busy}>{(close) => (<>
            <button type="button" role="menuitem" className={cn(ITEM, 'text-bad')} onClick={() => { close(); need(() => setAsk({ kind: 'unknown', field: 'all' }))() }}>Move All to Unknown</button>
            <button type="button" role="menuitem" className={ITEM} onClick={() => { close(); need(() => void unlock())() }}>Unlock</button>
          </>)}</BarMenu>
          <button type="button" disabled={!list.length} onClick={() => setSending(true)} title="Send all selected videos to a Telegram channel" className="inline-flex h-9 shrink-0 items-center gap-1.5 rounded-lg bg-accent px-3.5 text-[13px] font-bold text-accent-fg disabled:opacity-50"><Icon name="brand-telegram" className="size-4" />Send to Telegram</button>
        </div>
      </div>

      {ask?.kind === 'title' ? (
        <Dialog open onClose={() => setAsk(null)} title="Correct video title">
          <form className="grid grid-cols-[minmax(0,1fr)] gap-4" onSubmit={(e) => { e.preventDefault(); submitTitle() }}>
            <p className="text-sm text-muted">Enter the corrected video title.</p>
            <input value={title} onChange={(e) => { setTitle(e.target.value); setTitleMsg('') }} placeholder="Video title" aria-label="Video title" autoFocus className="h-12 w-full min-w-0 rounded-xl border border-line-2 bg-bg px-3.5 text-[15px] outline-none placeholder:text-faint focus:border-accent/60" />
            {titleMsg ? <p role="alert" className="text-sm text-warn">{titleMsg}</p> : null}
            <div className="flex flex-wrap justify-end gap-2">
              <button type="button" onClick={() => setAsk(null)} className="h-11 rounded-xl border border-line-2 px-4 text-sm font-semibold">Cancel</button>
              <button type="submit" className="h-11 rounded-xl bg-accent px-5 text-sm font-bold text-accent-fg">Apply value</button>
            </div>
          </form>
        </Dialog>
      ) : null}
      {ask?.kind === 'unknown' ? (
        <Dialog open onClose={() => setAsk(null)} title="Set to Unknown?">
          <div className="grid grid-cols-[minmax(0,1fr)] gap-4">
            <p className="text-sm text-muted">{unknownConfirm(ask.field, list.length)}</p>
            <div className="flex flex-wrap justify-end gap-2">
              <button type="button" onClick={() => setAsk(null)} className="h-11 rounded-xl border border-line-2 px-4 text-sm font-semibold">Cancel</button>
              <button type="button" onClick={() => { const f = ask.field; setAsk(null); void applyBulk(f, 'unknown') }} className="h-11 rounded-xl bg-bad px-4 text-sm font-bold text-white">{unknownOk(ask.field)}</button>
            </div>
          </div>
        </Dialog>
      ) : null}
      {sending ? <BulkSend picked={list} onDone={() => setSending(false)} /> : null}
    </>
  )
}
