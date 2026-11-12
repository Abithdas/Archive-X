import { describe, expect, it } from 'vitest'
import { analysisStatus, analyzeBody, analyzeCopy, assignBody, assignedText, clusterIds, differentBody, firstDetection, groupLabel, jobDone, jobEndText, jobStatusText, mergeBody, mergeCopy, rejectBody, scoreText, suggestionBody, suggestionsFor, viewFilter, type FaceGroup } from './faces'
import { photoParams } from './photos'

const g = (o: Partial<FaceGroup> = {}): FaceGroup => ({ id: 4, label: 'Face group 4', cluster_ids: [4, 9], photo_count: 3, face_count: 5, detection_ids: [11, 12], channel_ids: ['-1001'], ...o })
describe('face groups', () => {
  it('labels and ids', () => {
    expect(groupLabel(g())).toBe('Face group 4'); expect(groupLabel(g({ performer_name: 'Ava' }))).toBe('Ava'); expect(groupLabel({ id: 7 })).toBe('Face group 7')
    expect(clusterIds(g())).toEqual([4, 9]); expect(clusterIds({ id: 4 })).toEqual([4]); expect(clusterIds({ id: -2, cluster_ids: [] })).toEqual([])
    expect(assignedText(g())).toBe('Needs performer review'); expect(assignedText(g({ performer_id: 1, performer_name: 'Ava' }))).toBe('Assigned · Ava'); expect(firstDetection(g())).toBe(11); expect(firstDetection({ id: 1 })).toBe(0)
  })
  it('summarises the analysis and the analyze button', () => {
    expect(analysisStatus([g()], { total_photos: 32, analyzed_photos: 9, failed_photos: 0 }, 0, false)).toMatchObject({ tone: 'success', analyzeDisabled: false, analyzeLabel: 'Find people', text: '9 / 32 photos analyzed in the full indexed library · 1 visual groups. Groups are based on image pixels, not filenames.' })
    expect(analysisStatus([], { total_photos: 0 }, 0, false)).toMatchObject({ analyzeDisabled: true, text: expect.stringMatching(/No indexed photos yet/) })
    expect(analysisStatus([], { total_photos: 5, analyzed_photos: 5 }, 2, false)).toMatchObject({ text: 'Analysis finished, but no faces were detected in this selection.', analyzeLabel: 'Find people again', analyzeDisabled: false })
    expect(analysisStatus([g()], { total_photos: 5, analyzed_photos: 4, failed_photos: 1 }, 2, false)).toMatchObject({ tone: 'error', analyzeLabel: 'Retry failed images', text: expect.stringContaining('1 failed (can retry)') })
    expect(analysisStatus([g()], { total_photos: 5 }, 0, true)).toMatchObject({ analyzeDisabled: true, analyzeLabel: 'Finding people…' })
  })
  it('words the analyze job', () => {
    expect(analyzeCopy(0).text).toMatch(/whole indexed photo library/); expect(analyzeCopy(2).text).toMatch(/2 selected channels/); expect(analyzeBody(['-1'])).toEqual({ channel_ids: ['-1'], match_performers: true })
    expect(jobStatusText('IN_PROGRESS', 41.6, ' x.jpg ')).toBe('Image-based face analysis in progress · 42% · x.jpg'); expect(jobDone('failed')).toBe(true); expect(jobDone('RUNNING')).toBe(false)
    expect(jobEndText('COMPLETED')).toMatch(/complete/); expect(jobEndText('FAILED')).toBe('Face analysis failed. Check Operations for details; failed images can be retried.')
  })
  it('builds merge, assign, suggestion and reject requests', () => {
    expect(mergeCopy(3).text).toMatch(/Merge 3 selected visual clusters/); expect(mergeBody([1, 0, 2])).toEqual({ group_ids: [1, 2] })
    expect(assignBody(g(), 8)).toEqual({ performer_id: 8, group_ids: [4, 9] }); expect(assignBody(g(), null)).toEqual({ performer_id: null, group_ids: [4, 9] })
    expect(suggestionBody(g(), { id: 2, similarity_score: 0.5 }, 'confirmed_assignment', '')).toEqual({ performer_id: 2, group_ids: [4, 9], suggestion_source: 'confirmed_assignment', suggestion_score: 0.5, suggestion_reason: 'Confirmed from a previously assigned visual group' })
    expect(suggestionBody(g(), { id: 2 }, 'metadata', 'caption')).toMatchObject({ suggestion_score: null, suggestion_reason: 'Confirmed from source metadata: caption' })
    expect(rejectBody(g(), { id: 2 }, 'metadata')).toEqual({ group_ids: [4, 9], performer_id: 2, source: 'metadata', similarity_score: null, reason: 'Rejected by user in Image Analysis' })
    expect(scoreText({ id: 1, similarity_score: 0.456 })).toBe('Embedding similarity 0.46 · not a probability'); expect(scoreText({ id: 1 })).toBe('')
    expect(suggestionsFor(g({ performer_id: 3, metadata_suggestions: [{ id: 1 }] }))).toEqual({ confirmed: [], metadata: [], profile: [] })
    expect(suggestionsFor(g({ metadata_suggestions: [1, 2, 3, 4].map((id) => ({ id })) })).metadata).toHaveLength(3)
  })
  it('filters the photo grid by a group or its performer', () => {
    const a = viewFilter(g()); expect(a.filter).toMatchObject({ performerId: null, groupIds: [4, 9], groupId: 4, label: 'Face group 4' }); expect(a.channels).toEqual(['-1001'])
    const b = viewFilter(g({ id: -1, performer_id: 1, performer_name: 'Ava' })); expect(b.filter).toMatchObject({ performerId: 1, groupIds: [] })
    const base = { channels: ['-1'], q: '', sort: 'date_desc' as const, favorites: false, favoriteIds: new Set<string>(), page: 0 }
    expect(photoParams({ ...base, face: a.filter }).get('face_group_ids')).toBe('4,9'); expect(photoParams({ ...base, face: b.filter }).get('face_performer_id')).toBe('1'); expect(photoParams({ ...base, face: { performerId: null, groupIds: [], groupId: 5, label: '' } }).get('face_group_id')).toBe('5')
    expect(photoParams(base).has('face_group_ids')).toBe(false)
  })
  it('builds the different-person request only inside a group gallery', () => {
    const f = viewFilter(g()).filter
    expect(differentBody(7, f, [g()])).toEqual({ photo_id: 7, group_ids: [4, 9] }); expect(differentBody(7, f, [])).toEqual({ photo_id: 7, group_ids: [4, 9] })
    expect(differentBody(0, f, [g()])).toBeNull(); expect(differentBody(7, { performerId: null, groupIds: [], groupId: null, label: '' }, [])).toBeNull()
  })
})

import { candidateLabel, candidatesUrl, loadedText, providerName, reviewSubtitle, saveLabel } from './faces'
describe('reference review', () => {
  it('words the dialog', () => {
    expect(reviewSubtitle('Ava')).toBe("Compare the selected face group with Ava's saved profile and provider images."); expect(reviewSubtitle(null)).toMatch(/Choose a performer/)
    expect(saveLabel('Ava')).toBe('Map group to Ava'); expect(saveLabel(null)).toBe('Map group to selected performer')
    expect(candidatesUrl(4, 10)).toBe('/api/performers/4/image-candidates?limit=10&offset=10')
    expect(providerName({ provider: 'Stash<DB>' })).toBe('StashDB'); expect(providerName({})).toBe('Provider')
    expect(candidateLabel({ provider: 'StashDB', width: 400, height: 600 })).toBe('StashDB · 400×600'); expect(candidateLabel({ provider: 'X' })).toBe('X')
    expect(loadedText([])).toBe('No provider reference images available for this performer.')
    expect(loadedText([{ provider: 'A' }, { provider: 'A' }, { provider: 'B' }])).toBe('3 provider reference image(s) loaded · A: 2 · B: 1. Choose one to inspect, then assign the group manually.')
  })
})
