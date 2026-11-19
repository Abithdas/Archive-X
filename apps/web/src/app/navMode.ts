import { useSyncExternalStore } from 'react'

/** Where the main menu sits: "top" (one bar: logo, pages, search, account and, in the desktop app, the window's buttons) or "side" (the menu down the left). Kept per computer. */
export type NavMode = 'top' | 'side'
const KEY = 'ax_next_nav'
const listeners = new Set<() => void>()
const read = (): NavMode => {
  try {
    return localStorage.getItem(KEY) === 'side' ? 'side' : 'top'
  } catch {
    return 'top'
  }
}
let current: NavMode = read()

export function setNavMode(mode: NavMode): void {
  current = mode
  try {
    localStorage.setItem(KEY, mode)
  } catch {
    /* private mode: it lasts until the page closes */
  }
  listeners.forEach((l) => l())
}

export const useNavMode = (): NavMode =>
  useSyncExternalStore((cb) => { listeners.add(cb); return () => { listeners.delete(cb) } }, () => current, () => 'top')
