import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { createMemoryHistory, createRootRoute, createRoute, createRouter, RouterProvider } from '@tanstack/react-router'
import { render } from '@testing-library/react'
import type { ReactElement } from 'react'

export function withRouter(ui: ReactElement, path = '/') {
  const root = createRootRoute()
  const routes = ['/', '/performers', '/performers/$id', '/watch/$cid/$mid'].map((p) => createRoute({ getParentRoute: () => root, path: p, component: () => ui }))
  const router = createRouter({ routeTree: root.addChildren(routes), history: createMemoryHistory({ initialEntries: [path] }) })
  return render(<QueryClientProvider client={new QueryClient({ defaultOptions: { queries: { retry: false } } })}><RouterProvider router={router} /></QueryClientProvider>)
}
