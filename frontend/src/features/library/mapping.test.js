import { describe, expect, it } from 'vitest';
import { choiceLabel, choiceNote, failureMessage, mappingBody, mappingTasks, pickedFrom, studioChoices } from './mapping';
describe('studio search choices', () => {
    const hits = [{ id: 1, name: 'Northlight', video_count: 15 }, { id: 2, name: 'North Star', video_count: 1 }];
    it('is empty until something is typed', () => {
        expect(studioChoices(hits, '  ')).toEqual([]);
    });
    it('can list studios before anything is typed (bulk pickers), without a create row', () => {
        expect(studioChoices(hits, '', { listWhenEmpty: true }).map((c) => [c.name, c.create])).toEqual([['Northlight', false], ['North Star', false]]);
        expect(studioChoices(hits, '  ', { listWhenEmpty: true }).some((c) => c.create)).toBe(false);
    });
    it('adds a create row unless the name matches exactly, ignoring case', () => {
        expect(studioChoices(hits, 'north').map((c) => c.create)).toEqual([false, false, true]);
        expect(studioChoices(hits, 'NORTHLIGHT').some((c) => c.create)).toBe(false);
        expect(studioChoices([], 'Fresh').map((c) => [c.name, c.id, c.create])).toEqual([['Fresh', null, true]]);
    });
    it('labels rows like the classic picker', () => {
        const [a, , create] = studioChoices(hits, 'north');
        expect(choiceLabel(a)).toBe('Northlight');
        expect(choiceNote(a)).toBe('15 videos · link to this studio');
        expect(choiceNote(studioChoices(hits, 'north')[1])).toBe('1 video · link to this studio');
        expect(choiceLabel(create)).toBe('Create new studio “north”');
        expect(choiceNote(create)).toBe('');
        expect(pickedFrom({ name: ' Fresh ', id: null, count: 0, create: true })).toEqual({ name: 'Fresh', id: null });
    });
});
describe('mapping tasks', () => {
    const base = { title: 'Scene', originalTitle: 'Scene', studio: { name: 'Northlight', id: 1 }, originalStudio: 'Northlight' };
    it('sends nothing when nothing changed', () => {
        expect(mappingTasks(base)).toEqual([]);
        expect(mappingTasks({ ...base, title: '  Scene ' })).toEqual([]);
    });
    it('sends only the title when only the title changed, locked', () => {
        expect(mappingTasks({ ...base, title: 'New title' })).toEqual([{ field: 'title', target_name: 'New title', action: 'move', lock: true }]);
    });
    it('never sends an empty title', () => {
        expect(mappingTasks({ ...base, title: '   ' })).toEqual([]);
    });
    it('sends the studio with its id, or without one for a new studio', () => {
        expect(mappingTasks({ ...base, studio: { name: 'North Star', id: 2 } })).toEqual([{ field: 'site', site_id: 2, target_name: 'North Star', action: 'move', lock: true }]);
        expect(mappingTasks({ ...base, studio: { name: 'Fresh', id: null } })[0]).toMatchObject({ site_id: null, target_name: 'Fresh' });
    });
    it('treats removing the studio as an explicit change, but not when there was none', () => {
        expect(mappingTasks({ ...base, studio: null })).toEqual([{ field: 'site', site_id: null, target_name: '', action: 'move', lock: true }]);
        expect(mappingTasks({ ...base, studio: null, originalStudio: '' })).toEqual([]);
    });
    it('title first, then studio, in the order the classic dialog saves them', () => {
        expect(mappingTasks({ ...base, title: 'X', studio: null }).map((t) => t.field)).toEqual(['title', 'site']);
    });
    it('wraps a task for one video', () => {
        expect(mappingBody({ _cid: '-100', id: 7 }, { field: 'title', target_name: 'X', action: 'move', lock: true })).toEqual({ videos: [{ channel_id: '-100', message_id: 7 }], field: 'title', target_name: 'X', action: 'move', lock: true });
    });
    it('reports what was already saved when a later request fails', () => {
        expect(failureMessage('Boom', [])).toBe('Boom');
        expect(failureMessage('', [])).toBe('Could not save mapping.');
        expect(failureMessage('Boom', ['title'])).toBe('Boom (Already saved: title.)');
    });
});
