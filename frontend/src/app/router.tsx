import { createRootRoute, createRoute, createRouter } from '@tanstack/react-router'
import { LibraryPage } from '@/features/library/LibraryPage'
import { EntityDirectory } from '@/features/entities/EntityDirectory'
import { PerformerPage } from '@/features/entities/PerformerPage'
import { WatchPage } from '@/features/watch/WatchPage'
import { Shell } from './Shell'

function ComingSoon({ title }: { title: string }) {
  return (
    <section className="mx-auto max-w-xl p-8 text-center">
      <h1 className="text-2xl font-extrabold">{title}</h1>
      <p className="mt-2 text-muted">This page has not been migrated yet. It is still available in the classic interface.</p>
      <a className="mt-4 inline-block font-semibold text-accent underline" href="/">
        Open classic interface
      </a>
    </section>
  )
}

const root = createRootRoute({ component: Shell })
const index = createRoute({ getParentRoute: () => root, path: '/', component: LibraryPage })
const later = (path: string, title: string) => createRoute({ getParentRoute: () => root, path, component: () => <ComingSoon title={title} /> })

const performer = createRoute({
  getParentRoute: () => root,
  path: '/performers/$id',
  component: function PerformerRoute() {
    const { id } = performer.useParams()
    return <PerformerPage id={Number(id)} />
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
  watch,
  createRoute({ getParentRoute: () => root, path: '/studios', component: () => <EntityDirectory kind="studios" /> }),
  later('/operations', 'Operations'),
  later('/transfers', 'Transfers'),
  later('/settings', 'Settings'),
])

// The backend serves the app under /app-next, so the router needs that base path.
export const router = createRouter({ routeTree, basepath: '/app-next', defaultPreload: 'intent' })

declare module '@tanstack/react-router' {
  interface Register {
    router: typeof router
  }
}
