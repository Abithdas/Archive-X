# UI improvement plan (from SmoothUI and unlumen UI)

Sources read: the SmoothUI repo (README, components folder) and its site smoothui.dev; the unlumen UI docs repo and ui.unlumen.com (a script-driven site: only its navigation was readable, no component list); transitions.dev (home page text, including its library of 43+ transitions).

## What those projects are
- **SmoothUI**: MIT, React + Tailwind + shadcn registry, animated with `motion` (the library this app already uses). Light/dark, oklch colour props, `animationDuration` props, tests per component. About 150 components, e.g. animated-list, fluid-hover-highlight, glow-hover-card, expandable-cards, animated-stepper, count-badge, digit-roll, content-skeleton, empty, dock, drawer, command, fade-through, blur transitions, file-tree, coverflow-carousel, animated-file-upload, ai-* (loader, reasoning, task-list, context-meter).
- **unlumen UI**: MIT docs and public shadcn registry (Next.js, Tailwind, MDX), with small primitives and hooks (`use-controlled-state`, `get-strict-context`). Pro parts are private; only the public registry is visible.

Neither should be installed as a dependency. We copy ideas and write our own small versions, because the app already has its own theme tokens, zoom handling and Tailwind 4 setup, and shadcn components bring Radix and their own tokens.

## What transitions.dev adds
A library of 43+ transition recipes (HTML/CSS and React), plus an optional scanner, `npx transitions-agent`, that scores a codebase's motion 0 to 100 and lists findings such as "transition: all", hard-coded durations and missing `prefers-reduced-motion` guards. Recipes that fit this app directly:
- **Tabs sliding** (pill follows the active tab), **Toggle** (thumb with a double bounce), **Accordion** (grid-rows height), **Card resize**.
- **Page side-by-side** (forward/back page transition), **Modal open/close**, **Panel reveal**, **Toggle menu / dropdown morph** (origin-aware), **Tooltip** (delayed in, instant out).
- **Toast open/close** (rise, fade, blur, scale) and **Banner stacking** (stacked three deep), **Notification badge**.
- **Number pop-in / Spinning counter / Number subtle change**, **Skeleton loader and reveal** (pulse to content cross-fade), **Success check**, **Checkbox check** (stroke draw), **Icon swap**, **Text states swap**, **Streaming text**, **Shimmer text** (useful for "finding people" and live-caption status), **Avatar group hover**, **3D tilt** (poster cards).
- Their stated rules worth adopting: name the animated properties (never `transition: all`), animate transform and opacity only (an animated mask repaints the element), use duration and easing variables, guard with `prefers-reduced-motion`.
The scanner was not run: the sandbox declined to run third-party code from npm. It can be run on your PC (`npx transitions-agent` in `frontend/`) and its report pasted here.

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
13. **Audit the existing CSS** for `transition: all`, hard-coded durations and missing reduced-motion guards, and fix them (the transitions.dev scanner's own checks).
14. **Skeletons that match the final layout** for People, Performers and the watch page. (content-skeleton)
15. **Blur-in for images** (a short blur to sharp on first load) for photo tiles, avatars and posters, only for the first paint. (focus-blur-resolve)
16. **Tests**: each new piece gets a Testing Library test, and animations are turned off in jsdom through the existing reduced-motion path.

## Not recommended
- The decorative ones (aurora, gravity stars, holographic foil, ASCII render, cursor trail): they add GPU cost on a media app that is already decoding video.
- Adopting the shadcn registry wholesale: it would duplicate Dialog, Segmented, Switch and toasts that already match the app's theme and page zoom.

## Status
Batch 1 done: items 1 (page fade), 2 (sliding pill in Segmented and the Photos | People switch; sidebar not done), 3 (animated Operations jobs, Transfers lanes, Up next), 4 (counts on channel chips; job percentages not yet), 5 (shared Empty component used in Photos, performer/studio pages, directories and People), 12 (spring, fade-through and list presets in `design/motion.tsx`) and the two `transition-all` uses in item 13.

Batch 2 done: item 2 (sliding highlight now also in the sidebar), 6 (Ctrl-K palette: grouped Go to / Actions, performer and studio name search, recent choices), 11 (Find people progress card with four steps; `/api/photos/scan-status` now returns the job's `operation`).
Batch 3 done: item 4 (job percentages roll), 13 (duration and easing tokens in styles.css; reduced-motion guards were already global), 14 (People skeleton matches the real card; other pages already had shaped skeletons), 15 (`BlurImg` on channel pictures and faces; posters and video thumbnails keep their own placeholder treatment).
Batch 4: item 9 done (sliding marker on the phone bar). Item 10 dropped: Local to Telegram reads a folder on the PC by path through the folder picker, so a browser drag-and-drop zone cannot supply what it needs.
Batch 5: item 7 done (card glow on video, performer and studio cards: one document listener sets the pointer position, CSS draws the light, opacity is the only animated property; off on touch and for reduced motion).
Item 8 closed without building: a job already opens a full detail window (steps, activity, console), so inline expansion would duplicate it.
The plan is complete.

## Suggested first batch
Items 1, 2, 3, 4, 5 and 12: about a day of work, no new dependency, and the visible result is a calmer, smoother app.

## Needed from you
- Say which batch to start with.
- Optional: run `npx transitions-agent` in `frontend/` on your PC and send me the score and findings; I will fix them in the same batch.
