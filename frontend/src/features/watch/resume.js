/**
 * Resume rules, ported from the classic player. The localStorage keys are the same as the classic UI's, so both UIs
 * share one saved position. Only the last-played video keeps a position (the classic behaviour).
 */
import { formatDuration } from '@/features/library/format';
export const LAST_KEY = 'ta_last_played_video';
export const SAVE_EVERY_MS = 5000;
export const vkey = (cid, mid) => `${cid}:${mid}`;
export const resumeStorageKey = (cid, mid) => `ta_resume:${vkey(cid, mid)}`;
/** Keep a position locally only while the video is the last one played and clearly in the middle (not the first 20 s, not the last 25 s). */
export const shouldSaveLocal = (isLast, position, duration) => isLast && position > 20 && duration > 0 && position < duration - 25;
export const dueForSave = (now, last, every = SAVE_EVERY_MS) => now - last >= every;
/** The saved position to offer, or null. Needs the setting on, a saved video that was last played, and at least 30 s but not the last 20 s. */
export function promptPosition(i) {
    if (!i.enabled || i.fromQueue || !i.isLast)
        return null;
    const ok = Number.isFinite(i.stored) && i.stored >= 30 && (!i.duration || i.stored < i.duration - 20);
    return ok ? i.stored : null;
}
/** Watch time only counts for ordinary playback: a jump of more than 15 s (a seek) adds nothing. */
export const progressBody = (cid, mid, position, duration, previous) => {
    const delta = Math.max(0, position - previous);
    return { channel_id: cid, message_id: mid, position, duration, watch_delta: delta <= 15 ? delta : 0 };
};
export const finishedBody = (cid, mid, duration) => ({ channel_id: cid, message_id: mid, position: duration, duration, watch_delta: 0 });
export const labelFor = (seconds) => formatDuration(seconds) || `${Math.floor(seconds)}s`;
export const progressPct = (stored, duration) => (duration > 0 ? Math.max(0, Math.min(100, (stored / duration) * 100)) : 0);
export const timeLine = (label, duration) => `${label}${duration > 0 ? ` / ${formatDuration(duration) || ''}` : ''}`;
/** What the player does after the choice: where to start, and whether to play. Cancelling leaves the video paused at the start. */
export const outcome = (choice, stored) => ({ startAt: choice === 'resume' ? Math.max(0, stored) : 0, autoPlay: choice !== 'cancel' });
// ── storage (every access guarded: private mode and blocked storage must never break playback) ──
const get = (k) => { try {
    return localStorage.getItem(k);
}
catch {
    return null;
} };
const set = (k, v) => { try {
    localStorage.setItem(k, v);
}
catch { /* ignore */ } };
const del = (k) => { try {
    localStorage.removeItem(k);
}
catch { /* ignore */ } };
export const isLastPlayed = (cid, mid) => get(LAST_KEY) === vkey(cid, mid);
export const markPlayed = (cid, mid) => set(LAST_KEY, vkey(cid, mid));
export const readStored = (cid, mid) => Number(get(resumeStorageKey(cid, mid)) || 0);
export const saveStored = (cid, mid, position) => set(resumeStorageKey(cid, mid), String(Math.floor(position)));
export const clearStored = (cid, mid) => { del(resumeStorageKey(cid, mid)); if (isLastPlayed(cid, mid))
    del(LAST_KEY); };
