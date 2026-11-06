import { useSyncExternalStore } from 'react'

const current = () => (document.documentElement.dataset.theme === 'light' ? 'light' : 'dark')
const subscribe = (cb: () => void) => {
  const o = new MutationObserver(cb)
  o.observe(document.documentElement, { attributes: true, attributeFilter: ['data-theme'] })
  return () => o.disconnect()
}
export const useTheme = () => useSyncExternalStore(subscribe, current, () => 'dark' as const)

export function toggleTheme() {
  const next = current() === 'light' ? 'dark' : 'light'
  document.documentElement.dataset.theme = next
  try {
    localStorage.setItem('ax_theme_mode', next)
  } catch {
    /* private mode */
  }
}
