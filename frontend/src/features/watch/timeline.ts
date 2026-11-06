/** Pure helpers for the player timeline (kept apart from the component so they can be tested). */
export interface Marker { position: number; title: string; note?: string }
export interface Segment { start: number; end: number; title: string; intro: boolean }

/** Marker rows from the API → clean, sorted, de-duplicated chapters inside the video. */
export function normaliseChapters(rows: unknown, duration: number): Marker[] {
  if (!Array.isArray(rows) || !(duration > 0)) return []
  const seen = new Set<string>()
  return rows
    .map((m: Record<string, unknown>) => ({ position: Number(m?.position), title: String(m?.title ?? 'Scene').trim() || 'Scene', note: String(m?.note ?? '').trim() }))
    .filter((m) => Number.isFinite(m.position) && m.position >= 0 && m.position < duration)
    .sort((a, b) => a.position - b.position)
    .filter((m) => {
      const k = m.position.toFixed(3)
      return seen.has(k) ? false : (seen.add(k), true)
    })
}

/** Chapters → contiguous segments; an "Opening" segment is added when the first marker is not at the start. */
export function toSegments(chapters: Marker[], duration: number): Segment[] {
  if (!chapters.length || !(duration > 0)) return []
  const out: Segment[] = []
  if (chapters[0]!.position > 0.15) out.push({ start: 0, end: chapters[0]!.position, title: 'Opening', intro: true })
  chapters.forEach((c, i) => out.push({ start: c.position, end: chapters[i + 1]?.position ?? duration, title: c.title, intro: false }))
  return out.filter((s) => s.end - s.start >= 0.05)
}

/** Index of the preview frame closest to time t (frame_times is the list of 36 sampled times). */
export function frameIndexFor(times: number[], t: number): number {
  if (!times.length) return -1
  let best = 0
  for (let i = 1; i < times.length; i++) if (Math.abs(times[i]! - t) < Math.abs(times[best]! - t)) best = i
  return best
}

/** Pointer x → time, clamped to the track. */
export function timeAt(clientX: number, left: number, width: number, duration: number): number {
  if (!(width > 0) || !(duration > 0)) return 0
  return Math.max(0, Math.min(duration, ((clientX - left) / width) * duration))
}

export const segmentAt = (segments: Segment[], t: number) => segments.find((s) => t >= s.start && t < s.end)

/** Keyboard step for the slider: arrows ±5 s, PageUp/Down ±30 s, Home/End jump. Returns null for other keys. */
export function keyStep(key: string, t: number, duration: number): number | null {
  const clamp = (n: number) => Math.max(0, Math.min(duration, n))
  switch (key) {
    case 'ArrowLeft': case 'ArrowDown': return clamp(t - 5)
    case 'ArrowRight': case 'ArrowUp': return clamp(t + 5)
    case 'PageDown': return clamp(t - 30)
    case 'PageUp': return clamp(t + 30)
    case 'Home': return 0
    case 'End': return duration
    default: return null
  }
}
