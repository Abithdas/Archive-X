/*
 * Adapted from SmoothUI's theme-toggle (https://smoothui.dev/docs/components/theme-toggle), MIT licence, Copyright (c) 2024 Eduardo Calvo.
 * Kept: the "sun-moon" glyph (one disc carved into a crescent by a travelling mask, rays that retract) and the "switch" (a sky-to-night
 * track with stars and a lit knob). Changed: light and dark only (the app has no "system" mode here), plain inline styles instead of the
 * shadcn colour tokens, and the app's own sun and moon icons on the knob.
 */
import { AnimatePresence, motion, useReducedMotion } from 'motion/react'
import { useEffect, useId, useState } from 'react'
import { Icon } from './Icon'

export type ThemeValue = 'light' | 'dark'

const SPRING = { bounce: 0.1, duration: 0.25, type: 'spring' as const }
const MORPH_SPRING = { bounce: 0.1, duration: 0.35, type: 'spring' as const }
const KNOB_SPRING = { bounce: 0.1, duration: 0.3, type: 'spring' as const }
const INSTANT = { duration: 0 }
const FADE = { duration: 0.3, ease: [0.23, 1, 0.32, 1] as [number, number, number, number] }

const VIEWBOX = 24
const CENTER = 12
const RAY_ANGLES = [0, 45, 90, 135, 180, 225, 270, 315]
const RAY_INNER = 7.3
const RAY_OUTER = 10.3
const RAY_STAGGER = 0.025
const RAY_WIDTH = 1.7
const RAY_RETRACTED_SCALE = 0.12

/** Where the carving circle sits: off-canvas (sun) or overlapping (crescent moon). */
const MASK_STATE: Record<ThemeValue, { cx: number; cy: number; r: number }> = { dark: { cx: 17.6, cy: 8, r: 8.6 }, light: { cx: 38, cy: 4, r: 9 } }
const DISC_RADIUS: Record<ThemeValue, number> = { dark: 8.2, light: 5 }
const RAY_ROTATION: Record<ThemeValue, number> = { dark: 60, light: 0 }
const GLYPH_COLOR: Record<ThemeValue, string> = { dark: '#818cf8', light: '#f59e0b' }

/** Rays are visible in the light theme and retract into the disc in the dark one. */
export const rayTarget = (value: ThemeValue) => (value === 'light' ? { opacity: 1, scale: 1 } : { opacity: 0, scale: RAY_RETRACTED_SCALE })

function useCanAnimate(): boolean {
  const reduce = Boolean(useReducedMotion())
  const [mounted, setMounted] = useState(false)
  useEffect(() => setMounted(true), [])   // the first paint shows the current theme without morphing into it
  return mounted && !reduce
}

function SunMoonGlyph({ value, size, canAnimate }: { value: ThemeValue; size: number; canAnimate: boolean }) {
  const maskId = `ax-theme-mask-${useId().replace(/:/g, '')}`
  const mask = MASK_STATE[value]
  const morph = canAnimate ? MORPH_SPRING : INSTANT
  return (
    <svg aria-hidden="true" focusable="false" width={size} height={size} viewBox={`0 0 ${VIEWBOX} ${VIEWBOX}`} style={{ color: GLYPH_COLOR[value], transition: 'color .3s' }}>
      <mask id={maskId}>
        <rect fill="white" width={VIEWBOX} height={VIEWBOX} x={0} y={0} />
        <motion.circle animate={mask} cx={mask.cx} cy={mask.cy} r={mask.r} fill="black" initial={false} transition={morph} />
      </mask>
      <motion.circle animate={{ r: DISC_RADIUS[value] }} cx={CENTER} cy={CENTER} r={DISC_RADIUS[value]} fill="currentColor" initial={false} mask={`url(#${maskId})`} transition={morph} />
      <motion.g animate={{ rotate: RAY_ROTATION[value] }} initial={false} style={{ transformOrigin: `${CENTER}px ${CENTER}px` }} transition={morph}>
        {RAY_ANGLES.map((angle, i) => {
          const rad = (angle * Math.PI) / 180
          return (
            <motion.line
              key={angle}
              animate={rayTarget(value)}
              initial={false}
              stroke="currentColor"
              strokeLinecap="round"
              strokeWidth={RAY_WIDTH}
              style={{ transformOrigin: `${CENTER}px ${CENTER}px` }}
              transition={canAnimate ? { ...SPRING, delay: i * RAY_STAGGER } : INSTANT}
              x1={CENTER + Math.cos(rad) * RAY_INNER}
              x2={CENTER + Math.cos(rad) * RAY_OUTER}
              y1={CENTER + Math.sin(rad) * RAY_INNER}
              y2={CENTER + Math.sin(rad) * RAY_OUTER}
            />
          )
        })}
      </motion.g>
    </svg>
  )
}

