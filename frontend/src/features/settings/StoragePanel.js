import { jsxs as _jsxs, jsx as _jsx } from "react/jsx-runtime";
import { useQueryClient } from '@tanstack/react-query';
import { useEffect, useRef, useState } from 'react';
import { toast } from 'sonner';
import { ApiError, api } from '@/api/client';
import { Dialog } from '@/design/Dialog';
import { Icon } from '@/design/Icon';
import { Switch } from '@/design/Switch';
import { cn } from '@/design/cn';
import { formatBytes } from '@/design/format';
import { useBackupInfo, useCacheUsage, useStorageInfo } from './queries';
import { BACKUP_TYPES, BUDGETS, DEFAULT_POLICY, PROFILES, RAMS, READAHEADS, RETENTIONS, backupFrom, backupPasswordProblem, backupRows, backupSettingsBody, backupStatusLine, moveBlocked, moveConfirmText, moveProblem, moveWarning, policyBody, policyFrom, policyProblem, restorePreview, retentionNote, savedMessage, usageCards, } from './storage';
const BOX = 'rounded-[18px] border border-line bg-surface p-5';
const INPUT = 'h-12 w-full min-w-0 rounded-xl border border-line-2 bg-bg px-3.5 text-[15px] text-fg outline-none placeholder:text-faint focus:border-accent/60 disabled:opacity-50';
const SELECT = 'h-11 w-full rounded-xl border border-line-2 bg-bg px-3 text-sm font-medium';
const BTN = 'h-10 rounded-xl border border-line-2 px-4 text-sm font-semibold hover:bg-fg/5 disabled:opacity-50';
const PRIMARY = 'h-10 rounded-xl bg-accent px-5 text-sm font-bold text-accent-fg disabled:opacity-60';
const err = (e, fallback) => (e instanceof ApiError ? e.detail || fallback : fallback);
const post = (path, body = {}) => api(path, { method: 'POST', json: body });
const Field = ({ label, children }) => _jsxs("label", { className: "grid gap-1.5 text-xs font-semibold text-muted", children: [label, children] });
function Ask({ title, text, ok, danger, onCancel, onOk, children }) {
    return (_jsx(Dialog, { open: true, onClose: onCancel, title: title, children: _jsxs("div", { className: "grid grid-cols-[minmax(0,1fr)] gap-4", children: [text ? _jsx("p", { className: "whitespace-pre-line text-sm text-muted [overflow-wrap:anywhere]", children: text }) : null, children, _jsxs("div", { className: "flex flex-wrap justify-end gap-2", children: [_jsx("button", { type: "button", onClick: onCancel, className: "h-11 rounded-xl border border-line-2 px-4 text-sm font-semibold", children: "Cancel" }), _jsx("button", { type: "button", onClick: onOk, className: cn('h-11 rounded-xl px-4 text-sm font-bold', danger ? 'bg-bad text-white' : 'bg-accent text-accent-fg'), children: ok })] })] }) }));
}
/** Storage & Backup: cache limits and usage, the data folder, scheduled and manual backups, and restore. */
export function StoragePanel() {
    const qc = useQueryClient();
    const usage = useCacheUsage();
    const storage = useStorageInfo();
    const backups = useBackupInfo();
    const [pending, setPending] = useState(null);
    const [verifyPw, setVerifyPw] = useState('');
    // ---- cache
    const [policy, setPolicy] = useState(DEFAULT_POLICY);
    const [cacheMsg, setCacheMsg] = useState('');
    const policyLoaded = useRef(false);
    useEffect(() => { if (usage.data && !policyLoaded.current) {
        policyLoaded.current = true;
        setPolicy(policyFrom(usage.data));
    } }, [usage.data]);
    const refreshCache = () => qc.invalidateQueries({ queryKey: ['settings', 'cache-usage'] });
    const saveCache = async (next) => {
        const p = policyProblem(next);
        if (p)
            return void setCacheMsg(p);
        setCacheMsg('Saving cache settings…');
        try {
            const r = await post('/api/settings/cache-policy', policyBody(next));
            if (!r.ok)
                throw new ApiError(0, String(r.detail ?? 'Could not save cache settings'));
            setCacheMsg(savedMessage(r, formatBytes));
            await refreshCache();
        }
        catch (e) {
            setCacheMsg(`Could not save cache settings: ${err(e, '')}`);
        }
    };
    const change = (k, v) => { const next = { ...policy, [k]: v }; setPolicy(next); void saveCache(next); };
    const u = (usage.data ?? {});
    const budgets = BUDGETS.includes(policy.budget) ? [...BUDGETS] : [...BUDGETS, policy.budget].sort((a, b) => a - b);
    const release = async () => {
        setCacheMsg('Releasing kept buffers…');
        try {
            const r = await post('/api/settings/release-kept-video-buffers');
            if (!r.ok)
                throw new ApiError(0, String(r.detail ?? ''));
            try {
                localStorage.removeItem('ax_nobuf_kept_buffers_v1');
                localStorage.removeItem('ax_nobuf_buffer_decisions_v1');
            }
            catch { /* private mode */ }
            setCacheMsg(`Released ${Number(r.released ?? 0)} kept video${Number(r.released ?? 0) === 1 ? '' : 's'}; they now follow the retention setting.`);
            await refreshCache();
        }
        catch (e) {
            setCacheMsg(`Could not release kept buffers: ${err(e, '')}`);
        }
    };
    // ---- data folder
    const info = (storage.data ?? {});
    const [dest, setDest] = useState('');
    const [moveMsg, setMoveMsg] = useState('');
    useEffect(() => { if (!dest && info.pending_path)
        setDest(info.pending_path); }, [info.pending_path]); // eslint-disable-line react-hooks/exhaustive-deps
    const refreshStorage = () => { void qc.invalidateQueries({ queryKey: ['settings', 'storage'] }); void qc.invalidateQueries({ queryKey: ['storage'] }); };
    const browseData = async () => {
        setMoveMsg('Opening folder picker…');
        try {
            const r = await post('/api/settings/storage/browse');
            if (r.selected_path)
                setDest(String(r.selected_path));
            setMoveMsg(r.selected_path ? 'Folder selected.' : 'No folder selected.');
        }
        catch (e) {
            setMoveMsg(err(e, 'Folder picker unavailable.'));
        }
    };
    const cancelMove = async () => {
        try {
            await post('/api/settings/storage/cancel-move');
            setDest('');
            setMoveMsg('Pending move cancelled.');
            refreshStorage();
        }
        catch (e) {
            setMoveMsg(err(e, 'Could not cancel move.'));
        }
    };
    // ---- backups
    const bi = backups.data;
    const [bk, setBk] = useState(backupFrom(undefined));
    const [bkMsg, setBkMsg] = useState('');
    const [bkType, setBkType] = useState('full_application');
    const [bkPw, setBkPw] = useState('');
    const [restorePath, setRestorePath] = useState('');
    const [restorePw, setRestorePw] = useState('');
    const [restoreMsg, setRestoreMsg] = useState('');
    const bkLoaded = useRef(false);
    useEffect(() => { if (bi && !bkLoaded.current) {
        bkLoaded.current = true;
        setBk(backupFrom(bi));
    } }, [bi]);
    const setB = (k, v) => setBk((b) => ({ ...b, [k]: v }));
    const refreshBackups = () => { bkLoaded.current = false; return qc.invalidateQueries({ queryKey: ['settings', 'backup'] }); };
    const browseRepo = async () => {
        try {
            const r = await post('/api/settings/folder-picker', { title: 'Select Archive X backup repository', initial_path: bk.repository, must_exist: false, allow_create: true });
            if (!r.cancelled && r.path) {
                setB('repository', String(r.path));
                setBkMsg('Folder selected. Click Save backup settings to remember it.');
            }
        }
        catch (e) {
            setBkMsg(err(e, 'Folder picker unavailable.'));
        }
    };
    const saveBackup = async () => {
        setBkMsg('Saving…');
        try {
            const r = await post('/api/settings/backup', backupSettingsBody(bk));
            if (r.ok === false)
                throw new ApiError(0, String(r.detail ?? ''));
            setBkMsg('Backup settings saved.');
            await refreshBackups();
        }
        catch (e) {
            setBkMsg(`Could not save: ${err(e, 'unknown error')}`);
        }
    };
    const createBackup = async () => {
        const p = backupPasswordProblem(bkPw);
        if (p)
            return void setBkMsg(p);
        try {
            const r = await post('/api/backup/create', { backup_type: bkType, password: bkPw, repository: bk.repository });
            setBkMsg(`Backup queued: ${String(r.id ?? '')}`);
            toast('Backup queued in Operations.');
            setBkPw('');
            void qc.invalidateQueries({ queryKey: ['status'] });
        }
        catch (e) {
            setBkMsg(err(e, 'Could not create backup.'));
        }
    };
    const previewRestore = async () => {
        try {
            setRestoreMsg(restorePreview(await post('/api/backup/restore/preview', { path: restorePath, password: restorePw })));
        }
        catch (e) {
            setRestoreMsg(err(e, 'Backup validation failed.'));
        }
    };
    const confirmed = async () => {
        const c = pending;
        setPending(null);
        if (!c)
            return;
        if (c.kind === 'clear') {
            setCacheMsg('Clearing video byte cache…');
            try {
                const r = await post('/api/settings/clear-video-byte-cache');
                if (!r.ok)
                    throw new ApiError(0, String(r.detail ?? ''));
                setCacheMsg(`Cleared ${formatBytes(Number(r.bytes_cleared ?? 0))} from ${Number(r.entries_cleared ?? 0)} videos`);
                await refreshCache();
            }
            catch (e) {
                setCacheMsg(`Could not clear cache: ${err(e, '')}`);
            }
        }
        else if (c.kind === 'move') {
            setMoveMsg('Preparing data move…');
            try {
                await post('/api/settings/storage/move', { destination: dest.trim() });
                setMoveMsg('Move prepared. Restart Archive X to complete it.');
                refreshStorage();
            }
            catch (e) {
                setMoveMsg(err(e, 'Could not prepare data move.'));
            }
        }
        else if (c.kind === 'restore') {
            try {
                await post('/api/backup/restore', { path: restorePath, password: restorePw });
                setRestoreMsg('Restore staged successfully. Restart Archive X to apply it.');
                toast('Restore staged. Restart Archive X to apply.');
            }
            catch (e) {
                setRestoreMsg(err(e, 'Could not stage restore.'));
            }
        }
        else if (c.kind === 'delete') {
            try {
                await post('/api/backup/delete', { path: c.path });
                await refreshBackups();
            }
            catch (e) {
                toast.error(err(e, 'Could not delete backup.'));
            }
        }
        else {
            try {
                const r = await post('/api/backup/verify', { path: c.path, password: verifyPw });
                toast(`Backup verified: ${Number(r.manifest?.files?.length ?? 0).toLocaleString()} files.`);
                setVerifyPw('');
                await refreshBackups();
            }
            catch (e) {
                toast.error(err(e, 'Backup verification failed.'));
            }
        }
    };
    const warn = moveWarning(info);
    const blocked = moveBlocked(info);
    const rows = backupRows(bi);
    const cards = usageCards(u, formatBytes);
    const d = info.disk;
    return (_jsxs("div", { className: "grid gap-4", children: [_jsxs("section", { "aria-label": "Storage", className: BOX, children: [_jsxs("div", { className: "mb-4 flex items-center justify-between", children: [_jsxs("h3", { className: "flex items-center gap-3 text-xl font-extrabold", children: [_jsx("span", { className: "text-accent", children: _jsx(Icon, { name: "folders", className: "size-5" }) }), "Storage"] }), _jsx("span", { className: "text-xs text-faint", children: "06 / 08" })] }), _jsxs("div", { className: "rounded-2xl border border-line p-4", children: [_jsx("h4", { className: "mb-3 text-lg font-extrabold", children: "Archive X cache disk usage" }), _jsxs("div", { className: "grid gap-3 sm:grid-cols-2 lg:grid-cols-3", children: [_jsx(Field, { label: "Video byte-cache limit (GiB)", children: _jsx("select", { value: policy.budget, onChange: (e) => change('budget', Number(e.target.value)), "aria-label": "Video byte-cache limit", className: SELECT, children: budgets.map((b) => _jsxs("option", { value: b, children: [b, " GiB", b === 50 ? ' (default)' : BUDGETS.includes(b) ? '' : ' (custom)'] }, b)) }) }), _jsx(Field, { label: "Clear downloaded ranges", children: _jsx("select", { value: policy.retention, onChange: (e) => change('retention', e.target.value), "aria-label": "Clear downloaded ranges", className: SELECT, children: RETENTIONS.map(([k, l]) => _jsx("option", { value: k, children: l }, k)) }) }), _jsx(Field, { label: "Look-ahead buffer", children: _jsx("select", { value: policy.readahead, onChange: (e) => change('readahead', Number(e.target.value)), "aria-label": "Look-ahead buffer", className: SELECT, children: READAHEADS.map(([k, l]) => _jsx("option", { value: k, children: l }, k)) }) }), _jsx(Field, { label: "Connections", children: _jsx("select", { value: policy.profile, onChange: (e) => change('profile', e.target.value), "aria-label": "Connections", className: SELECT, children: PROFILES.map(([k, l]) => _jsx("option", { value: k, children: l }, k)) }) }), _jsx(Field, { label: "Memory buffer", children: _jsx("select", { value: policy.ram, onChange: (e) => change('ram', Number(e.target.value)), "aria-label": "Memory buffer", className: SELECT, children: RAMS.map(([k, l]) => _jsx("option", { value: k, children: l }, k)) }) }), _jsx("div", { className: "flex items-end", children: _jsx("button", { type: "button", onClick: () => void saveCache(policy), className: cn(PRIMARY, 'w-full'), children: "Save cache settings" }) })] }), _jsx("p", { className: "mt-3 text-xs text-muted", children: "Look-ahead is how far ahead of the playhead Archive X keeps fetching; a longer look-ahead rides out slow stretches but uses more memory and bandwidth. Videos you choose \u201CKeep Current Buffers\u201D for are never expired by the rules above. Connections sets how many parallel Telegram requests playback uses: choose Gentle if you ever see flood-wait messages, Fast only on a very fast link; a change to Fast takes full effect after restarting Archive X." }), _jsx("p", { className: "mt-2 text-xs text-muted", children: "Cached video ranges are reused; Archive X requests only missing ranges from Telegram. When the size limit is reached, the oldest inactive cache ranges may be removed. Active and upcoming videos are protected." }), usage.isError ? _jsx("p", { role: "alert", className: "mt-3 text-sm text-warn", children: "Could not read cache sizes." }) : (_jsx("dl", { className: "mt-3 grid gap-3 sm:grid-cols-3", "aria-label": "Cache usage", children: cards.map((c) => _jsxs("div", { className: "rounded-xl bg-bg p-3.5", children: [_jsx("dt", { className: "text-xs text-muted", children: c.label }), _jsx("dd", { className: "mt-1 text-[15px] font-bold", children: usage.isPending ? '…' : c.value })] }, c.label)) })), _jsxs("p", { className: "mt-3 text-xs text-muted", children: [retentionNote(policy.retention), " Keyframe data is included in the MP4 index total. ", Number(u.manifests ?? 0), " MP4 index manifests."] }), _jsxs("div", { className: "mt-3 flex flex-wrap items-center gap-2", children: [_jsx("button", { type: "button", onClick: () => void refreshCache(), className: BTN, children: "Refresh cache usage" }), Number(u.kept_entries) > 0 ? _jsx("button", { type: "button", onClick: () => void release(), className: BTN, children: "Release kept buffers" }) : null, _jsx("button", { type: "button", onClick: () => setPending({ kind: 'clear' }), className: cn(BTN, 'text-bad'), children: "Clear downloaded video-byte cache" }), _jsx("span", { role: "status", "aria-live": "polite", className: "text-xs text-muted", children: cacheMsg })] })] }), _jsxs("div", { className: "mt-4 grid gap-3 md:grid-cols-[minmax(0,2fr)_minmax(0,1fr)_minmax(0,1fr)]", children: [_jsxs("div", { className: "rounded-xl bg-bg p-4", children: [_jsx("p", { className: "text-xs text-muted", children: "Current data folder" }), _jsx("p", { className: "mt-1 break-all text-sm font-bold", children: info.current_path ?? '—' })] }), _jsxs("div", { className: "rounded-xl bg-bg p-4", children: [_jsx("p", { className: "text-xs text-muted", children: "Archive X data" }), _jsx("p", { className: "mt-1 text-sm font-semibold", children: info.bytes != null ? `${formatBytes(Number(info.bytes))} · ${Number(info.files ?? 0).toLocaleString()} files` : '…' })] }), _jsxs("div", { className: "rounded-xl bg-bg p-4", children: [_jsx("p", { className: "text-xs text-muted", children: "Drive" }), _jsx("p", { className: "mt-1 text-sm font-semibold", children: d?.total ? `${formatBytes(Number(d.free))} free of ${formatBytes(Number(d.total))}` : 'Drive capacity unavailable' })] })] }), _jsxs("div", { className: "mt-3 rounded-xl bg-bg p-4", children: [_jsx("h4", { className: "text-sm font-bold", children: "Move Archive X data" }), _jsx("p", { className: "mb-3 mt-1 text-xs text-muted", children: "This moves the database, Telegram session, thumbnails, metadata, preview cache, automation state, logs and other persistent Archive X data. The application files themselves are not moved." }), _jsxs("div", { className: "flex gap-2", children: [_jsx("input", { value: dest, onChange: (e) => setDest(e.target.value), placeholder: "Select a new folder\u2026", "aria-label": "New data folder", className: INPUT }), _jsx("button", { type: "button", onClick: () => void browseData(), className: cn(BTN, 'h-12 shrink-0'), children: "Browse\u2026" })] }), warn ? _jsx("p", { role: "note", className: "mt-3 rounded-lg border border-line-2 px-3 py-2 text-xs text-muted", children: warn }) : null, info.pending_path ? _jsxs("p", { className: "mt-2 text-xs text-warn", children: ["Pending move: ", info.pending_path, " \u00B7 restart Archive X to complete it."] }) : null] }), _jsxs("div", { className: "mt-3 flex flex-wrap items-center gap-2", children: [_jsx("button", { type: "button", onClick: refreshStorage, className: BTN, children: "Refresh" }), _jsx("button", { type: "button", onClick: () => void cancelMove(), disabled: !info.pending_path, className: BTN, children: "Cancel pending move" }), _jsx("button", { type: "button", disabled: blocked, onClick: () => { const p = moveProblem(dest); if (p)
                                    setMoveMsg(p);
                                else
                                    setPending({ kind: 'move' }); }, className: PRIMARY, children: "Move data on next restart" }), _jsx("span", { role: "status", "aria-live": "polite", className: "text-xs text-muted", children: moveMsg })] })] }), _jsxs("section", { "aria-label": "Backup and restore", className: BOX, children: [_jsxs("h3", { className: "mb-4 flex items-center gap-3 text-xl font-extrabold", children: [_jsx("span", { className: "text-accent", children: _jsx(Icon, { name: "database", className: "size-5" }) }), "Backup & Restore"] }), _jsxs("div", { className: "grid gap-3", children: [_jsx(Field, { label: "Backup repository", children: _jsxs("span", { className: "flex gap-2", children: [_jsx("input", { value: bk.repository, onChange: (e) => setB('repository', e.target.value), "aria-label": "Backup repository", className: INPUT }), _jsx("button", { type: "button", onClick: () => void browseRepo(), className: cn(BTN, 'h-12 shrink-0'), children: "Browse\u2026" })] }) }), _jsxs("div", { className: "flex items-center justify-between gap-4 border-y border-line py-3", children: [_jsx("b", { className: "text-[15px]", children: "Enable scheduled backup policy" }), _jsx(Switch, { label: "Enable scheduled backup policy", checked: bk.enabled, onChange: (v) => setB('enabled', v) })] }), _jsx(Field, { label: "Daily time", children: _jsx("input", { type: "time", value: bk.time, onChange: (e) => setB('time', e.target.value), "aria-label": "Daily time", className: INPUT }) }), _jsx("div", { className: "grid grid-cols-3 gap-3", children: ['daily', 'weekly', 'monthly'].map((k) => _jsx(Field, { label: k[0].toUpperCase() + k.slice(1), children: _jsx("input", { type: "number", min: 0, max: 365, value: bk[k], onChange: (e) => setB(k, Number(e.target.value)), "aria-label": `${k} backups to keep`, className: INPUT }) }, k)) }), _jsxs("div", { className: "flex flex-wrap items-center gap-3", children: [_jsx("button", { type: "button", onClick: () => void saveBackup(), className: PRIMARY, children: "Save backup settings" }), _jsx("span", { role: "status", "aria-live": "polite", className: "text-xs text-muted", children: bkMsg || backupStatusLine(bi) })] })] }), _jsxs("div", { className: "mt-5 rounded-2xl border border-line p-4", children: [_jsx("h4", { className: "text-sm font-bold", children: "Create verified backup" }), _jsx("p", { className: "mb-3 mt-1 text-xs text-muted", children: "Full application backups include the database, authentication/configuration, metadata, assets, automation state and logs. Regenerable player cache and media files are excluded." }), _jsxs("div", { className: "grid gap-3 sm:grid-cols-[220px_minmax(0,1fr)]", children: [_jsx("select", { value: bkType, onChange: (e) => setBkType(e.target.value), "aria-label": "Backup type", className: SELECT, children: BACKUP_TYPES.map(([k, l]) => _jsx("option", { value: k, children: l }, k)) }), _jsx("input", { type: "password", value: bkPw, onChange: (e) => setBkPw(e.target.value), placeholder: "Optional encryption password (8+ chars)", "aria-label": "Backup encryption password", autoComplete: "new-password", className: INPUT })] }), _jsxs("div", { className: "mt-3 flex gap-2", children: [_jsx("button", { type: "button", onClick: () => void createBackup(), className: PRIMARY, children: "Create backup" }), _jsx("button", { type: "button", onClick: () => void refreshBackups(), className: BTN, children: "Refresh" })] })] }), _jsxs("div", { className: "mt-5", children: [_jsx("h4", { className: "mb-2 text-sm font-bold", children: "Backup history" }), rows.length ? _jsx("ul", { className: "grid gap-2", "aria-label": "Backup history", children: rows.map((r) => (_jsxs("li", { className: "flex flex-wrap items-center justify-between gap-3 rounded-xl bg-bg p-3", children: [_jsxs("div", { className: "min-w-0", children: [_jsx("b", { className: "block truncate text-sm", children: r.name }), _jsxs("span", { className: "text-xs text-muted", children: [r.created, " \u00B7 ", r.size.toLocaleString(), " bytes \u00B7 ", r.files.toLocaleString(), " files"] })] }), _jsxs("div", { className: "flex items-center gap-2", children: [_jsx("span", { className: cn('rounded-full px-2.5 py-0.5 text-[11px] font-bold', r.verified ? 'bg-good/15 text-good' : 'bg-warn/15 text-warn'), children: r.verified ? 'VERIFIED' : 'CHECK' }), _jsx("button", { type: "button", onClick: () => setPending({ kind: 'verify', path: r.path, name: r.name }), className: "h-8 rounded-lg border border-line-2 px-3 text-xs font-bold", children: "Verify" }), _jsx("button", { type: "button", onClick: () => setPending({ kind: 'delete', path: r.path, name: r.name }), className: "h-8 rounded-lg border border-line-2 px-3 text-xs font-bold text-bad", children: "Delete" })] })] }, r.path))) }) : _jsx("p", { className: "text-sm text-muted", children: backups.isPending ? 'Loading…' : backups.isError ? 'Backup status unavailable.' : 'No Archive X backups found.' })] }), _jsxs("div", { className: "mt-5 rounded-2xl border border-line p-4", children: [_jsx("h4", { className: "text-sm font-bold", children: "Restore backup" }), _jsx("p", { className: "mb-3 mt-1 text-xs text-muted", children: "Restore is staged and applied on the next restart. Archive X first creates a safety backup of the current state. Active jobs must be stopped before restore." }), _jsxs("div", { className: "grid gap-3", children: [_jsx("input", { value: restorePath, onChange: (e) => setRestorePath(e.target.value), placeholder: "Path to .axbackup", "aria-label": "Backup file to restore", className: INPUT }), _jsx("input", { type: "password", value: restorePw, onChange: (e) => setRestorePw(e.target.value), placeholder: "Backup password if encrypted", "aria-label": "Restore password", autoComplete: "off", className: INPUT })] }), _jsxs("div", { className: "mt-3 flex flex-wrap items-center gap-2", children: [_jsx("button", { type: "button", disabled: !restorePath.trim(), onClick: () => void previewRestore(), className: BTN, children: "Validate / Preview" }), _jsx("button", { type: "button", disabled: !restorePath.trim(), onClick: () => setPending({ kind: 'restore' }), className: PRIMARY, children: "Stage restore" })] }), _jsx("p", { role: "status", "aria-live": "polite", className: "mt-3 min-h-4 text-sm text-muted", children: restoreMsg })] })] }), pending?.kind === 'clear' ? _jsx(Ask, { title: "Clear video-byte cache?", text: "Clear all persistently downloaded video bytes? Snapshots, metadata and MP4 indexes will be kept.", ok: "Clear cache", danger: true, onCancel: () => setPending(null), onOk: () => void confirmed() }) : null, pending?.kind === 'move' ? _jsx(Ask, { title: "Move Archive X data folder", text: moveConfirmText(dest.trim()), ok: "Prepare move", danger: true, onCancel: () => setPending(null), onOk: () => void confirmed() }) : null, pending?.kind === 'restore' ? _jsx(Ask, { title: "Stage backup restore", text: "Stage this backup for restore? Archive X will first create a safety backup and apply the restore on the next restart.", ok: "Stage restore", danger: true, onCancel: () => setPending(null), onOk: () => void confirmed() }) : null, pending?.kind === 'delete' ? _jsx(Ask, { title: "Delete backup", text: `Delete this Archive X backup (${pending.name})? This action cannot be undone.`, ok: "Delete backup", danger: true, onCancel: () => setPending(null), onOk: () => void confirmed() }) : null, pending?.kind === 'verify' ? _jsx(Ask, { title: "Verify backup", text: "Enter the backup password, or leave blank if the backup is not encrypted.", ok: "Verify backup", onCancel: () => { setPending(null); setVerifyPw(''); }, onOk: () => void confirmed(), children: _jsx("input", { type: "password", value: verifyPw, onChange: (e) => setVerifyPw(e.target.value), placeholder: "Backup password", "aria-label": "Backup password", autoComplete: "off", autoFocus: true, className: INPUT }) }) : null] }));
}
