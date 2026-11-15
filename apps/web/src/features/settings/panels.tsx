import { useQueryClient } from '@tanstack/react-query'
import { useEffect, useState, type ReactNode } from 'react'
import { toast } from 'sonner'
import { ApiError, api } from '@/api/client'
import { useStorage } from '@/api/hooks'
import { Icon } from '@/design/Icon'
import { ThemePreview } from './ThemePreview'
import { Segmented } from '@/design/Segmented'
import { Switch } from '@/design/Switch'
import { cn } from '@/design/cn'
import { formatBytes } from '@/design/format'
import { THEMES, SCALE_MAX, SCALE_MIN, SCALE_PRESETS, type Mode } from '@/theme/themes'
import { AUTONEXT_MODES, experienceFrom, pills, statusRows, type Experience, type PanelId } from './logic'
import { useAuthStatus, useExperienceSettings, useIconsStatus, useMetadataSettings, usePreflight, useWebLogin } from './queries'
import { CustomThemes } from './CustomThemes'
import { useAppearance, useAppearanceActions } from './useAppearance'
import { Disclosure } from '@/design/Accordion'

const BOX = 'rounded-[18px] border border-line bg-surface p-5'
function Head({ icon, title, count, aside }: { icon: string; title: string; count: string; aside?: ReactNode }) {
  return (
    <div className="mb-4 flex items-center justify-between gap-3">
      <h3 className="flex items-center gap-3 text-xl font-extrabold"><span className="text-accent"><Icon name={icon} className="size-5" /></span>{title}</h3>
      <span className="text-xs text-faint">{aside ?? count}</span>
    </div>
  )
}

export function OverviewPanel({ go }: { go: (p: PanelId) => void }) {
  const auth = useAuthStatus(), pre = usePreflight(), icons = useIconsStatus(), meta = useMetadataSettings(), web = useWebLogin(), storage = useStorage()
  const checks = { auth: auth.data, preflight: pre.data, icons: icons.data, metadata: meta.data, web: web.data, storage: storage.data as Record<string, unknown> | undefined }
  const rows = statusRows(checks, formatBytes)
  const tone = (t: string) => (t === 'ok' ? 'bg-good/15 text-good' : t === 'warn' ? 'bg-warn/15 text-warn' : 'bg-surface-3 text-muted')
  return (
    <div className="grid gap-4">
      <section aria-label="Connection status" className={BOX}>
        <div className="mb-3 flex items-center justify-between"><h3 className="text-xl font-extrabold">Connection status</h3><span className="text-xs text-faint">01 / 08</span></div>
        <ul className="mb-3 flex flex-wrap gap-2">{pills(checks).map((p) => <li key={p.label} className={cn('inline-flex items-center gap-2 rounded-full px-3 py-1.5 text-xs font-bold', p.ok ? 'bg-good/15 text-good' : 'bg-warn/15 text-warn')}><i className="size-1.5 rounded-full bg-current" />{p.label}</li>)}</ul>
        <p className="text-sm text-muted">Configuration changes are applied through the settings below; live jobs remain in Operations.</p>
      </section>
      <section aria-label="Status" className={BOX}>
        <Head icon="operations" title="Status" count="Runtime checks" />
        <ul>
          {rows.map((r) => {
            const inner = (
              <>
                <span className="grid size-9 shrink-0 place-items-center rounded-xl bg-surface-3 text-accent"><Icon name={r.icon} className="size-4" /></span>
                <span className="min-w-0 flex-1 text-left"><b className="block text-[15px]">{r.title}</b><small className="block truncate text-xs text-muted" title={r.sub}>{r.sub}</small></span>
                <span className={cn('rounded-full px-3 py-1 text-[11px] font-bold', tone(r.tone))}>{r.label}</span>
              </>
            )
            return <li key={r.title} className="border-b border-line last:border-0">{r.go ? <button type="button" onClick={() => go(r.go!)} className="flex w-full items-center gap-3 py-3 hover:bg-fg/5">{inner}</button> : <div className="flex items-center gap-3 py-3">{inner}</div>}</li>
          })}
        </ul>
        <Disclosure className="mt-2 border-t border-line pt-3" summaryClassName="text-sm font-bold" summary="Details · raw runtime report">
          <pre className="mt-2 max-h-64 overflow-auto rounded-xl bg-bg p-3 text-xs text-muted">{JSON.stringify({ telegram: auth.data, dependencies: pre.data, icons: icons.data }, null, 2)}</pre></Disclosure>
      </section>
    </div>
  )
}

function Row({ title, hint, children }: { title: string; hint?: string; children: ReactNode }) {
  return <div className="flex items-center justify-between gap-4 border-b border-line py-3"><div className="min-w-0"><b className="block text-[15px]">{title}</b>{hint ? <small className="text-xs text-muted">{hint}</small> : null}</div>{children}</div>
}

