import type { QueryClient } from '@tanstack/react-query'
import { queryKeys } from './hooks'
import type { Status } from './types'

/**
 * The backend counts changes per scope (videos, entities, channels, settings) and sends the counters with every status frame. When a counter moves,
 * the pages that show that kind of data are asked again, so a video added by a scan, a performer renamed by a job or a channel created elsewhere
 * shows up without a reload. The first frame only records the starting point: nothing was missed that the page did not just load.
 */
export const SCOPE_KEYS: Record<string, ReadonlyArray<readonly string[]>> = {
  videos: [['library'], ['library-summary'], ['video'], ['series'], ['performer'], ['studio'], ['entities'], ['titles-of-media'], ['local-folders'], ['lanes']],
  entities: [['entities'], ['performer'], ['studio'], ['performer-gallery'], ['performer-photos'], ['name-list'], ['suggestions'], ['library']],
  channels: [['channels'], ['source-channels'], ['photo-channels'], ['channel-marks'], ['library-summary']],
  settings: [['settings'], ['local-settings']],
}

/** The scopes whose counter is different from before (a scope seen for the first time counts as unchanged). */
export function changedScopes(prev: Record<string, number> | undefined, next: Record<string, number> | undefined): string[] {
  if (!prev || !next) return []
  return Object.keys(next).filter((scope) => scope in prev && prev[scope] !== next[scope])
}

function versionsOf(status: Status | undefined): Record<string, number> | undefined {
  const v = status?.data_versions
  return v && typeof v === 'object' && !Array.isArray(v) ? v : undefined
}

/** Refresh what the changed scopes show; returns the scopes it acted on. */
export function refreshChanged(client: QueryClient, prev: Status | undefined, next: Status | undefined): string[] {
  const changed = changedScopes(versionsOf(prev), versionsOf(next))
  const seen = new Set<string>()
  for (const scope of changed) {
    for (const key of SCOPE_KEYS[scope] ?? []) {
      const id = key.join('/')
      if (seen.has(id)) continue
      seen.add(id)
      void client.invalidateQueries({ queryKey: [...key] })
    }
  }
  return changed
}

/** Watch the status entry of the query cache (fed by the socket and by polling alike) and refresh on every counter change. Returns the unsubscribe. */
export function watchDataVersions(client: QueryClient): () => void {
  let last = client.getQueryData<Status>(queryKeys.status)
  return client.getQueryCache().subscribe((event) => {
    if (event.type !== 'updated' || event.query.queryKey[0] !== queryKeys.status[0]) return
    const next = event.query.state.data as Status | undefined
    if (!next || next === last) return
    const before = last
    last = next
    refreshChanged(client, before, next)
  })
}
