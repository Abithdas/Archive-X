/** Last-resort playback for any format: the PC converts the file to HLS (H.264/AAC) and the browser plays that. */
export const HLS_FAILED = 'This video could not be converted for the browser (is ffmpeg installed, and is the source reachable?). Retry, or open it in VLC.'

/** The server's HLS playlist for a stream URL such as /stream/<channel>/<message>. */
export function hlsUrl(src: string, media?: { cid: string; mid: number }): string | null {
  if (media) return `/hls/${encodeURIComponent(media.cid)}/${media.mid}/index.m3u8`
  const m = /\/stream\/([^/?]+)\/(\d+)/.exec(src)
  return m ? `/hls/${m[1]}/${m[2]}/index.m3u8` : null
}

export interface HlsRun { destroy: () => void }

export async function startHls(video: HTMLVideoElement, url: string, opts: { startTime?: number; onError: (why: string) => void }): Promise<HlsRun | null> {
  let dead = false
  let hls: { destroy: () => void } | null = null
  try {
    const Hls = (await import('hls.js')).default
    if (dead) return null
    if (Hls.isSupported()) {
      const h = new Hls({ startPosition: opts.startTime && opts.startTime > 0 ? opts.startTime : -1, maxBufferLength: 40, enableWorker: true })
      hls = h
      h.on(Hls.Events.ERROR, (_e, d) => {
        if (!d.fatal) return
        if (d.type === Hls.ErrorTypes.MEDIA_ERROR) { try { h.recoverMediaError(); return } catch { /* fall through */ } }
        opts.onError(`${d.type}: ${d.details}`)
      })
      h.loadSource(url)
      h.attachMedia(video)
    } else if (video.canPlayType('application/vnd.apple.mpegurl')) {
      video.src = url   // Safari plays HLS itself
    } else { opts.onError('HLS is not supported by this browser'); return null }
  } catch (e) { opts.onError(String(e)); return null }
  return { destroy: () => { dead = true; try { hls?.destroy() } catch { /* gone */ } } }
}
