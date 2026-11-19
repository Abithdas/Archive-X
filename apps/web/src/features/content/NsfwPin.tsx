import { useState } from 'react'
import { toast } from 'sonner'
import { Icon } from '@/design/Icon'
import { pinProblem } from './content'
import { errorText, useChangePin, useContentMode } from './useContentMode'

const BOX = 'rounded-[18px] border border-line bg-surface p-5'
const INPUT = 'h-10 w-40 rounded-xl border border-line-2 bg-bg px-3 text-center tracking-[0.3em] focus-visible:border-accent focus-visible:outline-2 focus-visible:-outline-offset-2 focus-visible:outline-accent'

/** Settings, Security: the PIN that guards NSFW, which is optional. (The SFW / NSFW switch itself is in the profile menu.) */
export function NsfwPin() {
  const state = useContentMode()
  const change = useChangePin()
  const [current, setCurrent] = useState('')
  const [next, setNext] = useState('')
  const [msg, setMsg] = useState('')
  const set = state.data?.pin_set ?? false
  const save = () => {
    const problem = pinProblem(next)
    if (problem) { setMsg(problem); return }
    change.mutate(set ? { current, new: next } : { new: next }, {
      onSuccess: () => { setCurrent(''); setNext(''); setMsg(''); toast('PIN saved') },
      onError: (e) => setMsg(errorText(e, 'Could not save the PIN.')),
    })
  }
  return (
    <>
      <section aria-label="NSFW PIN" className={BOX}>
        <h3 className="mb-1 flex items-center gap-3 text-xl font-extrabold"><span className="text-accent"><Icon name="lock" className="size-5" /></span>NSFW PIN</h3>
        <p className="mb-4 text-sm text-muted">{set ? 'Switching into NSFW asks for this PIN. Switching back to SFW never does. You can also remove the PIN: then nothing is asked.' : 'Optional. Without a PIN, NSFW switches on with one click. Choose one if others may use this screen.'}</p>
        <form className="flex flex-wrap items-center gap-3" onSubmit={(e) => { e.preventDefault(); save() }}>
          {set ? <input type="password" inputMode="numeric" autoComplete="off" aria-label="Current PIN" placeholder="Current" value={current} onChange={(e) => { setCurrent(e.target.value.replace(/\D/g, '')); setMsg('') }} maxLength={12} className={INPUT} /> : null}
          <input type="password" inputMode="numeric" autoComplete="off" aria-label="New PIN" placeholder="New PIN" value={next} onChange={(e) => { setNext(e.target.value.replace(/\D/g, '')); setMsg('') }} maxLength={12} className={INPUT} />
          <button type="submit" disabled={change.isPending || !next} className="h-10 rounded-xl bg-accent px-4 text-sm font-bold text-accent-fg disabled:opacity-60">{set ? 'Change PIN' : 'Set PIN'}</button>
          {set ? <button type="button" disabled={change.isPending || !current} onClick={() => change.mutate({ current, remove: true }, { onSuccess: () => { setCurrent(''); setNext(''); setMsg(''); toast('PIN removed: NSFW now switches on without one') }, onError: (e) => setMsg(errorText(e, 'Could not remove the PIN.')) })} title="Type the current PIN first" className="h-10 rounded-xl border border-line-2 px-4 text-sm font-semibold hover:bg-fg/5 disabled:opacity-60">Remove PIN</button> : null}
        </form>
        <p role="status" className="mt-2 min-h-5 text-sm text-bad">{msg}</p>
      </section>
    </>
  )
}
