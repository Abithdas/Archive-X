import { describe, expect, it } from 'vitest';
import { PHOTO_MAX_BYTES, displayName, initials, nameProblem, photoProblem, photoUrl, saveBody, unchanged } from './profile';
describe('profile rules', () => {
    it('builds initials and the photo URL', () => {
        expect(initials('Ava Stone Jr')).toBe('AS');
        expect(initials('')).toBe('A');
        expect(initials('zoë')).toBe('Z');
        expect(photoUrl({ has_custom_image: true, image_version: '5' })).toBe('/api/account/profile/image?v=5');
        expect(photoUrl({})).toBe('/api/telegram/me/photo?v=0');
        expect(photoUrl(undefined)).toBe('/api/telegram/me/photo?v=0');
        expect(displayName({ name: ' ', username: 'bob' })).toBe('bob');
        expect(displayName(undefined)).toBe('');
    });
    it('checks photos and names', () => {
        expect(photoProblem({ size: 10, type: 'image/png' })).toBe('');
        expect(photoProblem({ size: 10, type: '' })).toBe('');
        expect(photoProblem({ size: PHOTO_MAX_BYTES + 1, type: 'image/png' })).toMatch(/8 MiB/);
        expect(photoProblem({ size: 10, type: 'image/svg+xml' })).toMatch(/PNG, JPEG/);
        expect(nameProblem('x'.repeat(64))).toBe('');
        expect(nameProblem('x'.repeat(65))).toMatch(/64 characters/);
    });
    it('knows when there is nothing to save and builds the request', () => {
        expect(unchanged({ custom_name: 'Ava' }, { name: ' Ava ', imageData: '', reset: false })).toBe(true);
        expect(unchanged({}, { name: '', imageData: '', reset: false })).toBe(true);
        expect(unchanged({ custom_name: 'Ava' }, { name: 'Ava', imageData: 'data:x', reset: false })).toBe(false);
        expect(unchanged({ custom_name: 'Ava' }, { name: 'Ava', imageData: '', reset: true })).toBe(false);
        expect(saveBody({ name: ' Ava ', imageData: '', reset: false })).toEqual({ name: 'Ava' });
        expect(saveBody({ name: 'A', imageData: 'data:image/png;base64,xx', reset: false })).toEqual({ name: 'A', image_data: 'data:image/png;base64,xx' });
        expect(saveBody({ name: 'A', imageData: '', reset: true })).toEqual({ name: 'A', reset_image: true });
    });
});
