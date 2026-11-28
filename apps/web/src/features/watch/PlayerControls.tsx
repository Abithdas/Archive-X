import type { ReactNode } from 'react'
import { cn } from '@/design/cn'
import { IconSwap } from '@/design/IconSwap'
import { formatDuration } from '@/features/library/format'
import { BufferIcon, ChevronIcon, FullscreenIcon, GearIcon, NextIcon, PauseIcon, PipIcon, PlayIcon, PrevIcon, VolumeIcon } from './PlayerIcons'
import { Divider, MenuHead, MenuItem, MenuRow, PopMenu, VolumeBar } from './PlayerParts'
import { Timeline, type PreviewInfo } from './Timeline'
import type { Segment } from './timelineLogic'
import { DEFAULT_STYLE, SIZE_STEP, clampSize, sizeLabel, type CaptionBg, type CaptionStyle } from './captionStyle'
import type { TrackState } from './useTracks'
import { DEFAULT_PS, ENHANCES, PS_RANGE, SOUNDS, ZOOMS, isDefaultPS, type PictureSound } from './pictureSound'

/** The two engines (the browser's <video> and the native player) share this bar, these menus and the shortcuts: only the picture's source differs. */
export const RATES = [0.5, 0.75, 1, 1.25, 1.5, 2] as const
export const clock = (s: number) => formatDuration(s) || '0:00'
export const BTN = 'grid size-8 shrink-0 place-items-center rounded-md text-fg/85 transition-[background-color,color,transform] hover:bg-fg/10 hover:text-fg focus-visible:outline-2 focus-visible:outline-accent active:scale-95 disabled:opacity-35 disabled:hover:bg-transparent'

/** A row is a heading (`head`) or a label and value. */
export interface BufferStatus { chip: string; good: boolean; rows: Array<{ k: string; v: string; good?: boolean; head?: string }> }

export interface ControlsProps {
  time: number; duration: number; buffered: number
  segments?: Segment[]; preview?: PreviewInfo; onSeek: (t: number) => void
  /** The hover picture goes under the bar when the bar is under a picture that is a separate window (the native one). */
  tipBelow?: boolean
  playing: boolean; onToggle: () => void; toggleDisabled?: boolean
  onPrev?: () => void; onNext?: () => void
  rate: number; onRate: (r: number) => void
  muted: boolean; volume: number; onMute: () => void; onVolume: (v: number) => void
  status: BufferStatus
  pip: { onClick: () => void; label: string; title: string; pressed?: boolean; disabled?: boolean }
  tracksMenu?: ReactNode
  settings: ReactNode
  onFullscreen: () => void
  /** Menus open downwards when the picture is a window that would hide them. */
  drop?: 'up' | 'down'
  trailing?: ReactNode
  /** Before the previous button (the native player's way back in full screen). */
  leading?: ReactNode
  disabled?: boolean
}

