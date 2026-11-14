/** Thin fetch wrapper: same-origin cookie session, JSON in/out, typed errors, one place to react to a lost session. */
export class ApiError extends Error {
    status;
    detail;
    constructor(status, detail) {
        super(detail || `Request failed (${status})`);
        this.name = 'ApiError';
        this.status = status;
        this.detail = detail;
    }
}
export const SESSION_LOST_EVENT = 'archive-x:session-lost';
async function readDetail(response) {
    try {
        const body = (await response.clone().json());
        if (typeof body.detail === 'string')
            return body.detail;
        return ''; // valid JSON with no readable detail (e.g. `{}`): say nothing rather than show raw JSON
    }
    catch {
        /* not JSON */
    }
    try {
        return (await response.text()).slice(0, 200);
    }
    catch {
        return '';
    }
}
export async function api(path, init = {}) {
    const { json, headers, ...rest } = init;
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
    });
    if (!response.ok) {
        const detail = await readDetail(response);
        if (response.status === 401 && path !== '/login')
            window.dispatchEvent(new Event(SESSION_LOST_EVENT));
        throw new ApiError(response.status, detail);
    }
    if (response.status === 204)
        return undefined;
    return (await response.json());
}
export function libraryUrl(query) {
    const params = new URLSearchParams({
        page: String(query.page),
        per: String(query.per),
        channels: query.channels.join(','),
        sort: query.sort,
        q: query.q ?? '',
        favorites: query.favorites ? '1' : '0',
    });
    if (query.studio)
        params.set('studio', query.studio);
    if (query.performer)
        params.set('performer', query.performer);
    if (query.tag)
        params.set('tag', query.tag);
    if (query.dateFrom)
        params.set('date_from', query.dateFrom);
    if (query.dateTo)
        params.set('date_to', query.dateTo);
    if (query.resolution)
        params.set('resolution', query.resolution);
    return `/api/library?${params.toString()}`;
}
export const thumbUrl = (channelId, messageId) => `/api/thumb/${encodeURIComponent(channelId)}/${messageId}?r=f3`;
