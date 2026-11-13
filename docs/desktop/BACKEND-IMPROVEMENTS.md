# Backend: simplification and improvements

Found by scanning all 187 Python files (about 100,000 lines) of `telegram_archive/`. The numbers come from a syntax-tree scan, not from running the code, so treat the counts as approximate. Nothing here has been changed yet.

## A. Speed and responsiveness (highest value)

| # | Finding | Evidence | Fix |
|---|---------|----------|-----|
| A1 | Most request handlers are `async` but read the database directly, so one slow query freezes the whole server (the cause of the 19 s face-groups freeze, already fixed for that endpoint and the lanes endpoint) | 335 of 342 handlers are `async`; about 300 direct `conn.execute` calls inside async code: `routes_library` 86, `routes_entities` 81, `automation` 37, `routes_media` 32, `metadata_channel_pipeline` 25 | A small `run_db(fn)` helper that runs the read in a worker thread; apply it to the busiest endpoints first (library list, entities, media) |
| A2 | One shared SQLite connection behind one re-entrant lock, so every read waits for every other read or write | `core/database.py`: a single `sqlite3.connect` plus `RLock` | Open a few read-only connections (WAL mode is already on) for read endpoints; keep the one writer |
| A3 | Database queries inside loops (possible N+1) | 158 spots: `database.py` 31, `routes_library` 15, `delivery_ledger` 14, `metadata_channel_pipeline` 12 | Replace with one `IN (...)` query or a join; start with the routes |
| A4 | Blocking calls inside async code | 6: `urllib.request.urlopen` x3 (`metadata/matching.py` x2, `routes_telegram`), `subprocess.run` x3 (`uploader` x2, `routes.py`) | Wrap in `asyncio.to_thread` |
| A5 | `LIKE '%text%'` searches and `SELECT *` | 38 and 128 | Check the slow ones with `EXPLAIN QUERY PLAN`; FTS5 for search; name the columns |

## B. Simplification

| # | Finding | Evidence | Fix |
|---|---------|----------|-----|
| B1 | Giant functions: one function per route file holds every route | `register_library_routes` 2,466 lines, `register_playback_routes` 1,940, `register_entity_routes` 1,178, `uploader.run` 1,121, `init_schema` 879; 90 functions over 150 lines | Split each `register_*` into a package of small files by topic (list, detail, edit, bulk); same URLs. Split `uploader.run` into stages |
| B2 | Hand-written route wiring | `web/routes.py` (932 lines) passes dozens of helpers between modules through a dict, with a separate `elif` per module | One small `Context` object holding config, db, auth and shared helpers; each module takes it and registers itself, so the `elif` chain goes away |
| B3 | Big files | `stream_server.py` 5,477, `automation.py` 4,298, `providers.py` 3,158, `database.py` 3,154, `mm_vertical_mover.py` 2,941 | Split along their existing `SECTION:` markers, one concern per file |
| B4 | Probably dead code | 49 undecorated functions with no reference anywhere (about 1,200 lines), e.g. `vertical_mobile_process_one` 218 lines, `process_vertical_video` 156, `validate_split_volumes` 71, `resolve_seek_keyframe_segment` 51 | Check each (some may be reached by name or string), then delete. The vertical mover and archiver look like an abandoned path |
| B5 | Duplicated code | `stream_browser_range` and `stream_video` have the same body; `_performer_details` exists in `matching.py` and `providers.py`; thumbnail-name helper in `media_utils` and `mm_smart_router` | Keep one copy and import it |
| B6 | Two playback route modules | `routes_playback` (1,970 lines) and `routes_playback_compat` (647) | Fold compat into playback behind one stream-selection function |

## C. Reliability and debuggability

| # | Finding | Evidence | Fix |
|---|---------|----------|-----|
| C1 | Errors swallowed silently | 1,778 broad `except Exception`; 637 of them just `pass` or `continue` (worst: `telegram_sender` 32, `torrent_engine` 31, `mm_thumbnail_engine` 27, `internal_media` 25, `preview_prefetch` 24, `watchdogs` 24) | Keep the ones that are truly best-effort, but log the rest at debug level once per cause; never swallow in upload, delete or move paths |
| C2 | `print()` instead of logging | 820 `print(` calls, 306 in `mm_vertical_mover` alone; `logging` used in only 15 files | One logger per module; the Operations log and diagnostics already read the log |
| C3 | Database upgrades run as many `ALTER TABLE` statements on every start, with no schema version | 5 `ALTER TABLE` plus `_migrate_columns`; no `user_version` | Store a schema version and run numbered migrations once |
| C4 | Module-level mutable state | 58 `global` statements; my upload resume memory is the same pattern | Move into owned objects (the Context from B2) so they can be tested and reset |

## D. Testing and tooling

| # | Finding | Evidence | Fix |
|---|---------|----------|-----|
| D1 | Very thin backend tests | 36 test functions in 15 files for 100,000 lines (the frontend has 1,149 tests); `test_hosted_mode.py` cannot be collected in this sandbox | Add tests where bugs actually happened: routes order, media tracks, transfers, upload resume, face grouping; one API smoke test per route group |
| D2 | No lint or type check in the repo | none configured | Add `ruff` (unused code, silent excepts) and `mypy` on the new, smaller modules first |
| D3 | Dependency pins are loose | `fastapi>=0.115,<1`, `uvicorn>=0.30,<1`, `aiohttp>=3.9,<4`; heavy AI packages are required | Lock a tested set (`requirements.lock`); make `faster-whisper`, `nudenet`, `opencv` optional extras so a plain install is smaller |

## Suggested order
1. A4 (6 small fixes), then A1 on the five busiest route files: biggest user-visible gain.
2. C1 and C2 for the transfer, upload and move code: makes stalls diagnosable.
3. B4 and B5: delete what is dead, remove duplicates.
4. A2 and A3 once A1 is in.
5. B1 to B3 in small steps, one route group at a time, with D1 tests written first.
6. C3, C4, D2, D3.
