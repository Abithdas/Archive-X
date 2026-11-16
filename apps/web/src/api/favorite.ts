/** Flip `favorite` on every row for `mediaId` inside cached query data (a library page, one video, a performer's videos), without touching
 *  the rest. Returns the same object when nothing matched, so unrelated queries are not re-rendered. */
export function withFavorite<T>(data: T, mediaId: number, favorite: boolean): T {
  const walk = (v: unknown): unknown => {
    if (Array.isArray(v)) {
      let changed = false
      const out = v.map((x) => { const y = walk(x); if (y !== x) changed = true; return y })
      return changed ? out : v
    }
    if (v && typeof v === 'object') {
      const o = v as Record<string, unknown>
      let changed = false
      const out: Record<string, unknown> = {}
      for (const [k, x] of Object.entries(o)) { const y = walk(x); if (y !== x) changed = true; out[k] = y }
      if (o.media_id === mediaId && 'favorite' in o && Boolean(o.favorite) !== favorite) return { ...out, favorite }
      return changed ? out : v
    }
    return v
  }
  return walk(data) as T
}
