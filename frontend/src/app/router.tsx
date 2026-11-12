import { createRootRoute, createRoute, createRouter, redirect } from '@tanstack/react-router'
import { LibraryPage } from '@/features/library/LibraryPage'
import { EntityDirectory } from '@/features/entities/EntityDirectory'
import { StudioPage } from '@/features/entities/StudioPage'
import { PerformerPage } from '@/features/entities/PerformerPage'
import { WatchPage } from '@/features/watch/WatchPage'
import { OperationsPage } from '@/features/operations/OperationsPage'
import { TransfersPage } from '@/features/transfers/TransfersPage'
import { SettingsPage } from '@/features/settings/SettingsPage'
import { PhotosPage } from '@/features/photos/PhotosPage'
import { AiPage } from '@/features/ai/AiPage'
import { Shell } from './Shell'

const root = createRootRoute({ component: Shell })
// The app opens on Photos; the video library lives at /videos.
const index = createRoute({ getParentRoute: () => root, path: '/', beforeLoad: () => { throw redirect({ to: '/photos', replace: true }) } })
const videos = createRoute({ getParentRoute: () => root, path: '/videos', component: LibraryPage })

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
  videos,
  createRoute({ getParentRoute: () => root, path: '/performers', component: () => <EntityDirectory kind="performers" /> }),
  performer,
  studio,
  watch,
  createRoute({ getParentRoute: () => root, path: '/studios', component: () => <EntityDirectory kind="studios" /> }),
  createRoute({ getParentRoute: () => root, path: '/photos', component: PhotosPage }),
  createRoute({ getParentRoute: () => root, path: '/ai', component: AiPage }),
  createRoute({ getParentRoute: () => root, path: '/operations', component: OperationsPage }),
  createRoute({ getParentRoute: () => root, path: '/transfers', component: TransfersPage }),
  createRoute({ getParentRoute: () => root, path: '/settings', component: SettingsPage }),
])

// The backend serves the app at the site root.
export const router = createRouter({ routeTree, defaultPreload: 'intent' })

declare module '@tanstack/react-router' {
  interface Register {
    router: typeof router
  }
}
