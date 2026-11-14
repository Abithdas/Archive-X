import { jsx as _jsx, jsxs as _jsxs, Fragment as _Fragment } from "react/jsx-runtime";
import { useQueryClient } from '@tanstack/react-query';
import { useEffect, useMemo, useState } from 'react';
import { toast } from 'sonner';
import { ApiError, api } from '@/api/client';
import { useChannels, useSourceChannels } from '@/api/hooks';
import { Dialog } from '@/design/Dialog';
import { Icon } from '@/design/Icon';
import { Segmented } from '@/design/Segmented';
import { CARD, Check, FIELD, Field, Stepper, detail, videosHint } from './parts';
import { ChannelAvatar } from '@/features/channels/ChannelAvatar';
import { DEFAULT_LOCAL, DEFAULT_TELEGRAM, ORIENTATIONS, RESOLUTIONS, localPayload, planPhotoScan, resolveAsk, startMessage, telegramPayload, validateLocal, validateTelegram, } from './transfer';
const STEPS = ['Source', 'Options & Checks', 'Review', 'Run'];
function Select({ value, onChange, options, label }) {
    return (_jsx("select", { value: value, onChange: (e) => onChange(e.target.value), "aria-label": label, className: FIELD, children: options.map(([v, l]) => _jsx("option", { value: v, children: l }, v)) }));
}
/** Telegram Transfer Manager: Local → Telegram uploads (videos or images) and Telegram → Telegram transfers, as a four-step flow. */
export function TransferWorkflow() {
    const channels = useSourceChannels();
    const library = useChannels();
    const libraryCount = useMemo(() => new Map((Array.isArray(library.data) ? library.data : []).map((c) => [c.telegram_id, c.video_count])), [library.data]);
    const qc = useQueryClient();
    const [source, setSource] = useState('local');
    const [local, setLocal] = useState(DEFAULT_LOCAL);
    const [tg, setTg] = useState(DEFAULT_TELEGRAM);
    const [step, setStep] = useState(0);
    const [q, setQ] = useState('');
    const [folderMsg, setFolderMsg] = useState('');
    const [msg, setMsg] = useState('');
    const [busy, setBusy] = useState(false);
    const [ask, setAsk] = useState(null);
    const chans = channels.data ?? [];
    const nameOf = (id) => chans.find((c) => c.telegram_id === id)?.title ?? id;
    const setL = (k, v) => setLocal((o) => ({ ...o, [k]: v }));
    const setT = (k, v) => setTg((o) => ({ ...o, [k]: v }));
    const problem = source === 'local' ? validateLocal(local) : validateTelegram(tg);
    const canNext = step >= 2 || problem === '';
    // The folder chosen last time comes back pre-filled (classic: loadFolderPreference).
    useEffect(() => {
        let live = true;
        api('/api/settings/folder-preference?key=transfer_source')
            .then((r) => live && r?.ok && r.path && setLocal((o) => (o.folder ? o : { ...o, folder: r.path ?? '' })))
            .catch(() => undefined);
        return () => { live = false; };
    }, []);
    const browse = async () => {
        setFolderMsg('Opening folder picker…');
        try {
            const r = await api('/api/settings/folder-picker', { method: 'POST', json: { title: 'Select Telegram Transfer source folder', initial_path: local.folder, must_exist: true, allow_create: false } });
            if (r.cancelled)
                setFolderMsg('Folder selection cancelled.');
            else if (r.path) {
                setL('folder', r.path);
                setFolderMsg('Folder selected. Click Save folder to remember it.');
            }
            else
                setFolderMsg('No folder selected.');
        }
        catch (e) {
            setFolderMsg(detail(e, 'Folder picker unavailable.'));
        }
    };
    const saveFolder = async () => {
        if (!local.folder.trim())
            return setFolderMsg('Choose a folder first.');
        setFolderMsg('Saving folder…');
        try {
            const r = await api('/api/settings/folder-preference', { method: 'POST', json: { key: 'transfer_source', path: local.folder.trim() } });
            if (!r?.ok)
                throw new ApiError(0, r?.detail || 'Could not save folder');
            if (r.path)
                setL('folder', r.path);
            setFolderMsg('Folder saved.');
        }
        catch (e) {
            setFolderMsg(detail(e, 'Could not save folder.'));
        }
    };
    const finish = (text, ok) => {
        setMsg(text);
        ok ? toast(text) : toast.error(text);
    };
    const start = async (payload, count) => {
        const r = await api('/api/telegram-transfer/start', { method: 'POST', json: payload });
        finish(startMessage(source, r, local.images, count), Boolean(r.id));
        void qc.invalidateQueries({ queryKey: ['lanes'] });
        void qc.invalidateQueries({ queryKey: ['status'] });
    };
    const proceed = async (plan) => {
        if (plan.kind === 'error')
            return finish(plan.message, false);
        if (plan.kind === 'go')
            await start(localPayload(local, plan.includeSubfolders), plan.count);
    };
    const run = async () => {
        if (problem)
            return finish(problem, false);
        setBusy(true);
        setMsg('');
        try {
            if (source === 'telegram')
                return await start(telegramPayload(tg), null);
            if (!local.images)
                return await start(localPayload(local), null);
            const scan = await api('/api/telegram-transfer/scan-photos', { method: 'POST', json: { folder: local.folder.trim() } }).catch((e) => ({ detail: detail(e, '') }));
            const plan = planPhotoScan(scan, local.folder, local.subfolders);
            if (plan.kind === 'ask') {
                setAsk({ message: plan.message, scan, folder: local.folder });
                return;
            }
            await proceed(plan);
        }
        catch (e) {
            finish(`Could not start: ${detail(e, 'unknown error')}`, false);
        }
        finally {
            setBusy(false);
        }
    };
    const answer = async (include) => {
        const a = ask;
        if (!a)
            return;
        setAsk(null);
        if (include)
            setL('subfolders', true);
        setBusy(true);
        try {
            await proceed(resolveAsk(a.scan, a.folder, include));
        }
        catch (e) {
            finish(`Could not start: ${detail(e, 'unknown error')}`, false);
        }
        finally {
            setBusy(false);
        }
    };
    const visible = chans.filter((c) => c.title.toLowerCase().includes(q.trim().toLowerCase()) || c.telegram_id.includes(q.trim()));
    const toggle = (id) => setT('channels', tg.channels.includes(id) ? tg.channels.filter((x) => x !== id) : [...tg.channels, id]);
    const destination = (value, onChange, label) => (_jsx(Field, { label: label, children: _jsxs("select", { value: value, onChange: (e) => onChange(e.target.value), "aria-label": label, className: FIELD, children: [_jsx("option", { value: "", children: "\u2014 select channel \u2014" }), chans.map((c) => _jsx("option", { value: c.telegram_id, children: c.title }, c.telegram_id))] }) }));
    return (_jsxs("section", { "aria-label": "Telegram Transfer Manager", className: CARD, children: [_jsxs("div", { className: "mb-1 flex items-start gap-3", children: [_jsx("span", { className: "grid size-10 shrink-0 place-items-center rounded-xl bg-surface-3 text-accent", children: _jsx(Icon, { name: "transfer" }) }), _jsxs("div", { children: [_jsx("h2", { className: "text-lg font-extrabold", children: "Telegram Transfer Manager" }), _jsx("p", { className: "text-sm text-muted", children: "Use one workflow for local video uploads, image-document uploads, or Telegram-to-Telegram transfers." })] })] }), _jsxs("details", { className: "mt-2 rounded-xl border border-line px-3 py-2 text-sm text-muted", children: [_jsx("summary", { className: "cursor-pointer font-semibold text-fg", children: "Workflow details" }), _jsxs("p", { className: "mt-2", children: ["One transfer manager handles both Local \u2192 Telegram and Telegram \u2192 Telegram. For Local Files, leave ", _jsx("strong", { children: "Upload images" }), " unchecked to use the existing video preparation workflow, or check it to send supported images as Telegram documents. Verified image uploads are indexed in Photos; local originals are deleted only after byte-level destination verification when cleanup is enabled."] })] }), _jsx(Stepper, { steps: STEPS, step: step, onStep: setStep, canGo: canNext }), step === 0 ? (_jsxs("div", { className: "grid gap-4", children: [_jsxs("div", { children: [_jsx("h3", { className: "text-[15px] font-bold", children: "1 \u00B7 Source" }), _jsx("p", { className: "text-sm text-muted", children: "Choose where media comes from. The fields change between Local files and Telegram media." })] }), _jsxs("div", { className: "grid gap-2", children: [_jsx(Segmented, { label: "Transfer source", value: source, onChange: (v) => { setSource(v); setMsg(''); }, options: [{ value: 'local', label: 'Local files' }, { value: 'telegram', label: 'Telegram media' }] }), _jsx("span", { className: "text-xs text-faint", children: "No CLI needed \u2014 Archive X runs this job in the background." })] }), source === 'local' ? (_jsxs(_Fragment, { children: [_jsxs(Field, { label: "Source folder", children: [_jsxs("span", { className: "flex gap-2", children: [_jsx("input", { type: "text", value: local.folder, onChange: (e) => setL('folder', e.target.value), placeholder: "Choose a folder or enter its path\u2026", "aria-label": "Source folder path", autoComplete: "off", className: FIELD }), _jsx("button", { type: "button", onClick: () => void browse(), className: "h-11 shrink-0 rounded-xl border border-line-2 px-3 text-sm font-semibold", children: "Browse\u2026" }), _jsx("button", { type: "button", onClick: () => void saveFolder(), className: "h-11 shrink-0 rounded-xl border border-line-2 px-3 text-sm font-semibold", children: "Save folder" })] }), _jsx("span", { role: "status", className: "min-h-4 text-[11px] font-normal text-faint", children: folderMsg })] }), _jsx(Check, { checked: local.images, onChange: (v) => setL('images', v), title: "Upload images", hint: "When enabled, supported images are sent to Telegram as documents. Verified images are added to Photos." }), destination(local.destination, (v) => setL('destination', v), 'Destination channel')] })) : (_jsxs(_Fragment, { children: [_jsxs("div", { children: [_jsx("p", { className: "mb-1.5 text-xs font-semibold text-muted", children: "Source channel(s)" }), _jsxs("div", { className: "mb-2 flex gap-2", children: [_jsx("input", { type: "search", value: q, onChange: (e) => setQ(e.target.value), placeholder: "Search channels...", "aria-label": "Search channels", className: FIELD }), _jsx("button", { type: "button", onClick: () => setT('channels', chans.map((c) => c.telegram_id)), className: "h-11 rounded-xl border border-line-2 px-3 text-xs font-bold", children: "All" }), _jsx("button", { type: "button", onClick: () => setT('channels', []), className: "h-11 rounded-xl border border-line-2 px-3 text-xs font-bold", children: "Clear" })] }), _jsx("div", { role: "group", "aria-label": "Source channels", className: "max-h-44 overflow-auto rounded-xl border border-line bg-bg p-1", children: channels.isPending ? _jsx("p", { className: "p-3 text-sm text-muted", children: "Loading channels\u2026" }) : visible.length ? visible.map((c) => _jsx(Check, { checked: tg.channels.includes(c.telegram_id), onChange: () => toggle(c.telegram_id), title: c.title, icon: _jsx(ChannelAvatar, { id: c.telegram_id, title: c.title, image: c.image }), hint: videosHint(libraryCount.get(c.telegram_id) ?? c.video_count) }, c.telegram_id)) : _jsx("p", { className: "p-3 text-sm text-muted", children: "No channels match." }) }), _jsxs("p", { className: "mt-2 text-xs text-muted", "aria-live": "polite", children: ["Selected: ", tg.channels.length, " channel", tg.channels.length === 1 ? '' : 's'] })] }), destination(tg.destination, (v) => setT('destination', v), 'Destination channel')] })), _jsxs("div", { className: "rounded-xl border border-line bg-bg p-3 text-xs text-muted", children: [_jsx("b", { className: "block text-sm text-fg", children: "Preparation-first workflow" }), "Media is inventoried first. Existing metadata and playback assets are reused; new or incomplete records are prepared and saved before delivery."] })] })) : step === 1 ? (_jsxs("div", { className: "grid gap-4", children: [_jsx("div", { children: _jsx("h3", { className: "text-[15px] font-bold", children: "2 \u00B7 Options & Checks" }) }), source === 'local' ? (_jsxs(_Fragment, { children: [!local.images ? (_jsxs(_Fragment, { children: [_jsx(Field, { label: "Video orientation", hint: "Uses the displayed orientation, including phone rotation metadata. A specific orientation skips files with unknown dimensions.", children: _jsx(Select, { label: "Video orientation for local videos", value: local.orientation, onChange: (v) => setL('orientation', v), options: ORIENTATIONS }) }), _jsx(Field, { label: "Source resolution", hint: "Filters original files; no re-encoding. A specific tier skips unknown dimensions.", children: _jsx(Select, { label: "Source resolution for local videos", value: local.resolution, onChange: (v) => setL('resolution', v), options: RESOLUTIONS }) })] })) : _jsx("p", { className: "text-sm text-muted", children: "Image uploads have no video filters." }), _jsxs("div", { className: "rounded-xl border border-line p-2", children: [_jsx(Check, { checked: local.subfolders, onChange: (v) => setL('subfolders', v), title: "Include subfolders" }), _jsx(Check, { checked: local.deleteSource, onChange: (v) => setL('deleteSource', v), title: "Delete local source after verified delivery" })] }), !local.images ? (_jsx(Field, { label: "When a file exceeds Telegram's safe upload limit", hint: "Optimize keeps one Telegram file but re-encodes when needed. Skip leaves oversized files on your device without uploading them.", children: _jsx(Select, { label: "Oversized file handling", value: local.oversized, onChange: (v) => setL('oversized', v), options: [['optimize', 'Optimize to fit (may reduce quality; original preserved)'], ['skip', 'Skip oversized files (keep local source)']] }) })) : null] })) : (_jsxs(_Fragment, { children: [_jsxs("div", { className: "grid grid-cols-2 gap-3", children: [_jsx(Field, { label: "Source media", children: _jsx(Select, { label: "Source media", value: tg.sourceMedia, onChange: (v) => setT('sourceMedia', v), options: [['VIDEO', 'Video'], ['FILE', 'File / document']] }) }), _jsx(Field, { label: "Destination media", children: _jsx(Select, { label: "Destination media", value: tg.destinationMedia, onChange: (v) => setT('destinationMedia', v), options: [['VIDEO', 'Video'], ['FILE', 'File / document']] }) })] }), _jsx(Field, { label: "Video orientation", children: _jsx(Select, { label: "Video orientation", value: tg.orientation, onChange: (v) => setT('orientation', v), options: ORIENTATIONS }) }), _jsx(Field, { label: "Source resolution", hint: "Filters source videos; no re-encoding. A specific tier skips unknown dimensions.", children: _jsx(Select, { label: "Source resolution for Telegram videos", value: tg.resolution, onChange: (v) => setT('resolution', v), options: RESOLUTIONS }) }), _jsxs("div", { className: "rounded-xl border border-line p-2", children: [_jsx("p", { className: "px-2 pb-1 pt-1 text-xs font-bold text-muted", children: "Duplicate checks and cleanup" }), _jsx(Check, { checked: tg.dupSource, onChange: (v) => setT('dupSource', v), title: "Check source duplicates" }), _jsx(Check, { checked: tg.dupDestination, onChange: (v) => setT('dupDestination', v), title: "Check destination" }), _jsx(Check, { checked: tg.dupDb, onChange: (v) => setT('dupDb', v), title: "Check Archive-X DB" }), _jsx(Check, { checked: tg.cleanup, onChange: (v) => setT('cleanup', v), title: "Cleanup broken/orphaned" })] }), tg.cleanup ? destination(tg.trash, (v) => setT('trash', v), 'Trash channel') : null] })), problem ? _jsx("p", { role: "alert", className: "text-sm text-warn", children: problem }) : null] })) : step === 2 ? (_jsxs("div", { children: [_jsx("h3", { className: "mb-2 text-[15px] font-bold", children: "3 \u00B7 Review" }), _jsx("dl", { className: "grid gap-1.5 rounded-xl bg-bg p-3 text-sm", children: (source === 'local'
                            ? [['Source', 'Local files'], ['Media', local.images ? 'Images (as documents)' : 'Videos'], ['Folder', local.folder || '—'], ['Destination', local.destination ? nameOf(local.destination) : '—'], ['Include subfolders', local.subfolders ? 'Yes' : 'No'], ['Delete local source after delivery', local.deleteSource ? 'Yes' : 'No'], ...(local.images ? [] : [['Orientation', local.orientation], ['Resolution', local.resolution], ['Oversized files', local.oversized === 'optimize' ? 'Optimize to fit' : 'Skip']])]
                            : [['Source', 'Telegram media'], ['From', tg.channels.map(nameOf).join(', ') || '—'], ['Destination', tg.destination ? nameOf(tg.destination) : '—'], ['Media', `${tg.sourceMedia === 'VIDEO' ? 'Video' : 'File'} → ${tg.destinationMedia === 'VIDEO' ? 'Video' : 'File'}`], ['Orientation', tg.orientation], ['Resolution', tg.resolution], ['Duplicate checks', [tg.dupSource && 'source', tg.dupDestination && 'destination', tg.dupDb && 'database'].filter(Boolean).join(', ') || 'none'], ['Cleanup', tg.cleanup ? `Yes → ${nameOf(tg.trash)}` : 'No']]).map(([k, v]) => _jsxs("div", { className: "grid grid-cols-[minmax(0,150px)_minmax(0,1fr)] gap-3", children: [_jsx("dt", { className: "text-muted", children: k }), _jsx("dd", { className: "font-semibold [overflow-wrap:anywhere]", children: v })] }, k)) }), problem ? _jsx("p", { role: "alert", className: "mt-2 text-sm text-warn", children: problem }) : _jsx("p", { className: "mt-2 text-sm text-muted", children: "Everything needed is set. Continue to Run." })] })) : (_jsxs("div", { className: "grid gap-3", children: [_jsx("h3", { className: "text-[15px] font-bold", children: "4 \u00B7 Run" }), _jsx("div", { children: _jsx("button", { type: "button", disabled: busy, onClick: () => void run(), className: "h-11 rounded-xl bg-accent px-6 text-sm font-extrabold text-accent-fg disabled:opacity-60", children: busy ? 'Starting…' : source === 'local' ? (local.images ? 'Start image upload' : 'Start local transfer') : 'Start Telegram transfer' }) }), _jsx("p", { className: "min-h-5 text-xs text-muted", "aria-live": "polite", children: msg })] })), _jsxs("div", { className: "mt-auto flex justify-between gap-2 pt-5", children: [_jsx("button", { type: "button", disabled: step === 0, onClick: () => setStep(step - 1), className: "h-10 rounded-xl border border-line-2 px-4 text-sm font-semibold disabled:opacity-40", children: "Back" }), step < 3 ? _jsx("button", { type: "button", disabled: !canNext, onClick: () => setStep(step + 1), className: "h-10 rounded-xl bg-accent px-4 text-sm font-bold text-accent-fg disabled:opacity-50", children: ['Next: Options & Checks →', 'Next: Review →', 'Next: Run →'][step] }) : _jsx("span", {})] }), ask ? (_jsx(Dialog, { open: true, onClose: () => setAsk(null), title: "Photos in subfolders", children: _jsxs("div", { className: "grid grid-cols-[minmax(0,1fr)] gap-4", children: [_jsx("p", { className: "text-sm text-muted", children: ask.message }), _jsxs("div", { className: "flex flex-wrap justify-end gap-2", children: [_jsx("button", { type: "button", onClick: () => void answer(false), className: "h-11 rounded-xl border border-line-2 px-4 text-sm font-semibold", children: Number(ask.scan.direct_count) === 0 ? 'Cancel' : 'Only direct photos' }), _jsx("button", { type: "button", onClick: () => void answer(true), className: "h-11 rounded-xl bg-accent px-4 text-sm font-bold text-accent-fg", children: "Include subfolders" })] })] }) })) : null] }));
}
