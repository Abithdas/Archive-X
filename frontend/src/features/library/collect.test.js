import { afterEach, describe, expect, it, vi } from 'vitest';
import { collectLibrary, pageCount } from './collect';
afterEach(() => vi.unstubAllGlobals());
const json = (b) => new Response(JSON.stringify(b), { status: 200 });
const query = { channels: ['-1'], sort: 'date_desc', q: '', favorites: false };
describe('collectLibrary', () => {
    it('counts pages from the total at 48 per page', () => {
        expect(pageCount(0)).toBe(1);
        expect(pageCount(48)).toBe(1);
        expect(pageCount(49)).toBe(2);
        expect(pageCount(500)).toBe(11);
    });
    it('gathers every page in order and reports progress', async () => {
        const TOTAL = 130; // 3 pages: 48 + 48 + 34
        const requested = [];
        vi.stubGlobal('fetch', vi.fn(async (url) => {
            const u = new URL(url, 'http://x');
            const page = Number(u.searchParams.get('page'));
            requested.push(page);
            expect(u.searchParams.get('per')).toBe('48');
            expect(u.searchParams.get('channels')).toBe('-1');
            const n = Math.min(48, TOTAL - page * 48);
            return json({ total: TOTAL, page, per: 48, items: Array.from({ length: n }, (_, i) => ({ id: page * 48 + i, _cid: '-1' })) });
        }));
        const progress = [];
        const items = await collectLibrary(query, (n) => progress.push(n));
        expect(items).toHaveLength(TOTAL);
        expect(items.map((i) => i.id)).toEqual(Array.from({ length: TOTAL }, (_, i) => i)); // order preserved even though pages 1-2 load together
        expect([...requested].sort()).toEqual([0, 1, 2]);
        expect(progress).toEqual([48, 130]);
    });
    it('fetches only one page when everything fits', async () => {
        const f = vi.fn(async () => json({ total: 3, page: 0, per: 48, items: [{ id: 1 }, { id: 2 }, { id: 3 }] }));
        vi.stubGlobal('fetch', f);
        expect(await collectLibrary(query)).toHaveLength(3);
        expect(f).toHaveBeenCalledTimes(1);
    });
    it('fails loudly if a page cannot be loaded', async () => {
        let n = 0;
        vi.stubGlobal('fetch', vi.fn(async () => (++n === 1 ? json({ total: 100, page: 0, per: 48, items: [] }) : new Response('{"detail":"boom"}', { status: 500 }))));
        await expect(collectLibrary(query)).rejects.toThrow('boom');
    });
});
