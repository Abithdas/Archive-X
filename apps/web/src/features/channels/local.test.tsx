import { fireEvent, render, screen } from '@testing-library/react'
import { describe, expect, it } from 'vitest'
import { ChannelMenu } from '@/features/library/ChannelMenu'
import { isLocalChannel, isLocalId } from './local'

describe('folders on this PC', () => {
  it('are told apart from Telegram channels by id or role', () => {
    expect(isLocalId('-9000000000001')).toBe(true)
    expect(isLocalId('-1001234567890')).toBe(false)
    expect(isLocalId(null)).toBe(false)
    expect(isLocalChannel({ telegram_id: '-1001', role: 'local' })).toBe(true)
    expect(isLocalChannel({ telegram_id: '-1001', role: 'available' })).toBe(false)
  })
  it('are marked in the channel list of the Library', () => {
    const base = { id: 1, image: null, username: null, known_count: 0, prepared_count: 0, pending_count: 0, last_scan_at: null, video_count: 3 }
    render(<ChannelMenu channels={[{ ...base, telegram_id: '-1001', title: 'Telegram films', role: 'available' }, { ...base, id: 2, telegram_id: '-9000000000001', title: 'Movies', role: 'local' }]} selected={[]} onToggle={() => undefined} onAll={() => undefined} onEdit={() => undefined} />)
    fireEvent.click(screen.getByRole('button', { name: /^All channels/ }))
    expect(screen.getAllByTitle('Folder on this PC').length).toBeGreaterThan(0)
  })
})
