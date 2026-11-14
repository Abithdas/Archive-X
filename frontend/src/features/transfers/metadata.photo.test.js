import { describe, expect, it } from 'vitest';
import { joinLaunch, photoFailure, photoLaunch, photoMode, photoRequest } from './metadata';
describe('image scan and reconciliation', () => {
    it('picks one job for the chosen options', () => {
        expect(photoMode({ imageScan: false, photoReconcile: false })).toBeNull();
        expect(photoMode({ imageScan: true, photoReconcile: false })).toBe('scan');
        expect(photoMode({ imageScan: false, photoReconcile: true })).toBe('reconcile');
        expect(photoMode({ imageScan: true, photoReconcile: true })).toBe('both');
    });
    it('builds the request for each', () => {
        expect(photoRequest('scan', ['-1'])).toEqual({ url: '/api/photos/scan', body: { channel_ids: ['-1'] } });
        expect(photoRequest('reconcile', ['-1'])).toEqual({ url: '/api/photos/reconcile', body: { channel_ids: ['-1'] } });
        expect(photoRequest('both', ['-1', '-2'])).toEqual({ url: '/api/photos/reconcile', body: { channel_ids: ['-1', '-2'], also_scan: true } });
    });
    it('words failures and launches like classic', () => {
        expect(photoFailure('both', 'busy')).toBe('Image scan + reconciliation could not be queued: busy');
        expect(photoFailure('scan', '')).toBe('Image scan could not be queued: unknown error');
        expect(photoFailure('reconcile', 'x')).toBe('Photo reconciliation could not be queued: x');
        expect(photoLaunch('scan', 'j1', 1)).toBe('Image scan queued as job j1 for 1 selected channel; it scans full Telegram history without modifying messages.');
        expect(photoLaunch('both', 'j', 2)).toMatch(/followed by deleted-image reconciliation queued as job j for 2 selected channels/);
        expect(photoLaunch('reconcile', 'j', 2)).toMatch(/does not rescan channel history/);
        expect(joinLaunch('A', 'B')).toBe('A. B');
        expect(joinLaunch('', 'B')).toBe('B');
    });
});
