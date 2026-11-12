import { useCallback, useEffect, useRef, useState, type RefObject } from 'react'
import { CAPTION_WINDOW, activeText, initialChoice, loadPrefs, mergeCues, parseVtt, savePrefs, windowStart, type AudioTrack, type Cue, type SubChoice, type TrackInfo } from './tracks'

export interface MediaRef { cid: string; mid: number }
export type LiveState = 'idle' | 'working' | 'error'

const getJson = async <T,>(url: string): Promise<T> => {
  const r = await fetch(url, { credentials: 'same-origin', cache: 'no-store' })
  if (!r.ok) throw new Error(String(r.status))
  return (await r.json()) as T
}

/** Audio tracks, subtitle tracks and live English captions of one video; the caller shows `text` and plays `altAudioUrl`. */
export function useTracks(media: MediaRef | undefined, video: RefObject<HTMLVideoElement | null>, time: number, playing: boolean) {
  const [info, setInfo] = useState<TrackInfo | null>(null)
  const [audio, setAudioState] = useState<number | null>(null)     // null: the video's own audio
  const [sub, setSubState] = useState<SubChoice>({ kind: 'off' })
  const [cues, setCues] = useState<Cue[]>([])
  const [live, setLive] = useState<LiveState>('idle')
  const [altUrl, setAltUrl] = useState<string | null>(null)
  const [altBusy, setAltBusy] = useState(false)
  const fetched = useRef(new Set<number>())
  const key = media ? `${media.cid}/${media.mid}` : ''

  // Track list, once per video.
  useEffect(() => {
    setInfo(null); setAudioState(null); setSubState({ kind: 'off' }); setCues([]); setAltUrl(null); setLive('idle'); fetched.current = new Set()
    if (!media) return
    let dead = false
    getJson<TrackInfo>(`/api/play/tracks/${encodeURIComponent(media.cid)}/${media.mid}`).then((i) => {
      if (dead) return
      setInfo(i)
      const c = initialChoice(i, loadPrefs()); setAudioState(c.audio); setSubState(c.sub)
    }).catch(() => undefined)
    return () => { dead = true }
  }, [key])   // eslint-disable-line react-hooks/exhaustive-deps

  // An embedded subtitle track: fetch the whole file once.
  useEffect(() => {
    if (!media || sub.kind !== 'track') return
    let dead = false
    fetch(`/api/play/subtitle/${encodeURIComponent(media.cid)}/${media.mid}/${sub.index}.vtt`, { credentials: 'same-origin' })
      .then((r) => (r.ok ? r.text() : Promise.reject(new Error(String(r.status)))))
      .then((t) => { if (!dead) setCues(parseVtt(t)) }).catch(() => { if (!dead) setCues([]) })
    return () => { dead = true }
  }, [key, sub])   // eslint-disable-line react-hooks/exhaustive-deps

  // Live captions: transcribe the 30 s window being watched, and fetch the next one ahead of time.
  useEffect(() => { fetched.current = new Set(); if (sub.kind === 'live') setCues([]) }, [sub.kind, sub.kind === 'live' && sub.original, audio, key])
  useEffect(() => {
    if (!media || sub.kind !== 'live') return
    const w = windowStart(time)
    const wanted = [w, w + CAPTION_WINDOW].filter((s) => !fetched.current.has(s))
    if (!wanted.length) return
    const s = wanted[0]!
    fetched.current.add(s)
    setLive('working')
    getJson<{ cues: Cue[] }>(`/api/play/captions/${encodeURIComponent(media.cid)}/${media.mid}?start=${s}${audio !== null ? `&track=${audio}` : ''}${sub.original ? '&task=transcribe' : ''}`)
      .then((r) => { setCues((c) => mergeCues(c, r.cues)); setLive('idle') })
      .catch(() => { fetched.current.delete(s); setLive('error') })
  }, [media, sub.kind, sub.kind === 'live' && sub.original, audio, Math.floor(time / 5), playing])   // eslint-disable-line react-hooks/exhaustive-deps

  // Alternate audio: ask the server to build it, poll until it is ready.
  useEffect(() => {
    setAltUrl(null); setAltBusy(false)
    if (!media || audio === null) return
    let dead = false; let timer: ReturnType<typeof setTimeout> | undefined
    setAltBusy(true)
    const poll = () => getJson<{ ready: boolean; url?: string; error?: string }>(`/api/play/audio-track/${encodeURIComponent(media.cid)}/${media.mid}/${audio}/status`).then((s) => {
      if (dead) return
      if (s.ready && s.url) { setAltUrl(s.url); setAltBusy(false) } else if (s.error) { setAltBusy(false); setAudioState(null) } else timer = setTimeout(poll, 1500)
    }).catch(() => { if (!dead) { setAltBusy(false); setAudioState(null) } })
    void poll()
    return () => { dead = true; if (timer) clearTimeout(timer) }
  }, [key, audio])   // eslint-disable-line react-hooks/exhaustive-deps

  const pickAudio = useCallback((t: AudioTrack | null) => {
    setAudioState(t && t.ordinal !== 0 ? t.index : null)
    savePrefs({ ...loadPrefs(), audioLanguage: t?.language || undefined })
  }, [])
  const pickSub = useCallback((c: SubChoice, language = '') => {
    setSubState(c)
    savePrefs({ ...loadPrefs(), liveOriginal: c.kind === 'live' ? !!c.original : loadPrefs().liveOriginal, sub: c.kind === 'off' ? 'off' : c.kind, subLanguage: c.kind === 'track' ? language || undefined : loadPrefs().subLanguage })
  }, [])

  const text = sub.kind === 'off' ? '' : activeText(cues, time)
  void video
  return { info, audio, sub, text, live, altUrl, altBusy, pickAudio, pickSub }
}

/** Keep a hidden <audio> in step with the video (play, pause, seek, speed, volume) while it carries the chosen audio track. */
export function useAltAudio(video: RefObject<HTMLVideoElement | null>, audio: RefObject<HTMLAudioElement | null>, url: string | null, muted: boolean, volume: number, rate: number) {
  useEffect(() => {
    const v = video.current; const a = audio.current
    if (!v || !a || !url) return
    const sync = () => { if (Math.abs(a.currentTime - v.currentTime) > 0.25) a.currentTime = v.currentTime }
    const play = () => { sync(); void a.play().catch(() => undefined) }
    const pause = () => a.pause()
    const wait = () => a.pause()
    v.muted = true
    sync()
    if (!v.paused) play()
    v.addEventListener('play', play); v.addEventListener('playing', play); v.addEventListener('pause', pause); v.addEventListener('waiting', wait)
    v.addEventListener('seeked', sync); v.addEventListener('timeupdate', sync)
    return () => {
      v.removeEventListener('play', play); v.removeEventListener('playing', play); v.removeEventListener('pause', pause); v.removeEventListener('waiting', wait)
      v.removeEventListener('seeked', sync); v.removeEventListener('timeupdate', sync)
      a.pause(); v.muted = muted
    }
  }, [url])   // eslint-disable-line react-hooks/exhaustive-deps
  useEffect(() => {
    const a = audio.current; if (!a || !url) return
    a.muted = muted; a.volume = volume; a.playbackRate = rate
    if (video.current) video.current.muted = true
  }, [url, muted, volume, rate])   // eslint-disable-line react-hooks/exhaustive-deps
}
