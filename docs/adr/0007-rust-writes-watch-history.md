# 0007. Rust writes its first tables: watch history, entity favourites, scene markers

**Status:** accepted.

**Context.** Phase 3 of the plan moves write routes to Rust one table group at a time (ADR 0002: a table is written by Python or by Rust, never both). `watch_history` is the right first group: it has one write route, `POST /api/play/progress`, which the player calls every few seconds, and a small, well-defined rule set (play count, watch time, completed).

**Decision.**
- `ax-db` gets a `Writer`: one read-write connection, opened on the first write, never creating the file, 5 s busy timeout, every write one `BEGIN IMMEDIATE` transaction. Everything else stays read-only.
- `POST /api/play/progress` and `GET /api/play/history/{channel}/{message}` move together (`--native-routes play_progress,play_history`). A body Python would treat specially (not an object, a number written as text, a missing field) is declined and forwarded unchanged.
- The same cross-site guard Python applies runs before the write.
- The data-change triggers that tell open pages to refresh live in the database, so a Rust write notifies the web app just as a Python one does.
- `tools/parity/compare_writes.py` runs the same requests against Python and against Python-behind-Rust and compares the answers and the rows each changed (7 progress scenarios, 29 of 29 scenarios identical on a clean fixture).

**Exception, documented.** `telegram_identity_migration.py` still updates `watch_history` once at start-up, when it renumbers old Telegram ids. It runs before the app serves requests and never at the same time as the player saving progress. It goes away with the Telegram work (Phase 2/3).

**Second group: entity favourites** (`POST/GET /api/entities/{performer|studio}/{id}/favorite`, routes `entity_favorite_toggle`, `entity_favorite_status`). Same writer, same guard; other kinds or odd ids are forwarded. 34 of 34 write scenarios identical. Documented exception: `engines/entity_identity.py` also touches `entity_favorites` when two performers are merged (move or drop the mark); it is a small transaction on other rows and goes when merging moves (Phase 3).

**Third group: scene markers** (`GET/POST /api/media/{id}/markers`, `DELETE /api/media/{id}/markers/{marker}`; routes `markers_list`, `marker_create`, `marker_delete`). Text fields are trimmed and clipped as Python does; text holding the control characters 0x1c to 0x1f, a number where text is expected, or a position written as text is forwarded. Deleting a provider marker also writes the `USER_MODIFIED` note to `provider_sync_state`, as Python does. `/markers/sync` stays in Python (it calls the providers), and `metadata/providers.py` still inserts provider markers, a second documented exception until the provider sync moves. 40 of 40 write scenarios identical.

**Consequences.** Switching `play_progress` on makes Rust the writer of `watch_history` for the session. The route stays off by default until it has been used on a real library.
