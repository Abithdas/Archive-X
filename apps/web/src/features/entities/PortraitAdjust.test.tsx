import { fireEvent, render, screen } from '@testing-library/react'
import { beforeAll, beforeEach, describe, expect, it, vi } from 'vitest'
import { PortraitAdjust } from './PortraitAdjust'
import { STORAGE_KEY, readCrop } from './portrait'

vi.mock('sonner', () => ({ toast: vi.fn() }))
beforeAll(() => {
  HTMLDialogElement.prototype.showModal = function () { this.setAttribute('open', '') }
  HTMLDialogElement.prototype.close = function () { this.removeAttribute('open') }
})
beforeEach(() => localStorage.clear())

const show = (over: Partial<Parameters<typeof PortraitAdjust>[0]> = {}) => {
  const onClose = vi.fn(), onSaved = vi.fn()
  render(<PortraitAdjust id={5} name="Mia" src="/p.jpg" open onClose={onClose} onSaved={onSaved} {...over} />)
  return { onClose, onSaved }
}

describe('PortraitAdjust', () => {
  it('starts from the saved framing and shows the zoom', () => {
    localStorage.setItem(STORAGE_KEY, JSON.stringify({ 5: { x: 0, y: 0, zoom: 1.5 } }))
    show()
    expect(screen.getByRole('slider', { name: 'Portrait zoom' })).toHaveValue('1.5')
    expect(screen.getByText('150%')).toBeInTheDocument()
  })
  it('zoom previews at once and Save keeps it for this performer only', () => {
    const { onClose, onSaved } = show()
    fireEvent.change(screen.getByRole('slider', { name: 'Portrait zoom' }), { target: { value: '2' } })
    expect(screen.getByText('Preview updated. Save to keep this framing.')).toBeInTheDocument()
    expect(readCrop(5).zoom).toBe(1)
    fireEvent.click(screen.getByRole('button', { name: 'Save framing' }))
    expect(readCrop(5).zoom).toBe(2)
    expect(readCrop(6).zoom).toBe(1)
    expect(onSaved).toHaveBeenCalledWith({ x: 0, y: 0, zoom: 2 })
    expect(onClose).toHaveBeenCalled()
  })
  it('Cancel saves nothing', () => {
    const { onClose } = show()
    fireEvent.change(screen.getByRole('slider', { name: 'Portrait zoom' }), { target: { value: '2' } })
    fireEvent.click(screen.getByRole('button', { name: 'Cancel' }))
    expect(readCrop(5).zoom).toBe(1)
    expect(onClose).toHaveBeenCalled()
  })
  it('Reset goes back to the original framing and says to save', () => {
    localStorage.setItem(STORAGE_KEY, JSON.stringify({ 5: { x: 0, y: 0, zoom: 2 } }))
    show()
    fireEvent.click(screen.getByRole('button', { name: 'Reset' }))
    expect(screen.getByText('100%')).toBeInTheDocument()
    expect(screen.getByText('Original framing restored. Save to keep this setting.')).toBeInTheDocument()
  })
  it('says so when the browser will not store it, and stays open', () => {
    const { onClose } = show()
    const real = Storage.prototype.setItem
    Storage.prototype.setItem = () => { throw new Error('quota') }
    try { fireEvent.click(screen.getByRole('button', { name: 'Save framing' })) } finally { Storage.prototype.setItem = real }
    expect(screen.getByText(/Could not save in this browser/)).toBeInTheDocument()
    expect(onClose).not.toHaveBeenCalled()
  })
})
