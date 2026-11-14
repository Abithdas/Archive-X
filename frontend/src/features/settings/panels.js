import { jsx as _jsx, jsxs as _jsxs, Fragment as _Fragment } from "react/jsx-runtime";
import { useQueryClient } from '@tanstack/react-query';
import { useEffect, useState } from 'react';
import { toast } from 'sonner';
import { ApiError, api } from '@/api/client';
import { useStorage } from '@/api/hooks';
import { Icon } from '@/design/Icon';
import { ThemePreview } from './ThemePreview';
import { Segmented } from '@/design/Segmented';
import { Switch } from '@/design/Switch';
import { cn } from '@/design/cn';
import { formatBytes } from '@/design/format';
import { THEMES, SCALE_MAX, SCALE_MIN, SCALE_PRESETS } from '@/theme/themes';
import { AUTONEXT_MODES, experienceFrom, pills, statusRows } from './logic';
import { useAuthStatus, useExperienceSettings, useIconsStatus, useMetadataSettings, usePreflight, useWebLogin } from './queries';
import { CustomThemes } from './CustomThemes';
import { useAppearance, useAppearanceActions } from './useAppearance';
const BOX = 'rounded-[18px] border border-line bg-surface p-5';
function Head({ icon, title, count, aside }) {
    return (_jsxs("div", { className: "mb-4 flex items-center justify-between gap-3", children: [_jsxs("h3", { className: "flex items-center gap-3 text-xl font-extrabold", children: [_jsx("span", { className: "text-accent", children: _jsx(Icon, { name: icon, className: "size-5" }) }), title] }), _jsx("span", { className: "text-xs text-faint", children: aside ?? count })] }));
}
export function OverviewPanel({ go }) {
    const auth = useAuthStatus(), pre = usePreflight(), icons = useIconsStatus(), meta = useMetadataSettings(), web = useWebLogin(), storage = useStorage();
    const checks = { auth: auth.data, preflight: pre.data, icons: icons.data, metadata: meta.data, web: web.data, storage: storage.data };
    const rows = statusRows(checks, formatBytes);
    const tone = (t) => (t === 'ok' ? 'bg-good/15 text-good' : t === 'warn' ? 'bg-warn/15 text-warn' : 'bg-surface-3 text-muted');
    return (_jsxs("div", { className: "grid gap-4", children: [_jsxs("section", { "aria-label": "Connection status", className: BOX, children: [_jsxs("div", { className: "mb-3 flex items-center justify-between", children: [_jsx("h3", { className: "text-xl font-extrabold", children: "Connection status" }), _jsx("span", { className: "text-xs text-faint", children: "01 / 08" })] }), _jsx("ul", { className: "mb-3 flex flex-wrap gap-2", children: pills(checks).map((p) => _jsxs("li", { className: cn('inline-flex items-center gap-2 rounded-full px-3 py-1.5 text-xs font-bold', p.ok ? 'bg-good/15 text-good' : 'bg-warn/15 text-warn'), children: [_jsx("i", { className: "size-1.5 rounded-full bg-current" }), p.label] }, p.label)) }), _jsx("p", { className: "text-sm text-muted", children: "Configuration changes are applied through the settings below; live jobs remain in Operations." })] }), _jsxs("section", { "aria-label": "Status", className: BOX, children: [_jsx(Head, { icon: "operations", title: "Status", count: "Runtime checks" }), _jsx("ul", { children: rows.map((r) => {
                            const inner = (_jsxs(_Fragment, { children: [_jsx("span", { className: "grid size-9 shrink-0 place-items-center rounded-xl bg-surface-3 text-accent", children: _jsx(Icon, { name: r.icon, className: "size-4" }) }), _jsxs("span", { className: "min-w-0 flex-1 text-left", children: [_jsx("b", { className: "block text-[15px]", children: r.title }), _jsx("small", { className: "block truncate text-xs text-muted", title: r.sub, children: r.sub })] }), _jsx("span", { className: cn('rounded-full px-3 py-1 text-[11px] font-bold', tone(r.tone)), children: r.label })] }));
                            return _jsx("li", { className: "border-b border-line last:border-0", children: r.go ? _jsx("button", { type: "button", onClick: () => go(r.go), className: "flex w-full items-center gap-3 py-3 hover:bg-fg/5", children: inner }) : _jsx("div", { className: "flex items-center gap-3 py-3", children: inner }) }, r.title);
                        }) }), _jsxs("details", { className: "mt-2 border-t border-line pt-3", children: [_jsx("summary", { className: "cursor-pointer text-sm font-bold", children: "Details \u00B7 raw runtime report" }), _jsx("pre", { className: "mt-2 max-h-64 overflow-auto rounded-xl bg-bg p-3 text-xs text-muted", children: JSON.stringify({ telegram: auth.data, dependencies: pre.data, icons: icons.data }, null, 2) })] })] })] }));
}
function Row({ title, hint, children }) {
    return _jsxs("div", { className: "flex items-center justify-between gap-4 border-b border-line py-3", children: [_jsxs("div", { className: "min-w-0", children: [_jsx("b", { className: "block text-[15px]", children: title }), hint ? _jsx("small", { className: "text-xs text-muted", children: hint }) : null] }), children] });
}
export function ExperiencePanel() {
    const server = useExperienceSettings();
    const qc = useQueryClient();
    const [form, setForm] = useState(experienceFrom(undefined));
    const [msg, setMsg] = useState('');
    const [busy, setBusy] = useState(false);
    useEffect(() => { if (server.data)
        setForm(experienceFrom(server.data)); }, [server.data]);
    const set = (k, v) => { setForm((f) => ({ ...f, [k]: v })); setMsg(''); };
    const save = async () => {
        setBusy(true);
        try {
            const r = await api('/api/settings/experience', { method: 'POST', json: form });
            if (r?.ok === false)
                throw new ApiError(0, r.detail || 'save failed');
            setMsg('Saved.');
            toast('Experience settings saved');
            await qc.invalidateQueries({ queryKey: ['settings', 'experience'] });
            setTimeout(() => setMsg(''), 1800);
        }
        catch (e) {
            setMsg(e instanceof ApiError ? e.detail || 'Could not save.' : 'Could not save.');
        }
        finally {
            setBusy(false);
        }
    };
    return (_jsxs("section", { "aria-label": "Playback", className: BOX, children: [_jsx(Head, { icon: "play", title: "Playback", count: "04 / 08" }), server.isError ? _jsx("p", { role: "alert", className: "mb-3 text-sm text-warn", children: "Could not load the saved settings." }) : null, _jsx(Row, { title: "Autoplay", hint: "Start videos as soon as they open", children: _jsx(Switch, { label: "Autoplay", checked: form.autoplay, onChange: (v) => set('autoplay', v) }) }), _jsx(Row, { title: "Autoplay next", hint: "Continue with the next video when one ends", children: _jsx(Switch, { label: "Autoplay next", checked: form.autonext, onChange: (v) => set('autonext', v) }) }), _jsx(Row, { title: "Ask to resume playback", hint: "Offer to continue where you left off", children: _jsx(Switch, { label: "Ask to resume playback", checked: form.resume_prompt, onChange: (v) => set('resume_prompt', v) }) }), _jsx(Row, { title: "Live seek preview (timeline hover)", hint: "Show frames while scrubbing", children: _jsx(Switch, { label: "Live seek preview", checked: form.live_seek_preview, onChange: (v) => set('live_seek_preview', v) }) }), _jsx("p", { className: "py-2 text-xs text-muted", children: "Hover the playback timeline to preview a frame. On touch screens, drag to seek." }), _jsx(Row, { title: "Ask about partial cache on close", children: _jsx(Switch, { label: "Ask about partial cache on close", checked: form.partial_cache_prompt, onChange: (v) => set('partial_cache_prompt', v) }) }), _jsx("p", { className: "py-2 text-xs text-muted", children: "Off keeps cached bytes automatically without showing the close popup." }), _jsxs("label", { className: "mt-2 grid gap-2 text-center text-sm font-semibold", children: ["Autoplay source", _jsx("select", { value: form.autonext_mode, onChange: (e) => set('autonext_mode', e.target.value), "aria-label": "Autoplay source", className: "h-12 w-full rounded-xl border border-line-2 bg-bg px-3 text-left text-[15px] font-medium", children: AUTONEXT_MODES.map(([k, l]) => _jsx("option", { value: k, children: l }, k)) })] }), _jsx("p", { className: "mt-4 text-xs text-muted", children: "Continue Watching, play position, play count, markers, favorites and recommendation shelves are stored locally. Provider APIs are used for enrichment rather than page rendering." }), _jsx("p", { className: "mt-3 text-xs text-muted", children: "Playback QoS prioritizes the browser requested byte range, keeps a short bitrate-based read-ahead window, and preloads only the next queued video prefix at background priority." }), _jsxs("div", { className: "mt-4 flex items-center gap-3", children: [_jsx("button", { type: "button", onClick: () => void server.refetch(), className: "h-11 rounded-xl border border-line-2 px-4 text-sm font-semibold", children: "Reload" }), _jsx("button", { type: "button", disabled: busy, onClick: () => void save(), className: "h-11 rounded-xl bg-accent px-5 text-sm font-bold text-accent-fg disabled:opacity-60", children: busy ? 'Saving…' : 'Save experience settings' }), _jsx("span", { role: "status", className: "text-sm text-muted", children: msg })] })] }));
}
const SIZE_NOTES = { 70: 'Fits more on screen', 100: 'The standard size', 130: 'Larger and easier to read' };
export function AppearancePanel() {
    const { preset, mode, scale } = useAppearance();
    const { setTheme, setScale, previewScale } = useAppearanceActions();
    const [status, setStatus] = useState('Appearance preferences are saved automatically.');
    const save = async (fn, ok) => {
        try {
            await fn();
            setStatus(ok);
        }
        catch {
            setStatus('Applied locally; server save failed.');
        }
    };
    return (_jsxs("div", { className: "grid gap-4", children: [_jsxs("section", { "aria-label": "Themes", className: BOX, children: [_jsxs("div", { className: "mb-1 flex flex-wrap items-center justify-between gap-3", children: [_jsxs("h3", { className: "flex items-center gap-3 text-xl font-extrabold", children: [_jsx("span", { className: "text-accent", children: _jsx(Icon, { name: "sparkle", className: "size-5" }) }), "Themes"] }), _jsx(Segmented, { label: "Theme mode", value: mode, onChange: (m) => void save(() => setTheme(preset, m), `Theme: ${m === 'dark' ? 'Dark' : 'Light'}. Saved.`), options: [{ value: 'dark', label: 'Dark' }, { value: 'light', label: 'Light' }] })] }), _jsx("p", { className: "mb-4 text-sm text-muted", children: "Pick a look for the whole app. Every theme comes in a dark and a light version; the switch above changes between them." }), _jsx("ul", { className: "grid grid-cols-2 gap-3.5 md:grid-cols-3 xl:grid-cols-4", "aria-label": "Theme presets", children: THEMES.map((t) => {
                            const p = t[mode];
                            const on = preset === t.id;
                            return (_jsx("li", { children: _jsxs("button", { type: "button", "aria-pressed": on, "aria-label": t.name, onClick: () => void save(() => setTheme(t.id, mode), `Theme: ${t.name}. Saved.`), className: cn('group relative grid w-full gap-2.5 rounded-2xl border p-2.5 text-left transition-[border-color,box-shadow,transform] duration-150 hover:-translate-y-0.5', on ? 'border-accent shadow-[0_0_0_1px_var(--color-accent)]' : 'border-line-2 hover:border-fg/30'), children: [_jsx(ThemePreview, { c: { bg: p.bg, surface: p.surface, card: p.surface2, thumb: p.surface3, text: p.text, muted: p.text3, accent: p.accent } }), _jsxs("span", { className: "flex items-center justify-between gap-2 px-1", children: [_jsx("b", { className: "truncate text-[13px]", children: t.name }), on ? _jsxs("span", { className: "inline-flex items-center gap-1 text-[11px] font-bold text-accent", children: [_jsx(Icon, { name: "check", className: "size-3" }), "In use"] }) : _jsx("span", { className: "text-[11px] text-faint opacity-0 group-hover:opacity-100", children: "Use" })] })] }) }, t.id));
                        }) }), _jsx("p", { className: "mt-3 text-xs text-muted", role: "status", children: status })] }), _jsxs("section", { "aria-label": "Appearance", className: BOX, children: [_jsx(Head, { icon: "grid", title: "Display size", count: "How large everything is" }), _jsx("div", { className: "mb-4 grid grid-cols-3 gap-2.5", role: "group", "aria-label": "Application zoom level", children: SCALE_PRESETS.map((p) => (_jsxs("button", { type: "button", "aria-pressed": scale === p.value, onClick: () => void save(() => setScale(p.value), `Application zoom: ${p.value}%. Saved.`), className: cn('rounded-2xl border px-3 py-3.5 text-center transition-colors', scale === p.value ? 'border-accent bg-accent/10' : 'border-line-2 hover:bg-fg/5'), children: [_jsx("b", { className: "block text-sm", children: p.label }), _jsxs("small", { className: "text-xs text-muted", children: [p.value, "%"] }), _jsx("small", { className: "mt-0.5 hidden text-[11px] text-faint sm:block", children: SIZE_NOTES[p.value] })] }, p.id))) }), _jsxs("div", { className: "rounded-2xl border border-line bg-bg p-4", children: [_jsxs("div", { className: "mb-1 flex items-baseline justify-between", children: [_jsx("label", { htmlFor: "ui-scale", className: "text-sm font-semibold", children: "Fine-tune" }), _jsxs("b", { className: "text-xl tabular-nums", "aria-live": "polite", children: [scale, "%"] })] }), _jsx("input", { id: "ui-scale", type: "range", min: SCALE_MIN, max: SCALE_MAX, step: 1, value: scale, "aria-valuetext": `${scale} percent`, onChange: (e) => previewScale(Number(e.target.value)), onPointerUp: (e) => void save(() => setScale(Number(e.target.value)), `Application zoom saved at ${e.target.value}%.`), onKeyUp: (e) => { if (e.key.startsWith('Arrow') || e.key === 'Home' || e.key === 'End')
                                    void save(() => setScale(scale), `Application zoom saved at ${scale}%.`); }, "aria-label": "Interface scale", className: "w-full accent-[var(--color-accent)]" }), _jsx("div", { className: "mt-1 flex justify-between text-[11px] text-faint", "aria-hidden": "true", children: [70, 100, 130].map((n) => _jsxs("span", { children: [n, "%"] }, n)) })] }), _jsxs("p", { className: "mt-3 flex flex-wrap items-center gap-x-2 gap-y-1 text-xs text-muted", children: ["Shortcuts: ", _jsx("kbd", { className: "rounded border border-line-2 px-1.5", children: "Ctrl" }), " ", _jsx("kbd", { className: "rounded border border-line-2 px-1.5", children: "\u2212" }), " / ", _jsx("kbd", { className: "rounded border border-line-2 px-1.5", children: "+" }), ", reset with ", _jsx("kbd", { className: "rounded border border-line-2 px-1.5", children: "Ctrl" }), " ", _jsx("kbd", { className: "rounded border border-line-2 px-1.5", children: "0" }), scale !== 100 ? _jsx("button", { type: "button", onClick: () => void save(() => setScale(100), 'Application zoom: 100%. Saved.'), className: "font-semibold text-accent underline", children: "Reset to 100%" }) : null] })] }), _jsx(CustomThemes, { onStatus: setStatus })] }));
}
