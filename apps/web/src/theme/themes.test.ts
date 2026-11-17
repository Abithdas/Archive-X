import { beforeEach, describe, expect, it } from 'vitest'
import { THEMES, applyScale, applyTheme, familyOf, normalizeMode, normalizePreset, normalizeScale, scaleForKey, storedTheme, tokens } from './themes'

beforeEach(() => { localStorage.clear(); document.documentElement.removeAttribute('style'); document.documentElement.removeAttribute('data-theme') })

describe('presets', () => {
  it('has the seven classic themes and the three added ones, each with both variants', () => {
    expect(THEMES.map((t) => t.id)).toEqual(['nord', 'nero-gold', 'black-essence', 'pastel-lavender', 'pastel-mint', 'minimal-slate', 'minimal-sand', 'teal-coral', 'neon-lime', 'golden-noir'])
    for (const t of THEMES) for (const m of ['dark', 'light'] as const) expect(t[m].bg).toMatch(/^#[0-9a-f]{6}$/i)
  })
  it('uses the measured classic colours', () => {
    expect(familyOf('nord').dark).toMatchObject({ bg: '#232833', surface: '#2b313d', accent: '#5fd3e0' })
    expect(familyOf('nero-gold').light).toMatchObject({ bg: '#f7f4eb', accent: '#8d6a18' })
  })
  it('the added themes use their swatch colours and keep their text readable', () => {
    expect(familyOf('teal-coral').light.bg).toBe('#f3eee8'); expect(familyOf('teal-coral').dark.accent).toBe('#ff6845')
    expect(familyOf('neon-lime').dark).toMatchObject({ bg: '#08090b', accent: '#cdf22b' })
    expect(familyOf('golden-noir').dark.accent).toBe('#ffc900')
    const lum = (h: string) => { const c = [1, 3, 5].map((i) => parseInt(h.slice(i, i + 2), 16) / 255).map((v) => (v <= 0.03928 ? v / 12.92 : ((v + 0.055) / 1.055) ** 2.4)); return 0.2126 * c[0]! + 0.7152 * c[1]! + 0.0722 * c[2]! }
    const ratio = (a: string, b: string) => { const [x, y] = [lum(a), lum(b)].sort((p, q) => q - p); return (x! + 0.05) / (y! + 0.05) }
    for (const id of ['teal-coral', 'neon-lime', 'golden-noir']) for (const m of ['dark', 'light'] as const) {
      const p = familyOf(id)[m]
      expect(ratio(p.text, p.bg), `${id} ${m} text`).toBeGreaterThan(7)
      expect(ratio(p.onAccent, p.accent), `${id} ${m} button text`).toBeGreaterThan(4.5)
      expect(ratio(p.accent, p.bg), `${id} ${m} accent on page`).toBeGreaterThan(3)
    }
  })
  it('falls back to Nord for unknown ids and non-light modes', () => {
    expect(normalizePreset('custom')).toBe('nord')
    expect(normalizePreset(undefined)).toBe('nord')
    expect(normalizeMode('system')).toBe('dark')
    expect(normalizeMode('light')).toBe('light')
  })
})

describe('applyTheme', () => {
  it('sets the variables, attributes and remembers the choice for both interfaces', () => {
    applyTheme('nero-gold', 'light')
    const r = document.documentElement
    expect(r.style.getPropertyValue('--color-bg')).toBe('#f7f4eb')
    expect(r.style.getPropertyValue('--color-accent')).toBe('#8d6a18')
    expect(r.dataset.theme).toBe('light')
    expect(r.dataset.themePreset).toBe('nero-gold')
    expect([localStorage.getItem('ax_theme_preset'), localStorage.getItem('ax_theme_mode')]).toEqual(['nero-gold', 'light'])
    expect(storedTheme()).toEqual({ preset: 'nero-gold', mode: 'light' })
  })
  it('derives hairlines from the text colour', () => expect(tokens(familyOf('nord').dark)['--color-line']).toContain('#eceff4 7%'))
})

describe('interface scale', () => {
  it('clamps to 70–130 and rounds', () => {
    expect([normalizeScale(10), normalizeScale(500), normalizeScale(104.6), normalizeScale('x')]).toEqual([70, 130, 105, 100])
  })
  it('zooms the app and keeps its box at 100% so it still fills the window', () => {
    const app = document.createElement('div')
    applyScale(130, document.documentElement, app)
    expect(app.style.zoom).toBe('1.3')
    expect([app.style.width, app.style.height]).toEqual(['100%', '100%'])
    expect(document.documentElement.dataset.uiScale).toBe('130')
    expect(localStorage.getItem('ax_ui_scale')).toBe('130')
    expect(localStorage.getItem('ax_grid_density')).toBe('spacious')
  })
  it('steps by 10 with Ctrl + / − / 0 and ignores other combinations', () => {
    const k = (key: string, extra = {}) => ({ key, ctrlKey: true, ...extra })
    expect(scaleForKey(k('+'), 100)).toBe(110)
    expect(scaleForKey(k('='), 100)).toBe(110)
    expect(scaleForKey(k('-'), 70)).toBe(70)
    expect(scaleForKey(k('0'), 80)).toBe(100)
    expect(scaleForKey({ key: '+', ctrlKey: false }, 100)).toBeNull()
    expect(scaleForKey(k('+', { altKey: true }), 100)).toBeNull()
    expect(scaleForKey(k('a'), 100)).toBeNull()
  })
})
