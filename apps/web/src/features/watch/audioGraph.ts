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
/** The sound path of one element: input → high-pass → low shelf → two speech peaks → compressor → make-up gain → limiter → speakers. Every stage is neutral in Normal mode. */
interface Chain { hp: BiquadFilterNode; low: BiquadFilterNode; body: BiquadFilterNode; presence: BiquadFilterNode; comp: DynamicsCompressorNode; gain: GainNode; limit: DynamicsCompressorNode }
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
    const ctx = context
    const filter = (type: BiquadFilterType, frequency: number, q = 0.7) => { const f = ctx.createBiquadFilter(); f.type = type; f.frequency.value = frequency; f.Q.value = q; return f }
    const hp = filter('highpass', 10)                      // out of the way until a mode moves it (10 Hz passes everything audible)
    const low = filter('lowshelf', 150)                    // the weight of music and effects
    const body = filter('peaking', 1400, 0.8)              // the body of a voice
    const presence = filter('peaking', 3000, 1.0)          // the consonants that make speech understood
    const comp = ctx.createDynamicsCompressor()            // evens out loud and quiet
    const gain = ctx.createGain()                          // brings the level back after the compressor, and carries the volume boost
    const limit = ctx.createDynamicsCompressor()           // nothing the make-up gain adds may clip
    ctx.createMediaElementSource(el).connect(hp)
    hp.connect(low); low.connect(body); body.connect(presence); presence.connect(comp); comp.connect(gain); gain.connect(limit); limit.connect(ctx.destination)
    chains.set(el, { hp, low, body, presence, comp, gain, limit })
    apply(el)
    routed.add(el)
    void context.resume?.().catch(() => undefined)
    return true
  } catch { return false }
}

/**
 * The sound mode and boost as set now, on one element's chain.
 * Dialogue boost: rumble and the weight of music and effects come down, the range where speech sits comes up (about 1.4 and 3 kHz), and a gentle compressor keeps
 * every line at a similar level. Night mode: bass (explosions) is cut hard and everything is compressed so the loud parts come down towards the quiet ones, then the
 * level is brought back up and limited, so a whisper can be heard without the loud scene waking the house. Normal leaves the sound alone.
 */
function apply(el: HTMLMediaElement) {
  const c = chains.get(el)
  if (!c) return
  const dialogue = mode === 'dialogue', night = mode === 'night'
  c.hp.frequency.value = dialogue ? 90 : night ? 40 : 10
  c.low.gain.value = dialogue ? -4 : night ? -7 : 0
  c.low.frequency.value = night ? 140 : 180
  c.body.gain.value = dialogue ? 5 : night ? 2 : 0
  c.presence.gain.value = dialogue ? 7 : night ? 3 : 0
  c.comp.threshold.value = dialogue ? -26 : night ? -42 : 0
  c.comp.knee.value = dialogue ? 20 : night ? 24 : 0
  c.comp.ratio.value = dialogue ? 3 : night ? 8 : 1
  c.comp.attack.value = night ? 0.004 : 0.003
  c.comp.release.value = night ? 0.3 : 0.25
  c.gain.gain.value = wanted * (dialogue ? 1.35 : night ? 1.9 : 1)      // the compressor lowers the loud parts: bring the level back up
  c.limit.threshold.value = dialogue || night || wanted > 1 ? -3 : 0     // a ceiling only when something has made the sound louder
  c.limit.knee.value = 0
  c.limit.ratio.value = dialogue || night || wanted > 1 ? 20 : 1
  c.limit.attack.value = 0.001
  c.limit.release.value = 0.1
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
