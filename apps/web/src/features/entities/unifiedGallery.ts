import type { Photo } from '@/features/photos/photos'
import { providerName, type GalleryItem } from './gallery'

/** One tile of the performer gallery, wherever the picture comes from. */
export type GallerySource = 'photo' | 'provider' | 'video'
export interface GalleryEntry {
  key: string
  source: GallerySource
  /** Where it comes from, in words: shown on the tile and in the viewer. */
  label: string
  photo: Photo
  /** Set for pictures kept in the gallery (provider or video); a library photo has none. */
  galleryId?: number
  reference?: boolean
  faces?: number
}

const PHOTO_ID = 'gallery'

/** A gallery picture dressed as a library photo, so one viewer can show both kinds. */
function asPhoto(i: GalleryItem, name: string, label: string): Photo {
  return { id: i.id, channel_id: PHOTO_ID, message_id: i.id, filename: `${name} · ${label}`, size: 0, width: i.width, height: i.height, channel_title: label, thumbnail_url: i.url, preview_url: i.url, image_url: i.url }
}

/**
 * The performer's pictures in one run, like the photo gallery: first the photos of yours in which they were identified, then the pictures the
 * providers hold, then the faces found in their own videos.
 */
export function orderGallery(photos: readonly Photo[], pictures: readonly GalleryItem[], name: string): GalleryEntry[] {
  const mine: GalleryEntry[] = photos.map((p) => ({ key: `p:${p.channel_id}:${p.message_id}`, source: 'photo', label: p.channel_title ? `Your photos · ${p.channel_title}` : 'Your photos', photo: p }))
  const entry = (i: GalleryItem, source: GallerySource): GalleryEntry => {
    const label = source === 'video' ? 'From a video' : providerName(i.provider)
    return { key: `g:${i.id}`, source, label, photo: asPhoto(i, name, label), galleryId: i.id, reference: i.reference, faces: i.faces }
  }
  const isVideo = (i: GalleryItem) => i.provider.trim().toLowerCase() === 'snapshot' || i.source === 'snapshot'
  return [...mine, ...pictures.filter((i) => !isVideo(i)).map((i) => entry(i, 'provider')), ...pictures.filter(isVideo).map((i) => entry(i, 'video'))]
}

/** After removing the entry at `index`: where the viewer should be, or null when nothing is left. */
export const indexAfterRemoval = (index: number, remaining: number): number | null => (remaining <= 0 ? null : Math.min(index, remaining - 1))
