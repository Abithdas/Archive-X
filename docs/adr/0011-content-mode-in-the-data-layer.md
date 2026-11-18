# 0011. SFW / NSFW is enforced in the data layer

**Status:** accepted.

**Context.** The app has two modes and a whole-app promise: in SFW nothing adult is visible, in any page, search, count, direct link or file. Filtering in each route does not hold: a new route, a protocol handler or a job message that forgets the check leaks.

**Decision.**
- Read connections install SQLite TEMP views that shadow the content tables (`media`, `channels`, `photo_library_items`, performers, studios, tags and the link tables). The views read the current mode from `settings`. Python enables them per request with the `FILTER_ACTIVE` context variable; `ax-db` installs the same views on every Rust connection from `visibility.sql`, which `tools/gen_visibility_sql.py` generates from the Python text so the two cannot drift.
- `ContentFilterMiddleware` switches the filter on for content paths and `core/content_guard.py` answers 404 for a direct link to an item of the other mode.
- A channel is wholly SFW or NSFW (marked in Settings); everything found in it, and every local folder, performer, studio and tag, belongs to the mode it was marked with. Name collisions between a SFW and an NSFW record get a suffix, never a merge.
- The app starts in SFW every time; NSFW needs a PIN.
- Adult provider settings, searches and name lists, Telegram channel lists, and logs and diagnostics are closed in SFW.
- Any new code that reads content uses the filtered connection or the guard. `tools/tests/test_sfw_leaks.py` seeds adult data and calls every GET route in SFW; it must stay green.

**Consequence.** A future `ax://` thumbnail protocol (docs/architecture.md, step 2 of the streaming plan) bypasses Python's middleware, so it must read through `ax-db` (which has the views) and not through its own queries.

**Known gap.** Operations, Transfers and job text can still show adult file names in SFW.
