import { useCallback, useEffect, useRef, useState } from 'react'
import { SMART_FAILED, TRYING, initialMode, isDecodeFailure, loadSmartStream, shouldFallBack, type Mode, type PlayInfo, type SmartApi, type SmartPipeline } from './smartstream'
import { Icon } from '@/design/Icon'
import { BufferIcon, ChevronIcon, FullscreenIcon, GearIcon, NextIcon, PauseIcon, PipIcon, PlayIcon, PrevIcon, VolumeIcon } from './PlayerIcons'
import { Divider, MenuHead, MenuItem, MenuRow, PopMenu, VolumeBar } from './PlayerParts'
import { cn } from '@/design/cn'
import { formatDuration } from '@/features/library/format'
import { SnapshotGrid, type SnapshotData } from './SnapshotGrid'
import { Timeline, type PreviewInfo } from './Timeline'
import type { Segment } from './timelineLogic'
import { HLS_FAILED, hlsUrl, startHls } from './hlsplay'
import { DOUBLE_TAP_MS, KEEP_SKIPPING_MS, registerTap, type TapState } from './tapseek'
import { CcMenu } from './CcMenu'
import { blockingEnabled, resumeAudio, routeThroughApp, setBlocking } from './audioGraph'
import { CaptionOverlay } from './CaptionOverlay'
import { DEFAULT_STYLE, SIZE_STEP, clampSize, loadStyle, saveStyle, sizeLabel, type CaptionBg } from './captionStyle'
import { useAltAudio, useTracks, type MediaRef } from './useTracks'

export const RATES = [0.5, 0.75, 1, 1.25, 1.5, 2] as const
export const nextRate = (r: number) => RATES[(RATES.findIndex((x) => x === r) + 1) % RATES.length] ?? 1
export const clampTime = (t: number, dur: number) => Math.max(0, Number.isFinite(dur) && dur > 0 ? Math.min(dur, t) : t)
const clock = (s: number) => formatDuration(s) || '0:00'

const BTN = 'grid size-8 shrink-0 place-items-center rounded-md text-fg/85 transition-[background-color,color,transform] hover:bg-fg/10 hover:text-fg focus-visible:outline-2 focus-visible:outline-accent active:scale-95 disabled:opacity-35 disabled:hover:bg-transparent'

/**
 * Native <video> with the classic player's control bar: seek, play, previous/next, ±30 s, speed, mute and volume,
 * picture-in-picture, full screen, keyboard shortcuts, and the same "could not be played" fallback (retry or open in VLC).
 */
/** Tell the server the viewer left this video. Best effort: a failure only means its cache is cleared a little later. */
export function releasePlayback(channelId: string, messageId: number, reason = 'viewer_left') {
  const id = Number(channelId)
  if (!Number.isFinite(id) || !Number.isFinite(messageId)) return
  try {
    void fetch('/api/play/release', {
      method: 'POST', keepalive: true, credentials: 'same-origin', headers: { 'Content-Type': 'application/json', Accept: 'application/json' },
      body: JSON.stringify({ channel_id: id, message_id: messageId, reason }),
    }).catch(() => undefined)
  } catch { /* nothing to do */ }
}

