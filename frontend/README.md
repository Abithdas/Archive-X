# Archive X frontend (React + Tailwind)

React 19, TypeScript (strict), Vite 7, Tailwind CSS 4, Motion, TanStack Query and Router, Zustand, Sonner, Radix.
Served by the backend at `/` (see `telegram_archive/web/routes_frontend.py`) and bundled into the desktop app.
Migration plan: `docs/desktop/Archive-X-MIGRATION-PLAN.md`; page-by-page checklists: `docs/desktop/Archive-X-UI-PARITY-CHECKLISTS.md`.

```bash
npm install
npm run dev          # http://localhost:5173/, proxies /api, /login and /ws to https://127.0.0.1:8443
npm test             # Vitest
npm run build        # type-check + production build into dist/ (the backend serves it)
ARCHIVE_X_BACKEND=https://127.0.0.1:8443 npm run dev
```

## What exists (phase 0 and a slice of phase 1/2)
- `src/api`: fetch client with typed errors and session-loss handling, TanStack Query hooks, `/ws` live-status bridge
  into the query cache (reconnect with backoff).
- `src/design`: tokens (`styles.css` `@theme`, dark/light), shimmer `Skeleton` (transform-only sweep, reduced-motion
  safe), `Button`, motion primitives, toast (Sonner).
- `src/app`: shell (sidebar, phone bottom nav, live indicator, theme toggle), router at the site root.
- `src/features/auth`: sign-in gate. `src/features/library`: channel selection (persisted), paginated read-only grid,
  `VideoCard` and a `VideoCardSkeleton` built from the same shell constants so they cannot drift.

Everything else (performers, studios, photos, player, operations, transfers, settings) still lives in the classic UI;
those routes show a "not migrated yet" page with a link to it.

## Next
Follow the phases in the plan: typed API from Pydantic response models, theme presets, filters and bulk edit, then
the remaining pages. Keep the parity checklists as the definition of done.
