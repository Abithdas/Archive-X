/** Country calling codes for the phone sign-in (same list and order as the classic picker; India is the default). */
export interface Country { iso: string; name: string; dial: string }
const RAW: Array<[string, string, string]> = [
  ['IN', 'India', '91'], ['US', 'United States', '1'], ['GB', 'United Kingdom', '44'], ['CA', 'Canada', '1'],
  ['AU', 'Australia', '61'], ['BD', 'Bangladesh', '880'], ['PK', 'Pakistan', '92'], ['LK', 'Sri Lanka', '94'],
  ['NP', 'Nepal', '977'], ['AE', 'United Arab Emirates', '971'], ['SA', 'Saudi Arabia', '966'], ['SG', 'Singapore', '65'],
  ['MY', 'Malaysia', '60'], ['ID', 'Indonesia', '62'], ['PH', 'Philippines', '63'], ['TH', 'Thailand', '66'],
  ['VN', 'Vietnam', '84'], ['JP', 'Japan', '81'], ['KR', 'South Korea', '82'], ['CN', 'China', '86'],
  ['HK', 'Hong Kong', '852'], ['TW', 'Taiwan', '886'], ['DE', 'Germany', '49'], ['FR', 'France', '33'],
  ['IT', 'Italy', '39'], ['ES', 'Spain', '34'], ['PT', 'Portugal', '351'], ['NL', 'Netherlands', '31'],
  ['BE', 'Belgium', '32'], ['CH', 'Switzerland', '41'], ['AT', 'Austria', '43'], ['SE', 'Sweden', '46'],
  ['NO', 'Norway', '47'], ['DK', 'Denmark', '45'], ['FI', 'Finland', '358'], ['PL', 'Poland', '48'],
  ['UA', 'Ukraine', '380'], ['RU', 'Russia', '7'], ['TR', 'Türkiye', '90'], ['IL', 'Israel', '972'],
  ['EG', 'Egypt', '20'], ['NG', 'Nigeria', '234'], ['KE', 'Kenya', '254'], ['ZA', 'South Africa', '27'],
  ['BR', 'Brazil', '55'], ['MX', 'Mexico', '52'], ['AR', 'Argentina', '54'], ['CL', 'Chile', '56'],
  ['CO', 'Colombia', '57'], ['NZ', 'New Zealand', '64'], ['IE', 'Ireland', '353'], ['GR', 'Greece', '30'],
  ['CZ', 'Czechia', '420'], ['RO', 'Romania', '40'], ['HU', 'Hungary', '36'], ['IR', 'Iran', '98'],
  ['IQ', 'Iraq', '964'], ['QA', 'Qatar', '974'], ['KW', 'Kuwait', '965'], ['OM', 'Oman', '968'],
  ['BH', 'Bahrain', '973'],
]
export const COUNTRIES: Country[] = RAW.map(([iso, name, dial]) => ({ iso, name, dial }))
export const DEFAULT_COUNTRY = COUNTRIES[0]!
export const sameCountry = (a: Country, b: Country) => a.iso === b.iso && a.dial === b.dial

/** Search by name, ISO code or "+dial". */
export function filterCountries(query: string, list: readonly Country[] = COUNTRIES): Country[] {
  const q = query.trim().toLowerCase()
  return list.filter((c) => !q || c.name.toLowerCase().includes(q) || c.iso.toLowerCase().includes(q) || `+${c.dial}`.includes(q))
}
/** The country whose calling code starts an international number (the longest code wins), or null. */
export function matchDial(international: string, list: readonly Country[] = COUNTRIES): Country | null {
  const d = international.replace(/\D/g, '')
  return [...list].sort((a, b) => b.dial.length - a.dial.length).find((c) => d.startsWith(c.dial)) ?? null
}
/** Builds "+<dial><national>". A pasted "+…" or "00…" number is split on its calling code and replaces the selection. */
export function composePhone(typed: string, selected: Country): { full: string; country: Country; local: string } {
  const t = typed.trim()
  let country = selected
  let local = t.replace(/\D/g, '')
  if (/^\+|^00/.test(t)) {
    const intl = (t.startsWith('00') ? t.slice(2) : t.slice(1)).replace(/\D/g, '')
    const m = matchDial(intl)
    if (m) { country = m; local = intl.slice(m.dial.length) }   // a bare code leaves the number empty
  }
  return { full: local ? `+${country.dial}${local}` : '', country, local }
}
/** A saved full number back into the selector and the national part. */
export function splitSaved(full: string): { country: Country; local: string } {
  const raw = full.trim()
  if (!raw) return { country: DEFAULT_COUNTRY, local: '' }
  const digits = raw.replace(/\D/g, '')
  const m = matchDial(digits)
  return m ? { country: m, local: digits.slice(m.dial.length) } : { country: DEFAULT_COUNTRY, local: digits }
}
export const NEED_PHONE = 'Enter your phone number.'
