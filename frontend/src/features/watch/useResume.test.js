import { jsx as _jsx, jsxs as _jsxs } from "react/jsx-runtime";
import { act, fireEvent, render, screen, waitFor } from '@testing-library/react';
import { afterEach, beforeAll, beforeEach, describe, expect, it, vi } from 'vitest';
import { markPlayed, resumeStorageKey, saveStored } from './resume';
import { useResume } from './useResume';
beforeAll(() => {
    HTMLDialogElement.prototype.showModal = function () { this.setAttribute('open', ''); };
    HTMLDialogElement.prototype.close = function () { this.removeAttribute('open'); };
});
beforeEach(() => localStorage.clear());
afterEach(() => { vi.unstubAllGlobals(); vi.useRealTimers(); });
const BASE = { cid: '-1', mid: 7, name: 'Scene', duration: 3600, enabled: true, settled: true, autoplay: true };
let api;
function Harness(o = {}) {
    api = useResume({ ...BASE, ...o });
    return _jsxs("div", { children: [_jsx("span", { "data-testid": "state", children: JSON.stringify({ ready: api.ready, startAt: api.startAt, autoPlay: api.autoPlay }) }), api.dialog] });
}
const state = () => JSON.parse(screen.getByTestId('state').textContent);
const seed = (pos = 300) => { markPlayed('-1', 7); saveStored('-1', 7, pos); };
const stubFetch = () => {
    const calls = [];
    vi.stubGlobal('fetch', vi.fn(async (url, init) => { calls.push({ url, body: JSON.parse(String(init?.body ?? '{}')) }); return new Response('{"ok":true}', { status: 200 }); }));
    return calls;
};
describe('resume prompt', () => {
    it('asks before playing when there is a meaningful saved position', () => {
        seed(300);
        render(_jsx(Harness, {}));
        expect(screen.getByRole('dialog', { name: 'Scene' })).toHaveTextContent('Resume from 5:00?');
        expect(screen.getByRole('dialog')).toHaveTextContent('Enter resume · Esc cancel');
        expect(state().ready).toBe(false); // the player is held back until the viewer chooses
    });
    it('focuses Resume, so Enter means resume (as the dialog says)', () => {
        seed(300);
        render(_jsx(Harness, {}));
        expect(screen.getByRole('button', { name: 'Resume' })).toHaveFocus();
    });
    it('Resume starts at the saved position and plays', () => {
        seed(300);
        render(_jsx(Harness, {}));
        fireEvent.click(screen.getByRole('button', { name: 'Resume' }));
        expect(state()).toEqual({ ready: true, startAt: 300, autoPlay: true });
        expect(screen.queryByRole('dialog')).toBeNull();
    });
    it('Start over plays from the beginning', () => {
        seed(300);
        render(_jsx(Harness, {}));
        fireEvent.click(screen.getByRole('button', { name: 'Start over' }));
        expect(state()).toEqual({ ready: true, startAt: 0, autoPlay: true });
    });
    it('Cancel leaves the video paused at the start', () => {
        seed(300);
        render(_jsx(Harness, {}));
        fireEvent.click(screen.getByRole('button', { name: 'Cancel' }));
        expect(state()).toEqual({ ready: true, startAt: 0, autoPlay: false });
    });
    it('shows the preview frame at the saved time when one is available, else says it is unavailable', () => {
        seed(300);
        const { unmount } = render(_jsx(Harness, { frameFor: (t) => `/frame/${t}` }));
        expect(screen.getByRole('img', { name: 'Scene at 5:00' })).toHaveAttribute('src', '/frame/300');
        unmount();
        render(_jsx(Harness, {}));
        expect(screen.getByText('Preview unavailable at the saved position')).toBeInTheDocument();
    });
    it.each([
        ['the setting is off', { enabled: false }, () => seed(300)],
        ['it is not the last-played video', {}, () => saveStored('-1', 7, 300)],
        ['the position is under 30 seconds', {}, () => seed(29)],
        ['the position is in the last 20 seconds', {}, () => seed(3590)],
    ])('does not ask when %s', (_n, over, arrange) => {
        arrange();
        render(_jsx(Harness, { ...over }));
        expect(screen.queryByRole('dialog')).toBeNull();
        expect(state()).toEqual({ ready: true, startAt: 0, autoPlay: true }); // plays normally
    });
    it('waits for the settings and video to load before deciding', () => {
        seed(300);
        render(_jsx(Harness, { settled: false }));
        expect(state().ready).toBe(false);
        expect(screen.queryByRole('dialog')).toBeNull();
    });
    it('respects autoplay being off when there is no prompt', () => {
        render(_jsx(Harness, { autoplay: false }));
        expect(state()).toEqual({ ready: true, startAt: 0, autoPlay: false });
    });
});
describe('saving progress', () => {
    it('marks the video as last played, saves the position locally and reports watch time to the server', async () => {
        vi.useFakeTimers({ toFake: ['Date'] });
        vi.setSystemTime(100_000);
        const calls = stubFetch();
        render(_jsx(Harness, {}));
        act(() => api.onPlayed());
        act(() => api.onTime(120, 3600));
        expect(localStorage.getItem(resumeStorageKey('-1', 7))).toBe('120');
        await waitFor(() => expect(calls).toHaveLength(1));
        expect(calls[0]).toEqual({ url: '/api/play/progress', body: { channel_id: '-1', message_id: 7, position: 120, duration: 3600, watch_delta: 0 } });
    });
    it('throttles to once every five seconds, and counts only normal playback as watch time', async () => {
        vi.useFakeTimers({ toFake: ['Date'] });
        vi.setSystemTime(100_000);
        const calls = stubFetch();
        render(_jsx(Harness, {}));
        act(() => api.onPlayed());
        act(() => api.onTime(100, 3600));
        vi.setSystemTime(102_000);
        act(() => api.onTime(102, 3600));
        await waitFor(() => expect(calls).toHaveLength(1)); // second call was within 5 s
        vi.setSystemTime(106_000);
        act(() => api.onTime(106, 3600));
        await waitFor(() => expect(calls).toHaveLength(2));
        expect(calls[1].body.watch_delta).toBe(6);
        vi.setSystemTime(112_000);
        act(() => api.onTime(2000, 3600)); // a seek
        await waitFor(() => expect(calls).toHaveLength(3));
        expect(calls[2].body.watch_delta).toBe(0);
    });
    it('does not save a position for a video that was not the last played', () => {
        vi.useFakeTimers({ toFake: ['Date'] });
        vi.setSystemTime(100_000);
        stubFetch();
        render(_jsx(Harness, {}));
        act(() => api.onTime(120, 3600));
        expect(localStorage.getItem(resumeStorageKey('-1', 7))).toBeNull();
    });
    it('does not save the first 20 seconds or the last 25 seconds', () => {
        vi.useFakeTimers({ toFake: ['Date'] });
        vi.setSystemTime(100_000);
        stubFetch();
        render(_jsx(Harness, {}));
        act(() => api.onPlayed());
        act(() => api.onTime(10, 3600));
        expect(localStorage.getItem(resumeStorageKey('-1', 7))).toBeNull();
        vi.setSystemTime(110_000);
        act(() => api.onTime(3590, 3600));
        expect(localStorage.getItem(resumeStorageKey('-1', 7))).toBeNull();
    });
    it('finishing clears the saved position and tells the server it was watched to the end', async () => {
        const calls = stubFetch();
        seed(300);
        render(_jsx(Harness, { enabled: false }));
        act(() => api.onEnd());
        expect(localStorage.getItem(resumeStorageKey('-1', 7))).toBeNull();
        expect(localStorage.getItem('ta_last_played_video')).toBeNull();
        await waitFor(() => expect(calls).toHaveLength(1));
        expect(calls[0].body).toEqual({ channel_id: '-1', message_id: 7, position: 3600, duration: 3600, watch_delta: 0 });
    });
});
