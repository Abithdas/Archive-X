import type { ReactNode } from 'react'

/** The player's control icons: one family, drawn to match the minimal player design (fills for transport, 2px strokes for the rest). */
const Stroke = ({ children, cls = 'size-[18px]' }: { children: ReactNode; cls?: string }) => (
  <svg viewBox="0 0 24 24" aria-hidden="true" focusable="false" className={cls} fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">{children}</svg>
)
export const PlayIcon = ({ cls = 'size-5' }: { cls?: string }) => <svg viewBox="0 0 24 24" aria-hidden="true" focusable="false" className={cls} fill="currentColor"><path d="M8 5v14l11-7z" /></svg>
export const PauseIcon = ({ cls = 'size-5' }: { cls?: string }) => <Stroke cls={cls}><path d="M8 5v14M16 5v14" strokeWidth="2.4" /></Stroke>
export const NextIcon = () => <svg viewBox="0 0 24 24" aria-hidden="true" focusable="false" className="size-[18px]" fill="currentColor"><path d="M6 18l8.5-6L6 6v12zM16 6v12h2V6h-2z" /></svg>
export const PrevIcon = () => <svg viewBox="0 0 24 24" aria-hidden="true" focusable="false" className="size-[18px]" fill="currentColor"><path d="M18 18l-8.5-6L18 6v12zM8 6v12H6V6h2z" /></svg>
export const BufferIcon = () => <Stroke cls="size-3.5"><ellipse cx="12" cy="5" rx="9" ry="3" /><path d="M21 12c0 1.66-4 3-9 3s-9-1.34-9-3M3 5v14c0 1.66 4 3 9 3s9-1.34 9-3V5" /></Stroke>
export const ChevronIcon = () => <Stroke cls="size-2.5"><path d="M6 9l6 6 6-6" strokeWidth="2.4" /></Stroke>
export const VolumeIcon = ({ muted }: { muted: boolean }) => (muted
  ? <Stroke><path d="M11 5L6 9H2v6h4l5 4V5zM23 9l-6 6M17 9l6 6" /></Stroke>
  : <Stroke><path d="M11 5L6 9H2v6h4l5 4V5zM15.5 8.5a5 5 0 0 1 0 7M19 5a10 10 0 0 1 0 14" /></Stroke>)
export const PipIcon = () => <Stroke><rect x="2" y="4" width="20" height="16" rx="3" /><rect x="12" y="11" width="8" height="6" rx="1.5" fill="currentColor" /></Stroke>
export const GearIcon = () => <Stroke><circle cx="12" cy="12" r="3" /><path d="M19.4 15a1.65 1.65 0 0 0 .33 1.82l.06.06a2 2 0 0 1-2.83 2.83l-.06-.06a1.65 1.65 0 0 0-1.82-.33 1.65 1.65 0 0 0-1 1.51V21a2 2 0 0 1-4 0v-.09A1.65 1.65 0 0 0 9 19.4a1.65 1.65 0 0 0-1.82.33l-.06.06a2 2 0 0 1-2.83-2.83l.06-.06a1.65 1.65 0 0 0 .33-1.82 1.65 1.65 0 0 0-1.51-1H3a2 2 0 0 1 0-4h.09A1.65 1.65 0 0 0 4.6 9a1.65 1.65 0 0 0-.33-1.82l-.06-.06a2 2 0 0 1 2.83-2.83l.06.06a1.65 1.65 0 0 0 1.82.33H9a1.65 1.65 0 0 0 1-1.51V3a2 2 0 0 1 4 0v.09a1.65 1.65 0 0 0 1 1.51 1.65 1.65 0 0 0 1.82-.33l.06-.06a2 2 0 0 1 2.83 2.83l-.06.06a1.65 1.65 0 0 0-.33 1.82V9a1.65 1.65 0 0 0 1.51 1H21a2 2 0 0 1 0 4h-.09a1.65 1.65 0 0 0-1.51 1z" /></Stroke>
export const FullscreenIcon = () => <Stroke><path d="M4 8V4h4M20 8V4h-4M4 16v4h4M20 16v4h-4" strokeWidth="2.2" /></Stroke>
