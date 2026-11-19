/**
 * Keeps Chrome's own "Live Caption" away from the player.
 *
 * Chrome only sends a media element's sound to Live Caption while the element plays straight to the speakers. Chromium's audio
 * renderer skips transcription when a Web Audio graph is attached to the element (WebAudioSourceProviderImpl reports
 * IsOptimizedForHardwareParameters() = false once there is a client). So the video's (and the alternate audio track's) sound is
 * routed through an AudioContext, which then plays it. Same-origin media only (CORS would silence it); ours always is.
 * A media element can only be connected once, so this is done once per element, on its first play.
 */
export const BLOCK_KEY = 'archive-x:block-browser-captions'
const routed = new WeakSet<HTMLMediaElement>()
export type SoundMode = 'normal' | 'dialogue' | 'night'
interface Chain { gain: GainNode; voice: BiquadFilterNode; comp: DynamicsCompressorNode }
const chains = new WeakMap<HTMLMediaElement, Chain>()
let context: AudioContext | null = null
let wanted = 1   // the volume boost, 1 = none; applied to every routed element
let mode: SoundMode = 'normal'

export const blockingEnabled = (): boolean => { try { return localStorage.getItem(BLOCK_KEY) !== '0' } catch { return true } }
export const setBlocking = (on: boolean) => { try { localStorage.setItem(BLOCK_KEY, on ? '1' : '0') } catch { /* private mode */ } }

/** Route this element's sound through the app. Returns true when it is (or already was) routed. */
export function routeThroughApp(el: HTMLMediaElement | null | undefined, force = false): boolean {
  if (!el) return false
  if (routed.has(el)) return true
  if (!force && !blockingEnabled() && wanted <= 1 && mode === 'normal') return false
  const Ctor: typeof AudioContext | undefined = typeof window === 'undefined' ? undefined : window.AudioContext ?? (window as unknown as { webkitAudioContext?: typeof AudioContext }).webkitAudioContext
  if (!Ctor) return false
  try {
    context ??= new Ctor({ latencyHint: 'playback' })
    const gain = context.createGain()
    const voice = context.createBiquadFilter()     // lifts the range where speech sits
    voice.type = 'peaking'; voice.frequency.value = 2500; voice.Q.value = 0.9
    const comp = context.createDynamicsCompressor()   // evens out loud and quiet scenes
    context.createMediaElementSource(el).connect(voice)
    voice.connect(comp); comp.connect(gain); gain.connect(context.destination)
    chains.set(el, { gain, voice, comp })
    apply(el)
    routed.add(el)
    void context.resume?.().catch(() => undefined)
    return true
  } catch { return false }
}

/** The sound mode and boost as set now, on one element's chain. Normal leaves the sound alone (flat filter, compressor that does nothing). */
function apply(el: HTMLMediaElement) {
  const c = chains.get(el)
  if (!c) return
  const night = mode === 'night'
  c.voice.gain.value = mode === 'dialogue' ? 6 : 0
  c.comp.threshold.value = night ? -45 : 0
  c.comp.knee.value = night ? 30 : 0
  c.comp.ratio.value = night ? 10 : 1
  c.comp.attack.value = 0.003
  c.comp.release.value = 0.25
  c.gain.gain.value = wanted * (night ? 1.6 : 1)      // the compressor lowers the loud parts: bring the level back up
}

/** Dialogue boost or night mode. Routes the element through the app if it is not yet; returns false when that is not possible. */
export function setSoundMode(el: HTMLMediaElement | null | undefined, next: SoundMode): boolean {
  mode = next
  if (!el) return true
  if (next !== 'normal' && !routeThroughApp(el, true)) return false
  apply(el)
  return true
}

/** Volume boost, as a multiple (1 to 3). Routes the element through the app if it is not yet; returns false when that is not possible. */
export function setBoost(el: HTMLMediaElement | null | undefined, factor: number): boolean {
  wanted = Math.min(3, Math.max(1, factor))
  if (!el) return true
  if (wanted > 1 && !routeThroughApp(el, true)) return false
  apply(el)
  return true
}

/** A suspended context is silent; resume it on any play or click (browsers allow that after a user gesture). */
export function resumeAudio(): void { try { void context?.resume?.().catch(() => undefined) } catch { /* ignore */ } }
/** For tests. */
export const _resetAudioGraph = () => { context = null; wanted = 1; mode = 'normal' }
