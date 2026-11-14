/** Scene-marker section rules, ported from the classic watch page. Markers are read-only here, as in the classic page. */
import { formatDuration } from '@/features/library/format';
const clock = (s) => formatDuration(s) || '0:00';
/** Chips sorted by time. A missing title reads "Marker"; the note only appears in the tooltip. */
export function markerChips(rows) {
    return (rows ?? [])
        .map((m, i) => {
        const position = Math.max(0, Number(m.position) || 0);
        const title = String(m.title ?? '').trim() || 'Marker';
        const note = String(m.note ?? '').trim();
        const tag = String(m.tag ?? '').trim();
        const time = clock(position);
        return { key: `${m.id ?? i}:${position}`, position, time, title, tag, note, tooltip: `Go to ${time}${note ? ` · ${note}` : ''}`, label: `Go to ${time}: ${title}` };
    })
        .sort((a, b) => a.position - b.position);
}
export const headingFor = (count) => (count ? `Scene markers (${count})` : 'Scene markers');
/** What to say when there are no markers. */
export function emptyMessage(status, error) {
    if (error)
        return error;
    if (status === 'NO_PROVIDER_TOKEN')
        return 'No markers yet. Configure the ThePornDB provider token to check provider markers.';
    if (status === 'NO_TPDB_SCENE')
        return 'No matching provider scene was found.';
    if (status === 'PROVIDER_ERROR')
        return 'Provider markers could not be loaded. Try refreshing them.';
    return 'No markers yet.';
}
export const AUTO_SYNC_FAILED = 'Saved markers are empty and provider sync failed. Use Refresh provider markers to retry.';
export const REFRESH_FAILED = 'Provider marker refresh failed.';
export const NEEDS_INDEXING = 'Markers are available after catalogue indexing.';
export const MARKERS_UNAVAILABLE = 'Markers unavailable. Check the Operations log or retry.';
export const refreshLabel = (busy) => (busy ? 'Checking provider…' : 'Refresh provider markers');
/** The sync endpoint reports a missing media row as ok:false; anything else carries the markers. */
export const rowsFrom = (r) => (Array.isArray(r?.markers) ? r.markers : []);
