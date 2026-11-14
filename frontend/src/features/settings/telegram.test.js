import { describe, expect, it } from 'vitest';
import { EMPTY_PROXY, PORT_HINT, accountLine, apiStatus, codeSent, credentialsReady, normalizePhone, proxyFields, proxyForm, proxyPayload, proxyPlaceholders, proxyStatus, qrView, verifyOutcome } from './telegram';
describe('credentials', () => {
    it('shows verified, saved or not verified', () => {
        expect(apiStatus({ api_verified: true })).toEqual({ text: 'API credentials verified', tone: 'ok' });
        expect(apiStatus({ configured: true }).text).toContain('saved');
        expect(apiStatus({})).toEqual({ text: 'API credentials not verified', tone: 'warn' });
    });
    it('needs an API ID and a typed or already saved hash', () => {
        expect(credentialsReady('', 'h', true)).toBe(false);
        expect(credentialsReady('123', '', false)).toBe(false);
        expect(credentialsReady('123', '', true)).toBe(true);
        expect(credentialsReady(' 123 ', ' abc ', false)).toBe(true);
    });
    it('describes the account state', () => {
        expect(accountLine({ connected: true })).toContain('Signed in');
        expect(accountLine({ configured: true })).toContain('choose a sign-in method');
        expect(accountLine({})).toContain('add API credentials');
    });
});
describe('proxy', () => {
    it('shows only the fields a type needs', () => {
        expect(proxyFields('direct')).toEqual({ host: false, port: false, auth: false, secret: false });
        expect(proxyFields('socks5')).toEqual({ host: true, port: true, auth: true, secret: false });
        expect(proxyFields('mtproto')).toEqual({ host: true, port: true, auth: false, secret: true });
        expect(PORT_HINT.http).toBe('8080');
    });
    it('words every status like the classic page', () => {
        expect(proxyStatus({ status: 'direct' }).label).toBe('Direct connection');
        expect(proxyStatus({ status: 'configured', type: 'socks5' }).label).toBe('SOCKS5 configured');
        expect(proxyStatus({ status: 'configured', source: 'environment' })).toMatchObject({ label: 'Managed by environment', locked: true });
        expect(proxyStatus({ status: 'configured', restart_required: true }).label).toBe('Restart required');
        expect(proxyStatus({ status: 'invalid' }).label).toBe('Proxy setup needs attention');
        expect(proxyStatus(undefined).label).toBe('Status unavailable');
    });
    it('loads the saved endpoint but never credentials, and sends blanks to keep them', () => {
        expect(proxyForm({ type: 'http', host: 'h', port: 8080, password: 'LEAK' })).toEqual({ ...EMPTY_PROXY, type: 'http', host: 'h', port: '8080' });
        expect(proxyForm({ type: 'nonsense' }).type).toBe('direct');
        expect(proxyPayload({ ...EMPTY_PROXY, type: 'socks5', host: 'h', port: '1080', clearAuth: true })).toEqual({ type: 'socks5', host: 'h', port: '1080', username: '', password: '', secret: '', clear_auth: true, clear_secret: false });
        expect(proxyPlaceholders({ password_saved: true }).password).toBe('Saved password — leave blank to keep');
        expect(proxyPlaceholders({}).username).toBe('Username (optional)');
    });
});
describe('QR login', () => {
    it('maps each server state', () => {
        expect(qrView({ status: 'AUTHORIZED' })).toMatchObject({ kind: 'connected' });
        expect(qrView({ connected: true })).toMatchObject({ kind: 'connected' });
        expect(qrView({ status: 'WAITING_FOR_SCAN', qr_image: 'data:image/png;base64,AA' })).toEqual({ kind: 'waiting', message: 'Waiting for scan…', image: 'data:image/png;base64,AA' });
        expect(qrView({ status: 'PASSWORD_REQUIRED' }).kind).toBe('password');
        expect(qrView({ two_factor_required: true }).kind).toBe('password');
        expect(qrView({ status: 'CANCELLED' })).toMatchObject({ kind: 'idle', show: true, tone: 'info' });
        expect(qrView({ status: 'IDLE' })).toMatchObject({ kind: 'idle', show: false });
        expect(qrView({ status: 'CONFIG_REQUIRED', message: 'Add credentials' })).toMatchObject({ kind: 'idle', tone: 'warning', message: 'Add credentials' });
        expect(qrView({ status: 'ERROR' }).kind).toBe('error');
        expect(qrView({ status: 'SOMETHING', message: 'hi' })).toEqual({ kind: 'info', message: 'hi' });
    });
});
describe('phone login', () => {
    it('cleans the number and rejects one without a country code', () => {
        expect(normalizePhone('+1 (415) 555-0123')).toEqual({ phone: '+14155550123', error: '' });
        expect(normalizePhone('4155550123').error).toContain('international format');
        expect(normalizePhone('   ').error).toBe('Enter your phone number (+countrycode).');
        expect(normalizePhone('+12').error).toContain('international format');
    });
    it('recognises a sent code in any of the reply shapes', () => {
        expect([codeSent({ ok: true }), codeSent({ phone_code_hash: 'x' }), codeSent({ status: 'CODE_SENT' }), codeSent({ code_sent: true }), codeSent({})]).toEqual([true, true, true, true, false]);
    });
    it('turns a verify reply into done, password or error', () => {
        expect(verifyOutcome({ ok: true })).toEqual({ kind: 'done' });
        expect(verifyOutcome({ ok: true, two_factor_required: true })).toEqual({ kind: 'password' });
        expect(verifyOutcome({ ok: false, detail: 'Invalid code' })).toEqual({ kind: 'error', message: 'Invalid code' });
        expect(verifyOutcome({})).toEqual({ kind: 'error', message: 'Verification failed' });
    });
});
