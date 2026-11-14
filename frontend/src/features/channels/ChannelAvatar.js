import { jsx as _jsx, jsxs as _jsxs } from "react/jsx-runtime";
import { useState } from 'react';
import { cn } from '@/design/cn';
import { BlurImg } from '@/design/BlurImg';
const initialsOf = (title) => title.split(/\s+/).filter(Boolean).slice(0, 2).map((p) => Array.from(p)[0] ?? '').join('').toUpperCase() || 'TG';
/** The channel's own profile picture (its Telegram photo, or the one uploaded for it); the initials show while it loads or if the channel has none. */
export function channelPhotoUrl(id, image, width = 120) {
    const key = encodeURIComponent(String(id));
    const v = encodeURIComponent(String(image || '0'));
    return String(image || '').startsWith('manual-upload:') ? `/api/channels/${key}/image?v=${v}&w=${width * 2}` : `/api/channels/${key}/telegram-photo?v=${v}`;
}
export function ChannelAvatar({ id, title, image, className }) {
    const [ok, setOk] = useState(true);
    return (_jsxs("span", { className: cn('relative grid size-full place-items-center overflow-hidden rounded-full', className), children: [_jsx("span", { "aria-hidden": "true", children: initialsOf(title) }), ok ? _jsx(BlurImg, { src: channelPhotoUrl(id, image), alt: "", loading: "lazy", decoding: "async", onError: () => setOk(false), className: "absolute inset-0 size-full rounded-full object-cover" }) : null] }));
}
