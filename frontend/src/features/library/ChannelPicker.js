import { jsx as _jsx, Fragment as _Fragment, jsxs as _jsxs } from "react/jsx-runtime";
import { cn } from '@/design/cn';
import { channelMatches, channelName, isDisplayName, parseCaption } from './send';
function CaptionText({ nodes }) {
    return _jsx(_Fragment, { children: nodes.map((n, i) => {
            if (typeof n === 'string')
                return n;
            const kids = _jsx(CaptionText, { nodes: n.children });
            return n.tag === 'b' ? _jsx("b", { children: kids }, i) : n.tag === 'i' ? _jsx("i", { children: kids }, i) : n.tag === 'u' ? _jsx("u", { children: kids }, i) : n.tag === 's' ? _jsx("s", { children: kids }, i) : n.tag === 'code' ? _jsx("code", { className: "rounded bg-surface-2 px-1", children: kids }, i) : _jsx("pre", { className: "whitespace-pre-wrap", children: kids }, i);
        }) });
}
export const SINGLE_CAPTION_LABEL = 'Caption that will be sent (edit the template in Operations → Send to Telegram):';
export const BULK_CAPTION_LABEL = 'Caption preview for the first selected video. Each video gets its own caption from the template in Operations → Send to Telegram.';
/** Destination channel list, caption preview and "always send here" checkbox, shared by single and bulk send. */
export function ChannelPicker({ channels, q, onQ, target, onTarget, caption, captionLabel, remember, onRemember }) {
    const shown = channelMatches(channels, q);
    return (_jsxs(_Fragment, { children: [_jsx("input", { value: q, onChange: (e) => onQ(e.target.value), placeholder: "Search channel\u2026", "aria-label": "Search channel", className: "h-11 w-full min-w-0 rounded-xl border border-line-2 bg-bg px-3.5 text-[15px] outline-none placeholder:text-faint focus:border-accent/60" }), _jsx("div", { role: "listbox", "aria-label": "Destination channel", className: "max-h-[30vh] overflow-y-auto rounded-xl border border-line", children: shown.length ? shown.map((c) => {
                    const on = String(c.telegram_id) === target;
                    return (_jsxs("button", { type: "button", role: "option", "aria-selected": on, onClick: () => onTarget(String(c.telegram_id)), className: cn('flex w-full items-center gap-3 px-3 py-2.5 text-left text-sm hover:bg-fg/5', on && 'bg-accent/15 font-bold'), children: [_jsx("span", { className: "grid size-7 shrink-0 place-items-center rounded-full bg-surface-2 text-xs text-muted", "aria-hidden": "true", children: "#" }), _jsx("span", { className: "min-w-0 truncate", children: isDisplayName(c.title) ? c.title : channelName(channels, c.telegram_id) })] }, c.telegram_id));
                }) : _jsx("p", { className: "p-3 text-sm text-muted", children: "No channels." }) }), _jsxs("div", { children: [_jsx("p", { className: "mb-1 text-[11px] text-muted", children: captionLabel }), _jsx("p", { className: "max-h-32 min-h-9 overflow-y-auto whitespace-pre-line rounded-xl bg-bg p-3 text-sm [overflow-wrap:anywhere]", "aria-label": "Caption preview", children: caption === null ? 'Loading…' : caption ? _jsx(CaptionText, { nodes: parseCaption(caption) }) : _jsx("span", { className: "text-muted", children: "(no caption)" }) })] }), _jsxs("label", { className: "flex items-center gap-2 text-xs text-muted", children: [_jsx("input", { type: "checkbox", checked: remember, onChange: (e) => onRemember(e.target.checked), className: "size-4" }), "Always send to this channel (no more asking)"] })] }));
}
