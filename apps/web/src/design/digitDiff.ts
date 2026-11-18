/** One character of the new number: `changed` when it differs from the old one, and `old` is what it replaces (same-length numbers only). */
export interface DigitPart { ch: string; changed: boolean; old?: string }

/**
 * Compares two formatted numbers for the in-place number change (SubtleNumber). Characters that stay are plain; each changed one
 * carries the old character to fade out. When the lengths differ (999 to 1,000) nothing lines up, so every character is new and
 * fades in without an old one.
 */
export function diffDigits(prev: string, next: string): DigitPart[] {
  const sameLength = prev.length === next.length
  return [...next].map((ch, i) => {
    if (sameLength && prev[i] === ch) return { ch, changed: false }
    return sameLength ? { ch, changed: true, old: prev[i] } : { ch, changed: true }
  })
}
