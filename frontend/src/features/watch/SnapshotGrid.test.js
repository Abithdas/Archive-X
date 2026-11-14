import { jsx as _jsx } from "react/jsx-runtime";
import { act, fireEvent, render, screen } from '@testing-library/react';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { Player } from './Player';
import { SnapshotGrid } from './SnapshotGrid';
const TIMES = Array.from({ length: 36 }, (_, i) => i * 100);
const data = (over = {}) => ({ times: TIMES, spriteUrl: null, frameUrl: (i) => `/frame/${i}`, aspect: 16 / 9, ...over });
let images = [];
beforeEach(() => {
    images = [];
    vi.stubGlobal('ResizeObserver', class {
        observe() { }
        disconnect() { }
        unobserve() { }
    });
    vi.stubGlobal('Image', class {
        onload;
        onerror;
        src = '';
        naturalWidth = 1920;
        naturalHeight = 1080;
        decoding = '';
        constructor() { images.push(this); }
    });
    Object.defineProperty(HTMLElement.prototype, 'clientWidth', { configurable: true, get: () => 960 });
    Object.defineProperty(HTMLElement.prototype, 'clientHeight', { configurable: true, get: () => 540 });
});
afterEach(() => { vi.unstubAllGlobals(); delete HTMLElement.prototype.clientWidth; delete HTMLElement.prototype.clientHeight; });
describe('SnapshotGrid', () => {
    it('shows 36 snapshot buttons, each named by its time, and starts from the one chosen', () => {
        const onPick = vi.fn();
        render(_jsx(SnapshotGrid, { data: data(), onPick: onPick }));
        const tiles = screen.getAllByRole('button');
        expect(tiles).toHaveLength(36);
        expect(tiles[0]).toHaveAccessibleName('Play from 0:00');
        expect(tiles[5]).toHaveAccessibleName('Play from 8:20');
        fireEvent.click(tiles[5]);
        expect(onPick).toHaveBeenCalledWith(500);
        expect(screen.getByRole('group', { name: /Video snapshots/ })).toBeInTheDocument();
    });
    it('uses the individual frames when there is no sprite', () => {
        const { container } = render(_jsx(SnapshotGrid, { data: data(), onPick: () => { } }));
        const imgs = container.querySelectorAll('img');
        expect(imgs).toHaveLength(36);
        expect(imgs[3].getAttribute('src')).toBe('/frame/3');
    });
    it('with a sprite, stays invisible until it has loaded, then crops each tile from it', () => {
        const { container } = render(_jsx(SnapshotGrid, { data: data({ spriteUrl: '/sprite.jpg' }), onPick: () => { } }));
        const group = screen.getByRole('group', { hidden: true });
        expect(group.className).toContain('opacity-0');
        expect(container.querySelectorAll('img')).toHaveLength(0);
        act(() => images[0].onload?.());
        expect(group.className).toContain('opacity-100');
        const tile = screen.getAllByRole('button')[7]; // frame 7: column 1, row 1 of the 6×6 sprite
        expect(tile.style.backgroundImage).toContain('/sprite.jpg');
        expect(tile.style.backgroundPosition).toMatch(/^-?\d/);
    });
    it('falls back to the individual frames if the sprite cannot be loaded', () => {
        const { container } = render(_jsx(SnapshotGrid, { data: data({ spriteUrl: '/sprite.jpg' }), onPick: () => { } }));
        act(() => images[0].onerror?.());
        expect(container.querySelectorAll('img')).toHaveLength(36);
    });
    it('is keyboard operable', () => {
        const onPick = vi.fn();
        render(_jsx(SnapshotGrid, { data: data(), onPick: onPick }));
        const tile = screen.getAllByRole('button')[2];
        tile.focus();
        expect(tile).toHaveFocus();
        fireEvent.click(tile); // Enter / Space on a button become click
        expect(onPick).toHaveBeenCalledWith(200);
    });
});
describe('Player with snapshots', () => {
    const stubPlay = (v) => { const play = vi.fn(() => Promise.resolve()); v.play = play; return play; };
    const metadata = (v, duration) => { Object.defineProperty(v, 'duration', { value: duration, configurable: true }); fireEvent.loadedMetadata(v); };
    it('covers the video before playback and hides the big play button', () => {
        const { container } = render(_jsx(Player, { src: "/stream/-1/1", durationHint: 3600, snapshots: data(), onOpenVlc: () => { } }));
        fireEvent.canPlay(container.querySelector('video')); // not loading any more: the big play button would normally show
        expect(screen.getByRole('group', { name: /Video snapshots/ })).toBeInTheDocument();
        expect(container.querySelector('button.size-16')).toBeNull();
    });
    it('starts playing from the tile that was chosen', () => {
        const { container } = render(_jsx(Player, { src: "/stream/-1/1", durationHint: 3600, snapshots: data(), onOpenVlc: () => { } }));
        const v = container.querySelector('video');
        const play = stubPlay(v);
        metadata(v, 3600);
        Object.defineProperty(v, 'readyState', { value: 1, configurable: true });
        fireEvent.click(screen.getByRole('button', { name: 'Play from 16:40' }));
        expect(v.currentTime).toBe(1000);
        expect(play).toHaveBeenCalled();
    });
    it('waits for the video to know its length before seeking, then seeks once', () => {
        const { container } = render(_jsx(Player, { src: "/stream/-1/1", durationHint: 3600, snapshots: data(), onOpenVlc: () => { } }));
        const v = container.querySelector('video');
        const play = stubPlay(v);
        Object.defineProperty(v, 'readyState', { value: 0, configurable: true });
        fireEvent.click(screen.getByRole('button', { name: 'Play from 8:20' }));
        expect(play).toHaveBeenCalled();
        expect(v.currentTime).toBe(0);
        metadata(v, 3600);
        expect(v.currentTime).toBe(500);
    });
    it('goes away once playback has started, and is not shown when the video failed', () => {
        const { container } = render(_jsx(Player, { src: "/stream/-1/1", durationHint: 3600, snapshots: data(), onOpenVlc: () => { } }));
        const v = container.querySelector('video');
        fireEvent.play(v);
        expect(screen.queryByRole('group', { name: /Video snapshots/ })).toBeNull();
    });
    it('does not show for a failed stream', () => {
        const { container } = render(_jsx(Player, { src: "/stream/-1/1", durationHint: 3600, snapshots: data(), onOpenVlc: () => { } }));
        fireEvent.error(container.querySelector('video'));
        expect(screen.queryByRole('group', { name: /Video snapshots/ })).toBeNull();
    });
    it('without snapshots the player is unchanged (big play button, no grid)', () => {
        const { container } = render(_jsx(Player, { src: "/stream/-1/1", durationHint: 3600, onOpenVlc: () => { } }));
        fireEvent.canPlay(container.querySelector('video'));
        expect(screen.queryByRole('group', { name: /Video snapshots/ })).toBeNull();
        expect(screen.getAllByRole('button', { name: 'Play' }).some((b) => b.className.includes('size-12'))).toBe(true); // the big round play button
    });
});
