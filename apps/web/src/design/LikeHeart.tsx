import { useEffect, useRef, useState } from 'react'

const DOTS = 8
/** Matches --like-particle-dist in transitions.css. */
const DIST = 20
const BURST_MS = 900

/** Re-randomise the eight dots each time, so the spray never repeats (the Transitions.dev like-button snippet). */
export function seedParticles(dots: ArrayLike<HTMLElement>, random: () => number = Math.random): void {
  for (let i = 0; i < dots.length; i++) {
    const angle = (360 / dots.length) * i + (random() * 2 - 1) * 16
    const mag = DIST * (0.68 + random() * 0.5)
    const rad = (angle * Math.PI) / 180
    const s = dots[i]!.style
    s.setProperty('--px', `${(Math.cos(rad) * mag).toFixed(2)}px`)
    s.setProperty('--py', `${(Math.sin(rad) * mag).toFixed(2)}px`)
    s.setProperty('--pdur', `calc(var(--like-particle-dur) * ${(0.78 + random() * 0.44).toFixed(3)})`)
    s.setProperty('--pdelay', `${Math.round(random() * 70)}ms`)
    s.setProperty('--p-end-scale', (0.35 + random() * 0.4).toFixed(2))
    s.setProperty('--psize', (0.6 + random() * 0.8).toFixed(2))
  }
}

/**
 * A heart that fills red with a spring pop and an eight-dot burst when `liked` turns on, and quietly empties when it turns off.
 * Purely visual: put it inside the button that changes `liked`. A heart that is already liked when it first appears just shows filled.
 */
export function LikeHeart({ liked, className }: { liked: boolean; className?: string }) {
  const box = useRef<HTMLSpanElement>(null)
  const before = useRef(liked)
  const [still, setStill] = useState(true)   // true until the first change: no pop on a card that was liked all along
  useEffect(() => {
    if (before.current === liked) return
    const was = before.current
    before.current = liked
    setStill(false)
    const el = box.current
    if (!el) return
    el.classList.remove('is-bursting')
    if (!liked || was) return
    seedParticles(el.querySelectorAll<HTMLElement>('.t-like-particles i'))
    void el.offsetWidth   // reflow so the burst replays
    el.classList.add('is-bursting')
    const t = setTimeout(() => el.classList.remove('is-bursting'), BURST_MS)
    return () => clearTimeout(t)
  }, [liked])
  return (
    <span ref={box} className={`t-like relative inline-grid place-items-center ${className ?? ''}`} data-liked={liked ? 'true' : 'false'} {...(still ? { 'data-still': '' } : {})}>
      <span className="t-like-icon grid place-items-center">
        <svg className="t-like-heart" width="16" height="16" viewBox="0 0 16 16" fill="none" aria-hidden="true" focusable="false" style={{ overflow: 'visible' }}>
          <path
            d="M7.99511 3.42388C6.66221 1.8656 4.4395 1.44643 2.76947 2.87334C1.09944 4.30026 0.86432 6.68598 2.17581 8.3736C3.26622 9.77674 6.56619 12.7361 7.64774 13.6939C7.76874 13.801 7.82925 13.8546 7.89982 13.8757C7.96141 13.8941 8.02881 13.8941 8.0904 13.8757C8.16097 13.8546 8.22147 13.801 8.34248 13.6939C9.42403 12.7361 12.724 9.77674 13.8144 8.3736C15.1259 6.68598 14.9195 4.28525 13.2207 2.87334C11.522 1.46144 9.32801 1.8656 7.99511 3.42388Z"
            stroke="currentColor"
            strokeWidth="1.5"
            strokeLinecap="round"
            strokeLinejoin="round"
          />
        </svg>
      </span>
      <span className="t-like-particles" aria-hidden="true">
        {Array.from({ length: DOTS }, (_, i) => <i key={i} />)}
      </span>
    </span>
  )
}
