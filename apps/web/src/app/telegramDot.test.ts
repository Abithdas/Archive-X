import { describe, expect, it } from 'vitest'
import { versionLabel } from './Shell'
describe('the version under the storage card', () => {
  it('reads "v30.3.2" for a release, the word itself for a development build, and nothing when unknown', () => {
    expect(versionLabel('30.3.2')).toBe('v30.3.2')
    expect(versionLabel(' 30.3.2 ')).toBe('v30.3.2')
    expect(versionLabel('dev')).toBe('dev')
    expect(versionLabel(undefined)).toBe('')
    expect(versionLabel('')).toBe('')
  })
})
