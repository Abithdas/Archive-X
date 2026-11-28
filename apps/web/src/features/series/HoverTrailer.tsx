import { useEffect, useRef, useState } from 'react'

/**
 * The trailer inside an expanded card (like dx.bi): YouTube's embedded player, muted, hidden behind the artwork until it really plays, then faded in
 * with a speaker button and a full screen button on the picture. Only SFW cards ask for one (RowPreview), and only when the setting is on.
 */
const VALID = /^[\w-]{6,16}$/
const REVEAL_AFTER_MS = 2800   // YouTube shows its own controls for the first moments of playback: the trailer is shown only after they have faded

let muted = true   // the choice is kept from card to card; every trailer still starts muted

const post = (frame: HTMLIFrameElement | null, func: string, args: unknown[] = []) => {
  try { frame?.contentWindow?.postMessage(JSON.stringify({ event: 'command', func, args }), '*') } catch { /* the frame is gone */ }
}

const embed = (id: string, extra: string) => `https://www.youtube-nocookie.com/embed/${encodeURIComponent(id)}?${extra}`

/** The same trailer, full screen and with sound, from where the card had got to. Its own little layer on the page, because the card is gone the moment this opens. */
export function openTheatre(id: string, start: number, title: string): void {
  if (!VALID.test(id) || typeof document === 'undefined') return
  const el = document.createElement('div')
  el.setAttribute('role', 'dialog'); el.setAttribute('aria-label', `${title} trailer`); el.dataset.trailerTheatre = ''
  el.style.cssText = 'position:fixed;inset:0;z-index:2000;background:#000;opacity:0;transition:opacity .25s ease'
  const frame = document.createElement('iframe')
  frame.title = `${title} trailer`; frame.allow = 'autoplay; encrypted-media; fullscreen'
  frame.src = embed(id, `autoplay=1&mute=0&controls=1&rel=0&modestbranding=1&playsinline=1&start=${Math.max(0, Math.floor(start))}`)
  frame.style.cssText = 'position:absolute;inset:0;width:100%;height:100%;border:0'
  const close = document.createElement('button')
  close.type = 'button'; close.setAttribute('aria-label', 'Close trailer'); close.textContent = '✕'
  close.style.cssText = 'position:absolute;top:16px;right:16px;width:40px;height:40px;border-radius:50%;border:1px solid rgba(255,255,255,.5);background:rgba(0,0,0,.55);color:#fff;font-size:18px;cursor:pointer'
  el.append(frame, close)
  const done = () => {
    window.removeEventListener('keydown', onKey, true)
    document.removeEventListener('fullscreenchange', onFs)
    if (document.fullscreenElement === el) void document.exitFullscreen().catch(() => undefined)
    el.style.opacity = '0'; setTimeout(() => el.remove(), 260)
  }
  const onKey = (e: KeyboardEvent) => { if (e.key === 'Escape') { e.stopImmediatePropagation(); done() } }
  const onFs = () => { if (!document.fullscreenElement) done() }
  close.addEventListener('click', done)
  window.addEventListener('keydown', onKey, true)
  document.body.append(el)
  requestAnimationFrame(() => { el.style.opacity = '1' })
  void Promise.resolve(el.requestFullscreen?.()).then(() => document.addEventListener('fullscreenchange', onFs)).catch(() => undefined)
}

const SPEAKER = <svg viewBox="0 0 24 24" aria-hidden="true" className="size-4" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round"><path d="M11 5 6 9H3v6h3l5 4z" /><path d="M15.5 8.5a5 5 0 0 1 0 7M18.5 5.5a9 9 0 0 1 0 13" /></svg>
const SPEAKER_OFF = <svg viewBox="0 0 24 24" aria-hidden="true" className="size-4" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round"><path d="M11 5 6 9H3v6h3l5 4z" /><path d="m16 9 5 6M21 9l-5 6" /></svg>
const EXPAND = <svg viewBox="0 0 24 24" aria-hidden="true" className="size-4" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round"><path d="M14 4h6v6M10 20H4v-6M20 4l-7 7M4 20l7-7" /></svg>
const BTN = 'absolute bottom-2.5 z-10 grid size-8 place-items-center rounded-full border border-white/50 bg-black/45 text-white transition-opacity duration-300 hover:border-white hover:bg-black/70'

