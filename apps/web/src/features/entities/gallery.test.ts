import { describe, expect, it } from 'vitest'
import { GALLERY_PROVIDER_FAILED, galleryStatus, galleryTabCount, lastSyncText, providerName, referenceNote } from './gallery'

const item = (over = {}) => ({ id: 1, provider: 'stashdb', width: 300, height: 400, faces: 1, reference: true, source: 'provider', url: '/x', ...over })

describe('gallery wording', () => {
  it('counts pictures, the ones used for matching and the removed ones', () => {
    expect(galleryStatus({ items: [], references: 0, removed: 0 })).toBe('0 pictures')
    expect(galleryStatus({ items: [item()], references: 1, removed: 0 })).toBe('1 picture · 1 used for face matching')
    expect(galleryStatus({ items: [item(), item({ id: 2 })], references: 1, removed: 3 })).toBe('2 pictures · 1 used for face matching · 3 removed')
    expect(galleryTabCount(undefined)).toBe(0)
    expect(galleryTabCount({ items: [item(), item({ id: 2 })] })).toBe(2)
  })
  it('names the providers', () => {
    expect(providerName('stashdb')).toBe('StashDB')
    expect(providerName(' TheporNDB ')).toBe('ThePornDB')
    expect(providerName('')).toBe('Provider')
    expect(providerName('other')).toBe('other')
  })
  it('says when the providers were last asked', () => {
    const now = new Date('2026-11-16T12:00:00Z')
    expect(lastSyncText('', '', now)).toBe('Not fetched from the providers yet.')
    expect(lastSyncText('', 'provider lookup failed: x', now)).toBe(GALLERY_PROVIDER_FAILED)
    expect(lastSyncText('2026-11-16T01:00:00Z', 'ok', now)).toBe('Updated today.')
    expect(lastSyncText('2026-11-15T01:00:00Z', 'ok', now)).toBe('Updated yesterday.')
    expect(lastSyncText('2026-11-01T01:00:00Z', 'ok', now)).toBe('Updated 15 days ago.')
    expect(lastSyncText('not a date', 'ok', now)).toBe('')
  })
  it('explains why a picture is or is not used', () => {
    expect(referenceNote({ faces: 1, reference: true })).toBe('Used for face matching')
    expect(referenceNote({ faces: 0, reference: false })).toMatch(/No face/)
    expect(referenceNote({ faces: 3, reference: false })).toMatch(/Several faces/)
    expect(referenceNote({ faces: -1, reference: false })).toBe('Not read yet')
  })
})
