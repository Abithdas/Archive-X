import { describe, expect, it, vi } from 'vitest'
import { isChunkFailure, mayReload } from './lazyRetry'

const store = () => { const m = new Map<string, string>(); return { getItem: (k: string) => m.get(k) ?? null, setItem: (k: string, v: string) => void m.set(k, v) } }

describe('chunk failures', () => {
  it('are recognised by the browsers\' messages', () => {
    expect(isChunkFailure(new TypeError('Failed to fetch dynamically imported module: http://127.0.0.1:1/assets/OperationsPage-D0Gf7y0Y.js'))).toBe(true)
    expect(isChunkFailure(new Error('Importing a module script failed.'))).toBe(true)
    expect(isChunkFailure(new Error('Cannot read properties of undefined'))).toBe(false)
  })
  it('reload the page once per half minute, so a file that is really gone cannot loop', () => {
    const s = store()
    expect(mayReload(1_000_000, s)).toBe(true)
    expect(mayReload(1_010_000, s)).toBe(false)
    expect(mayReload(1_031_000, s)).toBe(true)
  })
  it('still reload when storage is blocked', () => {
    const blocked = { getItem: () => { throw new Error('blocked') }, setItem: vi.fn() }
    expect(mayReload(5, blocked)).toBe(true)
  })
})
