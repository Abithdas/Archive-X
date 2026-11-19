import { useEffect, useMemo } from 'react'
import { create } from 'zustand'
import { useStatus } from '@/api/hooks'
import { buildNotifications, type Notice } from './notifications'
import { loadSeen, markSeen, saveSeen, unseen } from './seenNotices'

/** Notices derived from the live status stream, minus the ones the person has cleared. The same list feeds the bell and the Notifications page. */
export function useNotices(): Notice[] {
  const { data } = useStatus()
  const events = data?.events
  const jobs = data?.jobs
  const gone = useCleared((s) => s.gone)
  const all = useMemo(() => buildNotifications(Array.isArray(events) ? (events as never[]) : [], Array.isArray(jobs) ? (jobs as never[]) : [], Date.now(), 100), [events, jobs])
  return useMemo(() => { const hidden = new Set(gone); return all.filter((n) => !hidden.has(n.key)) }, [all, gone])
}

const CLEARED_KEY = 'ax_cleared_notices'
const KEEP = 300
const loadCleared = (): string[] => { try { const raw = JSON.parse(localStorage.getItem(CLEARED_KEY) || '[]'); return Array.isArray(raw) ? raw.filter((x): x is string => typeof x === 'string').slice(-KEEP) : [] } catch { return [] } }
const saveCleared = (keys: readonly string[]) => { try { localStorage.setItem(CLEARED_KEY, JSON.stringify(keys.slice(-KEEP))) } catch { /* private mode: they come back on reload */ } }

/** Notices the person dismissed (one by one, or all at once). Kept in this browser. */
export const useCleared = create<{ gone: readonly string[]; dismiss: (keys: readonly string[]) => void }>((set, get) => ({
  gone: loadCleared(),
  dismiss: (keys) => { const next = [...new Set([...get().gone, ...keys])].slice(-KEEP); saveCleared(next); set({ gone: next }) },
}))

export const useSeen = create<{ seen: readonly string[]; mark: (rows: readonly { key: string }[]) => void }>((set, get) => ({
  seen: loadSeen(),
  mark: (rows) => { const next = markSeen(get().seen, rows); if (next !== get().seen) { saveSeen(next); set({ seen: next }) } },
}))

/** How many notices are new: the ones the person has not opened the Notifications page on yet. The bell shows this. */
export function useUnseenCount(): number {
  const rows = useNotices()
  const seen = useSeen((s) => s.seen)
  return useMemo(() => unseen(rows, seen).length, [rows, seen])
}

/** Being on the Notifications page is what makes its notices seen: the bell's count drops to nothing there (called by the shell, which knows the page). */
export function useMarkSeenOnPage(onPage: boolean): void {
  const rows = useNotices()
  const mark = useSeen((s) => s.mark)
  useEffect(() => { if (onPage) mark(rows) }, [onPage, rows, mark])
}