/** Fills the picture area of the card (it is `position: relative`). `onPlaying` tells the card when the trailer is on show, so its title can step aside. */
export function HoverTrailer({ id, title, onPlaying, onExpand }: { id: string; title: string; onPlaying: (on: boolean) => void; onExpand?: () => void }) {
  const frame = useRef<HTMLIFrameElement>(null)
  const time = useRef(0)
  const [shown, setShown] = useState(false)
  const [off, setOff] = useState(muted)
  const [failed, setFailed] = useState(false)
  const playing = useRef(onPlaying)
  playing.current = onPlaying

  useEffect(() => {
    let reveal: ReturnType<typeof setTimeout> | undefined
    let started = false
    const onMessage = (e: MessageEvent) => {
      if (!frame.current || e.source !== frame.current.contentWindow) return
      let d: { event?: string; info?: unknown } | null = null
      try { d = typeof e.data === 'string' ? JSON.parse(e.data) : e.data } catch { return }
      if (d?.event === 'onError') { setFailed(true); return }
      const info = d?.info
      if (info && typeof info === 'object' && typeof (info as { currentTime?: number }).currentTime === 'number') time.current = (info as { currentTime: number }).currentTime
      const state = info && typeof info === 'object' ? (info as { playerState?: number }).playerState : info
      if (state === 1 && !started) {
        started = true
        reveal = setTimeout(() => { post(frame.current, 'seekTo', [0, true]); time.current = 0; setShown(true); playing.current(true); if (!muted) post(frame.current, 'unMute') }, REVEAL_AFTER_MS)
      }
    }
    window.addEventListener('message', onMessage)
    return () => { window.removeEventListener('message', onMessage); clearTimeout(reveal); playing.current(false) }
  }, [])

  if (!VALID.test(id) || failed) return null
  const toggle = (e: React.MouseEvent) => {
    e.preventDefault(); e.stopPropagation()
    muted = !muted; setOff(muted); post(frame.current, muted ? 'mute' : 'unMute')
  }
  const expand = (e: React.MouseEvent) => { e.preventDefault(); e.stopPropagation(); openTheatre(id, time.current, title); onExpand?.() }   // the card closes (and its own trailer with it) as the full screen one opens
  return (
    <>
      <span aria-hidden="true" data-hover-trailer className={`pointer-events-none absolute inset-0 overflow-hidden transition-opacity duration-[600ms] ${shown ? 'opacity-100' : 'opacity-0'}`}>
        <iframe ref={frame} title={`${title} trailer`} tabIndex={-1} allow="autoplay; encrypted-media" className="absolute left-1/2 top-1/2 h-[140%] w-[140%] -translate-x-1/2 -translate-y-1/2 border-0"
          src={embed(id, 'autoplay=1&mute=1&controls=0&disablekb=1&fs=0&iv_load_policy=3&modestbranding=1&playsinline=1&rel=0&loop=1&enablejsapi=1&playlist=' + encodeURIComponent(id))}
          onLoad={() => { try { frame.current?.contentWindow?.postMessage(JSON.stringify({ event: 'listening', id: 1, channel: 'widget' }), '*') } catch { /* gone */ } }} />
      </span>
      <button type="button" onClick={expand} aria-label="Watch trailer full screen" title="Full screen" className={`${BTN} right-12 ${shown ? 'opacity-100' : 'pointer-events-none opacity-0'}`}>{EXPAND}</button>
      <button type="button" onClick={toggle} aria-label={off ? 'Unmute trailer' : 'Mute trailer'} aria-pressed={!off} title={off ? 'Unmute' : 'Mute'} className={`${BTN} right-2.5 ${shown ? 'opacity-100' : 'pointer-events-none opacity-0'}`}>{off ? SPEAKER_OFF : SPEAKER}</button>
    </>
  )
}