/** Time line, then the button row: previous, play, next, buffer status, speed, volume | picture-in-picture, captions menu, settings, full screen. */
export function PlayerControls(p: ControlsProps) {
  const drop = p.drop ?? 'up'
  return (
    <>
      <div>
        <div className="mb-1 flex items-center justify-between px-0.5 text-[13px] font-semibold tracking-tight tabular-nums" aria-hidden="true"><span>{clock(p.time)}</span><span className="font-medium text-muted">{p.duration ? clock(p.duration) : '–:––'}</span></div>
        <Timeline time={p.time} duration={p.duration} buffered={p.buffered} segments={p.segments ?? []} preview={p.preview} onSeek={p.onSeek} tipBelow={p.tipBelow} />
      </div>
      <div className="flex items-center justify-between py-1 text-xs font-medium text-fg/80 sm:text-[13px]">
        <div className="flex min-w-0 items-center gap-1 @lg:gap-2.5">
          {p.leading}
          <button type="button" className={BTN} onClick={p.onPrev} disabled={!p.onPrev} aria-label="Previous video" title="Previous (Shift+P)"><PrevIcon /></button>
          <button type="button" className={BTN} onClick={p.onToggle} disabled={p.toggleDisabled} aria-label={p.playing ? 'Pause' : 'Play'} title="Play / pause (Space)"><IconSwap on={p.playing} offIcon={<PlayIcon />} onIcon={<PauseIcon />} /></button>
          <button type="button" className={BTN} onClick={p.onNext} disabled={!p.onNext} aria-label="Next video" title="Next (N)"><NextIcon /></button>
          <Divider />
          <div className="hidden @lg:block">
            <PopMenu label="Buffer status" align="left" drop={drop} button={<><BufferIcon /><span className={cn('size-1.5 rounded-full', p.status.good ? 'bg-emerald-400 shadow-[0_0_6px_#34d399]' : 'bg-amber-400')} /><span className="hidden @lg:inline">{p.status.chip}</span><ChevronIcon /></>}>
              <MenuHead>Buffer status</MenuHead>
              {p.status.rows.map((r) => r.head ? <MenuHead key={`h-${r.head}`}>{r.head}</MenuHead> : <MenuRow key={r.k} k={r.k} v={r.v} good={r.good} />)}
            </PopMenu>
          </div>
          <Divider />
          <PopMenu label={`Speed ${p.rate}x`} align="left" drop={drop} button={<><span>{p.rate}x</span><ChevronIcon /></>}>
            {RATES.map((r) => <MenuItem key={r} active={r === p.rate} onClick={(close) => { p.onRate(r); close() }}>{r}x</MenuItem>)}
          </PopMenu>
          <Divider />
          <div className="group/vol flex items-center">
            <button type="button" className={BTN} onClick={p.onMute} disabled={p.disabled} aria-label={p.muted ? 'Unmute' : 'Mute'} aria-pressed={p.muted} title="Mute (M)"><IconSwap on={p.muted || p.volume === 0} offIcon={<VolumeIcon muted={false} />} onIcon={<VolumeIcon muted />} /></button>
            <VolumeBar value={p.muted ? 0 : p.volume} onChange={p.onVolume} />
          </div>
        </div>
        <div className="flex items-center gap-1 @lg:gap-2.5">
          <button type="button" className={cn(BTN, 'hidden @lg:grid')} onClick={p.pip.onClick} disabled={p.pip.disabled} aria-pressed={p.pip.pressed} aria-label={p.pip.label} title={p.pip.title}><PipIcon /></button>
          {p.tracksMenu}
          <PopMenu label="Settings" align="right" drop={drop} iconOnly button={<GearIcon />}>{p.settings}</PopMenu>
          <button type="button" className={BTN} onClick={p.onFullscreen} aria-label="Full screen" title="Full screen (F)"><FullscreenIcon /></button>
          {p.trailing}
        </div>
      </div>
    </>
  )
}

/** Caption look (size, box, reset): the same menu items in both players. */
export function CaptionStyleItems({ style, onChange, head = true }: { style: CaptionStyle; onChange: (patch: Partial<CaptionStyle>) => void; head?: boolean }) {
  return (
    <>
      {head ? <MenuHead>Caption style</MenuHead> : null}
      <div className="flex items-center justify-between gap-3 px-2.5 py-1 text-xs"><span className="text-muted">Size</span>
        <span className="flex items-center gap-1">
          <button type="button" aria-label="Smaller captions" onClick={() => onChange({ size: clampSize(style.size - SIZE_STEP) })} className="grid size-7 place-items-center rounded-md border border-line-2 font-bold hover:bg-fg/10">A−</button>
          <output aria-label="Caption size" className="w-10 text-center font-semibold tabular-nums">{sizeLabel(style.size)}</output>
          <button type="button" aria-label="Larger captions" onClick={() => onChange({ size: clampSize(style.size + SIZE_STEP) })} className="grid size-7 place-items-center rounded-md border border-line-2 font-bold hover:bg-fg/10">A+</button>
        </span></div>
      {(['none', 'soft', 'solid'] as CaptionBg[]).map((b) => <MenuItem key={b} active={style.bg === b} onClick={() => onChange({ bg: b })}>{b === 'none' ? 'No box (outlined text)' : b === 'soft' ? 'Soft box' : 'Solid box'}</MenuItem>)}
      <MenuItem onClick={() => onChange({ x: DEFAULT_STYLE.x, y: DEFAULT_STYLE.y, size: DEFAULT_STYLE.size })} label="Reset caption position and size">Reset position and size</MenuItem>
    </>
  )
}

