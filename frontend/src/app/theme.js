import { useSyncExternalStore } from 'react';
import { toggleThemeMode } from '@/features/settings/useAppearance';
const current = () => (document.documentElement.dataset.theme === 'light' ? 'light' : 'dark');
const subscribe = (cb) => {
    const o = new MutationObserver(cb);
    o.observe(document.documentElement, { attributes: true, attributeFilter: ['data-theme'] });
    return () => o.disconnect();
};
export const useTheme = () => useSyncExternalStore(subscribe, current, () => 'dark');
/** Flip light/dark within the current theme (also saved to the server). */
export const toggleTheme = toggleThemeMode;
