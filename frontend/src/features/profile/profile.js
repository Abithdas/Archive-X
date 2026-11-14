export const NAME_MAX = 64;
export const PHOTO_MAX_BYTES = 8 * 1024 * 1024;
export const PHOTO_TYPES = ['image/png', 'image/jpeg', 'image/webp', 'image/gif'];
export const INTRO = 'Choose the name and photo Archive X shows in the header and Settings. This only changes Archive X; your real Telegram profile is not touched.';
export const READY = 'Change the name, the photo, or both.';
export const SAVED = 'Profile updated.';
export const SAVE_FAILED = 'Could not save the profile.';
export const initials = (name) => (String(name || '').trim().split(/\s+/).filter(Boolean).slice(0, 2).map((p) => Array.from(p)[0] ?? '').join('') || 'A').toUpperCase();
/** Header/Settings photo: the saved custom one, else the Telegram account's own. */
export const photoUrl = (p) => `${p?.has_custom_image ? '/api/account/profile/image' : '/api/telegram/me/photo'}?v=${encodeURIComponent(p?.image_version || '0')}`;
export const displayName = (p) => (p?.name?.trim() || p?.username?.trim() || '');
/** Why a chosen file cannot be used, or '' when it can. */
export function photoProblem(file) {
    if (file.size > PHOTO_MAX_BYTES)
        return 'Photo must be 8 MiB or smaller.';
    if (file.type && !PHOTO_TYPES.includes(file.type))
        return 'Choose a PNG, JPEG, WebP or GIF image.';
    return '';
}
export const nameProblem = (name) => (name.trim().length > NAME_MAX ? `Display name must be ${NAME_MAX} characters or fewer.` : '');
/** Nothing to send when the name is unchanged and no photo was picked or reset. */
export const unchanged = (p, d) => d.name.trim() === (p?.custom_name || '') && !d.imageData && !d.reset;
export function saveBody(d) {
    const body = { name: d.name.trim() };
    if (d.imageData)
        body.image_data = d.imageData;
    if (d.reset)
        body.reset_image = true;
    return body;
}
export const chosenText = (fileName) => `Selected ${fileName}. Press Save to apply it.`;
export const RESET_TEXT = 'Saving will use your Telegram profile photo again.';
