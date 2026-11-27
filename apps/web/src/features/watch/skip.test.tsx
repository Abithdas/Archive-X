import { fireEvent, render, screen } from '@testing-library/react'
import { describe, expect, it, vi } from 'vitest'
import { SkipButton, skipLabel, skipNow, skipTarget, type Skip } from './skip'

const skips: Skip[] = [{ type: 'recap', start: 0, end: 20 }, { type: 'intro', start: 30, end: 90 }, { type: 'credits', start: 2400, end: null }]

describe('skipNow', () => {
  it('offers the stretch that is playing and nothing between them', () => {
    expect(skipNow(skips, 5, 2700)?.type).toBe('recap')
    expect(skipNow(skips, 45, 2700)?.type).toBe('intro')
    expect(skipNow(skips, 25, 2700)).toBeNull()
    expect(skipNow(skips, 600, 2700)).toBeNull()
  })
  it('stops offering a stretch in its last second, so the button does not appear as it ends', () => {
    expect(skipNow(skips, 89.5, 2700)).toBeNull()
  })
  it('offers credits that run to the end of the video', () => {
    expect(skipNow(skips, 2500, 2700)?.type).toBe('credits')
    expect(skipNow(skips, 2699.5, 2700)).toBeNull()
  })
})

describe('what the button says and does', () => {
  it('says Next episode for credits when there is a next one, and the plain skip otherwise', () => {
    expect(skipLabel(skips[2]!, true)).toBe('Next episode')
    expect(skipLabel(skips[2]!, false)).toBe('Skip credits')
    expect(skipLabel(skips[1]!, true)).toBe('Skip intro')
  })
  it('lands at the end of the stretch, or the end of the video when it runs to the end', () => {
    expect(skipTarget(skips[1]!, 2700)).toBe(90)
    expect(skipTarget(skips[2]!, 2700)).toBe(2700)
  })
  it('is shown only while there is something to skip, and presses once', () => {
    const press = vi.fn()
    const { rerender } = render(<SkipButton skip={null} canNext={false} onPress={press} />)
    expect(screen.queryByTestId('skip-button')).toBeNull()
    rerender(<SkipButton skip={skips[1]!} canNext={false} onPress={press} />)
    fireEvent.click(screen.getByRole('button', { name: 'Skip intro' }))
    expect(press).toHaveBeenCalledTimes(1)
  })
})
