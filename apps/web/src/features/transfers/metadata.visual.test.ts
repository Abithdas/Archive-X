import { describe, expect, it } from 'vitest'
import { DEFAULT_OPTIONS, pendingBody, processBody } from './metadata'

describe('the optional visual match (pHash) in the run options', () => {
  it('is on by default', () => expect(DEFAULT_OPTIONS.visualMatch).toBe(true))

  it('goes into the full run and the pending run exactly as chosen', () => {
    for (const visualMatch of [true, false]) {
      const o = { ...DEFAULT_OPTIONS, visualMatch }
      expect(processBody(['-1001'], ['metadata'], o).visual_match).toBe(visualMatch)
      expect(pendingBody(['-1001'], o).visual_match).toBe(visualMatch)
    }
  })

  it('leaves the other options as they were', () => {
    const body = processBody(['-1001'], ['inventory'], { ...DEFAULT_OPTIONS, forceMetadata: true, visualMatch: false })
    expect(body).toMatchObject({ channel_ids: ['-1001'], force_metadata: true, force_entity: false, visual_match: false, unbounded: true })
  })
})
