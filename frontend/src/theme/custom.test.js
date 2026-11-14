import { describe, expect, it } from 'vitest';
import { ACTIVE_KEY, DEFAULT_PALETTE, FIELDS, STORE_KEY, activeId, blend, createTheme, customTokens, deriveVariant, findTheme, newThemeId, normalizePalette, normalizeTheme, pickerColor, readThemes, readableOn, toRgb, upsert, validColor, withVariant, writeThemes } from './custom';
const mem = (init = {}) => { const m = new Map(Object.entries(init)); return { getItem: (k) => m.get(k) ?? null, setItem: (k, v) => void m.set(k, v), m }; };
describe('custom themes', () => {
    it('accepts only safe CSS colours', () => {
        for (const ok of ['#fff', '#112233', 'rgb(1,2,3)', 'rgba(1, 2, 3, .5)', 'red', 'hsl(10 20% 30%)'])
            expect(validColor(ok)).toBe(true);
        for (const bad of ['', '   ', 'red;', '{x}', 'url(x)<', 'a\\b', 'x'.repeat(97), 'not a colour', '#12'])
            expect(validColor(bad)).toBe(false);
    });
    it('parses and blends colours', () => {
        expect(toRgb('#102030')).toEqual([16, 32, 48]);
        expect(toRgb('#abc')).toEqual([170, 187, 204]);
        expect(toRgb('rgba(1, 2, 3, .4)')).toEqual([1, 2, 3]);
        expect(blend('#000000', '#ffffff', 0.5)).toBe('#808080');
        expect(blend('#000000', '#ffffff', 0)).toBe('#000000');
        expect(blend('nonsense', '#ffffff', 0.5)).toBe('#ffffff');
        expect(readableOn('#ffffff')).toBe('#101510');
        expect(readableOn('#000000')).toBe('#ffffff');
        expect(readableOn('weird')).toBe('#ffffff');
        expect(pickerColor('#123456')).toBe('#123456');
        expect(pickerColor('rgb(255, 0, 0)')).toBe('#ff0000');
        expect(pickerColor('#0f0')).toBe('#00ff00');
        expect(pickerColor('???')).toBe('#808080');
    });
    it('derives the other variant', () => {
        const light = deriveVariant(DEFAULT_PALETTE, 'light');
        expect(light.text).toBe('#17221c');
        expect(light.border).toBe('rgba(23,34,28,.17)');
        expect(toRgb(light.bg)[0]).toBeGreaterThan(200);
        const dark = deriveVariant(DEFAULT_PALETTE, 'dark');
        expect(dark.text).toBe('#f4f7f5');
        expect(toRgb(dark.bg)[0]).toBeLessThan(60);
    });
    it('normalises palettes and themes, rejecting bad ones', () => {
        expect(normalizePalette({ ...DEFAULT_PALETTE, bg: ' #111111 ' }).bg).toBe('#111111');
        expect(normalizePalette({ ...DEFAULT_PALETTE, bg: 'x;' })).toBeNull();
        expect(normalizePalette({})).toEqual(DEFAULT_PALETTE);
        expect(FIELDS).toHaveLength(8);
        expect(normalizeTheme(null)).toBeNull();
        expect(normalizeTheme({ id: 'bad', palette: DEFAULT_PALETTE })).toBeNull();
        const legacy = normalizeTheme({ id: 'ax-custom-abcd', name: '  Mine  ', isDark: false, palette: { ...DEFAULT_PALETTE, bg: '#eeeeee' } });
        expect(legacy.name).toBe('Mine');
        expect(legacy.palettes.light.bg).toBe('#eeeeee');
        expect(legacy.palettes.dark.text).toBe('#f4f7f5');
        expect(legacy.isDark).toBe(false);
        expect(normalizeTheme({ id: 'ax-custom-abcd', name: 'x'.repeat(60), palette: DEFAULT_PALETTE }).name).toHaveLength(40);
        expect(normalizeTheme({ id: 'ax-custom-abcd', name: '', palette: { ...DEFAULT_PALETTE, text: '{' } })).toBeNull();
    });
    it('creates, edits variants, and stores themes', () => {
        const t = createTheme(DEFAULT_PALETTE, 'dark', 'ax-custom-test1');
        expect(t.name).toBe('My Theme');
        expect(t.palettes.dark).toEqual(DEFAULT_PALETTE);
        expect(t.palettes.light.text).toBe('#17221c');
        const edited = withVariant(t, 'light', { ...t.palettes.light, bg: '#fafafa' });
        expect(edited.isDark).toBe(false);
        expect(edited.palettes.light.bg).toBe('#fafafa');
        expect(edited.palettes.dark).toEqual(DEFAULT_PALETTE);
        const s = mem();
        expect(writeThemes(s, [t])).toBe(true);
        expect(readThemes(s)[0].id).toBe('ax-custom-test1');
        expect(findTheme(s, 'ax-custom-test1').name).toBe('My Theme');
        expect(findTheme(s, 'zzz')).toBeNull();
        expect(upsert([t], edited)).toHaveLength(1);
        expect(upsert([t], { ...t, id: 'ax-custom-other' })).toHaveLength(2);
        s.setItem(ACTIVE_KEY, 'ax-custom-test1');
        expect(activeId(s)).toBe('ax-custom-test1');
        expect(activeId(null)).toBe('');
    });
});
describe('storage and tokens', () => {
    it('survives broken storage', () => {
        expect(readThemes(mem({ [STORE_KEY]: '{bad' }))).toEqual([]);
        expect(readThemes(mem({ [STORE_KEY]: '{"a":1}' }))).toEqual([]);
        expect(readThemes(null)).toEqual([]);
        expect(writeThemes({ setItem: () => { throw new Error('full'); } }, [])).toBe(false);
        expect(newThemeId(1, 0.123456)).toMatch(/^ax-custom-1-[a-z0-9]{4,}$/);
    });
    it('maps a palette onto the app variables', () => {
        const t = customTokens(DEFAULT_PALETTE, { ok: '#0f0', warn: '#ff0', bad: '#f00' });
        expect(t['--color-bg']).toBe('#2e3440');
        expect(t['--color-accent']).toBe('#88c0d0');
        expect(t['--color-fg']).toBe('#eceff4');
        expect(t['--color-accent-fg']).toBe('#101510');
        expect(t['--color-surface-2']).toBe('color-mix(in srgb, #3b4252 88%, #eceff4)');
        expect(t['--color-good']).toBe('#0f0');
        expect(t['--color-line']).toBe('rgba(236, 239, 244, 0.10)');
    });
});
