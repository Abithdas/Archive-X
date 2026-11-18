import { useQueryClient } from '@tanstack/react-query'
import { useEffect, useMemo, useState } from 'react'
import { toast } from 'sonner'
import { ApiError, api } from '@/api/client'
import { useChannels, useSourceChannels } from '@/api/hooks'
import { Dialog } from '@/design/Dialog'
import { Icon } from '@/design/Icon'
import { Segmented } from '@/design/Segmented'
import { CARD, CHANNEL_LIST, Check, FIELD, Field, Stepper, detail, videosHint } from './parts'
import { ChannelAvatar } from '@/features/channels/ChannelAvatar'
import {
  DEFAULT_LOCAL, DEFAULT_TELEGRAM, ORIENTATIONS, RESOLUTIONS, SNAPSHOT_CHOICES, describeMaxMinutes, localPayload, planPhotoScan, resolveAsk, startMessage, telegramPayload, validateLocal, validateTelegram,
  type LocalOptions, type PhotoPlan, type PhotoScan, type Source, type TelegramOptions,
} from './transfer'

const STEPS = ['Source', 'Options & Checks', 'Review', 'Run'] as const
type Ask = { message: string; scan: PhotoScan; folder: string }

function Select({ value, onChange, options, label }: { value: string; onChange: (v: string) => void; options: ReadonlyArray<readonly [string, string]>; label: string }) {
  return (
    <select value={value} onChange={(e) => onChange(e.target.value)} aria-label={label} className={FIELD}>
      {options.map(([v, l]) => <option key={v} value={v}>{l}</option>)}
    </select>
  )
}

