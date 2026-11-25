import { useEffect, useState } from 'react'
import { useQueryClient } from '@tanstack/react-query'
import { api } from './client'
import { queryKeys } from './hooks'
import { watchDataVersions } from './dataVersions'
import type { Status } from './types'

export type LiveState = 'connecting' | 'live' | 'offline'

/** Parse one /ws frame; the server sends the same JSON as /api/status. Returns null for anything else. */
export function parseStatusFrame(data: unknown): Status | null {
  if (typeof data !== 'string') return null
  try {
    const parsed: unknown = JSON.parse(data)
    return parsed && typeof parsed === 'object' && !Array.isArray(parsed) ? (parsed as Status) : null
  } catch {
    return null
  }
}

/** What the live connection tells the backend's diagnostics when it closes, so a red dot can be explained from the logs zip. */
export function closeReport(e: { code: number; reason: string; wasClean: boolean }, upMs: number | null, attempt: number, online: boolean) {
  return {
    type: 'live.socket_closed',
    level: 'WARN',
    message: `Live updates connection closed (code ${e.code}${e.reason ? `, ${e.reason}` : ''}); ${upMs === null ? 'it never opened' : `it was open for ${Math.round(upMs / 1000)} s`}`,
    data: { code: e.code, reason: e.reason.slice(0, 120), clean: e.wasClean, open_seconds: upMs === null ? null : Math.round(upMs / 1000), retry: attempt, browser_online: online },
  }
}

/** At most one report a minute: a flapping connection must not flood the log. */
export const REPORT_EVERY_MS = 60_000

/** How often the status is fetched instead while the live connection is down. */
export const POLL_WHILE_NOT_LIVE_MS = 5_000

/** Reconnect delay with exponential backoff, capped. */
export const backoffMs = (attempt: number) => Math.min(15_000, 500 * 2 ** Math.min(attempt, 5))

/**
 * Bridge the /ws stream into the TanStack Query cache so every component reads live data through useStatus().
 * Enabled only while signed in. Replaces the legacy startOpsSocket/checkOpsSocketHealth/retryOpsSocket trio.
 */
export function useLiveStatus(enabled: boolean): LiveState {
  const client = useQueryClient()
  const [state, setState] = useState<LiveState>('connecting')

  useEffect(() => {
    if (!enabled) return
    let socket: WebSocket | null = null
    let timer: ReturnType<typeof setTimeout> | undefined
    let attempt = 0
    let stopped = false
    let openedAt: number | null = null
    let lastReport = 0

    const connect = () => {
      if (stopped) return
      setState('connecting')
      const scheme = location.protocol === 'https:' ? 'wss' : 'ws'
      socket = new WebSocket(`${scheme}://${location.host}/ws`)
      openedAt = null
      socket.onopen = () => {
        attempt = 0
        openedAt = Date.now()
        setState('live')
      }
      socket.onmessage = (event) => {
        const frame = parseStatusFrame(event.data)
        if (frame) client.setQueryData(queryKeys.status, frame)
      }
      socket.onclose = (e) => {
        if (stopped) return
        setState('offline')
        const now = Date.now()
        if (now - lastReport >= REPORT_EVERY_MS) {
          lastReport = now
          const report = closeReport(e, openedAt === null ? null : now - openedAt, attempt, typeof navigator === 'undefined' ? true : navigator.onLine)
          void api('/api/diagnostics/client', { method: 'POST', json: { events: [report] } }).catch(() => undefined)
        }
        timer = setTimeout(connect, backoffMs(attempt++))
      }
      socket.onerror = () => socket?.close()
    }
    connect()
    return () => {
      stopped = true
      if (timer) clearTimeout(timer)
      socket?.close()
    }
  }, [enabled, client])

  // Whatever feeds the status (the socket or the polling below), a moved data counter refreshes the pages that show that data.
  useEffect(() => (enabled ? watchDataVersions(client) : undefined), [enabled, client])

  // While the live connection is not up (blocked by something on the way, or still connecting) the status is asked for every few seconds, so the
  // running-job counts, notices and the Telegram state keep moving instead of freezing at whatever was loaded first.
  useEffect(() => {
    if (!enabled || state === 'live') return
    const timer = setInterval(() => { void client.invalidateQueries({ queryKey: queryKeys.status }) }, POLL_WHILE_NOT_LIVE_MS)
    return () => clearInterval(timer)
  }, [enabled, state, client])

  return state
}
