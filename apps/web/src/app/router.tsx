import { noteLeft } from '@/features/entities/origin'
import { Link, createRootRoute, createRoute, createRouter, redirect } from '@tanstack/react-router'
import { lazy } from 'react'
const MovieDetailPage = lazy(() => import('@/features/series/MoviesPage').then((m) => ({ default: m.MovieDetailPage })))
const DiscoverTitlePage = lazy(() => import('@/features/series/DiscoverPages').then((m) => ({ default: m.DiscoverTitlePage })))
const DiscoverEpisodePage = lazy(() => import('@/features/series/DiscoverPages').then((m) => ({ default: m.DiscoverEpisodePage })))
const SeriesDetailPage = lazy(() => import('@/features/series/SeriesPage').then((m) => ({ default: m.SeriesDetailPage })))
const LibraryPage = lazy(() => import('@/features/library/LibraryPage').then((m) => ({ default: m.LibraryPage })))
const EntityDirectory = lazy(() => import('@/features/entities/EntityDirectory').then((m) => ({ default: m.EntityDirectory })))
const StudioPage = lazy(() => import('@/features/entities/StudioPage').then((m) => ({ default: m.StudioPage })))
const PerformerPage = lazy(() => import('@/features/entities/PerformerPage').then((m) => ({ default: m.PerformerPage })))
const WatchPage = lazy(() => import('@/features/watch/WatchPage').then((m) => ({ default: m.WatchPage })))
const OperationsPage = lazy(() => import('@/features/operations/OperationsPage').then((m) => ({ default: m.OperationsPage })))
const TransfersPage = lazy(() => import('@/features/transfers/TransfersPage').then((m) => ({ default: m.TransfersPage })))
const SettingsPage = lazy(() => import('@/features/settings/SettingsPage').then((m) => ({ default: m.SettingsPage })))
const PhotosPage = lazy(() => import('@/features/photos/PhotosPage').then((m) => ({ default: m.PhotosPage })))
const AiPage = lazy(() => import('@/features/ai/AiPage').then((m) => ({ default: m.AiPage })))
import { usePhotoStore } from '@/features/photos/store'
import { useScreenView } from '@/features/series/view'
import { ErrorScreen } from './ErrorScreen'
import { Shell } from './Shell'

const root = createRootRoute({ component: Shell })
// The app opens on Photos; the video library lives at /videos.
const index = createRoute({ getParentRoute: () => root, path: '/', beforeLoad: () => { throw redirect({ to: '/photos', replace: true }) } })
// Signed in already: /login (the address the server prints) just goes home. Signed out, the sign-in screen is shown before any page.
const login = createRoute({ getParentRoute: () => root, path: '/login', beforeLoad: () => { throw redirect({ to: '/photos', replace: true }) } })
const videos = createRoute({ getParentRoute: () => root, path: '/videos', component: LibraryPage })

const movieDetail = createRoute({
  getParentRoute: () => root,
  path: '/movies/$id',
  component: function MovieRoute() {
    const { id } = movieDetail.useParams()
    return <MovieDetailPage id={Number(id)} />
  },
})

const seriesDetail = createRoute({
  getParentRoute: () => root,
  path: '/series/$id',
  component: function SeriesRoute() {
    const { id } = seriesDetail.useParams()
    return <SeriesDetailPage id={Number(id)} />
  },
})

const kindOf = (v: string): 'movie' | 'series' => (v === 'series' ? 'series' : 'movie')
const discoverTitle = createRoute({
  getParentRoute: () => root,
  path: '/discover/$kind/$id',
  component: function DiscoverTitleRoute() {
    const { kind, id } = discoverTitle.useParams()
    return <DiscoverTitlePage key={`${kind}:${id}`} kind={kindOf(kind)} id={id} />
  },
})
const discoverEpisode = createRoute({
  getParentRoute: () => root,
  path: '/discover/$kind/$id/$season/$episode',
  component: function DiscoverEpisodeRoute() {
    const { id, season, episode } = discoverEpisode.useParams()
    return <DiscoverEpisodePage key={`${id}:${season}:${episode}`} id={id} season={Number(season)} episode={Number(episode)} />
  },
})

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

// The native player is no longer a page of its own: it plays inside the video page. An old /native/... link opens that page with it chosen.
const nativePlayer = createRoute({
  getParentRoute: () => root,
  path: '/native/$cid/$mid',
  beforeLoad: ({ params }) => { throw redirect({ to: '/watch/$cid/$mid', params, search: { engine: 'native' } as never, replace: true }) },
})

const routeTree = root.addChildren([
  index,
  login,
  videos,
  createRoute({ getParentRoute: () => root, path: '/performers', component: () => <EntityDirectory kind="performers" /> }),
  performer,
  movieDetail,
  // Movies and Series are parts of Movies & Shows; an old /movies or /series link opens that part.
  createRoute({ getParentRoute: () => root, path: '/movies', beforeLoad: () => { useScreenView.getState().setView('movies'); throw redirect({ to: '/videos', replace: true }) } }),
  seriesDetail,
  discoverTitle,
  discoverEpisode,
  createRoute({ getParentRoute: () => root, path: '/series', beforeLoad: () => { useScreenView.getState().setView('series'); throw redirect({ to: '/videos', replace: true }) } }),
  studio,
  watch,
  nativePlayer,
  createRoute({ getParentRoute: () => root, path: '/studios', component: () => <EntityDirectory kind="studios" /> }),
  createRoute({ getParentRoute: () => root, path: '/photos', component: PhotosPage }),
  // People lives inside Photos; an old /people link opens that view.
  createRoute({ getParentRoute: () => root, path: '/people', beforeLoad: () => { usePhotoStore.getState().setTab('faces'); throw redirect({ to: '/photos', replace: true }) } }),
  createRoute({ getParentRoute: () => root, path: '/ai', component: AiPage }),
  createRoute({ getParentRoute: () => root, path: '/operations', component: OperationsPage }),
  createRoute({ getParentRoute: () => root, path: '/transfers', component: TransfersPage }),
  createRoute({ getParentRoute: () => root, path: '/settings', component: SettingsPage }),
])

// The backend serves the app at the site root.
const NotFound = () => (
  <div className="grid justify-items-center gap-3 py-24 text-center">
    <h1 className="text-xl font-bold">Page not found</h1>
    <Link to="/photos" className="font-semibold text-accent underline">Go to Photos</Link>
  </div>
)
export const router = createRouter({ routeTree, defaultPreload: 'intent', defaultNotFoundComponent: NotFound, defaultErrorComponent: ({ error, reset }) => <ErrorScreen error={error} reset={reset} /> })
router.subscribe('onResolved', ({ fromLocation, toLocation }) => { if (fromLocation?.pathname !== toLocation.pathname) noteLeft(fromLocation?.pathname) })   // a tab or filter change on the same page is not leaving it

declare module '@tanstack/react-router' {
  interface Register {
    router: typeof router
  }
}
