import { fireEvent, screen } from '@testing-library/react'
import { describe, expect, it, vi } from 'vitest'
import { UpNext } from './WatchPage'
import { withRouter } from '@/test/withRouter'
import type { LibraryItem } from '@/api/types'

const v = (id: number, name: string) => ({ id, _cid: '-1', name, thumbnail_id: id, duration: 60 }) as unknown as LibraryItem

describe('Up next box', () => {
  it('lists what plays next and lets the person change the source from the video page', async () => {
    const onMode = vi.fn()
    withRouter(<UpNext queue={[v(2, 'Second'), v(3, 'Third')]} mode="context" source="context" onMode={onMode} />)
    expect(await screen.findByText('Second')).toBeInTheDocument()
    const pick = screen.getByRole('combobox', { name: 'Up next source' })
    expect([...(pick as HTMLSelectElement).options].map((o) => o.text)).toEqual(['Current queue', 'Same channel', 'Same performer', 'Same studio', 'Recommended'])
    fireEvent.change(pick, { target: { value: 'performer' } })
    expect(onMode).toHaveBeenCalledWith('performer')
  })
  it('says when the chosen source had nothing else and another order is used', async () => {
    withRouter(<UpNext queue={[v(2, 'Second')]} mode="performer" source="collection" onMode={vi.fn()} />)
    expect(await screen.findByRole('status')).toHaveTextContent('Nothing else matches “Same performer” for this video, so the channel\'s order is used.')
  })
})
