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

export interface LibraryQuery {
  channels: string[]
  page: number
  per: number
  sort: 'date_desc' | 'date_asc' | 'size_desc' | 'dur_desc' | 'name_asc'
  q?: string
  favorites?: boolean
}

/** Live status snapshot (/api/status and the /ws stream). Only the parts the shell shows are typed. */
export interface Status {
  current_job?: { id: string; job_type: string; status: string; progress?: number } | null
  telegram?: { connected?: boolean; status?: string }
  telegram_connected?: boolean
  telegram_status?: string
  data_versions?: Record<string, number>
  version?: string
  [key: string]: unknown
}

/** Row of /api/performers and /api/studios (only the fields the directory uses). */
export interface Entity {
  id: number
  name: string
  video_count: number
  image?: string | null
  gender?: string | null
}
export type EntityKind = 'performers' | 'studios'
