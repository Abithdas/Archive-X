import { beforeEach, describe, expect, it } from 'vitest'
import { useLibraryStore } from './store'

beforeEach(() => {
  localStorage.clear()
  useLibraryStore.setState({ channels: [], page: 0 })
})

describe('library store', () => {
  it('toggles channels, resets the page and persists the selection', () => {
    useLibraryStore.getState().setPage(3)
    useLibraryStore.getState().toggle('-1001')
    expect(useLibraryStore.getState().channels).toEqual(['-1001'])
    expect(useLibraryStore.getState().page).toBe(0)
    useLibraryStore.getState().toggle('-1002')
    useLibraryStore.getState().toggle('-1001')
    expect(useLibraryStore.getState().channels).toEqual(['-1002'])
    expect(JSON.parse(localStorage.getItem('ax_next_library_channels') ?? '[]')).toEqual(['-1002'])
  })
  it('never goes below page 0', () => {
    useLibraryStore.getState().setPage(-4)
    expect(useLibraryStore.getState().page).toBe(0)
  })
  it('select all and clear', () => {
    useLibraryStore.getState().selectAll(['a', 'b'])
    expect(useLibraryStore.getState().channels).toEqual(['a', 'b'])
    useLibraryStore.getState().clear()
    expect(useLibraryStore.getState().channels).toEqual([])
  })
})
