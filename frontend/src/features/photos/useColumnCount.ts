import { useEffect, useState } from 'react'
import { columnsFor } from './masonry'

/** How many gallery columns fit the window; follows resizes. */
export function useColumnCount(): number {
  const [n, setN] = useState(() => columnsFor(typeof window === 'undefined' ? 1280 : window.innerWidth))
  useEffect(() => {
    const on = () => setN(columnsFor(window.innerWidth))
    on()
    window.addEventListener('resize', on)
    return () => window.removeEventListener('resize', on)
  }, [])
  return n
}
