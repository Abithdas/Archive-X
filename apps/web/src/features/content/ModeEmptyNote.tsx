import { Link } from '@tanstack/react-router'
import { emptyHint, modeLabel } from './content'
import { useChannelMarks, useContentMode, useLocalFolders } from './useContentMode'

/** On a page with nothing to show: when the cause is that no channel or folder is in the library for the current mode, say so and point to Settings. */
export function ModeEmptyNote() {
  const mode = useContentMode().data?.mode
  const marks = useChannelMarks().data?.channels
  const local = useLocalFolders().data?.folders
  if (!mode || !marks || !local) return null
  const hint = emptyHint(mode, [...marks, ...local.map((f) => ({ in_library: f.in_library, content_rating: f.content_rating }))].filter((c, i, all) => all.indexOf(c) === i))
  if (!hint) return null
  return (
    <p role="note" className="mt-3 rounded-xl bg-warn/10 p-3 text-sm text-warn">
      Nothing is in the library as {modeLabel(mode)} yet. Put channels in the library and mark them {modeLabel(mode)} in <Link to="/settings" className="font-semibold underline">Settings, Telegram</Link>, or add a folder under <Link to="/settings" className="font-semibold underline">Local files</Link>.
    </p>
  )
}
