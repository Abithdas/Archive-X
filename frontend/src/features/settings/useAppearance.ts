import { useQueryClient } from '@tanstack/react-query'
import { useCallback, useEffect, useRef, useSyncExternalStore } from 'react'
import { api } from '@/api/client'
import { useExperienceSettings } from './queries'
import type { CustomTheme } from '@/theme/custom'
import { SCALE_KEY, applyCustom, applyScale, applyTheme, densityFor, normalizeMode, normalizePreset, normalizeScale, storedScale, storedTheme } from '@/theme/themes'

const listeners = new Set<() => void>()
let snapshot = { ...storedTheme(), scale: storedScale() }
const emit = () => { snapshot = { ...storedTheme(), scale: storedScale() }; listeners.forEach((l) => l()) }
const subscribe = (l: () => void) => {
  snapshot = { ...storedTheme(), scale: storedScale() } // never serve a value older than what is in storage
  listeners.add(l)
  return () => void listeners.delete(l)
}
/** Current theme, mode and zoom (shared by every component that shows them). */
export const useAppearance = () => useSyncExternalStore(subscribe, () => snapshot, () => snapshot)

// Saves are chained so two quick changes cannot arrive out of order.
let chain: Promise<unknown> = Promise.resolve()
const persist = (partial: Record<string, unknown>) => (chain = chain.catch(() => undefined).then(() => api('/api/settings/experience', { method: 'POST', json: partial })))

/** Choosing a theme, mode or zoom applies it at once, remembers it locally, and saves it to the server (shared with the classic UI). */
export function useAppearanceActions() {
  const qc = useQueryClient()
  const done = () => { emit(); void qc.invalidateQueries({ queryKey: ['settings', 'experience'] }) }
  const setTheme = useCallback(async (preset: string, mode: string) => {
    const r = applyTheme(preset, mode)
    emit()
    await persist({ theme_preset: r.preset, theme_mode: r.mode }).finally(done)
  }, []) // eslint-disable-line react-hooks/exhaustive-deps
  /** Use a saved custom theme: applied at once, remembered here, and saved to the server as the "custom" preset. */
  const setCustom = useCallback(async (theme: CustomTheme, mode: string) => {
    const r = applyCustom(theme, mode)
    emit()
    await persist({ theme_preset: r.preset, theme_mode: r.mode }).finally(done)
  }, []) // eslint-disable-line react-hooks/exhaustive-deps
  const setScale = useCallback(async (value: number) => {
    const scale = applyScale(value)
    emit()
    await persist({ ui_scale: scale, grid_density: densityFor(scale) }).finally(done)
  }, []) // eslint-disable-line react-hooks/exhaustive-deps
  const previewScale = useCallback((value: number) => { applyScale(value); emit() }, [])
  return { setTheme, setCustom, setScale, previewScale }
}

/** On start the saved server choice wins (so the PC and the browser agree), like the classic loader. Runs once per session. */
export function useAppearanceSync() {
  const exp = useExperienceSettings()
  const done = useRef(false)
  useEffect(() => {
    const d = exp.data
    if (!d || done.current) return
    done.current = true
    if (d.theme_preset || d.theme_mode) applyTheme(d.theme_preset === 'custom' ? 'custom' : normalizePreset(d.theme_preset), normalizeMode(d.theme_mode))
    if (d.ui_scale != null) applyScale(normalizeScale(d.ui_scale))
    emit()
  }, [exp.data])
}
export { SCALE_KEY }

/** Header/menu light-dark switch: keeps the chosen theme family, flips the mode, and saves it like any other appearance change. */
export function toggleThemeMode() {
  const { preset, mode } = storedTheme()
  const r = applyTheme(preset, mode === 'dark' ? 'light' : 'dark')
  emit()
  void persist({ theme_preset: r.preset, theme_mode: r.mode }).catch(() => undefined)
}
