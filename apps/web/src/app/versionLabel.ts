/** The app's version as shown to the person ("v30.6.0"; a development build says "dev"). */
export const versionLabel = (v: string | undefined): string => { const t = String(v ?? '').trim(); return !t ? '' : /^\d/.test(t) ? `v${t}` : t }
