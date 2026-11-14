import { THEMES, type Mode, type Palette, type ThemeFamily } from './palettes'
import { ACTIVE_KEY, customTokens, findTheme, activeId, toRgb, type CustomPalette, type CustomTheme } from './custom'

export { THEMES, type Mode, type Palette, type ThemeFamily }
const DEFAULT_PRESET = 'nord'
const PRESET_KEY = 'ax_theme_preset'
const MODE_KEY = 'ax_theme_mode'
const SCALE_KEY = 'ax_ui_scale'
const GRID_KEY = 'ax_grid_density'

/** Unknown or retired ids (and the classic "custom" theme) fall back to the default, like the classic normaliser. */
export const normalizePreset = (id: unknown): string => (THEMES.some((t) => t.id === id) ? String(id) : DEFAULT_PRESET)
export const normalizeMode = (m: unknown): Mode => (m === 'light' ? 'light' : 'dark')
export const familyOf = (id: unknown): ThemeFamily => THEMES.find((t) => t.id === normalizePreset(id)) ?? THEMES[0]!

/** CSS variables for a palette: the same names the Tailwind tokens read (--color-*). */
export function tokens(p: Palette): Record<string, string> {
  return {
    '--color-bg': p.bg, '--color-surface': p.surface, '--color-surface-2': p.surface2, '--color-surface-3': p.surface3,
    '--color-fg': p.text, '--color-muted': p.text2, '--color-faint': p.text3, '--color-accent': p.accent, '--color-accent-fg': p.onAccent,
    '--color-good': p.ok, '--color-warn': p.warn, '--color-bad': p.bad,
    '--color-line': `color-mix(in srgb, ${p.text} 7%, transparent)`, '--color-line-2': `color-mix(in srgb, ${p.text} 12%, transparent)`,
  }
}

const read = (key: string): string | null => { try { return localStorage.getItem(key) } catch { return null } }
const write = (key: string, value: string) => { try { localStorage.setItem(key, value) } catch { /* private mode */ } }

const store = (): Storage | null => { try { return window.localStorage } catch { return null } }
const CUSTOM = 'custom'
/** The custom theme chosen in this browser, if one is active and still stored. */
const activeCustom = (): CustomTheme | null => { const s = store(); const id = activeId(s); return id ? findTheme(s, id) : null }

export function storedTheme(): { preset: string; mode: Mode } {
  const preset = read(PRESET_KEY) === CUSTOM && activeCustom() ? CUSTOM : normalizePreset(read(PRESET_KEY))
  return { preset, mode: normalizeMode(read(MODE_KEY)) }
}

function setTokens(vars: Record<string, string>, m: Mode, preset: string, root: HTMLElement) {
  for (const [k, v] of Object.entries(vars)) root.style.setProperty(k, v)
  root.dataset.theme = m
  root.dataset.themeMode = m
  root.dataset.themePreset = preset
  root.style.colorScheme = m
}
/** Show a custom palette without remembering it (the editor's live preview). Status colours come from the Nord variant of the same mode. */
export function previewPalette(p: CustomPalette, mode: unknown, root: HTMLElement = document.documentElement) {
  const m = normalizeMode(mode)
  const base = THEMES[0]![m]
  setTokens(customTokens(p, { ok: base.ok, warn: base.warn, bad: base.bad }), m, CUSTOM, root)
}
/** Apply and remember a custom theme (the same keys the classic UI reads). */
export function applyCustom(theme: CustomTheme, mode: unknown, root: HTMLElement = document.documentElement) {
  const m = normalizeMode(mode)
  previewPalette(theme.palettes[m], m, root)
  write(ACTIVE_KEY, theme.id)
  write(PRESET_KEY, CUSTOM)
  write(MODE_KEY, m)
  return { preset: CUSTOM, mode: m }
}

/** Apply a theme to the page and remember it (the same localStorage keys the classic UI uses). */
export function applyTheme(preset: unknown, mode: unknown, root: HTMLElement = document.documentElement) {
  if (preset === CUSTOM) { const c = activeCustom(); if (c) return applyCustom(c, mode, root) }
  try { window.localStorage.removeItem(ACTIVE_KEY) } catch { /* private mode */ }
  const id = normalizePreset(preset)
  const m = normalizeMode(mode)
  const fam = familyOf(id)
  for (const [k, v] of Object.entries(tokens(fam[m]))) root.style.setProperty(k, v)
  root.dataset.theme = m
  root.dataset.themeMode = m
  root.dataset.themePreset = id
  root.style.colorScheme = m
  write(PRESET_KEY, id)
  write(MODE_KEY, m)
  return { preset: id, mode: m }
}

export const SCALE_MIN = 70
export const SCALE_MAX = 130
export const normalizeScale = (v: unknown): number => {
  const n = Number(v)
  return Number.isFinite(n) ? Math.max(SCALE_MIN, Math.min(SCALE_MAX, Math.round(n))) : 100
}
export const densityFor = (scale: number): 'compact' | 'default' | 'spacious' => (scale === 70 ? 'compact' : scale === 130 ? 'spacious' : 'default')
export const SCALE_PRESETS = [{ id: 'dense', label: 'Dense', value: 70 }, { id: 'default', label: 'Default', value: 100 }, { id: 'spacious', label: 'Spacious', value: 130 }] as const

/** Zoom the whole app. CSS zoom on #root with a 100% box fills the window at every level (an inverse-size box overflows below 100% and leaves a gap above it). */
export function applyScale(value: unknown, root: HTMLElement = document.documentElement, app: HTMLElement | null = document.getElementById('root')) {
  const scale = normalizeScale(value)
  const z = scale / 100
  root.dataset.uiScale = String(scale)
  root.style.setProperty('--ax-ui-zoom', String(z))
  if (app) {
    app.style.zoom = String(z)
    app.style.width = '100%'
    app.style.height = '100%'
  }
  write(SCALE_KEY, String(scale))
  write(GRID_KEY, densityFor(scale))
  return scale
}
export const storedScale = () => normalizeScale(read(SCALE_KEY) ?? 100)

/** Ctrl and + / − / 0 step the zoom by 10 points (0 resets), like the classic shortcuts. */
export function scaleForKey(e: { key: string; code?: string; ctrlKey: boolean; altKey?: boolean; metaKey?: boolean }, current: number): number | null {
  if (!e.ctrlKey || e.altKey || e.metaKey) return null
  if (e.key === '+' || e.key === '=' || e.code === 'NumpadAdd') return normalizeScale(current + 10)
  if (e.key === '-' || e.code === 'NumpadSubtract') return normalizeScale(current - 10)
  if (e.key === '0' || e.code === 'Numpad0') return 100
  return null
}

/** The colours currently on screen as an editable custom palette: a custom theme's own, or the built-in theme's mapped onto the eight fields. */
export function currentPalette(preset: string, mode: Mode): CustomPalette {
  if (preset === CUSTOM) { const c = activeCustom(); if (c) return { ...c.palettes[mode] } }
  const p = familyOf(preset)[mode]
  const rgb = toRgb(p.text)
  return {
    bg: p.bg, surface: p.surface, primary: p.accent, secondary: p.text2, text: p.text, subtext: p.text2,
    border: rgb ? `rgba(${rgb.join(', ')}, 0.10)` : 'rgba(255, 255, 255, 0.10)', hover: mode === 'dark' ? 'rgba(255, 255, 255, 0.08)' : 'rgba(23, 34, 28, 0.06)',
  }
}
