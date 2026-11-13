# Backend: simplification and improvements

Found by scanning all 187 Python files (about 100,000 lines) of `telegram_archive/`. The numbers come from a syntax-tree scan, not from running the code, so treat the counts as approximate. Nothing here has been changed yet.

## Progress and corrections

- **A4 was a false alarm.** The first scan counted calls inside nested helper functions that already run in worker threads. A stricter scan (ignoring nested sync helpers) finds no blocking calls inside async code.
- **A1 re-measured:** 274 async functions (850 calls) still do database work directly on the event loop: `routes_library` 147, `routes_entities` 109, `automation` 47, `routes_media` 46, `providers` 41, `routes_ai` 39.
- **Done:** 30 route handlers (36 first; 6 were put back, see below) that never awaited anything were turned into plain `def` handlers, which FastAPI runs in a worker thread (`routes_jobs` 7, `routes_ai` 5, `routes_entity_pages` 4, and others). Checked: no other code awaits them (one internal call was fixed), backend tests pass, and the converted endpoints answer 200 on the live server.
- **Caught in testing, and fixed:** moving a handler into a worker thread removes the event loop, and some helpers need it. `POST /api/mapping/bulk` failed with "no running event loop" because queuing the caption sync starts an asyncio task; it now does the database work in the thread and starts the task back on the loop. An audit of the converted handlers found 6 more that call `jobs.cancel` (which cancels an asyncio task, unsafe from another thread) or the same caption-sync helper (`cancel_job`, `media_manager_stop`, `telegram_transfer_stop`, `automation_stop`, `thumbnail_index_stop`, `reconcile_verified_delivery_captions`); they are async again. `tools/tests/test_sync_handlers_safe.py` now fails if a plain `def` handler calls anything that needs the event loop.
- **Also done:** `POST /api/media/quick-edit` and `POST /api/mapping/bulk` now run their database work in worker threads; verified with real edits on the live server.
- **C1 started (silent errors):** new `core/swallowed.py`. `note("where")` inside an `except` that used to just `pass` records the place, error type, message and a count; the first time a cause is seen it is logged as a warning, after that it is only counted. 106 silent handlers in the transfer, upload and move code now report: `telegram_sender` 32, `jobs/manager` 20, `telegram_media_manager` 17, `delivery_ledger` 16, `uploader` 14, `delivery_pipeline` 4, `safe_message_move` 2, `runner` 1. Behaviour is unchanged (each handler still ignores the error). The list is at `GET /api/diagnostics/ignored-errors` and in `/api/diagnostics/summary` as `ignored_errors`. The other ~530 silent handlers in the rest of the backend are untouched; `tools/note_swallowed.py <files>` can be run to more files the same way.
- **B4 started (dead code):** removed 20 module-level functions (about 330 lines) that nothing references in the Python, docs, frontend or scripts: `apply_one`, `_decode_one_target`, `open_stream_url` (thumbnail engine), `scan_vertical_candidates`, `vertical_video_summary`, `vertical_mobile_generate_thumbnail`, `vertical_mobile_find_thumbnail`, `_verify_destination_cover_or_thumbnail`, `unstreamable_mover_main`, `validate_split_zip_stored`, `_media_row`, `_serialize_entity`, `get_send_thumbnail_bytes`, `thumb_cached_info_sync`, `ensure_thumbnail`, `sniff_image_type`, `_range_cache_manifest_path`, `_candidates`, `build_local_caption`, `_safe_dest_name`. Kept four that look like public or test helpers (`oshash_telegram`, `migrate_session_file`, `telegram_proxy_status`, `_reset_telegram_proxy_runtime_for_tests`). Checked: no new undefined names (compared with the previous version), all modules import, backend tests pass, live server endpoints answer. Not removed: methods and the large functions the first scan listed (`vertical_mobile_process_one`, `process_vertical_video`): they are named somewhere outside the Python files (docs or strings), so they need a manual look.
- **B5 done (duplicates):** `_performer_details` now lives only in `providers.py` (`matching.py` imports it); `expected_thumbnail_names_router` is now the shared `source_thumbnail_filename_candidates`; the two identical streaming generators in `routes_playback` (`stream_video`, `stream_browser_range`) are one. Verified the shared helpers return the same results and the server starts; the streaming route itself needs Telegram to exercise, so it is checked by reading only.
- **D2 started (lint):** `pyproject.toml` configures `ruff` with only bug-finding rules (syntax errors, undefined names, duplicate definitions). `pip install ruff` then `ruff check` from the repo root should say "All checks passed". The first run found a real bug, below.
- **Bug found by the lint and fixed:** a performer-picture download block from the "People" commit had been pasted into `_ensure_photo_file` (a different function) instead of the face-analysis job. Effect: any photo not already cached on disk failed with `NameError: match_people`, and the "Find people" job always failed at its summary with `NameError: pictures`. Fixed; the job now runs to completion (checked on the live server) and `tools/tests/test_face_job_paths.py` covers both places. Lesson: a stale `.pyc` can hide a fix when the new file has the same size and timestamp second; deleting `__pycache__` fixed it.
- **C1 widened:** the same `note()` was added to the silent handlers in the rest of the backend (about 366 more in 130 files, except the `mm_*` fragments, which share `telegram_media_manager`'s namespace). The backend now has about 571 reporting handlers; every one still ignores the error as before. Checked: ruff clean, every module imports as before (only the five `mm_*` fragments, which cannot be imported alone, fail, same as before), backend tests pass, live endpoints answer and nothing is logged at startup. `tools/note_swallowed.py <files>` is safe to run again (it skips handlers that already report).
- **Left for A1:** the heavy handlers mix database work with Telegram awaits (`library_move_delete` 517 lines, `photo_library_delete`, `library_delete_video`, `channel_maintenance_delete_duplicate`) or read a request body first (`media_quick_edit`, `mapping_bulk`, `performer_edit`, `studio_edit`). They need their database segments moved into `asyncio.to_thread`, one handler at a time with a test.
- `tools/tests/test_duplicate_delete_thumbnail.py` already fails with an IndexError before these changes (not caused by them); four of the older test files are plain scripts rather than pytest tests.

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
