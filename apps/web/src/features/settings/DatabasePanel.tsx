import { useQueryClient } from '@tanstack/react-query'
import { useState } from 'react'
import { toast } from 'sonner'
import { ApiError, api } from '@/api/client'
import { Dialog } from '@/design/Dialog'
import { Icon } from '@/design/Icon'
import { formatBytes } from '@/design/format'
import { useDatabaseInfo } from './queries'
import { compactConfirmText, compactResultMessage, databaseNote, databaseRows } from './database'

const BOX = 'rounded-[18px] border border-line bg-surface p-5'
const BTN = 'h-10 rounded-xl border border-line-2 px-4 text-sm font-semibold hover:bg-fg/5 disabled:opacity-50'
const PRIMARY = 'h-11 rounded-xl bg-accent px-4 text-sm font-bold text-accent-fg disabled:opacity-60'

/** Database: its size, the unused space inside it, and a button that compacts it. The app also gives space back by itself while it is idle. */
export function DatabasePanel() {
  const qc = useQueryClient()
  const info = useDatabaseInfo()
  const [asking, setAsking] = useState(false)
  const [busy, setBusy] = useState(false)
  const data = info.data ?? {}

  const compact = async () => {
    setBusy(true)
    try {
      const r = await api<Record<string, unknown>>('/api/settings/database/compact', { method: 'POST', json: {} })
      toast.success(compactResultMessage(r, formatBytes))
    } catch (e) {
      toast.error(e instanceof ApiError ? e.detail || 'Could not compact the database' : 'Could not compact the database')
    } finally {
      setBusy(false)
      setAsking(false)
      void qc.invalidateQueries({ queryKey: ['settings', 'database'] })
    }
  }

  return (
    <section aria-label="Database" className={BOX}>
      <h3 className="mb-4 flex items-center gap-3 text-xl font-extrabold"><span className="text-accent"><Icon name="database" className="size-5" /></span>Database</h3>
      {info.isLoading ? <p className="text-sm text-muted">Reading the database…</p> : (
        <>
          <dl className="grid gap-2 text-sm">
            {databaseRows(data, formatBytes).map((row) => (
              <div key={row.label} className="flex flex-wrap justify-between gap-x-4"><dt className="text-muted">{row.label}</dt><dd className="font-semibold">{row.value}</dd></div>
            ))}
          </dl>
          <p className="mt-3 text-xs text-muted">{databaseNote(data, formatBytes)}</p>
          <div className="mt-4"><button type="button" className={BTN} disabled={busy || data.can_compact === false} onClick={() => setAsking(true)}>Compact database</button></div>
        </>
      )}
      {asking ? (
        <Dialog open onClose={() => (busy ? undefined : setAsking(false))} title="Compact the database?">
          <div className="grid gap-4">
            <p className="whitespace-pre-line text-sm text-muted">{compactConfirmText(data, formatBytes)}</p>
            <div className="flex flex-wrap justify-end gap-2">
              <button type="button" disabled={busy} onClick={() => setAsking(false)} className="h-11 rounded-xl border border-line-2 px-4 text-sm font-semibold disabled:opacity-50">Cancel</button>
              <button type="button" disabled={busy} onClick={() => void compact()} className={PRIMARY}>{busy ? 'Compacting…' : 'Compact'}</button>
            </div>
          </div>
        </Dialog>
      ) : null}
    </section>
  )
}