const PILL = 'rounded-full border px-3 py-1.5 text-xs font-semibold transition-colors'
const Pills = <T extends string>({ label, items, value, onPick }: { label: string; items: Array<[T, string]>; value: T; onPick: (v: T) => void }) => (
  <div role="radiogroup" aria-label={label} className="flex max-w-[16rem] flex-wrap gap-1.5 px-2.5 py-1">
    {items.map(([k, text]) => <button key={k} type="button" role="radio" aria-checked={value === k} onClick={() => onPick(k)} className={cn(PILL, value === k ? 'border-transparent bg-fg text-bg' : 'border-line-2 text-fg/85 hover:bg-fg/10')}>{text}</button>)}
  </div>
)
const Note = ({ children }: { children: ReactNode }) => <p className="max-w-[16rem] px-2.5 pb-1 text-[11px] leading-snug text-muted">{children}</p>

/** Picture (zoom, enhancement), Sound (normal, dialogue boost, night mode, volume boost) and Subtitle sync. */
export function PictureSoundItems({ value, onChange, boostFailed, sync }: { value: PictureSound; onChange: (patch: Partial<PictureSound>) => void; boostFailed?: boolean; sync?: { seconds: number; on: boolean; onChange: (s: number) => void } }) {
  return (
    <>
      <MenuHead>Picture</MenuHead>
      <Pills label="Picture zoom" items={ZOOMS} value={value.zoom} onPick={(zoom) => onChange({ zoom })} />
      <Note>Zoom crops the edges. Remove black bars fills the window when the video does not match it.</Note>
      <MenuHead>Picture enhancement</MenuHead>
      <Pills label="Picture enhancement" items={ENHANCES} value={value.enhance} onPick={(enhance) => onChange({ enhance })} />
      <Note>{value.enhance === 'off' ? 'The picture as it is in the file.' : value.enhance === 'sharpen' ? 'Brings out edges and fine detail in soft or low-resolution video.' : value.enhance === 'vivid' ? 'A little more contrast and colour for dull, washed-out video.' : 'Sharper detail and richer colour together.'}</Note>
      <MenuHead>Sound</MenuHead>
      <Pills label="Sound mode" items={SOUNDS} value={value.sound} onPick={(sound) => onChange({ sound })} />
      <Note>{value.sound === 'night' ? 'Evens out loud and quiet scenes so you can keep the volume down.' : value.sound === 'dialogue' ? 'Lifts the range where speech sits.' : 'The sound as it is in the file.'}</Note>
      <PsSlider k="boost" label="Volume boost" value={value.boost} onChange={onChange} />
      {boostFailed ? <span className="max-w-[16rem] px-2.5 py-1 text-[11px] leading-snug text-warn">The sound changes are not available in this window.</span> : null}
      {sync ? (<>
        <MenuHead>Subtitle sync</MenuHead>
        {sync.on ? (
          <div className="flex items-center justify-between gap-3 px-2.5 py-1 text-xs"><span className="text-muted">Subtitles are</span>
            <span className="flex items-center gap-1">
              <button type="button" aria-label="Subtitles earlier" onClick={() => sync.onChange(Math.round((sync.seconds - 0.1) * 10) / 10)} className="grid size-7 place-items-center rounded-md border border-line-2 font-bold hover:bg-fg/10">−</button>
              <output aria-label="Subtitle shift" className="w-14 text-center font-semibold tabular-nums">{sync.seconds > 0 ? '+' : ''}{sync.seconds.toFixed(1)} s</output>
              <button type="button" aria-label="Subtitles later" onClick={() => sync.onChange(Math.round((sync.seconds + 0.1) * 10) / 10)} className="grid size-7 place-items-center rounded-md border border-line-2 font-bold hover:bg-fg/10">+</button>
            </span></div>
        ) : <Note>Turn subtitles on from the subtitles menu first.</Note>}
      </>) : null}
      <MenuItem onClick={() => onChange({ ...DEFAULT_PS })} label="Reset picture and sound">Reset picture and sound {isDefaultPS(value) ? <span className="text-muted">(standard)</span> : null}</MenuItem>
    </>
  )
}
function PsSlider({ k, label, value, onChange }: { k: 'boost'; label: string; value: number; onChange: (patch: Partial<PictureSound>) => void }) {
  return (
    <label className="flex items-center justify-between gap-3 px-2.5 py-1 text-xs"><span className="text-muted">{label}</span>
      <span className="flex items-center gap-2"><input type="range" aria-label={label} min={PS_RANGE[k][0]} max={PS_RANGE[k][1]} step={5} value={value} onChange={(e) => onChange({ [k]: Number(e.target.value) })} className="w-24 accent-[var(--color-accent)]" /><output className="w-9 text-right font-semibold tabular-nums">{value}%</output></span></label>
  )
}

