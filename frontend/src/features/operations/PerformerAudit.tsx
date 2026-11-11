import { useQueryClient } from '@tanstack/react-query'
import { useCallback, useEffect, useRef, useState } from 'react'
import { toast } from 'sonner'
import { ApiError, api } from '@/api/client'
import { Confirm } from '@/design/Confirm'
import { Dialog } from '@/design/Dialog'
import { cn } from '@/design/cn'
import { AUDITING, AUDIT_FAILED, AUDIT_NOTE, AUTO_FAILED, JUNK_FAILED, MERGE_FAILED, NOTHING_SAFE, NO_GROUPS_NOTE, NO_GROUPS_TITLE, autoConfirm, autoGroups, autoLabel, autoOutcome, autoToast, canMerge, groupFooter, headingFor, identityText, isProtected, junkBody, junkCheck, junkConfirm, junkOutcome, kindInfo, manualConfirm, memberLine, mergeBody, mergeIds, mergedMessage, similarityLabel, targetLine, totalsLine, truncatedNote, type Audit, type AutoResult, type Group, type JunkAction } from './audit'

const BTN = 'h-9 rounded-xl border border-line-2 px-3 text-sm font-semibold hover:bg-fg/5 disabled:opacity-50'
const PRIMARY = 'h-9 rounded-xl bg-accent px-3 text-sm font-bold text-accent-fg disabled:opacity-50'
const reason = (e: unknown, fb: string) => (e instanceof ApiError && e.detail ? e.detail : e instanceof Error && e.message ? e.message : fb)

type Ask = null | { title: string; text: string; ok: string; run: () => Promise<void> }

/** One group of likely duplicates: choose one to Keep and tick the ones to Merge into it. */
function GroupCard({ group, busy, onMerge }: { group: Group; busy: boolean; onMerge: (group: Group, keep: number, ids: number[]) => void }) {
  const [keep, setKeep] = useState<number | null>(null)
  const [checked, setChecked] = useState<Set<number>>(new Set())
  const info = kindInfo(group.kind)
  const locked = isProtected(group)
  const toggle = (id: number) => setChecked((s) => { const n = new Set(s); if (n.has(id)) n.delete(id); else n.add(id); return n })
  return (
    <section className="rounded-2xl border border-line p-3.5" aria-label={group.title || info.title}>
      <div className="flex flex-wrap items-start justify-between gap-3">
        <div className="min-w-0 flex-1">
          <h5 className="text-sm font-bold">{group.title || info.title}{similarityLabel(group)}</h5>
          <p className="mt-1 text-[11px] leading-snug text-muted">{group.reason || info.note}</p>
          <p className="mt-1 text-[11px] text-accent">Recommended treatment: {group.recommended_treatment || info.treatment}</p>
        </div>
        {locked ? null : <button type="button" disabled={busy || !canMerge(keep, checked)} onClick={() => keep !== null && onMerge(group, keep, mergeIds(keep, checked))} className={PRIMARY}>Merge checked</button>}
      </div>
      <ul className="mt-3 grid gap-2">
        {(group.performers ?? []).map((p) => (
          <li key={p.id} className="flex flex-wrap items-center justify-between gap-3 rounded-xl bg-bg p-3">
            <div className="min-w-0">
              <b className="block truncate text-sm">{p.name || 'Unnamed performer'}</b>
              <span className="block text-xs text-muted">{memberLine(p)}</span>
              <small className="block text-[11px] text-faint [overflow-wrap:anywhere]">{identityText(p)}</small>
              {p.aliases?.length ? <small className="block text-[11px] text-faint [overflow-wrap:anywhere]">Aliases: {p.aliases.join(', ')}</small> : null}
            </div>
            {locked ? <span className="text-xs text-muted">Protected fallback</span> : (
              <div className="flex gap-4 text-sm">
                <label className="inline-flex items-center gap-1.5"><input type="radio" name={`keep-${group.kind}-${group.performers?.[0]?.id}`} checked={keep === p.id} onChange={() => setKeep(p.id)} aria-label={`Keep ${p.name}`} />Keep</label>
                <label className="inline-flex items-center gap-1.5"><input type="checkbox" checked={checked.has(p.id) && keep !== p.id} disabled={keep === p.id} onChange={() => toggle(p.id)} aria-label={`Merge ${p.name}`} />Merge</label>
              </div>
            )}
          </li>
        ))}
      </ul>
      <p className="mt-2 text-[11px] text-muted">{groupFooter(group)}</p>
    </section>
  )
}

