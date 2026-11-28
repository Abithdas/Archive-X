import { useCallback, useEffect, useRef, useState, type CSSProperties } from 'react'
import { api } from '@/api/client'
import { cn } from '@/design/cn'
import { Icon } from '@/design/Icon'
import { CcMenu } from './CcMenu'
import { deviceScale, native, pixelRect, type NativeCommand, type NativeTrack, type Opened, type Snapshot , type VideoLayer } from './nativeBridge'
import { afterJump, aspectOf, decodingLabel, hwCode, quiet, stallStep, videoVisible, type StallState } from './nativeLogic'
import { releasePlayback } from './release'
import { enterFullscreen, fullscreenOn, leaveFullscreen, useFullscreen } from './fullscreen'
import { PlayIcon } from './PlayerIcons'
import { CaptionStyleItems, PlaybackItems, PlayerControls, playerKey } from './PlayerControls'
import { loadStyle, saveStyle } from './captionStyle'
import { SkipButton, skipNow, skipTarget, type Skip } from './skip'
import { SnapshotGrid, type SnapshotData } from './SnapshotGrid'
import { MenuHead, MenuItem } from './PlayerParts'
import type { PreviewInfo } from './Timeline'
import type { Segment } from './timelineLogic'
import type { AudioTrack, TrackInfo } from './tracks'
import { useTracks, type AddonRef } from './useTracks'
import { loadPrefs as loadPlayerPrefs, savePrefs as savePlayerPrefs } from './playerPrefs'

const noop = () => undefined
/** Set once the see-through arrangement was seen not to work on this PC (a flat screen over a playing video): the window above the page is used for the rest of the session. */
let underFailed = false
const FULL_BOX: CSSProperties = { position: 'fixed', inset: 0, width: '100%', height: '100%', maxWidth: 'none', maxHeight: 'none', margin: 0, padding: 0, border: 0, overflow: 'hidden' }

type Phase = 'checking' | 'unavailable' | 'opening' | 'ready' | 'failed'

/** True while a dialog or a menu of the page is open: it would be hidden under the video window, so the video steps aside. */
function covered(own?: Element | null): boolean {
  // a dialog, a popover, or any open menu (the player's own drop-downs open upwards over the picture, so the picture must give way to them);
  // the pane's own top-layer box (page-level full screen) is not something laid over the picture
  try {
    if (document.querySelector('dialog[open], [role="menu"]')) return true
    return [...document.querySelectorAll('[popover]:popover-open')].some((el) => !(own && el.contains(own)))
  } catch { return Boolean(document.querySelector('dialog[open], [role="menu"]')) }
}

/**
 * The desktop app's native engine (LAV decoders, hardware decoding) inside the watch page, in place of the browser's video: the same page, timeline,
 * markers, preview, resume and queue. The picture is a Windows window above the app, so nothing can be drawn over it: the controls are below it, and the
 * captions (live or from the file) are in a strip under it. The picture steps aside while a dialog or menu is open or the stage is scrolled partly out
 * of view. After a jump, if the stream is too slow and the picture crawls, playback stops for a moment to refill, as a browser would.
 */
