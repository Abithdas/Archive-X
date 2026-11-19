import { useCallback, useEffect, useRef, useState, type CSSProperties } from 'react'
import { api } from '@/api/client'
import { cn } from '@/design/cn'
import { CcMenu } from './CcMenu'
import { native, pixelRect, type NativeCommand, type NativeTrack, type Opened, type Snapshot } from './nativeBridge'
import { afterJump, aspectOf, quiet, stallStep, videoVisible, type StallState } from './nativeLogic'
import { releasePlayback } from './release'
import { PlayIcon } from './PlayerIcons'
import { CaptionStyleItems, PlaybackItems, PlayerControls, playerKey } from './PlayerControls'
import { loadStyle, saveStyle } from './captionStyle'
import { SnapshotGrid, type SnapshotData } from './SnapshotGrid'
import { MenuHead, MenuItem } from './PlayerParts'
import type { PreviewInfo } from './Timeline'
import type { Segment } from './timelineLogic'
import type { AudioTrack, TrackInfo } from './tracks'
import { useTracks } from './useTracks'

const noop = () => undefined

type Phase = 'checking' | 'unavailable' | 'opening' | 'ready' | 'failed'

/** True while a dialog or a menu of the page is open: it would be hidden under the video window, so the video steps aside. */
function covered(): boolean {
  try { return Boolean(document.querySelector('dialog[open], [popover]:popover-open')) } catch { return Boolean(document.querySelector('dialog[open]')) }
}

/**
 * The desktop app's native engine (LAV decoders, hardware decoding) inside the watch page, in place of the browser's video: the same page, timeline,
 * markers, preview, resume and queue. The picture is a Windows window above the app, so nothing can be drawn over it: the controls are below it, and the
 * captions (live or from the file) are in a strip under it. The picture steps aside while a dialog or menu is open or the stage is scrolled partly out
 * of view. After a jump, if the stream is too slow and the picture crawls, playback stops for a moment to refill, as a browser would.
 */