/** A record that looks like part of a filename or release title, with a safe suggested target. */
function Fragment({ group, busy, onAct }: { group: Group; busy: boolean; onAct: (g: Group, a: JunkAction) => void }) {
  const p = (group.performers ?? [])[0]
  if (!p) return null
  const target = group.suggested_target
  const linked = Number(p.video_count || 0) > 0
  return (
    <section className="rounded-2xl border border-warn/60 p-3.5" aria-label={`Fragment: ${p.name}`}>
      <h5 className="text-sm font-bold">Likely filename/title fragment: {p.name || 'Unnamed performer'}</h5>
      <p className="mt-1 text-xs text-muted">{memberLine(p)}</p>
      <p className="mt-1 text-[11px] text-muted">{group.reason || 'This performer name resembles a release tag or scene-title fragment.'}</p>
      {target ? <div className="my-2.5 rounded-xl border border-line bg-bg p-2.5"><small className="text-muted">Suggested merge target</small><div className="flex items-center justify-between gap-2"><b>{target.name || 'Performer'}</b><span className="text-xs text-muted">{targetLine(target)}</span></div></div>
        : <p className="my-2.5 text-xs text-muted">No safe suggested target found. Review manually before changing this record.</p>}
      <div className="flex flex-wrap items-center gap-2">
        {target && target.id !== p.id ? <button type="button" disabled={busy} onClick={() => onAct(group, 'merge')} className={PRIMARY}>Merge into {target.name || 'target'}</button> : null}
        {!linked ? <button type="button" disabled={busy} onClick={() => onAct(group, 'delete')} className={BTN}>Delete unlinked record</button> : <span className="text-[11px] text-muted">Delete disabled · merge linked videos first</span>}
      </div>
    </section>
  )
}

