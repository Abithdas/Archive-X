import { fireEvent, render, screen } from '@testing-library/react'
import { describe, expect, it, vi } from 'vitest'
import { PhotoCard } from './PhotoCard'
import type { Photo } from './photos'

const photo = (extra: Partial<Photo> = {}): Photo => ({ id: 1, channel_id: '-1001', message_id: 7, filename: 'a.jpg', size: 1, width: 10, height: 10, thumbnail_url: '/t', preview_url: '/p', image_url: '/i', ...extra })

describe('PhotoCard delete options', () => {
  it('offers the Telegram delete for a Telegram photo', () => {
    const onRemove = vi.fn()
    render(<PhotoCard photo={photo()} favorite={false} onOpen={() => undefined} onRemove={onRemove} />)
    expect(screen.queryByText('Delete file from this PC')).toBeNull()
    fireEvent.click(screen.getByText('Delete from Archive X and Telegram'))
    expect(onRemove).toHaveBeenCalledWith('telegram')
  })
  it('offers deleting the file, and not Telegram, for a photo that is a file on this PC', () => {
    const onRemove = vi.fn()
    render(<PhotoCard photo={photo({ source_kind: 'local' })} favorite={false} onOpen={() => undefined} onRemove={onRemove} />)
    expect(screen.queryByText('Delete from Archive X and Telegram')).toBeNull()
    fireEvent.click(screen.getByText('Delete file from this PC'))
    expect(onRemove).toHaveBeenCalledWith('file')
    fireEvent.click(screen.getByText('Remove from Archive X'))
    expect(onRemove).toHaveBeenCalledWith('archive')
  })
})
