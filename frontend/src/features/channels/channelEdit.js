export const TITLE_MAX = 255;
export const PHOTO_MAX_BYTES = 8 * 1024 * 1024;
export const PHOTO_TYPES = ['image/png', 'image/jpeg', 'image/webp', 'image/gif'];
export const INTRO = 'Edit the channel name and profile photo. Changes are saved locally first; sending them to Telegram requires a separate confirmation.';
export const READY = 'Change the name, choose a profile photo, or do both. Save locally first, then explicitly choose whether to sync changes to Telegram.';
export const NEED_NAME = 'Channel name is required.';
export const RESET_TEXT = 'Saving will remove the local photo override and show the current Telegram profile photo.';
export const SAVING = 'Saving channel changes locally…';
export const SAVE_FAILED = 'Could not update channel.';
export const NOT_FOUND = 'Channel not found.';
export const RESET_DONE = 'Channel photo reset to the current Telegram profile photo locally.';
export const LOCAL_ONLY = 'Changes saved locally only. Telegram was not changed.';
const real = (t) => { const v = String(t ?? '').trim(); return !!v && !/^unknown channel$/i.test(v) && !/^channel\s+-?\d+$/i.test(v) && !/^-?\d+$/.test(v) ? v : ''; };
/** The name to put in the box: a real title, else empty (the classic editor never pre-fills "Unknown channel"). */
export const startTitle = (c) => real(c.title);
export const initialsOf = (title) => title.trim().split(/\s+/).filter(Boolean).slice(0, 2).map((p) => Array.from(p)[0] ?? '').join('').toUpperCase() || 'TG';
/** The photo shown now: the local upload if there is one, else Telegram's own. */
export function previewUrl(c) {
    const id = encodeURIComponent(String(c.telegram_id)), v = encodeURIComponent(String(c.updated_at || '0'));
    return String(c.image || '').startsWith('manual-upload:') ? `/api/channels/${id}/image?v=${v}&w=240` : `/api/channels/${id}/telegram-photo?v=${v}`;
}
export const resetPreviewUrl = (id, now = Date.now()) => `/api/channels/${encodeURIComponent(String(id))}/telegram-photo?refresh=${now}`;
export function photoProblem(f) {
    if (f.size > PHOTO_MAX_BYTES)
        return 'Channel profile photo must be 8 MiB or smaller.';
    if (f.type && !PHOTO_TYPES.includes(f.type))
        return 'Choose a PNG, JPEG, WebP, or GIF image.';
    return '';
}
export const chosenText = (name) => `Selected ${name}. It will be saved locally first; you can then choose whether to push the photo to Telegram.`;
export const titleChanged = (d) => d.title.trim() !== d.original;
export const nothingToSave = (d) => !titleChanged(d) && !d.imageData && !d.reset;
export const editBody = (d) => { const b = { title: d.title.trim() }; if (d.imageData)
    b.image_data = d.imageData; if (d.reset)
    b.reset_image_to_api = true; return b; };
/** Telegram is only asked about when a new name or photo was saved (not for a plain photo reset). */
export const shouldAskSync = (d) => titleChanged(d) || !!d.imageData;
export const syncChanges = (d) => [titleChanged(d) ? 'channel name' : '', d.imageData ? 'profile photo' : ''].filter(Boolean).join(' and ');
export const syncCopy = (changes) => ({ title: 'Sync changes to Telegram?', ok: 'Push to Telegram', cancel: 'Save locally only', text: `Push the changed ${changes} to Telegram now? This modifies the real Telegram channel and requires owner/admin change-info permission.` });
export const syncBody = (d) => ({ sync_title: titleChanged(d), sync_photo: !!d.imageData });
export const syncDone = (synced) => `Saved locally and synchronized to Telegram: ${(synced ?? []).join(', ')}.`;
export const syncFailed = (why) => `Local changes saved, but Telegram sync failed: ${why || 'unknown error'}`;
