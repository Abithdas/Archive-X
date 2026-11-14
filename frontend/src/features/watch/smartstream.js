/** Client-side smart playback (MSE) for files the browser cannot play directly. The engine itself is the classic UI's smart-stream.js; this file loads it and holds the decisions around it. */
export const TRYING = 'Trying client-side smart playback…';
export const SMART_FAILED = 'Client-side smart playback failed. Automatic HLS/FFmpeg conversion is disabled. Retry the original stream or open it in VLC.';
/** A decoding problem (not a network one): media error 3 or 4, or a message that says so. */
export const isDecodeFailure = (code, why = '') => [3, 4].includes(Number(code)) || /no picture|cannot decode|not supported|unsupported codec/i.test(why);
/** The file is not likely to play directly and the engine has a pipeline for it. */
export const wantsSmart = (info, smart) => !!info && !info.direct_likely && !!smart?.canPlay(info);
/** Start on the smart path when the file needs it; otherwise play directly. */
export const initialMode = (info, smart) => (wantsSmart(info, smart) ? 'mse' : 'direct');
/** After a direct-play decode failure the smart path is tried once, and only if the engine can handle the file. */
export const shouldFallBack = (mode, tried, code, info, smart, why = '') => mode === 'direct' && !tried && isDecodeFailure(code, why) && wantsSmart(info, smart);
let loading = null;
const pick = (m, name) => (m[name] ?? m.default?.[name] ?? m.default);
/** Loads the engine and its two libraries (bundled, so it works offline) the first time a video needs it. Resolves to null if anything fails. */
export function loadSmartStream() {
    if (typeof window === 'undefined')
        return Promise.resolve(null);
    loading ??= (async () => {
        try {
            const [mp4box, mpegts] = await Promise.all([import('mp4box'), import('mpegts.js')]);
            window.MP4Box ??= pick(mp4box, 'MP4Box');
            window.mpegts ??= mpegts.default ?? mpegts;
            if (!window.ArchiveXSmartStream)
                await import('./vendor/smart-stream.js');
            const engine = window.ArchiveXSmartStream;
            if (!engine)
                return null;
            // MP4 files use our own pipeline (one SourceBuffer per track); MPEG-TS/FLV stay with the classic engine's mpegts.js path.
            const { startMp4Stream } = await import('./mp4stream');
            return { ...engine, canPlay: (i) => engine.canPlay(i), start: (v, url, info, opts) => (engine.choosePipeline?.(info) === 'mse-mp4box' ? startMp4Stream(v, url, opts) : engine.start(v, url, info, opts)) };
        }
        catch {
            loading = null;
            return null;
        }
    })();
    return loading;
}
