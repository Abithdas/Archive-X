import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { fireEvent, render, screen, waitFor, within } from '@testing-library/react'
import { afterEach, beforeAll, describe, expect, it, vi } from 'vitest'
import { toast } from 'sonner'
import { PerformerAudit } from './PerformerAudit'

vi.mock('sonner', () => ({ toast: Object.assign(vi.fn(() => 'id'), { error: vi.fn(), success: vi.fn(), loading: vi.fn(() => 'p'), dismiss: vi.fn() }), Toaster: () => null }))
beforeAll(() => {
  HTMLDialogElement.prototype.showModal = function () { this.setAttribute('open', '') }
  HTMLDialogElement.prototype.close = function () { this.removeAttribute('open') }
})
afterEach(() => { vi.unstubAllGlobals(); vi.clearAllMocks() })

const json = (b: unknown, code = 200) => new Response(JSON.stringify(b), { status: code })
const m = (id: number, name: string, over: object = {}) => ({ id, name, video_count: 2, ...over })
const AUDIT = {
  ok: true, performer_count: 50, candidate_merge_groups: 0, review_groups: 1, suggestion_groups: 0, junk_fragment_groups: 1, unmatched_performer_count: 4,
  groups: [
    { kind: 'strong_merge', auto_merge_eligible: true, performers: [m(1, 'Ava Stone'), m(2, 'Ava Stonee')] },
    { kind: 'review', contains_unknown_performer: true, performers: [m(9, 'Unknown Performer'), m(10, 'Unknown')] },
    { kind: 'junk_fragment', reason: 'Looks like a release tag.', performers: [m(7, '1080p WEB', { video_count: 0 })], suggested_target: { id: 3, name: 'Mia Rivera', video_count: 5 } },
    { kind: 'junk_fragment', performers: [m(8, 'Part 2', { video_count: 4 })], suggested_target: null },
  ],
}
type Call = { url: string; method: string; body: Record<string, unknown> | undefined }
const mount = (audit: unknown = AUDIT, extra: (u: string, m: string, n: number) => Response | undefined = () => undefined) => {
  const calls: Call[] = []
  const counts: Record<string, number> = {}
  vi.stubGlobal('fetch', vi.fn(async (url: string, init?: RequestInit) => {
    const method = init?.method ?? 'GET'
    calls.push({ url, method, body: init?.body ? (JSON.parse(String(init.body)) as Record<string, unknown>) : undefined })
    counts[url] = (counts[url] ?? 0) + 1
    const x = extra(url, method, counts[url]!)
    if (x) return x
    if (url === '/api/performers/duplicate-audit') return typeof audit === 'function' ? (audit as () => Response)() : json(audit)
    return json({ ok: true })
  }))
  const onClose = vi.fn()
  render(<QueryClientProvider client={new QueryClient()}><PerformerAudit onClose={onClose} /></QueryClientProvider>)
  return { calls, onClose }
}
const posts = (c: Call[], url: string) => c.filter((x) => x.url === url && x.method === 'POST')
const scans = (c: Call[]) => c.filter((x) => x.url === '/api/performers/duplicate-audit' && x.method === 'GET')
const ready = async () => { await screen.findByText(/Scanned 50 performer records/); return screen.getByRole('dialog', { name: 'Duplicate performers' }) }

describe('Duplicate performers: review', () => {
  it('scans, explains it is read-only while it works, then shows the totals and the groups under headings', async () => {
    mount()
    expect(screen.getByText(/The audit itself is read-only/)).toBeInTheDocument()
    const d = await ready()
    expect(d).toHaveTextContent('1 high-confidence groups safe to auto-merge')
    expect(within(d).getByRole('heading', { name: 'Strong candidates' })).toBeInTheDocument()
    expect(within(d).getByRole('heading', { name: 'Review before merging' })).toBeInTheDocument()
    expect(within(d).getByRole('heading', { name: 'Likely filename/title fragments' })).toBeInTheDocument()
    expect(within(d).getByText('ID 1 · 2 videos')).toBeInTheDocument()
  })

  it('says so when nothing looks duplicated', async () => {
    mount({ ok: true, performer_count: 50, groups: [] })
    expect(await screen.findByText('No likely duplicate groups found.')).toBeInTheDocument()
  })

  it('shows the reason when the audit fails', async () => {
    mount(() => json({ ok: false, detail: 'Database busy' }))
    expect(await screen.findByText('Database busy')).toBeInTheDocument()
  })
})

