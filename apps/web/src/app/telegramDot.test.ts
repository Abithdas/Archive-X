import { describe, expect, it } from 'vitest'
import { telegramDot } from './AccountMenu'

describe('the dot on the avatar', () => {
  it('is green when Telegram is connected, red when it is not, amber until the first answer', () => {
    expect(telegramDot(true, true)).toEqual({ tone: 'bg-good', label: 'Telegram connected' })
    expect(telegramDot(true, false)).toEqual({ tone: 'bg-bad', label: 'Telegram not connected' })
    expect(telegramDot(false, false)).toEqual({ tone: 'bg-warn', label: 'Checking Telegram' })
  })
})
