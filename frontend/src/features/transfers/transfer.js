export const ORIENTATIONS = [['BOTH', 'Preserve / both'], ['HORIZONTAL', 'Horizontal only'], ['VERTICAL', 'Vertical only']];
export const RESOLUTIONS = [['ANY', 'Any resolution'], ['480P', 'SD · 480p (720 × 480)'], ['720P', 'HD · 720p (1280 × 720)'], ['1080P', 'Full HD · 1080p (1920 × 1080)'], ['1440P', 'Quad HD · 1440p (2560 × 1440)'], ['2160P', '4K UHD · 2160p (3840 × 2160)'], ['4320P', '8K · 4320p (7680 × 4320)']];
export const DEFAULT_LOCAL = { folder: '', destination: '', images: false, subfolders: false, deleteSource: true, resolution: 'ANY', orientation: 'BOTH', oversized: 'optimize' };
export const DEFAULT_TELEGRAM = { channels: [], destination: '', sourceMedia: 'VIDEO', destinationMedia: 'VIDEO', orientation: 'BOTH', resolution: 'ANY', dupSource: true, dupDestination: true, dupDb: true, cleanup: false, trash: '' };
/** First problem that blocks the run, or '' when it can start. Same wording as the classic page. */
export function validateLocal(o) {
    if (!o.folder.trim())
        return 'Enter a source folder.';
    if (!o.destination)
        return 'Choose a destination channel.';
    return '';
}
export function validateTelegram(o) {
    if (!o.channels.length)
        return 'Select at least one source channel.';
    if (!o.destination)
        return 'Choose a destination channel.';
    if (o.channels.includes(String(o.destination)))
        return 'Destination cannot be one of the selected source channels.';
    if (o.cleanup && !o.trash)
        return 'Choose a trash channel when cleanup is enabled.';
    return '';
}
export function localPayload(o, includeSubfolders = o.subfolders) {
    const base = { source: 'local', media_type: o.images ? 'PHOTOS' : 'VIDEO', folder: o.folder.trim(), destination_channel: o.destination, include_subfolders: includeSubfolders, delete_local_after_delivery: o.deleteSource };
    return o.images ? base : { ...base, resolution_filter: o.resolution || 'ANY', orientation: o.orientation || 'BOTH', oversized_file_policy: o.oversized || 'optimize' };
}
export const telegramPayload = (o) => ({
    source: 'telegram', source_channels: o.channels, destination_channel: o.destination, source_mode: o.sourceMedia, destination_mode: o.destinationMedia,
    orientation: o.orientation || 'BOTH', resolution_filter: o.resolution || 'ANY',
    duplicate_check: { source: o.dupSource, destination: o.dupDestination, database: o.dupDb }, cleanup: { enabled: o.cleanup, trash_channel: o.trash },
});
/** Decide what to do with a folder scan before queueing an image upload. `ask` needs the user's choice (see resolveAsk). */
export function planPhotoScan(scan, folder, includeSubfolders) {
    if (!scan || typeof scan.direct_count !== 'number')
        return { kind: 'error', message: `Could not scan the selected photo folder: ${scan?.detail || 'folder scan failed. No upload was queued.'}` };
    const direct = Number(scan.direct_count || 0);
    const nested = Number(scan.nested_count || 0);
    if (!includeSubfolders && nested > 0) {
        const message = direct === 0
            ? `No supported photos were found directly in the selected folder, but ${nested} supported photo(s) were found in subfolders. Include subfolders and upload them?`
            : `Folder scan found ${direct} supported photo(s) directly in the selected folder and ${nested} more in subfolders. Include subfolders, or upload only the ${direct} directly listed photo(s)?`;
        return { kind: 'ask', directCount: direct, nestedCount: nested, message };
    }
    return finishPhotoPlan(scan, folder, includeSubfolders);
}
/** After the user answered the subfolder question (or none was needed). */
export function resolveAsk(scan, folder, include) {
    const direct = Number(scan.direct_count || 0);
    const nested = Number(scan.nested_count || 0);
    if (!include && direct === 0)
        return { kind: 'error', message: `No upload queued. ${nested} supported photo(s) are in subfolders; enable Include subfolders to send them.` };
    return finishPhotoPlan(scan, folder, include);
}
function finishPhotoPlan(scan, folder, include) {
    const count = include ? Number(scan.recursive_count || 0) : Number(scan.direct_count || 0);
    if (count === 0) {
        const seen = Object.entries(scan.recursive_extensions ?? {}).sort((a, b) => b[1] - a[1]).slice(0, 8).map(([ext, n]) => `${ext}: ${n}`).join(', ') || 'none';
        return { kind: 'error', message: `No supported photos detected in ${folder}. The scan inspected ${Number(scan.recursive_file_count || 0)} file(s). Extensions seen: ${seen}. No upload was queued.` };
    }
    return { kind: 'go', includeSubfolders: include, count };
}
export function startMessage(source, r, images, count) {
    if (!r.id)
        return `Could not start: ${r.detail || 'unknown error'}`;
    if (source === 'telegram')
        return 'Telegram media inspection/preparation queued.';
    return images ? `Image document upload queued (${count} supported photo(s) detected). Already-delivered Telegram documents may be verified and reused instead of uploaded again; verified images are indexed in Photos.` : 'Local preparation/transfer queued.';
}