describe('Duplicate performers: merging one group', () => {
  it('needs a keeper and a different record before it can merge', async () => {
    mount()
    const d = await ready()
    const group = within(d).getByRole('region', { name: 'Same canonical name and compatible provider IDs' })
    const merge = within(group).getByRole('button', { name: 'Merge checked' })
    expect(merge).toBeDisabled()
    fireEvent.click(within(group).getByLabelText('Keep Ava Stone'))
    expect(merge).toBeDisabled()
    fireEvent.click(within(group).getByLabelText('Merge Ava Stonee'))
    expect(merge).toBeEnabled()
    expect(within(group).getByLabelText('Merge Ava Stone')).toBeDisabled()   // the keeper cannot also be merged away
  })

  it('asks first, naming the keeper and what will be merged, and sends nothing on cancel', async () => {
    const { calls } = mount()
    const d = await ready()
    const group = within(d).getByRole('region', { name: 'Same canonical name and compatible provider IDs' })
    fireEvent.click(within(group).getByLabelText('Keep Ava Stone'))
    fireEvent.click(within(group).getByLabelText('Merge Ava Stonee'))
    fireEvent.click(within(group).getByRole('button', { name: 'Merge checked' }))
    const ask = await screen.findByRole('dialog', { name: 'Confirm performer cleanup' })
    expect(ask).toHaveTextContent('Merge 1 selected record(s) into “Ava Stone”?')
    expect(ask).toHaveTextContent('• Ava Stonee')
    expect(ask).toHaveTextContent('permanent change')
    fireEvent.click(within(ask).getByRole('button', { name: 'Cancel' }))
    expect(posts(calls, '/api/performers/merge-batch')).toHaveLength(0)
  })

  it('merges in one request, then reviews again so the group is gone', async () => {
    let n = 0
    const { calls } = mount(() => (++n === 1 ? json(AUDIT) : json({ ok: true, performer_count: 49, groups: [] })), (u, mth) => (u === '/api/performers/merge-batch' && mth === 'POST' ? json({ ok: true, merged_count: 1, name: 'Ava Stone' }) : undefined))
    const d = await ready()
    const group = within(d).getByRole('region', { name: 'Same canonical name and compatible provider IDs' })
    fireEvent.click(within(group).getByLabelText('Keep Ava Stone'))
    fireEvent.click(within(group).getByLabelText('Merge Ava Stonee'))
    fireEvent.click(within(group).getByRole('button', { name: 'Merge checked' }))
    fireEvent.click(within(await screen.findByRole('dialog', { name: 'Confirm performer cleanup' })).getByRole('button', { name: 'Merge 1' }))
    await waitFor(() => expect(posts(calls, '/api/performers/merge-batch')).toHaveLength(1))
    expect(posts(calls, '/api/performers/merge-batch')[0]!.body).toEqual({ keep_id: 1, merge_ids: [2] })
    await waitFor(() => expect(toast).toHaveBeenCalledWith('Merged 1 performer record(s) into “Ava Stone”.'))
    await waitFor(() => expect(scans(calls)).toHaveLength(2))
    expect(await screen.findByText('No likely duplicate groups found.')).toBeInTheDocument()
  })

  it('shows the server reason when the merge fails and keeps the group', async () => {
    mount(AUDIT, (u, mth) => (u === '/api/performers/merge-batch' && mth === 'POST' ? json({ detail: 'Conflicting provider IDs' }, 409) : undefined))
    const d = await ready()
    const group = within(d).getByRole('region', { name: 'Same canonical name and compatible provider IDs' })
    fireEvent.click(within(group).getByLabelText('Keep Ava Stone'))
    fireEvent.click(within(group).getByLabelText('Merge Ava Stonee'))
    fireEvent.click(within(group).getByRole('button', { name: 'Merge checked' }))
    fireEvent.click(within(await screen.findByRole('dialog', { name: 'Confirm performer cleanup' })).getByRole('button', { name: 'Merge 1' }))
    expect(await screen.findByText('Conflicting provider IDs')).toBeInTheDocument()
    expect(screen.getByRole('region', { name: 'Same canonical name and compatible provider IDs' })).toBeInTheDocument()
  })

  it('never offers to merge the protected Unknown Performer group', async () => {
    mount()
    const d = await ready()
    const group = within(d).getByRole('region', { name: 'Conflicting identity evidence' })
    expect(within(group).queryByRole('button', { name: 'Merge checked' })).toBeNull()
    expect(within(group).queryByRole('radio')).toBeNull()
    expect(within(group).getAllByText('Protected fallback')).toHaveLength(2)
    expect(group).toHaveTextContent('Unknown Performer is protected')
  })
})