export function ExperiencePanel() {
  const server = useExperienceSettings()
  const qc = useQueryClient()
  const [form, setForm] = useState<Experience>(experienceFrom(undefined))
  const [msg, setMsg] = useState('')
  const [busy, setBusy] = useState(false)
  useEffect(() => { if (server.data) setForm(experienceFrom(server.data)) }, [server.data])
  const set = <K extends keyof Experience>(k: K, v: Experience[K]) => { setForm((f) => ({ ...f, [k]: v })); setMsg('') }
  const save = async () => {
    setBusy(true)
    try {
      const r = await api<{ detail?: string }>('/api/settings/experience', { method: 'POST', json: form })
      if ((r as { ok?: boolean })?.ok === false) throw new ApiError(0, r.detail || 'save failed')
      setMsg('Saved.')
      toast('Experience settings saved')
      await qc.invalidateQueries({ queryKey: ['settings', 'experience'] })
      setTimeout(() => setMsg(''), 1800)
    } catch (e) {
      setMsg(e instanceof ApiError ? e.detail || 'Could not save.' : 'Could not save.')
    } finally {
      setBusy(false)
    }
  }
  return (
    <section aria-label="Playback" className={BOX}>
      <Head icon="play" title="Playback" count="04 / 08" />
      {server.isError ? <p role="alert" className="mb-3 text-sm text-warn">Could not load the saved settings.</p> : null}
      <Row title="Autoplay" hint="Start videos as soon as they open"><Switch label="Autoplay" checked={form.autoplay} onChange={(v) => set('autoplay', v)} /></Row>
      <Row title="Autoplay next" hint="Continue with the next video when one ends"><Switch label="Autoplay next" checked={form.autonext} onChange={(v) => set('autonext', v)} /></Row>
      <Row title="Ask to resume playback" hint="Offer to continue where you left off"><Switch label="Ask to resume playback" checked={form.resume_prompt} onChange={(v) => set('resume_prompt', v)} /></Row>
      <Row title="Live seek preview (timeline hover)" hint="Show frames while scrubbing"><Switch label="Live seek preview" checked={form.live_seek_preview} onChange={(v) => set('live_seek_preview', v)} /></Row>
      <p className="py-2 text-xs text-muted">Hover the playback timeline to preview a frame. On touch screens, drag to seek.</p>
      <Row title="Ask about partial cache on close"><Switch label="Ask about partial cache on close" checked={form.partial_cache_prompt} onChange={(v) => set('partial_cache_prompt', v)} /></Row>
      <p className="py-2 text-xs text-muted">Off keeps cached bytes automatically without showing the close popup.</p>
      <label className="mt-2 grid gap-2 text-center text-sm font-semibold">Autoplay source
        <select value={form.autonext_mode} onChange={(e) => set('autonext_mode', e.target.value)} aria-label="Autoplay source" className="h-12 w-full rounded-xl border border-line-2 bg-bg px-3 text-left text-[15px] font-medium">
          {AUTONEXT_MODES.map(([k, l]) => <option key={k} value={k}>{l}</option>)}
        </select>
      </label>
      <p className="mt-4 text-xs text-muted">Continue Watching, play position, play count, markers, favorites and recommendation shelves are stored locally. Provider APIs are used for enrichment rather than page rendering.</p>
      <p className="mt-3 text-xs text-muted">Playback QoS prioritizes the browser requested byte range, keeps a short bitrate-based read-ahead window, and preloads only the next queued video prefix at background priority.</p>
      <div className="mt-4 flex items-center gap-3">
        <button type="button" onClick={() => void server.refetch()} className="h-11 rounded-xl border border-line-2 px-4 text-sm font-semibold">Reload</button>
        <button type="button" disabled={busy} onClick={() => void save()} className="h-11 rounded-xl bg-accent px-5 text-sm font-bold text-accent-fg disabled:opacity-60">{busy ? 'Saving…' : 'Save experience settings'}</button>
        <span role="status" className="text-sm text-muted">{msg}</span>
      </div>
    </section>
  )
}

const SIZE_NOTES: Record<number, string> = { 70: 'Fits more on screen', 100: 'The standard size', 130: 'Larger and easier to read' }

