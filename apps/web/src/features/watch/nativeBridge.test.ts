import { afterEach, describe, expect, it, vi } from 'vitest'
import { hasNativeBridge, native, pixelRect } from './nativeBridge'

afterEach(() => { delete (window as unknown as { __TAURI_INTERNALS__?: unknown }).__TAURI_INTERNALS__ })

describe('native bridge', () => {
  it('is absent in a browser and present in the desktop app', () => {
    expect(hasNativeBridge()).toBe(false)
    ;(window as unknown as { __TAURI_INTERNALS__: unknown }).__TAURI_INTERNALS__ = { invoke: vi.fn() }
    expect(hasNativeBridge()).toBe(true)
  })

  it('calls the command by name with its arguments and returns the answer', async () => {
    const invoke = vi.fn(async () => ({ available: true, reason: '' }))
    ;(window as unknown as { __TAURI_INTERNALS__: unknown }).__TAURI_INTERNALS__ = { invoke }
    await expect(native.available()).resolves.toEqual({ available: true, reason: '' })
    await native.open('http://x/y', { x: 1, y: 2, w: 3, h: 4 }, 3)
    expect(invoke).toHaveBeenLastCalledWith('native_open', { url: 'http://x/y', x: 1, y: 2, w: 3, h: 4, hw: 3 })
    await native.command('seek', 42)
    expect(invoke).toHaveBeenLastCalledWith('native_command', { command: 'seek', value: 42 })
  })

  it('turns a refusal into an Error with the reason', async () => {
    ;(window as unknown as { __TAURI_INTERNALS__: unknown }).__TAURI_INTERNALS__ = { invoke: vi.fn(async () => { throw 'the LAV decoders are not installed' }) }
    await expect(native.state()).rejects.toThrow('the LAV decoders are not installed')
  })

  it('says so outside the desktop app', async () => {
    await expect(native.available()).rejects.toThrow('only in the desktop app')
  })

  it('places the video in device pixels: the page rectangle (zoom included) times the screen ratio', () => {
    const el = document.createElement('div')
    el.getBoundingClientRect = () => ({ left: 100.4, top: 50.2, width: 800.5, height: 450.6, right: 0, bottom: 0, x: 0, y: 0, toJSON: () => ({}) })
    expect(pixelRect(el, 1.5)).toEqual({ x: 151, y: 75, w: 1201, h: 676 })
    expect(pixelRect(el, 1)).toEqual({ x: 100, y: 50, w: 801, h: 451 })
  })
})
