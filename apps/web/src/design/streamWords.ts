/** Splits text into words and the whitespace between them, so every word can be wrapped and the spacing kept exactly. */
export function tokens(text: string): string[] {
  return text.split(/(\s+)/).filter((t) => t !== '')
}
export const isSpace = (t: string) => /^\s+$/.test(t)

/** How long to wait before showing the next word: the usual gap, but quicker when many are queued, so the text never falls far behind what has arrived. */
export function gapFor(backlog: number, base = 60): number {
  if (backlog <= 8) return base
  return Math.max(12, Math.round((base * 8) / backlog))
}
