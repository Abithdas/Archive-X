import { useContentMode } from './useContentMode'
import type { Mode } from './content'

/** The words the app uses, by mode: SFW is movies and shows, so people are Cast, studios are Studios & Networks, tags are Genres. */
export interface Labels {
  videos: string; videosBlurb: string
  performers: string; performersBlurb: string; performersEmpty: string; performer: string
  studios: string; studiosBlurb: string; studiosEmpty: string
  tags: string; series: string | null
}

const NSFW: Labels = {
  videos: 'Videos', videosBlurb: 'Browse and manage your video library',
  performers: 'Performers', performersBlurb: 'Everyone linked to your videos', performersEmpty: 'No performers yet.', performer: 'Performer',
  studios: 'Studios', studiosBlurb: 'Studios and sites in your archive', studiosEmpty: 'No studios yet.',
  tags: 'Tags', series: null,
}
const SFW: Labels = {
  videos: 'Movies & Shows', videosBlurb: 'Browse and manage your movies and shows',
  performers: 'Cast', performersBlurb: 'Actors in your movies and shows', performersEmpty: 'No cast yet. Identify your movies and shows in Settings → Metadata.', performer: 'Actor',
  studios: 'Studios & Networks', studiosBlurb: 'Studios and networks behind your movies and shows', studiosEmpty: 'No studios or networks yet.',
  tags: 'Genres', series: 'Series',
}

export const labelsFor = (mode: Mode | undefined): Labels => (mode === 'sfw' ? SFW : NSFW)

/** Until the mode is known the familiar wording stays, so nothing flickers or changes meaning. */
export const useLabels = (): Labels => labelsFor(useContentMode().data?.mode)
