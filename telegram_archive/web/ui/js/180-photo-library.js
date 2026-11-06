/* ============================================================================
 * FILE: js/180-photo-library.js
 * AREA: Photos
 * LOAD: Classic script. Order is set by index.html, the HTML partials and
 *       _LAYERS in web/html.py. Later scripts can wrap earlier globals,
 *       so check docs/CODE_MAP.md before moving code between files.
 * SECTIONS (search for "SECTION:"):
 *   - State and status helpers
 *   - Reference performer search
 *   - Channels and empty states
 *   - Card markup and virtual masonry grid
 *   - Loading and rendering the grid
 *   - Viewer: HUD and collection
 *   - Viewer: zoom and pan
 *   - Viewer: decoded image cache and queue
 *   - Viewer: preview to original handoff
 *   - Viewer: filmstrip and navigation
 * ============================================================================ */
/* Archive X Photos library: Telegram channel index + local document upload. */
(function () {
  'use strict';

  // ------------------------------------------------------------------
  // SECTION: State and status helpers
  // Favorites, shared state, element lookup and status text.
  // ------------------------------------------------------------------
  const FAVORITES_STORAGE_KEY = 'archive-x-photo-favorites-v1';
  function readFavoriteIds() {
    try { const parsed = JSON.parse(window.localStorage.getItem(FAVORITES_STORAGE_KEY) || '[]'); return new Set(Array.isArray(parsed) ? parsed.map(String) : []); }
    catch (_) { return new Set(); }
  }
  const state = { channels: [], savedChannels: [], selected: new Set(), loadedChannels: false, loading: false,
    page: 0, per: 48, total: 0, items: [], search: '', sort: 'date_desc', photoLoadObserver: null, autoLoadScheduled: false,
    // The gallery keeps lightweight height-reserving slots for all records and
    // mounts full card/image subtrees only near the viewport.
    photoVirtualLayout: [], photoVirtualColumnEntries: [], photoVirtualLayoutDirty: true, photoVirtualGridWidth: 0, photoVirtualItemCount: 0, photoVirtualImagesRaf: 0, photoVirtualActiveImages: new Set(),
    photoVirtualColumns: 0, photoVirtualCardWidth: 0, photoVirtualGridHeight: 0, photoVirtualRaf: 0,
    photoVirtualResizeObserver: null, photoVirtualObserver: null, photoVirtualSlotNodes: [], photoVirtualPeakMounted: 0, photoVirtualRenderPasses: 0,
    photoVirtualLastScrollAt: 0, photoVirtualRatioTimer: 0, photoVirtualPendingRatios: new Map(), photoVirtualAnchorRaf: 0, photoVirtualPendingAnchor: null,
    photoVirtualLayoutPasses: 0, photoVirtualMaxMounted: 48, photoVirtualBufferMin: 320, photoVirtualBufferMax: 480,
    viewerIndex: -1, viewerItems: [], viewerItemsLoadSeq: 0, viewerZoom: 1, viewerFitScale: 1, viewerRotation: 0, viewerX: 0, viewerY: 0,
    viewerTargetZoom: 1, viewerTargetX: 0, viewerTargetY: 0, viewerAnimRaf: 0,
    // Decoded-image cache and prioritized loader state. Keep prepared originals under a
    // strict memory budget and load candidates through a bounded priority queue.
    viewerImageCache: new Map(), viewerImageCacheBytes: 0, viewerImageCacheBudgetBytes: 192 * 1024 * 1024, viewerImageCacheMaxEntries: 16,
    viewerImageQueue: [], viewerImageInflight: new Map(), viewerImageQueueRunning: 0, viewerImageQueueLimit: 2, viewerImageQueueOrder: 0,
    viewerImageLoadSeq: 0, viewerOriginalCommitSeq: 0, viewerOriginalVisibleSeq: 0, viewerCurrentTargetUrl: '', viewerCurrentOriginalUrl: '',
    viewerPreviewSeq: 0, viewerPreviewImage: null, viewerStripStart: null, viewerStripWindowSize: 24, viewerLastSelectionAt: 0,
    viewerImageMetrics: { selections:0, cacheHits:0, cacheMisses:0, sharedRequests:0, queuedLoads:0, decodedLoads:0, decodeFailures:0, staleResults:0,
      cancelledQueued:0, cancelledRunning:0, cacheEvictions:0, oversizedNotCached:0, cacheReuses:0, queueWaitMsTotal:0, decodeMsTotal:0,
      originalDisplayMsTotal:0, originalDisplayMsLast:0, previewDisplays:0 },
    viewerPointers: new Map(), viewerGesture: null, viewerSourceImage: null, viewerSetFitOnLoad: false, viewerSlideInterval: null,
    viewerAudioContext: null, viewerSoundEnabled: false, lastFilmstripWheelAt: 0, filmstripWheelAccumulator: 0, filmstripWheelPendingDelta: 0, filmstripWheelRaf: 0, filmstripWheelResetTimer: null,
    filmstripDragging: false, filmstripDragMoved: false, filmstripDragStartX: 0, filmstripDragScrollLeft: 0, suppressFilmstripClick: false,
    favorites: readFavoriteIds(), favoritesOnly: false, activeTab: 'library',
    faceGroups: [], faceScanWatching: false, faceScanId: '', faceGroupId: null, faceGroupIds: [], facePerformerId: null, faceGroupNames: new Map(), faceSelectedClusters: new Set(), faceCandidates: new Map(), faceSearchTimers: new Map(),
    faceReview: { groupId: 0, performerId: 0, offset: 0, hasMore: false, candidates: [], selectedUrl: '', requestId: 0 } };
  const el = (id) => document.getElementById(id);
  const escape = (value) => String(value ?? '').replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;').replace(/"/g, '&quot;').replace(/'/g, '&#39;');
  const displayName = (channel) => typeof window.channelDisplayName === 'function'
    ? window.channelDisplayName(String(channel.telegram_id || ''), channel.title || '')
    : (String(channel.title || channel.telegram_id || 'Unknown channel'));
  function photoResultsSummaryText() {
    const total = Number(state.total) || 0;
    const imageLabel = `${total.toLocaleString()} image${total === 1 ? '' : 's'}`;
    const channelCount = state.selected.size;
    return channelCount
      ? `${imageLabel} · ${channelCount.toLocaleString()} channel${channelCount === 1 ? '' : 's'}`
      : imageLabel;
  }
  function syncPhotoResultsSummary() {
    const results = el('ax-photos-results-count');
    if (results) results.textContent = photoResultsSummaryText();
  }
  function status(message, tone) {
    const node = el('ax-photos-library-status'); if (!node) return;
    node.textContent = String(message || '');
    node.classList.toggle('is-error', tone === 'error'); node.classList.toggle('is-success', tone === 'success');
  }
  function uploadStatus(message, tone) {
    const node = el('ax-photos-upload-status'); if (!node) return;
    node.textContent = String(message || '');
    node.classList.toggle('is-error', tone === 'error'); node.classList.toggle('is-success', tone === 'success');
  }
  function isPhotosVisible() { return !!el('vphotos')?.classList.contains('on'); }

  window.axPhotosSetTab = function axPhotosSetTab(tab) {
    const active = tab === 'performers' ? 'performers' : 'library';
    state.activeTab = active;
    const mapping = [
      ['library', 'ax-photos-tab-library', 'ax-photos-library-panel'],
      ['performers', 'ax-photos-tab-performers', 'ax-photos-performers-panel']
    ];
    mapping.forEach(([key, buttonId, panelId]) => {
      const button = el(buttonId), panel = el(panelId), selected = key === active;
      if (button) {
        button.classList.toggle('is-active', selected);
        button.setAttribute('aria-selected', selected ? 'true' : 'false');
        button.tabIndex = selected ? 0 : -1;
      }
      if (panel) panel.hidden = !selected;
    });
    if (active === 'library' && state.selected.size && !state.loading && !state.items.length) loadGrid(true);
    if (active === 'performers') window.axPhotosLoadFaceGroups(true).catch(() => {});
  };

  function faceStatus(message, tone) {
    const node = el('ax-photos-face-status'); if (!node) return;
    node.textContent = String(message || '');
    node.classList.toggle('is-error', tone === 'error'); node.classList.toggle('is-success', tone === 'success');
  }

  window.axPhotosLoadFaceGroups = async function axPhotosLoadFaceGroups(force = false) {
    const box = el('ax-photos-face-groups'), empty = el('ax-photos-face-empty'), mergeToolbar = el('ax-photos-face-merge-toolbar');
    if (!box) return;
    if (!force && !isPhotosVisible()) return;
    if (empty) empty.hidden = true;
    if (mergeToolbar) mergeToolbar.hidden = true;
    box.innerHTML = '<div class="ax2-photos-grid-loading">Loading image-based face groups…</div>';
    try {
      const params = new URLSearchParams();
      if (state.selected.size) params.set('channels', [...state.selected].join(','));
      const result = await api('/api/photos/faces/groups' + (params.size ? '?' + params.toString() : ''), { cache: 'no-store' });
      if (!result || result._ok === false || result.ok !== true) throw new Error(result?.detail || 'Could not load face groups.');
      state.faceGroups = Array.isArray(result.groups) ? result.groups : [];
      const analysis = result.analysis || {};
      const total = Number(analysis.total_photos || 0), analyzed = Number(analysis.analyzed_photos || 0), failed = Number(analysis.failed_photos || 0);
      const tabCount = el('ax-photos-tab-performer-count'); if (tabCount) tabCount.textContent = state.faceGroups.length.toLocaleString();
      const button = el('ax-photos-analyze-faces-btn'); if (button) { button.disabled = state.faceScanWatching || total === 0 || (analyzed >= total && failed === 0); button.innerHTML = state.faceScanWatching ? '<span>Analyzing images…</span>' : ((total > 0 && analyzed >= total && failed === 0) ? '<span>All images analyzed</span>' : '<svg class="ax-icon" aria-hidden="true"><use href="#ax-images"></use></svg><span>Analyze images for faces</span>'); }
      const scope = state.selected.size ? 'selected channels' : 'the full indexed library';
      faceStatus(`${analyzed.toLocaleString()} / ${total.toLocaleString()} photos analyzed in ${scope} · ${state.faceGroups.length.toLocaleString()} visual groups${failed ? ` · ${failed.toLocaleString()} failed (can retry)` : ''}. Groups are based on image pixels, not filenames.`, failed ? 'error' : (analyzed ? 'success' : ''));
      if (!state.faceGroups.length) {
        box.innerHTML = '';
        if (empty) empty.hidden = false;
        if (total === 0) faceStatus('No indexed photos yet. Run Image scan in Metadata & Assets Manager → Processing Stages → Advanced options first.');
        else if (analyzed >= total && failed === 0) faceStatus('Analysis finished, but no faces were detected in this selection.');
        return;
      }
      if (empty) empty.hidden = true;
      const selectedClusterCount = state.faceSelectedClusters.size;
      const existingMergeBar = mergeToolbar;
      if (existingMergeBar) {
        existingMergeBar.hidden = false;
        existingMergeBar.innerHTML = `<span>${selectedClusterCount} clusters selected</span><button type="button" class="btn pri sm" data-ax-photo-action="face-clusters-merge" ${selectedClusterCount < 2 ? 'disabled' : ''}>Merge selected clusters</button><button type="button" class="btn sm" data-ax-photo-action="face-clusters-clear" ${selectedClusterCount ? '' : 'disabled'}>Clear selection</button>`;
      }
      const groups = state.faceGroups.map((group) => {
        const gid = Number(group.id), label = String(group.performer_name || group.label || `Face group ${gid}`);
        const crop = String(group.representative_url || '');
        const assigned = group.performer_id ? `<span class="ax2-face-group-assigned">Assigned · ${escape(group.performer_name || 'Performer')}</span>` : '<span class="ax2-face-group-unassigned">Needs performer review</span>';
        const profile = group.performer_id && group.performer_image_url ? `<img class="ax2-face-group-profile" src="${escape(group.performer_image_url)}?v=${encodeURIComponent(String(group.updated_at || ''))}" alt="" loading="lazy" onerror="this.style.display='none'">` : '';
        const checked = state.faceSelectedClusters.has(gid) ? ' checked' : '';
        const firstDetection = Array.isArray(group.detection_ids) && group.detection_ids.length ? Number(group.detection_ids[0]) : 0;
        const hasPerformerSelection = Boolean(group.performer_id || state.faceGroupNames.get(gid)?.id);
        const suggestions = Array.isArray(group.metadata_suggestions) ? group.metadata_suggestions.slice(0,3) : [];
        const confirmedSuggestions = Array.isArray(group.confirmed_assignment_suggestions) ? group.confirmed_assignment_suggestions.slice(0,3) : [];
        const suggestionCard = (candidate, source, score) => { const hasScore = score !== '' && score !== null && score !== undefined && Number.isFinite(Number(score)); const scoreValue = hasScore ? Number(score) : ''; return `<div class="ax2-face-suggestion-row"><img src="${escape(candidate.image_url || '')}" alt="" loading="lazy" onerror="this.style.display='none'"><span class="ax2-face-suggestion-copy"><b>${escape(candidate.name || 'Unknown performer')}</b><small>${escape(candidate.reason || (source === 'metadata' ? 'Source text match' : 'Similar to a previously confirmed group'))}</small>${source === 'confirmed_assignment' && hasScore ? `<small class="ax2-face-suggestion-score">Embedding similarity ${scoreValue.toFixed(2)} · not a probability</small>` : ''}</span><span class="ax2-face-suggestion-actions"><button type="button" class="btn sm" data-face-compare-suggestion data-face-suggestion-group="${gid}" data-face-suggestion-id="${Number(candidate.id)}" data-face-suggestion-name="${escape(candidate.name || '')}">Compare</button><button type="button" class="btn pri xs" data-face-confirm-suggestion data-face-suggestion-group="${gid}" data-face-suggestion-id="${Number(candidate.id)}" data-face-suggestion-name="${escape(candidate.name || '')}" data-face-suggestion-source="${source}" data-face-suggestion-score="${scoreValue}" data-face-suggestion-basis="${escape(candidate.basis || '')}">Confirm</button><button type="button" class="btn xs" data-face-reject-suggestion data-face-suggestion-group="${gid}" data-face-suggestion-id="${Number(candidate.id)}" data-face-suggestion-name="${escape(candidate.name || '')}" data-face-suggestion-source="${source}" data-face-suggestion-score="${scoreValue}">Not this</button></span></div>`; };
        const visualSuggestionMarkup = !group.performer_id && confirmedSuggestions.length ? `<div class="ax2-face-metadata-suggestions ax2-face-confirmed-suggestions"><strong>Suggestions from your previous confirmations</strong><small>Ranked by similarity to face groups you previously assigned. These are candidates, not automatic assignments.</small>${confirmedSuggestions.map((candidate) => suggestionCard(candidate, 'confirmed_assignment', candidate.similarity_score)).join('')}</div>` : '';
        const metadataSuggestionMarkup = !group.performer_id && suggestions.length ? `<div class="ax2-face-metadata-suggestions"><strong>Suggestions from source text</strong><small>Based on exact filename, caption, or channel-title text matches.</small>${suggestions.map((candidate) => suggestionCard(candidate, 'metadata', null)).join('')}</div>` : '';
        const suggestionMarkup = visualSuggestionMarkup + metadataSuggestionMarkup;
        return `<article class="ax2-face-group-card" data-face-group-card="${gid}">
          <label class="ax2-face-group-select"><input type="checkbox" data-face-cluster-select="${gid}"${checked}><span>Select cluster to merge</span></label>
          <button type="button" class="ax2-face-group-cover" data-ax-photo-action="face-group-view" data-ax-photo-value="${gid}" title="View photos in ${escape(label)}"><img src="${escape(crop)}" alt="Representative face for ${escape(label)}" loading="lazy" decoding="async" onerror="this.closest('.ax2-face-group-cover')?.classList.add('is-missing')"><span>${Number(group.photo_count || 0).toLocaleString()} photos</span></button>
          <div class="ax2-face-group-body"><div class="ax2-face-group-title-row">${profile}<div><h3>${escape(label)}</h3><p>${Number(group.face_count || 0).toLocaleString()} face detections · ${Number(group.photo_count || 0).toLocaleString()} images</p></div></div>${assigned}${suggestionMarkup}
            <label class="ax2-face-performer-search-label" for="ax-face-performer-search-${gid}">Assign to performer</label>
            <input id="ax-face-performer-search-${gid}" class="ax2-face-performer-search" data-face-performer-search="${gid}" type="search" placeholder="Search local performer names…" autocomplete="off" value="${escape(state.faceGroupNames.get(gid)?.name || group.performer_name || '')}">
            <div id="ax-face-performer-results-${gid}" class="ax2-face-performer-results" hidden></div>
            <div class="ax2-face-group-actions"><button type="button" class="btn sm" id="ax-face-review-${gid}" data-ax-photo-action="face-reference-review" data-ax-photo-value="${gid}" title="Open an unblurred side-by-side review; choose a performer here if one is not selected">Review profile/provider images</button><button type="button" class="btn pri sm" data-ax-photo-action="face-assignment-save" data-ax-photo-value="${gid}">Save assignment</button><button type="button" class="btn sm" data-ax-photo-action="face-assignment-clear" data-ax-photo-value="${gid}">Clear</button><button type="button" class="btn sm" data-ax-photo-action="face-group-view" data-ax-photo-value="${gid}">View photos</button>${firstDetection ? `<button type="button" class="btn sm" data-ax-photo-action="face-detection-split" data-ax-photo-value="${firstDetection}" title="Move one face detection to a new cluster">Separate one face</button>` : ''}</div>
          </div></article>`;
      }).join('');
      box.innerHTML = groups;
      state.faceGroups.forEach((group) => {
        const gid = Number(group.id);
        if (group.performer_id && !state.faceGroupNames.has(gid)) state.faceGroupNames.set(gid, { id:Number(group.performer_id), name:String(group.performer_name || '') });
      });
    } catch (error) {
      box.innerHTML = '';
      if (empty) empty.hidden = false;
      faceStatus(error?.message || 'Could not load face groups.', 'error');
    }
  };

  window.axPhotosClearSelectedFaceGroups = function axPhotosClearSelectedFaceGroups() {
    state.faceSelectedClusters.clear();
    window.axPhotosLoadFaceGroups(true).catch(() => {});
  };

  window.axPhotosMergeSelectedFaceGroups = async function axPhotosMergeSelectedFaceGroups() {
    const ids = [...state.faceSelectedClusters].map(Number).filter((id) => Number.isInteger(id) && id > 0);
    if (ids.length < 2) { faceStatus('Select at least two face clusters to merge.', 'error'); return; }
    const confirmText = `Merge ${ids.length} selected visual clusters? Their detected faces and performer assignment will be combined. This can be corrected later by separating individual face detections.`;
    const confirmed = typeof axConfirm === 'function' ? await axConfirm(confirmText, { title: 'Merge face clusters?', okLabel: 'Merge clusters', confirmLabel: 'Merge clusters' }) : window.confirm(confirmText);
    if (!confirmed) return;
    try {
      const result = await api('/api/photos/faces/groups/merge', { method: 'POST', body: JSON.stringify({ group_ids: ids }) });
      if (!result || result._ok === false || result.ok !== true) throw new Error(result?.detail || 'Could not merge face clusters.');
      state.faceSelectedClusters.clear();
      faceStatus(`Merged ${ids.length} visual clusters into group ${result.target_group_id}.`, 'success');
      await window.axPhotosLoadFaceGroups(true);
    } catch (error) { faceStatus(error?.message || 'Could not merge face clusters.', 'error'); }
  };

  window.axPhotosSplitFaceDetection = async function axPhotosSplitFaceDetection(detectionId) {
    const id = Number(detectionId);
    if (!Number.isInteger(id) || id <= 0) return;
    const confirmText = 'Separate this representative face detection into a new anonymous cluster? The other photos in the current cluster will remain unchanged.';
    const confirmed = typeof axConfirm === 'function' ? await axConfirm(confirmText, { title: 'Separate face detection?', okLabel: 'Separate face', confirmLabel: 'Separate face' }) : window.confirm(confirmText);
    if (!confirmed) return;
    try {
      const result = await api(`/api/photos/faces/detections/${id}/split`, { method: 'POST', body: '{}' });
      if (!result || result._ok === false || result.ok !== true) throw new Error(result?.detail || 'Could not split face detection.');
      state.faceSelectedClusters.clear();
      faceStatus(`Face detection moved to new cluster ${result.new_group_id}.`, 'success');
      await window.axPhotosLoadFaceGroups(true);
    } catch (error) { faceStatus(error?.message || 'Could not split face detection.', 'error'); }
  };

  window.axPhotosAnalyzeFaces = async function axPhotosAnalyzeFaces() {
    if (state.faceScanWatching) { faceStatus('Face analysis is already running.'); return; }
    const ids = [...state.selected];
    const scope = ids.length ? `${ids.length} selected channel${ids.length === 1 ? '' : 's'}` : 'the entire indexed Photos library';
    const warning = `Analyze image pixels in ${scope} to detect faces and cluster visually similar faces?

This is processed locally. Archive X will create anonymous face groups and will not automatically assign real performer names. You can assign each group manually after reviewing it. First run downloads the local OpenCV models (about 38 MB).`;
    const confirmed = typeof axConfirm === 'function' ? await axConfirm(warning, { title: 'Analyze photos for faces?', okLabel: 'Analyze images', confirmLabel: 'Analyze images' }) : window.confirm(warning);
    if (!confirmed) return;
    const button = el('ax-photos-analyze-faces-btn'); if (button) button.disabled = true;
    faceStatus('Queueing image-based face analysis…');
    try {
      const result = await api('/api/photos/faces/analyze', { method: 'POST', body: JSON.stringify({ channel_ids: ids }) });
      if (!result || result._ok === false || !result.id) throw new Error(result?.detail || 'Could not queue face analysis.');
      state.faceScanId = String(result.id); state.faceScanWatching = true;
      faceStatus(`Face analysis queued as job ${state.faceScanId}. View progress in Operations.`, 'success');
      await watchJob(state.faceScanId, 'face');
    } catch (error) {
      state.faceScanWatching = false;
      faceStatus(error?.message || 'Could not start face analysis.', 'error');
      if (button) button.disabled = false;
    }
  };

  window.axPhotosSearchPerformerForGroup = function axPhotosSearchPerformerForGroup(groupId) {
    const gid = Number(groupId), input = el(`ax-face-performer-search-${gid}`), results = el(`ax-face-performer-results-${gid}`);
    if (!input || !results) return;
    clearTimeout(state.faceSearchTimers.get(gid));
    const query = input.value.trim();
    if (query.length < 2) { results.hidden = true; results.innerHTML = ''; return; }
    const timer = setTimeout(async () => {
      try {
        const performers = await api('/api/photos/faces/performers?limit=12&q=' + encodeURIComponent(query), { cache: 'no-store' });
        const list = Array.isArray(performers?.items) ? performers.items : [];
        results.innerHTML = list.length ? list.slice(0,12).map((performer) => {
          const id = Number(performer.id); const name = String(performer.name || '');
          const avatar = String(performer.image_url || `/api/performers/${id}/image`);
          return `<button type="button" class="ax2-face-performer-result" data-face-pick-group="${gid}" data-face-pick-id="${id}" data-face-pick-name="${escape(name)}"><img src="${avatar}" alt="" loading="lazy" onerror="this.style.display='none'"><span>${escape(name)}</span><small>Local performer</small></button>`;
        }).join('') : '<div class="ax2-face-performer-no-results">No local performer matches.</div>';
        results.hidden = false;
      } catch (error) {
        results.innerHTML = `<div class="ax2-face-performer-no-results">${escape(error?.message || 'Performer search failed.')}</div>`;
        results.hidden = false;
      }
    }, 220);
    state.faceSearchTimers.set(gid, timer);
  };

  window.axPhotosSaveFaceGroupAssignment = async function axPhotosSaveFaceGroupAssignment(groupId) {
    const gid = Number(groupId), candidate = state.faceGroupNames.get(gid);
    if (!candidate?.id) { faceStatus('Search and select a local performer before saving this assignment.', 'error'); return false; }
    try {
      const result = await api(`/api/photos/faces/groups/${gid}/assign`, { method: 'POST', body: JSON.stringify({ performer_id: candidate.id, group_ids: (state.faceGroups.find((item) => Number(item.id) === gid)?.cluster_ids || [gid]).map(Number) }) });
      if (!result || result._ok === false || result.ok !== true) throw new Error(result?.detail || 'Could not save performer assignment.');
      faceStatus(`Assigned ${candidate.name} to this face group.`, 'success');
      await window.axPhotosLoadFaceGroups(true);
      return true;
    } catch (error) { faceStatus(error?.message || 'Could not save performer assignment.', 'error'); return false; }
  };

  window.axPhotosReviewFaceReferences = async function axPhotosReviewFaceReferences(groupId) {
    const gid = Number(groupId);
    const group = state.faceGroups.find((item) => Number(item.id) === gid);
    const selected = state.faceGroupNames.get(gid) || (group?.performer_id ? { id:Number(group.performer_id), name:String(group.performer_name || '') } : null);
    if (!group) { faceStatus('Face group is no longer available. Refresh the Image Analysis tab.', 'error'); return; }
    const dialog = el('ax-photos-reference-review');
    if (!dialog) { faceStatus('Reference review dialog is unavailable. Refresh Archive X.', 'error'); return; }
    state.faceReview = { groupId:gid, performerId:0, offset:0, hasMore:false, candidates:[], selectedUrl:'', requestId:state.faceReview.requestId + 1 };
    const requestId = state.faceReview.requestId;
    const groupImage = el('ax-photos-reference-group-image');
    if (groupImage) { groupImage.src = String(group.representative_url || ''); groupImage.style.filter = 'none'; groupImage.style.opacity = '1'; groupImage.onerror = () => { groupImage.removeAttribute('src'); }; }
    const groupMeta = el('ax-photos-reference-group-meta');
    if (groupMeta) groupMeta.textContent = `${Number(group.photo_count || 0).toLocaleString()} photos · ${Number(group.face_count || 0).toLocaleString()} face detections · anonymous cluster`;
    const subtitle = el('ax-photos-reference-review-subtitle'); if (subtitle) subtitle.textContent = 'Choose a performer to display its saved profile and provider image options next to this face group.';
    const chooser = el('ax-photos-reference-performer-search');
    if (chooser) chooser.value = selected?.name || '';
    const chooserResults = el('ax-photos-reference-performer-results'); if (chooserResults) { chooserResults.hidden = true; chooserResults.innerHTML = ''; }
    const profile = el('ax-photos-reference-profile-image');
    if (profile) { profile.style.display = selected?.id ? '' : 'none'; profile.style.filter = 'none'; profile.style.opacity = '1'; profile.removeAttribute('src'); }
    const performerName = el('ax-photos-reference-performer-name'); if (performerName) performerName.textContent = selected?.name || 'Choose a performer';
    const providerGrid = el('ax-photos-reference-provider-grid'); if (providerGrid) providerGrid.innerHTML = '';
    const statusNode = el('ax-photos-reference-provider-status'); if (statusNode) statusNode.textContent = selected?.id ? 'Loading saved/provider images…' : 'Search for a performer above. Candidate suggestions on the group card, when available, are based on caption, filename, or channel text only.';
    const more = el('ax-photos-reference-load-more'); if (more) more.hidden = true;
    const focus = el('ax-photos-reference-provider-focus-image'); if (focus) { focus.removeAttribute('src'); focus.hidden = true; focus.style.filter = 'none'; }
    const focusLabel = el('ax-photos-reference-provider-focus-label'); if (focusLabel) focusLabel.textContent = 'Select a provider image to inspect it larger.';
    const save = el('ax-photos-reference-save'); if (save) { save.textContent = `Map group to ${String(selected?.name || 'selected performer')}`; save.disabled = !selected?.id; }
    if (typeof dialog.showModal === 'function') dialog.showModal(); else dialog.setAttribute('open', '');
    if (selected?.id) await axPhotosSetReviewPerformer(selected.id, selected.name, `/api/performers/${Number(selected.id)}/image`, requestId, false);
    else setTimeout(() => chooser?.focus(), 0);
  };


  // ------------------------------------------------------------------
  // SECTION: Reference performer search
  // Performer search and reference images for face review.
  // ------------------------------------------------------------------
  async function axPhotosSetReviewPerformer(performerId, performerName, imageUrl, requestId = state.faceReview.requestId, syncCard = true) {
    const gid = Number(state.faceReview.groupId), id = Number(performerId);
    if (!gid || !id || requestId !== state.faceReview.requestId) return;
    const name = String(performerName || `Performer ${id}`);
    state.faceReview.performerId = id;
    state.faceReview.offset = 0; state.faceReview.hasMore = false; state.faceReview.candidates = []; state.faceReview.selectedUrl = '';
    state.faceGroupNames.set(gid, { id, name, source: 'user-selected' });
    const cardInput = el(`ax-face-performer-search-${gid}`); if (cardInput) cardInput.value = name;
    const cardResults = el(`ax-face-performer-results-${gid}`); if (cardResults) cardResults.hidden = true;
    const profile = el('ax-photos-reference-profile-image');
    if (profile) { profile.style.display = ''; profile.style.filter = 'none'; profile.style.opacity = '1'; profile.src = imageUrl || `/api/performers/${id}/image`; profile.onerror = () => { profile.style.display = 'none'; }; }
    const performerNameNode = el('ax-photos-reference-performer-name'); if (performerNameNode) performerNameNode.textContent = name;
    const subtitle = el('ax-photos-reference-review-subtitle'); if (subtitle) subtitle.textContent = `Compare the selected face group with ${name}'s saved profile and provider images.`;
    const save = el('ax-photos-reference-save'); if (save) { save.textContent = `Map group to ${name}`; save.disabled = false; }
    const statusNode = el('ax-photos-reference-provider-status'); if (statusNode) statusNode.textContent = 'Loading saved/provider images…';
    const grid = el('ax-photos-reference-provider-grid'); if (grid) grid.innerHTML = '';
    const focus = el('ax-photos-reference-provider-focus-image'); if (focus) { focus.removeAttribute('src'); focus.hidden = true; focus.style.filter = 'none'; }
    const focusLabel = el('ax-photos-reference-provider-focus-label'); if (focusLabel) focusLabel.textContent = 'Select a provider image to inspect it larger.';
    const chooser = el('ax-photos-reference-performer-search'); if (chooser) chooser.value = name;
    const chooserResults = el('ax-photos-reference-performer-results'); if (chooserResults) { chooserResults.hidden = true; chooserResults.innerHTML = ''; }
    await loadReferenceImages(requestId, false);
    if (syncCard) faceStatus(`Selected ${name} for side-by-side review. Confirm the assignment only after reviewing the references.`, 'success');
  }

  let referenceSearchTimer = 0;
  async function searchReferencePerformers(query) {
    const results = el('ax-photos-reference-performer-results');
    if (!results) return;
    clearTimeout(referenceSearchTimer);
    const q = String(query || '').trim();
    if (q.length < 2) { results.hidden = true; results.innerHTML = ''; return; }
    referenceSearchTimer = setTimeout(async () => {
      try {
        const response = await api('/api/photos/faces/performers?limit=12&q=' + encodeURIComponent(q), { cache:'no-store' });
        const items = Array.isArray(response?.items) ? response.items : [];
        results.innerHTML = items.length ? items.map((person) => `<button type="button" class="ax2-face-performer-result ax2-photo-reference-performer-result" data-reference-performer-id="${Number(person.id)}" data-reference-performer-name="${escape(person.name || '')}" data-reference-performer-image="${escape(person.image_url || '')}"><img src="${escape(person.image_url || '')}" alt="" loading="lazy" onerror="this.style.display='none'"><span>${escape(person.name || 'Unnamed performer')}</span><small>Choose for comparison</small></button>`).join('') : '<div class="ax2-face-performer-no-results">No performer records matched that search.</div>';
        results.hidden = false;
      } catch (error) {
        results.innerHTML = `<div class="ax2-face-performer-no-results">${escape(error?.message || 'Performer search failed.')}</div>`;
        results.hidden = false;
      }
    }, 180);
  }

  async function loadReferenceImages(requestId, loadMore) {
    const review = state.faceReview;
    if (!review.performerId || requestId !== review.requestId) return;
    const offset = loadMore ? review.offset : 0;
    const statusNode = el('ax-photos-reference-provider-status');
    if (statusNode) statusNode.textContent = loadMore ? 'Loading more provider images…' : 'Loading saved/provider images…';
    try {
      const result = await api(`/api/performers/${review.performerId}/image-candidates?limit=10&offset=${offset}`, { cache:'no-store' });
      if (requestId !== state.faceReview.requestId || review.performerId !== state.faceReview.performerId) return;
      if (!result || result._ok === false) throw new Error(result?.detail || 'Could not load provider images.');
      const incoming = Array.isArray(result.candidates) ? result.candidates : [];
      review.candidates = loadMore ? review.candidates.concat(incoming) : incoming;
      review.offset = offset + 10;
      review.hasMore = Boolean(result.has_more);
      const grid = el('ax-photos-reference-provider-grid');
      if (grid) grid.innerHTML = review.candidates.length ? review.candidates.map((candidate, index) => {
        const url = String(candidate.url || '');
        const provider = String(candidate.provider || 'Provider').replace(/[^a-z0-9 _-]/ig, '');
        const dimensions = candidate.width && candidate.height ? `${Number(candidate.width)}×${Number(candidate.height)}` : '';
        return `<button type="button" class="ax2-photo-reference-candidate" data-review-provider-url="${escape(url)}" data-review-provider-label="${escape(provider + (dimensions ? ' · ' + dimensions : ''))}" title="Inspect this provider image"><img src="${escape(url)}" alt="${escape(provider)} artwork" loading="lazy" onerror="this.closest('.ax2-photo-reference-candidate')?.classList.add('is-missing')"><span>${escape(provider)}${dimensions ? ' · ' + escape(dimensions) : ''}</span></button>`;
      }).join('') : '<div class="ax2-photo-reference-empty">No provider images were returned. The saved profile image is still available on the left.</div>';
      const more = el('ax-photos-reference-load-more'); if (more) more.hidden = !review.hasMore;
      if (statusNode) {
        const counts = review.candidates.reduce((totals, item) => { const key = String(item.provider || 'Provider'); totals[key] = (totals[key] || 0) + 1; return totals; }, {});
        const summary = Object.entries(counts).map(([key, value]) => `${key}: ${value}`).join(' · ');
        statusNode.textContent = review.candidates.length ? `${review.candidates.length} provider reference image(s) loaded${summary ? ' · ' + summary : ''}. Choose one to inspect, then assign the group manually.` : 'No provider reference images available for this performer.';
      }
    } catch (error) {
      if (requestId !== state.faceReview.requestId) return;
      if (statusNode) statusNode.textContent = `Provider image lookup unavailable: ${error?.message || error}. You can still compare with the saved profile picture and assign manually.`;
      const more = el('ax-photos-reference-load-more'); if (more) more.hidden = true;
    }
  }

  window.axPhotosLoadMoreReferenceImages = function axPhotosLoadMoreReferenceImages() {
    return loadReferenceImages(state.faceReview.requestId, true);
  };

  window.axPhotosCloseReferenceReview = function axPhotosCloseReferenceReview() {
    const dialog = el('ax-photos-reference-review');
    if (dialog?.open && typeof dialog.close === 'function') dialog.close();
    else dialog?.removeAttribute('open');
  };

  window.axPhotosConfirmReferenceAssignment = async function axPhotosConfirmReferenceAssignment() {
    const gid = Number(state.faceReview.groupId);
    if (!gid || !state.faceGroupNames.get(gid)?.id) {
      faceStatus('Select a local performer in the face-group card before mapping it.', 'error');
      return;
    }
    const saved = await window.axPhotosSaveFaceGroupAssignment(gid);
    if (saved) window.axPhotosCloseReferenceReview();
  };

  window.axPhotosClearFaceGroupAssignment = async function axPhotosClearFaceGroupAssignment(groupId) {
    const gid = Number(groupId);
    try {
      const result = await api(`/api/photos/faces/groups/${gid}/assign`, { method: 'POST', body: JSON.stringify({ performer_id: null, group_ids: (state.faceGroups.find((item) => Number(item.id) === gid)?.cluster_ids || [gid]).map(Number) }) });
      if (!result || result._ok === false || result.ok !== true) throw new Error(result?.detail || 'Could not clear performer assignment.');
      state.faceGroupNames.delete(gid);
      faceStatus('Performer assignment cleared.', 'success');
      await window.axPhotosLoadFaceGroups(true);
    } catch (error) { faceStatus(error?.message || 'Could not clear performer assignment.', 'error'); }
  };

  window.axPhotosViewFaceGroup = function axPhotosViewFaceGroup(groupId) {
    const group = state.faceGroups.find((item) => Number(item.id) === Number(groupId));
    if (!group) return;
    state.faceGroupId = Number(groupId);
    state.facePerformerId = group.performer_id ? Number(group.performer_id) : null;
    state.faceGroupIds = state.facePerformerId ? [] : (Array.isArray(group.cluster_ids) ? group.cluster_ids.map(Number).filter((value) => Number.isInteger(value) && value > 0) : (Number(groupId) > 0 ? [Number(groupId)] : []));
    const channelIds = Array.isArray(group.channel_ids) ? group.channel_ids.map(String) : [];
    if (channelIds.length) state.selected = new Set(channelIds);
    state.search = ''; state.page = 0; state.items = [];
    const globalSearch = el('gs'); if (globalSearch) globalSearch.value = '';
    const filter = el('ax-photos-group-filter'); if (filter) filter.hidden = false;
    const label = el('ax-photos-group-filter-label'); if (label) label.textContent = `Face group · ${group.performer_name || group.label || group.id}`;
    renderChannels(); window.axPhotosSetTab('library');
  };

  window.axPhotosClearFaceGroupFilter = function axPhotosClearFaceGroupFilter() {
    state.faceGroupId = null; state.faceGroupIds = []; state.facePerformerId = null; state.page = 0; state.items = [];
    const filter = el('ax-photos-group-filter'); if (filter) filter.hidden = true;
    loadGrid(true);
  };

  window.axPhotosLoadChannels = async function axPhotosLoadChannels(force = false) {
    const select = el('ax-photos-channel');
    if (select && (force || !state.loadedChannels)) {
      select.disabled = true; select.innerHTML = '<option value="">Loading channels…</option>';
      try {
        const result = await api('/api/source-channels', { cache: 'no-store' });
        if (!Array.isArray(result)) throw new Error(result?.detail || 'Could not load saved Telegram channels.');
        const channels = result.filter((channel) => String(channel?.telegram_id || '').trim());
        state.savedChannels = channels.slice();
        channels.sort((a, b) => String(a.title || a.username || a.telegram_id).localeCompare(String(b.title || b.username || b.telegram_id)));
        const options = ['<option value="">Choose a destination channel…</option>'];
        channels.forEach((channel) => {
          const id = String(channel.telegram_id).trim();
          const label = displayName(channel);
          options.push(`<option value="${escape(id)}">${escape(label)}</option>`);
        });
        const previous = select.value;
        select.innerHTML = options.join('');
        if (channels.some((channel) => String(channel.telegram_id) === previous)) select.value = previous;
        if (!channels.length) options.length = 0;
        state.loadedChannels = true;
      } catch (error) {
        select.innerHTML = '<option value="">Channels unavailable — retry</option>';
        uploadStatus(error?.message || 'Could not load saved Telegram channels.', 'error');
      } finally { select.disabled = false; }
    }
    await window.axPhotosRefreshLibrary({ quiet: true });
  };

  window.axPhotosRefreshLibrary = async function axPhotosRefreshLibrary(options = {}) {
    if (!isPhotosVisible() && !options.force) return;
    const cards = el('ax-photos-channel-cards');
    try {
      // Use the saved Telegram catalogue to resolve names and avatars, but only
      // expose channels whose full image-history scan completed successfully.
      // A completed scan with zero images still qualifies: it is a scanned channel,
      // not an unknown/unscanned channel. This mirrors the video library's
      // processed-channel scope without allowing unscanned channels into Photos.
      let saved = state.savedChannels.slice();
      if (!saved.length) {
        const savedResult = await api('/api/source-channels', { cache: 'no-store' });
        if (Array.isArray(savedResult)) {
          saved = savedResult.filter((channel) => String(channel?.telegram_id || '').trim());
          state.savedChannels = saved.slice();
        }
      }
      let photoData = [];
      let photoDataError = '';
      try {
        const result = await api('/api/photos/channels', { cache: 'no-store' });
        if (!Array.isArray(result)) throw new Error(result?.detail || 'Photo scan status is unavailable.');
        photoData = result;
      } catch (photoError) {
        photoDataError = photoError?.message || 'Photo scan status is unavailable.';
      }
      const photoById = new Map(photoData.map((channel) => [String(channel.telegram_id || ''), channel]));
      const seen = new Set();
      const mergedChannels = saved.map((channel) => {
        const id = String(channel.telegram_id || '');
        seen.add(id);
        return { ...(photoById.get(id) || {}), ...channel,
          photo_count: Number(photoById.get(id)?.photo_count ?? channel.photo_count ?? 0),
          photo_scan_status: String(photoById.get(id)?.photo_scan_status || channel.photo_scan_status || 'NEVER').toUpperCase(),
          photo_last_scan_at: photoById.get(id)?.photo_last_scan_at || null };
      });
      // Retain indexed-image rows for channels removed from the saved catalogue,
      // but only if those channels have a completed scan-state record as well.
      for (const channel of photoData) {
        const id = String(channel.telegram_id || '');
        if (id && !seen.has(id)) { mergedChannels.push(channel); seen.add(id); }
      }
      state.channels = mergedChannels.filter((channel) => String(channel.photo_scan_status || 'NEVER').toUpperCase() === 'COMPLETE');
      state.channels.sort((a, b) => displayName(a).localeCompare(displayName(b)));
      for (const id of [...state.selected]) if (!state.channels.some((channel) => String(channel.telegram_id) === id)) state.selected.delete(id);
      renderChannels();
      if (state.selected.size) await loadGrid(true);
      else if (!state.faceGroupId) {
        renderEmpty('Select scanned channels to see their photos.', 'Choose one or more completed channel cards above.');
        status(state.channels.length ? 'Only channels with a completed Image scan are shown here.' : '');
      }
      if (state.activeTab === 'performers') await window.axPhotosLoadFaceGroups(true);
      if (photoDataError) status(`Scanned channel list could not refresh: ${photoDataError}`, 'error');
      else if (!state.channels.length) {
        const message = !saved.length
          ? 'No saved Telegram channels found. Open Videos and refresh the Telegram channel list first.'
          : 'No channels have a completed Image scan yet. Open Transfers → Metadata & Assets Manager → Processing Stages → Advanced options, enable Image scan, select channels, and run it.';
        status(message, 'error');
        const summary = el('ax-photos-channel-summary'); if (summary) summary.textContent = '0 scanned channels';
      }
    } catch (error) {
      if (cards) cards.innerHTML = `<div class="muted ax2-photos-channel-error">${escape(error?.message || 'Photo channels are unavailable.')}</div>`;
      status(error?.message || 'Could not refresh photo channels.', 'error');
    }
  };


  // ------------------------------------------------------------------
  // SECTION: Channels and empty states
  // Channel strip, empty state, motion and skeletons.
  // ------------------------------------------------------------------
  function renderChannels() {
    const box = el('ax-photos-channel-cards'); if (!box) return;
    const allSelected = state.channels.length > 0 && state.channels.every((channel) => state.selected.has(String(channel.telegram_id)));
    const total = state.channels.reduce((sum, channel) => sum + Number(channel.photo_count || 0), 0);
    const allCard = state.channels.length ? `<button type="button" class="ax2-channel-card ax2-channel-all${allSelected ? ' selected' : ''}" aria-pressed="${allSelected}" data-ax-photo-action="channels-toggle-all" title="Select all scanned channels"><span class="ax2-channel-avatar"><svg class="ax-icon" aria-hidden="true"><use href="#ax-grid"></use></svg></span><span class="ax2-channel-name">All</span><span class="ax2-channel-count">${total.toLocaleString()}</span></button>` : '';
    const cards = state.channels.map((channel) => {
      const id = String(channel.telegram_id || '');
      const title = displayName(channel);
      const count = Number(channel.photo_count || 0);
      const initials = title.split(/\s+/).filter(Boolean).slice(0, 2).map((part) => Array.from(part)[0] || '').join('').toUpperCase() || 'TG';
      const selected = state.selected.has(id);
      const scanTitle = channel.photo_last_scan_at ? `Last completed image scan ${new Date(channel.photo_last_scan_at).toLocaleString()}` : 'Image scan complete';
      const image = String(channel.image || '').trim();
      const version = encodeURIComponent(String(channel.updated_at || '0'));
      const telegramPhotoUrl = `/api/channels/${encodeURIComponent(id)}/telegram-photo?v=${version}`;
      const isLocalUpload = image.startsWith('manual-upload:');
      const avatarUrl = isLocalUpload ? `/api/channels/${encodeURIComponent(id)}/image?v=${version}` : telegramPhotoUrl;
      const fallbackSrc = isLocalUpload ? telegramPhotoUrl : (/^https?:\/\//i.test(image) ? image : '');
      return `<button type="button" class="ax2-channel-card ax2-photo-channel-card${selected ? ' selected' : ''}" data-channel-id="${escape(id)}" data-ax-photo-action="channel-toggle" data-ax-photo-value="${escape(id)}" aria-pressed="${selected}" title="${escape(`${scanTitle} · ${count.toLocaleString()} photos`)}"><span class="channel-profile-avatar ax2-channel-avatar"><span class="channel-profile-initials" aria-hidden="true">${escape(initials)}</span><img src="${escape(avatarUrl)}" alt="${escape(title)} Telegram profile photo" loading="lazy" decoding="async" onerror="channelAvatarFallback(this,${escape(JSON.stringify(fallbackSrc))})"></span><span class="ax2-channel-name" title="${escape(title)}">${escape(title)}</span><span class="ax2-channel-count">${count.toLocaleString()}</span></button>`;
    }).join('');
    box.innerHTML = allCard + cards;
    const summary = el('ax-photos-channel-summary'); if (summary) summary.textContent = `${state.channels.length.toLocaleString()} scanned channels`;
    const count = el('ax-photos-selected-count');
    if (count) { count.hidden = !state.selected.size; count.textContent = `${state.selected.size} channel${state.selected.size === 1 ? '' : 's'} selected`; }
    syncPhotoResultsSummary();
    const editButton = el('ax-photos-selected-channel-edit');
    if (editButton) {
      const selectedChannel = state.selected.size === 1
        ? state.channels.find((channel) => String(channel.telegram_id || '') === [...state.selected][0])
        : null;
      editButton.disabled = !selectedChannel;
      editButton.title = selectedChannel ? `Edit ${displayName(selectedChannel)}` : 'Select one channel';
    }
  }

  window.axPhotosEditSelectedChannel = function axPhotosEditSelectedChannel() {
    if (state.selected.size !== 1) {
      window.toast?.('Select one channel first.');
      return;
    }
    const channelId = [...state.selected][0];
    if (typeof window.editChannelCard !== 'function') {
      window.toast?.('Channel editor is unavailable.');
      return;
    }
    window.editChannelCard(channelId);
  };

  window.axPhotosToggleChannel = function axPhotosToggleChannel(id) {
    const key = String(id || ''); if (!key) return;
    state.faceGroupId = null; state.faceGroupIds = []; state.facePerformerId = null; if (el('ax-photos-group-filter')) el('ax-photos-group-filter').hidden = true;
    if (state.selected.has(key)) state.selected.delete(key); else state.selected.add(key);
    state.page = 0; renderChannels();
    if (state.selected.size) loadGrid(true); else { renderEmpty('Select channels to see their photos.', 'Choose one or more channel cards above.'); status('Select channel cards to browse indexed photos. To index channel history, use Metadata & Assets Manager → Processing Stages → Advanced options → Image scan.'); }
    if (state.activeTab === 'performers') window.axPhotosLoadFaceGroups(true).catch(() => {});
  };
  window.axPhotosToggleAllChannels = function axPhotosToggleAllChannels() {
    state.faceGroupId = null; state.faceGroupIds = []; state.facePerformerId = null; if (el('ax-photos-group-filter')) el('ax-photos-group-filter').hidden = true;
    const all = state.channels.length > 0 && state.channels.every((channel) => state.selected.has(String(channel.telegram_id)));
    if (all) state.selected.clear(); else state.channels.forEach((channel) => state.selected.add(String(channel.telegram_id)));
    state.page = 0; renderChannels();
    if (state.selected.size) loadGrid(true); else { renderEmpty('Select channels to see their photos.', 'Choose channel cards above.'); status('Select channel cards to browse indexed photos. To index channel history, use Metadata & Assets Manager → Processing Stages → Advanced options → Image scan.'); }
    if (state.activeTab === 'performers') window.axPhotosLoadFaceGroups(true).catch(() => {});
  };

  // Empty library states are kept out of the middle of the masonry wall. Guidance
  // appears in the compact channel status row instead of a large placeholder card.
  function renderEmpty(title, body) {
    const grid = el('ax-photos-grid'), empty = el('ax-photos-empty');
    if (grid) { resetPhotoVirtualGrid(grid); grid.innerHTML = ''; }
    if (empty) empty.hidden = true; // compatibility with older HTML bundles
    syncPhotoResultsSummary();
    const more = el('ax-photos-load-more'); if (more) more.hidden = true;
    const sentinel = el('ax-photos-infinite-sentinel'); if (sentinel) sentinel.hidden = true;
  }

  function prefersReducedPhotoMotion() {
    return !!window.matchMedia?.('(prefers-reduced-motion: reduce)').matches;
  }

  function photoColumnCount(grid) {
    if (!grid) return 4;
    const css = window.getComputedStyle(grid);
    const gap = Number.parseFloat(css.columnGap) || 12;
    const width = Number.parseFloat(css.columnWidth) || 280;
    return Math.max(1, Math.floor((grid.clientWidth + gap) / (width + gap)));
  }

  function photoVisiblePositions() {
    const grid = el('ax-photos-grid');
    if (!grid || prefersReducedPhotoMotion()) return [];
    const margin = 1250;
    const viewHeight = window.innerHeight || document.documentElement.clientHeight;
    const result = [];
    for (const node of grid.querySelectorAll('[data-photo-card]')) {
      if (result.length >= 100) break;
      const rect = node.getBoundingClientRect();
      if (rect.bottom >= -margin && rect.top <= viewHeight + margin) result.push([node, rect]);
    }
    return result;
  }

  function animatePhotoReflow(before) {
    if (!before?.length || prefersReducedPhotoMotion()) return;
    requestAnimationFrame(() => {
      for (const [node, oldRect] of before) {
        if (!node.isConnected || node.classList.contains('ax-photo-entering')) continue;
        const now = node.getBoundingClientRect();
        const dx = oldRect.left - now.left;
        const dy = oldRect.top - now.top;
        if (Math.abs(dx) < 2 && Math.abs(dy) < 2) continue;
        if (typeof node.animate === 'function') {
          node.animate([
            { transform: `translate3d(${dx}px, ${dy}px, 0)` },
            { transform: 'translate3d(0, 0, 0)' }
          ], { duration: 320, easing: 'cubic-bezier(0.16, 1, 0.3, 1)' });
        }
      }
    });
  }

  function renderPhotoSkeletons(count, initial = false) {
    const heights = [236, 318, 188, 272, 214, 344, 198, 286];
    return Array.from({ length: Math.max(1, count) }, (_, index) => {
      const height = heights[(index + (initial ? 0 : 3)) % heights.length];
      return `<div class="ax2-photo-skeleton" aria-hidden="true" style="--skeleton-height:${height}px;--photo-delay:${(index % 5) * 45}ms"></div>`;
    }).join('');
  }


  // ------------------------------------------------------------------
  // SECTION: Card markup and virtual masonry grid
  // Windowed grid: layout, anchors, slots and image loading.
  // ------------------------------------------------------------------
  function photoCardMarkup(item, index, columnCount) {
    const title = String(item.filename || `Photo ${item.message_id}`);
    const thumb = String(item.thumbnail_url || item.image_url || '');
    const channelId = String(item.channel_id || '');
    const messageId = Number(item.message_id || 0);
    const width = Math.max(0, Number(item.width || 0));
    const height = Math.max(0, Number(item.height || 0));
    const ratio = width > 0 && height > 0 ? `${width} / ${height}` : '4 / 3';
    const groupContext = Boolean(state.faceGroupId || state.faceGroupIds.length || state.facePerformerId);
    const differentPersonAction = groupContext ? '<button type="button" role="menuitem" data-photo-action="different-person">Different person · remove from this group</button>' : '';
    const delay = (index % Math.max(1, columnCount)) * 60;
    const favoriteBadge = state.favorites.has(photoFavoriteKey(item)) ? '<span class="ax2-photo-card-favorite-badge" title="Favorite" aria-label="Favorite">★</span>' : '';
    return `<article class="ax2-photo-card" title="${escape(title)}" data-photo-card data-photo-id="${Number(item.id || 0)}" data-photo-channel-id="${escape(channelId)}" data-photo-message-id="${messageId}" data-photo-index="${index}" style="--photo-delay:${delay}ms"><button type="button" class="ax2-photo-card-image img-loading" style="--photo-ratio:${ratio}" data-photo-ratio-known="${width > 0 && height > 0 ? 'true' : 'false'}" data-ax-photo-action="image-open" data-ax-photo-value="${index}" aria-label="Open ${escape(title)}" tabindex="-1"><span class="ax2-photo-card-placeholder" aria-hidden="true"></span></button>${favoriteBadge}<button type="button" class="ax2-photo-card-menu-trigger" data-photo-menu-trigger aria-label="More options for ${escape(title)}" title="More options" aria-expanded="false" tabindex="-1"><svg class="ax2-photo-options-icon" viewBox="0 0 20 20" aria-hidden="true" focusable="false"><path d="M3.5 5.5h13M3.5 10h13M3.5 14.5h13" /></svg></button><div class="ax2-photo-card-menu" data-photo-card-menu role="menu" hidden><button type="button" role="menuitem" data-photo-action="open">Open photo</button><button type="button" role="menuitem" data-photo-action="copy-name">Copy filename</button>${differentPersonAction}<div class="ax2-photo-card-menu-separator" role="separator"></div><button type="button" role="menuitem" data-photo-action="archive-only">Remove from Archive X</button><button type="button" role="menuitem" class="is-danger" data-photo-action="archive-and-telegram">Delete from Archive X and Telegram</button></div></article>`;
  }


  function resetPhotoVirtualGrid(grid = el('ax-photos-grid')) {
    closePhotoCardMenus();
    if (state.photoVirtualObserver) {
      state.photoVirtualObserver.disconnect();
      state.photoVirtualObserver = null;
    }
    if (grid) {
      grid.querySelectorAll('[data-photo-card] img').forEach((image) => {
        image.onload = null; image.onerror = null;
        image.removeAttribute('srcset'); image.removeAttribute('src');
      });
      grid.innerHTML = '';
      grid.classList.remove('is-virtualized');
      grid.style.removeProperty('height');
      grid.style.removeProperty('position');
      grid.style.removeProperty('--ax-photo-grid-total-height');
    }
    state.photoVirtualSlotNodes = [];
    state.photoVirtualActiveImages = new Set();
    if (state.photoVirtualImagesRaf) { window.cancelAnimationFrame(state.photoVirtualImagesRaf); state.photoVirtualImagesRaf = 0; }
    if (state.photoVirtualRatioTimer) { window.clearTimeout(state.photoVirtualRatioTimer); state.photoVirtualRatioTimer = 0; }
    if (state.photoVirtualAnchorRaf) { window.cancelAnimationFrame(state.photoVirtualAnchorRaf); state.photoVirtualAnchorRaf = 0; }
    state.photoVirtualPendingAnchor = null; state.photoVirtualPendingRatios.clear();
    state.photoVirtualPeakMounted = 0;
    state.photoVirtualLayout = [];
    state.photoVirtualColumnEntries = [];
    state.photoVirtualLayoutDirty = true;
    state.photoVirtualGridWidth = 0;
    state.photoVirtualItemCount = 0;
    state.photoVirtualColumns = 0;
    state.photoVirtualCardWidth = 0;
    state.photoVirtualGridHeight = 0;
  }

  function photoVirtualConfig(grid) {
    const width = Math.round(grid?.clientWidth || grid?.getBoundingClientRect().width || 0);
    if (width < 2) return null;
    const style = window.getComputedStyle(grid);
    const configuredWidth = Number.parseFloat(style.getPropertyValue('--ax-photo-preferred-width'));
    const configuredGap = Number.parseFloat(style.getPropertyValue('--ax-photo-column-gap'));
    const preferred = Math.max(80, Number.isFinite(configuredWidth) ? configuredWidth : (window.innerWidth <= 520 ? 93 : window.innerWidth <= 860 ? 132 : window.innerWidth <= 1100 ? 156 : 192));
    const gap = Math.max(0, Number.isFinite(configuredGap) ? configuredGap : 7);
    const columns = Math.max(1, Math.floor((width + gap) / (preferred + gap)));
    const cardWidth = Math.max(1, (width - gap * (columns - 1)) / columns);
    const verticalGap = window.innerWidth <= 520 ? 6 : (window.innerWidth <= 860 ? 8 : 12);
    return { width, preferred, gap, columns, cardWidth, verticalGap };
  }

  function photoVirtualScrollElement(grid) {
    let firstScrollable = null;
    let movingScrollable = null;
    let ancestor = grid?.parentElement || null;
    while (ancestor && ancestor !== document.body && ancestor !== document.documentElement) {
      const style = window.getComputedStyle(ancestor);
      const overflowY = String(style.overflowY || style.overflow || 'visible').toLowerCase();
      if (/^(auto|scroll|overlay)$/.test(overflowY) && ancestor.clientHeight > 0
          && ancestor.scrollHeight > ancestor.clientHeight + 2) {
        if (!firstScrollable) firstScrollable = ancestor;
        if (ancestor.scrollTop > 0) { movingScrollable = ancestor; break; }
      }
      ancestor = ancestor.parentElement;
    }
    if (movingScrollable) return movingScrollable;
    if ((window.scrollY || document.documentElement.scrollTop || 0) > 0) return null;
    return firstScrollable;
  }

  function capturePhotoVirtualAnchor(grid) {
    if (!grid) return null;
    const viewportHeight = window.innerHeight || document.documentElement.clientHeight || 800;
    let best = null, bestDistance = Infinity;
    // A layout change should anchor to a slot that is actually visible now,
    // not an estimated top from cached masonry coordinates. Do the geometry read
    // only when a batch of ratios is applied, never in the per-scroll hot path.
    for (const slot of state.photoVirtualSlotNodes) {
      if (!slot?.isConnected) continue;
      const rect = slot.getBoundingClientRect();
      if (rect.bottom <= 0 || rect.top >= viewportHeight) continue;
      const distance = Math.abs(rect.top);
      if (distance < bestDistance) {
        bestDistance = distance;
        best = { index: Number(slot.dataset.photoIndex), screenTop: rect.top, scrollElement: photoVirtualScrollElement(grid) };
      }
    }
    return best;
  }

  function restorePhotoVirtualAnchor(grid, anchor) {
    if (!grid || !anchor || !Number.isInteger(anchor.index)) return;
    // Never fight active wheel/touch scrolling. Ratios are normally deferred until
    // scroll idle, but this guard also covers a scroll beginning during the frame.
    if (performance.now() - state.photoVirtualLastScrollAt < 180) return;
    const node = state.photoVirtualSlotNodes[anchor.index];
    if (!node?.isConnected) return;
    const delta = node.getBoundingClientRect().top - anchor.screenTop;
    if (Math.abs(delta) < 1) return;
    const scrollElement = photoVirtualScrollElement(grid) || anchor.scrollElement;
    if (scrollElement?.isConnected) scrollElement.scrollTop += delta;
    else window.scrollBy(0, delta);
  }

  function schedulePhotoVirtualAnchorRestore(grid, anchor) {
    if (!anchor) return;
    // Coalesce many thumbnail-decode corrections into one restoration. Multiple
    // per-image restore callbacks used to fight each other and shove the viewport.
    if (!state.photoVirtualPendingAnchor) state.photoVirtualPendingAnchor = { grid, anchor };
    if (state.photoVirtualAnchorRaf) return;
    state.photoVirtualAnchorRaf = window.requestAnimationFrame(() => {
      state.photoVirtualAnchorRaf = 0;
      const pending = state.photoVirtualPendingAnchor;
      state.photoVirtualPendingAnchor = null;
      if (pending) restorePhotoVirtualAnchor(pending.grid, pending.anchor);
    });
  }

  function preparePhotoCardForSlot(card, entry) {
    if (!card || !entry) return;
    card.classList.remove('ax-photo-entering');
    card.style.position = 'relative';
    card.style.left = 'auto';
    card.style.top = 'auto';
    card.style.transform = 'none';
    card.style.width = '100%';
    card.style.height = `${entry.height.toFixed(2)}px`;
    card.style.margin = '0';
    card.style.boxSizing = 'border-box';
  }

  // Card shells and height-reserving slots always stay in normal document flow.
  // Once a thumbnail has loaded, keep its source when it scrolls off-screen. The
  // earlier behavior removed src and the <img> itself for every old card; on long
  // infinite-scroll sessions that made previously visited photos look deleted or
  // blank when the page grew. Only cancel images that have not finished loading.
  function photoVirtualUnmountSlot(slot) {
    const card = slot?.querySelector?.('[data-photo-card]');
    if (!card) return;
    // Keep an actively focused card interactive; normally off-window cards are
    // removed in full, while their fixed-height masonry slot remains in normal
    // flow. This caps DOM/image memory without deleting a photo from the library.
    if (card.contains(document.activeElement)) return false;
    card.querySelectorAll('img').forEach((image) => {
      image.onload = null; image.onerror = null;
      image.removeAttribute('srcset'); image.removeAttribute('src');
    });
    card.remove();
    return true;
  }

  function photoVirtualMountSlot(slot) {
    if (!slot) return;
    const index = Number(slot.dataset.photoIndex);
    if (!Number.isInteger(index) || index < 0 || index >= state.items.length) return;
    const item = state.items[index];
    let card = slot.querySelector('[data-photo-card]');
    if (!card) {
      const entry = state.photoVirtualLayout[index];
      if (!entry) return;
      const template = document.createElement('template');
      template.innerHTML = photoCardMarkup(item, index, state.photoVirtualColumns).trim();
      card = template.content.firstElementChild;
      if (!card) return;
      preparePhotoCardForSlot(card, entry);
      slot.appendChild(card);
    }
    const button = card.querySelector('.ax2-photo-card-image');
    if (!button) return;
    const trigger = card.querySelector('[data-photo-menu-trigger]');
    button.tabIndex = 0;
    if (trigger) trigger.tabIndex = 0;
    if (button.querySelector('img')) return;
    const thumb = String(item.thumbnail_url || item.image_url || '');
    if (!thumb) {
      button.classList.remove('img-loading');
      button.classList.add('img-loaded', 'is-missing', 'is-loaded');
      return;
    }
    const image = document.createElement('img');
    image.alt = String(item.filename || `Photo ${item.message_id || index + 1}`);
    image.loading = 'eager'; // This node exists only for the viewport/buffer window.
    image.decoding = 'async';
    image.setAttribute('decoding', 'async');
    image.classList.add('img-loading');
    image.draggable = false;
    image.onload = () => window.axPhotosImageLoaded(image);
    image.onerror = () => window.axPhotosImageFailed(image);
    button.appendChild(image);
    image.src = thumb; // Set src after insertion so cached loads cannot beat the handler/DOM.
  }

  function gridCardCount(grid) { return grid?.querySelectorAll('[data-photo-card]').length || 0; }

  function photoVirtualVisibleViewport(grid) {
    const height = window.innerHeight || document.documentElement.clientHeight || 800;
    let top = 0, bottom = height;
    let ancestor = grid?.parentElement || null;
    while (ancestor && ancestor !== document.body && ancestor !== document.documentElement) {
      const style = window.getComputedStyle(ancestor);
      const overflow = String(style.overflowY || style.overflow || 'visible').toLowerCase();
      if (/^(auto|scroll|overlay|hidden|clip)$/.test(overflow) && ancestor.clientHeight > 0) {
        const rect = ancestor.getBoundingClientRect();
        const clipTop = rect.top + (ancestor.clientTop || 0);
        const clipBottom = clipTop + ancestor.clientHeight;
        top = Math.max(top, clipTop);
        bottom = Math.min(bottom, clipBottom);
      }
      ancestor = ancestor.parentElement;
    }
    return { top, bottom: Math.max(top, bottom), height: Math.max(0, bottom - top) };
  }

  function firstPhotoEntryAtBottom(entries, minY, rowGap) {
    let low = 0, high = entries.length;
    // Within a masonry column both top and bottom are monotonic. Find the first
    // entry that can intersect the prefetch window in O(log n), then scan only
    // the local range instead of all 10,000+ records on every scroll frame.
    while (low < high) {
      const mid = (low + high) >>> 1;
      if (entries[mid].top + entries[mid].height + rowGap < minY) low = mid + 1;
      else high = mid;
    }
    return low;
  }

  function updatePhotoVirtualImages() {
    const grid = el('ax-photos-grid');
    if (!grid || !grid.classList.contains('is-virtualized') || !state.photoVirtualSlotNodes.length) return;
    const viewport = photoVirtualVisibleViewport(grid);
    const buffer = Math.min(360, Math.max(240, state.photoVirtualBufferMin));
    const gridTop = grid.getBoundingClientRect().top;
    const minY = viewport.top - buffer - gridTop;
    const maxY = viewport.bottom + buffer - gridTop;
    const rowGap = Math.max(0, Number.parseFloat(window.getComputedStyle(grid).getPropertyValue('--ax-photo-virtual-row-gap')) || 12);
    const wanted = new Set();
    const columns = state.photoVirtualColumnEntries || [];
    for (const entries of columns) {
      if (!entries?.length) continue;
      const first = firstPhotoEntryAtBottom(entries, minY, rowGap);
      for (let i = first; i < entries.length; i += 1) {
        const entry = entries[i];
        if (entry.top > maxY) break;
        wanted.add(entry.index);
      }
    }
    for (const index of [...state.photoVirtualActiveImages]) {
      if (wanted.has(index)) continue;
      const removed = photoVirtualUnmountSlot(state.photoVirtualSlotNodes[index]);
      if (removed !== false) state.photoVirtualActiveImages.delete(index);
    }
    for (const index of wanted) {
      const slot = state.photoVirtualSlotNodes[index];
      if (!slot) continue;
      photoVirtualMountSlot(slot);
      if (slot.querySelector('[data-photo-card] img[src]')) state.photoVirtualActiveImages.add(index);
    }
    state.photoVirtualPeakMounted = Math.max(state.photoVirtualPeakMounted, grid.querySelectorAll('[data-photo-card] img[src]').length);
  }

  function schedulePhotoVirtualImages() {
    if (state.photoVirtualImagesRaf) return;
    state.photoVirtualImagesRaf = window.requestAnimationFrame(() => {
      state.photoVirtualImagesRaf = 0;
      updatePhotoVirtualImages();
    });
  }

  function observePhotoVirtualSlots(grid) {
    // One RAF scanner owns image mounting; do not layer a second IntersectionObserver
    // that can leave stale mounted windows after a fast scroll.
    if (state.photoVirtualObserver) { state.photoVirtualObserver.disconnect(); state.photoVirtualObserver = null; }
    state.photoVirtualActiveImages = new Set([...grid.querySelectorAll('[data-photo-card] img[src]')]
      .map((image) => Number(image.closest('[data-photo-card]')?.dataset.photoIndex)).filter(Number.isInteger));
    schedulePhotoVirtualImages();
  }

  function computePhotoVirtualLayout(grid, config = photoVirtualConfig(grid)) {
    if (!grid || !config || !state.items.length) return false;
    const canAppendOnly = grid.classList.contains('is-virtualized')
      && !state.photoVirtualLayoutDirty
      && state.photoVirtualItemCount === state.photoVirtualLayout.length
      && state.photoVirtualItemCount > 0
      && state.photoVirtualItemCount < state.items.length
      && Math.abs(state.photoVirtualGridWidth - config.width) <= 1
      && state.photoVirtualColumns === config.columns
      && Math.abs(state.photoVirtualCardWidth - config.cardWidth) <= 1
      && grid.querySelectorAll('.ax2-photo-masonry-column').length === config.columns;
    if (canAppendOnly) {
      const columnBottoms = Array(config.columns).fill(0);
      for (const entry of state.photoVirtualLayout) {
        columnBottoms[entry.column] = Math.max(columnBottoms[entry.column], entry.top + entry.height + config.verticalGap);
      }
      const columns = [...grid.querySelectorAll('.ax2-photo-masonry-column')];
      const slots = state.photoVirtualSlotNodes;
      for (let index = state.photoVirtualItemCount; index < state.items.length; index += 1) {
        let column = 0;
        for (let candidate = 1; candidate < columnBottoms.length; candidate += 1) {
          if (columnBottoms[candidate] < columnBottoms[column]) column = candidate;
        }
        const item = state.items[index] || {};
        const sourceWidth = Number(item.width || 0), sourceHeight = Number(item.height || 0);
        const ratio = sourceWidth > 0 && sourceHeight > 0 ? sourceWidth / sourceHeight : 4 / 3;
        const height = Math.max(40, config.cardWidth / Math.max(0.02, ratio));
        const entry = { index, left: column * (config.cardWidth + config.gap), top: columnBottoms[column], width: config.cardWidth, height, column, ratio };
        const slot = document.createElement('div');
        slot.className = 'ax2-photo-masonry-slot';
        slot.dataset.photoSlot = 'true';
        slot.dataset.photoIndex = String(index);
        slot.dataset.photoColumn = String(column);
        slot.style.setProperty('--photo-slot-height', `${(height + config.verticalGap).toFixed(2)}px`);
        slot.style.setProperty('--photo-card-height', `${height.toFixed(2)}px`);
        slot.style.setProperty('--photo-ratio', `${ratio} / 1`);
        columns[column].appendChild(slot);
        slots[index] = slot;
        state.photoVirtualLayout.push(entry);
        if (!state.photoVirtualColumnEntries[column]) state.photoVirtualColumnEntries[column] = [];
        state.photoVirtualColumnEntries[column].push(entry);
        columnBottoms[column] += height + config.verticalGap;
      }
      state.photoVirtualSlotNodes = slots;
      state.photoVirtualGridWidth = config.width;
      state.photoVirtualItemCount = state.items.length;
      state.photoVirtualGridHeight = Math.ceil(Math.max(0, ...columnBottoms));
      state.photoVirtualLayoutPasses += 1;
      state.photoVirtualLayoutDirty = false;
      schedulePhotoVirtualImages();
      return true;
    }
    const columnBottoms = Array(config.columns).fill(0);
    const layout = new Array(state.items.length);
    const gap = config.verticalGap;
    for (let index = 0; index < state.items.length; index += 1) {
      const item = state.items[index] || {};
      let column = 0;
      for (let candidate = 1; candidate < columnBottoms.length; candidate += 1) {
        if (columnBottoms[candidate] < columnBottoms[column]) column = candidate;
      }
      const sourceWidth = Number(item.width || 0), sourceHeight = Number(item.height || 0);
      const ratio = sourceWidth > 0 && sourceHeight > 0 ? sourceWidth / sourceHeight : 4 / 3;
      const cardHeight = Math.max(40, config.cardWidth / Math.max(0.02, ratio));
      const top = columnBottoms[column];
      layout[index] = { index, left: column * (config.cardWidth + config.gap), top, width: config.cardWidth, height: cardHeight, column, ratio };
      columnBottoms[column] = top + cardHeight + gap;
    }
    const totalHeight = Math.max(0, ...columnBottoms);
    grid.classList.add('is-virtualized');
    grid.style.removeProperty('position');
    grid.style.removeProperty('height');
    grid.style.setProperty('--ax-photo-grid-total-height', `${Math.ceil(totalHeight)}px`);
    grid.style.setProperty('--ax-photo-virtual-column-gap', `${config.gap}px`);
    grid.style.setProperty('--ax-photo-virtual-row-gap', `${gap}px`);
    // Keep any currently mounted cards alive while the column slots are rebuilt.
    // Clearing their <img> nodes on every append/resize caused visible blank frames
    // and restarted thumbnail decoding while the user was already scrolling.
    const preservedCards = new Map([...grid.querySelectorAll('[data-photo-card]')]
      .map((card) => [Number(card.dataset.photoIndex), card]));
    grid.innerHTML = '';
    const columns = Array.from({ length: config.columns }, (_, columnIndex) => {
      const column = document.createElement('div');
      column.className = 'ax2-photo-masonry-column';
      column.dataset.photoColumn = String(columnIndex);
      grid.appendChild(column);
      return column;
    });
    const slots = new Array(state.items.length);
    for (const entry of layout) {
      const slot = document.createElement('div');
      slot.className = 'ax2-photo-masonry-slot';
      slot.dataset.photoSlot = 'true';
      slot.dataset.photoIndex = String(entry.index);
      slot.dataset.photoColumn = String(entry.column);
      slot.style.setProperty('--photo-slot-height', `${(entry.height + gap).toFixed(2)}px`);
      slot.style.setProperty('--photo-card-height', `${entry.height.toFixed(2)}px`);
      slot.style.setProperty('--photo-ratio', `${entry.ratio} / 1`);
      columns[entry.column].appendChild(slot);
      slots[entry.index] = slot;
      const preserved = preservedCards.get(entry.index);
      if (preserved) {
        preparePhotoCardForSlot(preserved, entry);
        slot.appendChild(preserved);
        preservedCards.delete(entry.index);
      }
    }
    state.photoVirtualSlotNodes = slots;
    state.photoVirtualLayout = layout;
    state.photoVirtualColumnEntries = Array.from({ length: config.columns }, () => []);
    for (const entry of layout) state.photoVirtualColumnEntries[entry.column].push(entry);
    state.photoVirtualGridWidth = config.width;
    state.photoVirtualItemCount = state.items.length;
    state.photoVirtualColumns = config.columns;
    state.photoVirtualCardWidth = config.cardWidth;
    state.photoVirtualGridHeight = Math.ceil(totalHeight);
    state.photoVirtualLayoutDirty = false;
    state.photoVirtualLayoutPasses += 1;
    observePhotoVirtualSlots(grid);
    return true;
  }

  function renderPhotoVirtualWindow(forceLayout = false) {
    const grid = el('ax-photos-grid');
    if (!grid || !state.items.length) return;
    const config = photoVirtualConfig(grid);
    if (!config) return;
    const needsLayout = forceLayout || state.photoVirtualLayoutDirty || state.photoVirtualItemCount !== state.items.length
      || Math.abs(state.photoVirtualGridWidth - config.width) > 1
      || Math.abs(state.photoVirtualCardWidth - config.cardWidth) > 1;
    if (!needsLayout) return;
    // No manual scroll-anchor corrections are needed: all masonry slots stay in
    // normal flow with reserved heights, and unknown-ratio images do not alter
    // those heights after loading. Scripted scrollBy() restoration was fighting
    // the user's scroll and caused the viewport to jump upward as more cards loaded.
    if (!computePhotoVirtualLayout(grid, config)) return;
    state.photoVirtualRenderPasses += 1;
    state.photoVirtualPeakMounted = Math.max(state.photoVirtualPeakMounted, grid?.querySelectorAll('.ax2-photo-card-image.is-loaded').length || 0);
  }

  // Layout diagnostics separate always-present masonry card shells from the
  // small viewport window of actual thumbnail image elements. Keeping shells
  // in normal flow prevents blank gaps; image sources are the memory window.
  window.axPhotosGalleryDiagnostics = function axPhotosGalleryDiagnostics() {
    const grid = el('ax-photos-grid');
    return {
      virtualized: Boolean(grid?.classList.contains('is-virtualized')),
      loadedRecords: state.items.length,
      totalRecords: state.total,
      layoutItems: state.photoVirtualLayout.length,
      layoutSlots: grid?.querySelectorAll('[data-photo-slot]').length || 0,
      mountedCards: gridCardCount(grid),
      mountedImages: grid?.querySelectorAll('[data-photo-card] img[src]').length || 0,
      totalSlots: grid?.querySelectorAll('[data-photo-slot]').length || 0,
      mountedImgElements: grid?.querySelectorAll('[data-photo-card] img').length || 0,
      peakMountedImages: state.photoVirtualPeakMounted,
      maxMountedTarget: state.photoVirtualMaxMounted,
      columnCount: state.photoVirtualColumns,
      cardWidth: Math.round(state.photoVirtualCardWidth * 100) / 100,
      gridHeight: state.photoVirtualGridHeight,
      layoutPasses: state.photoVirtualLayoutPasses,
      renderPasses: state.photoVirtualRenderPasses,
      observerActive: Boolean(state.photoVirtualObserver),
      pendingRenderFrame: Boolean(state.photoVirtualRaf)
    };
  };

  function schedulePhotoVirtualRender(relayout = false) {
    if (relayout) state.photoVirtualLayoutDirty = true;
    if (state.photoVirtualRaf) return;
    state.photoVirtualRaf = window.requestAnimationFrame(() => {
      state.photoVirtualRaf = 0;
      renderPhotoVirtualWindow();
    });
  }

  // Re-declare diagnostics once the renderer is initialized; distinguish the
  // full set of lightweight geometry slots from the small active image window.
  window.axPhotosGalleryDiagnostics = function axPhotosGalleryDiagnostics() {
    const grid = el('ax-photos-grid');
    return {
      virtualized: Boolean(grid?.classList.contains('is-virtualized')),
      loadedRecords: state.items.length,
      totalRecords: state.total,
      layoutItems: state.photoVirtualLayout.length,
      layoutSlots: grid?.querySelectorAll('[data-photo-slot]').length || 0,
      mountedCards: gridCardCount(grid),
      mountedImages: grid?.querySelectorAll('[data-photo-card] img[src]').length || 0,
      totalSlots: grid?.querySelectorAll('[data-photo-slot]').length || 0,
      mountedImgElements: grid?.querySelectorAll('[data-photo-card] img').length || 0,
      peakMountedImages: state.photoVirtualPeakMounted,
      prefetchBufferPx: Math.min(360, Math.max(240, state.photoVirtualBufferMin)),
      columnCount: state.photoVirtualColumns,
      cardWidth: Math.round(state.photoVirtualCardWidth * 100) / 100,
      gridHeight: state.photoVirtualGridHeight,
      layoutPasses: state.photoVirtualLayoutPasses,
      renderPasses: state.photoVirtualRenderPasses,
      pendingRenderFrame: Boolean(state.photoVirtualRaf),
      pendingImagesFrame: Boolean(state.photoVirtualImagesRaf),
      activeImageIndices: [...state.photoVirtualActiveImages].slice(0, 12),
      firstColumnEntries: (state.photoVirtualColumnEntries[0] || []).slice(0, 3).map(e => ({index:e.index,top:e.top,height:e.height}))
    };
  };

  function applyPhotoVirtualItemRatios(grid, updates) {
    if (!grid || !updates?.length) return false;
    // Anchor only after active scrolling has stopped. Ratio changes are batched so
    // a burst of thumbnail decodes cannot schedule contradictory scroll restores.
    const anchor = performance.now() - state.photoVirtualLastScrollAt >= 180 ? capturePhotoVirtualAnchor(grid) : null;
    const gap = Math.max(0, Number.parseFloat(window.getComputedStyle(grid).getPropertyValue('--ax-photo-virtual-row-gap')) || 12);
    const affectedColumns = new Set();
    for (const { index, width, height } of updates) {
      const entry = state.photoVirtualLayout[index];
      const slot = state.photoVirtualSlotNodes[index];
      if (!entry || !slot || !(width > 0 && height > 0)) continue;
      entry.ratio = width / height;
      entry.height = Math.max(40, state.photoVirtualCardWidth / Math.max(0.02, entry.ratio));
      slot.style.setProperty('--photo-ratio', `${entry.ratio} / 1`);
      affectedColumns.add(entry.column);
    }
    for (const column of affectedColumns) {
      const entries = state.photoVirtualLayout.filter((entry) => entry.column === column).sort((a, b) => a.index - b.index);
      let columnHeight = 0;
      for (const entry of entries) {
        entry.top = columnHeight;
        const slot = state.photoVirtualSlotNodes[entry.index];
        slot?.style.setProperty('--photo-slot-height', `${(entry.height + gap).toFixed(2)}px`);
        slot?.style.setProperty('--photo-card-height', `${entry.height.toFixed(2)}px`);
        const card = slot?.querySelector('[data-photo-card]');
        if (card) preparePhotoCardForSlot(card, entry);
        columnHeight += entry.height + gap;
      }
    }
    const columns = Array(state.photoVirtualColumns).fill(0);
    for (const entry of state.photoVirtualLayout) columns[entry.column] = Math.max(columns[entry.column], entry.top + entry.height + gap);
    state.photoVirtualGridHeight = Math.ceil(Math.max(0, ...columns));
    state.photoVirtualLayoutPasses += 1;
    if (anchor) schedulePhotoVirtualAnchorRestore(grid, anchor);
    schedulePhotoVirtualImages();
    return true;
  }

  function flushPhotoVirtualItemRatios() {
    state.photoVirtualRatioTimer = 0;
    if (!state.photoVirtualPendingRatios.size) return;
    const elapsed = performance.now() - state.photoVirtualLastScrollAt;
    if (elapsed < 220) {
      state.photoVirtualRatioTimer = window.setTimeout(flushPhotoVirtualItemRatios, Math.max(40, 240 - elapsed));
      return;
    }
    const grid = el('ax-photos-grid');
    const updates = [...state.photoVirtualPendingRatios.entries()].map(([index, dims]) => ({ index, ...dims }));
    state.photoVirtualPendingRatios.clear();
    applyPhotoVirtualItemRatios(grid, updates);
  }

  function queuePhotoVirtualItemRatio(index, width, height) {
    if (!(Number(width) > 0 && Number(height) > 0)) return;
    state.photoVirtualPendingRatios.set(Number(index), { width: Number(width), height: Number(height) });
    if (state.photoVirtualRatioTimer) window.clearTimeout(state.photoVirtualRatioTimer);
    const elapsed = performance.now() - state.photoVirtualLastScrollAt;
    state.photoVirtualRatioTimer = window.setTimeout(flushPhotoVirtualItemRatios, Math.max(40, 240 - elapsed));
  }

  window.axPhotosImageLoaded = function axPhotosImageLoaded(image) {
    if (!image) return;
    const tile = image.closest('.ax2-photo-card-image');
    const loadedSource = image.currentSrc || image.src;
    let settled = false;
    let fallbackTimer = 0;
    const finish = () => {
      if (settled) return;
      settled = true;
      if (fallbackTimer) window.clearTimeout(fallbackTimer);
      image.removeEventListener('transitionend', onTransitionEnd);
      if (!image.isConnected || !tile?.isConnected || (image.currentSrc || image.src) !== loadedSource) return;
      image.classList.add('is-loaded');
      tile.classList.remove('img-loading');
      tile.classList.add('img-loaded', 'is-loaded');
    };
    const onTransitionEnd = (event) => {
      if (event.target === image && event.propertyName === 'filter') finish();
    };
    (async () => {
      try {
        if (typeof image.decode === 'function') await image.decode();
      } catch (_) {
        // Some valid image formats reject decode() despite having natural dimensions.
      }
      if (!image.isConnected || (image.currentSrc || image.src) !== loadedSource) return;
      if (!(image.naturalWidth > 0) || !(image.naturalHeight > 0)) {
        window.axPhotosImageFailed(image);
        return;
      }
      image.classList.remove('img-loading');
      image.classList.add('img-loaded');
      image.addEventListener('transitionend', onTransitionEnd);
      if (prefersReducedPhotoMotion()) finish();
      else fallbackTimer = window.setTimeout(finish, 520);
    })().catch(() => window.axPhotosImageFailed(image));
  };

  window.axPhotosImageFailed = function axPhotosImageFailed(image) {
    if (!image) return;
    image.classList.remove('img-loading', 'img-loaded');
    image.classList.add('img-failed');
    image.classList.add('is-loaded');
    const tile = image.closest('.ax2-photo-card-image');
    tile?.classList.remove('img-loading');
    tile?.classList.add('img-loaded', 'is-missing', 'is-loaded');
  };


  // ------------------------------------------------------------------
  // SECTION: Loading and rendering the grid
  // Identity, favorites, queries, grid loading and infinite scroll.
  // ------------------------------------------------------------------
  function photoIdentity(item) {
    if (!item) return '';
    const channel = String(item.channel_id || '');
    const message = String(item.message_id || '');
    return channel && message ? `${channel}:${message}` : String(item.id || '');
  }
  function photoFavoriteKey(item) { return String(item?.id || photoIdentity(item) || ''); }
  function persistFavorites() {
    try { window.localStorage.setItem(FAVORITES_STORAGE_KEY, JSON.stringify([...state.favorites])); } catch (_) { /* Browser storage may be disabled. */ }
  }
  function photoQueryParams(page = 0, per = state.per) {
    const params = new URLSearchParams({ page: String(page), per: String(per), channels: [...state.selected].join(','), q: state.search, sort: state.sort });
    if (state.facePerformerId) params.set('face_performer_id', String(state.facePerformerId));
    else if (state.faceGroupIds.length) params.set('face_group_ids', state.faceGroupIds.join(','));
    else if (state.faceGroupId && Number(state.faceGroupId) > 0) params.set('face_group_id', String(state.faceGroupId));
    if (state.favoritesOnly) {
      params.set('favorites', '1');
      params.set('favorite_ids', [...state.favorites].slice(0, 900).join(','));
    }
    return params;
  }
  function currentPhotoSearchValue() {
    const globalSearch = el('gs');
    if (isPhotosVisible() && globalSearch) return String(globalSearch.value || '').trim();
    return String(state.search || '').trim();
  }
  window.axPhotosToggleFavoritesFilter = function axPhotosToggleFavoritesFilter() {
    state.favoritesOnly = !state.favoritesOnly;
    const button = el('ax-photos-favorites-filter');
    if (button) {
      button.classList.toggle('is-active', state.favoritesOnly);
      button.setAttribute('aria-pressed', state.favoritesOnly ? 'true' : 'false');
      button.innerHTML = `<span aria-hidden="true">${state.favoritesOnly ? '★' : '☆'}</span> ${state.favoritesOnly ? 'Showing favorites' : 'Favorites'}`;
    }
    state.search = currentPhotoSearchValue();
    const sort = el('ax-photos-sort'); if (sort) state.sort = String(sort.value || 'date_desc');
    loadGrid(true);
  };
  async function loadGrid(reset) {
    if (!state.selected.size) {
      renderEmpty('Select channels to see their photos.', 'Choose one or more channel cards above.');
      status('Select channel cards to browse indexed photos. To index channel history, use Metadata & Assets Manager → Processing Stages → Advanced options → Image scan.');
      return;
    }
    if (state.loading) { state.reloadRequested = true; return; }
    if (reset) {
      state.page = 0; state.items = [];
      resetPhotoVirtualGrid(el('ax-photos-grid'));
      if (!el('ax-photos-lightbox')?.open) { state.viewerItems = []; state.viewerItemsComplete = false; state.viewerItemsLoadSeq += 1; }
    }
    const startIndex = state.items.length;
    state.loading = true;
    let requestSucceeded = false;
    const grid = el('ax-photos-grid'), empty = el('ax-photos-empty'), sentinel = el('ax-photos-infinite-sentinel');
    if (empty) empty.hidden = true;
    if (sentinel) {
      sentinel.hidden = false;
      sentinel.classList.toggle('is-loading', !reset && startIndex > 0);
    }
    if (grid) {
      if (!state.items.length) grid.innerHTML = renderPhotoSkeletons(8, true);
      else if (!grid.classList.contains('is-virtualized')) {
        const beforeSkeletons = photoVisiblePositions();
        grid.insertAdjacentHTML('beforeend', renderPhotoSkeletons(6, false));
        animatePhotoReflow(beforeSkeletons);
      }
    }
    try {
      state.search = currentPhotoSearchValue();
      state.sort = String(el('ax-photos-sort')?.value || 'date_desc');
      const params = photoQueryParams(state.page, state.per);
      const data = await api('/api/photos?' + params.toString(), { cache: 'no-store' });
      const filter = el('ax-photos-group-filter');
      if (filter) filter.hidden = !state.faceGroupId;
      if (state.faceGroupId) {
        const group = state.faceGroups.find((item) => Number(item.id) === Number(state.faceGroupId));
        const label = el('ax-photos-group-filter-label'); if (label) label.textContent = `Face group · ${group?.performer_name || group?.label || state.faceGroupId}`;
      }
      if (!data || data._ok === false || data.ok !== true) throw new Error(data?.detail || 'Could not load photo records.');
      const incoming = Array.isArray(data.items) ? data.items : [];
      state.total = Number(data.total || 0);
      if (reset) state.items = incoming; else state.items.push(...incoming);
      requestSucceeded = true;
      renderGrid({ append: !reset && startIndex > 0, startIndex });
      status(state.total ? '' : 'No photos indexed in these channels yet. Run Image scan in Metadata & Assets Manager → Processing Stages → Advanced options.', state.total ? 'success' : '');
    } catch (error) {
      const beforeCleanup = photoVisiblePositions();
      grid?.querySelectorAll('.ax2-photo-skeleton').forEach((node) => node.remove());
      if (grid && !state.items.length) grid.innerHTML = '';
      animatePhotoReflow(beforeCleanup);
      renderEmpty('Photo library could not be loaded.', error?.message || 'Check the Operations log and try again.');
      status(error?.message || 'Could not load photos.', 'error');
    } finally {
      state.loading = false;
      sentinel?.classList.remove('is-loading');
      const shouldReload = state.reloadRequested;
      state.reloadRequested = false;
      if (shouldReload) await loadGrid(true);
      else if (requestSucceeded) schedulePhotoAutoLoad();
    }
  }

  function renderGrid(options = {}) {
    const grid = el('ax-photos-grid'), empty = el('ax-photos-empty'), sentinel = el('ax-photos-infinite-sentinel');
    if (!grid) return;
    const items = state.items;
    if (!items.length) {
      renderEmpty(state.search ? 'No photos match this search.' : 'No photos indexed in selected channels yet.', state.search ? 'Try another filename, caption, or channel name.' : 'Use Metadata & Assets Manager → Processing Stages → Advanced options → Image scan to index channel history.');
    } else {
      if (empty) empty.hidden = true;
      grid.querySelectorAll('.ax2-photo-skeleton').forEach((node) => node.remove());
      renderPhotoVirtualWindow(false);
    }
    syncPhotoResultsSummary();
    const tabCount = el('ax-photos-tab-photo-count'); if (tabCount) tabCount.textContent = state.total.toLocaleString();
    if (sentinel) sentinel.hidden = !state.items.length || state.items.length >= state.total;
    refreshFavoriteBadges();
  }

  function schedulePhotoAutoLoad() {
    if (state.autoLoadScheduled) return;
    state.autoLoadScheduled = true;
    const run = () => {
      state.autoLoadScheduled = false;
      if (!isPhotosVisible() || state.activeTab !== 'library' || !state.selected.size || state.loading || state.items.length >= state.total) return;
      const sentinel = el('ax-photos-infinite-sentinel');
      if (!sentinel || sentinel.hidden) return;
      const rect = sentinel.getBoundingClientRect();
      // Start fetching before the bottom is reached; repeat after each batch while the
      // sentinel remains close, so short pages still fill a tall viewport naturally.
      if (rect.top <= (window.innerHeight || document.documentElement.clientHeight) + 900 && rect.bottom >= -900) {
        window.axPhotosLoadMore();
      }
    };
    if (typeof window.requestAnimationFrame === 'function') window.requestAnimationFrame(run);
    else window.setTimeout(run, 0);
  }

  function initPhotoInfiniteScroll() {
    const sentinel = el('ax-photos-infinite-sentinel');
    if (!sentinel || sentinel.dataset.infiniteReady === 'true') return;
    sentinel.dataset.infiniteReady = 'true';
    if ('IntersectionObserver' in window) {
      state.photoLoadObserver = new IntersectionObserver((entries) => {
        if (entries.some((entry) => entry.isIntersecting)) schedulePhotoAutoLoad();
      }, { root: null, rootMargin: '900px 0px 900px 0px', threshold: 0 });
      state.photoLoadObserver.observe(sentinel);
    }
    // Listen to both root/document scrolling and nested app panes. Use one
    // rAF scanner so each actual scroll position gets a fresh card window.
    const onPhotoScroll = () => {
      state.photoVirtualLastScrollAt = performance.now();
      schedulePhotoVirtualImages();
      schedulePhotoAutoLoad();
    };
    document.addEventListener('scroll', onPhotoScroll, { passive: true, capture: true });
    window.addEventListener('scroll', onPhotoScroll, { passive: true });
    window.addEventListener('resize', () => {
      schedulePhotoVirtualRender(true);
      schedulePhotoVirtualImages();
      schedulePhotoAutoLoad();
    }, { passive: true });
    const grid = el('ax-photos-grid');
    if (grid && 'ResizeObserver' in window) {
      state.photoVirtualResizeObserver = new ResizeObserver(() => {
        const width = Math.round(grid.clientWidth || 0);
        if (width >= 2 && Math.abs(width - state.photoVirtualGridWidth) > 1) schedulePhotoVirtualRender(true);
      });
      state.photoVirtualResizeObserver.observe(grid);
    }
  }

  window.axPhotosLoadMore = async function axPhotosLoadMore() { if (state.loading || !state.selected.size || state.items.length >= state.total) return; state.page++; await loadGrid(false); };


  // ------------------------------------------------------------------
  // SECTION: Viewer: HUD and collection
  // Current item, formatted details and favorite state.
  // ------------------------------------------------------------------
  function currentViewerItem() {
    return state.viewerItems[state.viewerIndex] || state.items[state.viewerIndex] || null;
  }
  function viewerCollection() { return Array.isArray(state.viewerItems) && state.viewerItems.length ? state.viewerItems : state.items; }
  function formatPhotoBytes(value) {
    const bytes = Number(value || 0);
    if (!(bytes > 0)) return '';
    if (bytes < 1024) return `${bytes} B`;
    if (bytes < 1024 * 1024) return `${(bytes / 1024).toFixed(0)} KB`;
    return `${(bytes / (1024 * 1024)).toFixed(1)} MB`;
  }
  function formatPhotoDate(value) {
    if (!value) return '';
    let date;
    if (typeof value === 'number' || /^\d{10,13}$/.test(String(value))) {
      const n = Number(value); date = new Date(n < 100000000000 ? n * 1000 : n);
    } else date = new Date(String(value));
    if (!Number.isFinite(date.getTime())) return String(value).slice(0, 24);
    return new Intl.DateTimeFormat(undefined, { year:'numeric', month:'short', day:'2-digit' }).format(date);
  }
  function updateViewerHud(item) {
    if (!item) return;
    const title = el('ax-photos-lightbox-title');
    const width = Number(item.width || 0), height = Number(item.height || 0);
    if (title) title.textContent = String(item.filename || `Photo ${item.message_id || state.viewerIndex + 1}`);
    const dims = el('ax-photos-viewer-dims'); if (dims) { dims.textContent = width > 0 && height > 0 ? `${width} × ${height}` : 'Dimensions unavailable'; dims.hidden = !(width > 0 && height > 0); }
    const mp = el('ax-photos-viewer-mp'); if (mp) { mp.textContent = width > 0 && height > 0 ? `${((width * height) / 1000000).toFixed(1)} MP` : ''; mp.hidden = !(width > 0 && height > 0); }
    const size = el('ax-photos-viewer-size'); if (size) { size.textContent = formatPhotoBytes(item.size); size.hidden = !Number(item.size || 0); }
    const date = el('ax-photos-viewer-date'); if (date) { const formatted = formatPhotoDate(item.message_date); date.textContent = formatted ? `Message · ${formatted}` : ''; date.hidden = !formatted; }
    const channel = el('ax-photos-viewer-channel'); if (channel) { channel.textContent = String(item.channel_title || ''); channel.hidden = !item.channel_title; channel.title = String(item.channel_title || ''); }
    const counter = el('ax-photos-viewer-counter');
    const list = viewerCollection();
    if (counter) counter.textContent = `${Math.max(0, state.viewerIndex) + 1} / ${state.viewerItemsComplete ? list.length : (state.total || list.length)}`;
    updateViewerFavoriteButtons(item);
  }
  function updateViewerFavoriteButtons(item) {
    const key = photoFavoriteKey(item), favorite = Boolean(key && state.favorites.has(key));
    const button = el('ax-photos-viewer-favorite');
    if (button) { button.textContent = favorite ? '★' : '☆'; button.classList.toggle('is-favorite', favorite); button.setAttribute('aria-pressed', favorite ? 'true' : 'false'); button.title = favorite ? 'Remove from favorites' : 'Add to favorites'; }
  }
  function refreshFavoriteBadges() {
    const grid = el('ax-photos-grid'); if (!grid) return;
    grid.querySelectorAll('[data-photo-card]').forEach((card) => {
      const id = String(card.dataset.photoId || '');
      let badge = card.querySelector('.ax2-photo-card-favorite-badge');
      if (id && state.favorites.has(id)) {
        if (!badge) { badge = document.createElement('span'); badge.className = 'ax2-photo-card-favorite-badge'; badge.textContent = '★'; badge.title = 'Favorite'; card.appendChild(badge); }
      } else badge?.remove();
    });
  }

  // ------------------------------------------------------------------
  // SECTION: Viewer: zoom and pan
  // Scale, pose clamping and fit behaviour.
  // ------------------------------------------------------------------
  function stopViewerTransformAnimation() {
    if (state.viewerAnimRaf) window.cancelAnimationFrame(state.viewerAnimRaf);
    state.viewerAnimRaf = 0;
  }
  function clampViewerScale(scale) {
    const fit = Math.max(0.025, Number(state.viewerFitScale || 1));
    const min = Math.max(0.008, fit * 0.15);
    const max = Math.min(16, Math.max(2, fit * 12));
    return Math.max(min, Math.min(max, Number(scale) || fit));
  }
  function clampViewerPose(zoom, x, y) {
    const image = el('ax-photos-lightbox-img'), stage = el('ax-photos-viewer-stage');
    if (!image || !stage || !(image.naturalWidth > 0) || !(image.naturalHeight > 0)) return { x, y };
    const rect = stage.getBoundingClientRect();
    const rotated = Math.abs(state.viewerRotation % 180) === 90;
    const displayWidth = (rotated ? image.naturalHeight : image.naturalWidth) * zoom;
    const displayHeight = (rotated ? image.naturalWidth : image.naturalHeight) * zoom;
    // If the image is larger than the stage, retain coverage at each edge. If
    // it is smaller, keep the complete image inside the stage while allowing it
    // to be repositioned at fitted and below-fit scales.
    const limitX = Math.abs(displayWidth - rect.width) / 2;
    const limitY = Math.abs(displayHeight - rect.height) / 2;
    return {
      x: Math.max(-limitX, Math.min(limitX, Number(x) || 0)),
      y: Math.max(-limitY, Math.min(limitY, Number(y) || 0)),
    };
  }
  function clampViewerPan() {
    const pose = clampViewerPose(state.viewerZoom, state.viewerX, state.viewerY);
    state.viewerX = pose.x; state.viewerY = pose.y;
  }
  function setViewerTransform() {
    const image = el('ax-photos-lightbox-img');
    if (!image) return;
    clampViewerPan();
    image.style.transform = `translate(-50%, -50%) translate3d(${state.viewerX}px, ${state.viewerY}px, 0) scale(${state.viewerZoom}) rotate(${state.viewerRotation}deg)`;
    image.classList.toggle('is-zoomed', state.viewerZoom > state.viewerFitScale + 0.015);
    const zoomLabel = el('ax-photos-zoom-level');
    if (zoomLabel) { zoomLabel.textContent = `${Math.round(state.viewerZoom * 100)}%`; zoomLabel.title = state.viewerZoom >= .999 ? 'Actual pixel scale (1:1)' : 'Image scale relative to original pixels'; }
    const stage = el('ax-photos-viewer-stage');
    if (stage) {
      stage.classList.toggle('is-zoomed', state.viewerZoom > state.viewerFitScale + 0.015);
      stage.classList.toggle('is-pannable', Boolean(image.naturalWidth && image.naturalHeight));
    }
  }
  function computeViewerFitScale() {
    const image = el('ax-photos-lightbox-img'), stage = el('ax-photos-viewer-stage');
    if (!image || !stage || !(image.naturalWidth > 0) || !(image.naturalHeight > 0)) return 1;
    const rect = stage.getBoundingClientRect();
    const availableWidth = Math.max(80, rect.width - (window.innerWidth <= 700 ? 50 : 100));
    const availableHeight = Math.max(80, rect.height - 18);
    const rotated = Math.abs(state.viewerRotation % 180) === 90;
    const width = rotated ? image.naturalHeight : image.naturalWidth;
    const height = rotated ? image.naturalWidth : image.naturalHeight;
    return Math.max(0.025, Math.min(1, availableWidth / width, availableHeight / height));
  }
  function animateViewerToTarget() {
    if (state.viewerAnimRaf) return;
    let lastTime = 0;
    const tick = (now) => {
      state.viewerAnimRaf = 0;
      if (!el('ax-photos-lightbox')?.open) return;
      const elapsed = lastTime ? Math.min(48, now - lastTime) : 16.7;
      lastTime = now;
      const blend = prefersReducedPhotoMotion() ? 1 : 1 - Math.exp(-elapsed / 58);
      state.viewerZoom += (state.viewerTargetZoom - state.viewerZoom) * blend;
      state.viewerX += (state.viewerTargetX - state.viewerX) * blend;
      state.viewerY += (state.viewerTargetY - state.viewerY) * blend;
      clampViewerPan();
      const done = Math.abs(state.viewerTargetZoom - state.viewerZoom) < 0.0006
        && Math.abs(state.viewerTargetX - state.viewerX) < 0.18
        && Math.abs(state.viewerTargetY - state.viewerY) < 0.18;
      if (done) {
        state.viewerZoom = state.viewerTargetZoom;
        const pose = clampViewerPose(state.viewerZoom, state.viewerTargetX, state.viewerTargetY);
        state.viewerX = pose.x; state.viewerY = pose.y;
        setViewerTransform();
        return;
      }
      setViewerTransform();
      state.viewerAnimRaf = window.requestAnimationFrame(tick);
    };
    state.viewerAnimRaf = window.requestAnimationFrame(tick);
  }
  function zoomViewerToScale(scale, clientX, clientY) {
    const image = el('ax-photos-lightbox-img'), stage = el('ax-photos-viewer-stage');
    if (!image || !stage || !(image.naturalWidth > 0) || !(image.naturalHeight > 0)) return;
    const rect = stage.getBoundingClientRect();
    const mx = Number.isFinite(clientX) ? clientX : rect.left + rect.width / 2;
    const my = Number.isFinite(clientY) ? clientY : rect.top + rect.height / 2;
    const currentScale = Math.max(0.001, state.viewerZoom);
    const nextScale = clampViewerScale(scale);
    // Compute the image coordinate under the cursor from the *currently rendered*
    // pose. Repeated wheel events can advance the target without anchoring against
    // a stale starting transform, which avoids drifting toward the stage center.
    const cursorX = mx - (rect.left + rect.width / 2);
    const cursorY = my - (rect.top + rect.height / 2);
    const ratio = nextScale / currentScale;
    const nextX = cursorX - (cursorX - state.viewerX) * ratio;
    const nextY = cursorY - (cursorY - state.viewerY) * ratio;
    const pose = clampViewerPose(nextScale, nextX, nextY);
    state.viewerTargetZoom = nextScale;
    state.viewerTargetX = pose.x;
    state.viewerTargetY = pose.y;
    animateViewerToTarget();
  }
  function fitViewerImage() {
    stopViewerTransformAnimation();
    state.viewerFitScale = computeViewerFitScale();
    state.viewerZoom = state.viewerFitScale;
    state.viewerTargetZoom = state.viewerFitScale;
    state.viewerX = state.viewerTargetX = 0;
    state.viewerY = state.viewerTargetY = 0;
    setViewerTransform();
  }
  function resetViewerZoom() { fitViewerImage(); }
  function resetViewerForNewPhoto() {
    stopViewerTransformAnimation();
    state.viewerRotation = 0;
    state.viewerFitScale = 1;
    state.viewerZoom = state.viewerTargetZoom = 1;
    state.viewerX = state.viewerTargetX = 0;
    state.viewerY = state.viewerTargetY = 0;
    state.viewerSetFitOnLoad = true;
    setViewerTransform();
  }

  // ------------------------------------------------------------------
  // SECTION: Viewer: decoded image cache and queue
  // Bounded decode cache and load queue.
  // ------------------------------------------------------------------
  // Picasa-inspired decoded-image pipeline. Image elements are decoded off the
  // interaction path, kept in a byte-budgeted LRU, and loaded through a priority queue.
  const VIEWER_IMAGE_DEFAULT_CACHE_BUDGET = 192 * 1024 * 1024;
  const VIEWER_IMAGE_DEFAULT_CACHE_ENTRIES = 16;
  const VIEWER_IMAGE_QUEUE_CONCURRENCY = 2;
  // Warm 50 originals through the bounded queue; keep decoded RAM capped rather
  // than retaining 50 full-resolution bitmaps at once.
  const VIEWER_IMAGE_PREFETCH_AHEAD = 50;
  function normalizedViewerImageUrl(url) {
    try { return new URL(String(url || ''), window.location.href).href; }
    catch (_) { return String(url || ''); }
  }
  function viewerImageMetric(name, amount = 1) {
    const metrics = state.viewerImageMetrics;
    if (metrics && Object.prototype.hasOwnProperty.call(metrics, name)) metrics[name] += amount;
  }
  function getViewerDecodedImage(url) {
    const key = normalizedViewerImageUrl(url);
    const entry = state.viewerImageCache.get(key);
    if (!entry) return null;
    state.viewerImageCache.delete(key);
    entry.lastUsedAt = performance.now();
    state.viewerImageCache.set(key, entry);
    viewerImageMetric('cacheHits');
    return entry;
  }
  function cacheViewerDecodedImage(entry, task) {
    const key = entry?.key;
    if (!key || !entry.image || !entry.width || !entry.height || task?.obsolete) return false;
    const budget = Number(state.viewerImageCacheBudgetBytes || VIEWER_IMAGE_DEFAULT_CACHE_BUDGET);
    const maxEntries = Number(state.viewerImageCacheMaxEntries || VIEWER_IMAGE_DEFAULT_CACHE_ENTRIES);
    if (entry.bytes > budget) { viewerImageMetric('oversizedNotCached'); return false; }
    const existing = state.viewerImageCache.get(key);
    if (existing) {
      state.viewerImageCacheBytes = Math.max(0, state.viewerImageCacheBytes - Number(existing.bytes || 0));
      state.viewerImageCache.delete(key);
    }
    while (state.viewerImageCache.size && (state.viewerImageCacheBytes + entry.bytes > budget || state.viewerImageCache.size >= maxEntries)) {
      let victimKey = null;
      for (const [candidateKey] of state.viewerImageCache) {
        // Keep the currently selected original pinned until another selection commits.
        if (candidateKey === state.viewerCurrentTargetUrl || candidateKey === key) continue;
        victimKey = candidateKey; break;
      }
      if (victimKey === null) break;
      const victim = state.viewerImageCache.get(victimKey);
      state.viewerImageCache.delete(victimKey);
      state.viewerImageCacheBytes = Math.max(0, state.viewerImageCacheBytes - Number(victim?.bytes || 0));
      viewerImageMetric('cacheEvictions');
    }
    if (state.viewerImageCacheBytes + entry.bytes > budget || state.viewerImageCache.size >= maxEntries) {
      viewerImageMetric('oversizedNotCached');
      return false;
    }
    entry.lastUsedAt = performance.now();
    state.viewerImageCache.set(key, entry);
    state.viewerImageCacheBytes += entry.bytes;
    return true;
  }
  function finishViewerImageTask(task, entry) {
    if (!task || task.finished) return;
    task.finished = true;
    if (state.viewerImageInflight.get(task.key) === task) state.viewerImageInflight.delete(task.key);
    state.viewerImageQueueRunning = Math.max(0, state.viewerImageQueueRunning - (task.status === 'running' ? 1 : 0));
    task.status = 'finished';
    task.resolve(entry || null);
    pumpViewerImageQueue();
  }
  function startViewerImageTask(task) {
    if (!task || task.finished || task.cancelled) { finishViewerImageTask(task, null); return; }
    task.status = 'running';
    task.startedAt = performance.now();
    viewerImageMetric('queueWaitMsTotal', Math.max(0, task.startedAt - task.enqueuedAt));
    state.viewerImageQueueRunning += 1;
    const image = new Image();
    task.image = image;
    image.decoding = 'async';
    image.loading = 'eager';
    try { if ('fetchPriority' in image) image.fetchPriority = task.priority >= 90 ? 'high' : (task.priority >= 60 ? 'auto' : 'low'); } catch (_) {}
    let finalizing = false;
    const fail = () => {
      if (finalizing || task.finished) return;
      finalizing = true;
      viewerImageMetric('decodeFailures');
      finishViewerImageTask(task, null);
    };
    const loaded = async () => {
      if (finalizing || task.finished) return;
      finalizing = true;
      const decodeStart = performance.now();
      try {
        if (typeof image.decode === 'function') await image.decode();
      } catch (_) {
        // Some formats report a load event but reject decode(); natural dimensions
        // remain the authoritative check before admitting the resource to the cache.
      }
      const decodeMs = Math.max(0, performance.now() - decodeStart);
      if (!(image.naturalWidth > 0) || !(image.naturalHeight > 0)) {
        viewerImageMetric('decodeFailures');
        finishViewerImageTask(task, null);
        return;
      }
      viewerImageMetric('decodedLoads');
      viewerImageMetric('decodeMsTotal', decodeMs);
      const entry = { key:task.key, url:task.url, image, width:image.naturalWidth, height:image.naturalHeight,
        bytes:Math.max(4, image.naturalWidth * image.naturalHeight * 4), decodeMs, purpose:task.purpose || 'prefetch',
        loadedAt:performance.now(), lastUsedAt:performance.now() };
      cacheViewerDecodedImage(entry, task);
      finishViewerImageTask(task, entry);
    };
    image.onload = loaded;
    image.onerror = fail;
    try { image.src = task.url; }
    catch (_) { fail(); return; }
    if (image.complete && image.naturalWidth > 0) Promise.resolve().then(loaded);
  }
  function pumpViewerImageQueue() {
    if (!Array.isArray(state.viewerImageQueue)) return;
    state.viewerImageQueue.sort((a, b) => (b.priority - a.priority) || (a.order - b.order));
    while (state.viewerImageQueueRunning < (state.viewerImageQueueLimit || VIEWER_IMAGE_QUEUE_CONCURRENCY) && state.viewerImageQueue.length) {
      const task = state.viewerImageQueue.shift();
      if (!task || task.finished || task.cancelled) continue;
      startViewerImageTask(task);
    }
  }
  function requestViewerDecodedImage(url, priority = 10, purpose = 'prefetch') {
    const key = normalizedViewerImageUrl(url);
    if (!key) return Promise.resolve(null);
    const cached = getViewerDecodedImage(key);
    if (cached) { viewerImageMetric('cacheReuses'); return Promise.resolve(cached); }
    const existing = state.viewerImageInflight.get(key);
    if (existing && !existing.finished) {
      existing.priority = Math.max(existing.priority, Number(priority) || 0);
      if (existing.status === 'queued') pumpViewerImageQueue();
      viewerImageMetric('sharedRequests');
      return existing.promise;
    }
    viewerImageMetric('cacheMisses');
    viewerImageMetric('queuedLoads');
    const task = { key, url:key, priority:Number(priority)||0, purpose, order:++state.viewerImageQueueOrder,
      enqueuedAt:performance.now(), status:'queued', finished:false, cancelled:false, obsolete:false, image:null, resolve:null, promise:null };
    task.promise = new Promise((resolve) => { task.resolve = resolve; });
    state.viewerImageInflight.set(key, task);
    state.viewerImageQueue.push(task);
    pumpViewerImageQueue();
    return task.promise;
  }
  function cancelObsoleteQueuedViewerImages(wantedPriorities) {
    for (const task of state.viewerImageQueue) {
      if (wantedPriorities.has(task.key)) {
        task.priority = Math.max(task.priority, wantedPriorities.get(task.key));
        task.obsolete = false;
      } else {
        task.cancelled = true;
        task.finished = true;
        task.status = 'cancelled';
        if (state.viewerImageInflight.get(task.key) === task) state.viewerImageInflight.delete(task.key);
        task.resolve(null);
        viewerImageMetric('cancelledQueued');
      }
    }
    state.viewerImageQueue = state.viewerImageQueue.filter((task) => !task.cancelled && !task.finished);
    for (const [key, task] of [...state.viewerImageInflight]) {
      if (task.status !== 'running') continue;
      if (wantedPriorities.has(key)) {
        task.priority = Math.max(task.priority, wantedPriorities.get(key));
        task.obsolete = false;
      } else {
        task.obsolete = true;
        // Stop obsolete in-flight image reads when they have not completed yet.
        // This frees a worker slot for the newly selected photo instead of letting
        // stale neighbors delay it. Completed decode operations are allowed to end.
        if (task.image && !task.image.complete && !task.finished) {
          task.cancelled = true;
          task.image.onload = null;
          task.image.onerror = null;
          try { task.image.src = ''; } catch (_) {}
          viewerImageMetric('cancelledRunning');
          finishViewerImageTask(task, null);
        }
      }
    }
  }
  function selectedViewerImageStillCurrent(index, seq, url) {
    const dialog = el('ax-photos-lightbox');
    return Boolean(dialog?.open && state.viewerImageLoadSeq === seq && state.viewerIndex === index
      && normalizedViewerImageUrl(state.viewerCurrentTargetUrl) === normalizedViewerImageUrl(url));
  }

  // ------------------------------------------------------------------
  // SECTION: Viewer: preview to original handoff
  // Preview and full-resolution image swap.
  // ------------------------------------------------------------------
  function hideViewerPreview(seq = state.viewerImageLoadSeq) {
    if (seq !== state.viewerImageLoadSeq) return;
    cancelViewerPreviewImage();
    const stage = el('ax-photos-viewer-stage'), preview = el('ax-photos-lightbox-preview');
    if (stage) stage.classList.remove('ax-photo-preview-active');
    if (preview) preview.style.opacity = '0';
  }
  function cancelViewerPreviewImage() {
    const image = state.viewerPreviewImage;
    state.viewerPreviewImage = null;
    if (!image) return;
    image.onload = null;
    image.onerror = null;
    try { image.src = ''; } catch (_) {}
  }
  function showViewerImageLoader(seq = state.viewerImageLoadSeq) {
    if (seq !== state.viewerImageLoadSeq) return;
    const loader = el('viewerLoader');
    if (loader) loader.hidden = false;
  }
  function hideViewerImageLoader(seq = state.viewerImageLoadSeq) {
    if (seq !== state.viewerImageLoadSeq) return;
    const loader = el('viewerLoader');
    if (loader) loader.hidden = true;
  }
  function showViewerPreview(item, index, seq) {
    const stage = el('ax-photos-viewer-stage'), preview = el('ax-photos-lightbox-preview');
    const fullUrl = String(item?.image_url || item?.preview_url || item?.thumbnail_url || '');
    // Prefer the small thumb while navigating; if unavailable, use the medium
    // preview, then fall back to the original. Never replace a visible preview
    // until the next candidate is already loaded and decoded.
    const thumbnailUrl = String(item?.thumbnail_url || item?.preview_url || fullUrl || '');
    if (!stage || !preview || !thumbnailUrl || !fullUrl) {
      hideViewerImageLoader(seq);
      return;
    }
    cancelViewerPreviewImage();
    state.viewerPreviewSeq = seq;
    const isCurrent = () => selectedViewerImageStillCurrent(index, seq, fullUrl)
      && state.viewerOriginalVisibleSeq !== seq;
    const revealDecodedPreview = (entry, purpose) => {
      if (!entry || !isCurrent()) return;
      const nextUrl = normalizedViewerImageUrl(entry.url);
      const displayedUrl = normalizedViewerImageUrl(preview.getAttribute('src') || '');
      if (nextUrl === displayedUrl && preview.complete && preview.naturalWidth > 0) {
        stage.classList.add('ax-photo-preview-active');
        preview.style.opacity = '1';
        hideViewerImageLoader(seq);
      } else {
        // The candidate was decoded in a separate Image element before being
        // assigned here. Keep the outgoing preview visible until this element
        // confirms the new source has actually loaded.
        const onLoad = async () => {
          if (!isCurrent() || normalizedViewerImageUrl(preview.getAttribute('src') || '') !== nextUrl) return;
          try {
            if (typeof preview.decode === 'function') await preview.decode();
          } catch (_) {
            if (!(preview.naturalWidth > 0)) return;
          }
          if (!isCurrent() || normalizedViewerImageUrl(preview.getAttribute('src') || '') !== nextUrl) return;
          stage.classList.add('ax-photo-preview-active');
          preview.style.opacity = '1';
          hideViewerImageLoader(seq);
          viewerImageMetric('previewDisplays');
        };
        preview.onload = onLoad;
        preview.onerror = () => { /* Keep the previously visible frame until the original is ready. */ };
        preview.src = entry.url;
        if (preview.complete && preview.naturalWidth > 0) Promise.resolve().then(onLoad);
      }
      // When the selected thumb is visible, upgrade it to the cached medium tier
      // in the background. The full original is independently queued at higher priority.
      const mediumUrl = String(item.preview_url || '');
      if (purpose === 'thumbnail-preview' && mediumUrl
          && normalizedViewerImageUrl(mediumUrl) !== normalizedViewerImageUrl(entry.url)) {
        requestViewerDecodedImage(mediumUrl, 86, 'medium-preview').then((mediumEntry) => {
          if (!mediumEntry || !isCurrent()) return;
          revealDecodedPreview(mediumEntry, 'medium-preview');
        }).catch(() => {});
      }
    };
    // Selected thumbnails bypass the full-resolution queue so a rapid browse
    // can reveal the newest photo even while large originals are decoding.
    const cachedThumbnail = getViewerDecodedImage(thumbnailUrl);
    if (cachedThumbnail) {
      revealDecodedPreview(cachedThumbnail, 'thumbnail-preview');
      return;
    }
    if (normalizedViewerImageUrl(thumbnailUrl) === normalizedViewerImageUrl(fullUrl)) {
      requestViewerDecodedImage(thumbnailUrl, 96, 'thumbnail-preview')
        .then((entry) => revealDecodedPreview(entry, 'thumbnail-preview'))
        .catch(() => {});
      return;
    }
    const thumbnailImage = new Image();
    state.viewerPreviewImage = thumbnailImage;
    thumbnailImage.decoding = 'async';
    thumbnailImage.loading = 'eager';
    let thumbnailFinalizing = false;
    const finishThumbnail = async () => {
      if (thumbnailFinalizing || state.viewerPreviewImage !== thumbnailImage) return;
      thumbnailFinalizing = true;
      try {
        if (typeof thumbnailImage.decode === 'function') await thumbnailImage.decode();
      } catch (_) {
        if (!(thumbnailImage.naturalWidth > 0) || !(thumbnailImage.naturalHeight > 0)) {
          state.viewerPreviewImage = null;
          return;
        }
      }
      if (state.viewerPreviewImage !== thumbnailImage || !isCurrent()) return;
      const entry = {
        key:normalizedViewerImageUrl(thumbnailUrl),
        url:thumbnailUrl,
        image:thumbnailImage,
        width:thumbnailImage.naturalWidth,
        height:thumbnailImage.naturalHeight,
        bytes:Math.max(4, thumbnailImage.naturalWidth * thumbnailImage.naturalHeight * 4),
        purpose:'thumbnail-preview',
        loadedAt:performance.now(),
        lastUsedAt:performance.now(),
      };
      cacheViewerDecodedImage(entry);
      state.viewerPreviewImage = null;
      revealDecodedPreview(entry, 'thumbnail-preview');
    };
    thumbnailImage.onload = finishThumbnail;
    thumbnailImage.onerror = () => {
      if (state.viewerPreviewImage === thumbnailImage) state.viewerPreviewImage = null;
    };
    thumbnailImage.src = thumbnailUrl;
    if (thumbnailImage.complete && thumbnailImage.naturalWidth > 0) Promise.resolve().then(finishThumbnail);
  }
  function finalizeViewerOriginal(seq, url, image) {
    if (seq !== state.viewerOriginalCommitSeq || !selectedViewerImageStillCurrent(state.viewerIndex, seq, url)
        || normalizedViewerImageUrl(image?.getAttribute('src') || '') !== normalizedViewerImageUrl(url)) {
      viewerImageMetric('staleResults'); return;
    }
    if (state.viewerOriginalVisibleSeq === seq) return;
    if (state.viewerSetFitOnLoad) { state.viewerSetFitOnLoad = false; fitViewerImage(); }
    else {
      const wasFit = state.viewerTargetZoom <= state.viewerFitScale + 0.02;
      state.viewerFitScale = computeViewerFitScale();
      if (wasFit) {
        state.viewerZoom = state.viewerTargetZoom = state.viewerFitScale;
        state.viewerX = state.viewerTargetX = 0; state.viewerY = state.viewerTargetY = 0;
      }
      clampViewerPan(); setViewerTransform();
    }
    state.viewerOriginalVisibleSeq = seq;
    const latency = Math.max(0, performance.now() - Number(state.viewerLastSelectionAt || performance.now()));
    viewerImageMetric('originalDisplayMsTotal', latency);
    state.viewerImageMetrics.originalDisplayMsLast = latency;
    hideViewerPreview(seq);
    hideViewerImageLoader(seq);
  }
  function commitViewerOriginal(item, index, url, seq, entry) {
    if (!entry || !selectedViewerImageStillCurrent(index, seq, url)) { if (entry) viewerImageMetric('staleResults'); return; }
    const image = el('ax-photos-lightbox-img');
    if (!image) return;
    state.viewerOriginalCommitSeq = seq;
    state.viewerCurrentOriginalUrl = normalizedViewerImageUrl(url);
    image.alt = String(item.filename || `Photo ${item.message_id || index + 1}`);
    const source = image.getAttribute('src') || '';
    if (normalizedViewerImageUrl(source) !== normalizedViewerImageUrl(url)) image.src = url;
    if (image.complete && image.naturalWidth > 0 && normalizedViewerImageUrl(image.getAttribute('src') || '') === normalizedViewerImageUrl(url)) {
      Promise.resolve().then(() => finalizeViewerOriginal(seq, url, image));
    }
  }
  function buildViewerImageLoadCandidates(items, index, direction, ahead = VIEWER_IMAGE_PREFETCH_AHEAD) {
    const step = Number(direction) < 0 ? -1 : 1;
    const candidates = [];
    if (items[index]?.image_url) candidates.push({ index, priority:100, purpose:'selected' });
    const lookAhead = Math.max(0, Math.trunc(Number(ahead) || 0));
    for (let offset = 1; offset <= lookAhead; offset += 1) {
      const candidateIndex = index + step * offset;
      if (candidateIndex < 0 || candidateIndex >= items.length || !items[candidateIndex]?.image_url) continue;
      candidates.push({
        index:candidateIndex,
        priority:82 - Math.floor((offset - 1) / 2),
        purpose:offset <= 2 ? 'neighbor-ahead' : 'lookahead-prefetch',
      });
    }
    const behindIndex = index - step;
    if (behindIndex >= 0 && behindIndex < items.length && items[behindIndex]?.image_url) {
      candidates.push({ index:behindIndex, priority:42, purpose:'neighbor-behind' });
    }
    return candidates;
  }
  function scheduleViewerImageLoads(index, direction, seq, currentEntry = null) {
    const items = viewerCollection();
    const item = items[index];
    const fullUrl = String(item?.image_url || item?.preview_url || item?.thumbnail_url || '');
    if (!item || !fullUrl) return;
    const candidates = buildViewerImageLoadCandidates(items, index, direction);
    const priorities = new Map();
    candidates.forEach((candidate) => priorities.set(normalizedViewerImageUrl(items[candidate.index].image_url), candidate.priority));
    const activeThumbnailUrl = String(item.thumbnail_url || item.preview_url || '');
    if (activeThumbnailUrl && normalizedViewerImageUrl(activeThumbnailUrl) !== normalizedViewerImageUrl(fullUrl)) {
      priorities.set(normalizedViewerImageUrl(activeThumbnailUrl), 96);
    }
    cancelObsoleteQueuedViewerImages(priorities);
    let currentPromise;
    if (currentEntry) currentPromise = Promise.resolve(currentEntry);
    else currentPromise = requestViewerDecodedImage(fullUrl, 100, 'selected');
    currentPromise.then((entry) => {
      if (!entry) { hideViewerImageLoader(seq); return; }
      if (!selectedViewerImageStillCurrent(index, seq, fullUrl)) { viewerImageMetric('staleResults'); return; }
      commitViewerOriginal(item, index, fullUrl, seq, entry);
    }).catch(() => {});
    candidates.slice(1).forEach((candidate) => {
      const neighbor = items[candidate.index];
      requestViewerDecodedImage(String(neighbor.image_url || ''), candidate.priority, candidate.purpose).catch(() => {});
    });
  }
  window.axPhotosViewerDiagnostics = function axPhotosViewerDiagnostics() {
    const metrics = state.viewerImageMetrics || {};
    const hits = Number(metrics.cacheHits || 0), misses = Number(metrics.cacheMisses || 0);
    return { ...metrics, cacheEntries:state.viewerImageCache.size, cacheBytes:state.viewerImageCacheBytes,
      cacheBudgetBytes:state.viewerImageCacheBudgetBytes, cacheBudgetMiB:Math.round(state.viewerImageCacheBudgetBytes / (1024*1024)),
      queueDepth:state.viewerImageQueue.length, inflight:state.viewerImageInflight.size, running:state.viewerImageQueueRunning,
      cacheHitRatio:(hits + misses) ? hits / (hits + misses) : 0 };
  };

  function playViewerTick(frequency = 1200, duration = 0.018) {
    if (!state.viewerSoundEnabled) return;
    try {
      const AudioCtx = window.AudioContext || window.webkitAudioContext;
      if (!AudioCtx) return;
      if (!state.viewerAudioContext) state.viewerAudioContext = new AudioCtx();
      const ctx = state.viewerAudioContext;
      if (ctx.state === 'suspended') ctx.resume();
      const oscillator = ctx.createOscillator(), gain = ctx.createGain();
      oscillator.type = 'triangle'; oscillator.frequency.setValueAtTime(frequency, ctx.currentTime);
      gain.gain.setValueAtTime(0.035, ctx.currentTime); gain.gain.exponentialRampToValueAtTime(0.0001, ctx.currentTime + duration);
      oscillator.connect(gain); gain.connect(ctx.destination); oscillator.start(); oscillator.stop(ctx.currentTime + duration);
    } catch (_) { /* Audio feedback is optional. */ }
  }

  // ------------------------------------------------------------------
  // SECTION: Viewer: filmstrip and navigation
  // Filmstrip window and moving between photos.
  // ------------------------------------------------------------------
  function viewerFilmstripCardHtml(item, index) {
    if (!item || index < 0 || index >= viewerCollection().length) return '<span class="ax2-photo-filmstrip-empty" aria-hidden="true"></span>';
    const title = String(item.filename || `Photo ${item.message_id || index + 1}`);
    const src = String(item.thumbnail_url || item.preview_url || item.image_url || '');
    const favorite = state.favorites.has(photoFavoriteKey(item));
    return `<button type="button" class="ax2-photo-filmstrip-item${index === state.viewerIndex ? ' active' : ''}" data-viewer-index="${index}" title="${escape(title)}" aria-label="Open ${escape(title)}" aria-current="${index === state.viewerIndex ? 'true' : 'false'}"><img src="${escape(src)}" alt="" loading="eager" decoding="async">${favorite ? '<span class="ax2-filmstrip-favorite" aria-hidden="true">★</span>' : ''}</button>`;
  }
  function renderViewerFilmstrip(previousIndex = null, force = true) {
    const strip = el('ax-photos-viewer-filmstrip');
    if (!strip) return;
    const items = viewerCollection();
    const size = Number(state.viewerStripWindowSize || 24);
    const maxStart = Math.max(0, items.length - size);
    // Keep a full, scrollable virtual window. Near the ends the selected thumb
    // may sit off-center, avoiding empty slots that made wheel scrolling inert.
    const start = Math.min(maxStart, Math.max(0, state.viewerIndex - Math.floor((size - 1) / 2)));
    const oldStart = Number(state.viewerStripStart);
    const delta = Number.isInteger(previousIndex) ? state.viewerIndex - previousIndex : 0;
    const canShift = !force && strip.children.length === size && Number.isFinite(oldStart)
      && Math.abs(delta) === 1 && start === oldStart + delta;
    if (canShift && delta > 0) {
      strip.firstElementChild?.remove();
      strip.insertAdjacentHTML('beforeend', viewerFilmstripCardHtml(items[start + size - 1], start + size - 1));
    } else if (canShift && delta < 0) {
      strip.lastElementChild?.remove();
      strip.insertAdjacentHTML('afterbegin', viewerFilmstripCardHtml(items[start], start));
    } else if (force || strip.children.length !== size || start !== oldStart) {
      let markup = '';
      for (let slot = 0; slot < size; slot += 1) {
        const index = start + slot;
        markup += viewerFilmstripCardHtml(items[index], index);
      }
      strip.innerHTML = markup;
    }
    state.viewerStripStart = start;
    strip.dataset.windowStart = String(start);
    strip.dataset.windowSize = String(size);
    centerViewerThumbnail(false);
    const loading = el('ax-photos-viewer-loading'); if (loading) loading.hidden = !state.viewerItemsLoading;
    const prev = el('ax-photos-viewer-prev'), next = el('ax-photos-viewer-next');
    if (prev) prev.disabled = state.viewerIndex <= 0;
    if (next) next.disabled = state.viewerIndex >= items.length - 1 && (state.viewerItemsComplete || items.length >= state.total);
    updateViewerHud(currentViewerItem());
  }
  function centerViewerThumbnail() {
    const strip = el('ax-photos-viewer-filmstrip');
    if (!strip) return;
    Array.from(strip.children).forEach((node, slot) => {
      const hasIndex = node.hasAttribute('data-viewer-index');
      const index = hasIndex ? Number(node.dataset.viewerIndex) : -1;
      const active = hasIndex && Number.isInteger(index) && index === state.viewerIndex;
      node.classList.toggle('active', active);
      if (hasIndex) node.setAttribute('aria-current', active ? 'true' : 'false');
      node.dataset.viewerSlot = String(slot);
    });
  }
  function shiftViewerFilmstripWindow(delta) {
    const strip = el('ax-photos-viewer-filmstrip');
    if (!strip || !Number.isFinite(delta) || !delta) return false;
    const items = viewerCollection();
    const size = Number(state.viewerStripWindowSize || 24);
    const maxStart = Math.max(0, items.length - size);
    const current = Number.isFinite(Number(state.viewerStripStart))
      ? Number(state.viewerStripStart)
      : Math.min(maxStart, Math.max(0, state.viewerIndex - Math.floor((size - 1) / 2)));
    const next = Math.min(maxStart, Math.max(0, current + Math.trunc(delta)));
    if (next === current) return false;
    let markup = '';
    for (let slot = 0; slot < size; slot += 1) {
      const index = next + slot;
      markup += viewerFilmstripCardHtml(items[index], index);
    }
    strip.innerHTML = markup;
    state.viewerStripStart = next;
    strip.dataset.windowStart = String(next);
    strip.dataset.windowSize = String(size);
    centerViewerThumbnail();
    return true;
  }
  function showViewerItem(index, direction = '') {
    const items = viewerCollection();
    const item = items[index];
    if (!item) return false;
    const previousIndex = state.viewerIndex;
    state.viewerIndex = index;
    const image = el('ax-photos-lightbox-img');
    if (!image) return false;
    const url = String(item.image_url || item.preview_url || item.thumbnail_url || '');
    const normalizedUrl = normalizedViewerImageUrl(url);
    const changedSelection = previousIndex !== index || state.viewerCurrentTargetUrl !== normalizedUrl;
    const seq = ++state.viewerImageLoadSeq;
    state.viewerLastSelectionAt = performance.now();
    state.viewerCurrentTargetUrl = normalizedUrl;
    state.viewerOriginalVisibleSeq = 0;
    viewerImageMetric('selections');
    const previousDirection = direction === 'left' ? -1 : (direction === 'right' ? 1 : (index < previousIndex ? -1 : 1));
    if (changedSelection) {
      stopViewerTransformAnimation();
      state.viewerRotation = 0;
      state.viewerZoom = state.viewerTargetZoom = 1;
      state.viewerFitScale = 1;
      state.viewerX = state.viewerTargetX = 0; state.viewerY = state.viewerTargetY = 0;
      state.viewerSetFitOnLoad = true;
      image.classList.remove('ax-photo-viewer-slide-in-right', 'ax-photo-viewer-slide-in-left', 'ax-photo-viewer-image-changing');
    }
    image.alt = String(item.filename || `Photo ${item.message_id || index + 1}`);
    const cached = getViewerDecodedImage(url);
    const displayedUrl = normalizedViewerImageUrl(image.getAttribute('src') || '');
    const originalAlreadyVisible = image.complete && image.naturalWidth > 0 && displayedUrl === normalizedUrl;
    if (cached || originalAlreadyVisible || !url.trim()) hideViewerImageLoader(seq);
    else showViewerImageLoader(seq);
    if (cached) {
      if (originalAlreadyVisible) hideViewerPreview(seq);
      else showViewerPreview(item, index, seq);
      commitViewerOriginal(item, index, url, seq, cached);
      scheduleViewerImageLoads(index, previousDirection, seq, cached);
    } else {
      showViewerPreview(item, index, seq);
      scheduleViewerImageLoads(index, previousDirection, seq, null);
    }
    renderViewerFilmstrip(previousIndex, !Number.isInteger(previousIndex) || previousIndex < 0 || !el('ax-photos-viewer-filmstrip')?.children.length);
    return true;
  }

  async function loadViewerCollection(anchorItem) {
    const requestId = ++state.viewerItemsLoadSeq;
    const anchorKey = photoIdentity(anchorItem || currentViewerItem());
    state.viewerItemsLoading = true;
    renderViewerFilmstrip();
    try {
      const all = [];
      const expected = Math.max(0, Number(state.total || 0));
      const per = 1000;
      for (let page = 0; page === 0 || all.length < expected; page++) {
        if (requestId !== state.viewerItemsLoadSeq) return;
        const params = photoQueryParams(page, per);
        const result = await api('/api/photos?' + params.toString(), { cache:'no-store' });
        if (!result || result._ok === false || result.ok !== true) throw new Error(result?.detail || 'Could not load the complete viewer filmstrip.');
        const batch = Array.isArray(result.items) ? result.items : [];
        all.push(...batch);
        if (page === 0 && Number.isFinite(Number(result.total))) state.viewerItemsExpected = Number(result.total);
        const total = Number(result.total || expected);
        if (!batch.length || all.length >= total) break;
        if (page > 1000) break;
      }
      if (requestId !== state.viewerItemsLoadSeq || !el('ax-photos-lightbox')?.open) return;
      const key = anchorKey || photoIdentity(currentViewerItem());
      state.viewerItems = all.length ? all : state.items.slice();
      state.viewerItemsComplete = all.length >= Number(state.viewerItemsExpected ?? state.total);
      const matchIndex = key ? state.viewerItems.findIndex((item) => photoIdentity(item) === key) : -1;
      if (matchIndex >= 0) state.viewerIndex = matchIndex;
      state.viewerItemsLoading = false;
      renderViewerFilmstrip();
      scheduleViewerImageLoads(state.viewerIndex, 1, state.viewerImageLoadSeq);
    } catch (error) {
      if (requestId !== state.viewerItemsLoadSeq) return;
      state.viewerItemsLoading = false;
      state.viewerItemsComplete = false;
      renderViewerFilmstrip();
      const counter = el('ax-photos-viewer-counter');
      if (counter) counter.title = error?.message || 'Only currently loaded photos are available in the filmstrip.';
    }
  }
  window.axPhotosOpenImageAt = function axPhotosOpenImageAt(index) {
    const dialog = el('ax-photos-lightbox'), image = el('ax-photos-lightbox-img');
    const numericIndex = Number(index);
    if (!dialog || !image || !Number.isInteger(numericIndex) || !state.items[numericIndex]) return;
    const selectedItem = state.items[numericIndex];
    const selectedKey = photoIdentity(selectedItem);
    const alreadyLoadedIndex = state.viewerItems.findIndex((item) => photoIdentity(item) === selectedKey);
    if (state.viewerItemsComplete && alreadyLoadedIndex >= 0) state.viewerIndex = alreadyLoadedIndex;
    else { state.viewerItems = state.items.slice(); state.viewerItemsComplete = false; state.viewerIndex = numericIndex; }
    const source = el('ax-photos-grid')?.querySelector(`[data-photo-index="${numericIndex}"] .ax2-photo-card-image img`);
    state.viewerSourceImage = source?.isConnected ? source : null;
    const openDialog = () => { showViewerItem(state.viewerIndex); if (!dialog.open) dialog.showModal(); };
    const startTransition = document.startViewTransition;
    if (!dialog.open && source?.isConnected && typeof startTransition === 'function' && !prefersReducedPhotoMotion()) {
      source.style.viewTransitionName = 'ax-photo-shared';
      document.documentElement.classList.add('ax-photo-vt-in-progress');
      const transition = startTransition.call(document, () => {
        source.style.viewTransitionName = 'none';
        image.style.viewTransitionName = 'ax-photo-shared';
        openDialog();
      });
      Promise.resolve(transition?.finished).catch(() => {}).finally(() => {
        image.style.viewTransitionName = 'none';
        if (source?.isConnected) source.style.viewTransitionName = '';
        document.documentElement.classList.remove('ax-photo-vt-in-progress');
      });
    } else openDialog();
    state.viewerCollectionPromise = loadViewerCollection(selectedItem);
  };
  // Compatibility for any older UI code still using URL/title arguments.
  window.axPhotosOpenImage = function axPhotosOpenImage(url, title) {
    const found = state.items.findIndex((item) => String(item.image_url || '') === String(url || '') && String(item.filename || '') === String(title || ''));
    if (found >= 0) return window.axPhotosOpenImageAt(found);
    const dialog = el('ax-photos-lightbox'), image = el('ax-photos-lightbox-img');
    if (!dialog || !image) { window.open(String(url || ''), '_blank', 'noopener'); return; }
    state.viewerItems = [{ image_url:String(url || ''), thumbnail_url:String(url || ''), filename:String(title || 'Photo') }];
    state.viewerItemsComplete = true; state.total = 1; state.viewerIndex = 0; state.viewerSourceImage = null;
    showViewerItem(0); if (!dialog.open) dialog.showModal();
  };
  window.axPhotosViewerStep = async function axPhotosViewerStep(direction) {
    const delta = Number(direction) < 0 ? -1 : 1;
    let items = viewerCollection();
    let target = state.viewerIndex + delta;
    if (target < 0) return;
    if (target >= items.length && !state.viewerItemsComplete && state.viewerCollectionPromise) {
      await state.viewerCollectionPromise;
      items = viewerCollection(); target = state.viewerIndex + delta;
    }
    if (target >= 0 && target < items.length) {
      playViewerTick(delta > 0 ? 1300 : 1050);
      showViewerItem(target, delta > 0 ? 'right' : 'left');
      return;
    }
    // A manual step stops at the end; slideshow playback wraps around.
  };
  // Zoom buttons change magnification incrementally; Fit is the only action that resets.
  window.axPhotosViewerZoom = function axPhotosViewerZoom(direction = 1) {
    const factor = Number(direction) < 0 ? (1 / 1.25) : 1.25;
    const base = state.viewerTargetZoom || state.viewerZoom || state.viewerFitScale || 1;
    zoomViewerToScale(base * factor);
  };
  window.axPhotosViewerSetZoom = function axPhotosViewerSetZoom(scale) { zoomViewerToScale(Number(scale)); };
  window.axPhotosViewerFit = function axPhotosViewerFit() { fitViewerImage(); };
  window.axPhotosViewerRotate = function axPhotosViewerRotate(degrees) {
    const image = el('ax-photos-lightbox-img');
    stopViewerTransformAnimation();
    state.viewerRotation = ((state.viewerRotation + Number(degrees || 0)) % 360 + 360) % 360;
    state.viewerFitScale = computeViewerFitScale();
    state.viewerZoom = state.viewerTargetZoom = state.viewerFitScale;
    state.viewerX = state.viewerTargetX = 0; state.viewerY = state.viewerTargetY = 0;
    if (image) { image.classList.add('ax-photo-viewer-rotating'); window.setTimeout(() => image.classList.remove('ax-photo-viewer-rotating'), 320); }
    setViewerTransform(); playViewerTick(state.viewerRotation ? 1080 : 900, 0.025);
  };
  function stopViewerSlideshow() {
    if (state.viewerSlideInterval) window.clearInterval(state.viewerSlideInterval);
    state.viewerSlideInterval = null;
    const button = el('ax-photos-viewer-play'), icon = el('ax-photos-viewer-play-icon');
    if (button) { button.classList.remove('is-playing'); button.title = 'Play slideshow (Space)'; button.setAttribute('aria-label', 'Play slideshow'); }
    if (icon) icon.textContent = '▶';
  }
  window.axPhotosViewerToggleSlideshow = function axPhotosViewerToggleSlideshow() {
    if (state.viewerSlideInterval) { stopViewerSlideshow(); return; }
    const button = el('ax-photos-viewer-play'), icon = el('ax-photos-viewer-play-icon');
    if (button) { button.classList.add('is-playing'); button.title = 'Pause slideshow (Space)'; button.setAttribute('aria-label', 'Pause slideshow'); }
    if (icon) icon.textContent = 'Ⅱ';
    state.viewerSlideInterval = window.setInterval(async () => {
      const items = viewerCollection();
      if (!items.length) return;
      if (state.viewerIndex >= items.length - 1) showViewerItem(0, 'right');
      else await window.axPhotosViewerStep(1);
    }, 3000);
  };
  window.axPhotosViewerToggleFavorite = function axPhotosViewerToggleFavorite() {
    const item = currentViewerItem(); if (!item) return;
    const key = photoFavoriteKey(item); if (!key) return;
    if (state.favorites.has(key)) state.favorites.delete(key); else state.favorites.add(key);
    persistFavorites(); updateViewerFavoriteButtons(item); renderViewerFilmstrip(); refreshFavoriteBadges();
    const filter = el('ax-photos-favorites-filter');
    if (filter) { filter.classList.toggle('is-active', state.favoritesOnly); filter.setAttribute('aria-pressed', state.favoritesOnly ? 'true' : 'false'); }
    if (state.favoritesOnly) state.refreshAfterViewerClose = true;
    playViewerTick(1500, 0.025);
  };
  function updateViewerSoundButton() {
    const button = el('ax-photos-viewer-sound');
    if (!button) return;
    const mutedIcon = '<svg viewBox="0 0 24 24" aria-hidden="true" focusable="false"><path d="M4 9v6h4l5 4V5L8 9H4Z"/><path d="m17 9 4 6m0-6-4 6"/></svg>';
    const soundIcon = '<svg viewBox="0 0 24 24" aria-hidden="true" focusable="false"><path d="M4 9v6h4l5 4V5L8 9H4Z"/><path d="M16 9a5 5 0 0 1 0 6m2-9a9 9 0 0 1 0 12"/></svg>';
    button.innerHTML = state.viewerSoundEnabled ? soundIcon : mutedIcon;
    button.classList.toggle('is-active', state.viewerSoundEnabled);
    button.setAttribute('aria-pressed', state.viewerSoundEnabled ? 'true' : 'false');
    button.setAttribute('aria-label', state.viewerSoundEnabled ? 'Mute filmstrip sound' : 'Enable filmstrip sound');
    button.title = state.viewerSoundEnabled ? 'Mute filmstrip tick sounds' : 'Enable filmstrip tick sounds';
  }
  window.axPhotosViewerToggleSound = function axPhotosViewerToggleSound() {
    state.viewerSoundEnabled = !state.viewerSoundEnabled;
    updateViewerSoundButton();
    if (state.viewerSoundEnabled) playViewerTick(1400, 0.03);
  };
  window.axPhotosViewerToggleFullscreen = function axPhotosViewerToggleFullscreen() {
    const dialog = el('ax-photos-lightbox');
    if (document.fullscreenElement) document.exitFullscreen?.().catch?.(() => {});
    else if (dialog?.requestFullscreen) dialog.requestFullscreen().catch(() => {});
  };
  window.axPhotosCloseLightbox = function axPhotosCloseLightbox() {
    const dialog = el('ax-photos-lightbox'), image = el('ax-photos-lightbox-img');
    if (document.fullscreenElement && (!dialog || document.fullscreenElement === dialog)) document.exitFullscreen?.().catch?.(() => {});
    stopViewerSlideshow(); stopViewerTransformAnimation(); state.viewerItemsLoadSeq += 1;
    state.viewerImageLoadSeq += 1;
    cancelObsoleteQueuedViewerImages(new Map());
    hideViewerPreview(state.viewerImageLoadSeq);
    const source = state.viewerSourceImage;
    const resetState = () => {
      if (dialog?.open) dialog.close();
      if (image) { image.removeAttribute('src'); image.style.transform = ''; image.style.viewTransitionName = 'none'; }
      const preview = el('ax-photos-lightbox-preview'); if (preview) preview.style.opacity = '0';
      el('ax-photos-viewer-stage')?.classList.remove('ax-photo-preview-active');
      state.viewerIndex = -1; state.viewerItems = []; state.viewerItemsComplete = false; state.viewerItemsLoading = false;
      state.viewerStripStart = null; state.viewerCurrentTargetUrl = ''; state.viewerCurrentOriginalUrl = ''; state.viewerOriginalCommitSeq = 0; state.viewerOriginalVisibleSeq = 0;
      state.viewerZoom = state.viewerTargetZoom = 1; state.viewerFitScale = 1; state.viewerRotation = 0; state.viewerX = state.viewerTargetX = 0; state.viewerY = state.viewerTargetY = 0; state.viewerPointers.clear(); state.viewerGesture = null;
      lastViewerTouchTap = null;
      el('ax-photos-viewer-stage')?.classList.remove('is-panning', 'is-pinch-zooming', 'is-zoomed', 'is-pannable');
    };
    const finishClose = () => {
      state.viewerSourceImage = null;
      if (state.refreshAfterViewerClose) { state.refreshAfterViewerClose = false; loadGrid(true); }
    };
    const startTransition = document.startViewTransition;
    if (dialog?.open && image && source?.isConnected && typeof startTransition === 'function' && !prefersReducedPhotoMotion()) {
      image.style.viewTransitionName = 'ax-photo-shared';
      document.documentElement.classList.add('ax-photo-vt-in-progress');
      const transition = startTransition.call(document, () => {
        image.style.viewTransitionName = 'none';
        source.style.viewTransitionName = 'ax-photo-shared';
        resetState();
      });
      Promise.resolve(transition?.finished).catch(() => {}).finally(() => {
        if (source?.isConnected) source.style.viewTransitionName = '';
        image.style.viewTransitionName = 'none';
        document.documentElement.classList.remove('ax-photo-vt-in-progress');
        finishClose();
      });
    } else {
      resetState();
      if (source?.isConnected) source.style.viewTransitionName = '';
      finishClose();
    }
  };
  const viewerImageElement = el('ax-photos-lightbox-img');
  if (viewerImageElement) {
    viewerImageElement.draggable = false;
    viewerImageElement.addEventListener('dragstart', (event) => event.preventDefault());
    viewerImageElement.addEventListener('load', () => {
    if (!el('ax-photos-lightbox')?.open) return;
    const seq = state.viewerOriginalCommitSeq;
    const loadedUrl = normalizedViewerImageUrl(viewerImageElement.getAttribute('src') || '');
    if (!seq || seq !== state.viewerImageLoadSeq || loadedUrl !== state.viewerCurrentTargetUrl || loadedUrl !== state.viewerCurrentOriginalUrl) {
      viewerImageMetric('staleResults');
      return;
    }
    finalizeViewerOriginal(seq, loadedUrl, viewerImageElement);
    });
  }
  // Sound feedback is intentionally session-opt-in: always start muted on page load.
  state.viewerSoundEnabled = false;
  updateViewerSoundButton();
  const favoritesFilterButton = el('ax-photos-favorites-filter');
  if (favoritesFilterButton) { favoritesFilterButton.classList.toggle('is-active', state.favoritesOnly); favoritesFilterButton.setAttribute('aria-pressed', state.favoritesOnly ? 'true' : 'false'); }
  window.addEventListener('resize', () => {
    if (!el('ax-photos-lightbox')?.open) return;
    fitViewerImage();
  });
  document.addEventListener('fullscreenchange', () => {
    const button = el('ax-photos-viewer-fullscreen');
    if (button) { button.classList.toggle('is-active', Boolean(document.fullscreenElement)); button.title = document.fullscreenElement ? 'Exit fullscreen (F)' : 'Toggle fullscreen (F)'; }
  });

  async function watchJob(jobId, kind, destinationChannelId = '') {
    if (kind === 'face') { state.faceScanWatching = true; const button = el('ax-photos-analyze-faces-btn'); if (button) button.disabled = true; }
    for (let attempt = 0; attempt < 7200; attempt++) {
      await new Promise((resolve) => setTimeout(resolve, 2000));
      let result;
      try { result = await api(`/api/photos/scan-status/${encodeURIComponent(jobId)}`, { cache: 'no-store' }); }
      catch (_) { continue; }
      if (!result || result._ok === false) continue;
      const jobStatus = String(result.status || '').toUpperCase();
      const progress = Number(result.progress || 0);
      const current = String(result.current_item || '').trim();
      if (kind === 'face') faceStatus(`Image-based face analysis ${jobStatus.toLowerCase().replaceAll('_',' ')} · ${progress.toFixed(0)}%${current ? ` · ${current}` : ''}`);
      else uploadStatus(`Photo upload ${jobStatus.toLowerCase().replaceAll('_',' ')} · ${progress.toFixed(0)}%${current ? ` · ${current}` : ''}`);
      if (['COMPLETED','COMPLETED_WITH_ERRORS','FAILED','CANCELLED','INTERRUPTED'].includes(jobStatus)) {
        if (kind === 'face') {
          state.faceScanWatching = false;
          const success = jobStatus === 'COMPLETED' || jobStatus === 'COMPLETED_WITH_ERRORS';
          faceStatus(success ? 'Face analysis complete. Refreshing visual groups…' : `Face analysis ${jobStatus.toLowerCase().replaceAll('_',' ')}. Check Operations for details; failed images can be retried.`, success ? 'success' : 'error');
          await window.axPhotosLoadFaceGroups(true);
          await window.axPhotosRefreshLibrary({ force: true, quiet: true });
          return;
        }
        // Scanned channel messages and verified local uploads share the same
        // photo_library_items table. After upload completion, select the chosen
        // destination so the new items are immediately visible in that library.
        if (destinationChannelId) {
          state.faceGroupId = null; state.faceGroupIds = []; state.facePerformerId = null; if (el('ax-photos-group-filter')) el('ax-photos-group-filter').hidden = true;
          state.selected.clear();
          state.selected.add(String(destinationChannelId));
          state.page = 0;
          state.items = [];
          renderChannels();
        }
        const uploadSuccess = jobStatus === 'COMPLETED';
        const uploadPartial = jobStatus === 'COMPLETED_WITH_ERRORS';
        uploadStatus(uploadSuccess
          ? 'Upload finished. Verified documents are indexed in the same Photos library as scanned Telegram images. Showing the destination channel…'
          : uploadPartial
            ? 'Upload finished with some file errors. Successfully verified documents remain in the Photos library; see Operations for details.'
            : `Photo upload ${jobStatus.toLowerCase().replaceAll('_',' ')}${result.error ? `: ${result.error}` : ''}. Open Operations for per-file details.`,
          uploadSuccess ? 'success' : 'error');
        await window.axPhotosRefreshLibrary({ force: true, quiet: true });
        return;
      }
    }
    if (kind === 'face') faceStatus('Face analysis is still running. You can leave this page; progress remains visible in Operations.');
    else uploadStatus('Photo upload is still running. You can leave this page; progress remains visible in Operations.');
  }

  let activePhotoMenuCard = null;
  let activePhotoMenuPortal = null;
  let activePhotoMenuTrigger = null;
  function closePhotoCardMenus(options = {}) {
    const restoreFocus = options?.restoreFocus === true;
    const owner = activePhotoMenuCard, trigger = activePhotoMenuTrigger;
    activePhotoMenuPortal?.remove();
    activePhotoMenuCard = null;
    activePhotoMenuPortal = null;
    activePhotoMenuTrigger = null;
    document.querySelectorAll('#ax-photos-grid [data-photo-card].has-menu-open').forEach((card) => {
      card.classList.remove('has-menu-open');
      card.querySelector('[data-photo-menu-trigger]')?.setAttribute('aria-expanded', 'false');
    });
    if (owner && trigger) {
      owner.classList.remove('has-menu-open');
      trigger.setAttribute('aria-expanded', 'false');
    }
    if (restoreFocus && trigger?.isConnected) trigger.focus({ preventScroll: true });
  }

  function positionPhotoCardMenu(menu, trigger, card) {
    const photo = card.querySelector('.ax2-photo-card-image');
    const photoRect = photo?.getBoundingClientRect() || trigger.getBoundingClientRect();
    const triggerRect = trigger.getBoundingClientRect();
    const menuRect = menu.getBoundingClientRect();
    const margin = 12, gap = 8;
    const viewportWidth = document.documentElement.clientWidth || window.innerWidth;
    const viewportHeight = window.innerHeight || document.documentElement.clientHeight;
    const below = photoRect.bottom + gap;
    const above = photoRect.top - menuRect.height - gap;
    let top;
    if (below + menuRect.height <= viewportHeight - margin) top = below;
    else if (above >= margin) top = above;
    else top = Math.max(margin, Math.min(triggerRect.bottom - menuRect.height, viewportHeight - menuRect.height - margin));
    const left = Math.max(margin, Math.min(triggerRect.right - menuRect.width, viewportWidth - menuRect.width - margin));
    menu.style.setProperty('--ax2-photo-menu-top', `${Math.round(top)}px`);
    menu.style.setProperty('--ax2-photo-menu-left', `${Math.round(left)}px`);
  }

  async function handlePhotoCardAction(card, action) {
    const channelId = String(card?.getAttribute('data-photo-channel-id') || '').trim();
    const messageId = Number(card?.getAttribute('data-photo-message-id') || 0);
    const index = Number(card?.getAttribute('data-photo-index') || 0);
    const item = state.items.find((photo) => String(photo.channel_id || '') === channelId && Number(photo.message_id || 0) === messageId);
    const filename = String(item?.filename || `Photo ${messageId}`);
    const channelTitle = String(item?.channel_title || state.channels.find((ch) => String(ch.telegram_id) === channelId)?.title || channelId);
    closePhotoCardMenus();

    if (action === 'open') {
      if (Number.isFinite(index) && index >= 0) window.axPhotosOpenImageAt(index);
      return;
    }
    if (action === 'copy-name') {
      try {
        if (navigator.clipboard?.writeText) await navigator.clipboard.writeText(filename);
        else { window.prompt('Copy photo filename:', filename); }
        status(`Filename copied: ${filename}`, 'success');
      } catch (error) {
        window.prompt('Copy photo filename:', filename);
      }
      return;
    }
    if (action === 'different-person') {
      const photoId = Number(card?.getAttribute('data-photo-id') || item?.id || 0);
      const activeGroup = state.faceGroups.find((group) => Number(group.id) === Number(state.faceGroupId));
      const groupIds = (activeGroup?.cluster_ids?.length ? activeGroup.cluster_ids : (state.faceGroupIds.length ? state.faceGroupIds : (Number(state.faceGroupId) > 0 ? [Number(state.faceGroupId)] : [])))
        .map(Number).filter((value) => Number.isInteger(value) && value > 0);
      if (!photoId || !groupIds.length) {
        status('Open a face-group gallery first, then use Different person on the incorrect photo.', 'error');
        return;
      }
      const confirmed = window.confirm(`Remove this photo's face detection(s) from the current group?\n\nArchive X will move those detections into new unassigned group(s). The photo and its Telegram message will not be deleted.`);
      if (!confirmed) return;
      try {
        status(`Separating ${filename} from the current face group…`);
        const result = await api('/api/photos/faces/different-person', { method:'POST', body:JSON.stringify({ photo_id:photoId, group_ids:groupIds }) });
        if (!result || result._ok === false || result.ok !== true) throw new Error(result?.detail || 'Could not separate this photo from the face group.');
        state.page = 0; state.items = [];
        await loadGrid(true);
        await window.axPhotosLoadFaceGroups(true);
        status(result.message || 'Moved the face detection(s) into new unassigned group(s).', 'success');
      } catch (error) {
        status(error?.message || 'Could not correct this face group.', 'error');
        window.alert(error?.message || 'Could not correct this face group.');
      }
      return;
    }
    if (action !== 'archive-only' && action !== 'archive-and-telegram') return;

    const remote = action === 'archive-and-telegram';
    const promptText = remote
      ? `Permanently delete "${filename}" from Archive X and its Telegram message in "${channelTitle}"?

This cannot be undone. Archive X will keep the cached image if Telegram deletion fails.`
      : `Remove "${filename}" from the Archive X Photos library and delete its local cached copies?

The Telegram message will remain and may be indexed again if its channel is scanned in the future.`;
    if (!window.confirm(promptText)) return;

    try {
      status(remote ? `Deleting ${filename} from Telegram and Archive X…` : `Removing ${filename} from Archive X…`);
      const result = await api(`/api/photos/${encodeURIComponent(channelId)}/${messageId}/delete`, {
        method: 'POST', body: JSON.stringify({ scope: remote ? 'archive_and_telegram' : 'archive_only' })
      });
      if (!result || result._ok === false || result.ok !== true) throw new Error(result?.detail || 'Photo deletion failed.');
      status(result.message || (remote ? 'Photo deleted from Archive X and Telegram.' : 'Photo removed from Archive X.'), result.cache_cleanup_warning ? 'error' : 'success');
      state.page = 0; state.items = [];
      await window.axPhotosRefreshLibrary({ force: true, quiet: true });
      if (state.activeTab === 'performers') await window.axPhotosLoadFaceGroups(true);
    } catch (error) {
      status(error?.message || 'Photo deletion failed. The library entry was retained.', 'error');
      window.alert(error?.message || 'Photo deletion failed. The library entry was retained.');
    }
  }

  document.addEventListener('click', async (event) => {
    const trigger = event.target.closest?.('[data-photo-menu-trigger]');
    if (trigger) {
      event.preventDefault(); event.stopPropagation();
      const card = trigger.closest('[data-photo-card]');
      const template = card?.querySelector('[data-photo-card-menu]');
      if (!card || !template) return;
      if (activePhotoMenuCard === card) { closePhotoCardMenus(); return; }
      closePhotoCardMenus();
      const menu = template.cloneNode(true);
      menu.classList.add('is-portaled');
      menu.hidden = false;
      document.body.appendChild(menu);
      activePhotoMenuCard = card;
      activePhotoMenuPortal = menu;
      activePhotoMenuTrigger = trigger;
      card.classList.add('has-menu-open');
      trigger.setAttribute('aria-expanded', 'true');
      positionPhotoCardMenu(menu, trigger, card);
      requestAnimationFrame(() => menu.querySelector('[role="menuitem"]')?.focus({ preventScroll: true }));
      return;
    }
    const action = event.target.closest?.('[data-photo-action]');
    if (action) {
      event.preventDefault(); event.stopPropagation();
      const actionMenu = action.closest('.ax2-photo-card-menu');
      const card = action.closest('[data-photo-card]') || (actionMenu === activePhotoMenuPortal ? activePhotoMenuCard : null);
      await handlePhotoCardAction(card, String(action.getAttribute('data-photo-action') || ''));
      return;
    }
    if (!event.target.closest?.('.ax2-photo-card-menu')) closePhotoCardMenus();
  });

  document.addEventListener('keydown', (event) => {
    if (event.key === 'Escape' && (activePhotoMenuPortal || document.querySelector('#ax-photos-grid .ax2-photo-card-menu:not([hidden])'))) {
      closePhotoCardMenus({ restoreFocus: true });
      event.stopImmediatePropagation();
      return;
    }
  }, true);
  window.addEventListener('scroll', () => { if (activePhotoMenuPortal) closePhotoCardMenus(); }, { capture: true, passive: true });
  window.addEventListener('resize', () => { if (activePhotoMenuPortal) closePhotoCardMenus(); }, { passive: true });

  // Keep the two photo sections keyboard accessible as a standard tablist.
  document.addEventListener('keydown', (event) => {
    const tab = event.target.closest?.('#ax-photos-tab-library, #ax-photos-tab-performers');
    if (!tab) return;
    if (event.key !== 'ArrowLeft' && event.key !== 'ArrowRight' && event.key !== 'Home' && event.key !== 'End') return;
    event.preventDefault();
    const next = event.key === 'Home' ? 'library' : event.key === 'End' ? 'performers'
      : (tab.id === 'ax-photos-tab-library' ? 'performers' : 'library');
    window.axPhotosSetTab(next);
    el(next === 'library' ? 'ax-photos-tab-library' : 'ax-photos-tab-performers')?.focus();
  });
  window.axPhotosSetTab('library');
  initPhotoInfiniteScroll();

  async function axPhotosConfirmFaceSuggestion(groupId, performerId, performerName, source, score, basis) {
    const gid = Number(groupId), pid = Number(performerId);
    const group = state.faceGroups.find((item) => Number(item.id) === gid);
    if (!gid || !pid || !group) { faceStatus('This suggestion is no longer available. Refresh Image Analysis.', 'error'); return; }
    const groupIds = (Array.isArray(group.cluster_ids) && group.cluster_ids.length ? group.cluster_ids : [gid]).map(Number).filter((value) => Number.isInteger(value) && value > 0);
    try {
      const result = await api(`/api/photos/faces/groups/${gid}/assign`, {
        method: 'POST', body: JSON.stringify({ performer_id: pid, group_ids: groupIds,
          suggestion_source: source, suggestion_score: score === '' || score == null ? null : Number(score),
          suggestion_reason: source === 'confirmed_assignment' ? 'Confirmed from a previously assigned visual group' : `Confirmed from source metadata: ${basis || 'text match'}` })
      });
      if (!result || result._ok === false || result.ok !== true) throw new Error(result?.detail || 'Could not confirm this suggestion.');
      state.faceGroupNames.set(gid, { id: pid, name: performerName, source: 'confirmed-suggestion', basis: source });
      faceStatus(`Confirmed ${performerName} for this face group. Future suggestions can learn from this confirmation.`, 'success');
      await window.axPhotosLoadFaceGroups(true);
    } catch (error) { faceStatus(error?.message || 'Could not confirm this suggestion.', 'error'); }
  }

  async function axPhotosRejectFaceSuggestion(groupId, performerId, source, score) {
    const gid = Number(groupId), pid = Number(performerId);
    const group = state.faceGroups.find((item) => Number(item.id) === gid);
    if (!gid || !pid || !group) { faceStatus('This suggestion is no longer available. Refresh Image Analysis.', 'error'); return; }
    const groupIds = (Array.isArray(group.cluster_ids) && group.cluster_ids.length ? group.cluster_ids : [gid]).map(Number).filter((value) => Number.isInteger(value) && value > 0);
    try {
      const result = await api('/api/photos/faces/suggestions/reject', {
        method:'POST', body:JSON.stringify({ group_ids:groupIds, performer_id:pid, source,
          similarity_score: score === '' || score == null ? null : Number(score), reason:'Rejected by user in Image Analysis' })
      });
      if (!result || result._ok === false || result.ok !== true) throw new Error(result?.detail || 'Could not save this rejection.');
      faceStatus('Suggestion rejected and remembered; it will stay hidden on future refreshes.', 'success');
      await window.axPhotosLoadFaceGroups(true);
    } catch (error) { faceStatus(error?.message || 'Could not reject this suggestion.', 'error'); }
  }

  document.addEventListener('click', async (event) => {
    const compareSuggestion = event.target.closest?.('[data-face-compare-suggestion][data-face-suggestion-id]');
    if (compareSuggestion) {
      event.preventDefault(); event.stopPropagation();
      const gid = Number(compareSuggestion.getAttribute('data-face-suggestion-group'));
      const pid = Number(compareSuggestion.getAttribute('data-face-suggestion-id'));
      const name = String(compareSuggestion.getAttribute('data-face-suggestion-name') || '');
      if (gid && pid && name) {
        state.faceGroupNames.set(gid, { id: pid, name, source: 'candidate-review' });
        await window.axPhotosReviewFaceReferences(gid);
      }
      return;
    }
    const confirmSuggestion = event.target.closest?.('[data-face-confirm-suggestion][data-face-suggestion-id]');
    if (confirmSuggestion) {
      event.preventDefault(); event.stopPropagation();
      await axPhotosConfirmFaceSuggestion(
        confirmSuggestion.getAttribute('data-face-suggestion-group'),
        confirmSuggestion.getAttribute('data-face-suggestion-id'),
        confirmSuggestion.getAttribute('data-face-suggestion-name') || '',
        confirmSuggestion.getAttribute('data-face-suggestion-source') || 'metadata',
        confirmSuggestion.getAttribute('data-face-suggestion-score'),
        confirmSuggestion.getAttribute('data-face-suggestion-basis') || ''
      );
      return;
    }
    const rejectSuggestion = event.target.closest?.('[data-face-reject-suggestion][data-face-suggestion-id]');
    if (rejectSuggestion) {
      event.preventDefault(); event.stopPropagation();
      await axPhotosRejectFaceSuggestion(
        rejectSuggestion.getAttribute('data-face-suggestion-group'),
        rejectSuggestion.getAttribute('data-face-suggestion-id'),
        rejectSuggestion.getAttribute('data-face-suggestion-source') || 'metadata',
        rejectSuggestion.getAttribute('data-face-suggestion-score')
      );
      return;
    }
    const suggestion = event.target.closest?.('[data-face-metadata-suggestion][data-face-suggestion-id]');
    if (suggestion) {
      const gid = Number(suggestion.getAttribute('data-face-metadata-suggestion'));
      const performerId = Number(suggestion.getAttribute('data-face-suggestion-id'));
      const performerName = String(suggestion.getAttribute('data-face-suggestion-name') || '');
      const basis = String(suggestion.getAttribute('data-face-suggestion-basis') || 'metadata');
      if (!gid || !performerId || !performerName) return;
      state.faceGroupNames.set(gid, { id: performerId, name: performerName, source: 'metadata-suggestion', basis });
      const input = el(`ax-face-performer-search-${gid}`); if (input) input.value = performerName;
      const results = el(`ax-face-performer-results-${gid}`); if (results) results.hidden = true;
      faceStatus(`Suggested ${performerName} from ${basis} text metadata. Review the profile/provider images and save only if it is correct.`, 'success');
      return;
    }
    const referencePick = event.target.closest?.('[data-reference-performer-id]');
    if (referencePick) {
      const id = Number(referencePick.getAttribute('data-reference-performer-id'));
      const name = String(referencePick.getAttribute('data-reference-performer-name') || '');
      const imageUrl = String(referencePick.getAttribute('data-reference-performer-image') || '');
      await axPhotosSetReviewPerformer(id, name, imageUrl, state.faceReview.requestId, true);
      return;
    }
  });
  document.addEventListener('input', (event) => {
    const chooser = event.target.closest?.('#ax-photos-reference-performer-search');
    if (chooser) searchReferencePerformers(chooser.value);
  });

  document.addEventListener('click', (event) => {
    const pick = event.target.closest?.('[data-face-pick-group][data-face-pick-id]');
    if (pick) {
      const gid = Number(pick.getAttribute('data-face-pick-group'));
      const performerId = Number(pick.getAttribute('data-face-pick-id'));
      const performerName = String(pick.getAttribute('data-face-pick-name') || '');
      state.faceGroupNames.set(gid, { id: performerId, name: performerName });
      const input = el(`ax-face-performer-search-${gid}`), results = el(`ax-face-performer-results-${gid}`);
      if (input) input.value = performerName;
      if (results) results.hidden = true;
      const reviewButton = el(`ax-face-review-${gid}`); if (reviewButton) reviewButton.disabled = false;
      faceStatus(`Selected ${performerName}. You can review the saved/provider images side by side, then save the assignment.`, 'success');
    }
  });
  document.addEventListener('click', (event) => {
    const candidate = event.target.closest?.('[data-review-provider-url]');
    if (!candidate) return;
    const url = String(candidate.getAttribute('data-review-provider-url') || '');
    const label = String(candidate.getAttribute('data-review-provider-label') || 'Provider image');
    const image = el('ax-photos-reference-provider-focus-image');
    const labelNode = el('ax-photos-reference-provider-focus-label');
    if (image) { image.hidden = false; image.src = url; image.onerror = () => { image.hidden = true; }; }
    if (labelNode) labelNode.textContent = label;
    state.faceReview.selectedUrl = url;
    el('ax-photos-reference-provider-grid')?.querySelectorAll('.ax2-photo-reference-candidate').forEach((button) => {
      button.classList.toggle('is-selected', button === candidate);
    });
  });
  document.addEventListener('input', (event) => {
    const search = event.target.closest?.('[data-face-performer-search]');
    if (search) {
      const gid = Number(search.getAttribute('data-face-performer-search'));
      state.faceGroupNames.delete(gid);
      window.axPhotosSearchPerformerForGroup(gid);
    }
  });
  document.addEventListener('change', (event) => {
    const clusterToggle = event.target?.closest?.('[data-face-cluster-select]');
    if (clusterToggle) {
      const gid = Number(clusterToggle.getAttribute('data-face-cluster-select'));
      if (clusterToggle.checked) state.faceSelectedClusters.add(gid); else state.faceSelectedClusters.delete(gid);
      const bar = el('ax-photos-face-merge-toolbar');
      if (bar) bar.innerHTML = `<span>${state.faceSelectedClusters.size} clusters selected</span><button type="button" class="btn pri sm" data-ax-photo-action="face-clusters-merge" ${state.faceSelectedClusters.size < 2 ? 'disabled' : ''}>Merge selected clusters</button><button type="button" class="btn sm" data-ax-photo-action="face-clusters-clear" ${state.faceSelectedClusters.size ? '' : 'disabled'}>Clear selection</button>`;
      return;
    }
    if (event.target?.id === 'ax-photos-channel' && event.target.value) {
      const label = event.target.selectedOptions?.[0]?.textContent || event.target.value;
      uploadStatus(`Destination: ${label}`);
    }
    if (event.target?.id === 'ax-photos-sort') loadGrid(true);
  });
  let searchTimer = 0;
  window.axPhotosSearch = function axPhotosSearch(query) {
    state.search = String(query || '').trim();
    window.clearTimeout(searchTimer);
    searchTimer = window.setTimeout(() => {
      if (isPhotosVisible()) loadGrid(true);
    }, 220);
  };
  document.addEventListener('keydown', (event) => {
    const dialog = el('ax-photos-lightbox');
    if (!dialog?.open) return;
    const editing = event.target?.closest?.('input,textarea,select,[contenteditable="true"]');
    if (editing && event.key !== 'Escape') return;
    if (event.key === 'Escape') { event.preventDefault(); window.axPhotosCloseLightbox?.(); return; }
    if (event.key === 'ArrowLeft') { event.preventDefault(); window.axPhotosViewerStep(-1); return; }
    if (event.key === 'ArrowRight') { event.preventDefault(); window.axPhotosViewerStep(1); return; }
    if (event.key === 'ArrowUp') { event.preventDefault(); window.axPhotosViewerZoom(1); return; }
    if (event.key === 'ArrowDown') { event.preventDefault(); window.axPhotosViewerZoom(-1); return; }
    if (event.key === '0') { event.preventDefault(); window.axPhotosViewerFit(); return; }
    if (event.key === '+' || event.key === '=') { event.preventDefault(); window.axPhotosViewerZoom(1); return; }
    if (event.key === '-' || event.key === '_') { event.preventDefault(); window.axPhotosViewerZoom(-1); return; }
    if (event.key === 'r' || event.key === 'R') { event.preventDefault(); window.axPhotosViewerRotate(event.shiftKey ? -90 : 90); return; }
    if (event.key === 'f' || event.key === 'F') { event.preventDefault(); window.axPhotosViewerToggleFullscreen(); return; }
    if (event.key === ' ') { event.preventDefault(); window.axPhotosViewerToggleSlideshow(); }
  });
  const photoGrid = el('ax-photos-grid');
  if (photoGrid) {
    photoGrid.addEventListener('pointerover', (event) => {
      if (event.pointerType === 'touch' || !window.matchMedia?.('(hover: hover)').matches) return;
      const card = event.target.closest?.('[data-photo-card]');
      if (!card || !photoGrid.contains(card) || card.contains(event.relatedTarget)) return;
      photoGrid.classList.add('is-hovering');
      photoGrid.querySelectorAll('.ax2-photo-card.is-hovered').forEach((node) => node.classList.remove('is-hovered'));
      card.classList.add('is-hovered');
    });
    photoGrid.addEventListener('pointerout', (event) => {
      const card = event.target.closest?.('[data-photo-card]');
      if (!card || card.contains(event.relatedTarget)) return;
      card.classList.remove('is-hovered');
      if (!photoGrid.querySelector('.ax2-photo-card.is-hovered')) photoGrid.classList.remove('is-hovering');
    });
  }

  document.addEventListener('click', (event) => {
    const thumb = event.target.closest('[data-viewer-index]');
    if (thumb && thumb.closest('#ax-photos-viewer-filmstrip')) {
      if (state.suppressFilmstripClick) { event.preventDefault(); event.stopPropagation(); state.suppressFilmstripClick = false; return; }
      const index = Number(thumb.getAttribute('data-viewer-index'));
      if (Number.isInteger(index)) { playViewerTick(index > state.viewerIndex ? 1300 : 1050); showViewerItem(index, index > state.viewerIndex ? 'right' : 'left'); }
    }
    if (event.target === el('ax-photos-lightbox')) window.axPhotosCloseLightbox?.();
  });
  const viewerStage = () => el('ax-photos-viewer-stage');
  const viewerStrip = el('ax-photos-viewer-filmstrip');
  if (viewerStrip) {
    viewerStrip.addEventListener('pointerdown', (event) => {
      if (event.pointerType !== 'mouse' || event.button !== 0 || event.target.closest('button')) return;
      state.filmstripDragging = true; state.filmstripDragMoved = false; state.filmstripDragStartX = event.clientX; state.filmstripDragScrollLeft = viewerStrip.scrollLeft;
    });
    viewerStrip.addEventListener('pointermove', (event) => {
      if (!state.filmstripDragging || event.pointerType !== 'mouse') return;
      const dx = event.clientX - state.filmstripDragStartX;
      if (Math.abs(dx) > 5) state.filmstripDragMoved = true;
      if (state.filmstripDragMoved) { viewerStrip.scrollLeft = state.filmstripDragScrollLeft - dx; }
    });
    const finishStripDrag = () => {
      if (state.filmstripDragging && state.filmstripDragMoved) {
        state.suppressFilmstripClick = true;
        window.setTimeout(() => { state.suppressFilmstripClick = false; }, 180);
      }
      state.filmstripDragging = false;
      window.setTimeout(() => { state.filmstripDragMoved = false; }, 0);
    };
    viewerStrip.addEventListener('pointerup', finishStripDrag);
    viewerStrip.addEventListener('pointercancel', finishStripDrag);
  }
  // Wheel over the control pill navigates the main image. The bottom
  // filmstrip keeps its own behavior and is intentionally not modified here.
  const viewerControls = document.querySelector('.ax2-photo-viewer-tools');
  if (viewerControls) {
    viewerControls.addEventListener('wheel', (event) => {
      if (!el('ax-photos-lightbox')?.open || event.ctrlKey || event.metaKey) return;
      const rawDelta = Math.abs(event.deltaX) > Math.abs(event.deltaY) ? event.deltaX : event.deltaY;
      if (!rawDelta) return;
      if (event.cancelable) event.preventDefault();
      event.stopPropagation();
      const direction = rawDelta > 0 ? 1 : -1;
      const magnitude = Math.abs(rawDelta);
      // Large wheel gestures may advance multiple images, but cap a single
      // event to avoid accidental jumps on high-resolution trackpads.
      const steps = Math.max(1, Math.min(3, Math.round(magnitude / 120) || 1));
      for (let i = 0; i < steps; i++) window.axPhotosViewerStep?.(direction);
    }, { passive: false });
  }

  // Wheel input over the lower toolbar moves the virtual thumbnail window,
  // without changing the currently selected photo. The 12-card DOM window is
  // clipped by design, so native scrollLeft cannot move it; update its index.
  const filmstripWheel = el('ax-photos-viewer-filmstrip');
  if (filmstripWheel) {
    filmstripWheel.addEventListener('wheel', (event) => {
      if (!el('ax-photos-lightbox')?.open || event.ctrlKey || event.metaKey) return;
      const rawDelta = Math.abs(event.deltaX) > Math.abs(event.deltaY) ? event.deltaX : event.deltaY;
      if (!rawDelta) return;
      const scale = event.deltaMode === 1 ? 16
        : (event.deltaMode === 2 ? Math.max(1, filmstripWheel.clientWidth) : 1);
      const delta = rawDelta * scale;
      if (event.cancelable) event.preventDefault();
      state.filmstripWheelAccumulator += delta;
      if (state.filmstripWheelRaf) return;
      state.filmstripWheelRaf = window.requestAnimationFrame(() => {
        state.filmstripWheelRaf = 0;
        const stepPixels = 72;
        const steps = Math.trunc(state.filmstripWheelAccumulator / stepPixels);
        if (!steps) return;
        state.filmstripWheelAccumulator -= steps * stepPixels;
        const moved = shiftViewerFilmstripWindow(steps);
        if (!moved) state.filmstripWheelAccumulator = 0;
      });
    }, { passive: false });
  }

  const stageForPointers = viewerStage();
  let lastViewerTouchTap = null;
  const handleViewerTouchTap = (event) => {
    const now = Date.now();
    const previous = lastViewerTouchTap;
    if (previous && now - previous.time < 330 && Math.hypot(event.clientX - previous.x, event.clientY - previous.y) < 34) {
      lastViewerTouchTap = null;
      if (state.viewerZoom > state.viewerFitScale + 0.015) fitViewerImage();
      else zoomViewerToScale(clampViewerScale(state.viewerFitScale * 2.5), event.clientX, event.clientY);
      return;
    }
    lastViewerTouchTap = { time:now, x:event.clientX, y:event.clientY };
  };
  if (stageForPointers) {
    stageForPointers.addEventListener('wheel', (event) => {
      if (!el('ax-photos-lightbox')?.open || event.target.closest('button')) return;
      const image = el('ax-photos-lightbox-img');
      if (!image || !(image.naturalWidth > 0) || !(image.naturalHeight > 0)) return;
      if (event.cancelable) event.preventDefault();
      const rect = stageForPointers.getBoundingClientRect();
      const delta = event.deltaMode === 1 ? event.deltaY * 16
        : (event.deltaMode === 2 ? event.deltaY * rect.height : event.deltaY);
      const factor = Math.exp(-Math.max(-2400, Math.min(2400, delta)) * 0.0018);
      const base = state.viewerTargetZoom || state.viewerZoom || state.viewerFitScale || 1;
      zoomViewerToScale(base * factor, event.clientX, event.clientY);
    }, { passive: false });
    stageForPointers.addEventListener('pointerdown', (event) => {
      if (event.target.closest('button')) return;
      if (event.pointerType !== 'touch' && event.button !== 0) return;
      stageForPointers.setPointerCapture?.(event.pointerId);
      state.viewerPointers.set(event.pointerId, { x:event.clientX, y:event.clientY, pointerType:event.pointerType });
      if (state.viewerPointers.size === 2) {
        const points = [...state.viewerPointers.values()];
        const midpoint = { x:(points[0].x + points[1].x) / 2, y:(points[0].y + points[1].y) / 2 };
        const distance = Math.max(1, Math.hypot(points[1].x - points[0].x, points[1].y - points[0].y));
        lastViewerTouchTap = null;
        stopViewerTransformAnimation();
        state.viewerTargetZoom = state.viewerZoom;
        state.viewerTargetX = state.viewerX; state.viewerTargetY = state.viewerY;
        state.viewerGesture = {
          kind:'pinch', startDistance:distance, startZoom:state.viewerZoom,
          startX:state.viewerX, startY:state.viewerY, startMidX:midpoint.x, startMidY:midpoint.y, moved:false,
        };
        stageForPointers.classList.add('is-pinch-zooming');
        stageForPointers.classList.remove('is-panning');
        if (event.cancelable) event.preventDefault();
      } else if (state.viewerPointers.size > 2) {
        state.viewerGesture = { kind:'ignore', moved:false };
        stageForPointers.classList.remove('is-panning', 'is-pinch-zooming');
      } else if (event.pointerType !== 'touch') {
        event.preventDefault();
        stopViewerTransformAnimation();
        state.viewerTargetZoom = state.viewerZoom;
        state.viewerTargetX = state.viewerX; state.viewerTargetY = state.viewerY;
        state.viewerGesture = {
          kind:'pan', pointerId:event.pointerId, x:event.clientX, y:event.clientY,
          startViewerX:state.viewerX, startViewerY:state.viewerY, moved:false,
        };
        stageForPointers.classList.add('is-panning');
      } else if (state.viewerZoom > state.viewerFitScale + 0.015) {
        stopViewerTransformAnimation();
        state.viewerTargetZoom = state.viewerZoom;
        state.viewerTargetX = state.viewerX; state.viewerTargetY = state.viewerY;
        state.viewerGesture = {
          kind:'pan', pointerId:event.pointerId, x:event.clientX, y:event.clientY,
          startViewerX:state.viewerX, startViewerY:state.viewerY, moved:false,
        };
        stageForPointers.classList.add('is-panning');
      } else {
        state.viewerGesture = { kind:'swipe', x:event.clientX, y:event.clientY, moved:false };
      }
    });
    stageForPointers.addEventListener('pointermove', (event) => {
      if (!state.viewerPointers.has(event.pointerId) || !state.viewerGesture) return;
      state.viewerPointers.set(event.pointerId, { x:event.clientX, y:event.clientY, pointerType:event.pointerType });
      const g = state.viewerGesture;
      if (g.kind === 'ignore') return;
      if (g.kind === 'pinch') {
        const points = [...state.viewerPointers.values()];
        if (points.length < 2) return;
        const midpointX = (points[0].x + points[1].x) / 2;
        const midpointY = (points[0].y + points[1].y) / 2;
        const distance = Math.max(1, Math.hypot(points[1].x - points[0].x, points[1].y - points[0].y));
        const zoom = clampViewerScale(g.startZoom * distance / g.startDistance);
        const ratio = zoom / Math.max(0.001, g.startZoom);
        const rect = stageForPointers.getBoundingClientRect();
        const centerX = rect.left + rect.width / 2, centerY = rect.top + rect.height / 2;
        const startImageX = g.startMidX - centerX - g.startX;
        const startImageY = g.startMidY - centerY - g.startY;
        const pose = clampViewerPose(
          zoom,
          midpointX - centerX - startImageX * ratio,
          midpointY - centerY - startImageY * ratio,
        );
        state.viewerZoom = state.viewerTargetZoom = zoom;
        state.viewerX = state.viewerTargetX = pose.x;
        state.viewerY = state.viewerTargetY = pose.y;
        if (Math.abs(distance - g.startDistance) > 2 || Math.abs(midpointX - g.startMidX) + Math.abs(midpointY - g.startMidY) > 3) g.moved = true;
        setViewerTransform();
        if (event.cancelable) event.preventDefault();
        return;
      }
      if (g.kind === 'pan' && g.pointerId === event.pointerId) {
        const dx = event.clientX - g.x, dy = event.clientY - g.y;
        if (Math.abs(dx) + Math.abs(dy) > 2) g.moved = true;
        const pose = clampViewerPose(state.viewerZoom, g.startViewerX + dx, g.startViewerY + dy);
        state.viewerX = state.viewerTargetX = pose.x;
        state.viewerY = state.viewerTargetY = pose.y;
        setViewerTransform();
        if (event.cancelable) event.preventDefault();
        return;
      }
      const dx = event.clientX - g.x, dy = event.clientY - g.y;
      if (Math.abs(dx) + Math.abs(dy) > 5) g.moved = true;
    });
    const finishPointer = (event) => {
      if (!state.viewerPointers.has(event.pointerId)) return;
      const g = state.viewerGesture;
      const trackedPointers = state.viewerPointers.size;
      if (g?.kind === 'pan' && g.pointerId === event.pointerId) {
        stageForPointers.classList.remove('is-panning');
        state.viewerTargetZoom = state.viewerZoom;
        state.viewerTargetX = state.viewerX; state.viewerTargetY = state.viewerY;
      }
      if (g?.kind === 'pinch') stageForPointers.classList.remove('is-pinch-zooming');
      if (g && trackedPointers === 1 && g.kind === 'swipe' && g.moved) {
        const dx = event.clientX - g.x;
        const dy = event.clientY - g.y;
        if (Math.abs(dx) > 70 && Math.abs(dx) > Math.abs(dy) * 1.15) {
          lastViewerTouchTap = null;
          window.axPhotosViewerStep(dx < 0 ? 1 : -1);
        }
      }
      if (event.pointerType === 'touch' && trackedPointers === 1 && !g?.moved && (g?.kind === 'swipe' || g?.kind === 'pan')) handleViewerTouchTap(event);
      state.viewerPointers.delete(event.pointerId);
      if (!state.viewerPointers.size) {
        state.viewerGesture = null;
        stageForPointers.classList.remove('is-panning', 'is-pinch-zooming');
      } else if (state.viewerPointers.size === 1) {
        const [pointerId, p] = [...state.viewerPointers.entries()][0];
        if (p.pointerType === 'touch' && state.viewerZoom > state.viewerFitScale + 0.015) {
          state.viewerGesture = {
            kind:'pan', pointerId, x:p.x, y:p.y,
            startViewerX:state.viewerX, startViewerY:state.viewerY, moved:false,
          };
          stageForPointers.classList.add('is-panning');
        } else {
          state.viewerGesture = { kind:'swipe', x:p.x, y:p.y, moved:false };
          stageForPointers.classList.remove('is-panning');
        }
      }
    };
    stageForPointers.addEventListener('pointerup', finishPointer);
    stageForPointers.addEventListener('pointercancel', finishPointer);
    stageForPointers.addEventListener('lostpointercapture', finishPointer);
  }
  document.addEventListener('visibilitychange', () => { if (document.visibilityState === 'visible' && isPhotosVisible() && state.selected.size) loadGrid(true); });
  document.addEventListener('click', (event) => { if (event.target.closest?.('[data-view="photos"], [data-nav="photos"], [href="#photos"]')) { initPhotoInfiniteScroll(); schedulePhotoAutoLoad(); } });
  window.axPhotosTrackUpload = function axPhotosTrackUpload(jobId, destinationChannelId) {
    return watchJob(String(jobId || ''), 'upload', String(destinationChannelId || ''));
  };
  window.ArchiveXPhotos = Object.freeze({
    loadChannels: window.axPhotosLoadChannels,
    search: window.axPhotosSearch,
    trackUpload: window.axPhotosTrackUpload,
    toggleFavoritesFilter: window.axPhotosToggleFavoritesFilter,
    toggleAllChannels: window.axPhotosToggleAllChannels,
    editSelectedChannel: window.axPhotosEditSelectedChannel,
    toggleChannel: window.axPhotosToggleChannel,
    setTab: window.axPhotosSetTab,
    clearFaceGroupFilter: window.axPhotosClearFaceGroupFilter,
    analyzeFaces: window.axPhotosAnalyzeFaces,
    mergeSelectedFaceGroups: window.axPhotosMergeSelectedFaceGroups,
    clearSelectedFaceGroups: window.axPhotosClearSelectedFaceGroups,
    viewFaceGroup: window.axPhotosViewFaceGroup,
    reviewFaceReferences: window.axPhotosReviewFaceReferences,
    saveFaceGroupAssignment: window.axPhotosSaveFaceGroupAssignment,
    clearFaceGroupAssignment: window.axPhotosClearFaceGroupAssignment,
    splitFaceDetection: window.axPhotosSplitFaceDetection,
    closeReferenceReview: window.axPhotosCloseReferenceReview,
    loadMoreReferenceImages: window.axPhotosLoadMoreReferenceImages,
    confirmReferenceAssignment: window.axPhotosConfirmReferenceAssignment,
    openImageAt: window.axPhotosOpenImageAt,
    viewerToggleSound: window.axPhotosViewerToggleSound,
    closeViewer: window.axPhotosCloseLightbox,
    viewerStep: window.axPhotosViewerStep,
    viewerToggleSlideshow: window.axPhotosViewerToggleSlideshow,
    viewerZoom: window.axPhotosViewerZoom,
    viewerFit: window.axPhotosViewerFit,
    viewerRotate: window.axPhotosViewerRotate,
    viewerToggleFavorite: window.axPhotosViewerToggleFavorite,
    viewerToggleFullscreen: window.axPhotosViewerToggleFullscreen
  });
})();
