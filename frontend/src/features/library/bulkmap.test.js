import { describe, expect, it } from 'vitest';
import { canSave, comboDone, comboSteps, heading, performerBody, performerChoices, performerLinked, performerNote, saveProblem, studioBody, studioLinked, titleOnlyDone, wantsProviderSearch } from './bulkmap';
const picked = [{ channel_id: '-1', message_id: 1 }, { channel_id: '-1', message_id: 2 }];
const local = (name, id) => ({ name, id, count: 3, image: null, kind: 'local' });
const api = (name, pid) => ({ name, id: null, count: 0, image: 'i.png', kind: 'provider', provider: 'stashdb', provider_id: pid, profile: 'p', exact: true });
const studio = { name: 'Northlight', id: 1, count: 15, create: false };
const newStudio = { name: 'Fresh', id: null, count: 0, create: true };
describe('performer choices', () => {
    it('lists local performers, then provider matches that have an id, without repeats', () => {
        const out = performerChoices([{ id: 1, name: 'Ava', video_count: 4 }], [
            { name: 'Ava S', provider: 'stashdb', provider_id: 'x1', image: 'a.png', exact: true }, { name: 'dup', provider: 'stashdb', provider_id: 'x1' },
            { name: 'No id', provider: 'tpdb' }, { provider: 'tpdb', provider_id: 'y' }, { name: 'Zed', provider: 'tpdb', provider_id: 'z', alias_exact: true },
        ]);
        expect(out.map((c) => [c.kind, c.name])).toEqual([['local', 'Ava'], ['provider', 'Ava S'], ['provider', 'Zed']]);
        expect(out[0]).toMatchObject({ id: 1, count: 4 });
        expect(out[1]).toMatchObject({ id: null, provider: 'stashdb', provider_id: 'x1' });
    });
    it('caps the list at 28 rows', () => {
        expect(performerChoices(Array.from({ length: 40 }, (_, i) => ({ id: i, name: `P${i}` })), [])).toHaveLength(28);
    });
    it('describes each row like the classic picker', () => {
        expect(performerNote(local('Ava', 1))).toBe('Local performer · 3 videos · link to this performer');
        expect(performerNote({ ...local('Ava', 1), count: 1 })).toBe('Local performer · 1 video · link to this performer');
        expect(performerNote(api('X', '1'))).toBe('API match · StashDB · exact name');
        expect(performerNote({ ...api('X', '1'), exact: false, alias_exact: true, provider: 'tpdb' })).toBe('API match · ThePornDB · alias match');
    });
    it('starts the provider search at two characters', () => {
        expect(wantsProviderSearch('a')).toBe(false);
        expect(wantsProviderSearch(' av ')).toBe(true);
    });
});
describe('single-field requests', () => {
    it('links a local performer by id, and a provider match with its provider identity', () => {
        expect(performerBody(picked, local('Ava', 7))).toEqual({ videos: picked, field: 'performer', performer_id: 7, target_name: 'Ava', provider: null, provider_id: null, image: null, profile: null, action: 'move', lock: true });
        expect(performerBody(picked, api('Ava S', 'x1'))).toMatchObject({ performer_id: null, provider: 'stashdb', provider_id: 'x1', image: 'i.png', profile: 'p' });
    });
    it('links a studio by id, or creates it with a null id', () => {
        expect(studioBody(picked, studio)).toEqual({ videos: picked, field: 'site', site_id: 1, target_name: 'Northlight', action: 'move', lock: true });
        expect(studioBody(picked, newStudio)).toMatchObject({ site_id: null, target_name: 'Fresh' });
    });
    it('words the results', () => {
        expect(performerLinked('Ava', 2)).toBe('Ava linked to 2 selected videos.');
        expect(performerLinked('Ava', 1)).toBe('Ava linked to 1 selected video.');
        expect(studioLinked('Northlight', 1)).toBe('Northlight linked to 1 video.');
    });
});
describe('combined mapping', () => {
    it('performer + studio needs both; apply-all needs at least one thing', () => {
        expect(canSave('both', { title: '', performer: local('A', 1), studio: null })).toBe(false);
        expect(canSave('both', { title: 'x', performer: null, studio: null })).toBe(false);
        expect(canSave('both', { title: '', performer: local('A', 1), studio })).toBe(true);
        expect(canSave('all', { title: '', performer: null, studio: null })).toBe(false);
        expect(canSave('all', { title: ' t ', performer: null, studio: null })).toBe(true);
        expect(canSave('all', { title: '', performer: null, studio })).toBe(true);
        expect(saveProblem('both')).toBe('Select both a performer and a studio.');
        expect(saveProblem('all')).toBe('Enter a title, or choose a performer or a studio.');
    });
    it('sends one performer_site request when both are chosen', () => {
        const steps = comboSteps(picked, { title: '', performer: local('Ava', 7), studio });
        expect(steps).toHaveLength(1);
        expect(steps[0].body).toMatchObject({ field: 'performer_site', action: 'move', lock: true, target_name: 'Ava', performer_id: 7, performer_name: 'Ava', site_id: 1, studio_name: 'Northlight' });
        expect(steps[0].failure).toBe('Combined mapping failed.');
    });
    it('applies a title first, then only the part that was chosen', () => {
        const steps = comboSteps(picked, { title: ' New ', performer: null, studio });
        expect(steps.map((s) => s.kind)).toEqual(['title', 'mapping']);
        expect(steps[0].body).toEqual({ videos: picked, field: 'title', target_name: 'New', action: 'move', lock: true });
        expect(steps[1].body).toMatchObject({ field: 'site', target_name: 'Northlight', site_id: 1 });
        expect(steps[1].body).not.toHaveProperty('performer_id');
        expect(comboSteps(picked, { title: '', performer: local('A', 1), studio: null })[0].body).toMatchObject({ field: 'performer' });
    });
    it('a title alone is a single title step', () => {
        expect(comboSteps(picked, { title: 'Only', performer: null, studio: null }).map((s) => s.kind)).toEqual(['title']);
        expect(comboSteps(picked, { title: ' ', performer: null, studio: null })).toEqual([]);
    });
    it('words the outcome', () => {
        expect(comboDone(2, false, 'performer_site')).toBe('2 videos mapped to performer + studio.');
        expect(comboDone(1, false, 'site')).toBe('1 video mapped to studio.');
        expect(comboDone(3, true, 'performer')).toBe('3 videos updated.');
        expect(titleOnlyDone(2)).toBe('Title applied to 2 videos.');
        expect(heading('both')).toBe('Map performer + studio');
        expect(heading('all')).toBe('Apply metadata');
    });
});
