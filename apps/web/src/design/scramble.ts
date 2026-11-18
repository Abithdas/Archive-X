const GLYPHS = 'ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789#%&*+=?'

/**
 * One frame of a scramble-decode: letters left of `progress` (0..1) already read as the new text, the rest are random glyphs.
 * Spaces and punctuation never scramble, so the shape of the word stays readable while it settles.
 */
export function scrambleFrame(to: string, progress: number, rand: () => number = Math.random): string {
  const settled = Math.floor(Math.max(0, Math.min(1, progress)) * to.length)
  let out = ''
  for (let i = 0; i < to.length; i++) {
    const ch = to.charAt(i)
    out += i < settled || !/[A-Za-z0-9]/.test(ch) ? ch : GLYPHS.charAt(Math.floor(rand() * GLYPHS.length))
  }
  return out
}
