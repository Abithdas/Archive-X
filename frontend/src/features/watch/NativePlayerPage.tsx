import { useRouter } from '@tanstack/react-router'
import { useCallback, useEffect, useRef, useState } from 'react'
import { createPortal } from 'react-dom'
import { api } from '@/api/client'
import { formatDuration } from '@/features/library/format'
import { native, pixelRect, type NativeTrack, type Opened, type Snapshot, type VideoLayer } from './nativeBridge'

const clock = (s: number) => (s > 0 ? formatDuration(s) : '0:00')
const SPEEDS = [0.5, 1, 1.25, 1.5, 2]
const BAR = 'flex flex-wrap items-center gap-3 bg-bg px-4 py-3 text-sm text-fg'
const BTN = 'h-9 rounded-xl border border-line-2 px-3 text-sm font-semibold hover:bg-fg/5 disabled:opacity-50'

type Phase = 'checking' | 'unavailable' | 'opening' | 'ready' | 'failed'

/**
 * The desktop app's native player (LAV decoders + Windows hardware decoding), as a full-window page: an opaque top bar and control bar, and
 * between them an empty stage. The native video window sits behind the web view and the web view is transparent over the stage, so the
 * picture shows there and the page can draw over it. The rest of the app is hidden while this page is open.
 */
