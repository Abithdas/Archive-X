import { useQuery } from '@tanstack/react-query'
import { api } from '@/api/client'

type Obj = Record<string, unknown>
const q = (key: string, path: string, stale = 15_000) => ({ queryKey: ['settings', key] as const, queryFn: () => api<Obj>(path), staleTime: stale, retry: false })

export const useExperienceSettings = () => useQuery(q('experience', '/api/settings/experience', 30_000))
export const useAuthStatus = () => useQuery(q('auth', '/api/telegram/auth-status'))
export const usePreflight = () => useQuery(q('preflight', '/api/media-manager/preflight', 60_000))
export const useIconsStatus = () => useQuery(q('icons', '/api/icons/status', 60_000))
export const useMetadataSettings = () => useQuery(q('metadata', '/api/settings/metadata'))
export const useWebLogin = () => useQuery(q('weblogin', '/api/settings/weblogin'))
export const useTelegramSettings = () => useQuery(q('telegram', '/api/settings/telegram', 10_000))
