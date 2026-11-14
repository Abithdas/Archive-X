/** "Mapping / report" rules: correct a video's title and studio. Ported from the classic metadata editor. */
export const NO_STUDIOS_HINT = 'Type a studio name to search.';
/** Search hits (the single-video dialog waits for text; bulk pickers list at once), plus a "create new" row unless the typed name already matches one exactly (case-insensitive). */
export function studioChoices(hits, query, opts = {}) {
    const q = query.trim();
    if (!q && !opts.listWhenEmpty)
        return [];
    const out = hits.map((h) => ({ name: h.name, id: h.id, count: h.video_count ?? 0, create: false, image: h.image ?? null }));
    if (!q)
        return out; // the bulk pickers list studios before anything is typed; "create" only makes sense once there is a name
    if (!hits.some((h) => String(h.name ?? '').toLowerCase() === q.toLowerCase()))
        out.push({ name: q, id: null, count: 0, create: true });
    return out;
}
export const choiceLabel = (c) => (c.create ? `Create new studio “${c.name}”` : c.name);
export const choiceNote = (c) => (c.create ? '' : `${c.count} video${c.count === 1 ? '' : 's'} · link to this studio`);
export const pickedFrom = (c) => ({ name: c.name.trim(), id: c.id });
/** Only what changed is sent. An empty title is ignored; clearing the studio is an explicit change. Both are locked so provider syncs do not undo them. */
export function mappingTasks(input) {
    const tasks = [];
    const title = input.title.trim();
    if (title && title !== input.originalTitle.trim())
        tasks.push({ field: 'title', target_name: title, action: 'move', lock: true });
    const picked = input.studio ? input.studio.name.trim() : '';
    if (picked !== input.originalStudio.trim())
        tasks.push({ field: 'site', site_id: input.studio?.id ?? null, target_name: picked, action: 'move', lock: true });
    return tasks;
}
export const mappingBody = (item, task) => ({ videos: [{ channel_id: item._cid, message_id: item.id }], ...task });
export const NO_CHANGES = 'No changes to save.';
export const SAVED = 'Video title/studio mapping updated.';
/** If a later request fails, the earlier ones are already stored: say so. */
export const failureMessage = (detail, appliedFields) => `${detail || 'Could not save mapping.'}${appliedFields.length ? ` (Already saved: ${appliedFields.join(', ')}.)` : ''}`;
