/* ============================================================================
 * FILE: js/178-photo-upload-toolbar.js
 * AREA: Photos
 * STATUS: Not referenced by web/html.py or the HTML templates, so it is not
 *         served as a script. Check before relying on it or deleting it.
 * ============================================================================ */
/* Archive X Photos page — folder selection and local photo uploads to Telegram. */
(function () {
  'use strict';
  let loadedChannels = false;
  let channelLoadTask = null;

  function el(id) { return document.getElementById(id); }
  function setStatus(message, tone) {
    const status = el('ax-photos-upload-status');
    if (!status) return;
    status.textContent = String(message || '');
    status.classList.toggle('is-error', tone === 'error');
    status.classList.toggle('is-success', tone === 'success');
  }

  window.axPhotosLoadChannels = async function axPhotosLoadChannels(force = false) {
    const select = el('ax-photos-channel');
    if (!select) return;
    if (channelLoadTask && !force) return channelLoadTask;
    if (loadedChannels && !force) return;
    channelLoadTask = (async () => {
      select.disabled = true;
      select.innerHTML = '<option value="">Loading channels…</option>';
      const result = await api('/api/source-channels', { cache: 'no-store' });
      if (!Array.isArray(result)) {
        select.innerHTML = '<option value="">Channels unavailable</option>';
        select.disabled = false;
        setStatus(result?.detail || 'Could not load saved Telegram channels. Check Telegram in Settings and try again.', 'error');
        return;
      }
      const channels = result.filter((channel) => String(channel?.telegram_id || '').trim());
      channels.sort((a, b) => String(a.title || a.username || a.telegram_id).localeCompare(String(b.title || b.username || b.telegram_id)));
      const options = ['<option value="">Choose a destination channel…</option>'];
      channels.forEach((channel) => {
        const id = String(channel.telegram_id).trim();
        const label = typeof channelDisplayName === 'function'
          ? channelDisplayName(id, channel.title || channel.username || '')
          : (String(channel.title || channel.username || '').trim() || `Channel ${id}`);
        options.push(`<option value="${esc(id)}">${esc(label)}</option>`);
      });
      select.innerHTML = options.join('');
      select.disabled = false;
      loadedChannels = true;
      if (!channels.length) {
        setStatus('No saved channels found yet. Connect Telegram and make sure the destination channel is known to Archive X.', 'error');
      } else {
        setStatus('Choose a photo folder and destination channel to begin.');
      }
    })();
    try { await channelLoadTask; }
    finally { channelLoadTask = null; }
  };

  window.axPhotosBrowseFolder = async function axPhotosBrowseFolder() {
    const field = el('ax-photos-folder');
    if (!field) return;
    setStatus('Opening the folder picker on the Archive X PC…');
    try {
      const result = await api('/api/settings/storage/browse', {
        method: 'POST',
        body: '{}'
      });
      if (result?._ok === false) {
        throw new Error(result?.detail || 'Folder picker is unavailable on this device.');
      }
      if (!result?.selected_path) {
        setStatus('Folder selection cancelled. Enter the folder path manually if needed.');
        return;
      }
      field.value = result.selected_path;
      setStatus(`Photo folder selected: ${result.selected_path}`, 'success');
    } catch (error) {
      setStatus(error?.message || 'Folder picker unavailable. Enter the folder path manually.', 'error');
    }
  };

  window.axPhotosUploadFolder = async function axPhotosUploadFolder() {
    const folder = String(el('ax-photos-folder')?.value || '').trim();
    const channel = String(el('ax-photos-channel')?.value || '').trim();
    const button = el('ax-photos-upload-btn');
    if (!folder) {
      setStatus('Choose a photo folder or enter its full path first.', 'error');
      el('ax-photos-folder')?.focus();
      return;
    }
    if (!channel) {
      setStatus('Choose the Telegram destination channel first.', 'error');
      el('ax-photos-channel')?.focus();
      return;
    }
    const deleteAfterVerification = el('ax-photos-delete-source')?.checked !== false;
    if (deleteAfterVerification) {
      const option = el('ax-photos-channel')?.selectedOptions?.[0];
      const destinationName = option?.textContent?.trim() || channel;
      const warning = `Upload the images from this folder as Telegram documents to “${destinationName}”?\n\nArchive X will verify the destination message, filename and size, then compare the downloaded document’s SHA-256 with the source file before permanently deleting that exact source file. Files with failed or inconclusive verification will be retained.\n\nThis cannot be undone.`;
      const confirmed = typeof axConfirm === 'function'
        ? await axConfirm(warning, {title: 'Upload documents and delete verified sources?', okLabel: 'Upload and delete sources', confirmLabel: 'Upload and delete sources', danger: true})
        : window.confirm(warning);
      if (!confirmed) return;
    }
    if (button) button.disabled = true;
    setStatus('Checking the folder and queueing document uploads…');
    try {
      const result = await api('/api/telegram-transfer/start', {
        method: 'POST',
        body: JSON.stringify({
          source: 'local',
          media_type: 'PHOTOS',
          folder,
          destination_channel: channel,
          include_subfolders: !!el('ax-photos-subfolders')?.checked,
          delete_local_after_delivery: deleteAfterVerification
        })
      });
      if (!result || result._ok === false || !result.id) {
        throw new Error(result?.detail || 'Archive X could not queue the photo upload.');
      }
      setStatus(`Photo document upload queued as job ${result.id}. ${deleteAfterVerification ? 'Source files will be deleted only after destination verification.' : 'Source files will be kept.'} Verified destination images are added to the shared Photos library and will open under this channel when the job finishes. Track progress in Operations.`, 'success');
      if (typeof toast === 'function') toast('Photo document upload queued.');
      if (typeof refresh === 'function') refresh();
      const photosApi = window.ArchiveXPhotos;
      if (typeof photosApi?.trackUpload === 'function') photosApi.trackUpload(result.id, channel).catch(() => {});
    } catch (error) {
      setStatus(error?.message || 'Could not start photo upload.', 'error');
    } finally {
      if (button) button.disabled = false;
    }
  };

  document.addEventListener('change', (event) => {
    if (event.target?.id === 'ax-photos-channel' && event.target.value) {
      const option = event.target.selectedOptions?.[0];
      setStatus(option ? `Destination: ${option.textContent}` : 'Destination channel selected.');
    }
  });
})();
