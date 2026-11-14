import { describe, expect, it } from 'vitest';
import { candidatesUrl, editUrl, fileProblem, footerFor, groups, imageUrl, initialState, nameProblem, providerSummary, saveBody, savedMessage, saveLabel, stateProvider, titleFor } from './editor';
const form = (o = {}) => ({ kind: 'performers', name: ' Ava ', manualUrl: '', reset: false, profile: ' https://p ', bio: ' bio ', imageData: '', provider: null, ...o });
const tpdb = { url: 'https://i/a.jpg', provider: 'theporndb', width: 800, height: 1200 };
const stash = { url: 'https://i/b.jpg', provider: 'stashdb' };
describe('wording and urls', () => {
    it('names the dialog and button for each kind', () => {
        expect(titleFor('performers')).toBe('Profile picture');
        expect(titleFor('studios')).toBe('Studio image');
        expect(saveLabel('performers')).toBe('Save profile picture');
        expect(saveLabel('studios')).toBe('Save studio image');
    });
    it('builds the endpoints', () => {
        expect(editUrl('performers', 3)).toBe('/api/performers/3/edit');
        expect(candidatesUrl('studios', 4, 20)).toBe('/api/studios/4/image-candidates?limit=10&offset=20');
        expect(imageUrl('performers', 3, 'a b')).toBe('/api/performers/3/image?v=a%20b');
        expect(imageUrl('performers', 3, null)).toBe('/api/performers/3/image?v=');
    });
    it('states what the current image is', () => {
        expect(initialState(true)).toBe('Locked current choice');
        expect(initialState(false)).toBe('Current provider image');
        expect(stateProvider('theporndb')).toBe('Selected THEPORNDB image — save to apply');
    });
});
describe('provider images', () => {
    it('groups ThePornDB first, then StashDB, keeping each image\'s position, and drops empty groups', () => {
        const g = groups([stash, tpdb, { url: 'x', provider: 'other' }]);
        expect(g.map((x) => x.label)).toEqual(['ThePornDB', 'STASHDB']);
        expect(g[0].items[0]).toMatchObject({ index: 1, caption: 'ThePornDB · 800×1200' });
        expect(g[1].items[0]).toMatchObject({ index: 0, caption: 'STASHDB' });
        expect(groups([stash]).map((x) => x.key)).toEqual(['stashdb']);
        expect(groups([])).toEqual([]);
    });
    it('summarises the counts and whether there is more', () => {
        expect(providerSummary([], false)).toBe('No provider images found.');
        expect(providerSummary([tpdb, tpdb, stash], false)).toBe('THEPORNDB: 2 · STASHDB: 1');
        expect(providerSummary([tpdb], true)).toBe('THEPORNDB: 1 · More images available');
        expect(footerFor(0)).toBe('You can use a manual image URL or file.');
        expect(footerFor(3)).toBe('Select an image, then save.');
    });
});
describe('files', () => {
    it('limits uploads to 8 MiB', () => {
        expect(fileProblem(8 * 1024 * 1024)).toBe('');
        expect(fileProblem(8 * 1024 * 1024 + 1)).toBe('Image must be 8 MiB or smaller.');
    });
});
describe('save request', () => {
    it('sends trimmed name, profile and bio for a performer, and only the name for a studio', () => {
        expect(saveBody(form())).toEqual({ name: 'Ava', image_url: '', reset_image_to_api: false, profile: 'https://p', bio: 'bio' });
        expect(saveBody(form({ kind: 'studios' }))).toEqual({ name: 'Ava', image_url: '', reset_image_to_api: false });
    });
    it('sends the chosen provider image', () => {
        expect(saveBody(form({ provider: tpdb }))).toMatchObject({ provider_image_url: 'https://i/a.jpg', provider_image_provider: 'theporndb' });
        expect(saveBody(form({ provider: { url: 'https://i/z.jpg' } }))).toMatchObject({ provider_image_provider: 'provider' });
    });
    it('a typed URL wins over a provider image and over restore', () => {
        const b = saveBody(form({ manualUrl: ' https://m/x.png ', provider: tpdb, reset: true }));
        expect(b).toMatchObject({ image_url: 'https://m/x.png', reset_image_to_api: false });
        expect(b).not.toHaveProperty('provider_image_url');
    });
    it('an uploaded file wins over a provider image', () => {
        const b = saveBody(form({ imageData: 'data:image/png;base64,AAA', provider: tpdb }));
        expect(b.image_data).toBe('data:image/png;base64,AAA');
        expect(b).not.toHaveProperty('provider_image_url');
    });
    it('restore sends the flag and no provider image', () => {
        const b = saveBody(form({ reset: true, provider: tpdb }));
        expect(b.reset_image_to_api).toBe(true);
        expect(b).not.toHaveProperty('provider_image_url');
    });
    it('requires a name', () => {
        expect(nameProblem('  ')).toBe('Enter a name.');
        expect(nameProblem('x')).toBe('');
    });
    it('words the result like the classic page', () => {
        expect(savedMessage(form({ provider: tpdb }))).toBe('Selected profile image saved.');
        expect(savedMessage(form({ reset: true }))).toBe('Provider profile image restored.');
        expect(savedMessage(form({ manualUrl: 'https://m', provider: tpdb }))).toBe('Profile picture saved.');
        expect(savedMessage(form({ imageData: 'data:x', provider: tpdb }))).toBe('Profile picture saved.');
        expect(savedMessage(form())).toBe('Profile picture saved.');
    });
});
