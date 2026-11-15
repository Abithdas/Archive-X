import { act, render, waitFor } from '@testing-library/react'
import { Outlet, RouterProvider, createMemoryHistory, createRootRoute, createRoute, createRouter } from '@tanstack/react-router'
import { useRef } from 'react'
import { describe, expect, it } from 'vitest'
import { useScrollMemory } from './scrollMemory'

function Frame() {
  const ref = useRef<HTMLElement>(null)
  useScrollMemory(ref)
  return <main ref={ref} data-testid="scroller"><Outlet /></main>
}

describe('useScrollMemory with a router', () => {
  it('restores the scroll of a page when you go back to it, and starts a new page at the top', async () => {
    const root = createRootRoute({ component: Frame })
    const routes = ['/', '/performers', '/performers/$id'].map((path) => createRoute({ getParentRoute: () => root, path, component: () => <p>{path}</p> }))
    const router = createRouter({ routeTree: root.addChildren(routes), history: createMemoryHistory({ initialEntries: ['/performers'] }) })
    const view = render(<RouterProvider router={router} />)
    const box = await view.findByTestId('scroller')
    Object.defineProperty(box, 'scrollHeight', { configurable: true, value: 5000 })
    Object.defineProperty(box, 'clientHeight', { configurable: true, value: 800 })
    box.scrollTop = 1200
    await act(async () => { await router.navigate({ to: '/performers/$id', params: { id: '7' } }) })
    await waitFor(() => expect(box.scrollTop).toBe(0))     // a fresh page starts at the top
    await act(async () => { router.history.back() })
    await waitFor(() => expect(box.scrollTop).toBe(1200))  // back finds the old spot
  })
})