export function NativePane({ media, durationHint, size, segments = [], preview, snapshots, startAt = 0, autoPlay = true, autoNext = false, seekRequest, onTime, onPlayed, onEnd, onPrev, onNext, onBrowser }: {
  media: { cid: string; mid: number }; durationHint?: number | null; size?: { width?: number | null; height?: number | null }; segments?: Segment[]; preview?: PreviewInfo; snapshots?: SnapshotData
  startAt?: number; autoPlay?: boolean; autoNext?: boolean; seekRequest?: { t: number; n: number }
  onTime?: (t: number, duration: number) => void; onPlayed?: () => void; onEnd?: () => void; onPrev?: () => void; onNext?: () => void; onBrowser?: () => void
}) {
  const stage = useRef<HTMLDivElement>(null)
  const [phase, setPhase] = useState<Phase>('checking')
  const [message, setMessage] = useState('')
  const [opened, setOpened] = useState<Opened | null>(null)
  const [snap, setSnap] = useState<Snapshot | null>(null)
  const [nativeTracks, setNativeTracks] = useState<NativeTrack[]>([])
  const [audioPick, setAudioPick] = useState(0)
  const [volume, setVolume] = useState(1)
  const [muted, setMuted] = useState(false)
  const [rate, setRate] = useState(1)
  const [holding, setHolding] = useState(false)
  const [jump, setJump] = useState<{ to: number; at: number } | null>(null)
  const [shown, setShown] = useState(false)
  // As in the browser player: the snapshot frames are the picture until the video has played; then the video is shown.
  const [started, setStarted] = useState(false)
  const startedRef = useRef(false)
  const [mini, setMini] = useState(false)
  const [loop, setLoop] = useState(false)
  const loopRef = useRef(false)
  const [hint, setHint] = useState('')
  const hintTimer = useRef<ReturnType<typeof setTimeout>>(undefined)
  const [capStyle, setCapStyle] = useState(loadStyle)
  const [isFs, setIsFs] = useState(false)
  const box = useRef<HTMLDivElement>(null)
  const miniRef = useRef(false)
  const [scrolling, setScrolling] = useState(false)
  const scrollingRef = useRef(false)
  const shownRef = useRef(false)
  const stall = useRef<StallState>(quiet())
  const weHeld = useRef(false)
  const rateRef = useRef(1)
  const startRef = useRef(startAt)
  const playRef = useRef(autoPlay)
  const playedSent = useRef(false)
  const endSent = useRef(false)
  const noVideo = useRef<HTMLVideoElement | null>(null)

  const position = snap?.position ?? 0
  const playing = Boolean(snap?.playing)
  const duration = snap?.duration || opened?.duration || durationHint || 0
  const tracks = useTracks(media, noVideo, position, playing)
  const send = useCallback((command: NativeCommand, value?: number) => { void native.command(command, value).catch((e: unknown) => setMessage(e instanceof Error ? e.message : '')) }, [])
  const shapeRef = useRef(16 / 9)
  /** Where the video goes: on the stage, or (mini player) in the lower right corner of the window, above everything. */
  const rect = useCallback(() => {
    if (miniRef.current) {
      const ratio = window.devicePixelRatio || 1
      const w = Math.min(420, window.innerWidth * 0.45)
      const h = w / shapeRef.current
      return { x: Math.round((window.innerWidth - w - 16) * ratio), y: Math.round((window.innerHeight - h - 16) * ratio), w: Math.round(w * ratio), h: Math.round(h * ratio) }
    }
    if (stage.current && document.fullscreenElement === box.current) {
      // Full screen: the stage is the whole screen above the bar; the picture keeps its own shape inside it, centred.
      const r = stage.current.getBoundingClientRect(); const ratio = window.devicePixelRatio || 1
      const w = Math.min(r.width, r.height * shapeRef.current); const h = w / shapeRef.current
      return { x: Math.round((r.left + (r.width - w) / 2) * ratio), y: Math.round((r.top + (r.height - h) / 2) * ratio), w: Math.round(w * ratio), h: Math.round(h * ratio) }
    }
    return stage.current ? pixelRect(stage.current) : { x: 0, y: 0, w: 0, h: 0 }
  }, [])

  // ---- open the video, once per video
  useEffect(() => {
    let dead = false
    ;(async () => {
      const avail = await native.available()
      if (dead) return
      if (!avail.available) { setPhase('unavailable'); setMessage(avail.reason); return }
      setPhase('opening')
      const link = await api<{ url: string }>(`/api/play/device-link/${encodeURIComponent(media.cid)}/${media.mid}`)
      if (dead) return
      const o = await native.open(link.url, rect())
      if (dead) { void native.command('close').catch(noop); return }
      setOpened(o)
      setPhase('ready')
      // Only the window above the app shows the picture on every PC tried so far, and it is the one the pane lays out for.
      if (o.layer !== 'popup') await native.command('popup').catch(noop)
      await native.command('hide').catch(noop)   // the frames are shown first; the picture comes when playback starts (see sync)
      if (startRef.current > 1) await native.command('seek', startRef.current).catch(noop)
      if (playRef.current) await native.command('play')
      setNativeTracks(await native.tracks().catch(() => []))
    })().catch((e: unknown) => { if (!dead) { setPhase('failed'); setMessage(e instanceof Error ? e.message : 'The native player failed') } })
    return () => { dead = true; void native.command('close').catch(noop); releasePlayback(media.cid, media.mid) }   // the server drops this video's session and temporary downloads
  }, [media.cid, media.mid, rect])

  // ---- keep the video window on the stage; step aside when it cannot be shown whole or something is open over it
  const sync = useCallback(() => {
    const el = stage.current
    if (!el) return
    const top = document.fullscreenElement === box.current ? 0 : document.querySelector('header')?.getBoundingClientRect().bottom ?? 0   // in full screen the page's bar is not on screen
    // While the page is scrolling the picture steps aside and comes back where it settles: moving a separate window in step with the page always
    // lags a little, and the lag showed as a black box left over the page.
    const visible = startedRef.current && !scrollingRef.current && (miniRef.current ? !covered() : videoVisible(el.getBoundingClientRect(), { top, bottom: window.innerHeight, left: 0, right: window.innerWidth }, covered()))
    if (visible) {
      void native.rect(rect()).catch(noop)
      if (!shownRef.current) { shownRef.current = true; setShown(true); void native.command('popup').catch(noop) }
    } else if (shownRef.current) {
      shownRef.current = false; setShown(false); void native.command('hide').catch(noop)
    }
  }, [rect])
  useEffect(() => {
    if (phase !== 'ready' || !stage.current) return
    let raf = 0
    const soon = () => { if (!raf) raf = requestAnimationFrame(() => { raf = 0; sync() }) }
    const ro = new ResizeObserver(soon)
    ro.observe(stage.current)
    const mo = new MutationObserver(soon)
    mo.observe(document.body, { subtree: true, childList: true, attributes: true, attributeFilter: ['open'] })
    window.addEventListener('resize', soon)
    let settle: ReturnType<typeof setTimeout> | undefined
    const onScroll = () => {
      if (miniRef.current) return
      if (!scrollingRef.current) { scrollingRef.current = true; setScrolling(true) }
      if (settle) clearTimeout(settle)
      settle = setTimeout(() => { scrollingRef.current = false; setScrolling(false); soon() }, 160)
      soon()
    }
    window.addEventListener('scroll', onScroll, true)
    document.addEventListener('toggle', soon, true)
    soon()
    return () => { ro.disconnect(); mo.disconnect(); window.removeEventListener('resize', soon); window.removeEventListener('scroll', onScroll, true); if (settle) clearTimeout(settle); document.removeEventListener('toggle', soon, true); if (raf) cancelAnimationFrame(raf) }
  }, [phase, sync])

  // ---- what the engine is doing, four times a second; also the slow-motion watch and the callbacks the page listens to
  useEffect(() => {
    if (phase !== 'ready') return
    let busy = false
    const t = setInterval(() => {
      if (busy) return
      busy = true
      native.state().then((s) => {
        setSnap(s)
        if (s.playing && !startedRef.current) { startedRef.current = true; setStarted(true) }
        const r = stallStep(stall.current, Date.now(), s.position, { playing: s.playing, rate: rateRef.current })
        stall.current = r.state
        if (r.action === 'pause') { weHeld.current = true; setHolding(true); send('pause') }
        else if (r.action === 'resume') { weHeld.current = false; setHolding(false); send('play') }
        onTime?.(s.position, s.duration || 0)
        if (s.playing && !playedSent.current) { playedSent.current = true; onPlayed?.() }
        if (s.ended && !endSent.current && loopRef.current) { endSent.current = true; stall.current = afterJump(Date.now()); send('seek', 0); send('play'); window.setTimeout(() => { endSent.current = false }, 1500) }
        else if (s.ended && !endSent.current) { endSent.current = true; onEnd?.(); if (autoNext) onNext?.() } else if (!s.ended) endSent.current = false
      }).catch(noop).finally(() => { busy = false })
    }, 250)
    return () => clearInterval(t)
  }, [phase, send, onTime, onPlayed, onEnd, onNext, autoNext])
  useEffect(() => { if (started) sync() }, [started, sync])
  useEffect(() => {
    if (!jump || !snap) return
    if (Math.abs(snap.position - jump.to) <= 2.5 || Date.now() - jump.at > 15_000) setJump(null)
  }, [snap, jump])

  const seekTo = useCallback((t: number) => {
    const to = Math.max(0, duration > 0 ? Math.min(duration, t) : t)
    stall.current = afterJump(Date.now()); weHeld.current = false; setHolding(false)
    setJump({ to, at: Date.now() })
    send('seek', to)
  }, [duration, send])
  useEffect(() => { if (seekRequest && phase === 'ready') seekTo(seekRequest.t) }, [seekRequest, phase]) // eslint-disable-line react-hooks/exhaustive-deps

  const toggle = () => {
    if (weHeld.current) { weHeld.current = false; setHolding(false); stall.current = { ...stall.current, holdUntil: null } }   // the person decides, not the refill
    send(playing || holding ? 'pause' : 'play')
  }
  const pickRate = (r: number) => { setRate(r); rateRef.current = r; send('rate', r) }
  const setVol = (v: number) => { setVolume(v); setMuted(false); send('volume', Math.round(v * 100)); send('mute', 0) }
  const flipMute = () => { send('mute', muted ? 0 : 1); setMuted(!muted) }
  const flash = (text: string) => { setHint(text); clearTimeout(hintTimer.current); hintTimer.current = setTimeout(() => setHint(''), 900) }
  useEffect(() => () => clearTimeout(hintTimer.current), [])
  const full = () => (document.fullscreenElement ? void document.exitFullscreen() : void box.current?.requestFullscreen?.())
  useEffect(() => {
    const change = () => { setIsFs(document.fullscreenElement === box.current); window.setTimeout(() => sync(), 60) }
    document.addEventListener('fullscreenchange', change)
    return () => document.removeEventListener('fullscreenchange', change)
  }, [sync])
  // The same keys as the browser player, on the whole window (the picture window above the page keeps the keyboard focus otherwise).
  const keyRef = useRef<(e: KeyboardEvent) => void>(noop)
  keyRef.current = (e) => { if (phase === 'ready') playerKey(e, { toggle, seekBy: (d) => seekTo(position + d), mute: flipMute, full, next: onNext, prev: onPrev }) }
  useEffect(() => {
    const on = (e: KeyboardEvent) => keyRef.current(e)
    window.addEventListener('keydown', on)
    return () => window.removeEventListener('keydown', on)
  }, [])

  // The engine's audio tracks, named by the file's own list when the server has it (the engine counts audio streams in the same order).
  const audioList: AudioTrack[] = nativeTracks.map((t, i) => ({ index: i, ordinal: i, label: t.name || `Track ${i + 1}`, language: '', default: i === 0 }))
  const info: TrackInfo | null = tracks.info ? { ...tracks.info, audio: tracks.info.audio.length ? tracks.info.audio : audioList } : audioList.length > 1 ? { audio: audioList, subtitles: [], captions: { available: false, engine: '', install: '' } } : null
  const pickAudio = (t: AudioTrack | null) => { const n = t ? t.ordinal : 0; setAudioPick(n); send('audio', n) }
  const aspect = aspectOf(opened, size ?? {})
  const shape = Math.min(2.4, Math.max(0.5, aspect))   // the browser player limits the proportions the same way
  shapeRef.current = shape
  const flipMini = () => { const on = !miniRef.current; miniRef.current = on; setMini(on); void native.rect(rect()).catch(noop); sync() }
  const status = phase === 'checking' ? 'Checking the native player…' : phase === 'opening' ? 'Opening the video…' : holding ? 'Buffering after the jump…' : snap?.buffering ? 'Buffering…' : jump ? 'Jumping…' : ''
  const broken = phase === 'unavailable' || phase === 'failed'

  const capShade = capStyle.bg === 'solid' ? 'bg-black text-white' : capStyle.bg === 'soft' ? 'bg-black/55 text-white' : 'bg-surface-2 text-fg'

  return (
    // One page for both engines (see PlayerControls.tsx): the same frame, the same bar, menus and keys. The one difference is that the controls cannot sit
    // on the picture, because the native picture is a window above the page; in full screen the bar stays under the picture for the same reason.
    <div ref={box} className={cn('@container relative grid outline-none', isFs ? 'h-full w-full grid-rows-[minmax(0,1fr)_auto] gap-2 bg-black p-2' : 'w-full gap-4 sm:w-[var(--pw)] sm:gap-5')} data-testid="native-pane" tabIndex={-1}
      style={isFs ? undefined : ({ '--pw': `min(100%, max(330px, calc((100dvh - 290px) / (var(--ax-page-zoom, 1) * var(--ax-ui-zoom, 1)) * ${shape.toFixed(4)})))` } as CSSProperties)}>
      <div ref={stage} data-testid="stage" style={isFs ? undefined : { aspectRatio: shape }} className={cn('relative w-full overflow-hidden bg-[#111215]', isFs ? 'h-full min-h-0' : 'rounded-[18px] shadow-2xl ring-1 ring-white/10 sm:rounded-[22px]')}>
        {phase === 'ready' && mini ? <div role="status" className="absolute inset-0 grid place-items-center content-center gap-2 p-4 text-center text-sm text-white/70"><p>Playing in the mini player.</p><button type="button" onClick={flipMini} className="h-9 rounded-xl bg-accent px-4 text-sm font-bold text-accent-fg">Back to the page</button></div> : null}
        {phase === 'ready' && started && !shown && !scrolling && !mini ? <p role="status" className="absolute inset-0 grid place-items-center p-4 text-center text-sm text-white/70">The picture is hidden while a menu is open or it is scrolled partly out of view. The sound keeps playing.</p> : null}
        {status ? <span role="status" className="pointer-events-none absolute left-3 top-3 rounded-full bg-black/60 px-3 py-1 text-xs font-medium text-white">{status}</span> : null}
        {snapshots && !started && !broken ? <SnapshotGrid data={snapshots} onPick={(t) => { seekTo(t); send('play') }} /> : null}
        {phase === 'ready' && !started && !broken ? (
          <button type="button" onClick={toggle} aria-label="Play" className="absolute left-1/2 top-1/2 z-20 grid size-12 -translate-x-1/2 -translate-y-1/2 place-items-center rounded-full border border-white/15 bg-[#1e1f23]/80 text-white shadow-xl backdrop-blur-md transition-[scale] duration-200 hover:scale-105 active:scale-95 sm:size-14">
            <span className="ml-0.5"><PlayIcon /></span>
          </button>
        ) : null}
        {broken ? (
          <div role="alert" className="absolute inset-0 grid content-center justify-items-center gap-3 bg-black/85 p-5 text-center text-white">
            <p className="max-w-lg text-sm font-bold">{phase === 'failed' ? `The native player could not play this video: ${message}` : message || 'The native player is not available.'}</p>
            {onBrowser ? <button type="button" onClick={onBrowser} className="h-10 rounded-xl bg-accent px-4 text-sm font-bold text-accent-fg">Use the browser player</button> : null}
          </div>
        ) : null}
      </div>

      {tracks.sub.kind !== 'off' ? (
        <div data-testid="captions" aria-live="polite" className={cn('grid min-h-[3.4rem] place-items-center rounded-[14px] px-4 py-2 text-center', capShade)}>
          {tracks.text ? <p className="whitespace-pre-line font-semibold leading-snug" style={{ fontSize: `${17 * capStyle.size}px` }}>{tracks.text}</p> : <p className="text-xs text-muted">{tracks.liveStatus || 'Captions appear here'}</p>}
        </div>
      ) : null}

      <div className="@container relative grid gap-3">
        {hint ? <div role="status" className="pointer-events-none absolute -top-9 left-1/2 z-30 -translate-x-1/2 rounded-full border border-white/15 bg-[#141417]/90 px-4 py-1.5 text-xs font-medium text-white shadow-lg backdrop-blur-md">{hint}</div> : null}
        <PlayerControls
          time={jump?.to ?? position} duration={duration} buffered={0} segments={segments} preview={preview} onSeek={seekTo} tipBelow
          playing={playing || holding} onToggle={toggle} toggleDisabled={phase !== 'ready'} disabled={phase !== 'ready'} onPrev={onPrev} onNext={onNext}
          rate={rate} onRate={(r) => { pickRate(r); flash(`Speed: ${r}x`) }}
          muted={muted} volume={volume} onMute={flipMute} onVolume={setVol}
          status={{ chip: 'Native', good: true, rows: [{ k: 'Playback', v: opened?.decoder ? `Native · ${opened.decoder}` : 'Native player' }] }}
          pip={{ onClick: flipMini, label: 'Mini player', title: 'Mini player: keeps playing in the corner while you scroll the page', pressed: mini, disabled: phase !== 'ready' }}
          tracksMenu={info ? <CcMenu drop="down" info={info} audio={info.audio.find((t) => t.ordinal === audioPick)?.index ?? null} sub={tracks.sub} live={tracks.live} altBusy={false} onAudio={pickAudio} onSub={tracks.pickSub} /> : null}
          onFullscreen={full} drop="down"
          settings={<>
            <CaptionStyleItems style={capStyle} onChange={(patch) => setCapStyle((c) => { const n = { ...c, ...patch }; saveStyle(n); return n })} />
            <PlaybackItems tracks={tracks} loop={loop} onLoop={() => { const on = !loop; loopRef.current = on; setLoop(on); flash(`Auto-repeat: ${on ? 'ON' : 'OFF'}`) }} />
            <MenuHead>Player</MenuHead>
            {onBrowser ? <MenuItem onClick={(close) => { close(); onBrowser() }} label="Play this video with the browser's player instead">Use the browser player</MenuItem> : null}
          </>}
        />
      </div>
    </div>
  )
}
