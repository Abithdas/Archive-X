/** The assistant's notebook: what it noticed, and what the person decided about each note. */
export type FindingStatus = 'OPEN' | 'ACCEPTED' | 'FIXED' | 'REJECTED'
export interface Finding { id: number; kind: string; title: string; detail: string; status: FindingStatus; created_at: string; updated_at: string }
export interface FindingsState { findings: Finding[]; counts: Record<FindingStatus, number>; knowledge: Record<string, number> }

export const FILTERS: Array<{ value: FindingStatus; label: string }> = [
  { value: 'OPEN', label: 'To review' }, { value: 'ACCEPTED', label: 'Accepted' }, { value: 'FIXED', label: 'Fixed' }, { value: 'REJECTED', label: 'Rejected' },
]
export const INTRO = 'The assistant writes down problems and ideas it notices here. Nothing is changed for you: this is only its notebook. Accept what is right, mark it fixed once it is, and reject what is not: a rejected note is never written again.'
export const KIND: Record<string, string> = { issue: 'Problem', idea: 'Idea', fix: 'Fix', note: 'Note' }

/** What each state of a note can be changed to. */
export function actions(status: FindingStatus): Array<{ to: FindingStatus; label: string }> {
  if (status === 'OPEN') return [{ to: 'ACCEPTED', label: 'Accept' }, { to: 'REJECTED', label: 'Reject' }]
  if (status === 'ACCEPTED') return [{ to: 'FIXED', label: 'Mark fixed' }, { to: 'REJECTED', label: 'Reject' }]
  return [{ to: 'OPEN', label: 'Reopen' }]
}

/** "What it knows about Archive X: 120 changelog notes, 40 docs sections, 2,300 code descriptions." */
export function knowledgeLine(k: Record<string, number> | undefined): string {
  const n = (key: string) => Number(k?.[key] ?? 0)
  const total = n('changelog') + n('docs') + n('code')
  if (!total) return 'It has not read the changelog, docs or code descriptions yet.'
  const part = (count: number, one: string, many: string) => (count ? `${count.toLocaleString('en-US')} ${count === 1 ? one : many}` : '')
  return `It has read ${[part(n('changelog'), 'changelog note', 'changelog notes'), part(n('docs'), 'docs section', 'docs sections'), part(n('code'), 'code description', 'code descriptions')].filter(Boolean).join(', ')}.`
}