const TRACK: Record<ThemeValue, string> = {
  dark: 'linear-gradient(180deg,#1e293b 0%,#0f172a 55%,#020617 100%)',
  light: 'linear-gradient(180deg,#7dd3fc 0%,#38bdf8 55%,#0284c7 100%)',
}
const STARS = [
  { left: 0.14, size: 2, top: 0.26 }, { left: 0.29, size: 1.5, top: 0.64 }, { left: 0.45, size: 2.5, top: 0.2 },
  { left: 0.58, size: 1.5, top: 0.72 }, { left: 0.72, size: 2, top: 0.36 }, { left: 0.86, size: 1.5, top: 0.58 },
]
const KNOB_ICON: Record<ThemeValue, { icon: string; color: string }> = { dark: { icon: 'moon', color: '#334155' }, light: { icon: 'sun', color: '#f59e0b' } }

function SwitchVisual({ value, canAnimate, knob = 20, pad = 3 }: { value: ThemeValue; canAnimate: boolean; knob?: number; pad?: number }) {
  const w = knob * 2 + pad * 2
  const h = knob + pad * 2
  const night = value === 'dark'
  return (
    <span aria-hidden="true" className="relative block overflow-hidden rounded-full" style={{ height: h, width: w }}>
      {(['light', 'dark'] as const).map((layer) => (
        <motion.span key={layer} animate={{ opacity: layer === value ? 1 : 0 }} className="absolute inset-0" initial={false} style={{ background: TRACK[layer] }} transition={canAnimate ? FADE : INSTANT} />
      ))}
      {STARS.map((s, i) => (
        <motion.span
          key={`${s.left}-${s.top}`}
          animate={{ opacity: night ? 1 : 0, scale: night ? 1 : 0.4 }}
          className="absolute rounded-full bg-white"
          initial={false}
          style={{ height: s.size, left: s.left * w, top: s.top * h, width: s.size }}
          transition={canAnimate ? { ...SPRING, delay: i * RAY_STAGGER } : INSTANT}
        />
      ))}
      <span className="pointer-events-none absolute inset-0 rounded-full shadow-[inset_0_2px_6px_rgba(0,0,0,0.5),inset_0_-1px_0_rgba(255,255,255,0.2)]" />
      <motion.span
        animate={{ x: night ? knob : 0 }}
        className="absolute flex items-center justify-center rounded-full bg-gradient-to-b from-white to-slate-200 shadow-[0_3px_8px_rgba(0,0,0,0.4),inset_0_-2px_4px_rgba(0,0,0,0.12),inset_0_2px_3px_rgba(255,255,255,0.95)]"
        initial={false}
        style={{ height: knob, left: pad, top: pad, width: knob }}
        transition={canAnimate ? KNOB_SPRING : INSTANT}
      >
        <AnimatePresence initial={false} mode="wait">
          <motion.span
            key={value}
            animate={{ opacity: 1, rotate: 0, scale: 1 }}
            className="relative grid place-items-center"
            exit={canAnimate ? { opacity: 0, rotate: -120, scale: 0.4 } : { opacity: 0, transition: INSTANT }}
            initial={canAnimate ? { opacity: 0, rotate: 120, scale: 0.4 } : false}
            transition={canAnimate ? SPRING : INSTANT}
            style={{ color: KNOB_ICON[value].color }}
          >
            <Icon name={KNOB_ICON[value].icon} className="size-3" />
          </motion.span>
        </AnimatePresence>
      </motion.span>
    </span>
  )
}

/**
 * The theme control. `sun-moon` is a bare glyph for the top bar; `switch` is the sky-to-night track for a menu row.
 * A button in both cases; `onToggle` flips the theme. `className` sets its display and size (it is an inline grid when none is given).
 */
export function ThemeToggle({ theme, onToggle, variant = 'sun-moon', className }: { theme: ThemeValue; onToggle: () => void; variant?: 'sun-moon' | 'switch'; className?: string }) {
  const canAnimate = useCanAnimate()
  const label = `${theme === 'dark' ? 'Dark' : 'Light'} theme. Activate to switch to ${theme === 'dark' ? 'light' : 'dark'}.`
  return (
    <button
      type="button"
      onClick={onToggle}
      aria-label={label}
      aria-pressed={theme === 'dark'}
      title="Toggle theme"
      className={`ax-theme-toggle cursor-pointer place-items-center rounded-full focus-visible:outline-2 focus-visible:outline-accent motion-safe:active:scale-[0.97] ${className ?? 'inline-grid'}`}
    >
      {variant === 'switch' ? <SwitchVisual value={theme} canAnimate={canAnimate} /> : <SunMoonGlyph value={theme} size={26} canAnimate={canAnimate} />}
    </button>
  )
}
