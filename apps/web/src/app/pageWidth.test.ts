import { readdirSync, readFileSync, statSync } from 'node:fs'
import { join } from 'node:path'
import { describe, expect, it } from 'vitest'

const files = (dir: string): string[] => readdirSync(dir).flatMap((f) => { const p = join(dir, f); return statSync(p).isDirectory() ? files(p) : p.endsWith('.tsx') && !p.endsWith('.test.tsx') ? [p] : [] })

describe('page width at every zoom', () => {
  // The whole app is zoomed with CSS zoom (Settings, Appearance, 70% to 130%), so a page's width cap measured in CSS pixels shrinks on screen when
  // the zoom is below 100% and leaves empty bands at both sides of the page (the window is wider than the cap). The cap has to be divided by the zoom.
  it('no page keeps a fixed pixel cap (1400 px or more) on its width: the cap is divided by --ax-ui-zoom, so the page fills the window at 70% as at 100%', () => {
    const bad = files(join(__dirname, '..')).filter((f) => /max-w-\[(1[4-9]\d\d|[2-9]\d\d\d)px\]/.test(readFileSync(f, 'utf8')))
    expect(bad.map((f) => f.split('/src/')[1])).toEqual([])
  })
  it('the pages that cap their width use the zoom-aware form', () => {
    const using = files(join(__dirname, '..')).filter((f) => readFileSync(f, 'utf8').includes('max-w-[calc(1800px/var(--ax-ui-zoom,1))]'))
    expect(using.length).toBeGreaterThanOrEqual(5)   // the lists of videos, performers and studios, Settings and AI Assistance have no cap at all: they fill the window
  })
})
