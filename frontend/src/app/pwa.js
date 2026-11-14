import { create } from 'zustand';
export const useInstall = create((set) => ({ offer: null, set: (offer) => set({ offer }) }));
export const isInstalled = () => typeof window !== 'undefined' && (window.matchMedia?.('(display-mode: standalone)').matches || navigator.standalone === true);
export async function install() {
    const offer = useInstall.getState().offer;
    if (!offer)
        return false;
    await offer.prompt();
    const { outcome } = await offer.userChoice;
    useInstall.getState().set(null);
    return outcome === 'accepted';
}
/** Registers the offline file cache and listens for the install offer. Does nothing in dev, in tests, or where service workers are unavailable. */
export function setupPwa() {
    if (typeof window === 'undefined')
        return;
    window.addEventListener('beforeinstallprompt', (e) => { e.preventDefault(); useInstall.getState().set(e); });
    window.addEventListener('appinstalled', () => useInstall.getState().set(null));
    if (import.meta.env.PROD && 'serviceWorker' in navigator && window.isSecureContext) {
        window.addEventListener('load', () => { void navigator.serviceWorker.register('/sw.js').catch(() => { }); });
    }
}
