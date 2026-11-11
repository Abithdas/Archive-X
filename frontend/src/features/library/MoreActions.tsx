import { useQueryClient } from '@tanstack/react-query'
import { useChannels } from '@/api/hooks'
import { Dialog } from '@/design/Dialog'
import { Icon } from '@/design/Icon'
import { toast } from 'sonner'
import { ENTER_HINT, PICK_CHANNEL_FIRST } from './bulk'
import { useSelection } from './selection'
import { useLibraryStore } from './store'

function Row({ icon, label, onClick, disabled }: { icon: string; label: string; onClick: () => void; disabled?: boolean }) {
  return (
    <button type="button" onClick={onClick} disabled={disabled} className="flex h-12 w-full items-center gap-3 rounded-xl px-3 text-[15px] font-semibold hover:bg-fg/5 disabled:opacity-50">
      <Icon name={icon} className="text-muted" />
      {label}
    </button>
  )
}

/** "More actions": the classic ⋯ menu entries that work without the bulk-edit mode. */
export function MoreActions({ open, onClose }: { open: boolean; onClose: () => void }) {
  const channels = useChannels()
  const qc = useQueryClient()
  const { selectAll, clear, channels: selected } = useLibraryStore()
  const selecting = useSelection((st) => st.active)
  const done = (msg: string) => {
    toast(msg)
    onClose()
  }
  return (
    <Dialog open={open} onClose={onClose} title="More actions">
      <div className="grid gap-0.5 border-t border-line pt-2">
        <Row icon="grid" label="Select all channels" disabled={!channels.data?.length} onClick={() => { selectAll((channels.data ?? []).map((c) => c.telegram_id)); done('All channels selected') }} />
        <Row icon="close" label="Clear selection" disabled={!selected.length} onClick={() => { clear(); done('Selection cleared') }} />
        <Row icon="edit" label={selecting ? 'Finish editing multiple videos' : 'Edit multiple videos'} onClick={() => {
          const sel = useSelection.getState()
          if (sel.active) { sel.exit(); return done('Selection mode ended') }
          if (!selected.length) { onClose(); return void toast(PICK_CHANNEL_FIRST) }
          sel.enter(); done(ENTER_HINT)
        }} />
        <Row icon="refresh" label="Reload selected" onClick={() => { void qc.invalidateQueries({ queryKey: ['library'] }); void qc.invalidateQueries({ queryKey: ['channels'] }); done('Reloading the library') }} />
      </div>
    </Dialog>
  )
}
