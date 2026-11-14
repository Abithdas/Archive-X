import { jsx as _jsx } from "react/jsx-runtime";
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { createMemoryHistory, createRootRoute, createRoute, createRouter, RouterProvider } from '@tanstack/react-router';
import { render } from '@testing-library/react';
export function withRouter(ui, path = '/') {
    const root = createRootRoute();
    const routes = ['/', '/performers', '/performers/$id', '/studios', '/studios/$id', '/photos', '/ai', '/watch/$cid/$mid'].map((p) => createRoute({ getParentRoute: () => root, path: p, component: () => ui }));
    const router = createRouter({ routeTree: root.addChildren(routes), history: createMemoryHistory({ initialEntries: [path] }) });
    return render(_jsx(QueryClientProvider, { client: new QueryClient({ defaultOptions: { queries: { retry: false } } }), children: _jsx(RouterProvider, { router: router }) }));
}