export function Player({ media, info, infoPending = false, src, poster, durationHint, segments = [], preview, autoPlay = false, autoNext = false, startAt = 0, snapshots, seekRequest, onTime, onPlayed, onEnd, onPrev, onNext, onOpenVlc, onOpenNative }: { media?: MediaRef; info?: PlayInfo | null; infoPending?: boolean; seekRequest?: { t: number; n: number }; snapshots?: SnapshotData; startAt?: number; onTime?: (position: number, duration: number) => void; onPlayed?: () => void; onEnd?: () => void; autoPlay?: boolean; autoNext?: boolean; src: string; poster?: string; durationHint?: number | null; segments?: Segment[]; preview?: PreviewInfo; onPrev?: () => void; onNext?: () => void; onOpenVlc: () => void; onOpenNative?: () => void }) {
  const video = useRef<HTMLVideoElement>(null)
  const startApplied = useRef(false)
  const smartPipeline = useRef<SmartPipeline | null>(null)   // while the smart (in-browser remux) path owns the video
  const pendingStart = useRef<number | null>(null)
  const [started, setStarted] = useState(false)
  // Leaving the video (Back, another page, another video, closing the tab) tells the server to stop that video's downloads and clear its
  // temporary cache, so only the video on screen has a playback job. `keepalive` lets the request finish even while the page unloads.
  const mediaCid = media?.cid
  const mediaMid = media?.mid
  useEffect(() => {
    if (!mediaCid || !Number.isFinite(mediaMid)) return
    const release = () => releasePlayback(mediaCid, Number(mediaMid))
    window.addEventListener('pagehide', release)
    return () => { window.removeEventListener('pagehide', release); release() }
  }, [mediaCid, mediaMid])
  useEffect(() => { startApplied.current = false; pendingStart.current = null; setStarted(false); setRes('') }, [src])   // a new video may have its own start position
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
  const triedHls = useRef(false)
  const [committed, setCommitted] = useState(false)   // the source was handed to the video: late codec info must not switch it mid-play
  useEffect(() => {
    if (!info || info.direct_likely) return
    let live = true
    void loadSmartStream().then((api) => { if (live) setSmart(api) })
    return () => { live = false }
  }, [info])
  const needsSmart = !!info && !info.direct_likely
  const ready = !infoPending && (!needsSmart || smart !== undefined)
  useEffect(() => { triedSmart.current = false; triedHls.current = false; setCommitted(false); setMode('direct'); setFailText('') }, [src])
  useEffect(() => {
    if (!ready || committed) return
    setCommitted(true)
    setMode(initialMode(info, smart))
  }, [ready, committed, info, smart, src])
  const [loop, setLoop] = useState(false)
  const [block, setBlock] = useState(blockingEnabled)
  const [hint, setHint] = useState('')
  const [res, setRes] = useState('')
  const [ratio, setRatio] = useState(16 / 9)   // the video's own shape, so the frame fits it instead of leaving black bars
  const [isFs, setIsFs] = useState(false)
  const [idle, setIdle] = useState(false)
  const overBar = useRef(false)
  const idleTimer = useRef<ReturnType<typeof setTimeout>>(undefined)
  const altAudio = useRef<HTMLAudioElement>(null)
  const [capStyle, setCapStyle] = useState(loadStyle)
  const capRef = useRef(capStyle)
  capRef.current = capStyle
  const changeCap = (patch: Partial<typeof capStyle>) => setCapStyle((c) => { const n = { ...c, ...patch }; saveStyle(n); return n })
  const tracks = useTracks(media, video, time, playing)
  useAltAudio(video, altAudio, tracks.altUrl, muted, volume, rate)
  const tap = useRef<TapState | null>(null)
  const [skip, setSkip] = useState<{ side: 'left' | 'right'; total: number } | null>(null)
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
  /** One tap toggles play. Double-tap the left or right third to skip 10 s; keep tapping to add 10 s more each time (like YouTube). */
  const onTap = (e: React.MouseEvent<HTMLVideoElement>) => {
    resumeAudio()
    const r = e.currentTarget.getBoundingClientRect()
    const { state, skip: by } = registerTap(tap.current, (e.clientX - r.left) / r.width, Date.now())
    tap.current = state
    clearTimeout(tapTimer.current)
    if (by !== null) {
      seek((video.current?.currentTime ?? 0) + by)
      setSkip({ side: state.side === 'left' ? 'left' : 'right', total: state.total })
      tapTimer.current = setTimeout(() => { tap.current = null; setSkip(null) }, KEEP_SKIPPING_MS)
      return
    }
    tapTimer.current = setTimeout(() => { tap.current = null; toggle() }, DOUBLE_TAP_MS)
  }
  const full = () => (document.fullscreenElement ? void document.exitFullscreen() : void box.current?.requestFullscreen?.())
  useEffect(() => {
    const change = () => { const on = document.fullscreenElement === box.current; setIsFs(on); setIdle(false) }
    document.addEventListener('fullscreenchange', change)
    return () => document.removeEventListener('fullscreenchange', change)
  }, [])
  // Full screen: controls fade out after a moment without movement (like YouTube), and come back on any movement or key.
  const wake = useCallback(() => {
    setIdle(false)
    clearTimeout(idleTimer.current)
    idleTimer.current = setTimeout(() => { if (!overBar.current) setIdle(true) }, 2800)
  }, [])
  useEffect(() => { if (isFs) wake(); return () => clearTimeout(idleTimer.current) }, [isFs, wake])
  const hideBar = isFs && idle && playing
  const overlay = isFs || ratio < 0.9   // tall and square videos carry the controls on the picture, so everything fits the window
  const pip = () => void (document.pictureInPictureElement ? document.exitPictureInPicture() : video.current?.requestPictureInPicture?.())?.catch(() => {})

  useEffect(() => {
    setFailed(false)
    setFailText('')
    setLoading(true)
    setTime(0)
  }, [src, attempt])

  /** Hand the file to the PC's converter (HLS), once, resuming where playback had reached. */
  const toHls = useCallback((at: number) => {
    if (triedHls.current || !hlsUrl(src, media)) return false
    triedHls.current = true
    if (at > 0.5) pendingStart.current = at
    setHint('Converting for this browser…'); setLoading(true); setFailed(false); setMode('hls')
    return true
  }, [src, media])

  useEffect(() => {
    if (!committed || mode !== 'hls') return
    const v = video.current; const url = hlsUrl(src, media)
    if (!v || !url) return
    let run: { destroy: () => void } | null = null; let gone = false
    void startHls(v, url, { startTime: pendingStart.current ?? (startAt > 0 && !startApplied.current ? startAt : 0), onError: (why) => { console.warn('[HLS] error:', why); if (!gone) { setFailText(HLS_FAILED); setFailed(true); setLoading(false) } } })
      .then((r) => { if (gone) r?.destroy(); else run = r })
    return () => { gone = true; run?.destroy() }
  }, [committed, mode, src, attempt]) // eslint-disable-line react-hooks/exhaustive-deps

  // The smart pipeline owns the video element's source while it runs.
  useEffect(() => {
    if (!committed || mode !== 'mse' || !smart || !info) return
    const v = video.current
    if (!v) return
    const startTime = pendingStart.current ?? (startAt > 0 && !startApplied.current ? startAt : 0)
    if (startTime > 0) pendingStart.current = startTime
    const pipeline = smart.start(v, src, info, {
      startTime,
      onError: (msg) => { console.warn('[SmartStream] error:', msg); if (toHls(video.current?.currentTime ?? 0)) return; setFailText(SMART_FAILED); setFailed(true); setLoading(false) },
    })
    if (!pipeline) { triedSmart.current = true; setMode('direct'); return }
    smartPipeline.current = pipeline
    return () => { smartPipeline.current = null; try { pipeline.destroy() } catch { /* already gone */ } }
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
      v.muted = muted || tracks.altUrl !== null
      v.volume = volume
    }
  }, [rate, muted, volume, tracks.altUrl])

  const buffer = dur > 0 ? Math.min(100, Math.round((buffered / dur) * 100)) : 0
  return (
    <div ref={box} onPointerMove={isFs ? wake : undefined} onKeyDown={isFs ? wake : undefined} onTouchStart={isFs ? wake : undefined}
      style={isFs ? undefined : ({ '--pw': `min(100%, max(330px, calc((100dvh - 290px) / (var(--ax-page-zoom, 1) * var(--ax-ui-zoom, 1)) * ${ratio.toFixed(4)})))` } as React.CSSProperties)}
      className={cn(isFs ? 'relative h-full w-full bg-black' : cn('relative mx-auto grid w-full sm:w-[var(--pw)]', !overlay && 'gap-4 sm:gap-5'), hideBar && 'cursor-none [&_video]:cursor-none')}>
      <div data-caption-frame style={isFs ? undefined : { aspectRatio: ratio }} className={cn('@container relative w-full overflow-hidden bg-[#111215]', isFs ? 'absolute inset-0 h-full' : 'rounded-[18px] shadow-2xl ring-1 ring-white/10 sm:rounded-[22px]')}>
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
          onPlay={(e) => { routeThroughApp(e.currentTarget); resumeAudio(); setPlaying(true); setStarted(true); onPlayed?.() }}
          onPause={() => setPlaying(false)}
          onWaiting={() => setLoading(true)}
          // The in-browser remux path reads the file from the start; a jump to a moment it has not read yet makes it read from there instead.
          onSeeking={(e) => { if (smartPipeline.current?.type === 'mse-mp4box') smartPipeline.current.seekTo?.(e.currentTarget.currentTime) }}
          onPlaying={() => { resumeAudio(); setLoading(false) }}
          onCanPlay={() => setLoading(false)}
          onLoadedMetadata={(e) => {
            const v = e.currentTarget
            setDur(v.duration || durationHint || 0)
            if (v.videoWidth) { setRes(`${v.videoWidth} × ${v.videoHeight}`); setRatio(Math.min(2.4, Math.max(0.5, v.videoWidth / v.videoHeight))) }
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
            if (mode !== 'hls' && isDecodeFailure(v.error?.code) && toHls(v.currentTime)) return
            if (mode === 'mse') setFailText(SMART_FAILED)
            if (mode === 'hls') setFailText(HLS_FAILED)
            setFailed(true); setLoading(false)
          }}
          className="absolute inset-0 h-full w-full cursor-pointer object-contain"
        />
        {skip ? (
          <div data-testid="skip-indicator" aria-live="polite" className={cn('pointer-events-none absolute inset-y-0 z-20 grid w-1/3 place-items-center bg-white/10 text-white', skip.side === 'left' ? 'left-0 rounded-r-[100%]' : 'right-0 rounded-l-[100%]')}>
            <div className="grid justify-items-center gap-0.5 text-sm font-semibold"><span aria-hidden="true" className="text-xl">{skip.side === 'left' ? '◀◀' : '▶▶'}</span>{skip.total} seconds</div>
          </div>
        ) : null}
        {tracks.altUrl ? <audio ref={altAudio} src={tracks.altUrl} preload="auto" className="hidden" /> : null}
        {tracks.text ? <CaptionOverlay text={tracks.text} style={capStyle} onMove={(x, y) => setCapStyle((c) => ({ ...c, x, y }))} onMoveEnd={() => saveStyle(capRef.current)} /> : null}
        {tracks.liveStatus ? <div role="status" className="pointer-events-none absolute left-3 top-3 z-20 rounded-full bg-black/60 px-3 py-1 text-xs font-medium text-white backdrop-blur-sm">{tracks.liveStatus}</div> : null}
        {snapshots && !started && !failed ? <SnapshotGrid data={snapshots} onPick={pickTime} /> : null}
        {hint ? <div role="status" className="pointer-events-none absolute left-1/2 top-4 z-30 -translate-x-1/2 rounded-full border border-white/15 bg-[#141417]/90 px-4 py-1.5 text-xs font-medium text-white shadow-lg backdrop-blur-md">{hint}</div> : null}
        {loading && !failed ? <div role="status" aria-label="Loading video" className="absolute inset-0 grid place-items-center"><span className="size-10 animate-spin rounded-full border-4 border-white/20 border-t-white" /></div> : null}
        {!playing && !failed && !loading ? (
          <button type="button" onClick={toggle} aria-label="Play" className="absolute left-1/2 top-1/2 z-20 grid size-12 -translate-x-1/2 -translate-y-1/2 place-items-center rounded-full border border-white/15 bg-[#1e1f23]/80 text-white shadow-xl backdrop-blur-md transition-[scale] duration-200 hover:scale-105 active:scale-95 sm:size-14">
            <span className="ml-0.5"><PlayIcon cls="size-5 sm:size-6" /></span>
          </button>
        ) : null}
        {failed ? (
          <div role="alert" className="absolute inset-0 grid content-center justify-items-center gap-2 overflow-y-auto bg-black/85 p-3 text-center text-white sm:gap-3 sm:p-5">
            <span className="hidden size-12 place-items-center rounded-full bg-bad/20 text-xl font-bold text-bad sm:grid">!</span>
            <p className="max-w-lg text-sm font-bold leading-snug sm:text-lg">
              {failText ? failText : <><span className="sm:hidden">This video could not be played here.</span><span className="hidden sm:inline">This video could not be played from the original stream (the browser cannot decode this file, or Telegram is not connected).</span></>}
            </p>
            <p className="hidden max-w-lg text-xs text-zinc-400 sm:block">{onOpenNative ? 'The browser cannot decode every file (some HEVC, 10-bit and MKV videos). The native player can.' : 'You can retry the original stream or open it in VLC.'}</p>
            <div className="flex flex-wrap justify-center gap-2">
              {onOpenNative ? <button type="button" onClick={onOpenNative} className="h-9 rounded-xl bg-accent px-3 text-sm font-bold text-accent-fg sm:h-10 sm:px-4">Play with the native player</button> : null}
              <button type="button" onClick={() => setAttempt(attempt + 1)} className={onOpenNative ? 'h-9 rounded-xl border border-white/20 bg-white/10 px-3 text-sm font-bold sm:h-10 sm:px-4' : 'h-9 rounded-xl bg-accent px-3 text-sm font-bold text-accent-fg sm:h-10 sm:px-4'}>Retry original</button>
              <button type="button" onClick={onOpenVlc} className="inline-flex h-9 items-center gap-2 rounded-xl border border-white/20 bg-white/10 px-3 text-sm font-bold sm:h-10 sm:px-4"><Icon name="brand-vlc" />Open in VLC</button>
            </div>
          </div>
        ) : null}
      </div>
      <div data-ov={overlay ? '' : undefined} onPointerEnter={() => { overBar.current = true; setIdle(false) }} onPointerLeave={() => { overBar.current = false }}
        className={cn('@container grid gap-1.5', overlay && 'absolute inset-x-0 bottom-0 z-30 bg-gradient-to-t from-black/85 via-black/50 to-transparent px-3 pb-2 pt-12 text-white [--color-fg:#fff] [--color-muted:#c9d1e0] transition-opacity duration-300 sm:px-4', isFs && 'px-4 pb-3 sm:px-8', !overlay && 'gap-3', overlay && !isFs && 'rounded-b-[18px] sm:rounded-b-[22px]', hideBar && 'pointer-events-none opacity-0')}>
        <div>
          <div className="mb-1 flex items-center justify-between px-0.5 text-[13px] font-semibold tracking-tight tabular-nums" aria-hidden="true"><span>{clock(time)}</span><span className="font-medium text-muted">{dur ? clock(dur) : '–:––'}</span></div>
          <Timeline time={time} duration={dur} buffered={buffered} segments={segments} preview={preview} onSeek={seek} />
        </div>
        <div className="flex items-center justify-between py-1 text-xs font-medium text-fg/80 sm:text-[13px]">
          <div className="flex min-w-0 items-center gap-1 @lg:gap-2.5">
            <button type="button" className={BTN} onClick={onPrev} disabled={!onPrev} aria-label="Previous video" title="Previous (Shift+P)"><PrevIcon /></button>
            <button type="button" className={BTN} onClick={toggle} aria-label={playing ? 'Pause' : 'Play'} title="Play / pause (Space)">{playing ? <PauseIcon /> : <PlayIcon />}</button>
            <button type="button" className={BTN} onClick={onNext} disabled={!onNext} aria-label="Next video" title="Next (N)"><NextIcon /></button>
            <Divider />
            <div className="hidden @lg:block">
            <PopMenu label="Buffer status" align="left" button={<><BufferIcon /><span className={cn('size-1.5 rounded-full', buffer >= 99 ? 'bg-emerald-400 shadow-[0_0_6px_#34d399]' : 'bg-amber-400')} /><span className="hidden @lg:inline">{buffer >= 99 ? 'Cached' : `${buffer}%`}</span><ChevronIcon /></>}>
              <MenuHead>Buffer status</MenuHead>
              <MenuRow k="Buffered" v={`${buffer}%`} good={buffer >= 99} />
              <MenuRow k="Playback" v={mode === 'mse' ? 'Smart' : 'Direct'} />
              {res ? <MenuRow k="Picture" v={res} /> : null}
            </PopMenu>
            </div>
            <Divider />
            <PopMenu label={`Speed ${rate}x`} align="left" button={<><span>{rate}x</span><ChevronIcon /></>}>
              {RATES.map((r) => (
                <MenuItem key={r} active={r === rate} onClick={(close) => { setRate(r); flash(`Speed: ${r}x`); close() }}>{r}x</MenuItem>
              ))}
            </PopMenu>
            <Divider />
            <div className="flex items-center gap-1.5">
              <button type="button" className={BTN} onClick={() => setMuted(!muted)} aria-label={muted ? 'Unmute' : 'Mute'} aria-pressed={muted} title="Mute (M)"><VolumeIcon muted={muted || volume === 0} /></button>
              <VolumeBar value={muted ? 0 : volume} onChange={(v) => { setVolume(v); setMuted(false) }} />
            </div>
          </div>
          <div className="flex items-center gap-1 @lg:gap-2.5">
            <button type="button" className={cn(BTN, 'hidden @lg:grid')} onClick={pip} aria-label="Picture in picture" title="Picture in picture"><PipIcon /></button>
            {tracks.info && (tracks.info.audio.length > 1 || tracks.info.subtitles.length > 0 || tracks.info.captions.available) ? <CcMenu info={tracks.info} audio={tracks.audio} sub={tracks.sub} live={tracks.live} altBusy={tracks.altBusy} onAudio={tracks.pickAudio} onSub={tracks.pickSub} /> : null}
            <PopMenu label="Settings" align="right" iconOnly button={<GearIcon />}>
              <MenuItem pressed={block} onClick={() => { const on = !block; setBlock(on); setBlocking(on); flash(on ? 'Chrome Live Caption blocked after reload' : 'Chrome Live Caption allowed after reload') }} label="Keep Chrome's Live Caption off this player">Keep Chrome's Live Caption off <span className={block ? 'text-emerald-400' : 'text-muted'}>{block ? 'ON' : 'OFF'}</span></MenuItem>
              <p className="max-w-[15rem] px-2.5 pb-1 text-[11px] leading-snug text-muted">Plays the sound through the app so Chrome's own black "Live Caption" box has nothing to caption. Applies after a page reload.</p>
              <MenuHead>Caption style</MenuHead>
              <div className="flex items-center justify-between gap-3 px-2.5 py-1 text-xs"><span className="text-muted">Size</span>
                <span className="flex items-center gap-1">
                  <button type="button" aria-label="Smaller captions" onClick={() => changeCap({ size: clampSize(capStyle.size - SIZE_STEP) })} className="grid size-7 place-items-center rounded-md border border-line-2 font-bold hover:bg-fg/10">A−</button>
                  <output aria-label="Caption size" className="w-10 text-center font-semibold tabular-nums">{sizeLabel(capStyle.size)}</output>
                  <button type="button" aria-label="Larger captions" onClick={() => changeCap({ size: clampSize(capStyle.size + SIZE_STEP) })} className="grid size-7 place-items-center rounded-md border border-line-2 font-bold hover:bg-fg/10">A+</button>
                </span></div>
              {(['none', 'soft', 'solid'] as CaptionBg[]).map((b) => <MenuItem key={b} active={capStyle.bg === b} onClick={() => changeCap({ bg: b })}>{b === 'none' ? 'No box (outlined text)' : b === 'soft' ? 'Soft box' : 'Solid box'}</MenuItem>)}
              <MenuItem onClick={() => changeCap({ x: DEFAULT_STYLE.x, y: DEFAULT_STYLE.y, size: DEFAULT_STYLE.size })} label="Reset caption position and size">Reset position and size</MenuItem>
              <MenuHead>Playback</MenuHead>
              <MenuHead>Live captions</MenuHead>
              {tracks.info?.captions.available ? (<>
                <MenuItem active={tracks.sub.kind !== 'live'} onClick={(close) => { if (tracks.sub.kind === 'live') tracks.pickSub({ kind: 'off' }); close() }}>Off</MenuItem>
                <MenuItem active={tracks.sub.kind === 'live' && !tracks.sub.original} onClick={(close) => { tracks.pickSub({ kind: 'live' }); close() }}>English (translated)</MenuItem>
                <MenuItem active={tracks.sub.kind === 'live' && !!tracks.sub.original} onClick={(close) => { tracks.pickSub({ kind: 'live', original: true }); close() }}>Original language</MenuItem>
              </>) : <span className="max-w-[13rem] px-2.5 py-1 text-[11px] leading-snug text-muted">{tracks.info ? <>Needs <code>pip install faster-whisper</code></> : 'Loading…'}</span>}
              <MenuHead>Playback</MenuHead>
              <MenuItem onClick={() => { setLoop(!loop); flash(`Auto-repeat: ${loop ? 'OFF' : 'ON'}`) }} label="Loop" pressed={loop}>Auto-repeat <span className={loop ? 'text-emerald-400' : 'text-muted'}>{loop ? 'ON' : 'OFF'}</span></MenuItem>
            </PopMenu>
            <button type="button" className={BTN} onClick={full} aria-label="Full screen" title="Full screen (F)"><FullscreenIcon /></button>
          </div>
        </div>
      </div>
    </div>
  )
}
