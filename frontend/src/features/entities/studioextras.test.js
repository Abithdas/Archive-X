import { describe, expect, it } from 'vitest';
import { brandIcon, careerRows, confirmBody, detailRows, metaRecords, metaText, providerBio, relatedList, similarStudios, socialLinks, succeeded, suggestionTitle } from './studioextras';
const provider = (details, extra = {}, name = 'stashdb') => ({ provider: name, normalized: JSON.stringify({ details, ...extra }) });
describe('studio extras', () => {
    it('reads provider records and text values', () => {
        expect(metaText(['a', { name: 'b' }, ''])).toBe('a, b');
        expect(metaText(null)).toBe('');
        expect(metaText({ label: 'L' })).toBe('L');
        expect(metaText(5)).toBe('5');
        expect(metaRecords({ providers: [{ provider: 'X', normalized: '{bad' }, provider({ a: 1 })] })).toHaveLength(2);
        expect(metaRecords(null)).toEqual([]);
        expect(providerBio({ providers: [provider({ description: ' Hello ' }, { summary: 'later' })] })).toBe('Hello');
        expect(providerBio({ providers: [] })).toBe('');
    });
    it('finds social links, skipping provider sites and duplicates, and adds the saved website first', () => {
        const p = { providers: [provider({ instagram: 'https://www.instagram.com/north', social: [{ platform: 'Reddit', url: 'https://reddit.com/r/north' }, 'https://stashdb.org/studios/1'], website: 'https://north.example', twitter: 'not a url' })] };
        const l = socialLinks({ site_url: 'https://official.example' }, p);
        expect(l.map((x) => x.label)).toEqual(['Official website', 'Instagram', 'Reddit', 'Website']);
        expect(l[0].href).toBe('https://official.example/');
        expect(l[1].href).toBe('https://www.instagram.com/north');
        expect(socialLinks({ site_url: 'https://north.example' }, p).filter((x) => x.href.startsWith('https://north.example'))).toHaveLength(1);
        expect(socialLinks({ site_url: 'javascript:alert(1)' }, null)).toEqual([]);
        expect(socialLinks({ profile: 'https://youtube.com/@n' }, null)[0].label).toBe('YouTube');
    });
    it('picks a brand icon', () => { expect(brandIcon('https://x.com/a')).toBe('brand-x'); expect(brandIcon('https://sub.youtube.com/a')).toBe('brand-youtube'); expect(brandIcon('https://example.org')).toBe('brand-website'); expect(brandIcon('nope')).toBe('brand-website'); });
    it('builds the Details rows', () => {
        const site = { id: 1, name: 'North', aliases: '["North", "NL", "N-Light"]', provider_ids: '{"stashdb":"x"}' };
        const p = { providers: [provider({ country: 'US', founded_year: 1999, parent_studio: { name: 'BigCo' } }, {}, 'stashdb')], career_counts: [{ provider: 'StashDB', count: 120 }, { provider: 'stashdb', count: 5 }, { provider: 'Bad', count: -1 }] };
        const rows = detailRows(site, p, 15, 9);
        expect(rows).toEqual([['Country', 'US'], ['Parent studio', 'BigCo'], ['Founded', '1999'], ['Videos in Archive X', '15'], ['Scenes listed by StashDB', '120'], ['Linked performers', '9'], ['Aliases', 'NL · N-Light'], ['Metadata', 'STASHDB']]);
        expect(detailRows({ id: 1, name: 'A' }, null, 0, 0)).toEqual([['Videos in Archive X', '0'], ['Linked performers', '0'], ['Metadata', 'Local profile']]);
        expect(detailRows({ id: 1, name: 'A', provider_ids: { x: 1 } }, null, 1, 1).at(-1)).toEqual(['Metadata', 'Linked']);
        expect(detailRows({ id: 1, name: 'A', provider_ids: '{}' }, null, 1, 1).at(-1)).toEqual(['Metadata', 'Local profile']);
        expect(careerRows({ career_counts: [{ provider: 'A', count: 1.5 }] })).toEqual([]);
    });
    it('keeps only valid related studios', () => { expect(relatedList([{ id: 2, name: 'Sub', relation: 'Child' }, { id: 0, name: 'x' }, { id: 3, name: ' ' }])).toHaveLength(1); expect(relatedList(undefined)).toEqual([]); });
    it('lists similar studios by shared videos, excluding itself', () => {
        const vids = [{ studios: ['North', 'Aurora'] }, { studios: ['Aurora', 'Zed'] }, { studios: ['Zed'] }, { studios: ['Aurora'] }, {}];
        const all = [{ id: 1, name: 'North' }, { id: 2, name: 'Aurora' }, { id: 3, name: 'Zed' }, { id: 4, name: 'Other' }];
        expect(similarStudios(vids, 'north', all).map((x) => [x.name, x.count])).toEqual([['Aurora', 3], ['Zed', 2]]);
        expect(similarStudios(vids, 'North', all, 1)).toHaveLength(1);
    });
    it('words suggestions', () => { expect(suggestionTitle({ suggestion_id: 1, channel_id: 1, message_id: 7 })).toBe('video 7'); expect(suggestionTitle({ suggestion_id: 1, channel_id: 1, message_id: 7, filename: 'f.mp4' })).toBe('f.mp4'); expect(confirmBody(4)).toEqual({ entity_type: 'studio', entity_id: 4 }); expect(succeeded({ ok: false })).toBe(false); expect(succeeded({})).toBe(true); expect(succeeded(null)).toBe(false); });
});
