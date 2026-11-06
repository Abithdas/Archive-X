/* ============================================================================
 * FILE: js/181-photo-actions.js
 * PURPOSE: Photos page action menu.
 * AREA: Photos
 * LOAD: Classic script. Order is set by index.html, the HTML partials and
 *       _LAYERS in web/html.py. Later scripts can wrap earlier globals,
 *       so check docs/CODE_MAP.md before moving code between files.
 * ============================================================================ */
(function initPhotoActions(window, document) {
  'use strict';

  const photoPageActions = Object.freeze({
    'favorites-toggle': (_button, photos) => photos.toggleFavoritesFilter(),
    'channels-refresh': (button, photos) => photos.loadChannels(button.dataset.axPhotoValue === 'true'),
    'channels-toggle-all': (_button, photos) => photos.toggleAllChannels(),
    'channel-edit-selected': (_button, photos) => photos.editSelectedChannel(),
    'channel-toggle': (button, photos) => photos.toggleChannel(button.dataset.axPhotoValue || ''),
    'tab-set': (button, photos) => photos.setTab(button.dataset.axPhotoValue || 'library'),
    'face-filter-clear': (_button, photos) => photos.clearFaceGroupFilter(),
    'faces-analyze': (_button, photos) => photos.analyzeFaces(),
    'face-clusters-merge': (_button, photos) => photos.mergeSelectedFaceGroups(),
    'face-clusters-clear': (_button, photos) => photos.clearSelectedFaceGroups(),
    'face-group-view': (button, photos) => photos.viewFaceGroup(Number(button.dataset.axPhotoValue)),
    'face-reference-review': (button, photos) => photos.reviewFaceReferences(Number(button.dataset.axPhotoValue)),
    'face-assignment-save': (button, photos) => photos.saveFaceGroupAssignment(Number(button.dataset.axPhotoValue)),
    'face-assignment-clear': (button, photos) => photos.clearFaceGroupAssignment(Number(button.dataset.axPhotoValue)),
    'face-detection-split': (button, photos) => photos.splitFaceDetection(Number(button.dataset.axPhotoValue)),
    'reference-review-close': (_button, photos) => photos.closeReferenceReview(),
    'reference-images-more': (_button, photos) => photos.loadMoreReferenceImages(),
    'reference-assignment-save': (_button, photos) => photos.confirmReferenceAssignment(),
    'image-open': (button, photos) => photos.openImageAt(Number(button.dataset.axPhotoValue)),
    'viewer-sound-toggle': (_button, photos) => photos.viewerToggleSound(),
    'viewer-close': (_button, photos) => photos.closeViewer(),
    'viewer-step': (button, photos) => photos.viewerStep(Number(button.dataset.axPhotoValue)),
    'viewer-slideshow-toggle': (_button, photos) => photos.viewerToggleSlideshow(),
    'viewer-zoom': (button, photos) => photos.viewerZoom(Number(button.dataset.axPhotoValue)),
    'viewer-fit': (_button, photos) => photos.viewerFit(),
    'viewer-rotate': (button, photos) => photos.viewerRotate(Number(button.dataset.axPhotoValue)),
    'viewer-favorite-toggle': (_button, photos) => photos.viewerToggleFavorite(),
    'viewer-fullscreen-toggle': (_button, photos) => photos.viewerToggleFullscreen()
  });

  document.addEventListener('click', (event) => {
    const button = event.target.closest?.('[data-ax-photo-action]');
    const root = document.getElementById('vphotos');
    if (!button || !root?.contains(button)) return;

    const action = button.getAttribute('data-ax-photo-action');
    const handler = photoPageActions[action];
    const photos = window.ArchiveXPhotos;
    if (typeof handler !== 'function' || !photos) return;

    event.preventDefault();
    try {
      Promise.resolve(handler(button, photos)).catch((error) => {
        console.error(`Photo action "${action}" failed`, error);
      });
    } catch (error) {
      console.error(`Photo action "${action}" failed`, error);
    }
  });
})(window, document);