export function NativePane({ media, addon, linkPath, onClose, durationHint, size, segments = [], preview, snapshots, startAt = 0, autoPlay = true, autoNext = false, seekRequest, onTime, onPlayed, onEnd, onPrev, onNext, onBrowser, skips = [], decoding }: { skips?: Skip[]; decoding?: string;
  /** A library video (`media`), or an add-on stream: `linkPath` answers `{ url }` with the address the engine opens, `addon` names the title for its subtitle files. */
  media?: { cid: string; mid: number }; addon?: AddonRef; linkPath?: string; onClose?: () => void; durationHint?: number | null; size?: { width?: number | null; height?: number | null }; segments?: Segment[]; preview?: PreviewInfo; snapshots?: SnapshotData
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
  const [saved] = useState(loadPlayerPrefs)   // volume, mute and speed carry over from the last video
  const [volume, setVolume] = useState(saved.volume)
  const [muted, setMuted] = useState(saved.muted)
  const [rate, setRate] = useState(saved.rate)
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
  // Full screen of the whole page (Play in SFW asks for it in the click, see fullscreen.ts): the pane then shows itself over the screen, in the top layer, as the browser player does
  const pageFs = useFullscreen()
  const wide = pageFs
  const inFs = () => fullscreenOn()
  // The layer the engine's video window is in. "under": the video is a window beneath the see-through app window, so in full screen the page is drawn OVER the
  // picture (controls, captions, menus, the seek preview), and nothing has to step aside. Everywhere else it is the window above the page, with the controls around it.
  const [layer, setLayer] = useState<VideoLayer>('popup')
  const layerRef = useRef<VideoLayer>('popup')
  const under = pageFs && layer === 'under'
  const [idle, setIdle] = useState(false)
  const overBar = useRef(false)
  const idleTimer = useRef<ReturnType<typeof setTimeout>>(undefined)
  const box = useRef<HTMLDivElement>(null)
  const miniRef = useRef(false)
  const [scrolling, setScrolling] = useState(false)
  const scrollingRef = useRef(false)
  const shownRef = useRef(false)
  const stall = useRef<StallState>(quiet())
  const weHeld = useRef(false)
  const rateRef = useRef(saved.rate)
  const startRef = useRef(startAt)
  const playRef = useRef(autoPlay)
  const playedSent = useRef(false)
  const endSent = useRef(false)
  const noVideo = useRef<HTMLVideoElement | null>(null)

  const position = snap?.position ?? 0
  const playing = Boolean(snap?.playing)
  const duration = snap?.duration || opened?.duration || durationHint || 0
  const tracks = useTracks(media, noVideo, position, playing, 0, addon)
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
    if (stage.current && inFs()) {
      // Full screen: the window is the whole stage (everything above the bar); the engine keeps the picture's shape inside it and the rest is black.
      const r = stage.current.getBoundingClientRect(); const ratio = deviceScale(stage.current)
      return { x: Math.round(r.left * ratio), y: Math.round(r.top * ratio), w: Math.round(r.width * ratio), h: Math.round(r.height * ratio) }
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
      const link = await api<{ url: string }>(media ? `/api/play/device-link/${encodeURIComponent(media.cid)}/${media.mid}` : String(linkPath))
      if (dead) return
      const o = await native.open(link.url, rect(), hwCode(decoding), fullscreenOn() && !underFailed ? 'under' : undefined)
      if (dead) { void native.command('close').catch(noop); return }
      setOpened(o)
      layerRef.current = o.layer; setLayer(o.layer)
      setPhase('ready')
      // What the person set last time (volume, mute, speed) is applied to this video too.
      await native.command('volume', Math.round(saved.volume * 100)).catch(noop)
      if (saved.muted) await native.command('mute', 1).catch(noop)
      if (saved.rate !== 1) await native.command('rate', saved.rate).catch(noop)
      // The window above the app is the layer for the page around the picture; in full screen the engine opened the "under" one (the page is drawn over it).
      if (o.layer !== 'popup' && o.layer !== 'under') { await native.command('popup').catch(noop); layerRef.current = 'popup'; setLayer('popup') }
      if (layerRef.current !== 'under') await native.command('hide').catch(noop)   // the frames are shown first; the picture comes when playback starts (see sync). The under window is black until then.
      if (startRef.current > 1) await native.command('seek', startRef.current).catch(noop)
      if (playRef.current) await native.command('play')
      setNativeTracks(await native.tracks().catch(() => []))
    })().catch((e: unknown) => { if (!dead) { setPhase('failed'); setMessage(e instanceof Error ? e.message : 'The native player failed') } })
    return () => { dead = true; void native.command('close').catch(noop); if (media) releasePlayback(media.cid, media.mid) }   // the server drops this video's session and temporary downloads
  }, [media?.cid, media?.mid, linkPath, rect])   // eslint-disable-line react-hooks/exhaustive-deps

  // ---- keep the video window on the stage; step aside when it cannot be shown whole or something is open over it
  const sync = useCallback(() => {
    const el = stage.current
    if (!el) return
    if (layerRef.current === 'under' && fullscreenOn()) {
      // Under the see-through page the picture needs no stepping aside: nothing is laid over it that the window would hide. It just follows the stage.
      void native.rect(rect()).catch(noop)
      if (!shownRef.current) { shownRef.current = true; setShown(true) }
      return
    }
    const top = inFs() ? 0 : document.querySelector('header')?.getBoundingClientRect().bottom ?? 0   // in full screen the page's bar is not on screen
    // While the page is scrolling the picture steps aside and comes back where it settles: moving a separate window in step with the page always
    // lags a little, and the lag showed as a black box left over the page.
    const visible = startedRef.current && !scrollingRef.current && (miniRef.current ? !covered(box.current) : videoVisible(el.getBoundingClientRect(), { top, bottom: window.innerHeight, left: 0, right: window.innerWidth }, covered(box.current) || tipRef.current))
    if (visible) {
      void native.rect(rect()).catch(noop)
      if (!shownRef.current) { shownRef.current = true; setShown(true); void native.command('popup').catch(noop); layerRef.current = 'popup' }
    } else if (shownRef.current) {
      shownRef.current = false; setShown(false); void native.command('hide').catch(noop)
    }
  }, [rect])
  // The seek preview and every menu open upwards over the picture, like the browser player's: the picture (a window above the page) steps aside while they are up.
  const tipRef = useRef(false)
  const [tipOn, setTipOn] = useState(false)
  const onTip = useCallback((on: boolean) => { if (tipRef.current === on) return; tipRef.current = on; setTipOn(on); sync() }, [sync])
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
    window.addEventListener('wheel', onScroll, { capture: true, passive: true })   // the first tick of a wheel hides the picture at once: the scroll event itself can arrive after the window has already been left behind
    document.addEventListener('toggle', soon, true)
    soon()
    return () => { ro.disconnect(); mo.disconnect(); window.removeEventListener('resize', soon); window.removeEventListener('scroll', onScroll, true); window.removeEventListener('wheel', onScroll, true); if (settle) clearTimeout(settle); document.removeEventListener('toggle', soon, true); if (raf) cancelAnimationFrame(raf) }
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
        if (s.layer !== layerRef.current) { layerRef.current = s.layer; setLayer(s.layer) }
        if (s.error && !s.playing) { setPhase('failed'); setMessage(s.message || 'the engine stopped while playing'); return }
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
  const pickRate = (r: number) => { setRate(r); rateRef.current = r; send('rate', r); savePlayerPrefs({ rate: r }) }
  const setVol = (v: number) => { setVolume(v); setMuted(false); send('volume', Math.round(v * 100)); send('mute', 0); savePlayerPrefs({ volume: v, muted: false }) }
  const flipMute = () => { send('mute', muted ? 0 : 1); setMuted(!muted); savePlayerPrefs({ muted: !muted }) }
  const flash = (text: string) => { setHint(text); clearTimeout(hintTimer.current); hintTimer.current = setTimeout(() => setHint(''), 900) }
  useEffect(() => () => clearTimeout(hintTimer.current), [])
  const full = () => (fullscreenOn() ? leaveFullscreen() : enterFullscreen())
  useEffect(() => {
    const el = box.current as (HTMLDivElement & { showPopover?: () => void; hidePopover?: () => void }) | null
    if (pageFs) { try { el?.showPopover?.() } catch { /* already shown */ } }
    const t = window.setTimeout(() => sync(), 60)                  // the picture window follows the pane into and out of full screen
    return () => { window.clearTimeout(t); try { el?.hidePopover?.() } catch { /* already hidden */ } }
  }, [pageFs, sync])
  // Into and out of full screen while a video plays: the video window changes layer (the engine opens the video again in the other kind of window, at the same second).
  const lastAsked = useRef<'popup' | 'under' | null>(null)   // the layer this pane last asked the engine for (the one it opened in, to begin with)
  useEffect(() => {
    if (phase !== 'ready' || !opened) return
    const want = pageFs && !underFailed ? 'under' : 'popup'
    if (lastAsked.current === null) lastAsked.current = layerRef.current === 'under' ? 'under' : 'popup'
    if (lastAsked.current === want) return
    lastAsked.current = want
    void native.command(want).then(() => { shownRef.current = false; window.setTimeout(() => sync(), 80) }).catch(noop)   // the layer itself is read back from the engine's state
  }, [pageFs, phase, opened, sync])
  // A safety net: if the screen where the video is stays one flat colour while the video plays, the see-through window is not showing the picture on this PC
  // (the engine measures what is really on screen): go back to the window above the page, which is known to work, and stay there.
  const flat = useRef(0)
  useEffect(() => {
    if (!under || !snap || !snap.playing || snap.position < 5) { flat.current = 0; return }
    flat.current = snap.screenDistinct === 1 && snap.screenLumaRange <= 2 ? flat.current + 1 : 0
    if (flat.current >= 16) {                                  // about four seconds
      flat.current = 0
      underFailed = true
      lastAsked.current = 'popup'
      void native.command('popup').then(() => { shownRef.current = false; window.setTimeout(() => sync(), 80) }).catch(noop)
    }
  }, [snap, under, sync])
  // The page is see-through while the video is under it: the app window, the page and everything around the pane show nothing but the pane (index.css, .ax-under).
  useEffect(() => {
    document.documentElement.classList.toggle('ax-under', under)
    return () => document.documentElement.classList.remove('ax-under')
  }, [under])
  // Over the picture the controls fade out after a moment without movement, like the browser player's full screen, and come back on any movement or key.
  const wake = useCallback(() => {
    setIdle(false)
    clearTimeout(idleTimer.current)
    idleTimer.current = setTimeout(() => { if (!overBar.current) setIdle(true) }, 2800)
  }, [])
  useEffect(() => { if (under) wake(); return () => clearTimeout(idleTimer.current) }, [under, wake])
  const hideBar = under && idle && playing
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
    <div ref={box} popover={pageFs ? 'manual' : undefined} data-ax-under-pane={under ? '' : undefined} onPointerMove={under ? wake : undefined} onTouchStart={under ? wake : undefined} onKeyDown={under ? wake : undefined}
      className={cn('@container relative outline-none', under ? 'block h-full w-full overflow-hidden bg-transparent' : cn('grid', wide ? 'h-full w-full grid-rows-[auto_minmax(0,1fr)_auto] gap-2 bg-black' : 'w-full gap-4 sm:w-[var(--pw)] sm:gap-5'), hideBar && 'cursor-none')} data-testid="native-pane" tabIndex={-1}
      style={pageFs ? FULL_BOX : ({ '--pw': `min(100%, max(330px, calc((100dvh - 290px) / (var(--ax-page-zoom, 1) * var(--ax-ui-zoom, 1)) * ${shape.toFixed(4)})))` } as CSSProperties)}>
      {wide ? (
        // A slim bar above the picture with the way back (leaving full screen is the button in the bar below, or Esc). No name of the video or file is shown in full screen.
        <div className={cn('flex items-center px-3 pt-2 text-white', under && 'absolute inset-x-0 top-0 z-30 bg-gradient-to-b from-black/70 to-transparent pb-8 transition-opacity duration-300', hideBar && 'pointer-events-none opacity-0')}>
          <button type="button" onClick={() => { if (fullscreenOn()) leaveFullscreen(); else void document.exitFullscreen().catch(noop); onClose?.() }} aria-label="Back" title="Back" className="grid size-10 place-items-center rounded-full hover:bg-white/15"><Icon name="arrow-left" className="size-5" /></button>
        </div>
      ) : null}
      <div ref={stage} data-testid="stage" style={wide ? undefined : { aspectRatio: shape }} className={under ? 'absolute inset-0 h-full w-full overflow-hidden bg-transparent' : cn('relative w-full overflow-hidden', wide ? 'h-full min-h-0 bg-black' : 'bg-[#111215] rounded-[18px] shadow-2xl ring-1 ring-white/10 sm:rounded-[22px]')}>
        {phase === 'ready' && mini ? <div role="status" className="absolute inset-0 grid place-items-center content-center gap-2 p-4 text-center text-sm text-white/70"><p>Playing in the mini player.</p><button type="button" onClick={flipMini} className="h-9 rounded-xl bg-accent px-4 text-sm font-bold text-accent-fg">Back to the page</button></div> : null}
        {phase === 'ready' && started && !shown && !scrolling && !mini && !tipOn && !under ? <p role="status" className="absolute inset-0 grid place-items-center p-4 text-center text-sm text-white/70">The picture is hidden while a menu is open or it is scrolled partly out of view. The sound keeps playing.</p> : null}
        {status ? <span role="status" className={cn('pointer-events-none absolute rounded-full bg-black/60 px-3 py-1 text-xs font-medium text-white', under ? 'left-1/2 top-4 z-30 -translate-x-1/2' : 'left-3 top-3')}>{status}</span> : null}
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
        <div data-testid="captions" aria-live="polite" className={under ? cn('pointer-events-none absolute inset-x-0 z-20 grid place-items-center px-6 text-center transition-[bottom] duration-300', hideBar ? 'bottom-8' : 'bottom-32') : cn('grid min-h-[3.4rem] place-items-center rounded-[14px] px-4 py-2 text-center', capShade)}>
          {tracks.text ? <p className={cn('whitespace-pre-line font-semibold leading-snug', under && cn('rounded-lg px-3 py-1', capShade))} style={{ fontSize: `${(under ? 22 : 17) * capStyle.size}px` }}>{tracks.text}</p> : under ? null : <p className="text-xs text-muted">{tracks.liveStatus || 'Captions appear here'}</p>}
        </div>
      ) : null}

      <div data-ov={under ? '' : undefined} onPointerEnter={() => { overBar.current = true; setIdle(false) }} onPointerLeave={() => { overBar.current = false }}
        className={cn('@container grid', under ? 'absolute inset-x-0 bottom-0 z-30 gap-1.5 bg-gradient-to-t from-black/85 via-black/50 to-transparent px-4 pb-3 pt-12 text-white transition-opacity duration-300 [--color-fg:#fff] [--color-muted:#c9d1e0] sm:px-8' : 'relative gap-3', hideBar && 'pointer-events-none opacity-0')}>
        {hint ? <div role="status" className="pointer-events-none absolute -top-9 left-1/2 z-30 -translate-x-1/2 rounded-full border border-white/15 bg-[#141417]/90 px-4 py-1.5 text-xs font-medium text-white shadow-lg backdrop-blur-md">{hint}</div> : null}
        <SkipButton skip={skipNow(skips, position, duration)} canNext={Boolean(onNext)} className="absolute -top-14 right-0"
          onPress={() => { const p = skipNow(skips, position, duration); if (p?.type === 'credits' && onNext) onNext(); else if (p) seekTo(skipTarget(p, duration)) }} />
        <PlayerControls
          time={jump?.to ?? position} duration={duration} buffered={0} segments={segments} preview={preview} onSeek={seekTo} onTip={onTip}
          playing={playing || holding} onToggle={toggle} toggleDisabled={phase !== 'ready'} disabled={phase !== 'ready'} onPrev={onPrev} onNext={onNext}
          rate={rate} onRate={(r) => { pickRate(r); flash(`Speed: ${r}x`) }}
          muted={muted} volume={volume} onMute={flipMute} onVolume={setVol}
          status={{ chip: 'Native', good: true, rows: [{ k: 'Playback', v: opened?.decoder ? `Native · ${opened.decoder}` : 'Native player' }, { k: 'Decoding', v: decodingLabel(snap?.decoder || undefined), good: Boolean(snap?.decoder) && snap?.decoder !== 'software' }] }}
          pip={{ onClick: flipMini, label: 'Mini player', title: 'Mini player: keeps playing in the corner while you scroll the page', pressed: mini, disabled: phase !== 'ready' }}
          tracksMenu={info ? <CcMenu info={info} audio={info.audio.find((t) => t.ordinal === audioPick)?.index ?? null} sub={tracks.sub} live={tracks.live} altBusy={false} onAudio={pickAudio} onSub={tracks.pickSub} /> : null}
          onFullscreen={full}
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
