import { noteLeft } from '@/features/entities/origin'
import { Link, createRootRoute, createRoute, createRouter, redirect } from '@tanstack/react-router'
import { lazyPage as lazy } from './lazyRetry'
const MovieDetailPage = lazy(() => import('@/features/series/MoviesPage').then((m) => ({ default: m.MovieDetailPage })))
const DiscoverTitlePage = lazy(() => import('@/features/series/DiscoverPages').then((m) => ({ default: m.DiscoverTitlePage })))
const DiscoverEpisodePage = lazy(() => import('@/features/series/DiscoverPages').then((m) => ({ default: m.DiscoverEpisodePage })))
const SeriesDetailPage = lazy(() => import('@/features/series/SeriesPage').then((m) => ({ default: m.SeriesDetailPage })))
const LibraryPage = lazy(() => import('@/features/library/LibraryPage').then((m) => ({ default: m.LibraryPage })))
const EntityDirectory = lazy(() => import('@/features/entities/EntityDirectory').then((m) => ({ default: m.EntityDirectory })))
const StudioPage = lazy(() => import('@/features/entities/StudioPage').then((m) => ({ default: m.StudioPage })))
const PerformerPage = lazy(() => import('@/features/entities/PerformerPage').then((m) => ({ default: m.PerformerPage })))
import { CastPage } from '@/features/series/CastPage'
const AddonPlayPage = lazy(() => import('@/features/series/AddonPlayPage').then((m) => ({ default: m.AddonPlayPage })))
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

const castPage = createRoute({
  getParentRoute: () => root,
  path: '/cast/$name',
  component: function CastRoute() {
    const { name } = castPage.useParams()
    return <CastPage key={name} name={name} />
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

// A stream from an add-on, in the same player as the library's videos. Only the app's own stream addresses are accepted.
const addonPlay = createRoute({
  getParentRoute: () => root,
  path: '/addon-play',
  validateSearch: (s: Record<string, unknown>) => ({
    src: typeof s.src === 'string' && /^\/api\/screen\/stream\/[\w-]{8,60}$/.test(s.src) ? s.src : '',
    name: typeof s.name === 'string' ? s.name.slice(0, 300) : '',
    title: typeof s.title === 'string' ? s.title.slice(0, 200) : undefined,   // the title's own name and picture, for Continue watching (the stream's name is a release file name)
    poster: typeof s.poster === 'string' && /^https:\/\//.test(s.poster) ? s.poster.slice(0, 500) : undefined,
    kind: s.kind === 'movie' ? 'movie' as const : 'series' as const,
    ident: typeof s.ident === 'string' && /^tt\d{5,10}$/.test(s.ident) ? s.ident : '',
    season: Number.isInteger(Number(s.season)) && s.season !== undefined && s.season !== '' ? Number(s.season) : undefined,
    episode: Number.isInteger(Number(s.episode)) && s.episode !== undefined && s.episode !== '' ? Number(s.episode) : undefined,
  }),
  component: function AddonPlayRoute() {
    const play = addonPlay.useSearch()
    return play.src ? <AddonPlayPage play={play} /> : <p role="alert" className="p-10 text-center text-sm text-warn">That stream is not available. Go back and pick it again.</p>
  },
})

const routeTree = root.addChildren([
  index,
  addonPlay,
  login,
  videos,
  createRoute({ getParentRoute: () => root, path: '/performers', component: () => <EntityDirectory kind="performers" /> }),
  performer,
  movieDetail,
  // Movies and Series are parts of Movies & Shows; an old /movies or /series link opens that part.
  createRoute({ getParentRoute: () => root, path: '/movies', beforeLoad: () => { useScreenView.getState().setView('movies'); throw redirect({ to: '/videos', replace: true }) } }),
  seriesDetail,
  discoverTitle,
  castPage,
  discoverEpisode,
  createRoute({ getParentRoute: () => root, path: '/series', beforeLoad: () => { useScreenView.getState().setView('series'); throw redirect({ to: '/videos', replace: true }) } }),
  studio,
  watch,
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
