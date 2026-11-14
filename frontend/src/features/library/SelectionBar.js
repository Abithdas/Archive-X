import { jsx as _jsx, jsxs as _jsxs, Fragment as _Fragment } from "react/jsx-runtime";
import { zoomOf } from '@/design/placement';
import { useQueryClient } from '@tanstack/react-query';
import { useId, useRef, useState } from 'react';
import { toast } from 'sonner';
import { ApiError, api } from '@/api/client';
import { Dialog } from '@/design/Dialog';
import { Icon } from '@/design/Icon';
import { cn } from '@/design/cn';
import { BulkMapDialog } from './BulkMapDialog';
import { BulkSend } from './BulkSend';
import { NONE_PICKED, UNLOCKED, bulkBody, countLabel, recheckMessage, titleProblem, unknownConfirm, unknownOk, unlockBody, updatedMessage } from './bulk';
import { pickedList, useSelection } from './selection';
const BAR_BTN = 'h-9 shrink-0 rounded-lg border border-line-2 bg-surface px-3 text-[13px] font-semibold hover:bg-fg/5 disabled:opacity-50';
const ITEM = 'flex h-10 w-full items-center gap-2 rounded-lg px-3 text-left text-sm font-medium hover:bg-fg/5 disabled:opacity-50';
/** A small dropdown that opens upward, since the bar sits at the bottom of the screen. */
function BarMenu({ label, children, disabled }) {
    const id = useId();
    const pop = useRef(null);
    const btn = useRef(null);
    const place = () => {
        const el = pop.current, b = btn.current?.getBoundingClientRect();
        if (!el || !b)
            return;
        const z = zoomOf(btn.current);
        const w = 224;
        el.style.left = `${Math.max(8, Math.min(window.innerWidth / z - w - 8, b.left / z))}px`;
        el.style.bottom = `${(window.innerHeight - b.top) / z + 6}px`;
    };
    return (_jsxs(_Fragment, { children: [_jsxs("button", { ref: btn, type: "button", popoverTarget: id, "aria-haspopup": "menu", disabled: disabled, onClick: place, className: cn(BAR_BTN, 'inline-flex items-center gap-1.5'), children: [label, _jsx(Icon, { name: "chevron-down", className: "size-3.5" })] }), _jsx("div", { ref: pop, id: id, popover: "auto", role: "menu", "aria-label": label, className: "fixed m-0 w-56 rounded-2xl border border-line-2 bg-surface-2 p-1.5 text-fg shadow-[0_18px_40px_-8px_rgb(0_0_0/0.6)] [inset:auto]", children: children(() => pop.current?.hidePopover?.()) })] }));
}
/** The classic "mapping mode" bar: what to do with the picked videos. */
export function SelectionBar({ visible }) {
    const qc = useQueryClient();
    const { picked, clear, exit, selectMany } = useSelection();
    const list = pickedList(picked);
    const [ask, setAsk] = useState(null);
    const [title, setTitle] = useState('');
    const [titleMsg, setTitleMsg] = useState('');
    const [busy, setBusy] = useState(false);
    const [sending, setSending] = useState(false);
    const [mapMode, setMapMode] = useState(null);
    const first = visible.find((v) => list.some((p) => p.channel_id === v._cid && p.message_id === v.id));
    const prefill = { performer: first?.performers[0], studio: first?.studio || undefined };
    const refresh = () => { void qc.invalidateQueries({ queryKey: ['library'] }); void qc.invalidateQueries({ queryKey: ['entities'] }); void qc.invalidateQueries({ queryKey: ['channels'] }); void qc.invalidateQueries({ queryKey: ['performer'] }); void qc.invalidateQueries({ queryKey: ['studio'] }); };
    const need = (fn) => () => (list.length ? fn() : void toast(NONE_PICKED));
    const detail = (e, fallback) => (e instanceof ApiError ? e.detail || fallback : fallback);
    const applyBulk = async (field, action, target = '') => {
        setBusy(true);
        try {
            const r = await api('/api/mapping/bulk', { method: 'POST', json: bulkBody(list, field, action, target) });
            if (!r?.ok)
                throw new ApiError(0, r?.detail || 'Mapping failed');
            toast(updatedMessage(r.changed, list.length));
            clear();
            refresh();
        }
        catch (e) {
            toast.error(detail(e, 'Mapping failed'));
        }
        finally {
            setBusy(false);
        }
    };
    const recheck = async () => {
        try {
            toast(recheckMessage(await api('/api/mapping/recheck', { method: 'POST', json: { videos: list } }), list.length));
        }
        catch (e) {
            toast.error(detail(e, 'Recheck failed'));
        }
    };
    const unlock = async () => {
        try {
            await api('/api/mapping/unlock', { method: 'POST', json: unlockBody(list) });
            toast(UNLOCKED);
        }
        catch (e) {
            toast.error(detail(e, 'Unlock failed'));
        }
    };
    const submitTitle = () => {
        const p = titleProblem(title);
        if (p)
            return void setTitleMsg(p);
        setAsk(null);
        void applyBulk('title', 'move', title);
        setTitle('');
    };
    return (_jsxs(_Fragment, { children: [_jsxs("div", { role: "toolbar", "aria-label": "Edit multiple videos", className: "sticky bottom-[84px] z-30 mt-4 flex flex-wrap items-center gap-2 rounded-2xl border border-line-2 bg-surface-2/95 p-2.5 shadow-[0_18px_40px_-8px_rgb(0_0_0/0.6)] backdrop-blur md:bottom-3 md:flex-nowrap", children: [_jsxs("div", { className: "order-1 flex w-full items-center gap-2 md:order-2 md:ml-auto md:w-auto", children: [_jsx("span", { role: "status", "aria-live": "polite", className: "rounded-lg bg-accent/15 px-2.5 py-1.5 text-[13px] font-bold text-accent", children: countLabel(list.length) }), _jsx("span", { className: "ml-auto md:hidden" }), _jsx("button", { type: "button", onClick: () => selectMany(visible), className: BAR_BTN, children: "Select all" }), _jsx("button", { type: "button", onClick: clear, className: BAR_BTN, children: "Deselect" }), _jsx("button", { type: "button", onClick: exit, className: "h-9 shrink-0 rounded-lg bg-accent px-4 text-[13px] font-bold text-accent-fg", children: "Done" })] }), _jsxs("div", { className: "order-2 flex w-full min-w-0 items-center gap-2 overflow-x-auto md:order-1 md:w-auto md:overflow-visible", children: [_jsx("span", { className: "hidden text-[11px] text-muted lg:inline", children: "Pick videos, then choose what to change" }), _jsx(BarMenu, { label: "Performer / Studio", disabled: busy, children: (close) => (_jsxs(_Fragment, { children: [_jsx("button", { type: "button", role: "menuitem", className: ITEM, onClick: () => { close(); need(() => setMapMode('performer'))(); }, children: "Set Performer" }), _jsx("button", { type: "button", role: "menuitem", className: ITEM, onClick: () => { close(); need(() => setMapMode('site'))(); }, children: "Set Studio" }), _jsx("button", { type: "button", role: "menuitem", className: ITEM, onClick: () => { close(); need(() => setMapMode('both'))(); }, children: "Set Performer + Studio" }), _jsx("button", { type: "button", role: "menuitem", className: ITEM, onClick: () => { close(); need(() => setAsk({ kind: 'unknown', field: 'performer' }))(); }, children: "Clear Performer" }), _jsx("button", { type: "button", role: "menuitem", className: ITEM, onClick: () => { close(); need(() => setAsk({ kind: 'unknown', field: 'site' }))(); }, children: "Clear Studio" })] })) }), _jsx(BarMenu, { label: "Metadata", disabled: busy, children: (close) => (_jsxs(_Fragment, { children: [_jsxs("button", { type: "button", role: "menuitem", className: ITEM, onClick: () => { close(); need(() => void recheck())(); }, children: [_jsx(Icon, { name: "refresh", className: "size-4 text-muted" }), "Recheck Metadata"] }), _jsx("button", { type: "button", role: "menuitem", className: ITEM, onClick: () => { close(); need(() => { setTitleMsg(''); setAsk({ kind: 'title' }); })(); }, children: "Edit Title" }), _jsx("button", { type: "button", role: "menuitem", className: ITEM, onClick: () => { close(); need(() => setMapMode('all'))(); }, children: "Apply All Metadata" })] })) }), _jsx(BarMenu, { label: "More", disabled: busy, children: (close) => (_jsxs(_Fragment, { children: [_jsx("button", { type: "button", role: "menuitem", className: cn(ITEM, 'text-bad'), onClick: () => { close(); need(() => setAsk({ kind: 'unknown', field: 'all' }))(); }, children: "Move All to Unknown" }), _jsx("button", { type: "button", role: "menuitem", className: ITEM, onClick: () => { close(); need(() => void unlock())(); }, children: "Unlock" })] })) }), _jsxs("button", { type: "button", disabled: !list.length, onClick: () => setSending(true), title: "Send all selected videos to a Telegram channel", className: "inline-flex h-9 shrink-0 items-center gap-1.5 rounded-lg bg-accent px-3.5 text-[13px] font-bold text-accent-fg disabled:opacity-50", children: [_jsx(Icon, { name: "brand-telegram", className: "size-4" }), "Send to Telegram"] })] })] }), ask?.kind === 'title' ? (_jsx(Dialog, { open: true, onClose: () => setAsk(null), title: "Correct video title", children: _jsxs("form", { className: "grid grid-cols-[minmax(0,1fr)] gap-4", onSubmit: (e) => { e.preventDefault(); submitTitle(); }, children: [_jsx("p", { className: "text-sm text-muted", children: "Enter the corrected video title." }), _jsx("input", { value: title, onChange: (e) => { setTitle(e.target.value); setTitleMsg(''); }, placeholder: "Video title", "aria-label": "Video title", autoFocus: true, className: "h-12 w-full min-w-0 rounded-xl border border-line-2 bg-bg px-3.5 text-[15px] outline-none placeholder:text-faint focus:border-accent/60" }), titleMsg ? _jsx("p", { role: "alert", className: "text-sm text-warn", children: titleMsg }) : null, _jsxs("div", { className: "flex flex-wrap justify-end gap-2", children: [_jsx("button", { type: "button", onClick: () => setAsk(null), className: "h-11 rounded-xl border border-line-2 px-4 text-sm font-semibold", children: "Cancel" }), _jsx("button", { type: "submit", className: "h-11 rounded-xl bg-accent px-5 text-sm font-bold text-accent-fg", children: "Apply value" })] })] }) })) : null, ask?.kind === 'unknown' ? (_jsx(Dialog, { open: true, onClose: () => setAsk(null), title: "Set to Unknown?", children: _jsxs("div", { className: "grid grid-cols-[minmax(0,1fr)] gap-4", children: [_jsx("p", { className: "text-sm text-muted", children: unknownConfirm(ask.field, list.length) }), _jsxs("div", { className: "flex flex-wrap justify-end gap-2", children: [_jsx("button", { type: "button", onClick: () => setAsk(null), className: "h-11 rounded-xl border border-line-2 px-4 text-sm font-semibold", children: "Cancel" }), _jsx("button", { type: "button", onClick: () => { const f = ask.field; setAsk(null); void applyBulk(f, 'unknown'); }, className: "h-11 rounded-xl bg-bad px-4 text-sm font-bold text-white", children: unknownOk(ask.field) })] })] }) })) : null, mapMode ? _jsx(BulkMapDialog, { mode: mapMode, picked: list, prefill: prefill, onDone: (applied) => { setMapMode(null); if (applied)
                    clear(); } }) : null, sending ? _jsx(BulkSend, { picked: list, onDone: () => setSending(false) }) : null] }));
}
