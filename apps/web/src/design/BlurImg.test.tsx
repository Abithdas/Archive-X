import { fireEvent, render } from '@testing-library/react'
import { describe, expect, it } from 'vitest'
import { BlurImg } from './BlurImg'

describe('BlurImg', () => {
  it('starts blurred and clear once it has loaded', () => {
    const { container } = render(<BlurImg src="/a.jpg" alt="" />)
    const img = container.querySelector('img')!
    expect(img.className).toContain('blur-md')
    fireEvent.load(img)
    expect(img.className).toContain('blur-0'); expect(img.className).not.toContain('blur-md')
  })
  it('passes its own onLoad through', () => {
    let hit = 0
    const { container } = render(<BlurImg src="/a.jpg" alt="" onLoad={() => { hit++ }} />)
    fireEvent.load(container.querySelector('img')!)
    expect(hit).toBe(1)
  })
})
