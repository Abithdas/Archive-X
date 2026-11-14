/** Custom themes: eight editable colours per light/dark variant, stored in this browser (same keys and shape as the classic UI). */
export type Mode = 'dark' | 'light'
export const STORE_KEY = 'ax_custom_themes_v1'
export const ACTIVE_KEY = 'ax_active_custom_theme'
const MAX_THEMES = 100
export const NAME_MAX = 40

export type PaletteKey = 'bg' | 'surface' | 'primary' | 'secondary' | 'text' | 'subtext' | 'border' | 'hover'
export type CustomPalette = Record<PaletteKey, string>
export const FIELDS: ReadonlyArray<readonly [PaletteKey, string, string]> = [
  ['bg', 'Background', '#2e3440'], ['surface', 'Surface', '#3b4252'], ['primary', 'Primary', '#88c0d0'], ['secondary', 'Secondary', '#81a1c1'],
  ['text', 'Text', '#eceff4'], ['subtext', 'Subtext', '#c3ccda'], ['border', 'Border', 'rgba(236, 239, 244, 0.10)'], ['hover', 'Hover', 'rgba(255, 255, 255, 0.08)'],
]
export const DEFAULT_PALETTE: CustomPalette = Object.fromEntries(FIELDS.map(([k, , d]) => [k, d])) as CustomPalette
export interface CustomTheme { id: string; name: string; isDark: boolean; palette: CustomPalette; palettes: { dark: CustomPalette; light: CustomPalette } }

