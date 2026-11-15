import { useEffect, useRef, useState } from 'react'
import { Icon } from '@/design/Icon'
import { cn } from '@/design/cn'
import { composePhone, filterCountries, sameCountry, type Country } from './countries'
import { ClearField } from '@/design/ClearField'

/** Phone number entry: a searchable country-code picker plus the national number. A pasted +/00 number is split on its calling code. */
export function CountryPhone({ country, local, onChange, inputClass }: { country: Country; local: string; onChange: (country: Country, local: string) => void; inputClass: string }) {
  const [open, setOpen] = useState(false)
  const [q, setQ] = useState('')
  const [up, setUp] = useState(false)
  const wrap = useRef<HTMLDivElement>(null)
  const toggle = useRef<HTMLButtonElement>(null)
  const search = useRef<HTMLInputElement>(null)
  const phone = useRef<HTMLInputElement>(null)
  const list = filterCountries(q)
  useEffect(() => {
    if (!open) return
    const r = toggle.current?.getBoundingClientRect()
    if (r) { const below = window.innerHeight - r.bottom - 12, above = r.top - 12; setUp(below < Math.min(240, window.innerHeight - 40) && above > below) }
    search.current?.focus()
    const away = (e: MouseEvent) => { if (!wrap.current?.contains(e.target as Node)) setOpen(false) }
    document.addEventListener('mousedown', away)
    return () => document.removeEventListener('mousedown', away)
  }, [open])
  const pick = (c: Country) => { onChange(c, local); setOpen(false); setQ(''); phone.current?.focus() }
  const type = (v: string) => { const p = composePhone(v, country); onChange(p.country, /^\+|^00/.test(v.trim()) && p.local ? p.local : v) }
  return (
    <div className="flex gap-2">
      <div ref={wrap} className="relative" onKeyDown={(e) => { if (e.key === 'Escape' && open) { e.stopPropagation(); setOpen(false); toggle.current?.focus() } }}>
        <button ref={toggle} type="button" aria-haspopup="listbox" aria-expanded={open} aria-label={`Country code ${country.name} +${country.dial}`} onClick={() => { setOpen((o) => !o); setQ('') }} className="inline-flex h-12 items-center gap-2 rounded-xl border border-line-2 bg-bg px-3 text-[15px] font-semibold hover:bg-fg/5">
          <span>{country.iso}</span><span className="text-muted">+{country.dial}</span><Icon name="chevron-down" className="size-4 text-muted" />
        </button>
        {open ? (
          <div className={cn(up ? 't-drop-up' : 't-drop-down', 'absolute z-30 w-[min(320px,calc(100vw-48px))] rounded-xl border border-line-2 bg-surface p-2 shadow-[0_18px_50px_rgb(0_0_0/0.5)]', up ? 'bottom-full mb-1' : 'top-full mt-1')}>
            <ClearField inputRef={search} value={q} onChange={setQ} placeholder="Search country…" label="Search country" clearLabel="Clear country search" className="mb-1 h-9 rounded-lg border border-line px-2" />
            <div role="listbox" aria-label="Country calling codes" className="max-h-60 overflow-y-auto">
              {list.length ? list.map((c) => (
                <button key={`${c.iso}${c.dial}`} type="button" role="option" aria-selected={sameCountry(c, country)} onClick={() => pick(c)} className={cn('flex w-full items-center gap-3 rounded-lg px-2 py-1.5 text-left text-sm hover:bg-fg/5', sameCountry(c, country) && 'bg-accent/10')}>
                  <span className="w-7 font-bold">{c.iso}</span><span className="min-w-0 flex-1 truncate">{c.name}</span><span className="text-muted">+{c.dial}</span>
                </button>
              )) : <p className="px-2 py-2 text-sm text-muted">No country matches.</p>}
            </div>
          </div>
        ) : null}
      </div>
      <input ref={phone} type="tel" inputMode="tel" autoComplete="tel-national" value={local} onChange={(e) => type(e.target.value)} placeholder="234 567 8900" aria-label="Phone number" className={inputClass} />
    </div>
  )
}