/** Live captions (off, English, original language) and Auto-repeat: the same items in both players. */
export function PlaybackItems({ tracks, loop, onLoop }: { tracks: TrackState; loop: boolean; onLoop: () => void }) {
  return (
    <>
      <MenuHead>Live captions</MenuHead>
      {tracks.info?.captions.available ? (<>
        <MenuItem active={tracks.sub.kind !== 'live'} onClick={(close) => { if (tracks.sub.kind === 'live') tracks.pickSub({ kind: 'off' }); close() }}>Off</MenuItem>
        <MenuItem active={tracks.sub.kind === 'live' && !tracks.sub.original} onClick={(close) => { tracks.pickSub({ kind: 'live' }); close() }}>English (translated)</MenuItem>
        <MenuItem active={tracks.sub.kind === 'live' && !!tracks.sub.original} onClick={(close) => { tracks.pickSub({ kind: 'live', original: true }); close() }}>Original language</MenuItem>
      </>) : <span className="max-w-[13rem] px-2.5 py-1 text-[11px] leading-snug text-muted">{tracks.info?.captions.engine === 'stream' ? 'Live captions are for videos in your library.' : tracks.info ? <>Needs <code>pip install faster-whisper</code></> : 'Loading…'}</span>}
      <MenuHead>Playback</MenuHead>
      <MenuItem onClick={onLoop} label="Loop" pressed={loop}>Auto-repeat <span className={loop ? 'text-emerald-400' : 'text-muted'}>{loop ? 'ON' : 'OFF'}</span></MenuItem>
    </>
  )
}

/** Keys both players answer, on the whole window: Space or K play/pause, J and L ±30 s, arrows ±5 s, M mute, F full screen, N next, Shift+P previous. */
export function playerKey(e: KeyboardEvent, a: { toggle: () => void; seekBy: (s: number) => void; mute: () => void; full: () => void; next?: () => void; prev?: () => void }): void {
  const el = e.target as HTMLElement | null
  if (el && (el.isContentEditable || /^(INPUT|TEXTAREA|SELECT)$/.test(el.tagName)) && (el as HTMLInputElement).type !== 'range') return
  if (e.ctrlKey || e.metaKey || e.altKey) return
  const k = e.key.toLowerCase()
  if (k === ' ' || k === 'k') { e.preventDefault(); a.toggle() }
  else if (k === 'j') a.seekBy(-30)
  else if (k === 'l') a.seekBy(30)
  else if (k === 'arrowleft') a.seekBy(-5)
  else if (k === 'arrowright') a.seekBy(5)
  else if (k === 'm') a.mute()
  else if (k === 'f') a.full()
  else if (k === 'n') a.next?.()
  else if (e.key === 'P') a.prev?.()
}
