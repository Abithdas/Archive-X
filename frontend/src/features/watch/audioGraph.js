/**
 * Keeps Chrome's own "Live Caption" away from the player.
 *
 * Chrome only sends a media element's sound to Live Caption while the element plays straight to the speakers. Chromium's audio
 * renderer skips transcription when a Web Audio graph is attached to the element (WebAudioSourceProviderImpl reports
 * IsOptimizedForHardwareParameters() = false once there is a client). So the video's (and the alternate audio track's) sound is
 * routed through an AudioContext, which then plays it. Same-origin media only (CORS would silence it); ours always is.
 * A media element can only be connected once, so this is done once per element, on its first play.
 */
export const BLOCK_KEY = 'archive-x:block-browser-captions';
const routed = new WeakSet();
let context = null;
export const blockingEnabled = () => { try {
    return localStorage.getItem(BLOCK_KEY) !== '0';
}
catch {
    return true;
} };
export const setBlocking = (on) => { try {
    localStorage.setItem(BLOCK_KEY, on ? '1' : '0');
}
catch { /* private mode */ } };
/** Route this element's sound through the app. Returns true when it is (or already was) routed. */
export function routeThroughApp(el) {
    if (!el)
        return false;
    if (routed.has(el))
        return true;
    if (!blockingEnabled())
        return false;
    const Ctor = typeof window === 'undefined' ? undefined : window.AudioContext ?? window.webkitAudioContext;
    if (!Ctor)
        return false;
    try {
        context ??= new Ctor({ latencyHint: 'playback' });
        context.createMediaElementSource(el).connect(context.destination);
        routed.add(el);
        void context.resume?.().catch(() => undefined);
        return true;
    }
    catch {
        return false;
    }
}
/** A suspended context is silent; resume it on any play or click (browsers allow that after a user gesture). */
export function resumeAudio() { try {
    void context?.resume?.().catch(() => undefined);
}
catch { /* ignore */ } }
/** For tests. */
export const _resetAudioGraph = () => { context = null; };
