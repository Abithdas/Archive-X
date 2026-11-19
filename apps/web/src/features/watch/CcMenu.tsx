import type { ReactNode } from 'react'
import { CcIcon } from './PlayerIcons'
import { MenuItem, PopMenu } from './PlayerParts'
import type { AudioTrack, SubChoice, SubtitleTrack, TrackInfo } from './tracks'
import type { LiveState } from './useTracks'

const Col = ({ title, children }: { title: string; children: ReactNode }) => (
  <div role="group" aria-label={title} className="flex min-w-[10rem] max-w-[15rem] flex-col gap-0.5">
    <div className="px-2.5 py-1 text-[10.5px] font-bold uppercase tracking-wider text-muted">{title}</div>{children}
  </div>
)

/** One "CC" menu with an Audio column and a Subtitles column: embedded audio tracks, embedded/sidecar subtitles, and live English captions. */
export function CcMenu({ info, audio, sub, live, altBusy, onAudio, onSub, drop }: { drop?: 'up' | 'down'; info: TrackInfo; audio: number | null; sub: SubChoice; live: LiveState; altBusy: boolean; onAudio: (t: AudioTrack | null) => void; onSub: (c: SubChoice, language?: string) => void }) {
  const current = audio ?? info.audio[0]?.index
  const on = sub.kind !== 'off'
  const state = live === 'working' ? <span className="text-muted"> …</span> : live === 'error' ? <span className="text-bad"> failed</span> : null
  return (
    <PopMenu label="Audio and subtitles" align="right" drop={drop} iconOnly button={<span className={on ? 'text-accent' : undefined}><CcIcon /></span>}>
      <div className="flex max-w-[calc(100vw-2rem)] flex-col gap-1 sm:flex-row sm:gap-3">
        <Col title="Audio">
          {info.audio.length ? info.audio.map((t: AudioTrack) => (
            <MenuItem key={t.index} active={t.index === current} onClick={() => onAudio(t)}><span className="min-w-0 truncate" title={t.label}>{t.label}</span>{altBusy && t.index === audio ? <span className="text-muted"> loading…</span> : null}</MenuItem>
          )) : <span className="px-2.5 py-1 text-xs text-muted">One audio track</span>}
        </Col>
        <Col title="Subtitles">
          <MenuItem active={sub.kind === 'off'} onClick={() => onSub({ kind: 'off' })}>Off</MenuItem>
          {info.subtitles.filter((s: SubtitleTrack) => s.text).map((s: SubtitleTrack) => (
            <MenuItem key={s.index} active={sub.kind === 'track' && sub.index === s.index} onClick={() => onSub({ kind: 'track', index: s.index }, s.language)}><span className="min-w-0 truncate" title={s.label}>{s.label}</span></MenuItem>
          ))}
          {(info.addon ?? []).map((a) => (
            <MenuItem key={a.token} active={sub.kind === 'addon' && sub.token === a.token} onClick={() => onSub({ kind: 'addon', token: a.token }, a.language)}><span className="min-w-0 truncate" title={a.label}>{a.label}</span></MenuItem>
          ))}
          {info.captions.available ? (<>
            <MenuItem active={sub.kind === 'live' && !sub.original} onClick={() => onSub({ kind: 'live' })}>Live captions → English{sub.kind === 'live' && !sub.original ? state : null}</MenuItem>
            <MenuItem active={sub.kind === 'live' && !!sub.original} onClick={() => onSub({ kind: 'live', original: true })}>Live captions · original language{sub.kind === 'live' && sub.original ? state : null}</MenuItem>
          </>) : <span className="max-w-[14rem] px-2.5 py-1 text-[11px] leading-snug text-muted">{info.captions.engine === 'stream' ? 'Live captions are for videos in your library.' : <>Live captions need <code>pip install faster-whisper</code></>}</span>}
        </Col>
      </div>
    </PopMenu>
  )
}
