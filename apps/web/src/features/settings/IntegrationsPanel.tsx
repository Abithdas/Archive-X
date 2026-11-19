import { useState } from 'react'
import { Icon } from '@/design/Icon'

const BOX = 'rounded-[18px] border border-line bg-surface p-5'
const RANGES = [[30, 'Next 30 days'], [90, 'Next 3 months'], [365, 'Next year']] as const

/**
 * Settings, Integrations: things that connect the library to other apps. Calendar: the coming episodes of the shows in the library as a calendar file
 * any calendar app can import (the file is made on this PC, from titles already identified; nothing is sent anywhere).
 */
export function IntegrationsPanel() {
  const [days, setDays] = useState<number>(90)
  return (
    <div className="grid gap-4">
      <section aria-label="Calendar" className={BOX}>
        <h2 className="flex items-center gap-3 text-lg font-bold"><span className="text-accent"><Icon name="clock" className="size-5" /></span>Calendar</h2>
        <p className="mb-4 mt-1 text-sm text-muted">Download the coming episodes of the shows in your library and add them to Outlook, Google Calendar, Apple Calendar or any other calendar app (Import). Only shows that have been identified are included; run it again whenever you want it refreshed.</p>
        <div className="flex flex-wrap items-center gap-2">
          <select aria-label="How far ahead" value={days} onChange={(e) => setDays(Number(e.target.value))} className="h-12 rounded-xl border border-line-2 bg-bg px-3.5 text-sm text-fg outline-none focus:border-accent/60">
            {RANGES.map(([n, label]) => <option key={n} value={n}>{label}</option>)}
          </select>
          <a href={`/api/screen/calendar.ics?days=${days}`} download="archive-x-airing.ics" className="inline-flex h-12 items-center rounded-xl bg-accent px-5 text-sm font-bold text-accent-fg">Download calendar file</a>
        </div>
      </section>
    </div>
  )
}
