import { fireEvent, render, screen } from '@testing-library/react'
import { describe, expect, it, vi } from 'vitest'
import { Player, nextRate } from './Player'
import { Timeline } from './Timeline'
import { telegramLink } from './WatchPage'

describe('Player resume hooks', () => {
  const metadata = (video: HTMLVideoElement, duration: number) => {
    Object.defineProperty(video, 'duration', { value: duration, configurable: true })
    fireEvent.loadedMetadata(video)
  }
  it('starts at the chosen position once the video knows its length, and not again after a retry', () => {
    const { container } = render(<Player src="/stream/-1/1" durationHint={600} startAt={300} onOpenVlc={() => {}} />)
    const v = container.querySelector('video')!
    metadata(v, 600)
    expect(v.currentTime).toBe(300)
    v.currentTime = 450
    metadata(v, 600)   // e.g. a second loadedmetadata after a stall: must not jump back
    expect(v.currentTime).toBe(450)
  })
  it('never starts past the end', () => {
    const { container } = render(<Player src="/stream/-1/1" startAt={9999} onOpenVlc={() => {}} />)
    const v = container.querySelector('video')!
    metadata(v, 600)
    expect(v.currentTime).toBeLessThanOrEqual(600)
  })
  it('reports time, play and the end', () => {
    const onTime = vi.fn(), onPlayed = vi.fn(), onEnd = vi.fn()
    const { container } = render(<Player src="/stream/-1/1" durationHint={600} onTime={onTime} onPlayed={onPlayed} onEnd={onEnd} onOpenVlc={() => {}} />)
    const v = container.querySelector('video')!
    v.currentTime = 42
    fireEvent.timeUpdate(v)
    expect(onTime).toHaveBeenCalledWith(42, 600)
    fireEvent.play(v)
    expect(onPlayed).toHaveBeenCalledTimes(1)
    fireEvent.ended(v)
    expect(onEnd).toHaveBeenCalledTimes(1)
  })
  it('does not start at 0 seconds specially', () => {
    const { container } = render(<Player src="/stream/-1/1" durationHint={600} onOpenVlc={() => {}} />)
    const v = container.querySelector('video')!
    v.currentTime = 12
    metadata(v, 600)
    expect(v.currentTime).toBe(12)
  })
})

describe('Player', () => {
  it('shows each time once (no duplicate "0:00 / 2:00" copy)', () => {
    render(<Player src="/stream/-1/1" durationHint={120} onOpenVlc={() => {}} />)
    expect(screen.getAllByText('0:00')).toHaveLength(1)
    expect(screen.getAllByText('2:00')).toHaveLength(1)
    expect(screen.queryByText('0:00 / 2:00')).not.toBeInTheDocument()
  })
  it('offers retry and VLC when the stream cannot be played', () => {
    const vlc = vi.fn()
    const { container } = render(<Player src="/stream/-1/1" durationHint={120} onOpenVlc={vlc} />)
    fireEvent.error(container.querySelector('video')!)
    expect(screen.getByRole('alert')).toHaveTextContent('could not be played')
    fireEvent.click(screen.getByRole('button', { name: 'Open in VLC' }))
    expect(vlc).toHaveBeenCalledTimes(1)
    fireEvent.click(screen.getByRole('button', { name: 'Retry original' }))
    expect(screen.queryByRole('alert')).not.toBeInTheDocument()
  })
  it('disables previous and next when there is nothing to go to', () => {
    render(<Player src="/stream/-1/1" onOpenVlc={() => {}} />)
    expect(screen.getByRole('button', { name: 'Previous video' })).toBeDisabled()
    expect(screen.getByRole('button', { name: 'Next video' })).toBeDisabled()
  })
  it('cycles the playback speed', () => {
    expect([nextRate(1), nextRate(2), nextRate(0.5)]).toEqual([1.25, 0.5, 0.75])
  })
})

// jsdom has no PointerEvent; a MouseEvent subclass carries the coordinates the component reads.
if (!('PointerEvent' in window)) {
  class PE extends MouseEvent { pointerId = 1; pointerType = 'mouse' }
  Object.defineProperty(window, 'PointerEvent', { value: PE })
}

describe('Timeline', () => {
  const rect = { left: 0, width: 200, top: 0, height: 28, right: 200, bottom: 28, x: 0, y: 0, toJSON: () => ({}) }
  it('exposes slider semantics and seeks with the keyboard', () => {
    const seeks: number[] = []
    render(<Timeline time={50} duration={100} buffered={60} segments={[]} onSeek={(t) => seeks.push(t)} />)
    const s = screen.getByRole('slider', { name: 'Seek' })
    expect(s).toHaveAttribute('aria-valuenow', '50')
    expect(s).toHaveAttribute('aria-valuetext', '0:50 of 1:40')
    fireEvent.keyDown(s, { key: 'ArrowRight' })
    fireEvent.keyDown(s, { key: 'End' })
    expect(seeks).toEqual([55, 100])
  })
  it('is disabled until the duration is known', () => {
    const seeks: number[] = []
    render(<Timeline time={0} duration={0} buffered={0} segments={[]} onSeek={(t) => seeks.push(t)} />)
    const s = screen.getByRole('slider', { name: 'Seek' })
    expect(s).toHaveAttribute('aria-disabled', 'true')
    fireEvent.keyDown(s, { key: 'ArrowRight' })
    expect(seeks).toEqual([])
  })
  it('draws one segment per chapter and shows the time and scene on hover', () => {
    const segs = [{ start: 0, end: 40, title: 'Opening', intro: true }, { start: 40, end: 100, title: 'Main', intro: false }]
    render(<Timeline time={0} duration={100} buffered={0} segments={segs} onSeek={() => {}} />)
    const s = screen.getByRole('slider', { name: 'Seek' })
    s.getBoundingClientRect = () => rect as DOMRect
    expect(document.querySelectorAll('[data-segment]')).toHaveLength(2)
    fireEvent.pointerMove(s, { clientX: 150 })
    expect(screen.getByText('Main')).toBeInTheDocument()
    expect(screen.getByText('1:15')).toBeInTheDocument()
  })
})

describe('telegramLink', () => {
  it('builds a t.me link for -100 channel ids only', () => {
    expect(telegramLink('-5002345', 7)).toBeNull()
    expect(telegramLink('-1001234567890', 7)).toBe('https://t.me/c/1234567890/7')
  })
})
