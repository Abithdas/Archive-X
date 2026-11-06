# Archive X: responsive pass

Tested in headless Chromium against the running app (real routes, 72 demo videos, 12 performers, 6 studios), at 17 sizes from
320x568 to 2560x1440 including phone landscape, across 12 screens: library, filters sheet, video page, performers, performer detail,
studios, studio detail, photos, transfers, operations, settings, AI.

| Measure (204 screen x size combinations) | Before | After |
|---|---|---|
| Pages that scroll sideways / elements past the right edge | 0 | 0 |
| Visible text under 10.5 px | 526 | 39 |
| Touch targets under 30 px on screens <= 900 px wide | 369 | 48 |

## What was wrong, and what changed
1. **Large displays rendered at laptop size** (text about 11 px on a 2560 px screen). New fluid scale: 1.15x from 1800 px up to 3.6x at 7000 px, so layout stays about 1600-2100 CSS px wide. It multiplies the user's own Interface scale and also applies to dialogs and sheets.
2. **591 font sizes under 11 px** (338 at exactly 10 px). Sizes under 10 px became 10.5 px and 10 px became 11 px across 78 CSS, HTML and JS files. Only `font-size` values changed; I checked every changed file.
3. **Library card "..." button sat on top of the performer/channel rows** whenever a card was taller than wide, because it was placed at 56.25% of the card height instead of the thumbnail height. It now follows the card width. Titles clamp to two lines and tag chips wrap instead of being cut off.
4. **Cut-off items on phones:** Operations stat tiles (now a 3-column grid, 2 on 360 px and below), Jobs tab bar (wraps), and the studio chip on the video page (wraps).
5. **Blank strip beside the theme picker** on wide screens: the grid now fills its row.
6. **Touch targets:** buttons, inputs, selects and card icons are at least 36-40 px on screens up to 900 px wide, chips get a larger invisible hit area, and bottom-navigation labels went from 9.9 px to 10.5 px.

## Not fixed or not verified
- The remaining 39 small-text and 48 small-target hits are decorative hints (keyboard shortcut labels, the photo channel counter) and one sort control that is collapsed on those screens.
- Thumbnails and performer photos were stand-in gradients, because the test server has no Telegram connection. Real artwork can change card heights slightly.
- Not exercised: the player while a video is actually playing, the photo grid with photos, bulk-edit mode, and the Telegram sign-in flow. Phone-landscape filter sheets scroll but only show two fields at once.
- Tested only in Chromium. Safari and Firefox are untested; the large-display scale uses CSS `zoom`, which Safari and Firefox support in current versions but which was not checked here.
- The test server ran with Uvicorn, not the shipped Hypercorn; this does not affect the UI.

## Files
- `Archive-X-mobile-ready/`: the full app with all changes (new `css/191-responsive-fluid.css`, plus the mobile layer from earlier).
- `Archive-X-Mobile/responsive-ui.patch`: the same changes as a patch against the original zip contents (82 files).
- `Archive-X-mobile-ready/tools/responsive-check/`: the checker, so you can re-run it after future UI changes.

## Scene Match Review: smooth next-card and toast above the dialog
- **Window closed and reopened on every Approve/Reject.** The handler called `loadSceneMatchReview()`, which replaced the list with "Loading…", re-fetched page one and re-rendered every card. Now the finished card collapses and slides out (right for approve, left for reject), the next card slides up, and the list reconciles with the server in the background. Cards that vanish on the server (for example other suggestions for the same video) are animated out too, new ones are appended, and counts and "Load more" are updated. The same quiet update is used for "Reject selected" and "Approve above". Reduced-motion users get an instant removal.
- **Toast hidden behind the blurred backdrop.** The dialog is a native modal `<dialog>` in the browser's top layer, so no z-index can put a toast above it. The toast host is now a manual popover (top layer) and, while a modal dialog is open, sits inside that dialog. Without that it would be visible but unclickable, because everything outside a modal dialog is inert. `js/159-modal-viewport-centering.js` no longer pulls it back out. It returns to its normal place when the dialog closes. Desktop and phone positions are unchanged.
- Verified in Chromium with 12 seeded suggestions. Original: one "Loading…" flash per action, no animation, all cards re-rendered. Fixed: no flash, same card elements kept, slide-out played, dialog never closed, toast clickable over the dialog, no page errors. Tests are in `tools/tests/`.
- Browsers without the Popover API (before Chrome 114, Safari 17, Firefox 125) keep the old toast behaviour.

## Shimmer Skeleton: loading placeholders that match the real content
**Before:** one generic card (image block, two bars, a pill) stood in for channels, performers and studios; the library video grid, detail pages, review dialogs, job details, scene markers, provider status and backup history had no skeleton at all, only "Loading…" text. Six unused or mismatched shimmer animations existed (several animated `background-position`, which repaints every frame).

**Now (one system, `css/192-shimmer-skeleton.css` + `js/192-shimmer-skeleton.js`, `window.AXSkeleton`):**
- **Lightweight shimmer.** One shared sweep animation on a pseudo-element using only `transform` (compositor-only; no JavaScript runs while it animates), `contain: paint`, theme-token colours (dark, light and custom themes), and no animation at all under `prefers-reduced-motion`.
- **Each skeleton reuses the real classes of its content**, so sizes come from the same CSS at every breakpoint: video card (16:9 thumb with badge pills, two-line title, avatar row, channel row, tag chips), channel avatar strip, performer card, studio card, performer/studio detail page (hero, details, tabs, video grid), scene-match review card (provider image plus four frames), auto-match review rows, job-detail key/value rows, scene markers, provider tiles, backup-history rows, Operations resource tiles and inline values. Text bars take their height from the inherited line height. Widths vary from card to card.
- **No flash on fast loads.** Library pages and profile pages show their skeleton only if loading takes longer than 140 ms, and never over an already populated grid.
- **Photos:** the existing masonry placeholders (already varied heights and real aspect ratios) now use the same transform sweep.
- **Measured against the real content that replaces them** (loading held open in Chromium, 1440, 768, 390 and 320 px wide): video, performer and studio cards 0% height difference at 1440 and 768, performer hero and portrait 0%, tab bar 0%, review frames exact, review card 2%. Narrow screens differ more because real titles, bios and tag rows vary: video card +6% at 390 and +17% at 320, profile hero 2-12% short.
- Two defects found and fixed during verification: the "Loading videos" screen-reader label was visible and took a grid cell, and the real thumbnail's brand-logo placeholder showed through the video skeleton.
- Tests: `tools/tests/test_skeleton_matches_content.js` (skeleton vs real size per type and viewport) and `tools/tests/test_skeleton_states.js` (shimmer actually moves, reduced motion, review/auto-match dialogs, Operations tiles, light theme).
- Not covered: the watch page and settings forms have no skeleton (the page shell renders immediately), and loading text inside small status lines and dropdown options (for example "Loading channels…" in a select) was left as text.
