import { useEffect, useRef, useState } from 'react'
import { toast } from 'sonner'
import { Dialog } from '@/design/Dialog'
import { formatBytes, formatDuration } from '@/features/library/format'
import { FRACTIONS, INCOMPLETE_NOTICE, framePercent, incomplete, liveSrc, previewQuestion, snapshotIndex, snapshotSrc, type FrameState } from './suggestpreview'
import type { Kind, Suggested } from './studioExtrasLogic'
import { suggestionTitle, yesLabel } from './studioExtrasLogic'

function Tile({ s, frac, label, onState }: { s: Suggested; frac: number; label: number; onState: (i: number, st: FrameState) => void }) {
  const cid = String(s.channel_id), mid = Number(s.message_id), idx = snapshotIndex(frac)
  const [src, setSrc] = useState(snapshotSrc(cid, mid, idx))
  const [state, setState] = useState<FrameState>('loading')
  const triedLive = useRef(false)
  const set = (st: FrameState) => { setState(st); onState(label, st) }
  const live = () => {
    if (triedLive.current) return set('failed')
    triedLive.current = true
    setSrc(liveSrc(cid, mid, Number(s.duration || 0), frac))
  }
  return (
    <div className="relative grid aspect-video place-items-center overflow-hidden rounded-xl bg-black text-center text-xs text-muted" data-source={state}>
      <img alt={`Verification thumbnail ${label + 1}`} src={src} onLoad={(e) => {
        if (e.currentTarget.naturalWidth > 1 && e.currentTarget.naturalHeight > 1) set(triedLive.current ? 'live' : 'snapshot')
        else live()
      }} onError={live} className={state === 'failed' ? 'hidden' : 'absolute inset-0 size-full object-cover'} />
      {state === 'loading' ? <span>Loading…</span> : state === 'failed' ? <span>Preview unavailable</span> : null}
      <span className="absolute bottom-1.5 right-1.5 rounded-md bg-black/70 px-1.5 py-0.5 text-[10px] font-semibold text-white">{framePercent(idx)}%</span>
    </div>
  )
}

/** Four frames of the suggested video, so you can tell whether it really belongs before linking. */
export function SuggestionPreview({ s, name, kind, busy, onConfirm, onReject, onClose }: { s: Suggested; name: string; kind: Kind; busy: boolean; onConfirm: () => void; onReject: () => void; onClose: () => void }) {
  const states = useRef<FrameState[]>(FRACTIONS.map(() => 'loading'))
  const warned = useRef(false)
  const report = (i: number, st: FrameState) => {
    states.current[i] = st
    if (!warned.current && incomplete(states.current)) { warned.current = true; toast(INCOMPLETE_NOTICE) }
  }
  useEffect(() => { states.current = FRACTIONS.map(() => 'loading'); warned.current = false }, [s.suggestion_id])
  const dur = Number(s.duration || 0)
  return (
    <Dialog open onClose={onClose} title={suggestionTitle(s)} wide
      footer={
        <div className="flex flex-wrap items-center justify-end gap-2 border-t border-line pt-3">
          <button type="button" disabled={busy} onClick={onReject} className="h-10 rounded-xl border border-line-2 px-4 text-sm font-semibold hover:bg-fg/5 disabled:opacity-60">No, don’t link</button>
          <button type="button" disabled={busy} onClick={onConfirm} className="h-10 rounded-xl bg-accent px-4 text-sm font-bold text-accent-fg hover:brightness-110 disabled:opacity-60">{yesLabel(kind)}</button>
        </div>
      }>
      <div key={s.suggestion_id} aria-label="Video verification thumbnails" className="grid grid-cols-2 gap-3 sm:grid-cols-4">
        {FRACTIONS.map((f, i) => <Tile key={f} s={s} frac={f} label={i} onState={report} />)}
      </div>
      <div className="mt-3 flex flex-wrap gap-2 text-xs font-semibold">
        <span className="rounded-md bg-surface-2 px-2 py-1">{s.filename || 'Telegram video'}</span>
        {dur ? <span className="rounded-md bg-surface-2 px-2 py-1">{formatDuration(dur)}</span> : null}
        {s.size ? <span className="rounded-md bg-surface-2 px-2 py-1">{formatBytes(s.size)}</span> : null}
      </div>
      <p className="mt-2 text-xs text-muted">{previewQuestion(kind, name)}</p>
      {busy ? <p role="status" className="mt-1 text-xs text-muted">Saving this relationship…</p> : null}
    </Dialog>
  )
}
