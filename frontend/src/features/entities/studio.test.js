import { describe, expect, it } from 'vitest';
import { BIO_LIMIT, bioShown, bioText, favLabel, favText, imagePath, initial, linkedPerformers, metadataStatus, plural, providerNames, providerPills, siteLink } from './studio';
describe('counts', () => {
    it('pluralises with thousands separators', () => {
        expect(plural(1, 'video')).toBe('1 video');
        expect(plural(2, 'video')).toBe('2 videos');
        expect(plural(1234, 'performer')).toBe(`${(1234).toLocaleString()} performers`);
        expect(plural(0, 'video')).toBe('0 videos');
    });
    it('lists each performer once, most videos first, ignoring accents and spacing', () => {
        const out = linkedPerformers([{ performers: ['Ava Stone', 'Zoë Park'] }, { performers: ['ava  stone', 'Zoe Park', 'Mia'] }, { performers: ['Ava Stone', 'Ava Stone'] }, { performers: null }, {}]);
        expect(out).toEqual([{ name: 'Ava Stone', count: 3 }, { name: 'Zoë Park', count: 2 }, { name: 'Mia', count: 1 }]);
    });
    it('breaks ties by name and drops blanks', () => {
        expect(linkedPerformers([{ performers: ['B', ' ', 'A'] }]).map((p) => p.name)).toEqual(['A', 'B']);
    });
});
describe('providers', () => {
    it('reads provider ids from JSON text or an object and ignores empty ones', () => {
        expect(providerNames('{"stashdb":"x","tpdb":"","other":null}')).toEqual(['stashdb']);
        expect(providerNames({ tpdb: '7' })).toEqual(['tpdb']);
        expect(providerNames('{broken')).toEqual([]);
        expect(providerNames(null)).toEqual([]);
        expect(providerNames('{}')).toEqual([]);
    });
    it('names and tones the provider pills', () => {
        expect(providerPills({ stashdb: '1', ThePornDB: '2', local_import: '3' })).toEqual([{ label: 'StashDB', tone: 'violet' }, { label: 'ThePornDB', tone: 'accent' }, { label: 'local_import', tone: 'plain' }]);
        expect(providerPills({ tpdb: 'x' })[0].label).toBe('ThePornDB');
    });
    it('says whether metadata is linked', () => {
        expect(metadataStatus({ id: 1, name: 'x', provider_ids: '{"stashdb":"1"}' })).toBe('Metadata linked');
        expect(metadataStatus({ id: 1, name: 'x', provider_ids: '{}' })).toBe('Local profile');
        expect(metadataStatus({ id: 1, name: 'x', provider_ids: null })).toBe('Local profile');
    });
});
describe('profile text and links', () => {
    it('shortens a long bio until expanded', () => {
        const long = 'x'.repeat(BIO_LIMIT + 50);
        expect(bioShown(long, false)).toBe(`${'x'.repeat(BIO_LIMIT)}…`);
        expect(bioShown(long, true)).toBe(long);
        expect(bioShown('short', false)).toBe('short');
        expect(bioText({ id: 1, name: 'x', bio: '  hi  ' })).toBe('hi');
        expect(bioText({ id: 1, name: 'x' })).toBe('');
    });
    it('only links to http(s) websites', () => {
        expect(siteLink({ id: 1, name: 'x', site_url: 'https://studio.test' })).toBe('https://studio.test');
        expect(siteLink({ id: 1, name: 'x', url: 'javascript:alert(1)' })).toBe('');
        expect(siteLink({ id: 1, name: 'x' })).toBe('');
    });
    it('builds the logo path and the fallback letter', () => {
        expect(imagePath(3, 'a b')).toBe('/api/studios/3/image?v=a%20b');
        expect(imagePath(3, null)).toBe('/api/studios/3/image?v=');
        expect(initial(' northlight')).toBe('N');
        expect(initial('')).toBe('?');
    });
    it('words the favourite button', () => {
        expect(favLabel(true)).toBe('Remove from favourites');
        expect(favLabel(false)).toBe('Add to favourites');
        expect(favText(true)).toBe('Favourite');
        expect(favText(false)).toBe('Add to favourites');
    });
});
