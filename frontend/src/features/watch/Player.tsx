import { useCallback, useEffect, useRef, useState } from 'react'
import { SMART_FAILED, TRYING, initialMode, loadSmartStream, shouldFallBack, type Mode, type PlayInfo, type SmartApi } from './smartstream'
import { Icon } from '@/design/Icon'
import { cn } from '@/design/cn'
import { formatDuration } from '@/features/library/format'
import { SnapshotGrid, type SnapshotData } from './SnapshotGrid'
import { Timeline, type PreviewInfo } from './Timeline'
import type { Segment } from './timeline'

export const RATES = [0.5, 0.75, 1, 1.25, 1.5, 2] as const
export const nextRate = (r: number) => RATES[(RATES.findIndex((x) => x === r) + 1) % RATES.length] ?? 1
export const clampTime = (t: number, dur: number) => Math.max(0, Number.isFinite(dur) && dur > 0 ? Math.min(dur, t) : t)
const clock = (s: number) => formatDuration(s) || '0:00'

const BTN = 'grid size-9 place-items-center rounded-lg text-fg/90 hover:bg-fg/10 hover:text-fg focus-visible:outline-2 focus-visible:outline-accent'

/**
 * Native <video> with the classic player's control bar: seek, play, previous/next, ±30 s, speed, mute and volume,
 * picture-in-picture, full screen, keyboard shortcuts, and the same "could not be played" fallback (retry or open in VLC).
 */
