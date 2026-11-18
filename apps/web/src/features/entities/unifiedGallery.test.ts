import { describe, expect, it } from 'vitest'
import type { Photo } from '@/features/photos/photos'
import type { GalleryItem } from './gallery'
import { indexAfterRemoval, orderGallery } from './unifiedGallery'

const photo = (n: number): Photo => ({ id: n, channel_id: 'c', message_id: n, filename: `p${n}`, size: 1, width: 1, height: 1, channel_title: 'Chan', thumbnail_url: 't', preview_url: 'p', image_url: 'i' })
const pic = (id: number, provider: string, source = ''): GalleryItem => ({ id, provider, width: 10, height: 10, faces: 1, reference: id === 2, source, url: `/u/${id}` })

describe('orderGallery', () => {
  it('lists identified photos, then provider pictures, then video pictures', () => {
    const out = orderGallery([photo(1), photo(2)], [pic(5, 'snapshot'), pic(2, 'stashdb'), pic(3, 'theporndb')], 'Ava')
    expect(out.map((e) => e.source)).toEqual(['photo', 'photo', 'provider', 'provider', 'video'])
    expect(out.map((e) => e.label)).toEqual(['Your photos · Chan', 'Your photos · Chan', 'StashDB', 'ThePornDB', 'From a video'])
  })
  it('gives gallery pictures a viewer photo that names the source', () => {
    const e = orderGallery([], [pic(7, 'stashdb')], 'Ava')[0]!
    expect(e).toMatchObject({ galleryId: 7, photo: { image_url: '/u/7', channel_title: 'StashDB' } })
  })
  it('moves the viewer after a removal', () => {
    expect(indexAfterRemoval(3, 3)).toBe(2)
    expect(indexAfterRemoval(0, 0)).toBeNull()
    expect(indexAfterRemoval(1, 5)).toBe(1)
  })
})
