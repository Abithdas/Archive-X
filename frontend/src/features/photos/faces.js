export const LOAD_FAILED = 'Could not load face groups.';
export const EMPTY_TITLE = 'No face groups yet';
export const EMPTY_BODY = 'Archive X groups similar faces from indexed image pixels, not filenames. Run Image scan in Metadata & Assets Manager first; groups stay anonymous until you confirm an assignment.';
export const INTRO = 'People are built from image pixels. Find people downloads every performer picture, groups the faces in your photos and names clear matches automatically; the rest are suggestions (profile picture, source text, or your earlier confirmations). Suggestions need your confirmation.';
export const UNANALYZED = 'Face analysis has not been run yet. Run Image scan in Metadata & Assets Manager first so channel images are indexed.';
export const NEEDS_PERFORMER = 'Search and select a local performer before saving this assignment.';
export const JOB_END = ['COMPLETED', 'COMPLETED_WITH_ERRORS', 'FAILED', 'CANCELLED', 'INTERRUPTED'];
export const groupLabel = (g) => String(g.performer_name || g.label || `Face group ${g.id}`);
export const clusterIds = (g) => (g.cluster_ids?.length ? g.cluster_ids : [g.id]).map(Number).filter((n) => Number.isInteger(n) && n > 0);
export const isAssigned = (g) => Boolean(g.performer_id);
export const assignedText = (g) => (isAssigned(g) ? `Assigned · ${g.performer_name || 'Performer'}` : 'Needs performer review');
export const countsLine = (g) => `${Number(g.face_count || 0).toLocaleString()} face detections · ${Number(g.photo_count || 0).toLocaleString()} images`;
export const photosLabel = (g) => `${Number(g.photo_count || 0).toLocaleString()} photos`;
export function analysisStatus(groups, a, selectedCount, watching) {
    const total = Number(a.total_photos || 0), done = Number(a.analyzed_photos || 0), failed = Number(a.failed_photos || 0);
    const scope = selectedCount ? 'selected channels' : 'the full indexed library';
    let text = `${done.toLocaleString()} / ${total.toLocaleString()} photos analyzed in ${scope} · ${groups.length.toLocaleString()} visual groups${failed ? ` · ${failed.toLocaleString()} failed (can retry)` : ''}. Groups are based on image pixels, not filenames.`;
    let tone = failed ? 'error' : done ? 'success' : '';
    if (!groups.length) {
        if (total === 0) {
            text = 'No indexed photos yet. Run Image scan in Metadata & Assets Manager → Processing Stages → Advanced options first.';
            tone = '';
        }
        else if (done >= total && failed === 0) {
            text = 'Analysis finished, but no faces were detected in this selection.';
        }
    }
    const complete = total > 0 && done >= total && failed === 0;
    return { text, tone, analyzeLabel: watching ? 'Finding people…' : complete ? 'Find people again' : failed ? 'Retry failed images' : 'Find people', analyzeDisabled: watching || total === 0 };
}
export const analyzeCopy = (channels) => {
    const scope = channels ? `${channels} selected channel${channels === 1 ? '' : 's'}` : 'your whole indexed photo library';
    return { title: 'Find people?', ok: 'Find people', text: `Archive X will:\n\n1. Download the available picture of every performer through your metadata APIs.\n2. Detect and group the faces in ${scope}, on this PC.\n3. Name each group after the performer whose picture it clearly matches. Weaker matches stay as suggestions for you to confirm, and you can change any name.\n\nThe first run downloads the local face models (about 38 MB) and can take a while.` };
};
export const analyzeBody = (channels) => ({ channel_ids: [...channels], match_performers: true });
export const jobStatusText = (status, progress, current) => `Image-based face analysis ${String(status).toLowerCase().replaceAll('_', ' ')} · ${Number(progress || 0).toFixed(0)}%${String(current ?? '').trim() ? ` · ${String(current).trim()}` : ''}`;
export const jobDone = (status) => JOB_END.includes(String(status).toUpperCase());
export const jobEndText = (status) => (status === 'COMPLETED' || status === 'COMPLETED_WITH_ERRORS' ? 'Face analysis complete. Refreshing visual groups…' : `Face analysis ${status.toLowerCase().replaceAll('_', ' ')}. Check Operations for details; failed images can be retried.`);
export const mergeCopy = (n) => ({ title: 'Merge face clusters?', ok: 'Merge clusters', text: `Merge ${n} selected visual clusters? Their detected faces and performer assignment will be combined. This can be corrected later by separating individual face detections.` });
export const mergeBody = (ids) => ({ group_ids: ids.filter((n) => Number.isInteger(n) && n > 0) });
export const mergedText = (n, target) => `Merged ${n} visual clusters into group ${target}.`;
export const MERGE_NEEDS_TWO = 'Select at least two face clusters to merge.';
export const splitCopy = { title: 'Separate face detection?', ok: 'Separate face', text: 'Separate this representative face detection into a new anonymous cluster? The other photos in the current cluster will remain unchanged.' };
export const splitText = (newId) => `Face detection moved to new cluster ${newId}.`;
export const firstDetection = (g) => (g.detection_ids?.length ? Number(g.detection_ids[0]) : 0);
export const assignBody = (g, performerId) => ({ performer_id: performerId, group_ids: clusterIds(g) });
export const assignedStatus = (name) => `Assigned ${name} to this face group.`;
export const CLEARED = 'Performer assignment cleared.';
export const suggestionBody = (g, s, source, basis) => ({
    performer_id: s.id, group_ids: clusterIds(g), suggestion_source: source,
    suggestion_score: s.similarity_score == null || !Number.isFinite(Number(s.similarity_score)) ? null : Number(s.similarity_score),
    suggestion_reason: source === 'confirmed_assignment' ? 'Confirmed from a previously assigned visual group' : source === 'profile_image' ? 'Confirmed from a face match with the performer profile picture' : `Confirmed from source metadata: ${basis || 'text match'}`,
});
export const confirmedText = (name) => `Confirmed ${name} for this face group. Future suggestions can learn from this confirmation.`;
export const rejectBody = (g, s, source) => ({ group_ids: clusterIds(g), performer_id: s.id, source, similarity_score: s.similarity_score == null || !Number.isFinite(Number(s.similarity_score)) ? null : Number(s.similarity_score), reason: 'Rejected by user in Image Analysis' });
export const REJECTED = 'Suggestion rejected and remembered; it will stay hidden on future refreshes.';
export const scoreText = (s) => (s.similarity_score != null && Number.isFinite(Number(s.similarity_score)) ? `Embedding similarity ${Number(s.similarity_score).toFixed(2)} · not a probability` : '');
export const suggestionReason = (s, source) => String(s.reason || (source === 'metadata' ? 'Source text match' : source === 'profile_image' ? "Face resembles this performer's profile picture" : 'Similar to a previously confirmed group'));
export const suggestionsFor = (g) => (isAssigned(g) ? { confirmed: [], metadata: [], profile: [] } : { profile: (g.profile_image_suggestions ?? []).slice(0, 3), confirmed: (g.confirmed_assignment_suggestions ?? []).slice(0, 3), metadata: (g.metadata_suggestions ?? []).slice(0, 3) });
/** Selecting a group to view photos: an assigned group filters by performer, others by their cluster ids. */
export function viewFilter(g) {
    const performerId = g.performer_id ? Number(g.performer_id) : null;
    return { filter: { performerId, groupId: Number(g.id), groupIds: performerId ? [] : clusterIds(g), label: groupLabel(g) }, channels: (g.channel_ids ?? []).map(String) };
}
export const filterLabel = (f) => `Face group · ${f.label}`;
export const differentCopy = "Remove this photo's face detection(s) from the current group?\n\nArchive X will move those detections into new unassigned group(s). The photo and its Telegram message will not be deleted.";
export function differentBody(photoId, f, groups) {
    const active = groups.find((g) => Number(g.id) === Number(f.groupId));
    const ids = (active?.cluster_ids?.length ? active.cluster_ids : f.groupIds.length ? f.groupIds : f.groupId && f.groupId > 0 ? [f.groupId] : []).map(Number).filter((n) => Number.isInteger(n) && n > 0);
    return photoId && ids.length ? { photo_id: photoId, group_ids: ids } : null;
}
export const DIFFERENT_NEEDS_GROUP = 'Open a face-group gallery first, then use Different person on the incorrect photo.';
export const REVIEW_PAGE = 10;
export const REVIEW_TITLE = 'Review performer images';
export const REVIEW_NOTE = 'Card suggestions come from exact source-text matches or face groups you previously confirmed. Profile and provider images are shown for your own review; no new assignment is saved without your confirmation.';
export const REVIEW_CHOOSE = 'Choose a performer to display its saved profile and provider image options next to this face group.';
export const REVIEW_SEARCH_HINT = 'Search for a performer above. Candidate suggestions on the group card, when available, are based on caption, filename, or channel text only.';
export const REVIEW_LOADING = 'Loading saved/provider images…';
export const FOCUS_HINT = 'Select a provider image to inspect it larger.';
export const reviewSubtitle = (name) => (name ? `Compare the selected face group with ${name}'s saved profile and provider images.` : REVIEW_CHOOSE);
export const reviewMeta = (g) => `${Number(g.photo_count || 0).toLocaleString()} photos · ${Number(g.face_count || 0).toLocaleString()} face detections · anonymous cluster`;
export const saveLabel = (name) => `Map group to ${name || 'selected performer'}`;
export const candidatesUrl = (performerId, offset) => `/api/performers/${performerId}/image-candidates?limit=${REVIEW_PAGE}&offset=${offset}`;
export const providerName = (c) => String(c.provider || 'Provider').replace(/[^a-z0-9 _-]/gi, '');
export const candidateLabel = (c) => `${providerName(c)}${c.width && c.height ? ` · ${Number(c.width)}×${Number(c.height)}` : ''}`;
export const loadedText = (list) => {
    if (!list.length)
        return 'No provider reference images available for this performer.';
    const counts = new Map();
    for (const c of list)
        counts.set(String(c.provider || 'Provider'), (counts.get(String(c.provider || 'Provider')) ?? 0) + 1);
    const summary = [...counts].map(([k, v]) => `${k}: ${v}`).join(' · ');
    return `${list.length} provider reference image(s) loaded${summary ? ` · ${summary}` : ''}. Choose one to inspect, then assign the group manually.`;
};
export const NO_PROVIDER = 'No provider images were returned. The saved profile image is still available on the left.';
export const lookupFailed = (m) => `Provider image lookup unavailable: ${m}. You can still compare with the saved profile picture and assign manually.`;
/** The people shown across the top of Photos (like the People row in Google/Apple Photos): named people first, then the faces seen most often; one-off faces are left out. */
export function peopleRow(groups, max = 24) {
    const seen = (g) => Number(g.photo_count ?? 0);
    return groups
        .filter((g) => isAssigned(g) || seen(g) >= 3)
        .sort((a, b) => Number(isAssigned(b)) - Number(isAssigned(a)) || seen(b) - seen(a) || groupLabel(a).localeCompare(groupLabel(b)))
        .slice(0, max);
}
/** A suggestion good enough to ask "Is this X?" about on the person's tile: the best candidate from any source. */
export function bestGuess(g) {
    if (isAssigned(g))
        return null;
    const all = [...(g.metadata_suggestions ?? []), ...(g.profile_image_suggestions ?? []), ...(g.confirmed_assignment_suggestions ?? [])];
    return all.sort((a, b) => Number(b.similarity_score ?? 0.99) - Number(a.similarity_score ?? 0.99))[0] ?? null;
}
/** The People view in plain groups: named people, faces with a good guess ("Is this X?"), faces to name, and faces seen too rarely to bother with. */
export function peopleSections(groups) {
    const seen = (g) => Number(g.photo_count ?? 0);
    const byCount = (a, b) => seen(b) - seen(a) || groupLabel(a).localeCompare(groupLabel(b));
    const named = groups.filter(isAssigned).sort((a, b) => groupLabel(a).localeCompare(groupLabel(b)));
    const open = groups.filter((g) => !isAssigned(g));
    const common = open.filter((g) => seen(g) >= 3).sort(byCount);
    return {
        named,
        guesses: common.filter((g) => bestGuess(g)),
        toName: common.filter((g) => !bestGuess(g)),
        rare: open.filter((g) => seen(g) < 3).sort(byCount),
    };
}
export const findPeopleHint = (groups, analysis) => {
    const total = Number(analysis?.total_photos ?? 0), done = Number(analysis?.analyzed_photos ?? 0);
    if (!total)
        return 'No photos are indexed yet. Scan a channel for images first (Transfers → Metadata & Assets → Image scan).';
    if (!groups.length)
        return done ? 'No faces were found in your photos.' : 'Press Find people to look for faces in your photos.';
    return `${groups.length.toLocaleString()} ${groups.length === 1 ? 'person' : 'people'} found in ${done.toLocaleString()} analysed photos.`;
};
/** The steps "Find people" goes through, in order. */
export const FIND_STEPS = ['Download performer pictures', 'Read the faces in those pictures', 'Find faces in your photos', 'Match faces to performers'];
/** Which step the running job is on, from the operation text it reports (0 = not started yet). */
export function findStage(operation, status = '') {
    if (jobDone(status))
        return FIND_STEPS.length;
    const t = String(operation ?? '').toLowerCase();
    if (/matching faces/.test(t))
        return 3;
    if (/face analysis|photos ·|analyzing|detect/.test(t))
        return 2;
    if (/reading faces|preparing local face|models/.test(t))
        return 1;
    if (/downloading performer|performer picture/.test(t))
        return 0;
    return 0;
}
/** Photos that have no named person, and why. Each opens the gallery filtered to exactly those photos. */
export function scopeChips(scopes) {
    const n = (k) => Number(scopes?.[k] ?? 0);
    return [
        { scope: 'unnamed', count: n('unnamed'), text: 'with faces nobody has named', label: 'Photos with faces nobody has named', hint: 'Name them in the sections above.' },
        { scope: 'no_face', count: n('no_face'), text: 'where no face was found', label: 'Photos where no face was found', hint: 'Nothing to name in these.' },
        { scope: 'pending', count: n('pending'), text: 'not analysed yet', label: 'Photos not analysed yet', hint: 'Press Find people to analyse them.' },
    ].filter((c) => c.count > 0);
}
