import { describe, expect, it } from 'vitest'
import type { LibraryItem } from '@/api/types'
import { upNext } from './WatchPage'

const v = (id: number) => ({ id, _cid: 'c' }) as LibraryItem

describe('upNext', () => {
  it('starts after the current video and wraps to the ones before it', () => expect(upNext([v(1), v(2), v(3), v(4)], v(3)).map((i) => i.id)).toEqual([4, 1, 2]))
  it('lists everything else when the current video is not in the list', () => expect(upNext([v(1), v(2)], v(9)).map((i) => i.id)).toEqual([1, 2]))
})
