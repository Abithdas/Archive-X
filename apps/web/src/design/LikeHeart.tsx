import { useEffect, useRef, useState } from 'react'

const DOTS = 8
/** How far the sparks fly, in px (the stylesheet moves each by --d). */
const REACH = 20
const BURST_MS = 900

/** Gives every spark its own direction, distance, size and timing, fresh each time, so the burst never looks the same twice. */
export function seedParticles(dots: ArrayLike<HTMLElement>, random: () => number = Math.random): void {
  for (let i = 0; i < dots.length; i++) {
    const s = dots[i]!.style
    s.setProperty('--a', `${((360 / dots.length) * i + (random() * 2 - 1) * 16).toFixed(1)}deg`)
    s.setProperty('--d', `${(REACH * (0.7 + random() * 0.5)).toFixed(1)}px`)
    s.setProperty('--dur', `${Math.round(520 + random() * 240)}ms`)
    s.setProperty('--delay', `${Math.round(random() * 70)}ms`)
    s.setProperty('--e', (0.35 + random() * 0.4).toFixed(2))
    s.setProperty('--s', `${(2 + random() * 2).toFixed(1)}px`)
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
    seedParticles(el.querySelectorAll<HTMLElement>('.mo-sparks i'))
    void el.offsetWidth   // reflow so the burst replays
    el.classList.add('is-bursting')
    const t = setTimeout(() => el.classList.remove('is-bursting'), BURST_MS)
    return () => clearTimeout(t)
  }, [liked])
  return (
    <span ref={box} className={`mo-like relative inline-grid place-items-center ${className ?? ''}`} data-liked={liked ? 'true' : 'false'} {...(still ? { 'data-still': '' } : {})}>
      <span className="mo-like-icon grid place-items-center">
        <svg className="mo-like-heart" width="16" height="16" viewBox="0 0 16 16" fill="none" aria-hidden="true" focusable="false" style={{ overflow: 'visible' }}>
          <path
            d="M7.99511 3.42388C6.66221 1.8656 4.4395 1.44643 2.76947 2.87334C1.09944 4.30026 0.86432 6.68598 2.17581 8.3736C3.26622 9.77674 6.56619 12.7361 7.64774 13.6939C7.76874 13.801 7.82925 13.8546 7.89982 13.8757C7.96141 13.8941 8.02881 13.8941 8.0904 13.8757C8.16097 13.8546 8.22147 13.801 8.34248 13.6939C9.42403 12.7361 12.724 9.77674 13.8144 8.3736C15.1259 6.68598 14.9195 4.28525 13.2207 2.87334C11.522 1.46144 9.32801 1.8656 7.99511 3.42388Z"
            stroke="currentColor"
            strokeWidth="1.5"
            strokeLinecap="round"
            strokeLinejoin="round"
          />
        </svg>
      </span>
      <span className="mo-sparks" aria-hidden="true">
        {Array.from({ length: DOTS }, (_, i) => <i key={i} />)}
      </span>
    </span>
  )
}
