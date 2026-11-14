import { afterEach, beforeEach, describe, expect, it } from 'vitest';
import { clearImageCache, firstLoaded, isCached, loadImage } from './imageCache';
let made = [];
const Real = globalThis.Image;
beforeEach(() => {
    made = [];
    clearImageCache();
    globalThis.Image = class {
        onload;
        onerror;
        decoding = '';
        _src = '';
        constructor() { made.push(this); }
        set src(v) { this._src = v; }
        get src() { return this._src; }
        decode() { return Promise.resolve(); }
    };
});
afterEach(() => { globalThis.Image = Real; });
const finish = (i, ok = true) => (ok ? made[i].onload?.() : made[i].onerror?.());
describe('viewer image cache', () => {
    it('fetches a url once, however many times it is asked for', async () => {
        const a = loadImage('/a'), b = loadImage('/a');
        expect(made).toHaveLength(1);
        finish(0);
        expect(await a).toBe(await b);
        expect(isCached('/a')).toBe(true);
    });
    it('forgets a failed url so it can be retried', async () => {
        const p = loadImage('/bad');
        finish(0, false);
        await expect(p).rejects.toThrow();
        await Promise.resolve();
        expect(isCached('/bad')).toBe(false);
    });
    it('firstLoaded gives whichever loads first', async () => {
        const p = firstLoaded(['/preview', '/original']);
        finish(1);
        expect((await p).url).toBe('/original');
    });
    it('firstLoaded still succeeds when one of them fails', async () => {
        const p = firstLoaded(['/preview', '/original']);
        finish(0, false);
        finish(1);
        expect((await p).url).toBe('/original');
    });
    it('keeps at most 24 images in memory', () => {
        for (let i = 0; i < 40; i++)
            loadImage(`/p${i}`);
        expect(isCached('/p0')).toBe(false);
        expect(isCached('/p39')).toBe(true);
    });
});
