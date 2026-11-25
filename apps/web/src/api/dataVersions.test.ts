import { QueryClient } from '@tanstack/react-query'
import { describe, expect, it, vi } from 'vitest'
import { changedScopes, refreshChanged, watchDataVersions } from './dataVersions'
import { queryKeys } from './hooks'

describe('changedScopes', () => {
  it('names the scopes whose counter moved', () => expect(changedScopes({ videos: 1, entities: 4 }, { videos: 2, entities: 4 })).toEqual(['videos']))
  it('does nothing without a starting point or a new scope', () => {
    expect(changedScopes(undefined, { videos: 2 })).toEqual([])
    expect(changedScopes({ videos: 1 }, undefined)).toEqual([])
    expect(changedScopes({ videos: 1 }, { videos: 1, channels: 9 })).toEqual([])
  })
})

describe('refreshChanged', () => {
  it('asks again for the library when videos changed, and only then', () => {
    const client = new QueryClient()
    const spy = vi.spyOn(client, 'invalidateQueries')
    expect(refreshChanged(client, { data_versions: { videos: 1, settings: 3 } }, { data_versions: { videos: 2, settings: 3 } })).toEqual(['videos'])
    expect(spy).toHaveBeenCalledWith({ queryKey: ['library'] })
    expect(spy).not.toHaveBeenCalledWith({ queryKey: ['settings'] })
  })
  it('asks once for a key that two scopes share', () => {
    const client = new QueryClient()
    const spy = vi.spyOn(client, 'invalidateQueries')
    refreshChanged(client, { data_versions: { videos: 1, entities: 1 } }, { data_versions: { videos: 2, entities: 2 } })
    expect(spy.mock.calls.filter(([f]) => JSON.stringify(f) === JSON.stringify({ queryKey: ['library'] }))).toHaveLength(1)
  })
})

describe('watchDataVersions', () => {
  it('refreshes when a later status frame carries a moved counter, not on the first one', () => {
    const client = new QueryClient()
    const spy = vi.spyOn(client, 'invalidateQueries')
    const stop = watchDataVersions(client)
    client.setQueryData(queryKeys.status, { data_versions: { channels: 1 } })
    expect(spy).not.toHaveBeenCalled()
    client.setQueryData(queryKeys.status, { data_versions: { channels: 2 } })
    expect(spy).toHaveBeenCalledWith({ queryKey: ['channels'] })
    stop()
    spy.mockClear()
    client.setQueryData(queryKeys.status, { data_versions: { channels: 3 } })
    expect(spy).not.toHaveBeenCalled()
  })
})
