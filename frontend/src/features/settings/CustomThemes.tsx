import { useEffect, useRef, useState } from 'react'
import { Confirm } from '@/design/Confirm'
import { Icon } from '@/design/Icon'
import { Segmented } from '@/design/Segmented'
import { cn } from '@/design/cn'
import { ACTIVE_KEY, FIELDS, NAME_MAX, activeId, createTheme, normalizeTheme, pickerColor, readThemes, upsert, validColor, withVariant, writeThemes, type CustomPalette, type CustomTheme, type Mode, type PaletteKey } from '@/theme/custom'
import { applyTheme, currentPalette, previewPalette, storedTheme } from '@/theme/themes'
import { useAppearance, useAppearanceActions } from './useAppearance'
import { ThemePreview } from './ThemePreview'

const BTN = 'h-10 rounded-xl border border-line-2 px-4 text-sm font-semibold hover:bg-fg/5 disabled:opacity-50'
const PRIMARY = 'h-10 rounded-xl bg-accent px-4 text-sm font-bold text-accent-fg disabled:opacity-60'
const ls = (): Storage | null => { try { return window.localStorage } catch { return null } }
interface Edit { theme: CustomTheme; isNew: boolean; mode: Mode; before: { preset: string; mode: Mode } }

function Chip({ theme, mode, active, onSelect, onDelete }: { theme: CustomTheme; mode: Mode; active: boolean; onSelect: () => void; onDelete: () => void }) {
  const p = theme.palettes[mode]
  return (
    <li className="relative">
      <button type="button" aria-pressed={active} aria-label={theme.name} title={theme.name} onClick={onSelect} className={cn('relative grid w-full gap-2 rounded-2xl border p-2 text-left', active ? 'border-accent bg-accent/10' : 'border-line hover:bg-fg/5')}>
        <ThemePreview c={{ bg: p.bg, surface: p.surface, card: p.surface, thumb: p.hover, text: p.text, muted: p.subtext, accent: p.primary }} />
        <b className="truncate px-1 text-xs">{theme.name}</b>
      </button>
      <button type="button" onClick={onDelete} aria-label={`Delete ${theme.name}`} title="Delete custom theme" className="absolute right-3 top-3 grid size-6 place-items-center rounded-full bg-black/55 text-white hover:bg-black/75"><Icon name="trash" className="size-3" /></button>
    </li>
  )
}

