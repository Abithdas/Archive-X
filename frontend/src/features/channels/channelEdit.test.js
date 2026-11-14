import { describe, expect, it } from 'vitest';
import { chosenText, editBody, initialsOf, nothingToSave, photoProblem, previewUrl, resetPreviewUrl, shouldAskSync, startTitle, syncBody, syncChanges, syncCopy, syncDone, syncFailed, titleChanged } from './channelEdit';
const d = (o = {}) => ({ title: 'Main', original: 'Main', imageData: '', reset: false, ...o });
describe('channel edit', () => {
    it('prefills only a real title and builds initials', () => { expect(startTitle({ title: 'Channel -100' })).toBe(''); expect(startTitle({ title: 'Unknown channel' })).toBe(''); expect(startTitle({ title: ' Main ' })).toBe('Main'); expect(initialsOf('Main Archive')).toBe('MA'); expect(initialsOf('')).toBe('TG'); });
    it('shows the local photo or Telegram\'s', () => {
        expect(previewUrl({ telegram_id: '-1', image: 'manual-upload:abc', updated_at: 't 1' })).toBe('/api/channels/-1/image?v=t%201&w=240');
        expect(previewUrl({ telegram_id: '-1' })).toBe('/api/channels/-1/telegram-photo?v=0');
        expect(resetPreviewUrl('-1', 5)).toBe('/api/channels/-1/telegram-photo?refresh=5');
    });
    it('checks photos', () => { expect(photoProblem({ size: 1, type: 'image/webp' })).toBe(''); expect(photoProblem({ size: 9e6, type: 'image/png' })).toMatch(/8 MiB/); expect(photoProblem({ size: 1, type: 'image/svg+xml' })).toMatch(/PNG, JPEG, WebP, or GIF/); expect(chosenText('a.png')).toMatch(/^Selected a\.png\./); });
    it('knows what changed and builds the request', () => {
        expect(nothingToSave(d())).toBe(true);
        expect(nothingToSave(d({ title: ' Main ' }))).toBe(true);
        expect(titleChanged(d({ title: 'New' }))).toBe(true);
        expect(nothingToSave(d({ reset: true }))).toBe(false);
        expect(editBody(d({ title: ' New ', imageData: 'data:x' }))).toEqual({ title: 'New', image_data: 'data:x' });
        expect(editBody(d({ reset: true }))).toEqual({ title: 'Main', reset_image_to_api: true });
    });
    it('asks about Telegram only for a new name or photo, and words it like classic', () => {
        expect(shouldAskSync(d())).toBe(false);
        expect(shouldAskSync(d({ reset: true }))).toBe(false);
        expect(shouldAskSync(d({ title: 'N' }))).toBe(true);
        expect(shouldAskSync(d({ imageData: 'x' }))).toBe(true);
        expect(syncChanges(d({ title: 'N', imageData: 'x' }))).toBe('channel name and profile photo');
        expect(syncChanges(d({ imageData: 'x' }))).toBe('profile photo');
        expect(syncCopy('profile photo')).toMatchObject({ ok: 'Push to Telegram', cancel: 'Save locally only', text: expect.stringContaining('Push the changed profile photo to Telegram now?') });
        expect(syncBody(d({ title: 'N' }))).toEqual({ sync_title: true, sync_photo: false });
        expect(syncDone(['title', 'photo'])).toBe('Saved locally and synchronized to Telegram: title, photo.');
        expect(syncFailed('')).toBe('Local changes saved, but Telegram sync failed: unknown error');
    });
});
