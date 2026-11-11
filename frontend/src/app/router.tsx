import { createRootRoute, createRoute, createRouter } from '@tanstack/react-router'
import { LibraryPage } from '@/features/library/LibraryPage'
import { EntityDirectory } from '@/features/entities/EntityDirectory'
import { StudioPage } from '@/features/entities/StudioPage'
import { PerformerPage } from '@/features/entities/PerformerPage'
import { WatchPage } from '@/features/watch/WatchPage'
import { OperationsPage } from '@/features/operations/OperationsPage'
import { TransfersPage } from '@/features/transfers/TransfersPage'
import { SettingsPage } from '@/features/settings/SettingsPage'
import { Shell } from './Shell'

const root = createRootRoute({ component: Shell })
const index = createRoute({ getParentRoute: () => root, path: '/', component: LibraryPage })

const performer = createRoute({
  getParentRoute: () => root,
  path: '/performers/$id',
  component: function PerformerRoute() {
    const { id } = performer.useParams()
    return <PerformerPage id={Number(id)} />
  },
})

const studio = createRoute({
  getParentRoute: () => root,
  path: '/studios/$id',
  component: function StudioRoute() {
    const { id } = studio.useParams()
    return <StudioPage id={Number(id)} />
  },
})

const watch = createRoute({
  getParentRoute: () => root,
  path: '/watch/$cid/$mid',
  component: function WatchRoute() {
    const { cid, mid } = watch.useParams()
    return <WatchPage key={`${cid}:${mid}`} cid={cid} mid={Number(mid)} />
  },
})

const routeTree = root.addChildren([
  index,
  createRoute({ getParentRoute: () => root, path: '/performers', component: () => <EntityDirectory kind="performers" /> }),
  performer,
  studio,
  watch,
  createRoute({ getParentRoute: () => root, path: '/studios', component: () => <EntityDirectory kind="studios" /> }),
  createRoute({ getParentRoute: () => root, path: '/operations', component: OperationsPage }),
  createRoute({ getParentRoute: () => root, path: '/transfers', component: TransfersPage }),
  createRoute({ getParentRoute: () => root, path: '/settings', component: SettingsPage }),
])

// The backend serves the app under /app-next, so the router needs that base path.
export const router = createRouter({ routeTree, basepath: '/app-next', defaultPreload: 'intent' })

declare module '@tanstack/react-router' {
  interface Register {
    router: typeof router
  }
}
