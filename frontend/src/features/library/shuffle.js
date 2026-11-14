/** Fisher–Yates, returning a new array. `rand` is injectable so tests are deterministic. */
export function shuffled(list, rand = Math.random) {
    const a = [...list];
    for (let i = a.length - 1; i > 0; i--) {
        const j = Math.floor(rand() * (i + 1));
        [a[i], a[j]] = [a[j], a[i]];
    }
    return a;
}
export const playable = (list) => list.filter((v) => v && v.id != null && v._cid != null);
export const playlistItems = (list) => list.map((v) => ({ channel_id: v._cid, message_id: v.id, name: v.name || `video ${v.id}` }));
export const externalBody = (player, items) => ({ player, random: true, playlist: items });
export const playerName = (p) => (p === 'mpc' || p === 'mpc-hc' ? 'MPC-HC' : String(p).toUpperCase());
export const isRemoteHost = (hostname) => !['127.0.0.1', 'localhost', '::1', '[::1]'].includes(hostname);
export const isIOS = (ua, platform, touchPoints) => /iPad|iPhone|iPod/.test(ua) || (platform === 'MacIntel' && touchPoints > 1);
export const isAndroid = (ua) => /Android/i.test(ua);
export const routeFor = (player, remote, mobile) => (!remote ? 'launch' : player === 'vlc' && mobile ? 'mobile-vlc' : 'download');
export const NO_LIBRARY_VIDEOS = 'Select a published channel first.';
export const NO_PERFORMER_VIDEOS = 'This performer has no videos in the local catalogue yet.';
export const NO_STUDIO_VIDEOS = 'This studio has no videos in the local catalogue yet.';
export const collecting = (n) => `Collecting videos for the shuffle… ${n.toLocaleString()} so far`;
export const startingMessage = (player, n) => `Starting random ${playerName(player)} playlist: ${n.toLocaleString()} video${n === 1 ? '' : 's'}…`;
export const failedMessage = (player, detail) => `${playerName(player)} shuffle could not start: ${detail || 'unknown error'}`;
export const startedMessage = (r, player, n, remote) => `${r.player || playerName(player)} shuffled playlist started: ${r.count || n} video${(r.count || n) === 1 ? '' : 's'}${remote ? ' (on the PC)' : ''}`;
export const downloadedMessage = (player) => `Random playlist downloaded — open it in ${playerName(player)} on this device.`;
export const NOT_ON_LAN = 'VLC device playback requires LAN access to Archive X.';
/** The mobile VLC hand-off URL for a generated playlist link, or '' when the platform has none. */
export function mobileVlcUrl(url, android, ios) {
    if (android)
        return `intent://${url.replace(/^https?:\/\//, '')}#Intent;scheme=http;type=audio/x-mpegurl;package=org.videolan.vlc;end`;
    if (ios)
        return `vlc-x-callback://x-callback-url/stream?url=${encodeURIComponent(url)}`;
    return '';
}