export function AppearancePanel() {
  const { preset, mode, scale } = useAppearance()
  const { setTheme, setScale, previewScale } = useAppearanceActions()
  const [status, setStatus] = useState('Appearance preferences are saved automatically.')
  const save = async (fn: () => Promise<void>, ok: string) => {
    try { await fn(); setStatus(ok) } catch { setStatus('Applied locally; server save failed.') }
  }
  return (
    <div className="grid gap-4">
      <section aria-label="Themes" className={BOX}>
        <div className="mb-1 flex flex-wrap items-center justify-between gap-3">
          <h3 className="flex items-center gap-3 text-xl font-extrabold"><span className="text-accent"><Icon name="sparkle" className="size-5" /></span>Themes</h3>
          <Segmented<Mode> label="Theme mode" value={mode} onChange={(m) => void save(() => setTheme(preset, m), `Theme: ${m === 'dark' ? 'Dark' : 'Light'}. Saved.`)} options={[{ value: 'dark', label: 'Dark' }, { value: 'light', label: 'Light' }]} />
        </div>
        <p className="mb-4 text-sm text-muted">Pick a look for the whole app. Every theme comes in a dark and a light version; the switch above changes between them.</p>
        <ul className="grid grid-cols-2 gap-3.5 md:grid-cols-3 xl:grid-cols-4" aria-label="Theme presets">
          {THEMES.map((t) => {
            const p = t[mode]
            const on = preset === t.id
            return (
              <li key={t.id}>
                <button type="button" aria-pressed={on} aria-label={t.name} onClick={() => void save(() => setTheme(t.id, mode), `Theme: ${t.name}. Saved.`)}
                  className={cn('group relative grid w-full gap-2.5 rounded-2xl border p-2.5 text-left transition-[border-color,box-shadow,transform] duration-150 hover:-translate-y-0.5', on ? 'border-accent shadow-[0_0_0_1px_var(--color-accent)]' : 'border-line-2 hover:border-fg/30')}>
                  <ThemePreview c={{ bg: p.bg, surface: p.surface, card: p.surface2, thumb: p.surface3, text: p.text, muted: p.text3, accent: p.accent }} />
                  <span className="flex items-center justify-between gap-2 px-1">
                    <b className="truncate text-[13px]">{t.name}</b>
                    {on ? <span className="inline-flex items-center gap-1 text-[11px] font-bold text-accent"><Icon name="check" className="size-3" />In use</span> : <span className="text-[11px] text-faint opacity-0 group-hover:opacity-100">Use</span>}
                  </span>
                </button>
              </li>
            )
          })}
        </ul>
        <p className="mt-3 text-xs text-muted" role="status">{status}</p>
      </section>

      <section aria-label="Appearance" className={BOX}>
        <Head icon="grid" title="Display size" count="How large everything is" />
        <div className="mb-4 grid grid-cols-3 gap-2.5" role="group" aria-label="Application zoom level">
          {SCALE_PRESETS.map((p) => (
            <button key={p.id} type="button" aria-pressed={scale === p.value} onClick={() => void save(() => setScale(p.value), `Application zoom: ${p.value}%. Saved.`)}
              className={cn('rounded-2xl border px-3 py-3.5 text-center transition-colors', scale === p.value ? 'border-accent bg-accent/10' : 'border-line-2 hover:bg-fg/5')}>
              <b className="block text-sm">{p.label}</b>
              <small className="text-xs text-muted">{p.value}%</small>
              <small className="mt-0.5 hidden text-[11px] text-faint sm:block">{SIZE_NOTES[p.value]}</small>
            </button>
          ))}
        </div>
        <div className="rounded-2xl border border-line bg-bg p-4">
          <div className="mb-1 flex items-baseline justify-between"><label htmlFor="ui-scale" className="text-sm font-semibold">Fine-tune</label><b className="text-xl tabular-nums" aria-live="polite">{scale}%</b></div>
          <input id="ui-scale" type="range" min={SCALE_MIN} max={SCALE_MAX} step={1} value={scale} aria-valuetext={`${scale} percent`} onChange={(e) => previewScale(Number(e.target.value))} onPointerUp={(e) => void save(() => setScale(Number((e.target as HTMLInputElement).value)), `Application zoom saved at ${(e.target as HTMLInputElement).value}%.`)} onKeyUp={(e) => { if (e.key.startsWith('Arrow') || e.key === 'Home' || e.key === 'End') void save(() => setScale(scale), `Application zoom saved at ${scale}%.`) }} aria-label="Interface scale" className="w-full accent-[var(--color-accent)]" />
          <div className="mt-1 flex justify-between text-[11px] text-faint" aria-hidden="true">{[70, 100, 130].map((n) => <span key={n}>{n}%</span>)}</div>
        </div>
        <p className="mt-3 flex flex-wrap items-center gap-x-2 gap-y-1 text-xs text-muted">Shortcuts: <kbd className="rounded border border-line-2 px-1.5">Ctrl</kbd> <kbd className="rounded border border-line-2 px-1.5">−</kbd> / <kbd className="rounded border border-line-2 px-1.5">+</kbd>, reset with <kbd className="rounded border border-line-2 px-1.5">Ctrl</kbd> <kbd className="rounded border border-line-2 px-1.5">0</kbd>
          {scale !== 100 ? <button type="button" onClick={() => void save(() => setScale(100), 'Application zoom: 100%. Saved.')} className="font-semibold text-accent underline">Reset to 100%</button> : null}</p>
      </section>

      <CustomThemes onStatus={setStatus} />
    </div>
  )
}
