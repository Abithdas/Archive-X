const MAX = 20;
const log = [];
const text = (v) => (v instanceof Error ? v.message : typeof v === 'string' ? v : (() => { try {
    return JSON.stringify(v);
}
catch {
    return String(v);
} })()) ?? '';
export function record(v, now = new Date()) {
    log.push({ at: now.toISOString(), message: text(v).slice(0, 500) });
    if (log.length > MAX)
        log.shift();
}
export const recentErrors = () => [...log];
export const clearErrors = () => { log.length = 0; };
let installed = false;
export function installErrorLog() {
    if (installed || typeof window === 'undefined')
        return;
    installed = true;
    window.addEventListener('error', (e) => record(e.error ?? e.message));
    window.addEventListener('unhandledrejection', (e) => record(e.reason));
}
