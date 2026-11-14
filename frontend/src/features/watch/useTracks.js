import { toast } from 'sonner';
import { routeThroughApp } from './audioGraph';
import { useCallback, useEffect, useRef, useState } from 'react';
import { CAPTION_WINDOW, activeText, initialChoice, loadPrefs, mergeCues, parseVtt, savePrefs, windowStart } from './tracks';
const getJson = async (url, signal) => {
    const r = await fetch(url, { credentials: 'same-origin', cache: 'no-store', signal });
    if (!r.ok) {
        let why = String(r.status);
        try {
            const j = (await r.json());
            if (typeof j.detail === 'string' && j.detail)
                why = j.detail;
        }
        catch { /* not JSON */ }
        throw new Error(why);
    }
    return (await r.json());
};
/** Audio tracks, subtitle tracks and live English captions of one video; the caller shows `text` and plays `altAudioUrl`. */
export function useTracks(media, video, time, playing) {
    const [info, setInfo] = useState(null);
    const [audio, setAudioState] = useState(null); // null: the video's own audio
    const [sub, setSubState] = useState({ kind: 'off' });
    const [cues, setCues] = useState([]);
    const [live, setLive] = useState('idle');
    const [altUrl, setAltUrl] = useState(null);
    const [altBusy, setAltBusy] = useState(false);
    const [model, setModel] = useState('idle');
    const [liveError, setLiveError] = useState('');
    const fetched = useRef(new Set());
    const inflight = useRef(new Map());
    const gen = useRef(0);
    const [retry, setRetry] = useState(0);
    const key = media ? `${media.cid}/${media.mid}` : '';
    // Track list, once per video. What the viewer chose last time carries over at once: live captions keep going on the next video
    // without waiting for the track list (which can be slow or fail on a remote file).
    useEffect(() => {
        const prefs = loadPrefs();
        setInfo(null);
        setAudioState(null);
        setCues([]);
        setAltUrl(null);
        setLive('idle');
        setLiveError('');
        fetched.current = new Set();
        setSubState(prefs.sub === 'live' ? { kind: 'live', original: !!prefs.liveOriginal } : { kind: 'off' });
        if (!media)
            return;
        let dead = false;
        getJson(`/api/play/tracks/${encodeURIComponent(media.cid)}/${media.mid}`).then((i) => {
            if (dead)
                return;
            setInfo(i);
            const c = initialChoice(i, prefs);
            setAudioState(c.audio);
            setSubState((cur) => (cur.kind === 'live' ? (i.captions.available ? cur : { kind: 'off' }) : c.sub));
        }).catch(() => {
            // The track list failed: still offer live captions if the speech engine is there.
            getJson('/api/captions/status').then((cap) => { if (!dead) {
                setInfo({ audio: [], subtitles: [], captions: cap });
                if (!cap.available)
                    setSubState((cur) => (cur.kind === 'live' ? { kind: 'off' } : cur));
            } }).catch(() => undefined);
        });
        return () => { dead = true; };
    }, [key]); // eslint-disable-line react-hooks/exhaustive-deps
    // An embedded subtitle track: fetch the whole file once.
    useEffect(() => {
        if (!media || sub.kind !== 'track')
            return;
        let dead = false;
        fetch(`/api/play/subtitle/${encodeURIComponent(media.cid)}/${media.mid}/${sub.index}.vtt`, { credentials: 'same-origin' })
            .then((r) => (r.ok ? r.text() : Promise.reject(new Error(String(r.status)))))
            .then((t) => { if (!dead)
            setCues(parseVtt(t)); }).catch(() => { if (!dead)
            setCues([]); });
        return () => { dead = true; };
    }, [key, sub]); // eslint-disable-line react-hooks/exhaustive-deps
    // Live captions need the speech model. Choosing them starts its download/load at once and tells the viewer in a pop-up outside the player.
    useEffect(() => {
        if (sub.kind !== 'live' || model === 'ready')
            return;
        let dead = false;
        let timer;
        let shown = false;
        const poll = () => getJson('/api/captions/status?prepare=1').then((s) => {
            if (dead)
                return;
            const m = s.model ?? 'ready';
            setModel(m);
            if (m === 'ready') {
                if (shown)
                    toast.success('Live captions are ready', { id: 'caption-model' });
                shown = false;
                try {
                    if (!localStorage.getItem('archive-x:chrome-caption-hint')) {
                        localStorage.setItem('archive-x:chrome-caption-hint', '1');
                        toast('A black "Live Caption" box at the bottom of the window is Chrome\'s own feature, not this app. Turn it off in Chrome: Settings → Accessibility → Live Caption.', { duration: 15000 });
                    }
                }
                catch { /* private mode */ }
                return;
            }
            if (m === 'error') {
                toast.error(`Live captions could not start: ${s.model_error || 'speech model unavailable'}`, { id: 'caption-model' });
                setLive('error');
                setLiveError(s.model_error || 'speech model unavailable');
                shown = false;
                return;
            }
            if (!shown) {
                shown = true;
                toast.loading('Preparing live captions: downloading the speech model (first time only, can take a few minutes)…', { id: 'caption-model', duration: Infinity });
            }
            timer = setTimeout(poll, 2000);
        }).catch(() => { if (!dead) {
            setModel('error');
            setLive('error');
        } });
        void poll();
        return () => { dead = true; if (timer)
            clearTimeout(timer); if (shown)
            toast.dismiss('caption-model'); };
    }, [sub.kind, model]);
    // Live captions: transcribe the 30 s window being watched, and the next one ahead of time. A seek, a new video or a changed setting
    // drops every request still on its way (they would be for the wrong place), so the new position is served first.
    useEffect(() => {
        gen.current++;
        for (const c of inflight.current.values())
            c.abort();
        inflight.current.clear();
        fetched.current = new Set();
        if (sub.kind === 'live')
            setCues([]);
    }, [sub.kind, sub.kind === 'live' && sub.original, audio, key]); // eslint-disable-line react-hooks/exhaustive-deps
    useEffect(() => {
        if (!media || sub.kind !== 'live' || model !== 'ready')
            return;
        const w = windowStart(time);
        for (const [start, c] of inflight.current)
            if (Math.abs(start - w) > CAPTION_WINDOW) {
                c.abort();
                inflight.current.delete(start);
                fetched.current.delete(start);
            } // the viewer left that place
        const wanted = [w, w + CAPTION_WINDOW].filter((s) => !fetched.current.has(s));
        if (!wanted.length)
            return;
        const s = wanted[0];
        const mine = gen.current;
        const ac = new AbortController();
        fetched.current.add(s);
        inflight.current.set(s, ac);
        setLive('working');
        getJson(`/api/play/captions/${encodeURIComponent(media.cid)}/${media.mid}?start=${s}${audio !== null ? `&track=${audio}` : ''}${sub.original ? '&task=transcribe' : ''}`, ac.signal)
            .then((r) => {
            if (mine !== gen.current)
                return;
            inflight.current.delete(s);
            if (r.preparing) {
                fetched.current.delete(s);
                setModel('preparing');
                setLive('idle');
                return;
            }
            if (r.skipped) {
                fetched.current.delete(s);
                setLive('idle');
                setTimeout(() => { if (mine === gen.current)
                    setRetry((n) => n + 1); }, 1000);
                return;
            } // the server skipped it as out of date; ask again for where we are now
            setCues((c) => mergeCues(c, r.cues));
            setLive('idle');
            setLiveError('');
        })
            .catch((e) => {
            if (mine !== gen.current || (e instanceof DOMException && e.name === 'AbortError'))
                return;
            inflight.current.delete(s);
            fetched.current.delete(s);
            setLive('error');
            setLiveError(e instanceof Error ? e.message : '');
            setTimeout(() => { if (mine === gen.current)
                setRetry((n) => n + 1); }, 4000); // try that window again
        });
    }, [media, sub.kind, sub.kind === 'live' && sub.original, audio, Math.floor(time / 5), playing, model, retry]); // eslint-disable-line react-hooks/exhaustive-deps
    // Alternate audio: ask the server to build it, poll until it is ready.
    useEffect(() => {
        setAltUrl(null);
        setAltBusy(false);
        if (!media || audio === null)
            return;
        let dead = false;
        let timer;
        setAltBusy(true);
        const poll = () => getJson(`/api/play/audio-track/${encodeURIComponent(media.cid)}/${media.mid}/${audio}/status`).then((s) => {
            if (dead)
                return;
            if (s.ready && s.url) {
                setAltUrl(s.url);
                setAltBusy(false);
            }
            else if (s.error) {
                setAltBusy(false);
                setAudioState(null);
            }
            else
                timer = setTimeout(poll, 1500);
        }).catch(() => { if (!dead) {
            setAltBusy(false);
            setAudioState(null);
        } });
        void poll();
        return () => { dead = true; if (timer)
            clearTimeout(timer); };
    }, [key, audio]); // eslint-disable-line react-hooks/exhaustive-deps
    const pickAudio = useCallback((t) => {
        setAudioState(t && t.ordinal !== 0 ? t.index : null);
        savePrefs({ ...loadPrefs(), audioLanguage: t?.language || undefined });
    }, []);
    const pickSub = useCallback((c, language = '') => {
        setSubState(c);
        savePrefs({ ...loadPrefs(), liveOriginal: c.kind === 'live' ? !!c.original : loadPrefs().liveOriginal, sub: c.kind === 'off' ? 'off' : c.kind, subLanguage: c.kind === 'track' ? language || undefined : loadPrefs().subLanguage });
    }, []);
    const text = sub.kind === 'off' ? '' : activeText(cues, time);
    void video;
    /** One plain line about live captions while they are not showing text yet ('' when nothing needs saying). */
    const liveStatus = sub.kind !== 'live' ? '' : model === 'error' || live === 'error' ? `Live captions failed${liveError ? `: ${liveError}` : ''}` : model !== 'ready' ? 'Live captions: getting the speech model ready…' : live === 'working' && !cues.length ? 'Live captions: listening…' : '';
    return { info, audio, sub, text, live, liveStatus, altUrl, altBusy, pickAudio, pickSub };
}
/** Keep a hidden <audio> in step with the video (play, pause, seek, speed, volume) while it carries the chosen audio track. */
export function useAltAudio(video, audio, url, muted, volume, rate) {
    useEffect(() => {
        const v = video.current;
        const a = audio.current;
        if (!v || !a || !url)
            return;
        const sync = () => { if (Math.abs(a.currentTime - v.currentTime) > 0.25)
            a.currentTime = v.currentTime; };
        const play = () => { sync(); void a.play().catch(() => undefined); };
        const pause = () => a.pause();
        const wait = () => a.pause();
        v.muted = true;
        routeThroughApp(a);
        sync();
        if (!v.paused)
            play();
        v.addEventListener('play', play);
        v.addEventListener('playing', play);
        v.addEventListener('pause', pause);
        v.addEventListener('waiting', wait);
        v.addEventListener('seeked', sync);
        v.addEventListener('timeupdate', sync);
        return () => {
            v.removeEventListener('play', play);
            v.removeEventListener('playing', play);
            v.removeEventListener('pause', pause);
            v.removeEventListener('waiting', wait);
            v.removeEventListener('seeked', sync);
            v.removeEventListener('timeupdate', sync);
            a.pause();
            v.muted = muted;
        };
    }, [url]); // eslint-disable-line react-hooks/exhaustive-deps
    useEffect(() => {
        const a = audio.current;
        if (!a || !url)
            return;
        a.muted = muted;
        a.volume = volume;
        a.playbackRate = rate;
        if (video.current)
            video.current.muted = true;
    }, [url, muted, volume, rate]); // eslint-disable-line react-hooks/exhaustive-deps
}