/** Custom themes: saved in this browser, eight colours per light and dark variant, with a live preview while editing. */
export function CustomThemes({ onStatus }: { onStatus: (text: string) => void }) {
  const { preset, mode } = useAppearance()
  const { setCustom, setTheme } = useAppearanceActions()
  const [themes, setThemes] = useState<CustomTheme[]>(() => readThemes(ls()))
  const [edit, setEdit] = useState<Edit | null>(null)
  const [msg, setMsg] = useState<{ text: string; error: boolean }>({ text: '', error: false })
  const [invalid, setInvalid] = useState<Set<PaletteKey>>(new Set())
  const [ask, setAsk] = useState<CustomTheme | null>(null)
  const active = preset === 'custom' ? activeId(ls()) : ''
  const editRef = useRef(edit)
  editRef.current = edit

  const refresh = () => setThemes(readThemes(ls()))
  const say = (text: string, error = false) => setMsg({ text, error })

  // The header's light/dark switch changes the variant being edited.
  useEffect(() => {
    const e = editRef.current
    if (!e || e.mode === mode) return
    const next = withVariant(e.theme, mode)
    setEdit({ ...e, theme: next, mode }); previewPalette(next.palettes[mode], mode); setInvalid(new Set())
  }, [mode])

  const open = (theme: CustomTheme, isNew: boolean) => {
    const before = storedTheme()
    const t = withVariant(theme, mode)
    setEdit({ theme: t, isNew, mode, before }); setInvalid(new Set()); say('Changes preview live. Save and Apply to keep them.')
    previewPalette(t.palettes[mode], mode)
  }
  const create = () => open(createTheme(currentPalette(preset, mode), mode), true)
  const select = (theme: CustomTheme) => { void setCustom(theme, mode).catch(() => undefined); open(theme, false); onStatus(`Custom theme “${theme.name}” is applied across Archive X.`) }
  const draft = edit?.theme

  const setColor = (key: PaletteKey, value: string) => {
    if (!edit) return
    const palette: CustomPalette = { ...edit.theme.palettes[edit.mode], [key]: value }
    setEdit({ ...edit, theme: withVariant(edit.theme, edit.mode, palette) })
    const bad = new Set(invalid)
    if (!validColor(value)) { bad.add(key); setInvalid(bad); say(`Enter a valid CSS color for ${FIELDS.find((f) => f[0] === key)?.[1] ?? key}.`, true); return }
    bad.delete(key); setInvalid(bad)
    say('Live preview active. Save to keep this theme.'); previewPalette(palette, edit.mode)
  }
  const setVariant = (m: Mode) => {
    if (!edit || m === edit.mode) return
    const next = withVariant(edit.theme, m)
    setEdit({ ...edit, theme: next, mode: m }); setInvalid(new Set()); previewPalette(next.palettes[m], m)
  }
  const rename = (name: string) => edit && setEdit({ ...edit, theme: { ...edit.theme, name: name.slice(0, NAME_MAX) } })
  const save = async () => {
    if (!edit) return
    const t = normalizeTheme({ ...edit.theme, name: edit.theme.name.trim() || 'My Theme' })
    if (!t) return void say('One or more color values are invalid. Check the highlighted values.', true)
    if (!writeThemes(ls(), upsert(readThemes(ls()), t))) return void say('Could not save this theme in local storage.', true)
    try { await setCustom(t, edit.mode) } catch { /* local save already worked */ }
    refresh()
    setEdit({ theme: t, isNew: false, mode: edit.mode, before: { preset: 'custom', mode: edit.mode } })
    say('Theme saved and applied across Archive X.'); onStatus(`Custom theme “${t.name}” saved and applied across Archive X.`)
  }
  const cancel = () => {
    const b = edit?.before
    setEdit(null); setInvalid(new Set()); say('')
    if (b) applyTheme(b.preset, b.mode)
    onStatus('Custom theme edit cancelled.')
  }
  const remove = async (t: CustomTheme) => {
    setAsk(null)
    const rest = readThemes(ls()).filter((x) => x.id !== t.id)
    if (!writeThemes(ls(), rest)) return void say('Could not update saved themes.', true)
    const wasActive = activeId(ls()) === t.id
    if (edit?.theme.id === t.id) { setEdit(null); say('') }
    if (wasActive) { try { ls()?.removeItem(ACTIVE_KEY) } catch { /* ignore */ } await setTheme('nord', mode).catch(() => undefined) }
    refresh(); onStatus('Custom theme deleted.')
  }

  return (
    <>
      <section aria-label="Custom themes" className="rounded-[18px] border border-line bg-surface p-5">
        <div className="mb-4 flex items-center justify-between gap-3"><h3 className="text-xl font-extrabold">Custom Themes</h3><span className="text-xs font-semibold text-muted">{themes.length} saved</span></div>
        {themes.length ? (
          <ul className="grid grid-cols-2 gap-3 sm:grid-cols-4" aria-label="Saved custom themes">{themes.map((t) => <Chip key={t.id} theme={t} mode={mode} active={t.id === active || (edit?.theme.id === t.id && preset === 'custom')} onSelect={() => select(t)} onDelete={() => setAsk(t)} />)}</ul>
        ) : <p className="rounded-xl border border-dashed border-line p-4 text-center text-sm text-muted">No custom themes yet. Create one to customize the entire Archive X interface.</p>}
        <button type="button" onClick={create} className={cn(BTN, 'mt-3 inline-flex items-center gap-2')}><span aria-hidden="true">＋</span> Create Theme</button>
        <p className="mt-2 text-xs text-faint">Custom themes are stored in this browser.</p>
      </section>
      {edit && draft ? (
        <section aria-label={edit.isNew ? 'Create Theme' : 'Edit Theme'} className="rounded-[18px] border border-line bg-surface p-5">
          <div className="mb-3 flex items-center justify-between gap-3"><h3 className="text-lg font-extrabold">{edit.isNew ? 'Create Theme' : 'Edit Theme'}</h3>
            {edit.isNew ? null : <button type="button" onClick={() => setAsk(readThemes(ls()).find((x) => x.id === draft.id) ?? draft)} aria-label="Delete custom theme" title="Delete custom theme" className="grid size-9 place-items-center rounded-xl border border-line-2 text-bad hover:bg-fg/5"><Icon name="trash" className="size-4" /></button>}</div>
          <label className="mb-3 grid gap-1 text-xs font-semibold text-muted">Name<input value={draft.name} onChange={(e) => rename(e.target.value)} maxLength={NAME_MAX} autoComplete="off" placeholder="My Theme" className="h-11 rounded-xl border border-line-2 bg-bg px-3 text-sm font-normal text-fg" /></label>
          <div className="mb-3 flex flex-wrap items-center gap-3"><span className="text-xs font-semibold text-muted">Edit variant</span>
            <Segmented<Mode> label="Theme variant" value={edit.mode} onChange={setVariant} options={[{ value: 'dark', label: 'Dark' }, { value: 'light', label: 'Light' }]} /></div>
          <div className="grid gap-3 sm:grid-cols-2">
            {FIELDS.map(([key, label]) => {
              const value = draft.palettes[edit.mode][key]
              return (
                <div key={key} className="grid gap-1 text-xs font-semibold text-muted"><span>{label}</span>
                  <span className="flex gap-2">
                    <input type="color" aria-label={`${label} color picker`} value={pickerColor(value)} onChange={(e) => setColor(key, e.target.value)} className="h-11 w-12 shrink-0 cursor-pointer rounded-xl border border-line-2 bg-bg p-1" />
                    <input type="text" aria-label={`${label} color`} aria-invalid={invalid.has(key) || undefined} value={value} onChange={(e) => setColor(key, e.target.value)} spellCheck={false} className={cn('h-11 min-w-0 flex-1 rounded-xl border bg-bg px-3 font-mono text-sm font-normal text-fg', invalid.has(key) ? 'border-bad' : 'border-line-2')} />
                  </span></div>
              )
            })}
          </div>
          <div className="mt-4 flex flex-wrap justify-end gap-2"><button type="button" className={BTN} onClick={cancel}>Cancel</button><button type="button" className={PRIMARY} disabled={invalid.size > 0} onClick={() => void save()}>Save and Apply</button></div>
          <p role="status" aria-live="polite" className={cn('mt-2 text-sm', msg.error ? 'text-bad' : 'text-muted')}>{msg.text}</p>
        </section>
      ) : null}
      {ask ? <Confirm title="Delete theme?" text={`Delete the custom theme “${ask.name}”? This cannot be undone.`} ok="Delete theme" danger onCancel={() => setAsk(null)} onOk={() => void remove(ask)} /> : null}
    </>
  )
}
