import { describe, expect, it } from 'vitest'
import { EMPTY_LOGIN, loginBody, loginProblem, savedMessage } from './security'

const ok = { username: 'admin', current: 'old-password', password: '', repeat: '' }
describe('loginProblem', () => {
  it('needs the current password, always', () => {
    expect(loginProblem({ ...ok, current: '' })).toBe('Enter your current password to change login details.')
    expect(loginProblem(ok)).toBe('')
  })
  it('checks the new password: match first, then length', () => {
    expect(loginProblem({ ...ok, password: 'a-long-new-password', repeat: 'different' })).toBe('The two passwords do not match.')
    expect(loginProblem({ ...ok, password: 'short', repeat: 'short' })).toBe('Use at least 12 characters.')
    expect(loginProblem({ ...ok, password: 'exactly12chr', repeat: 'exactly12chr' })).toBe('')
  })
  it('reports a password problem before a missing current password (classic order)', () => {
    expect(loginProblem({ ...EMPTY_LOGIN, username: 'a', password: 'x', repeat: 'y' })).toBe('The two passwords do not match.')
  })
  it('needs a username', () => expect(loginProblem({ ...ok, username: '   ' })).toBe('Enter a username.'))
})
describe('request', () => {
  it('trims the username and sends a blank password to keep the old one', () => {
    expect(loginBody({ ...ok, username: ' admin ' })).toEqual({ username: 'admin', password: '', current_password: 'old-password' })
  })
  it('says other devices are signed out only when the password changed', () => {
    expect(savedMessage(true)).toBe('Saved. Other devices were signed out.')
    expect(savedMessage(false)).toBe('Saved.')
  })
})
