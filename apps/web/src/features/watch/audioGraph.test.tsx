import { fireEvent, render, screen } from '@testing-library/react'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { Player } from './Player'
import { BLOCK_KEY, _resetAudioGraph, blockingEnabled, resumeAudio, routeThroughApp, setBlocking, setBoost, setSoundMode } from './audioGraph'

class FakeCtx {
  static made = 0
  static connected: unknown[] = []
  state = 'suspended'
  destination = { dest: true }
  resumed = 0
  constructor() { FakeCtx.made++ }
  last: unknown = null
  node = (): { connect: (d: unknown) => void; [k: string]: unknown } => ({ connect: () => undefined, gain: { value: 1 }, frequency: { value: 0 }, Q: { value: 0 }, threshold: { value: 0 }, knee: { value: 0 }, ratio: { value: 0 }, attack: { value: 0 }, release: { value: 0 }, type: '' })
  createGain() { const g = this.node(); g.connect = (d: unknown) => { FakeCtx.connected.push([this.last, d]) }; return g }
  createBiquadFilter() { return this.node() }
  createDynamicsCompressor() { return this.node() }
  createMediaElementSource(el: unknown) { this.last = el; return { connect: () => undefined } }
  resume() { this.resumed++; this.state = 'running'; return Promise.resolve() }
}
beforeEach(() => { localStorage.clear(); FakeCtx.made = 0; FakeCtx.connected = []; _resetAudioGraph(); vi.stubGlobal('AudioContext', FakeCtx) })
afterEach(() => vi.unstubAllGlobals())

describe('keeping Chrome Live Caption away', () => {
  it('routes an element through one shared AudioContext, once per element', () => {
    const a = document.createElement('video'); const b = document.createElement('audio')
    expect(routeThroughApp(a)).toBe(true); expect(routeThroughApp(a)).toBe(true); expect(routeThroughApp(b)).toBe(true)
    expect(FakeCtx.made).toBe(1)                       // one context for everything
    expect(FakeCtx.connected).toHaveLength(2)          // each element connected exactly once, to the speakers
    expect(FakeCtx.connected[0]).toEqual([a, { dest: true }])
  })
  it('is on by default and can be switched off (then nothing is routed)', () => {
    expect(blockingEnabled()).toBe(true)
    setBlocking(false); expect(localStorage.getItem(BLOCK_KEY)).toBe('0'); expect(blockingEnabled()).toBe(false)
    expect(routeThroughApp(document.createElement('video'))).toBe(false); expect(FakeCtx.made).toBe(0)
  })
  it('leaves the element alone when the browser has no Web Audio, or connecting fails', () => {
    vi.stubGlobal('AudioContext', undefined)
    expect(routeThroughApp(document.createElement('video'))).toBe(false)
    class Broken extends FakeCtx { createMediaElementSource(): never { throw new Error('already connected') } }
    vi.stubGlobal('AudioContext', Broken); _resetAudioGraph()
    expect(routeThroughApp(document.createElement('video'))).toBe(false)
  })
  it('the player routes its video on first play and wakes the audio context, and the Settings switch is there', () => {
    const { container } = render(<Player src="/stream/-1/1" durationHint={60} onOpenVlc={() => {}} />)
    const v = container.querySelector('video')!
    expect(FakeCtx.made).toBe(0)                       // nothing until playback starts
    fireEvent.play(v)
    expect(FakeCtx.connected[0]).toEqual([v, { dest: true }])
    fireEvent.click(screen.getByRole('button', { name: 'Settings' }))
    const sw = screen.getByRole('menuitemcheckbox', { name: /Keep Chrome's Live Caption off/ })
    expect(sw).toHaveAttribute('aria-checked', 'true')
    fireEvent.click(sw)
    expect(localStorage.getItem(BLOCK_KEY)).toBe('0')
    resumeAudio()
  })

  it('a volume boost routes the element even when the caption block is off, and stays within 1 to 3', () => {
    setBlocking(false)
    const a = document.createElement('video')
    expect(routeThroughApp(a)).toBe(false)
    expect(setBoost(a, 2)).toBe(true)
    expect(FakeCtx.connected).toHaveLength(1)
    expect(setBoost(a, 9)).toBe(true)
  })

  it('night mode and dialogue boost route the element and set the filters', () => {
    setBlocking(false)
    const a = document.createElement('video')
    expect(setSoundMode(a, 'night')).toBe(true)
    expect(FakeCtx.connected).toHaveLength(1)
    expect(setSoundMode(a, 'normal')).toBe(true)
  })
})
