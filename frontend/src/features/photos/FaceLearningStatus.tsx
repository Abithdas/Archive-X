import { useQuery } from '@tanstack/react-query'
import { api } from '@/api/client'

export interface FaceLearning { ok?: boolean; faces: number; confirmed: number; removed: number; people?: Array<{ performer_id: number; name: string; confirmed: number; removed: number }> }

/** One line about what the app has learned from the faces you named and the ones you took out. */
export function faceLearningLine(d: FaceLearning | undefined): string {
  if (!d || typeof d.confirmed !== 'number' || typeof d.removed !== 'number') return ''
  if (!d.confirmed && !d.removed) return 'Face learning starts when you name people or take wrong faces out of them.'
  const n = (k: number, one: string, many: string) => `${k.toLocaleString()} ${k === 1 ? one : many}`
  return `Learning from ${n(d.confirmed, 'confirmed face', 'confirmed faces')} and ${n(d.removed, 'removed face', 'removed faces')}. Open “Check faces” on a person to see look-alikes and faces that look out of place.`
}

export function FaceLearningStatus() {
  const q = useQuery({ queryKey: ['face-learning'] as const, queryFn: () => api<FaceLearning>('/api/photos/faces/learning/status'), retry: false, staleTime: 30_000 })
  const text = faceLearningLine(q.data)
  return text ? <p className="text-xs text-muted" role="status">{text}</p> : null
}