export function NativePlayerPage({ cid, mid }: { cid: string; mid: number }) {
  const router = useRouter()
  const stage = useRef<HTMLDivElement>(null)
  const [phase, setPhase] = useState<Phase>('checking')
  const [message, setMessage] = useState('')
  const [opened, setOpened] = useState<Opened | null>(null)
  const [snap, setSnap] = useState<Snapshot | null>(null)
  const [tracks, setTracks] = useState<NativeTrack[]>([])
  const [volume, setVolume] = useState(100)
  const [muted, setMuted] = useState(false)
  const [rate, setRate] = useState(1)
  const [seeking, setSeeking] = useState<number | null>(null)
  const [layer, setLayer] = useState<VideoLayer>('popup')   // the video window: its own window above the app (default; the one that shows the picture), in front of the page, or behind it
  const [report, setReport] = useState('')
  const [copied, setCopied] = useState(false)
  const sentReport = useRef(false)

  // While this page is open nothing of the rest of the app may paint (it would cover the video), and the page itself is see-through.
  useEffect(() => {
    const root = document.getElementById('root')
    const html = document.documentElement.style
    const body = document.body.style
    const before = { root: root?.style.visibility ?? '', html: html.background, body: body.background }
    if (root) root.style.visibility = 'hidden'
    html.background = 'transparent'
    body.background = 'transparent'
    return () => {
      if (root) root.style.visibility = before.root
      html.background = before.html
      body.background = before.body
    }
  }, [])

  const rect = useCallback(() => (stage.current ? pixelRect(stage.current) : { x: 0, y: 0, w: 0, h: 0 }), [])

  useEffect(() => {
    let dead = false
    ;(async () => {
      const avail = await native.available()
      if (dead) return
      if (!avail.available) { setPhase('unavailable'); setMessage(avail.reason); return }
      setPhase('opening')
      const link = await api<{ url: string }>(`/api/play/device-link/${encodeURIComponent(cid)}/${mid}`)
      if (dead) return
      const o = await native.open(link.url, rect())
      if (dead) { void native.command('close').catch(() => undefined); return }
      setOpened(o)
      setLayer(o.layer)
      setPhase('ready')
      await native.command('play')
      setTracks(await native.tracks().catch(() => []))
    })().catch((e: unknown) => { if (!dead) { setPhase('failed'); setMessage(e instanceof Error ? e.message : 'The native player failed') } })
    return () => { dead = true; void native.command('close').catch(() => undefined) }
  }, [cid, mid, rect])

  // Keep the video window under the stage when the window or the zoom changes.
  useEffect(() => {
    if (phase !== 'ready' || !stage.current) return
    const move = () => { void native.rect(rect()).catch(() => undefined) }
    const ro = new ResizeObserver(move)
    ro.observe(stage.current)
    window.addEventListener('resize', move)
    return () => { ro.disconnect(); window.removeEventListener('resize', move) }
  }, [phase, rect])

  // What the player is doing, four times a second.
  useEffect(() => {
    if (phase !== 'ready') return
    let busy = false
    const t = setInterval(() => {
      if (busy) return
      busy = true
      native.state().then(setSnap).catch(() => undefined).finally(() => { busy = false })
    }, 250)
    return () => clearInterval(t)
  }, [phase])

  const send = (command: Parameters<typeof native.command>[0], value?: number) => { void native.command(command, value).catch((e: unknown) => setMessage(e instanceof Error ? e.message : '')) }
  const playing = snap?.playing ?? false
  const duration = snap?.duration || opened?.duration || 0
  const position = seeking ?? snap?.position ?? 0
  const back = () => router.history.back()
  const NEXT: Record<VideoLayer, VideoLayer> = { front: 'popup', popup: 'behind', behind: 'front' }
  const COMMAND = { front: 'front', popup: 'popup', behind: 'back' } as const
  const flipLayer = () => { const next = NEXT[layer]; setLayer(next); sentReport.current = false; send(COMMAND[next]) }
  const LABEL: Record<VideoLayer, string> = { front: 'Video: in front', popup: 'Video: own window', behind: 'Video: behind page' }

  const refreshReport = useCallback(async () => {
    const text = await native.diagnostics().catch((e: unknown) => `Could not read the report: ${e instanceof Error ? e.message : ''}`)
    setReport(text)
    return text
  }, [])
  // A few seconds into playback, and again whenever the layer changes, take the report and keep a copy in the app's logs (Operations → Download logs).
  const playingNow = snap?.playing ?? false
  useEffect(() => {
    if (phase !== 'ready' || !playingNow || sentReport.current) return
    const t = setTimeout(() => {
      sentReport.current = true
      void refreshReport().then((text) => api('/api/diagnostics/client', { method: 'POST', json: { events: [{ level: 'WARN', source: 'native-player', message: `Native player report (${layer})`, details: text, ts: Date.now() }] } }).catch(() => undefined))
    }, 3000)
    return () => clearTimeout(t)
  }, [phase, playingNow, layer, refreshReport])
  const copyReport = async () => {
    const text = report || await refreshReport()
    try { await navigator.clipboard.writeText(text); setCopied(true); setTimeout(() => setCopied(false), 2000) } catch { /* the text stays selectable in the panel */ }
  }

  const body = (
    <div className="fixed inset-0 z-[2000] grid grid-rows-[auto_1fr_auto]" data-testid="native-player">
      <div className={BAR}>
        <button type="button" className={BTN} onClick={back}>← Back</button>
        <span className="font-semibold">Native player</span>
        <span className="text-muted" role="status">
          {phase === 'checking' ? 'Checking…' : phase === 'opening' ? 'Opening the video…' : phase === 'unavailable' ? message : phase === 'failed' ? `Could not play: ${message}` : snap?.buffering ? 'Buffering…' : ''}
        </span>
      </div>

      <div ref={stage} className="relative min-h-0" data-testid="stage">
        {phase === 'ready' ? (
          <div className="pointer-events-none absolute left-3 top-3 max-w-[80%] rounded-lg bg-black/70 px-3 py-2 text-xs text-white">
            If you can read this over the picture, controls and captions can sit on top of the video.
            <div className="mt-1 opacity-80">
              {opened?.decoder ? `decoder ${opened.decoder} · ` : ''}{snap?.width || opened?.width}×{snap?.height || opened?.height} · {layer === 'front' ? 'video in front' : layer === 'popup' ? 'video in its own window' : 'video behind the page'}
              {snap ? ` · video window ${snap.windowVisible ? 'shown' : 'HIDDEN'} ${snap.windowWidth}×${snap.windowHeight}` : ''}
              {snap?.error ? ` · error 0x${(snap.error >>> 0).toString(16)}` : ''}
              {snap ? ` · frames drawn ${snap.framesDrawn} · on screen ${snap.screenDistinct <= 2 ? 'FLAT (no picture seen)' : 'picture seen'}` : ''}
            </div>
          </div>
        ) : null}
        {phase === 'ready' ? (
          <details className="absolute bottom-3 left-3 max-w-[90%] rounded-lg bg-black/80 px-3 py-2 text-xs text-white" onToggle={(e) => { if ((e.target as HTMLDetailsElement).open) void refreshReport() }}>
            <summary className="cursor-pointer select-none">Details for support</summary>
            <pre className="mt-2 max-h-64 overflow-auto whitespace-pre-wrap" data-testid="report">{report || 'Reading…'}</pre>
            <button type="button" className={`${BTN} mt-2`} onClick={() => void copyReport()}>{copied ? 'Copied' : 'Copy report'}</button>
          </details>
        ) : null}
        {phase === 'unavailable' || phase === 'failed' ? <p className="m-6 max-w-xl rounded-xl bg-bg p-4 text-sm" role="alert">{message}</p> : null}
      </div>

      <div className={BAR}>
        <button type="button" className={BTN} disabled={phase !== 'ready'} onClick={() => send(playing ? 'pause' : 'play')}>{playing ? 'Pause' : 'Play'}</button>
        <span className="w-24 tabular-nums">{clock(position)} / {clock(duration)}</span>
        <input
          type="range" aria-label="Seek" className="min-w-40 flex-1" min={0} max={Math.max(1, Math.floor(duration))} step={1} value={Math.floor(position)} disabled={phase !== 'ready' || !duration}
          onChange={(e) => setSeeking(Number(e.target.value))}
          onPointerUp={() => { if (seeking !== null) { send('seek', seeking); setSeeking(null) } }}
          onKeyUp={() => { if (seeking !== null) { send('seek', seeking); setSeeking(null) } }}
        />
        <label className="flex items-center gap-2">Volume
          <input type="range" aria-label="Volume" min={0} max={100} value={volume} disabled={phase !== 'ready'} onChange={(e) => { const v = Number(e.target.value); setVolume(v); send('volume', v) }} />
        </label>
        <button type="button" className={BTN} aria-pressed={muted} disabled={phase !== 'ready'} onClick={() => { send('mute', muted ? 0 : 1); setMuted(!muted) }}>{muted ? 'Unmute' : 'Mute'}</button>
        <button type="button" className={BTN} aria-label="Video layer" disabled={phase !== 'ready'} onClick={flipLayer} title="If the picture does not show, try the next layer">
          {LABEL[layer]}
        </button>
        <label className="flex items-center gap-2">Speed
          <select aria-label="Speed" className="h-9 rounded-xl border border-line-2 bg-bg px-2" value={rate} disabled={phase !== 'ready'} onChange={(e) => { const v = Number(e.target.value); setRate(v); send('rate', v) }}>
            {SPEEDS.map((s) => <option key={s} value={s}>{s}×</option>)}
          </select>
        </label>
        {tracks.length > 1 ? (
          <label className="flex items-center gap-2">Audio
            <select aria-label="Audio track" className="h-9 rounded-xl border border-line-2 bg-bg px-2" defaultValue={Math.max(0, tracks.findIndex((t) => t.selected))} onChange={(e) => send('audio', Number(e.target.value))}>
              {tracks.map((t, i) => <option key={i} value={i}>{t.name || `Track ${i + 1}`}</option>)}
            </select>
          </label>
        ) : null}
      </div>
    </div>
  )
  return createPortal(body, document.body)
}
