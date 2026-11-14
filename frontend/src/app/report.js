/** What gets copied by "Copy report": enough to diagnose, with nothing private. The address keeps its path but drops any query or fragment. */
export function buildReport({ error, url, userAgent, recent, now = new Date() }) {
    const e = error instanceof Error ? error : new Error(String(error));
    let where = url;
    try {
        const u = new URL(url);
        where = u.origin + u.pathname;
    }
    catch { /* keep as is */ }
    const stack = (e.stack ?? '').split('\n').slice(0, 12).join('\n');
    return [
        'Archive X problem report',
        `Time: ${now.toISOString()}`,
        `Page: ${where}`,
        `Browser: ${userAgent}`,
        '',
        `Error: ${e.name}: ${e.message}`,
        stack,
        '',
        `Recent errors (${recent.length}):`,
        ...recent.slice(-10).map((r) => `- ${r.at} ${r.message}`),
    ].join('\n');
}