export function Player({ info, infoPending = false, src, poster, durationHint, segments = [], preview, autoPlay = false, autoNext = false, startAt = 0, snapshots, seekRequest, onTime, onPlayed, onEnd, onPrev, onNext, onOpenVlc }: { info?: PlayInfo | null; infoPending?: boolean; seekRequest?: { t: number; n: number }; snapshots?: SnapshotData; startAt?: number; onTime?: (position: number, duration: number) => void; onPlayed?: () => void; onEnd?: () => void; autoPlay?: boolean; autoNext?: boolean; src: string; poster?: string; durationHint?: number | null; segments?: Segment[]; preview?: PreviewInfo; onPrev?: () => void; onNext?: () => void; onOpenVlc: () => void }) {
  const video = useRef<HTMLVideoElement>(null)
  const startApplied = useRef(false)
  const pendingStart = useRef<number | null>(null)
  const [started, setStarted] = useState(false)
  useEffect(() => { startApplied.current = false; pendingStart.current = null; setStarted(false) }, [src])   // a new video may have its own start position
  const box = useRef<HTMLDivElement>(null)
  const [playing, setPlaying] = useState(false)
  const [time, setTime] = useState(0)
  const [dur, setDur] = useState(durationHint ?? 0)
  const [buffered, setBuffered] = useState(0)
  const [rate, setRate] = useState<number>(1)
  const [muted, setMuted] = useState(false)
  const [volume, setVolume] = useState(1)
  const [loading, setLoading] = useState(true)
  const [failed, setFailed] = useState(false)
  const [attempt, setAttempt] = useState(0)
  // Smart playback: files the browser may not play directly go through the client-side engine (loaded only when needed).
  const [smart, setSmart] = useState<SmartApi | null | undefined>(undefined)   // undefined: not loaded yet, null: unavailable
  const [mode, setMode] = useState<Mode>('direct')
  const [failText, setFailText] = useState('')
  const triedSmart = useRef(false)
  const [committed, setCommitted] = useState(false)   // the source was handed to the video: late codec info must not switch it mid-play
  useEffect(() => {
    if (!info || info.direct_likely) return
    let live = true
    void loadSmartStream().then((api) => { if (live) setSmart(api) })
    return () => { live = false }
  }, [info])
  const needsSmart = !!info && !info.direct_likely
  const ready = !infoPending && (!needsSmart || smart !== undefined)
  useEffect(() => { triedSmart.current = false; setCommitted(false); setMode('direct'); setFailText('') }, [src])
  useEffect(() => {
    if (!ready || committed) return
    setCommitted(true)
    setMode(initialMode(info, smart))
  }, [ready, committed, info, smart, src])
  const [loop, setLoop] = useState(false)
  const [hint, setHint] = useState('')
  const tap = useRef<{ t: number; x: number } | null>(null)
  const tapTimer = useRef<ReturnType<typeof setTimeout>>(undefined)
  const hintTimer = useRef<ReturnType<typeof setTimeout>>(undefined)

  const seek = useCallback((t: number) => {
    const v = video.current
    if (v) v.currentTime = clampTime(t, v.duration || dur)
  }, [dur])
  const toggle = useCallback(() => {
    const v = video.current
    if (!v) return
    if (v.paused) void v.play().catch(() => setFailed(true))
    else v.pause()
  }, [])
  /** A snapshot tile was chosen: play from that moment (now if the video knows its length, otherwise as soon as it does). */
  const pickTime = (t: number) => {
    const v = video.current
    if (!v) return
    if (v.readyState >= 1) v.currentTime = clampTime(t, v.duration || dur)
    else pendingStart.current = t
    void v.play().catch(() => setFailed(true))
  }
  /** A seek asked for from outside the player (a scene-marker chip): seek now, or as soon as the video knows its length. Does not start playback. */
  useEffect(() => {
    const v = video.current
    if (!seekRequest || !v) return
    if (v.readyState >= 1) v.currentTime = clampTime(seekRequest.t, v.duration || dur)
    else pendingStart.current = seekRequest.t
  }, [seekRequest]) // eslint-disable-line react-hooks/exhaustive-deps
  const flash = (text: string) => {
    setHint(text)
    clearTimeout(hintTimer.current)
    hintTimer.current = setTimeout(() => setHint(''), 700)
  }
  /** One tap toggles play. A second tap within 280 ms on the left or right third seeks 10 s that way (phone gesture). */
  const onTap = (e: React.MouseEvent<HTMLVideoElement>) => {
    const r = e.currentTarget.getBoundingClientRect()
    const x = (e.clientX - r.left) / r.width
    const now = Date.now()
    if (tap.current && now - tap.current.t < 280 && (x < 0.33 || x > 0.67)) {
      clearTimeout(tapTimer.current)
      tap.current = null
      const delta = x < 0.33 ? -10 : 10
      seek((video.current?.currentTime ?? 0) + delta)
      flash(delta < 0 ? '−10 s' : '+10 s')
      return
    }
    tap.current = { t: now, x }
    clearTimeout(tapTimer.current)
    tapTimer.current = setTimeout(() => { tap.current = null; toggle() }, 280)
  }
  const full = () => (document.fullscreenElement ? void document.exitFullscreen() : void box.current?.requestFullscreen?.())
  const pip = () => void (document.pictureInPictureElement ? document.exitPictureInPicture() : video.current?.requestPictureInPicture?.())?.catch(() => {})

  useEffect(() => {
    setFailed(false)
    setFailText('')
    setLoading(true)
    setTime(0)
  }, [src, attempt])

  // The smart pipeline owns the video element's source while it runs.
  useEffect(() => {
    if (!committed || mode !== 'mse' || !smart || !info) return
    const v = video.current
    if (!v) return
    const startTime = pendingStart.current ?? (startAt > 0 && !startApplied.current ? startAt : 0)
    if (startTime > 0) pendingStart.current = startTime
    const pipeline = smart.start(v, src, info, {
      startTime,
      onError: (msg) => { console.warn('[SmartStream] error:', msg); setFailText(SMART_FAILED); setFailed(true); setLoading(false) },
    })
    if (!pipeline) { triedSmart.current = true; setMode('direct'); return }
    return () => { try { pipeline.destroy() } catch { /* already gone */ } }
  }, [committed, mode, smart, info, src, attempt]) // eslint-disable-line react-hooks/exhaustive-deps

  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      const el = e.target as HTMLElement | null
      if (el && (el.isContentEditable || /^(INPUT|TEXTAREA|SELECT)$/.test(el.tagName)) && (el as HTMLInputElement).type !== 'range') return
      if (e.ctrlKey || e.metaKey || e.altKey) return
      const k = e.key.toLowerCase()
      if (k === ' ' || k === 'k') { e.preventDefault(); toggle() }
      else if (k === 'j') seek((video.current?.currentTime ?? 0) - 30)
      else if (k === 'l') seek((video.current?.currentTime ?? 0) + 30)
      else if (k === 'arrowleft') seek((video.current?.currentTime ?? 0) - 5)
      else if (k === 'arrowright') seek((video.current?.currentTime ?? 0) + 5)
      else if (k === 'm') setMuted((m) => !m)
      else if (k === 'f') full()
      else if (k === 'n') onNext?.()
      else if (e.key === 'P') onPrev?.()
    }
    window.addEventListener('keydown', onKey)
    return () => window.removeEventListener('keydown', onKey)
  }, [toggle, seek, onNext, onPrev])

  useEffect(() => {
    const v = video.current
    if (v) {
      v.playbackRate = rate
      v.muted = muted
      v.volume = volume
    }
  }, [rate, muted, volume])

  return (
    <div ref={box} className="overflow-hidden rounded-[18px] border border-line bg-surface">
      <div className="relative aspect-video w-full bg-black">
        <video
          key={`${attempt}-${mode}`}
          ref={video}
          src={committed && mode === 'direct' ? src : undefined}
          poster={poster}
          playsInline
          preload="metadata"
          autoPlay={autoPlay}
          onClick={onTap}
          loop={loop}
          onPlay={() => { setPlaying(true); setStarted(true); onPlayed?.() }}
          onPause={() => setPlaying(false)}
          onWaiting={() => setLoading(true)}
          onPlaying={() => setLoading(false)}
          onCanPlay={() => setLoading(false)}
          onLoadedMetadata={(e) => {
            const v = e.currentTarget
            setDur(v.duration || durationHint || 0)
            // Start where the viewer chose to resume, once per video (a retry keeps the position it reached).
            if (pendingStart.current !== null) { v.currentTime = clampTime(pendingStart.current, v.duration || durationHint || 0); pendingStart.current = null; startApplied.current = true }
            else if (startAt > 0 && !startApplied.current) { startApplied.current = true; v.currentTime = clampTime(startAt, v.duration || durationHint || 0) }
          }}
          onTimeUpdate={(e) => {
            setTime(e.currentTarget.currentTime)
            onTime?.(e.currentTarget.currentTime, e.currentTarget.duration || durationHint || 0)
            const b = e.currentTarget.buffered
            setBuffered(b.length ? b.end(b.length - 1) : 0)
          }}
          onEnded={() => { onEnd?.(); if (autoNext) onNext?.() }}
          onError={(e) => {
            const v = e.currentTarget
            if (shouldFallBack(mode, triedSmart.current, v.error?.code, info, smart)) {
              triedSmart.current = true
              if (v.currentTime > 0.5) pendingStart.current = v.currentTime
              flash(TRYING); setLoading(true); setMode('mse')
              return
            }
            if (mode === 'mse') setFailText(SMART_FAILED)
            setFailed(true); setLoading(false)
          }}
          className="absolute inset-0 h-full w-full object-contain"
        />
        {snapshots && !started && !failed ? <SnapshotGrid data={snapshots} onPick={pickTime} /> : null}
        {hint ? <div role="status" className="pointer-events-none absolute left-1/2 top-1/2 -translate-x-1/2 -translate-y-1/2 rounded-full bg-black/65 px-4 py-2 text-sm font-bold text-white">{hint}</div> : null}
        {loading && !failed ? <div role="status" aria-label="Loading video" className="absolute inset-0 grid place-items-center"><span className="size-10 animate-spin rounded-full border-4 border-white/20 border-t-accent" /></div> : null}
        {!playing && !failed && !loading && !(snapshots && !started) ? (
          <button type="button" onClick={toggle} aria-label="Play" className="absolute left-1/2 top-1/2 grid size-16 -translate-x-1/2 -translate-y-1/2 place-items-center rounded-full bg-black/50 text-white backdrop-blur-sm hover:bg-black/65">
            <Icon name="play" className="size-7" />
          </button>
        ) : null}
        {failed ? (
          <div role="alert" className="absolute inset-0 grid content-center justify-items-center gap-3 bg-black/85 p-5 text-center">
            <span className="grid size-12 place-items-center rounded-full bg-bad/20 text-xl font-bold text-bad">!</span>
            <p className="max-w-lg text-lg font-bold leading-snug">{failText || 'This video could not be played from the original stream (the browser cannot decode this file, or Telegram is not connected).'}</p>
            <p className="max-w-lg text-xs text-muted">Automatic conversion is off. You can retry the original stream or open it in VLC.</p>
            <div className="flex flex-wrap justify-center gap-2">
              <button type="button" onClick={() => setAttempt(attempt + 1)} className="h-10 rounded-xl bg-accent px-4 text-sm font-bold text-accent-fg">Retry original</button>
              <button type="button" onClick={onOpenVlc} className="inline-flex h-10 items-center gap-2 rounded-xl border border-line-2 bg-surface px-4 text-sm font-bold"><Icon name="brand-vlc" />Open in VLC</button>
            </div>
          </div>
        ) : null}
      </div>
      <div className="grid gap-1 px-4 pb-3 pt-2">
        <div className="flex justify-between text-xs font-semibold tabular-nums" aria-hidden="true"><span>{clock(time)}</span><span className="text-muted">{dur ? clock(dur) : '–:––'}</span></div>
        <Timeline time={time} duration={dur} buffered={buffered} segments={segments} preview={preview} onSeek={seek} />
        <div className="flex flex-wrap items-center gap-0.5">
          <button type="button" className={BTN} onClick={onPrev} disabled={!onPrev} aria-label="Previous video" title="Previous (Shift+P)"><Icon name="arrow-left" /></button>
          <button type="button" className={cn(BTN, 'size-10')} onClick={toggle} aria-label={playing ? 'Pause' : 'Play'} title="Play / pause (Space)"><Icon name={playing ? 'pause' : 'play'} /></button>
          <button type="button" className={BTN} onClick={onNext} disabled={!onNext} aria-label="Next video" title="Next (N)"><Icon name="arrow-right" /></button>
          <button type="button" className={BTN} onClick={() => seek((video.current?.currentTime ?? 0) - 30)} aria-label="Back 30 seconds" title="Back 30 seconds (J)"><Icon name="rewind" /></button>
          <button type="button" className={BTN} onClick={() => seek((video.current?.currentTime ?? 0) + 30)} aria-label="Forward 30 seconds" title="Forward 30 seconds (L)"><Icon name="forward" /></button>
          <span className="flex-1" />
          <button type="button" className={cn(BTN, 'w-11 text-xs font-bold')} onClick={() => setRate(nextRate(rate))} aria-label={`Speed ${rate}x`} title="Speed">{rate}×</button>
          <button type="button" className={cn(BTN, loop && 'bg-accent/20 text-accent')} onClick={() => setLoop(!loop)} aria-label="Loop" aria-pressed={loop} title="Loop this video"><Icon name="refresh" /></button>
          <button type="button" className={cn(BTN, 'hidden sm:grid')} onClick={() => setMuted(!muted)} aria-label={muted ? 'Unmute' : 'Mute'} aria-pressed={muted} title="Mute (M)">
            <span aria-hidden="true" className="text-base leading-none">{muted || volume === 0 ? '🔇' : '🔊'}</span>
          </button>
          <input type="range" min={0} max={1} step={0.05} value={muted ? 0 : volume} onChange={(e) => { setVolume(Number(e.target.value)); setMuted(false) }} aria-label="Volume" className="hidden h-1.5 w-20 accent-[var(--color-accent)] sm:block" />
          <button type="button" className={cn(BTN, 'hidden sm:grid')} onClick={pip} aria-label="Picture in picture" title="Picture in picture"><Icon name="picture" /></button>
          <button type="button" className={BTN} onClick={full} aria-label="Full screen" title="Full screen (F)"><Icon name="expand" /></button>
        </div>
      </div>
    </div>
  )
}