/** Performer duplicate review. Scanning is read-only; every merge or delete asks first and is a separate, explicit action. */
export function PerformerAudit({ onClose }: { onClose: () => void }) {
  const qc = useQueryClient()
  const [data, setData] = useState<Audit | null>(null)
  const [status, setStatus] = useState(AUDITING)
  const [outcome, setOutcome] = useState('')
  const [loading, setLoading] = useState(true)
  const [busy, setBusy] = useState(false)
  const [ask, setAsk] = useState<Ask>(null)
  const alive = useRef(true)
  useEffect(() => { alive.current = true; return () => { alive.current = false } }, [])

  const scan = useCallback(async () => {
    setLoading(true); setStatus(AUDITING)
    try {
      const r = await api<Audit>('/api/performers/duplicate-audit')
      if (!alive.current) return
      if (r?.ok !== true) throw new ApiError(0, r?.detail || AUDIT_FAILED)
      setData(r); setStatus(totalsLine(r))
    } catch (e) { if (alive.current) { setData(null); setStatus(reason(e, AUDIT_FAILED)) } } finally { if (alive.current) setLoading(false) }
  }, [])
  useEffect(() => { void scan() }, [scan])
  const changed = () => { void qc.invalidateQueries({ queryKey: ['entities'] }); void qc.invalidateQueries({ queryKey: ['performer'] }) }

  const merge = (group: Group, keepId: number, ids: number[]) => {
    const c = manualConfirm(group, keepId, ids)
    if (!c.ok) return void toast(c.message)
    const keepName = (group.performers ?? []).find((p) => p.id === keepId)?.name ?? ''
    setAsk({ title: c.title, text: c.text, ok: c.okLabel, run: async () => {
      setBusy(true); setStatus(`Merging ${ids.length} selected performer record(s)…`)
      try {
        const r = await api<{ ok?: boolean; merged_count?: number; name?: string; detail?: string }>('/api/performers/merge-batch', { method: 'POST', json: mergeBody(keepId, ids) })
        if (!r?.ok) throw new ApiError(0, r?.detail || MERGE_FAILED)
        changed(); await scan(); toast(mergedMessage(r, ids.length, keepName))
      } catch (e) { setStatus(reason(e, MERGE_FAILED)) } finally { setBusy(false) }
    } })
  }

  const auto = () => {
    if (!data) return
    const c = autoConfirm(data)
    if (!c.eligible) return void toast(NOTHING_SAFE)
    setAsk({ title: c.title, text: c.text, ok: c.okLabel, run: async () => {
      setBusy(true); setStatus(`Safely merging ${c.rows} duplicate performer record(s)…`)
      try {
        const r = await api<AutoResult>('/api/performers/duplicate-audit/auto-cleanup', { method: 'POST', json: {} })
        if (r?.ok !== true) throw new ApiError(0, r?.detail || AUTO_FAILED)
        changed()
        const o = autoOutcome(r)
        setOutcome(o)
        if (r.audit) { setData(r.audit); setStatus(`${o} ${totalsLine(r.audit)}`) } else await scan()
        toast(autoToast(r))
      } catch (e) { const m = reason(e, AUTO_FAILED); setOutcome(m); toast(m) } finally { setBusy(false) }
    } })
  }

  const junk = (group: Group, action: JunkAction) => {
    const bad = junkCheck(group, action)
    if (bad) return void toast(bad)
    const p = group.performers![0]!
    const c = junkConfirm(group, action)
    const targetName = group.suggested_target?.name || 'the selected target'
    setAsk({ title: c.title, text: c.text, ok: c.okLabel, run: async () => {
      setBusy(true)
      try {
        const r = await api<{ ok?: boolean; name?: string; detail?: string }>('/api/performers/junk-cleanup', { method: 'POST', json: junkBody(p.id, action, action === 'merge' ? group.suggested_target!.id : null) })
        if (!r?.ok) throw new ApiError(0, r?.detail || JUNK_FAILED)
        const o = junkOutcome(action, p.name ?? '', r, targetName)
        setOutcome(o); changed(); await scan(); toast(o)
      } catch (e) { toast(reason(e, JUNK_FAILED)) } finally { setBusy(false) }
    } })
  }

  const groups = data?.groups ?? []
  const eligible = data ? autoGroups(data).length : 0
  let previous = ''
  return (
    <Dialog wide open onClose={() => !busy && onClose()} title="Duplicate performers"
      footer={<div className="flex flex-wrap items-center justify-end gap-2 border-t border-line pt-3"><button type="button" disabled={busy || loading} onClick={() => void scan()} className={BTN}>Run audit again</button><button type="button" disabled={busy || loading || !eligible} onClick={auto} className={PRIMARY}>{autoLabel(eligible)}</button></div>}>
      <div className="grid gap-3 text-left">
        <p role="status" aria-live="polite" className="text-xs text-muted">{outcome && data && !loading && !status.startsWith(outcome) ? `${outcome} ${status}` : status}</p>
        {loading ? <div className="rounded-2xl border border-line p-3.5 text-sm text-muted">{AUDIT_NOTE}</div>
          : !data ? null
          : !groups.length ? <div className="rounded-2xl border border-line p-3.5"><b>{NO_GROUPS_TITLE}</b><p className="mt-1 text-xs text-muted">{NO_GROUPS_NOTE}</p></div>
          : groups.map((g, i) => {
            const head = g.kind !== previous ? (previous = g.kind, <h4 key={`h${i}`} className="mt-2 text-xs font-bold uppercase tracking-wide text-muted">{headingFor(g.kind)}</h4>) : null
            return <div key={i} className={cn('grid gap-3')}>{head}{g.kind === 'junk_fragment' ? <Fragment group={g} busy={busy} onAct={junk} /> : <GroupCard group={g} busy={busy} onMerge={merge} />}</div>
          })}
        {data && truncatedNote(data) ? <p className="text-[11px] text-muted">{truncatedNote(data)}</p> : null}
        {data && groups.length ? <p className="text-[11px] text-muted">Records without reliable identity evidence are not paired and should be kept separate.</p> : null}
      </div>
      {ask ? <Confirm title={ask.title} text={ask.text} ok={ask.ok} danger onCancel={() => setAsk(null)} onOk={() => { const run = ask.run; setAsk(null); void run() }} /> : null}
    </Dialog>
  )
}
