import { describe, expect, it } from 'vitest'
import { DEFAULT_ADMIN, SETUP_HINT, TOO_MANY, UNREACHABLE, WRONG, loginMessage, setupBody, setupProblem } from './auth'

describe('auth rules', () => {
  it('words sign-in failures', () => {
    expect(loginMessage(401)).toEqual({ text: WRONG, needsSetup: false }); expect(loginMessage(null)).toEqual({ text: UNREACHABLE, needsSetup: false }); expect(loginMessage(0).text).toBe(UNREACHABLE)
    expect(loginMessage(428)).toEqual({ text: SETUP_HINT, needsSetup: true }); expect(loginMessage(429)).toEqual({ text: TOO_MANY, needsSetup: false }); expect(loginMessage(429, 'Wait 5 minutes').text).toBe('Wait 5 minutes')
  })
  it('checks the setup form', () => {
    expect(setupProblem('admin', 'a-long-password', 'a-long-password')).toBe(''); expect(setupProblem(' ', 'a-long-password', 'a-long-password')).toBe('Username is required')
    expect(setupProblem('x'.repeat(81), 'a-long-password', 'a-long-password')).toMatch(/80 characters/); expect(setupProblem('a', 'short', 'short')).toBe('Password must be at least 12 characters')
    expect(setupProblem('a', 'a-long-password', 'a-long-passworD')).toBe('Passwords do not match')
  })
  it('builds the request, defaulting the username', () => { expect(setupBody(' ', 'pw', 'pw')).toEqual({ username: DEFAULT_ADMIN, password: 'pw', confirmation: 'pw' }); expect(setupBody(' bob ', 'p', 'p').username).toBe('bob') })
})
