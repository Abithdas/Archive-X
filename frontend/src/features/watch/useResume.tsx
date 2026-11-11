import { useEffect, useMemo, useRef, useState, type ReactNode } from 'react'
import { api } from '@/api/client'
import { Dialog } from '@/design/Dialog'
import { SAVE_EVERY_MS, clearStored, dueForSave, finishedBody, isLastPlayed, labelFor, markPlayed, outcome, progressBody, progressPct, promptPosition, readStored, saveStored, shouldSaveLocal, timeLine, type Choice } from './resume'


export interface ResumeOptions { cid: string; mid: number; name: string; duration: number; enabled: boolean; settled: boolean; autoplay: boolean; frameFor?: (seconds: number) => string | null }

/**
 * Continue-where-you-left-off for the watch page. Before playback it may ask "Resume from 12:05?" (Resume / Start over / Cancel);
 * while playing it saves the position (locally every 5 s, and to the server) and clears it when the video finishes.
 */
export function useResume(o: ResumeOptions) {
  const { cid, mid } = o
  const [choice, setChoice] = useState<Choice | null>(null)
  const lastLocal = useRef(0), lastServer = useRef(0), lastPos = useRef(0)

  // A new video starts with no choice and no throttling history.
  useEffect(() => { setChoice(null); lastLocal.current = 0; lastServer.current = 0; lastPos.current = 0 }, [cid, mid])

  const stored = useMemo(
    () => (o.settled ? promptPosition({ enabled: o.enabled, isLast: isLastPlayed(cid, mid), stored: readStored(cid, mid), duration: o.duration }) : null),
    [o.settled, o.enabled, o.duration, cid, mid],
  )
  const asking = stored !== null && choice === null
  const resumeBtn = useRef<HTMLButtonElement>(null)
  // The native dialog moves focus to its first control (✕) when it opens; Enter must mean "Resume", so focus it once open.
  useEffect(() => { if (asking) resumeBtn.current?.focus() }, [asking])
  const ready = o.settled && !asking
  const decided = choice && stored !== null ? outcome(choice, stored) : null
  const startAt = decided?.startAt ?? 0
  const autoPlay = decided ? decided.autoPlay : o.autoplay

  const onTime = (position: number, duration: number) => {
    const now = Date.now()
    if (dueForSave(now, lastLocal.current, SAVE_EVERY_MS)) {
      lastLocal.current = now
      if (shouldSaveLocal(isLastPlayed(cid, mid), position, duration)) saveStored(cid, mid, position)
    }
    if (dueForSave(now, lastServer.current, SAVE_EVERY_MS)) {
      lastServer.current = now
      const body = progressBody(cid, mid, position, duration, lastPos.current)
      lastPos.current = position
      api('/api/play/progress', { method: 'POST', json: body }).catch(() => { /* progress is best-effort */ })
    }
  }
  const onPlayed = () => markPlayed(cid, mid)
  const onEnd = () => {
    clearStored(cid, mid)
    api('/api/play/progress', { method: 'POST', json: finishedBody(cid, mid, o.duration) }).catch(() => { /* best-effort */ })
  }

  const frame = stored !== null ? (o.frameFor?.(stored) ?? null) : null
  const dialog: ReactNode = asking ? (
    <Dialog open onClose={() => setChoice('cancel')} title={o.name || 'Continue video'}>
      <div className="grid grid-cols-[minmax(0,1fr)] gap-4">
        <p className="text-xs font-semibold uppercase tracking-wide text-faint">Archive X · Playback</p>
        <p id="resume-position" className="text-base font-bold">Resume from {labelFor(stored)}?</p>
        <div className="relative aspect-video w-full overflow-hidden rounded-xl bg-surface-2">
          {frame ? <img src={frame} alt={`${o.name} at ${labelFor(stored)}`} className="size-full object-cover" onError={(e) => (e.currentTarget.style.visibility = 'hidden')} /> : <span role="status" className="absolute inset-0 grid place-items-center text-xs text-muted">Preview unavailable at the saved position</span>}
          <div className="absolute inset-x-0 bottom-0 h-1 bg-black/40"><span className="block h-full bg-accent" style={{ width: `${progressPct(stored, o.duration)}%` }} /></div>
          <span className="absolute bottom-2 right-2 rounded-md bg-black/65 px-2 py-0.5 text-xs font-semibold text-white">{timeLine(labelFor(stored), o.duration)}</span>
        </div>
        <p className="text-sm text-muted">You stopped at {labelFor(stored)}</p>
        <p className="text-[11px] text-faint">Enter resume · Esc cancel</p>
        <div className="flex flex-wrap justify-end gap-2">
          <button type="button" onClick={() => setChoice('cancel')} className="h-11 rounded-xl border border-line-2 px-4 text-sm font-semibold">Cancel</button>
          <button type="button" onClick={() => setChoice('start')} className="h-11 rounded-xl border border-line-2 px-4 text-sm font-semibold">Start over</button>
          <button ref={resumeBtn} type="button" onClick={() => setChoice('resume')} className="h-11 rounded-xl bg-accent px-5 text-sm font-bold text-accent-fg">Resume</button>
        </div>
      </div>
    </Dialog>
  ) : null

  return { ready, startAt, autoPlay, dialog, onTime, onPlayed, onEnd }
}
