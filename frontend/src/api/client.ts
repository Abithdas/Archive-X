/** Thin fetch wrapper: same-origin cookie session, JSON in/out, typed errors, one place to react to a lost session. */

export class ApiError extends Error {
  readonly status: number
  readonly detail: string
  constructor(status: number, detail: string) {
    super(detail || `Request failed (${status})`)
    this.name = 'ApiError'
    this.status = status
    this.detail = detail
  }
}

export const SESSION_LOST_EVENT = 'archive-x:session-lost'

async function readDetail(response: Response): Promise<string> {
  try {
    const body = (await response.clone().json()) as { detail?: unknown }
    if (typeof body.detail === 'string') return body.detail
  } catch {
    /* not JSON */
  }
  try {
    return (await response.text()).slice(0, 200)
  } catch {
    return ''
  }
}

export async function api<T>(path: string, init: RequestInit & { json?: unknown } = {}): Promise<T> {
  const { json, headers, ...rest } = init
  const response = await fetch(path, {
    credentials: 'same-origin',
    cache: 'no-store',
    ...rest,
    headers: {
      Accept: 'application/json',
      ...(json !== undefined ? { 'Content-Type': 'application/json' } : {}),
      ...headers,
    },
    body: json !== undefined ? JSON.stringify(json) : rest.body,
  })
  if (!response.ok) {
    const detail = await readDetail(response)
    if (response.status === 401 && path !== '/login') window.dispatchEvent(new Event(SESSION_LOST_EVENT))
    throw new ApiError(response.status, detail)
  }
  if (response.status === 204) return undefined as T
  return (await response.json()) as T
}

export function libraryUrl(query: { channels: string[]; page: number; per: number; sort: string; q?: string; favorites?: boolean; studio?: string; performer?: string; tag?: string; dateFrom?: string; dateTo?: string; resolution?: string }): string {
  const params = new URLSearchParams({
    page: String(query.page),
    per: String(query.per),
    channels: query.channels.join(','),
    sort: query.sort,
    q: query.q ?? '',
    favorites: query.favorites ? '1' : '0',
  })
  if (query.studio) params.set('studio', query.studio)
  if (query.performer) params.set('performer', query.performer)
  if (query.tag) params.set('tag', query.tag)
  if (query.dateFrom) params.set('date_from', query.dateFrom)
  if (query.dateTo) params.set('date_to', query.dateTo)
  if (query.resolution) params.set('resolution', query.resolution)
  return `/api/library?${params.toString()}`
}

export const thumbUrl = (channelId: string, messageId: number) => `/api/thumb/${encodeURIComponent(channelId)}/${messageId}?r=f3`
