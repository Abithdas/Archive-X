import { useRouter } from '@tanstack/react-router'
import { useEffect, type RefObject } from 'react'

/** Where each history entry was scrolled to. Entries are told apart by the router's own key, so going back to a page finds its own spot. */
const saved = new Map<string, number>()
const MAX_ENTRIES = 80

export const entryKey = (loc: { href: string; state?: unknown }): string => {
  const k = (loc.state as { __TSR_key?: string } | undefined)?.__TSR_key
  return k || loc.href
}

export function rememberScroll(key: string, top: number): void {
  saved.delete(key)
  saved.set(key, top)
  if (saved.size > MAX_ENTRIES) saved.delete(saved.keys().next().value as string)
}
export const recalledScroll = (key: string): number => saved.get(key) ?? 0

/**
 * Keeps the page scroll when you go back: the position is stored as you leave a page, and when you come back it is restored, waiting a
 * moment for the page's content to be tall enough again. A page you open fresh starts at the top.
 */
export function useScrollMemory(ref: RefObject<HTMLElement | null>): void {
  const router = useRouter()
  useEffect(() => {
    let frame = 0
    const settle = (key: string) => {
      cancelAnimationFrame(frame)
      const target = recalledScroll(key)
      const el = ref.current
      if (!el) return
      if (!target) { el.scrollTop = 0; return }
      const began = performance.now()
      const step = () => {
        const box = ref.current
        if (!box) return
        if (box.scrollHeight - box.clientHeight >= target || performance.now() - began > 1500) { box.scrollTop = target; return }
        frame = requestAnimationFrame(step)
      }
      step()
    }
    const off1 = router.subscribe('onBeforeLoad', ({ fromLocation, pathChanged }) => {
      if (fromLocation && pathChanged && ref.current) rememberScroll(entryKey(fromLocation), ref.current.scrollTop)
    })
    const off2 = router.subscribe('onResolved', ({ toLocation, pathChanged }) => {
      if (pathChanged) settle(entryKey(toLocation))
    })
    return () => { off1(); off2(); cancelAnimationFrame(frame) }
  }, [router, ref])
}
