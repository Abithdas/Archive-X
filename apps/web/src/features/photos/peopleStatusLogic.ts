/** The small facts under the People heading: how many names are kept on this PC, what the app has learned from you, and a problem if StashDB or
 *  ThePornDB could not be read. Plain words; the details a person needs only when something is wrong. */
export interface NameList {
  ok?: boolean; total: number; ready: boolean
  providers: Record<string, { configured: boolean; status: string; count: number; last_finished: string; last_error: string; page: number }>
}
export interface FaceLearning { ok?: boolean; faces: number; confirmed: number; removed: number }

const NAMES: Record<string, string> = { stashdb: 'StashDB', theporndb: 'ThePornDB' }
export const providerLabel = (p: string) => NAMES[p] ?? p
const n = (k: number) => k.toLocaleString('en-US')

/** What went wrong, in words: the raw reason from the provider ("HTTP 401: Unauthenticated") says little to someone who just wants names. */
export function reasonText(raw: string): string {
  const t = String(raw ?? '').trim()
  if (!t) return 'an unknown problem'
  if (/HTTP (401|403)|unauthori[sz]ed|unauthenticated|forbidden|api ?key|invalid (key|token)/i.test(t)) return 'the key was not accepted, check it in Settings'
  if (/HTTP 429|too many|rate.?limit/i.test(t)) return 'too many requests, it will slow down and try again'
  if (/HTTP 5\d\d/i.test(t)) return 'the service had a problem, it will try again'
  if (/timed? ?out|connect|dns|network|resolve|unreachable/i.test(t)) return 'no connection to the service'
  return t.length > 110 ? `${t.slice(0, 109)}…` : t
}

/** The first provider that is failing, with the reason in words; null when none is. */
export function problemOf(d: NameList | undefined): { provider: string; reason: string } | null {
  const failed = Object.entries(d?.providers ?? {}).find(([, p]) => p.configured && p.status === 'error')
  return failed ? { provider: providerLabel(failed[0]), reason: reasonText(failed[1].last_error) } : null
}

/** "1,240 names on this PC", "reading StashDB names (page 12)", or what to do when no key is set. '' while unknown. */
export function namesLine(d: NameList | undefined): string {
  if (!d || typeof d.total !== 'number' || !d.providers) return ''
  const used = Object.entries(d.providers).filter(([, p]) => p.configured)
  if (!used.length) return 'Add a StashDB or ThePornDB key in Settings to search performer names here.'
  const busy = used.find(([, p]) => p.status === 'running')
  if (busy) return `${n(d.total)} names on this PC · reading ${providerLabel(busy[0])} (page ${busy[1].page})`
  if (d.total) return `${n(d.total)} names on this PC`
  return used.some(([, p]) => p.status === 'error') ? '' : 'Collecting performer names in the background…'   // the warning below says what is wrong
}

/** "learning from 5 confirmed and 2 removed faces"; '' until there is something learned. */
export function learningLine(d: FaceLearning | undefined): string {
  if (!d || typeof d.confirmed !== 'number' || typeof d.removed !== 'number' || (!d.confirmed && !d.removed)) return ''
  return `learning from ${n(d.confirmed)} confirmed and ${n(d.removed)} removed ${d.confirmed + d.removed === 1 ? 'face' : 'faces'}`
}

/** The one small line under the headline: names kept, and what it learns from, joined. */
export const detailLine = (names: NameList | undefined, learning: FaceLearning | undefined): string => [namesLine(names), learningLine(learning)].filter(Boolean).join(' · ')