const FALLBACK = /^(#[0-9a-f]{3,8}|(rgb|hsl)a?\([0-9.,%\s/-]+\)|[a-z]{3,24})$/i
/** A CSS colour the browser accepts (checked with CSS.supports where it exists), with nothing that could break out of a declaration. */
export function validColor(value: unknown): boolean {
  const v = String(value ?? '').trim()
  if (!v || v.length > 96 || /[;{}<>\\]/.test(v)) return false
  try { if (typeof CSS !== 'undefined' && CSS.supports) return CSS.supports('color', v) } catch { /* use the fallback */ }
  return FALLBACK.test(v)
}

/** [r,g,b] for hex and rgb()/rgba() values; other CSS colours are resolved through a canvas when the browser has one. */
export function toRgb(value: string): [number, number, number] | null {
  const v = value.trim()
  let m = /^#([0-9a-f]{6})$/i.exec(v)
  if (m) return [parseInt(m[1]!.slice(0, 2), 16), parseInt(m[1]!.slice(2, 4), 16), parseInt(m[1]!.slice(4, 6), 16)]
  m = /^#([0-9a-f]{3})$/i.exec(v)
  if (m) return [...m[1]!].map((c) => parseInt(c + c, 16)) as [number, number, number]
  m = /^rgba?\(\s*(\d+)\s*,\s*(\d+)\s*,\s*(\d+)/i.exec(v)
  if (m) return [Number(m[1]), Number(m[2]), Number(m[3])]
  try {
    const ctx = document.createElement('canvas').getContext('2d')
    if (!ctx) return null
    ctx.fillStyle = v
    const n = ctx.fillStyle
    return n === v && !n.startsWith('#') && !n.startsWith('rgb') ? null : toRgbSimple(n)
  } catch { return null }
}
const toRgbSimple = (v: string) => { const h = /^#([0-9a-f]{6})$/i.exec(v); if (h) return toRgb(v); const r = /^rgba?\(\s*(\d+)\s*,\s*(\d+)\s*,\s*(\d+)/i.exec(v); return r ? ([Number(r[1]), Number(r[2]), Number(r[3])] as [number, number, number]) : null }

export function blend(a: string, b: string, amount: number): string {
  const ca = toRgb(a), cb = toRgb(b)
  if (!ca || !cb) return b
  return `#${ca.map((v, i) => Math.max(0, Math.min(255, Math.round(v * (1 - amount) + cb[i]! * amount))).toString(16).padStart(2, '0')).join('')}`
}
/** The other variant of a palette, derived the same way the classic editor does. */
export function deriveVariant(s: CustomPalette, target: Mode): CustomPalette {
  return target === 'light'
    ? { bg: blend(s.bg, '#f4f6f5', 0.91), surface: blend(s.surface, '#ffffff', 0.94), primary: blend(s.primary, '#173d2e', 0.42), secondary: blend(s.secondary, '#425c50', 0.48), text: '#17221c', subtext: '#536159', border: 'rgba(23,34,28,.17)', hover: 'rgba(23,34,28,.06)' }
    : { bg: blend(s.bg, '#101312', 0.72), surface: blend(s.surface, '#1c211e', 0.68), primary: blend(s.primary, '#e1fff0', 0.1), secondary: blend(s.secondary, '#a4b5ac', 0.24), text: '#f4f7f5', subtext: '#b7c3bc', border: 'rgba(244,247,245,.16)', hover: 'rgba(255,255,255,.07)' }
}
export function normalizePalette(raw: unknown, fallback?: CustomPalette | null): CustomPalette | null {
  const out = {} as CustomPalette
  for (const [k, , d] of FIELDS) {
    const r = raw as Record<string, unknown> | null
    const v = r && typeof r[k] === 'string' ? (r[k] as string).trim() : (fallback?.[k] ?? d)
    if (!validColor(v)) return null
    out[k] = v
  }
  return out
}
export function normalizeTheme(item: unknown): CustomTheme | null {
  if (!item || typeof item !== 'object') return null
  const t = item as Record<string, unknown>
  const id = String(t.id ?? '')
  if (!/^ax-custom-[a-z0-9-]{4,70}$/.test(id)) return null
  const name = String(t.name ?? 'My Theme').trim().slice(0, NAME_MAX) || 'My Theme'
  const isDark = t.isDark !== false
  const legacy = normalizePalette(t.palette, DEFAULT_PALETTE)
  if (!legacy) return null
  const ps = (t.palettes && typeof t.palettes === 'object' ? t.palettes : {}) as Record<string, unknown>
  const pal: Partial<Record<Mode, CustomPalette | null>> = {}
  if (ps.dark && typeof ps.dark === 'object') pal.dark = normalizePalette(ps.dark, isDark ? legacy : null)
  if (ps.light && typeof ps.light === 'object') pal.light = normalizePalette(ps.light, !isDark ? legacy : null)
  const base: Mode = isDark ? 'dark' : 'light'
  const other: Mode = isDark ? 'light' : 'dark'
  pal[base] = pal[base] ?? legacy
  pal[other] = pal[other] ?? deriveVariant(pal[base]!, other)
  return { id, name, isDark, palette: { ...pal[base]! }, palettes: { dark: pal.dark!, light: pal.light! } }
}
export const newThemeId = (now = Date.now(), rand = Math.random()) => `ax-custom-${now.toString(36)}-${rand.toString(36).slice(2, 8).padEnd(4, '0')}`
/** A new theme starting from the colours on screen, with the other variant derived. */
export function createTheme(current: CustomPalette, mode: Mode, id = newThemeId()): CustomTheme {
  const other: Mode = mode === 'dark' ? 'light' : 'dark'
  return { id, name: 'My Theme', isDark: mode === 'dark', palette: { ...current }, palettes: { [mode]: { ...current }, [other]: deriveVariant(current, other) } as CustomTheme['palettes'] }
}
/** The theme with `mode`'s colours replaced (or derived if it has none yet). */
export const withVariant = (t: CustomTheme, mode: Mode, palette?: CustomPalette): CustomTheme => {
  const p = palette ?? t.palettes[mode] ?? deriveVariant(t.palette, mode)
  return { ...t, isDark: mode === 'dark', palette: { ...p }, palettes: { ...t.palettes, [mode]: { ...p } } }
}

type R = Pick<Storage, 'getItem'>
type W = Pick<Storage, 'setItem'>
export function readThemes(s: R | null): CustomTheme[] {
  try { const v = JSON.parse(s?.getItem(STORE_KEY) || '[]') as unknown; return Array.isArray(v) ? v.map(normalizeTheme).filter((t): t is CustomTheme => !!t).slice(0, MAX_THEMES) : [] } catch { return [] }
}
export function writeThemes(s: W | null, themes: readonly CustomTheme[]): boolean { try { s?.setItem(STORE_KEY, JSON.stringify(themes.slice(-MAX_THEMES))); return true } catch { return false } }
export const activeId = (s: R | null) => { try { return s?.getItem(ACTIVE_KEY) || '' } catch { return '' } }
export const findTheme = (s: R | null, id: string) => readThemes(s).find((t) => t.id === id) ?? null
export const upsert = (themes: readonly CustomTheme[], t: CustomTheme) => { const i = themes.findIndex((x) => x.id === t.id); return i >= 0 ? themes.map((x, j) => (j === i ? t : x)) : [...themes, t] }

/** Readable text colour (dark or white) for a coloured background. */
export function readableOn(color: string): string {
  const c = toRgb(color)
  if (!c) return '#ffffff'
  const lin = c.map((v) => { const x = v / 255; return x <= 0.04045 ? x / 12.92 : ((x + 0.055) / 1.055) ** 2.4 })
  return 0.2126 * lin[0]! + 0.7152 * lin[1]! + 0.0722 * lin[2]! > 0.43 ? '#101510' : '#ffffff'
}
const mix = (a: string, pct: number, b: string) => `color-mix(in srgb, ${a} ${pct}%, ${b})`
/** The app's colour variables for a custom palette; ok/warn/bad come from the built-in theme because a custom theme has no status colours. */
export function customTokens(p: CustomPalette, status: { ok: string; warn: string; bad: string }): Record<string, string> {
  return {
    '--color-bg': p.bg, '--color-surface': p.surface, '--color-surface-2': mix(p.surface, 88, p.text), '--color-surface-3': mix(p.surface, 76, p.text),
    '--color-fg': p.text, '--color-muted': p.subtext, '--color-faint': mix(p.subtext, 74, p.bg), '--color-accent': p.primary, '--color-accent-fg': readableOn(p.primary),
    '--color-good': status.ok, '--color-warn': status.warn, '--color-bad': status.bad, '--color-line': p.border, '--color-line-2': mix(p.border, 62, p.text),
  }
}
/** The editor's eight colour pickers need #rrggbb. */
export function pickerColor(value: string): string {
  if (/^#[0-9a-f]{6}$/i.test(value)) return value
  const c = toRgb(value)
  return c ? `#${c.map((v) => v.toString(16).padStart(2, '0')).join('')}` : '#808080'
}
