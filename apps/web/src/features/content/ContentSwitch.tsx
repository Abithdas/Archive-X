import { useState } from 'react'
import { Dialog } from '@/design/Dialog'
import { Segmented } from '@/design/Segmented'
import { askFor, modeLabel, pinProblem, switchBody, MODES, type Ask, type Mode } from './content'
import { errorText, useContentMode, useSwitchMode } from './useContentMode'

/** SFW / NSFW. Towards SFW it switches at once; into NSFW it asks for the PIN (or, the first time, for a new one). */
export function ContentSwitch({ compact = false }: { compact?: boolean }) {
  const state = useContentMode()
  const change = useSwitchMode()
  const [asking, setAsking] = useState<{ target: Mode; ask: Ask } | null>(null)
  const [pin, setPin] = useState('')
  const [again, setAgain] = useState('')
  const [error, setError] = useState('')
  const current = state.data?.mode ?? 'sfw'

  const close = () => { setAsking(null); setPin(''); setAgain(''); setError(''); change.reset() }
  const choose = (target: Mode) => {
    if (!state.data || target === current) return
    const ask = askFor(target, state.data)
    if (ask === 'none') change.mutate(switchBody(target, ask, ''), { onError: (e) => setError(errorText(e, 'Could not switch.')) })
    else setAsking({ target, ask })
  }
  const submit = () => {
    if (!asking) return
    const problem = pinProblem(pin) ?? (asking.ask === 'create' && pin !== again ? 'The two PINs are different.' : null)
    if (problem) { setError(problem); return }
    change.mutate(switchBody(asking.target, asking.ask, pin), { onSuccess: close, onError: (e) => setError(errorText(e, 'Could not switch.')) })
  }
  return (
    <div className={compact ? 'grid gap-1.5' : 'grid gap-2'} data-testid="content-switch" data-ready={state.data ? 'true' : undefined}>
      <Segmented<Mode> label="Content mode" value={current} onChange={choose} options={MODES.map((m) => ({ value: m.value, label: m.label }))} />
      {!compact ? <p className="text-xs text-muted">{MODES.find((m) => m.value === current)?.hint}. Only channels in the library that are marked {modeLabel(current)} are shown.</p> : null}
      {error && !asking ? <p role="alert" className="text-xs text-bad">{error}</p> : null}
      {asking ? (
        <Dialog open onClose={close} title={asking.ask === 'create' ? 'Choose a PIN for NSFW' : 'Enter the PIN'}>
          <form className="grid gap-3" onSubmit={(e) => { e.preventDefault(); submit() }}>
            <p className="text-sm text-muted">{asking.ask === 'create' ? 'NSFW is switched on with a PIN, so a shared screen cannot show it with one click. Choose 4 to 12 digits.' : 'NSFW needs the PIN you chose.'}</p>
            <input autoFocus type="password" inputMode="numeric" autoComplete="off" aria-label={asking.ask === 'create' ? 'New PIN' : 'PIN'} value={pin} onChange={(e) => { setPin(e.target.value.replace(/\D/g, '')); setError('') }} maxLength={12} className="h-11 rounded-xl border border-line-2 bg-bg px-3 text-center text-lg tracking-[0.4em]" />
            {asking.ask === 'create' ? <input type="password" inputMode="numeric" autoComplete="off" aria-label="Repeat the PIN" value={again} onChange={(e) => { setAgain(e.target.value.replace(/\D/g, '')); setError('') }} maxLength={12} className="h-11 rounded-xl border border-line-2 bg-bg px-3 text-center text-lg tracking-[0.4em]" /> : null}
            {error ? <p role="alert" className="text-sm text-bad">{error}</p> : null}
            <div className="flex justify-end gap-2">
              <button type="button" onClick={close} className="h-11 rounded-xl border border-line-2 px-4 text-sm font-semibold">Cancel</button>
              <button type="submit" disabled={change.isPending} className="h-11 rounded-xl bg-accent px-4 text-sm font-bold text-accent-fg disabled:opacity-60">{asking.ask === 'create' ? 'Set PIN and switch' : 'Switch to NSFW'}</button>
            </div>
          </form>
        </Dialog>
      ) : null}
    </div>
  )
}
