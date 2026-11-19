import { useQuery, useQueryClient } from '@tanstack/react-query'
import { useState } from 'react'
import { toast } from 'sonner'
import { ApiError, api } from '@/api/client'
import { Dialog } from '@/design/Dialog'
import { cn } from '@/design/cn'
import { NamePicker } from './NamePicker'
import { photoName, type Photo } from './photos'

interface PhotoFace { detection_id: number; crop_url: string; performer_id: number | null; performer_name: string }

/**
 * "Add to a person": the photo's faces, then the person they are. The chosen face is named after that person, so the photo shows under them
 * (a photo with one face needs no choosing; a photo with none has nothing to add yet).
 */
export function AddToPerson({ photo, onClose }: { photo: Photo; onClose: () => void }) {
  const qc = useQueryClient()
  const faces = useQuery({
    queryKey: ['photo-faces', photo.channel_id, photo.message_id] as const,
    queryFn: () => api<{ faces: PhotoFace[] }>(`/api/photos/faces/of-photo?channel_id=${encodeURIComponent(photo.channel_id)}&message_id=${photo.message_id}`),
    retry: false,
  })
  const list = faces.data?.faces ?? []
  const [picked, setPicked] = useState<number | null>(null)
  const [busy, setBusy] = useState(false)
  const face = picked ?? (list.length === 1 ? list[0]!.detection_id : null)

  const assign = async (p: { id: number; name: string }) => {
    if (face === null) return
    setBusy(true)
    try {
      await api('/api/photos/faces/detections/move', { method: 'POST', json: { detection_ids: [face], performer_id: p.id } })
      toast(`Added to ${p.name}`)
      await Promise.all(['photos', 'face-groups', 'performer-photos', 'performer-gallery', 'photo-faces'].map((k) => qc.invalidateQueries({ queryKey: [k] })))
      onClose()
    } catch (e) {
      toast.error(e instanceof ApiError && e.detail ? e.detail : 'Could not add this photo to that person.')
      setBusy(false)
    }
  }

  return (
    <Dialog open onClose={onClose} title="Add this photo to a person">
      <div className="grid gap-3">
        <p className="truncate text-sm text-muted" title={photoName(photo)}>{photoName(photo)}</p>
        {faces.isPending ? <p role="status" className="text-sm text-muted">Looking for faces in this photo…</p>
          : faces.isError ? <p role="alert" className="text-sm text-warn">Could not read the faces of this photo.</p>
          : !list.length ? <p role="status" className="text-sm text-muted">No face has been found in this photo yet. Use “Find people” on the People tab, then try again.</p>
          : (
            <>
              {list.length > 1 ? <p className="text-sm font-semibold">Which face?</p> : null}
              <ul aria-label="Faces in this photo" className="flex flex-wrap gap-2">
                {list.map((f) => (
                  <li key={f.detection_id}>
                    <button type="button" aria-pressed={face === f.detection_id} aria-label={f.performer_name ? `Face of ${f.performer_name}` : 'Face, not named yet'} onClick={() => setPicked(f.detection_id)}
                      className={cn('grid w-20 gap-1 rounded-xl border p-1 text-center text-[11px]', face === f.detection_id ? 'border-accent bg-accent/10' : 'border-line hover:bg-fg/5')}>
                      <img src={f.crop_url} alt="" className="aspect-square w-full rounded-lg object-cover" />
                      <span className="truncate text-muted">{f.performer_name || 'Not named'}</span>
                    </button>
                  </li>
                ))}
              </ul>
              {face !== null ? (
                <div className={cn(busy && 'pointer-events-none opacity-60')}>
                  <p className="mb-1 text-sm font-semibold">Who is it?</p>
                  <NamePicker onPick={(p) => void assign(p)} onCancel={onClose} />
                </div>
              ) : null}
            </>
          )}
      </div>
    </Dialog>
  )
}
