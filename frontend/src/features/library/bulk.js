export const NONE_PICKED = 'Select at least one video.';
export const PICK_CHANNEL_FIRST = 'Pick a channel first, then choose the videos to edit.';
export const ENTER_HINT = 'Pick videos, then choose what to change.';
export const countLabel = (n) => `${n} selected`;
export const videos = (n) => `${n} video${n === 1 ? '' : 's'}`;
/** Applies a value (or Unknown) to every picked video. Always locked so provider syncs do not undo it. */
export const bulkBody = (picked, field, action, target = '') => ({ videos: picked, field, target_name: action === 'unknown' ? '' : target.trim(), action, lock: true });
export const updatedMessage = (changed, fallback) => `${Number(changed ?? fallback) || 0} video(s) updated and locked.`;
export const titleProblem = (t) => (t.trim() ? '' : 'Enter the corrected video title.');
const FIELD_NAME = { performer: 'performer', site: 'studio', title: 'title', performer_site: 'performer and studio', all: 'performer, studio and title' };
/** Clearing is not undoable from this screen, so (unlike the classic page) it asks first. */
export const unknownConfirm = (field, n) => `Set the ${FIELD_NAME[field]} of ${videos(n)} to Unknown? The field is also locked so metadata syncs will not refill it. Use Unlock to allow syncs again.`;
export const unknownOk = (field) => (field === 'all' ? 'Move all to Unknown' : `Clear ${FIELD_NAME[field]}`);
export const recheckMessage = (r, fallback) => (r.ok ? `Metadata recheck queued for ${r.count || fallback} video(s).` : r.detail || 'Recheck failed');
export const unlockBody = (picked) => ({ videos: picked, field: 'all' });
export const UNLOCKED = 'Mapping locks removed.';
