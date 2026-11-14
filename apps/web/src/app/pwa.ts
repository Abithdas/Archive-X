import { create } from 'zustand'

/** The browser's "install this app" offer, kept until the person uses it or it is no longer valid. */
interface InstallEvent extends Event { prompt: () => Promise<void>; userChoice: Promise<{ outcome: 'accepted' | 'dismissed' }> }
export const useInstall = create<{ offer: InstallEvent | null; set: (e: InstallEvent | null) => void }>((set) => ({ offer: null, set: (offer) => set({ offer }) }))


export async function install(): Promise<boolean> {
  const offer = useInstall.getState().offer
  if (!offer) return false
  await offer.prompt()
  const { outcome } = await offer.userChoice
  useInstall.getState().set(null)
  return outcome === 'accepted'
}

/** Registers the offline file cache and listens for the install offer. Does nothing in dev, in tests, or where service workers are unavailable. */
export function setupPwa() {
  if (typeof window === 'undefined') return
  window.addEventListener('beforeinstallprompt', (e) => { e.preventDefault(); useInstall.getState().set(e as InstallEvent) })
  window.addEventListener('appinstalled', () => useInstall.getState().set(null))
  if (import.meta.env.PROD && 'serviceWorker' in navigator && window.isSecureContext) {
    window.addEventListener('load', () => { void navigator.serviceWorker.register('/sw.js').catch(() => { /* untrusted certificate or blocked: the app works the same without it */ }) })
  }
}
