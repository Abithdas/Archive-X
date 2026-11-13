import { fireEvent, render, screen, within } from '@testing-library/react'
import { describe, expect, it, vi } from 'vitest'
import { ChannelMenu } from './ChannelMenu'

const CH = [{ telegram_id: '1', title: 'Main', video_count: 5 }, { telegram_id: '2', title: 'Alpha', video_count: 2 }] as never

describe('ChannelMenu', () => {
  it('reads "All channels" with nothing picked, and a tick marks the chosen channels', () => {
    const onToggle = vi.fn(); const onAll = vi.fn()
    const { rerender } = render(<ChannelMenu channels={CH} selected={[]} onToggle={onToggle} onAll={onAll} onEdit={vi.fn()} />)
    fireEvent.click(screen.getByRole('button', { name: /^All channels/ }))
    const list = within(screen.getByRole('group', { name: 'Channels' }))
    expect(list.getByRole('button', { name: /^All channels/ })).toHaveAttribute('aria-pressed', 'true')
    fireEvent.click(list.getByRole('button', { name: /^Main/ }))
    expect(onToggle).toHaveBeenCalledWith('1')
    rerender(<ChannelMenu channels={CH} selected={['1']} onToggle={onToggle} onAll={onAll} onEdit={vi.fn()} />)
    expect(screen.getByRole('button', { name: /^Main/, expanded: true })).toBeInTheDocument()
    expect(within(screen.getByRole('group', { name: 'Channels' })).getByRole('button', { name: /^Main/ })).toHaveAttribute('aria-pressed', 'true')
    fireEvent.click(within(screen.getByRole('group', { name: 'Channels' })).getByRole('button', { name: /^All channels/ }))
    expect(onAll).toHaveBeenCalled()
  })
  it('offers Edit channel only for exactly one pick', () => {
    const onEdit = vi.fn()
    render(<ChannelMenu channels={CH} selected={['2']} onToggle={vi.fn()} onAll={vi.fn()} onEdit={onEdit} />)
    fireEvent.click(screen.getByRole('button', { name: /^Alpha/ }))
    fireEvent.click(screen.getByRole('button', { name: 'Edit selected channel' }))
    expect(onEdit).toHaveBeenCalledWith('2')
  })
})
