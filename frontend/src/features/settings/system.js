const s = (v) => (v == null ? '' : String(v));
const positive = (v) => Number.isFinite(Number(v)) && Number(v) > 0;
const gb = (v) => (positive(v) ? `${Number(v)} GB` : 'Unavailable');
/** CPU, GPU, RAM, storage, conversion backend and (when verified) hardware encoders. */
export function hardwareRows(r) {
    const cpu = (r.cpu ?? {}), ram = (r.ram ?? {}), storage = (r.storage ?? {});
    const gpus = (Array.isArray(r.gpus) ? r.gpus : []);
    const gpu = gpus.map((g) => `${s(g.name) || 'GPU'}${Array.isArray(g.hardware_encoders) && g.hardware_encoders.length ? ` — ${g.hardware_encoders.join(', ')}` : ''}`).join(' | ') || 'No dedicated GPU detected';
    const encoders = Object.entries((r.verified_encoders ?? {})).filter(([, v]) => v).map(([k]) => k).join(', ');
    const threads = `${s(cpu.physical_cores) || '?'} cores / ${s(cpu.logical_threads) || '?'} threads`;
    const rows = [
        { label: 'CPU', value: `${s(cpu.name) || 'Unknown'} · ${threads}` },
        { label: 'GPU', value: gpu },
        { label: 'RAM', value: `${gb(ram.total_gb)} · ${gb(ram.available_gb)} free` },
        { label: 'Storage', value: `${gb(storage.free_gb)} free` },
        { label: 'Conversion', value: `${s(r.conversion_backend) || 'libx264'}${encoders ? ' · verified' : ''}` },
    ];
    if (encoders)
        rows.push({ label: 'Hardware encoding', value: encoders });
    return rows;
}
const mbps = (v) => (typeof v === 'number' && Number.isFinite(v) ? `${v} Mbps` : 'Unavailable');
export function speedResult(r) {
    return {
        ok: Boolean(r.ok),
        status: r.ok ? 'Test complete' : 'No network result',
        rows: [
            { label: 'Download', value: mbps(r.download_mbps) },
            { label: 'Upload', value: mbps(r.upload_mbps) },
            { label: 'Latency', value: r.latency_ms == null ? 'Unavailable' : `${s(r.latency_ms)} ms` },
            { label: 'Route', value: s(r.quality) || 'unknown' },
        ],
        note: `Network mode: ${s(r.network_mode) || 'adaptive'} · Background capacity: ${r.background_capacity == null ? 'auto' : s(r.background_capacity)}${r.note ? ` · ${s(r.note)}` : ''}`,
    };
}
export function browserName(ua) {
    const chromium = /Chrome|Chromium|Edg\//.test(ua) && !/OPR\//.test(ua);
    if (chromium)
        return /Edg\//.test(ua) ? 'Edge' : 'Chromium';
    if (/Firefox\//.test(ua))
        return 'Firefox';
    if (/Safari\//.test(ua))
        return 'Safari';
    return 'Other';
}
export function osName(ua, platform, touchPoints) {
    if (/iPad|iPhone|iPod/.test(ua) || (platform === 'MacIntel' && touchPoints > 1))
        return 'iOS/iPadOS';
    if (/Android/i.test(ua))
        return 'Android';
    if (/Windows/i.test(ua))
        return 'Windows';
    if (/Mac OS X/i.test(ua))
        return 'macOS';
    if (/Linux/i.test(ua))
        return 'Linux';
    return 'Other';
}
export function browserRows(i) {
    return [
        { label: 'Browser', value: browserName(i.ua) }, { label: 'OS', value: osName(i.ua, i.platform, i.touchPoints) },
        { label: 'MP4 / H.264', value: i.mp4 ? 'Supported' : 'Fallback' }, { label: 'WebM', value: i.webm ? 'Supported' : 'Fallback' },
        { label: 'HLS', value: i.hls ? 'Native' : 'App fallback' }, { label: 'Fetch', value: i.fetch ? 'Available' : 'Limited' },
        { label: 'Abort', value: i.abort ? 'Available' : 'Limited' }, { label: 'Pointer input', value: i.pointer ? 'Available' : 'Fallback' },
    ];
}
