/** A tiny picture of the app in a theme's own colours: sidebar, top bar and a few cards. Decorative; the card it sits in carries the name. */
export interface PreviewColors { bg: string; surface: string; card: string; thumb: string; text: string; muted: string; accent: string }

export function ThemePreview({ c }: { c: PreviewColors }) {
  return (
    <span aria-hidden="true" className="block aspect-[16/10] w-full overflow-hidden rounded-lg border border-black/20" style={{ background: c.bg }}>
      <span className="flex h-full">
        <span className="flex w-[24%] flex-col gap-[5%] p-[4%]" style={{ background: c.surface }}>
          <i className="block h-[9%] w-[45%] rounded-sm" style={{ background: c.accent }} />
          <i className="block h-[7%] w-full rounded-sm opacity-90" style={{ background: c.accent, opacity: 0.35 }} />
          <i className="block h-[7%] w-[80%] rounded-sm" style={{ background: c.muted, opacity: 0.45 }} />
          <i className="block h-[7%] w-[65%] rounded-sm" style={{ background: c.muted, opacity: 0.45 }} />
        </span>
        <span className="flex min-w-0 flex-1 flex-col gap-[6%] p-[4%]">
          <span className="flex items-center gap-[4%]">
            <i className="block h-[7px] flex-1 rounded-full" style={{ background: c.surface }} />
            <i className="block size-[7px] rounded-full" style={{ background: c.accent }} />
          </span>
          <span className="grid flex-1 grid-cols-3 gap-[4%]">
            {[0, 1, 2].map((i) => (
              <span key={i} className="flex flex-col overflow-hidden rounded-[3px]" style={{ background: c.card }}>
                <i className="block flex-1" style={{ background: c.thumb }} />
                <i className="mx-[10%] mt-[12%] block h-[3px] rounded-full" style={{ background: c.text, opacity: 0.85 }} />
                <i className="mx-[10%] mb-[12%] mt-[8%] block h-[3px] w-[55%] rounded-full" style={{ background: i === 0 ? c.accent : c.muted, opacity: i === 0 ? 1 : 0.6 }} />
              </span>
            ))}
          </span>
        </span>
      </span>
    </span>
  )
}
