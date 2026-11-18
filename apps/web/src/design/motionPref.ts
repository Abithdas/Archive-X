import { useSyncExternalStore } from 'react'

/**
 * Animations: "full" always plays them; "system" follows the Windows/OS "show animations" switch.
 * Many PCs have that switch off (power saver, remote desktop, performance tweaks), which silently turned off every
 * breathing dot, shimmer and glide in the app. The default is therefore "full", with a choice in Settings → Appearance.
 */
export type MotionPref = 'full' | 'system'
const KEY = 'ax_motion'
const listeners = new Set<() => void>()

export function storedMotion(): MotionPref {
  try { return localStorage.getItem(KEY) === 'system' ? 'system' : 'full' } catch { return 'full' }
}

export function applyMotion(pref: MotionPref = storedMotion()): void {
  if (typeof document !== 'undefined') document.documentElement.dataset.motion = pref
}

export function setMotion(pref: MotionPref): void {
  try { localStorage.setItem(KEY, pref) } catch { /* private window: the choice lasts until reload */ }
  applyMotion(pref)
  listeners.forEach((l) => l())
}

export const useMotionPref = (): MotionPref => useSyncExternalStore((cb) => { listeners.add(cb); return () => { listeners.delete(cb) } }, storedMotion, () => 'full')

/** True only when the person chose "follow the system" and the system asks for less motion. */
export const prefersReduced = (): boolean =>
  storedMotion() === 'system' && typeof matchMedia === 'function' && matchMedia('(prefers-reduced-motion: reduce)').matches
