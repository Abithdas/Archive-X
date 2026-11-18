# Archive X web app

The user interface shown inside the desktop window. React 19, TypeScript (strict), Vite, Tailwind CSS 4, Motion, TanStack Query and Router, Zustand, Sonner, Radix.

It only talks to the HTTP API (and the `/ws` live-status socket). In the desktop app the built files (`dist/`) are served by the Rust front door (`crates/ax-server --dist apps/web/dist`); everything else goes to the Python backend. How the pieces fit: [docs/architecture.md](../../docs/architecture.md).

```bash
npm ci
npm test             # Vitest
npm run typecheck    # tsc --noEmit
npm run unused       # knip: unused files, exports and dependencies (CI runs it)
npm run build        # type-check + production build into dist/
npm run check:size   # bundle size budget
npm run dev          # Vite dev server; proxies /api, /login and /ws to ARCHIVE_X_BACKEND (see vite.config.ts)
```

## Layout (`src/`)

| Folder | What is in it |
|---|---|
| `api/` | Fetch client with typed errors and session-loss handling, TanStack Query hooks, the `/ws` bridge into the query cache |
| `app/` | Shell (sidebar, bottom nav on phone widths, account menu, live status), router, theme |
| `design/` | Tokens, `Button`, `Skeleton`, `Empty`, `ScrambleText`, motion primitives, toast, transitions |
| `features/` | One folder per area: `library`, `photos`, `entities` (performers, studios, tags), `watch` (player), `series` (shows and movies), `content` (SFW/NSFW switch, channel marks, local folders, providers), `channels`, `operations`, `transfers`, `settings`, `ai`, `auth`, `profile` |

## Rules that matter

- **Words follow the mode.** Labels that differ between SFW and NSFW (Movies & Shows / Videos, Cast / Performers, ...) come from `features/content/labels.ts`; do not hard-code them.
- **Edits are drafts until Save.** Lists where many choices are made (channel marks, local folders) collect changes with `withPatch` / `groupPatches` and write them from a `SaveBar`.
- **Switching modes must not wait on refetches.** Mutations that change the mode close their dialog at once and let the queries refresh in the background.
- **Motion respects the reduced-motion setting.** New animations go in `design/` or the `@layer` blocks of `styles.css` with a `prefers-reduced-motion` fallback.
- Run `npm run unused` before pushing; dead exports fail CI.
