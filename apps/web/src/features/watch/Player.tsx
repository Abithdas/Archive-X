import { useCallback, useEffect, useRef, useState } from 'react'
import { Icon } from '@/design/Icon'
import { PlayIcon } from './PlayerIcons'
import { MenuItem, MenuSection } from './PlayerParts'
import { CaptionStyleItems, PictureSoundItems, PlaybackItems, PlayerControls, RATES, playerKey } from './PlayerControls'
import { cn } from '@/design/cn'
import { SnapshotGrid, type SnapshotData } from './SnapshotGrid'
import type { PreviewInfo } from './Timeline'
import type { Segment } from './timelineLogic'
import { DOUBLE_TAP_MS, KEEP_SKIPPING_MS, registerTap, type TapState } from './tapseek'
import { CcMenu } from './CcMenu'
import { blockingEnabled, resumeAudio, routeThroughApp, setBlocking, setBoost, setSoundMode } from './audioGraph'
import { SHARPEN_ID, loadPS, normalizePS, pictureFilter, savePS, zoomScale, type PictureSound } from './pictureSound'
import { loadPrefs as loadPlayerPrefs, savePrefs as savePlayerPrefs } from './playerPrefs'
import { sampleOf, statRows, type StreamFacts, type StreamSample } from './streamStats'
import { CaptionOverlay } from './CaptionOverlay'
import { releasePlayback } from './release'
import { loadStyle, saveStyle } from './captionStyle'
import { useAltAudio, useTracks, type MediaRef } from './useTracks'

export const nextRate = (r: number) => RATES[(RATES.findIndex((x) => x === r) + 1) % RATES.length] ?? 1
const clampTime = (t: number, dur: number) => Math.max(0, Number.isFinite(dur) && dur > 0 ? Math.min(dur, t) : t)

/**
 * Native <video> with the classic player's control bar: seek, play, previous/next, ±30 s, speed, mute and volume,
 * picture-in-picture, full screen, keyboard shortcuts, and the same "could not be played" fallback (retry or open in VLC).
 */

