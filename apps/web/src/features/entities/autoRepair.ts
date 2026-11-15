import { useQueryClient } from '@tanstack/react-query'
import { useEffect } from 'react'
import { api } from '@/api/client'
import type { EntityKind } from '@/api/types'

/** While the Performers or Studios page is open, ask the backend this often to fill in blank genders and missing pictures. */
export const REPAIR_EVERY_MS = 10 * 60_000
/** The fetch runs on its own after the request returns; look at the list again after these delays to show what arrived. */
export const REFRESH_AFTER_MS = [20_000, 90_000] as const

export function repairBody(kind: EntityKind): { kind: EntityKind } {
  return { kind }
}

/**
 * Nothing to press: the backend repairs blank genders and missing pictures by itself after a metadata scan, and this asks it to do the
 * same when the page opens and every ten minutes while it stays open. A failed request is not shown; the next one tries again.
 */
export function useAutoRepair(kind: EntityKind): void {
  const qc = useQueryClient()
  useEffect(() => {
    let alive = true
    const timers: ReturnType<typeof setTimeout>[] = []
    const run = () => {
      api('/api/entities/auto-repair', { method: 'POST', json: repairBody(kind) })
        .then(() => {
          for (const ms of REFRESH_AFTER_MS) timers.push(setTimeout(() => { if (alive) void qc.invalidateQueries({ queryKey: ['entities'] }) }, ms))
        })
        .catch(() => undefined)
    }
    run()
    const every = setInterval(run, REPAIR_EVERY_MS)
    return () => {
      alive = false
      clearInterval(every)
      timers.forEach(clearTimeout)
    }
  }, [kind, qc])
}
