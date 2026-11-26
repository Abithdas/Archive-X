// Response shapes of the endpoints used so far. Hand-written from the live responses until the backend has
// Pydantic response_model definitions (plan phase 0); `npm run gen:api` will then generate openapi.d.ts and
// these types should be replaced by it. Fields not used by the UI are intentionally left out.

export interface Channel {
  id: number
  telegram_id: string
  title: string
  username: string | null
  image: string | null
  role: string | null
  content_rating?: string | null
  /** A channel with videos but no row of its own (no name saved): listed last, labelled by the end of its id. */
  unnamed?: boolean
  video_count: number
  known_count: number
  prepared_count: number
  pending_count: number
  last_scan_at: string | null
}

export interface LibraryItem {
  /** Telegram message id; with `_cid` it is the stable playback identity. */
  id: number
  _cid: string
  /** SQLite media row id, for database mutations. */
  media_id: number
  name: string
  filename: string
  size: number | null
  duration: number | null
  width: number | null
  height: number | null
  resolution_category: string | null
  codec?: string | null
  thumbnail_id: number
  favorite: 0 | 1
  upload_date: string | null
  channel_title: string | null
  performers: string[]
  studios: string[]
  tags: string[]
  studio: string
}

export interface LibraryPage {
  total: number
  page: number
  per: number
  items: LibraryItem[]
}

export type SortKey = 'date_desc' | 'date_asc' | 'size_desc' | 'dur_desc' | 'name_asc' | 'studio_asc' | 'performer_asc' | 'tag_asc'

export interface LibraryQuery {
  channels: string[]
  page: number
  per: number
  sort: SortKey
  q?: string
  favorites?: boolean
  studio?: string
  performer?: string
  tag?: string
  dateFrom?: string
  dateTo?: string
  resolution?: string
}

/** Live status snapshot (/api/status and the /ws stream). Only the parts the shell shows are typed. */
export interface Status {
  current_job?: { id: string; job_type: string; status: string; progress?: number } | null
  telegram?: { connected?: boolean; status?: string }
  telegram_connected?: boolean
  telegram_status?: string
  data_versions?: Record<string, number>
  other_mode_jobs?: { count: number; running: number }
  version?: string
  [key: string]: unknown
}

/** Row of /api/performers and /api/studios (only the fields the directory uses). */
export interface Entity {
  id: number
  name: string
  video_count: number
  /** In SFW the titles, not the videos: how many movies and how many series (an episode is not a series). */
  movies?: number
  series?: number
  image?: string | null
  gender?: string | null
}
export type EntityKind = 'performers' | 'studios'

interface PerformerProfile {
  id: number
  name: string
  image: string | null
  bio: string | null
  profile: string | null
  gender: string | null
  favorite: boolean
  provider_ids?: unknown
}
export interface PerformerDetail {
  performer: PerformerProfile
  videos: Array<Omit<LibraryItem, '_cid' | 'name'> & { source_channel: string; title: string | null; filename: string }>
}
