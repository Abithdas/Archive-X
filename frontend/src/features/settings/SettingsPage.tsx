import { useQueryClient } from '@tanstack/react-query'
import { useState } from 'react'
import { toast } from 'sonner'
import { api } from '@/api/client'
import { Icon } from '@/design/Icon'
import { PageHead } from '@/design/PageHead'
import { cn } from '@/design/cn'
import { PANELS, isPanel, validationMessage, type PanelId } from './logic'
import { SecurityPanel } from './SecurityPanel'
import { TelegramPanel } from './TelegramPanel'
import { AppearancePanel, ClassicPanel, ExperiencePanel, OverviewPanel } from './panels'

const KEY = 'ax_next_settings_panel'
const read = (): PanelId => { try { const v = localStorage.getItem(KEY); return isPanel(v) ? v : 'overview' } catch { return 'overview' } }
const settle = <T,>(p: Promise<T>) => p.then((value) => ({ value }), () => ({ value: undefined as T | undefined }))

export function SettingsPage() {
  const [panel, setPanel] = useState<PanelId>(read)
  const [validating, setValidating] = useState(false)
  const qc = useQueryClient()
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
  const current = PANELS.find((p) => p.id === panel)!
  const index = PANELS.findIndex((p) => p.id === panel)
  return (
    <section className="mx-auto w-full max-w-[1800px] pb-10 pl-4 pr-4 pt-6 sm:pl-[58px] sm:pr-10">
      <PageHead eyebrow="Configuration" title="Settings" blurb="Settings are validated before they are applied" />
      <div className="grid items-start gap-5 lg:grid-cols-[256px_minmax(0,1fr)]">
        <nav aria-label="Settings sections" className="grid gap-1">
          <ul className="flex gap-1 overflow-x-auto pb-1 [scrollbar-width:none] lg:grid lg:overflow-visible lg:pb-0">
            {PANELS.map((p) => (
              <li key={p.id} className="shrink-0 lg:shrink">
                <button type="button" aria-current={p.id === panel ? 'page' : undefined} onClick={() => go(p.id)} title={p.blurb} className={cn('flex h-11 w-full items-center gap-3 whitespace-nowrap rounded-xl px-3 text-[15px] font-medium transition-colors', p.id === panel ? 'bg-accent/15 text-fg [&_svg]:text-accent' : 'text-muted hover:bg-fg/5 hover:text-fg')}>
                  <Icon name={p.icon} />{p.title}
                </button>
              </li>
            ))}
          </ul>
          <button type="button" disabled={validating} onClick={() => void validate()} className="mt-3 h-11 rounded-xl border border-line bg-surface text-sm font-bold hover:bg-surface-2 disabled:opacity-60">{validating ? 'Validating…' : 'Validate configuration'}</button>
          <button type="button" onClick={() => { void qc.invalidateQueries({ queryKey: ['settings'] }); void qc.invalidateQueries({ queryKey: ['storage'] }); toast('Status refreshed') }} className="h-11 rounded-xl border border-line bg-surface text-sm font-bold hover:bg-surface-2">Refresh status</button>
        </nav>
        <div className="min-w-0" aria-live="polite">
          {panel === 'overview' ? <OverviewPanel go={go} /> : panel === 'experience' ? <ExperiencePanel /> : panel === 'appearance' ? <AppearancePanel /> : panel === 'telegram' ? <TelegramPanel /> : panel === 'security' ? <SecurityPanel /> : <ClassicPanel title={current.title} blurb={current.blurb} />}
          <p className="mt-2 text-right text-xs text-faint">{String(index + 1).padStart(2, '0')} / {String(PANELS.length).padStart(2, '0')}</p>
        </div>
      </div>
    </section>
  )
}
