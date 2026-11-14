import { jsx as _jsx, jsxs as _jsxs } from "react/jsx-runtime";
import { useQueryClient } from '@tanstack/react-query';
import { useState } from 'react';
import { toast } from 'sonner';
import { api } from '@/api/client';
import { Icon } from '@/design/Icon';
import { PageHead } from '@/design/PageHead';
import { cn } from '@/design/cn';
import { PANELS, isPanel, validationMessage } from './logic';
import { SecurityPanel } from './SecurityPanel';
import { StoragePanel } from './StoragePanel';
import { TelegramPanel } from './TelegramPanel';
import { MetadataPanel } from './MetadataPanel';
import { SystemPanel } from './SystemPanel';
import { AppearancePanel, ExperiencePanel, OverviewPanel } from './panels';
const KEY = 'ax_next_settings_panel';
const read = () => { try {
    const v = localStorage.getItem(KEY);
    return isPanel(v) ? v : 'overview';
}
catch {
    return 'overview';
} };
const settle = (p) => p.then((value) => ({ value }), () => ({ value: undefined }));
export function SettingsPage() {
    const [panel, setPanel] = useState(read);
    const [validating, setValidating] = useState(false);
    const qc = useQueryClient();
    const go = (p) => { setPanel(p); try {
        localStorage.setItem(KEY, p);
    }
    catch { /* private mode */ } };
    const validate = async () => {
        setValidating(true);
        try {
            const [telegram, metadata, web, preflight] = await Promise.all([settle(api('/api/settings/telegram')), settle(api('/api/settings/metadata')), settle(api('/api/settings/weblogin')), settle(api('/api/media-manager/preflight'))]);
            toast(validationMessage({ telegram: telegram.value, metadata: metadata.value, web: web.value, preflight: preflight.value }), { duration: 9000 });
            await qc.invalidateQueries({ queryKey: ['settings'] });
        }
        finally {
            setValidating(false);
        }
    };
    const index = PANELS.findIndex((p) => p.id === panel);
    return (_jsxs("section", { className: "mx-auto w-full max-w-[1800px] pb-10 pl-4 pr-4 pt-6 sm:pl-[58px] sm:pr-10", children: [_jsx(PageHead, { eyebrow: "Configuration", title: "Settings", blurb: "Settings are validated before they are applied" }), _jsxs("div", { className: "grid items-start gap-5 lg:grid-cols-[256px_minmax(0,1fr)]", children: [_jsxs("nav", { "aria-label": "Settings sections", className: "grid gap-1", children: [_jsx("ul", { className: "flex gap-1 overflow-x-auto pb-1 [scrollbar-width:none] lg:grid lg:overflow-visible lg:pb-0", children: PANELS.map((p) => (_jsx("li", { className: "shrink-0 lg:shrink", children: _jsxs("button", { type: "button", "aria-current": p.id === panel ? 'page' : undefined, onClick: () => go(p.id), title: p.blurb, className: cn('flex h-11 w-full items-center gap-3 whitespace-nowrap rounded-xl px-3 text-[15px] font-medium transition-colors', p.id === panel ? 'bg-accent/15 text-fg [&_svg]:text-accent' : 'text-muted hover:bg-fg/5 hover:text-fg'), children: [_jsx(Icon, { name: p.icon }), p.title] }) }, p.id))) }), _jsx("button", { type: "button", disabled: validating, onClick: () => void validate(), className: "mt-3 h-11 rounded-xl border border-line bg-surface text-sm font-bold hover:bg-surface-2 disabled:opacity-60", children: validating ? 'Validating…' : 'Validate configuration' }), _jsx("button", { type: "button", onClick: () => { void qc.invalidateQueries({ queryKey: ['settings'] }); void qc.invalidateQueries({ queryKey: ['storage'] }); toast('Status refreshed'); }, className: "h-11 rounded-xl border border-line bg-surface text-sm font-bold hover:bg-surface-2", children: "Refresh status" })] }), _jsxs("div", { className: "min-w-0", "aria-live": "polite", children: [panel === 'overview' ? _jsx(OverviewPanel, { go: go }) : panel === 'experience' ? _jsx(ExperiencePanel, {}) : panel === 'appearance' ? _jsx(AppearancePanel, {}) : panel === 'telegram' ? _jsx(TelegramPanel, {}) : panel === 'security' ? _jsx(SecurityPanel, {}) : panel === 'storage' ? _jsx(StoragePanel, {}) : panel === 'metadata' ? _jsx(MetadataPanel, {}) : panel === 'system' ? _jsx(SystemPanel, {}) : null, _jsxs("p", { className: "mt-2 text-right text-xs text-faint", children: [String(index + 1).padStart(2, '0'), " / ", String(PANELS.length).padStart(2, '0')] })] })] })] }));
}