export function Player({ media, infoPending = false, src, poster, durationHint, segments = [], preview, autoPlay = false, autoNext = false, startAt = 0, snapshots, seekRequest, onTime, onPlayed, onEnd, onPrev, onNext, onOpenVlc, onOpenNative, onUnplayable, facts }: { facts?: StreamFacts; onUnplayable?: () => void; media?: MediaRef; infoPending?: boolean; seekRequest?: { t: number; n: number }; snapshots?: SnapshotData; startAt?: number; onTime?: (position: number, duration: number) => void; onPlayed?: () => void; onEnd?: () => void; autoPlay?: boolean; autoNext?: boolean; src: string; poster?: string; durationHint?: number | null; segments?: Segment[]; preview?: PreviewInfo; onPrev?: () => void; onNext?: () => void; onOpenVlc?: () => void; onOpenNative?: () => void }) {
  const video = useRef<HTMLVideoElement>(null)
  const startApplied = useRef(false)
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
  useEffect(() => { startApplied.current = false; pendingStart.current = null; setStarted(false) }, [src])   // a new video may have its own start position
  const box = useRef<HTMLDivElement>(null)
  const [playing, setPlaying] = useState(false)
  const [time, setTime] = useState(0)
  const [dur, setDur] = useState(durationHint ?? 0)
  const [buffered, setBuffered] = useState(0)
  const [saved] = useState(loadPlayerPrefs)   // volume, mute and speed carry over from the last video
  const [rate, setRate] = useState<number>(saved.rate)
  const [muted, setMuted] = useState(saved.muted)
  const [volume, setVolume] = useState(saved.volume)
  useEffect(() => { savePlayerPrefs({ volume, muted, rate }) }, [volume, muted, rate])
  const [loading, setLoading] = useState(true)
  const [failed, setFailed] = useState(false)
  useEffect(() => { if (failed) onUnplayable?.() }, [failed]) // eslint-disable-line react-hooks/exhaustive-deps -- the page switches to the native player once, when this video cannot be played here
  const [attempt, setAttempt] = useState(0)
  // The video plays as it is: this web view plays what Windows can decode. A file it cannot play is handed to the native player (see WatchPage), never converted here.
  const [committed, setCommitted] = useState(false)   // the source was handed to the video: late codec info must not switch it mid-play
  const ready = !infoPending
  useEffect(() => { setCommitted(false) }, [src])
  useEffect(() => {
    if (!ready || committed) return
    setCommitted(true)
  }, [ready, committed, src])
  const [loop, setLoop] = useState(false)
  const [block, setBlock] = useState(blockingEnabled)
  const [hint, setHint] = useState('')
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
  const [ps, setPs] = useState<PictureSound>(loadPS)
  const [boostFailed, setBoostFailed] = useState(false)
  const changePs = (patch: Partial<PictureSound>) => setPs((c) => { const n = normalizePS({ ...c, ...patch }); savePS(n); return n })
  const [shift, setShift] = useState(0)   // subtitle sync, in seconds; starts at 0 for every video
  useEffect(() => setShift(0), [src])
  useEffect(() => { setBoostFailed(!(setBoost(video.current, ps.boost / 100) && setSoundMode(video.current, ps.sound))) }, [ps.boost, ps.sound])
  const [sample, setSample] = useState<StreamSample | null>(null)
  const tracks = useTracks(media, video, time, playing, shift)
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
  useEffect(() => () => { clearTimeout(hintTimer.current); clearTimeout(tapTimer.current) }, [])   // a hint or a tap still waiting must not fire after the player is gone
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
    setLoading(true)
    setTime(0)
  }, [src, attempt])

  useEffect(() => {
    const onKey = (e: KeyboardEvent) => playerKey(e, { toggle, seekBy: (d) => seek((video.current?.currentTime ?? 0) + d), mute: () => setMuted((m) => !m), full, next: onNext, prev: onPrev })
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
      className={cn(isFs ? 'relative h-full w-full bg-black' : cn('relative grid w-full sm:w-[var(--pw)]', !overlay && 'gap-4 sm:gap-5'), hideBar && 'cursor-none [&_video]:cursor-none')}>
      <div data-caption-frame style={isFs ? undefined : { aspectRatio: ratio }} className={cn('@container relative w-full overflow-hidden bg-[#111215]', isFs ? 'absolute inset-0 h-full' : 'rounded-[18px] shadow-2xl ring-1 ring-white/10 sm:rounded-[22px]')}>
        <svg aria-hidden="true" focusable="false" className="pointer-events-none absolute size-0"><filter id={SHARPEN_ID} colorInterpolationFilters="sRGB"><feConvolveMatrix order="3" kernelMatrix="0 -0.6 0 -0.6 3.4 -0.6 0 -0.6 0" divisor="1" preserveAlpha="true" /></filter></svg>
        <video
          key={attempt}
          ref={video}
          src={committed ? src : undefined}
          poster={poster}
          playsInline
          preload="metadata"
          autoPlay={autoPlay}
          onClick={onTap}
          loop={loop}
          onPlay={(e) => { routeThroughApp(e.currentTarget); resumeAudio(); setPlaying(true); setStarted(true); onPlayed?.() }}
          onPause={() => setPlaying(false)}
          onWaiting={() => setLoading(true)}
          onPlaying={() => { resumeAudio(); setLoading(false) }}
          onCanPlay={() => setLoading(false)}
          onLoadedMetadata={(e) => {
            const v = e.currentTarget
            setDur(v.duration || durationHint || 0)
            if (v.videoWidth) { setRatio(Math.min(2.4, Math.max(0.5, v.videoWidth / v.videoHeight))) }
            // Start where the viewer chose to resume, once per video (a retry keeps the position it reached).
            if (pendingStart.current !== null) { v.currentTime = clampTime(pendingStart.current, v.duration || durationHint || 0); pendingStart.current = null; startApplied.current = true }
            else if (startAt > 0 && !startApplied.current) { startApplied.current = true; v.currentTime = clampTime(startAt, v.duration || durationHint || 0) }
          }}
          onTimeUpdate={(e) => {
            setTime(e.currentTarget.currentTime)
            onTime?.(e.currentTarget.currentTime, e.currentTarget.duration || durationHint || 0)
            const b = e.currentTarget.buffered
            setBuffered(b.length ? b.end(b.length - 1) : 0)
            setSample(sampleOf(e.currentTarget))
          }}
          onEnded={() => { onEnd?.(); if (autoNext) onNext?.() }}
          onError={() => { setFailed(true); setLoading(false) }}
          style={{ filter: pictureFilter(ps), transform: ps.zoom === 'off' ? undefined : `scale(${zoomScale(ps.zoom, ratio, isFs ? window.innerWidth / Math.max(1, window.innerHeight) : ratio)})` }}
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
              <span className="sm:hidden">This video could not be played here.</span><span className="hidden sm:inline">This video could not be played from the original stream (this player cannot decode this file, or Telegram is not connected).</span>
            </p>
            <p className="hidden max-w-lg text-xs text-zinc-400 sm:block">{onOpenNative ? 'This player cannot decode every file (some HEVC, 10-bit and MKV videos). The native player can.' : 'You can retry the original stream or open it in VLC.'}</p>
            <div className="flex flex-wrap justify-center gap-2">
              {onOpenNative ? <button type="button" onClick={onOpenNative} className="h-9 rounded-xl bg-accent px-3 text-sm font-bold text-accent-fg sm:h-10 sm:px-4">Play with the native player</button> : null}
              <button type="button" onClick={() => setAttempt(attempt + 1)} className={onOpenNative ? 'h-9 rounded-xl border border-white/20 bg-white/10 px-3 text-sm font-bold sm:h-10 sm:px-4' : 'h-9 rounded-xl bg-accent px-3 text-sm font-bold text-accent-fg sm:h-10 sm:px-4'}>Retry original</button>
              {onOpenVlc ? <button type="button" onClick={onOpenVlc} className="inline-flex h-9 items-center gap-2 rounded-xl border border-white/20 bg-white/10 px-3 text-sm font-bold sm:h-10 sm:px-4"><Icon name="brand-vlc" />Open in VLC</button> : null}
            </div>
          </div>
        ) : null}
      </div>
      <div data-ov={overlay ? '' : undefined} onPointerEnter={() => { overBar.current = true; setIdle(false) }} onPointerLeave={() => { overBar.current = false }}
        className={cn('@container grid gap-1.5', overlay && 'absolute inset-x-0 bottom-0 z-30 bg-gradient-to-t from-black/85 via-black/50 to-transparent px-3 pb-2 pt-12 text-white [--color-fg:#fff] [--color-muted:#c9d1e0] transition-opacity duration-300 sm:px-4', isFs && 'px-4 pb-3 sm:px-8', !overlay && 'gap-3', overlay && !isFs && 'rounded-b-[18px] sm:rounded-b-[22px]', hideBar && 'pointer-events-none opacity-0')}>
        <PlayerControls
          time={time} duration={dur} buffered={buffered} segments={segments} preview={preview} onSeek={seek}
          playing={playing} onToggle={toggle} onPrev={onPrev} onNext={onNext}
          rate={rate} onRate={(r) => { setRate(r); flash(`Speed: ${r}x`) }}
          muted={muted} volume={volume} onMute={() => setMuted(!muted)} onVolume={(v) => { setVolume(v); setMuted(false) }}
          status={{ chip: buffer >= 99 ? 'Cached' : `${buffer}%`, good: buffer >= 99, rows: statRows(buffer, sample, { ...facts, audio: tracks.info?.audio.find((a) => tracks.audio === null ? a.default || a.ordinal === 0 : a.index === tracks.audio)?.label }) }}
          pip={{ onClick: pip, label: 'Picture in picture', title: 'Picture in picture' }}
          tracksMenu={tracks.info && (tracks.info.audio.length > 1 || tracks.info.subtitles.length > 0 || tracks.info.captions.available) ? <CcMenu info={tracks.info} audio={tracks.audio} sub={tracks.sub} live={tracks.live} altBusy={tracks.altBusy} onAudio={tracks.pickAudio} onSub={tracks.pickSub} /> : null}
          onFullscreen={full}
          settings={<>
            <MenuItem pressed={block} onClick={() => { const on = !block; setBlock(on); setBlocking(on); flash(on ? 'Chrome Live Caption blocked after reload' : 'Chrome Live Caption allowed after reload') }} label="Keep Chrome's Live Caption off this player">Keep Chrome's Live Caption off <span className={block ? 'text-emerald-400' : 'text-muted'}>{block ? 'ON' : 'OFF'}</span></MenuItem>
            <p className="max-w-[15rem] px-2.5 pb-1 text-[11px] leading-snug text-muted">Plays the sound through the app so Chrome's own black "Live Caption" box has nothing to caption. Applies after a page reload.</p>
            <MenuSection title="Picture and sound"><PictureSoundItems value={ps} onChange={changePs} boostFailed={boostFailed} sync={{ seconds: shift, on: tracks.sub.kind === 'track' || tracks.sub.kind === 'addon', onChange: setShift }} /></MenuSection>
            <MenuSection title="Caption style" defaultOpen><CaptionStyleItems head={false} style={capStyle} onChange={changeCap} /></MenuSection>
            <PlaybackItems tracks={tracks} loop={loop} onLoop={() => { setLoop(!loop); flash(`Auto-repeat: ${loop ? 'OFF' : 'ON'}`) }} />
          </>}
        />
      </div>
    </div>
  )
}
