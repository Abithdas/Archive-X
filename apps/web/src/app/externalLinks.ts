/** In the desktop app a `target="_blank"` link does nothing inside the app window, so a click on a link to another website is handed to the app, which opens the person's own browser. In a browser nothing changes. */

type Invoke = (command: string, args?: Record<string, unknown>) => Promise<unknown>
const invoke = (): Invoke | undefined => (window as unknown as { __TAURI_INTERNALS__?: { invoke?: Invoke } }).__TAURI_INTERNALS__?.invoke

export function installExternalLinks(): void {
  document.addEventListener('click', (e) => {
    if (e.defaultPrevented || e.button !== 0) return
    const inv = invoke()
    const a = (e.target as Element | null)?.closest?.('a[href]') as HTMLAnchorElement | null
    if (!inv || !a) return
    let url: URL
    try { url = new URL(a.href, window.location.href) } catch { return }
    if (!/^https?:$/.test(url.protocol) || url.origin === window.location.origin) return
    e.preventDefault()
    void inv('open_external', { url: url.href }).catch(() => undefined)
  })
}
