import { ContentSwitch } from '@/features/content/ContentSwitch'
import { forgetModeData } from '@/features/content/modeSwap'
import { useQueryClient } from '@tanstack/react-query'
import { useId, useRef, useState } from 'react'
import { SESSION_LOST_EVENT, api } from '@/api/client'
import { useDesktopInfo, useProfile, useStatus } from '@/api/hooks'
import { displayName } from '@/features/profile/profile'
import { AccountAvatar, ProfileEditor } from '@/features/profile/ProfileEditor'
import { Icon } from '@/design/Icon'
import { cn } from '@/design/cn'
import { install, useInstall } from './pwa'

const ROW = 'flex h-11 w-full items-center gap-3 rounded-xl px-3 text-left text-[15px] font-medium hover:bg-fg/5 focus-visible:bg-fg/5 focus-visible:outline-none'

/** The green dot on the avatar stands in for the Telegram pill while the bar is too narrow to show the pill (`pillFrom` is where the pill appears). Avatar button and its menu: content mode, profile, theme, sign out, exit. (Telegram, storage and settings are in Settings; the command palette is Ctrl K.) */
export function AccountMenu({ live, pillFrom = 'sm' }: { live: string; pillFrom?: 'sm' | 'lg' }) {
  const id = useId()
  const pop = useRef<HTMLDivElement>(null)
  const qc = useQueryClient()
  const profile = useProfile()
  const status = useStatus()
  const desktop = useDesktopInfo()
  const name = displayName(profile.data) || 'A'
  const [editing, setEditing] = useState(false)
  const offer = useInstall((st) => st.offer)
  const tg = Boolean(status.data?.telegram_connected ?? status.data?.telegram?.connected)
  const close = () => pop.current?.hidePopover?.()

  const signOut = async () => {
    close()
    try {
      await api('/logout', { method: 'POST', json: {} })
    } catch {
      /* the session may already be gone */
    }
    qc.clear()
    forgetModeData()   // the other mode's remembered screens go with the session
    window.dispatchEvent(new Event(SESSION_LOST_EVENT))
  }

  return (
    <>
      <button type="button" popoverTarget={id} aria-label="Account menu" aria-haspopup="menu" className="relative grid size-10 place-items-center rounded-full bg-surface-3 text-[15px] font-bold" title={`${tg ? 'Telegram connected' : 'Telegram not connected'} · live updates: ${live}`}>
        <AccountAvatar className="size-10 text-[15px]" />
        {tg ? <i data-live="true" aria-hidden="true" className={cn('ax-live-dot absolute -right-px -top-px size-2 bg-good ring-[1.5px] ring-bg', pillFrom === 'lg' ? 'lg:!hidden' : 'sm:!hidden')} /> : null}
      </button>
      <div ref={pop} id={id} popover="auto" role="menu" aria-label="Account menu" className="mo-menu fixed right-4 top-[60px] left-auto bottom-auto m-0 w-[280px] rounded-[18px] border border-line-2 bg-surface text-fg shadow-[0_18px_50px_rgb(0_0_0/0.5)]">
        <div className="mo-fold-inner p-2">
        <div className="px-3 pb-1.5 pt-2 text-xs text-muted">Signed in as <b className="text-fg">{name}</b></div>
        <div className="flex items-center justify-between gap-3 px-3 pb-2 pt-1"><span className="text-sm font-semibold text-muted">Content</span><ContentSwitch compact onDone={close} /></div>
        <button type="button" role="menuitem" className={ROW} onClick={() => { close(); setEditing(true) }}>
          <Icon name="edit" className="text-muted" />Edit profile
        </button>
        {offer && !desktop.data ? (
          <button type="button" role="menuitem" className={ROW} onClick={() => { close(); void install() }}>
            <Icon name="download" className="text-muted" />Install app
          </button>
        ) : null}
        <div role="separator" className="my-1.5 h-px bg-line" />
        {desktop.data ? null : (
          <button type="button" role="menuitem" className={cn(ROW, 'text-[13px]')} onClick={() => void signOut()}>
            <Icon name="lock" className="text-muted" />Sign out
          </button>
        )}
      </div>
      </div>
      {editing ? <ProfileEditor onClose={() => setEditing(false)} /> : null}
    </>
  )
}
