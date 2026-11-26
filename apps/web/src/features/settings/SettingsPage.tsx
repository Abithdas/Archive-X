import { useQueryClient } from '@tanstack/react-query'
import { useRef, useState } from 'react'
import { toast } from 'sonner'
import { api } from '@/api/client'
import { Icon } from '@/design/Icon'
import { PageHead } from '@/design/PageHead'
import { cn } from '@/design/cn'
import { useScrollRow } from '@/design/useScrollRow'
import { LocalFolders } from '@/features/content/LocalFolders'
import { useContentMode } from '@/features/content/useContentMode'
import { PANELS, isPanel, validationMessage, type PanelId } from './logic'
import { SecurityPanel } from './SecurityPanel'
import { StoragePanel } from './StoragePanel'
import { TelegramPanel } from './TelegramPanel'
import { AddonsPanel } from './AddonsPanel'
import { MetadataPanel } from './MetadataPanel'
import { SystemPanel } from './SystemPanel'
import { AppearancePanel, ExperiencePanel, OverviewPanel } from './panels'

const KEY = 'ax_next_settings_panel'
const read = (): PanelId => { try { const v = localStorage.getItem(KEY); return isPanel(v) ? v : 'overview' } catch { return 'overview' } }
const settle = <T,>(p: Promise<T>) => p.then((value) => ({ value }), () => ({ value: undefined as T | undefined }))

export function SettingsPage() {
  const [panel, setPanel] = useState<PanelId>(read)
  const [validating, setValidating] = useState(false)
  const sfw = useContentMode().data?.mode === 'sfw'
  const panels = PANELS.filter((p) => p.id !== 'addons' || sfw)   // add-ons are an SFW feature: no tab (and no panel) in NSFW
  const shown: PanelId = panel === 'addons' && !sfw ? 'overview' : panel
  const qc = useQueryClient()
  const row = useRef<HTMLUListElement>(null)
  useScrollRow(row)   // in a narrow window the sections are one row that scrolls sideways: the mouse wheel and a drag move it
  const go = (p: PanelId) => { setPanel(p); try { localStorage.setItem(KEY, p) } catch { /* private mode */ } }
  const validate = async () => {
    setValidating(true)
    try {
      const [telegram, metadata, web, preflight] = await Promise.all([settle(api<Record<string, unknown>>('/api/settings/telegram')), settle(api<Record<string, unknown>>('/api/settings/metadata')), settle(api<Record<string, unknown>>('/api/settings/weblogin')), settle(api<Record<string, unknown>>('/api/media-manager/preflight'))])
      toast(validationMessage({ telegram: telegram.value, metadata: metadata.value, web: web.value, preflight: preflight.value }), { duration: 9000 })
      await qc.invalidateQueries({ queryKey: ['settings'] })
    } finally {
      setValidating(false)
    }
  }
  const index = panels.findIndex((p) => p.id === shown)
  return (
    <section className="w-full px-4 pb-10 pt-6 sm:px-10">
      <PageHead eyebrow="Configuration" title="Settings" blurb="Settings are validated before they are applied" />
      <div className="grid items-start gap-5 lg:grid-cols-[256px_minmax(0,1fr)]">
        <nav aria-label="Settings sections" className="grid gap-1 lg:sticky lg:top-20 lg:self-start">
          <ul ref={row} className="flex gap-1 overflow-x-auto pb-1 [scrollbar-width:none] lg:grid lg:overflow-visible lg:pb-0">
            {panels.map((p) => (
              <li key={p.id} className="shrink-0 lg:shrink">
                <button type="button" aria-current={p.id === shown ? 'page' : undefined} onClick={() => go(p.id)} title={p.blurb} className={cn('flex h-11 w-full items-center gap-3 whitespace-nowrap rounded-xl px-3 text-[15px] font-medium transition-colors', p.id === shown ? 'bg-accent/15 text-fg [&_svg]:text-accent' : 'text-muted hover:bg-fg/5 hover:text-fg')}>
                  <Icon name={p.icon} />{p.title}
                </button>
              </li>
            ))}
          </ul>
          <button type="button" disabled={validating} onClick={() => void validate()} className="mt-3 h-11 rounded-xl border border-line bg-surface text-sm font-bold hover:bg-surface-2 disabled:opacity-60">{validating ? 'Validating…' : 'Validate configuration'}</button>
          <button type="button" onClick={() => { void qc.invalidateQueries({ queryKey: ['settings'] }); void qc.invalidateQueries({ queryKey: ['storage'] }); toast('Status refreshed') }} className="h-11 rounded-xl border border-line bg-surface text-sm font-bold hover:bg-surface-2">Refresh status</button>
        </nav>
        <div className="min-w-0" aria-live="polite">
          {shown === 'overview' ? <OverviewPanel go={go} /> : shown === 'experience' ? <ExperiencePanel /> : shown === 'appearance' ? <AppearancePanel /> : shown === 'telegram' ? <TelegramPanel /> : shown === 'local' ? <LocalFolders /> : shown === 'security' ? <SecurityPanel /> : shown === 'storage' ? <StoragePanel /> : shown === 'metadata' ? <MetadataPanel /> : shown === 'addons' ? <AddonsPanel /> : shown === 'system' ? <SystemPanel /> : null}
          <p className="mt-2 text-right text-xs text-faint">{String(index + 1).padStart(2, '0')} / {String(panels.length).padStart(2, '0')}</p>
        </div>
      </div>
    </section>
  )
}
