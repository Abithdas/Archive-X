const STAMP = /(?:(\d+):)?(\d{1,2}):(\d{2})[.,](\d{1,3})/;
export function stampSeconds(s) {
    const m = STAMP.exec(s.trim());
    if (!m)
        return NaN;
    return Number(m[1] ?? 0) * 3600 + Number(m[2]) * 60 + Number(m[3]) + Number(m[4].padEnd(3, '0')) / 1000;
}
const stripTags = (t) => t.replace(/<[^>]*>/g, '').replace(/&amp;/g, '&').replace(/&lt;/g, '<').replace(/&gt;/g, '>').replace(/&nbsp;/g, ' ');
/** Cues of a WebVTT (or SRT) file. */
export function parseVtt(text) {
    const cues = [];
    for (const block of text.replace(/\r/g, '').split(/\n{2,}/)) {
        const lines = block.split('\n');
        const at = lines.findIndex((l) => l.includes('-->'));
        if (at < 0)
            continue;
        const [a, b] = lines[at].split('-->');
        const start = stampSeconds(a ?? '');
        const end = stampSeconds((b ?? '').trim().split(/\s+/)[0] ?? '');
        const body = stripTags(lines.slice(at + 1).join('\n')).trim();
        if (Number.isFinite(start) && Number.isFinite(end) && body)
            cues.push({ start, end, text: body });
    }
    return cues.sort((x, y) => x.start - y.start);
}
/** The cue text to show at time `t` (overlapping cues are stacked). */
export function activeText(cues, t) {
    const out = [];
    for (const c of cues) {
        if (c.start > t)
            break;
        if (t < c.end)
            out.push(c.text);
    }
    return out.join('\n');
}
/** Merge newly fetched cues into a list without duplicating a cue that is already there. */
export function mergeCues(have, add) {
    const seen = new Set(have.map((c) => `${c.start.toFixed(2)}|${c.text}`));
    const out = have.slice();
    for (const c of add) {
        const k = `${c.start.toFixed(2)}|${c.text}`;
        if (!seen.has(k)) {
            seen.add(k);
            out.push(c);
        }
    }
    return out.sort((x, y) => x.start - y.start);
}
export const CAPTION_WINDOW = 30;
export const windowStart = (t) => Math.max(0, Math.floor(t / CAPTION_WINDOW) * CAPTION_WINDOW);
/** Start choices from the saved preference: the audio in the viewer's language if the file has it, and subtitles only when they asked for them. */
export function initialChoice(info, prefs) {
    const lang = (a) => a.language.toLowerCase().slice(0, 2);
    const want = prefs.audioLanguage?.toLowerCase().slice(0, 2);
    const pick = info.audio.find((a) => want && lang(a) === want);
    const audio = pick && !pick.default && pick.ordinal !== 0 ? pick.index : null; // null: keep the video's own audio
    let sub = { kind: 'off' };
    if (prefs.sub === 'live' && info.captions.available)
        sub = { kind: 'live', original: !!prefs.liveOriginal };
    else if (prefs.sub === 'track') {
        const s = info.subtitles.find((x) => x.text && prefs.subLanguage && lang(x) === prefs.subLanguage.toLowerCase().slice(0, 2));
        if (s)
            sub = { kind: 'track', index: s.index };
    }
    return { audio, sub };
}
const KEY = 'archive-x:track-prefs';
export function loadPrefs() { try {
    return JSON.parse(localStorage.getItem(KEY) ?? '{}');
}
catch {
    return {};
} }
export function savePrefs(p) { try {
    localStorage.setItem(KEY, JSON.stringify(p));
}
catch { /* private mode */ } }
