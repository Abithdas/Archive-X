# UI improvement plan (from SmoothUI and unlumen UI)

Sources read: the SmoothUI repo (github.com/educlopez/smoothui: README and its components folder) and the unlumen UI docs repo (github.com/leovvx/unlumen-ui-docs: README and folder layout).
The three sites (smoothui.dev, ui.unlumen.com, transitions.dev) could not be opened: the sandbox network blocks them. Their entries below are not based on those pages.

## What those projects are
- **SmoothUI**: MIT, React + Tailwind + shadcn registry, animated with `motion` (the library this app already uses). Light/dark, oklch colour props, `animationDuration` props, tests per component. About 150 components, e.g. animated-list, fluid-hover-highlight, glow-hover-card, expandable-cards, animated-stepper, count-badge, digit-roll, content-skeleton, empty, dock, drawer, command, fade-through, blur transitions, file-tree, coverflow-carousel, animated-file-upload, ai-* (loader, reasoning, task-list, context-meter).
- **unlumen UI**: MIT docs and public shadcn registry (Next.js, Tailwind, MDX), with small primitives and hooks (`use-controlled-state`, `get-strict-context`). Pro parts are private; only the public registry is visible.

Neither should be installed as a dependency. We copy ideas and write our own small versions, because the app already has its own theme tokens, zoom handling and Tailwind 4 setup, and shadcn components bring Radix and their own tokens.

## Plan, in order of value for this app

### Phase 1: feel (small, safe, quick)
1. **Route transitions**: a short fade-through (150 to 200 ms, opacity + 8 px rise) between pages with `AnimatePresence` in the Shell. Respect `prefers-reduced-motion`. (fade-through)
2. **Sliding highlight** in tab bars, Segmented controls and the sidebar: one pill that glides to the active item (`layoutId`), instead of each item flashing. (fluid-hover-highlight)
3. **Animated lists**: items entering and leaving lists (jobs, queues, up next, suggestions, People) fade and slide, and the rest reflow smoothly. (animated-list)
4. **Count and progress numbers**: counts in channel chips, job percentages and storage roll to the new value rather than jump. (digit-roll, count-badge)
5. **Better empty states**: one shared Empty component with an icon, a sentence and the next action (Photos, People, Library, Operations). (empty)

### Phase 2: media-app specific
6. **Command palette upgrade** (Ctrl K): grouped results (videos, performers, studios, channels, actions), recent items, keyboard hints. (command)
7. **Card hover**: a soft glow that follows the pointer on video, performer and studio cards. Keep the current lift; add only if it stays smooth. (glow-hover-card)
8. **Expandable job cards** in Operations and Transfers, with a stepper for the multi-stage jobs. (expandable-cards, animated-stepper)
9. **Dock-style bottom bar on phones** with a sliding active marker. (dock)
10. **Upload and scan progress**: an animated file-upload dropzone for Local to Telegram. (animated-file-upload)
11. **People page**: a "finding people" progress card with the stages (download pictures, read faces, group, match) instead of the plain job line. (ai-task-list, ai-loader ideas)

### Phase 3: polish and rules
12. **Motion tokens**: `design/motion.tsx` already has durations (120 / 200 / 320 ms), one easing and reduced-motion handling. Extend it with a single popover spring and a shared fade-through and list-item preset, and move the CSS-only animations (card entrance, skeleton) onto the same values.
13. **Skeletons that match the final layout** for People, Performers and the watch page. (content-skeleton)
14. **Blur-in for images** (a short blur to sharp on first load) for photo tiles, avatars and posters, only for the first paint. (focus-blur-resolve)
15. **Tests**: each new piece gets a Testing Library test, and animations are turned off in jsdom through the existing reduced-motion path.

## Not recommended
- The decorative ones (aurora, gravity stars, holographic foil, ASCII render, cursor trail): they add GPU cost on a media app that is already decoding video.
- Adopting the shadcn registry wholesale: it would duplicate Dialog, Segmented, Switch and toasts that already match the app's theme and page zoom.

## Suggested first batch
Items 1, 2, 3, 4, 5 and 12: about a day of work, no new dependency, and the visible result is a calmer, smoother app.

## Needed from you
- Allow smoothui.dev, ui.unlumen.com and transitions.dev in the session's network settings, so I can read the actual transition recipes (especially transitions.dev) before building items 1 and 12.
- Say which batch to start with.
