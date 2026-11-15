import { describe, expect, it } from 'vitest'
import { REFRESH_AFTER_MS, REPAIR_EVERY_MS, repairBody } from './autoRepair'

describe('automatic repair of genders and pictures', () => {
  it('asks for the kind of page that is open', () => {
    expect(repairBody('performers')).toEqual({ kind: 'performers' })
    expect(repairBody('studios')).toEqual({ kind: 'studios' })
  })
  it('asks again every ten minutes (the backend also limits itself to one run per ten minutes)', () => expect(REPAIR_EVERY_MS).toBe(600_000))
  it('looks at the list again after the fetch had time to work, twice and in order', () => {
    expect([...REFRESH_AFTER_MS]).toEqual([20_000, 90_000])
  })
})