/** Telegram Transfer Manager: Local → Telegram uploads (videos or images) and Telegram → Telegram transfers, as a four-step flow. */
export function TransferWorkflow() {
  const channels = useSourceChannels()
  const library = useChannels()
  const libraryCount = useMemo(() => new Map((Array.isArray(library.data) ? library.data : []).map((c) => [c.telegram_id, c.video_count])), [library.data])
  const qc = useQueryClient()
  const [source, setSource] = useState<Source>('local')
  const [local, setLocal] = useState<LocalOptions>(DEFAULT_LOCAL)
  const [tg, setTg] = useState<TelegramOptions>(DEFAULT_TELEGRAM)
  const [step, setStep] = useState(0)
  const [q, setQ] = useState('')
  const [folderMsg, setFolderMsg] = useState('')
  const [msg, setMsg] = useState('')
  const [busy, setBusy] = useState(false)
  const [ask, setAsk] = useState<Ask | null>(null)
  const chans = channels.data ?? []
  const nameOf = (id: string) => chans.find((c) => c.telegram_id === id)?.title ?? id
  const setL = <K extends keyof LocalOptions>(k: K, v: LocalOptions[K]) => setLocal((o) => ({ ...o, [k]: v }))
  const setT = <K extends keyof TelegramOptions>(k: K, v: TelegramOptions[K]) => setTg((o) => ({ ...o, [k]: v }))
  const problem = source === 'local' ? validateLocal(local) : validateTelegram(tg)
  const canNext = step >= 2 || problem === ''

  // The folder chosen last time comes back pre-filled (classic: loadFolderPreference).
  useEffect(() => {
    let live = true
    api<{ ok?: boolean; path?: string }>('/api/settings/folder-preference?key=transfer_source')
      .then((r) => live && r?.ok && r.path && setLocal((o) => (o.folder ? o : { ...o, folder: r.path ?? '' })))
      .catch(() => undefined)
    return () => { live = false }
  }, [])

  const browse = async () => {
    setFolderMsg('Opening folder picker…')
    try {
      const r = await api<{ cancelled?: boolean; path?: string }>('/api/settings/folder-picker', { method: 'POST', json: { title: 'Select Telegram Transfer source folder', initial_path: local.folder, must_exist: true, allow_create: false } })
      if (r.cancelled) setFolderMsg('Folder selection cancelled.')
      else if (r.path) { setL('folder', r.path); setFolderMsg('Folder selected. Click Save folder to remember it.') } else setFolderMsg('No folder selected.')
    } catch (e) {
      setFolderMsg(detail(e, 'Folder picker unavailable.'))
    }
  }
  const saveFolder = async () => {
    if (!local.folder.trim()) return setFolderMsg('Choose a folder first.')
    setFolderMsg('Saving folder…')
    try {
      const r = await api<{ ok?: boolean; path?: string; detail?: string }>('/api/settings/folder-preference', { method: 'POST', json: { key: 'transfer_source', path: local.folder.trim() } })
      if (!r?.ok) throw new ApiError(0, r?.detail || 'Could not save folder')
      if (r.path) setL('folder', r.path)
      setFolderMsg('Folder saved.')
    } catch (e) {
      setFolderMsg(detail(e, 'Could not save folder.'))
    }
  }

  const finish = (text: string, ok: boolean) => {
    setMsg(text)
    ok ? toast(text) : toast.error(text)
  }
  const start = async (payload: object, count: number | null) => {
    const r = await api<{ id?: string; detail?: string }>('/api/telegram-transfer/start', { method: 'POST', json: payload })
    finish(startMessage(source, r, local.images, count), Boolean(r.id))
    void qc.invalidateQueries({ queryKey: ['lanes'] })
    void qc.invalidateQueries({ queryKey: ['status'] })
  }
  const proceed = async (plan: PhotoPlan) => {
    if (plan.kind === 'error') return finish(plan.message, false)
    if (plan.kind === 'go') await start(localPayload(local, plan.includeSubfolders), plan.count)
  }
  const run = async () => {
    if (problem) return finish(problem, false)
    setBusy(true)
    setMsg('')
    try {
      if (source === 'telegram') return await start(telegramPayload(tg), null)
      if (!local.images) return await start(localPayload(local), null)
      const scan = await api<PhotoScan>('/api/telegram-transfer/scan-photos', { method: 'POST', json: { folder: local.folder.trim() } }).catch((e: unknown): PhotoScan => ({ detail: detail(e, '') }))
      const plan = planPhotoScan(scan, local.folder, local.subfolders)
      if (plan.kind === 'ask') {
        setAsk({ message: plan.message, scan, folder: local.folder })
        return
      }
      await proceed(plan)
    } catch (e) {
      finish(`Could not start: ${detail(e, 'unknown error')}`, false)
    } finally {
      setBusy(false)
    }
  }
  const answer = async (include: boolean) => {
    const a = ask
    if (!a) return
    setAsk(null)
    if (include) setL('subfolders', true)
    setBusy(true)
    try {
      await proceed(resolveAsk(a.scan, a.folder, include))
    } catch (e) {
      finish(`Could not start: ${detail(e, 'unknown error')}`, false)
    } finally {
      setBusy(false)
    }
  }

  const visible = chans.filter((c) => c.title.toLowerCase().includes(q.trim().toLowerCase()) || c.telegram_id.includes(q.trim()))
  const toggle = (id: string) => setT('channels', tg.channels.includes(id) ? tg.channels.filter((x) => x !== id) : [...tg.channels, id])
  const destination = (value: string, onChange: (v: string) => void, label: string) => (
    <Field label={label}>
      <select value={value} onChange={(e) => onChange(e.target.value)} aria-label={label} className={FIELD}>
        <option value="">— select channel —</option>
        {chans.map((c) => <option key={c.telegram_id} value={c.telegram_id}>{c.title}</option>)}
      </select>
    </Field>
  )

  return (
    <section aria-label="Telegram Transfer Manager" className={CARD}>
      <div className="mb-1 flex items-start gap-3"><span className="grid size-10 shrink-0 place-items-center rounded-xl bg-surface-3 text-accent"><Icon name="transfer" /></span>
        <div><h2 className="text-lg font-extrabold">Telegram Transfer Manager</h2><p className="text-sm text-muted">Use one workflow for local video uploads, image-document uploads, or Telegram-to-Telegram transfers.</p></div></div>
      <details className="mt-2 rounded-xl border border-line px-3 py-2 text-sm text-muted"><summary className="cursor-pointer font-semibold text-fg">Workflow details</summary>
        <p className="mt-2">One transfer manager handles both Local → Telegram and Telegram → Telegram. For Local Files, leave <strong>Upload images</strong> unchecked to use the existing video preparation workflow, or check it to send supported images as Telegram documents. Verified image uploads are indexed in Photos; local originals are deleted only after byte-level destination verification when cleanup is enabled.</p></details>
      <Stepper steps={STEPS} step={step} onStep={setStep} canGo={canNext} />

      {step === 0 ? (
        <div className="grid gap-4">
          <div><h3 className="text-[15px] font-bold">1 · Source</h3><p className="text-sm text-muted">Choose where media comes from. The fields change between Local files and Telegram media.</p></div>
          <div className="grid gap-2"><Segmented<Source> label="Transfer source" value={source} onChange={(v) => { setSource(v); setMsg('') }} options={[{ value: 'local', label: 'Local files' }, { value: 'telegram', label: 'Telegram media' }]} />
            <span className="text-xs text-faint">No CLI needed — Archive X runs this job in the background.</span></div>
          {source === 'local' ? (
            <>
              <Field label="Source folder">
                <span className="flex gap-2"><input type="text" value={local.folder} onChange={(e) => setL('folder', e.target.value)} placeholder="Choose a folder or enter its path…" aria-label="Source folder path" autoComplete="off" className={FIELD} />
                  <button type="button" onClick={() => void browse()} className="h-11 shrink-0 rounded-xl border border-line-2 px-3 text-sm font-semibold">Browse…</button>
                  <button type="button" onClick={() => void saveFolder()} className="h-11 shrink-0 rounded-xl border border-line-2 px-3 text-sm font-semibold">Save folder</button></span>
                <span role="status" className="min-h-4 text-[11px] font-normal text-faint">{folderMsg}</span>
              </Field>
              <Check checked={local.images} onChange={(v) => setL('images', v)} title="Upload images" hint="When enabled, supported images are sent to Telegram as documents. Verified images are added to Photos." />
              {destination(local.destination, (v) => setL('destination', v), 'Destination channel')}
            </>
          ) : (
            <>
              <div>
                <p className="mb-1.5 text-xs font-semibold text-muted">Source channel(s)</p>
                <div className="mb-2 flex gap-2"><input type="search" value={q} onChange={(e) => setQ(e.target.value)} placeholder="Search channels..." aria-label="Search channels" className={FIELD} />
                  <button type="button" onClick={() => setT('channels', chans.map((c) => c.telegram_id))} className="h-11 rounded-xl border border-line-2 px-3 text-xs font-bold">All</button>
                  <button type="button" onClick={() => setT('channels', [])} className="h-11 rounded-xl border border-line-2 px-3 text-xs font-bold">Clear</button></div>
                <div role="group" aria-label="Source channels" className={CHANNEL_LIST}>
                  {channels.isPending ? <p className="col-span-full p-3 text-sm text-muted">Loading channels…</p> : visible.length ? visible.map((c) => <Check key={c.telegram_id} checked={tg.channels.includes(c.telegram_id)} onChange={() => toggle(c.telegram_id)} title={c.title} icon={<ChannelAvatar id={c.telegram_id} title={c.title} image={c.image} />} hint={videosHint(libraryCount.get(c.telegram_id) ?? c.video_count)} />) : <p className="col-span-full p-3 text-sm text-muted">No channels match.</p>}
                </div>
                <p className="mt-2 text-xs text-muted" aria-live="polite">Selected: {tg.channels.length} channel{tg.channels.length === 1 ? '' : 's'}</p>
              </div>
              {destination(tg.destination, (v) => setT('destination', v), 'Destination channel')}
            </>
          )}
          <div className="rounded-xl border border-line bg-bg p-3 text-xs text-muted"><b className="block text-sm text-fg">Preparation-first workflow</b>Media is inventoried first. Existing metadata and playback assets are reused; new or incomplete records are prepared and saved before delivery.</div>
        </div>
      ) : step === 1 ? (
        <div className="grid gap-4">
          <div><h3 className="text-[15px] font-bold">2 · Options &amp; Checks</h3></div>
          {source === 'local' ? (
            <>
              {!local.images ? (
                <>
                  <Field label="Video orientation" hint="Uses the displayed orientation, including phone rotation metadata. A specific orientation skips files with unknown dimensions."><Select label="Video orientation for local videos" value={local.orientation} onChange={(v) => setL('orientation', v)} options={ORIENTATIONS} /></Field>
                  <Field label="Source resolution" hint="Filters original files; no re-encoding. A specific tier skips unknown dimensions."><Select label="Source resolution for local videos" value={local.resolution} onChange={(v) => setL('resolution', v)} options={RESOLUTIONS} /></Field>
                  <Field label="Only videos shorter than (minutes)" hint="Leave empty for any length. 10 sends only videos under 10 minutes; a video exactly 10:00 long, or one whose length cannot be read, is left out."><input value={local.maxMinutes} onChange={(e) => setL('maxMinutes', e.target.value)} inputMode="decimal" placeholder="Any length" aria-label="Only local videos shorter than this many minutes" className={FIELD} /></Field>
                </>
              ) : <p className="text-sm text-muted">Image uploads have no video filters.</p>}
              <div className="rounded-xl border border-line p-2">
                <Check checked={local.subfolders} onChange={(v) => setL('subfolders', v)} title="Include subfolders" />
                <Check checked={local.deleteSource} onChange={(v) => setL('deleteSource', v)} title="Delete local source after verified delivery" />
              </div>
              {!local.images ? (
                <Field label="When a file exceeds Telegram's safe upload limit" hint="Optimize keeps one Telegram file but re-encodes when needed. Skip leaves oversized files on your device without uploading them.">
                  <Select label="Oversized file handling" value={local.oversized} onChange={(v) => setL('oversized', v as LocalOptions['oversized'])} options={[['optimize', 'Optimize to fit (may reduce quality; original preserved)'], ['skip', 'Skip oversized files (keep local source)']]} />
                </Field>
              ) : null}
            </>
          ) : (
            <>
              <div className="grid grid-cols-2 gap-3">
                <Field label="Source media"><Select label="Source media" value={tg.sourceMedia} onChange={(v) => setT('sourceMedia', v as 'VIDEO' | 'FILE')} options={[['VIDEO', 'Video'], ['FILE', 'File / document']]} /></Field>
                <Field label="Destination media"><Select label="Destination media" value={tg.destinationMedia} onChange={(v) => setT('destinationMedia', v as 'VIDEO' | 'FILE')} options={[['VIDEO', 'Video'], ['FILE', 'File / document']]} /></Field>
              </div>
              <Field label="Video orientation"><Select label="Video orientation" value={tg.orientation} onChange={(v) => setT('orientation', v)} options={ORIENTATIONS} /></Field>
              <Field label="Source resolution" hint="Filters source videos; no re-encoding. A specific tier skips unknown dimensions."><Select label="Source resolution for Telegram videos" value={tg.resolution} onChange={(v) => setT('resolution', v)} options={RESOLUTIONS} /></Field>
              <Field label="Only videos shorter than (minutes)" hint="Leave empty for any length. 10 moves only videos under 10 minutes; a video exactly 10:00 long, or one whose length Telegram does not report, is left out (and stays in the source)."><input value={tg.maxMinutes} onChange={(e) => setT('maxMinutes', e.target.value)} inputMode="decimal" placeholder="Any length" aria-label="Only Telegram videos shorter than this many minutes" className={FIELD} /></Field>
              <Field label="Videos without saved snapshots" hint="The cover of each transferred video is its 36-frame snapshot set, made when a channel is scanned. A video that has none can be skipped (it stays in the source, and the job lists it) or have the set made from Telegram before it moves.">
                <Select label="Videos without saved snapshots" value={tg.snapshots} onChange={(v) => setT('snapshots', v as TelegramOptions['snapshots'])} options={SNAPSHOT_CHOICES} />
              </Field>
              <div className="rounded-xl border border-line p-2"><p className="px-2 pb-1 pt-1 text-xs font-bold text-muted">Duplicate checks and cleanup</p>
                <Check checked={tg.dupSource} onChange={(v) => setT('dupSource', v)} title="Check source duplicates" />
                <Check checked={tg.dupDestination} onChange={(v) => setT('dupDestination', v)} title="Check destination" />
                <Check checked={tg.dupDb} onChange={(v) => setT('dupDb', v)} title="Check Archive-X DB" />
                <Check checked={tg.cleanup} onChange={(v) => setT('cleanup', v)} title="Cleanup broken/orphaned" />
              </div>
              {tg.cleanup ? destination(tg.trash, (v) => setT('trash', v), 'Trash channel') : null}
            </>
          )}
          {problem ? <p role="alert" className="text-sm text-warn">{problem}</p> : null}
        </div>
      ) : step === 2 ? (
        <div>
          <h3 className="mb-2 text-[15px] font-bold">3 · Review</h3>
          <dl className="grid gap-1.5 rounded-xl bg-bg p-3 text-sm">
            {(source === 'local'
              ? [['Source', 'Local files'], ['Media', local.images ? 'Images (as documents)' : 'Videos'], ['Folder', local.folder || '—'], ['Destination', local.destination ? nameOf(local.destination) : '—'], ['Include subfolders', local.subfolders ? 'Yes' : 'No'], ['Delete local source after delivery', local.deleteSource ? 'Yes' : 'No'], ...(local.images ? [] : [['Orientation', local.orientation], ['Resolution', local.resolution], ['Length', describeMaxMinutes(local.maxMinutes)], ['Oversized files', local.oversized === 'optimize' ? 'Optimize to fit' : 'Skip']])]
              : [['Source', 'Telegram media'], ['From', tg.channels.map(nameOf).join(', ') || '—'], ['Destination', tg.destination ? nameOf(tg.destination) : '—'], ['Media', `${tg.sourceMedia === 'VIDEO' ? 'Video' : 'File'} → ${tg.destinationMedia === 'VIDEO' ? 'Video' : 'File'}`], ['Orientation', tg.orientation], ['Resolution', tg.resolution], ['Duplicate checks', [tg.dupSource && 'source', tg.dupDestination && 'destination', tg.dupDb && 'database'].filter(Boolean).join(', ') || 'none'], ['Cleanup', tg.cleanup ? `Yes → ${nameOf(tg.trash)}` : 'No'], ['Length', describeMaxMinutes(tg.maxMinutes)], ['Without saved snapshots', tg.snapshots === 'generate' ? 'Make them from Telegram first' : 'Skip them (source kept)']]
            ).map(([k, v]) => <div key={k} className="grid grid-cols-[minmax(0,150px)_minmax(0,1fr)] gap-3"><dt className="text-muted">{k}</dt><dd className="font-semibold [overflow-wrap:anywhere]">{v}</dd></div>)}
          </dl>
          {problem ? <p role="alert" className="mt-2 text-sm text-warn">{problem}</p> : <p className="mt-2 text-sm text-muted">Everything needed is set. Continue to Run.</p>}
        </div>
      ) : (
        <div className="grid gap-3">
          <h3 className="text-[15px] font-bold">4 · Run</h3>
          <div><button type="button" disabled={busy} onClick={() => void run()} className="h-11 rounded-xl bg-accent px-6 text-sm font-extrabold text-accent-fg disabled:opacity-60">{busy ? 'Starting…' : source === 'local' ? (local.images ? 'Start image upload' : 'Start local transfer') : 'Start Telegram transfer'}</button></div>
          <p className="min-h-5 text-xs text-muted" aria-live="polite">{msg}</p>
        </div>
      )}
      <div className="mt-auto flex justify-between gap-2 pt-5">
        <button type="button" disabled={step === 0} onClick={() => setStep(step - 1)} className="h-10 rounded-xl border border-line-2 px-4 text-sm font-semibold disabled:opacity-40">Back</button>
        {step < 3 ? <button type="button" disabled={!canNext} onClick={() => setStep(step + 1)} className="h-10 rounded-xl bg-accent px-4 text-sm font-bold text-accent-fg disabled:opacity-50">{['Next: Options & Checks →', 'Next: Review →', 'Next: Run →'][step]}</button> : <span />}
      </div>

      {ask ? (
        <Dialog open onClose={() => setAsk(null)} title="Photos in subfolders">
          <div className="grid grid-cols-[minmax(0,1fr)] gap-4">
            <p className="text-sm text-muted">{ask.message}</p>
            <div className="flex flex-wrap justify-end gap-2">
              <button type="button" onClick={() => void answer(false)} className="h-11 rounded-xl border border-line-2 px-4 text-sm font-semibold">{Number(ask.scan.direct_count) === 0 ? 'Cancel' : 'Only direct photos'}</button>
              <button type="button" onClick={() => void answer(true)} className="h-11 rounded-xl bg-accent px-4 text-sm font-bold text-accent-fg">Include subfolders</button>
            </div>
          </div>
        </Dialog>
      ) : null}
    </section>
  )
}