describe('Duplicate performers: automatic cleanup', () => {
  it('asks first, stating how many records and groups, then sends one request and shows the new audit and outcome', async () => {
    const result = { ok: true, merged_performer_count: 1, merged_group_count: 1, ambiguous_groups_left_for_review: 2, protected_unknown_group_count: 1, audit: { ok: true, performer_count: 49, groups: [] } }
    const { calls } = mount(AUDIT, (u, mth) => (u === '/api/performers/duplicate-audit/auto-cleanup' && mth === 'POST' ? json(result) : undefined))
    const d = await ready()
    fireEvent.click(within(d).getByRole('button', { name: 'Auto-merge high-confidence (1)' }))
    const ask = await screen.findByRole('dialog', { name: 'Auto-clean high-confidence duplicates' })
    expect(ask).toHaveTextContent('Automatically merge 1 duplicate performer record(s) in 1 high-confidence group(s)?')
    expect(posts(calls, '/api/performers/duplicate-audit/auto-cleanup')).toHaveLength(0)
    fireEvent.click(within(ask).getByRole('button', { name: 'Auto-merge 1' }))
    await waitFor(() => expect(posts(calls, '/api/performers/duplicate-audit/auto-cleanup')).toHaveLength(1))
    expect(posts(calls, '/api/performers/duplicate-audit/auto-cleanup')[0]!.body).toEqual({})
    expect(await screen.findByText(/Auto-cleanup complete: merged 1 performer record\(s\) in 1 group\(s\)\. Left 2 ambiguous group\(s\)/)).toBeInTheDocument()
    expect(screen.getByText('No likely duplicate groups found.')).toBeInTheDocument()
    expect(toast).toHaveBeenCalledWith('Auto-cleanup merged 1 high-confidence duplicate performer record(s). Ambiguous matches were left for review.')
  })

  it('is disabled when nothing is safe to merge', async () => {
    mount({ ok: true, performer_count: 5, groups: [{ kind: 'review', performers: [m(1, 'A'), m(2, 'B')] }] })
    await screen.findByText(/Scanned 5 performer records/)
    expect(screen.getByRole('button', { name: 'No safe auto-merges' })).toBeDisabled()
  })

  it('cancelling the confirmation sends nothing', async () => {
    const { calls } = mount()
    const d = await ready()
    fireEvent.click(within(d).getByRole('button', { name: /Auto-merge high-confidence/ }))
    fireEvent.click(within(await screen.findByRole('dialog', { name: 'Auto-clean high-confidence duplicates' })).getByRole('button', { name: 'Cancel' }))
    expect(posts(calls, '/api/performers/duplicate-audit/auto-cleanup')).toHaveLength(0)
  })
})

describe('Duplicate performers: filename fragments', () => {
  it('merges a fragment into its suggested target after asking', async () => {
    const { calls } = mount(AUDIT, (u, mth) => (u === '/api/performers/junk-cleanup' && mth === 'POST' ? json({ ok: true, name: 'Mia Rivera' }) : undefined))
    const d = await ready()
    const frag = within(d).getByRole('region', { name: 'Fragment: 1080p WEB' })
    expect(frag).toHaveTextContent('Suggested merge target')
    expect(frag).toHaveTextContent('ID 3 · 5 linked videos')
    fireEvent.click(within(frag).getByRole('button', { name: 'Merge into Mia Rivera' }))
    const ask = await screen.findByRole('dialog', { name: 'Merge filename fragment' })
    expect(ask).toHaveTextContent('Merge “1080p WEB” into “Mia Rivera”?')
    expect(posts(calls, '/api/performers/junk-cleanup')).toHaveLength(0)
    fireEvent.click(within(ask).getByRole('button', { name: 'Merge performer' }))
    await waitFor(() => expect(posts(calls, '/api/performers/junk-cleanup')).toHaveLength(1))
    expect(posts(calls, '/api/performers/junk-cleanup')[0]!.body).toEqual({ performer_id: 7, action: 'merge', target_id: 3 })
    await waitFor(() => expect(toast).toHaveBeenCalledWith('Merged likely fragment “1080p WEB” into “Mia Rivera”.'))
  })

  it('deletes only an unlinked record, after asking', async () => {
    const { calls } = mount(AUDIT, (u, mth) => (u === '/api/performers/junk-cleanup' && mth === 'POST' ? json({ ok: true }) : undefined))
    const d = await ready()
    fireEvent.click(within(within(d).getByRole('region', { name: 'Fragment: 1080p WEB' })).getByRole('button', { name: 'Delete unlinked record' }))
    const ask = await screen.findByRole('dialog', { name: 'Delete unlinked fragment' })
    expect(ask).toHaveTextContent('This cannot be undone.')
    fireEvent.click(within(ask).getByRole('button', { name: 'Delete record' }))
    await waitFor(() => expect(posts(calls, '/api/performers/junk-cleanup')[0]!.body).toEqual({ performer_id: 7, action: 'delete', target_id: null }))
  })

  it('offers no delete for a record with linked videos, and no merge without a target', async () => {
    mount()
    const d = await ready()
    const linked = within(d).getByRole('region', { name: 'Fragment: Part 2' })
    expect(within(linked).queryByRole('button', { name: 'Delete unlinked record' })).toBeNull()
    expect(within(linked).queryByRole('button', { name: /^Merge into/ })).toBeNull()
    expect(linked).toHaveTextContent('Delete disabled · merge linked videos first')
    expect(linked).toHaveTextContent('No safe suggested target found.')
  })
})
