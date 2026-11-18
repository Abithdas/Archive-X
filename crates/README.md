# Archive X: Rust side of the backend migration

See `docs/plan.md`, section 6. These crates are members of the single Cargo workspace at the repository root (with the desktop crates); build and test from the root.

## ax-server (phase 2: the front door)

Serves the built web app (`apps/web/dist`) itself and forwards every other request to the Python backend, so route groups
can move to Rust one at a time.

```bash
cargo test -p ax-server          # config, static files, proxying, WebSocket, streaming, connection reuse, native routes
cargo build --release -p ax-server   # about 4 MB
ARCHIVE_X_PROXY_KEY=<random secret, 16+ chars> \
  target/release/ax-server --listen 0.0.0.0:8443 --upstream 127.0.0.1:18443 --dist apps/web/dist \
  --cert <data>/auth/cert/cert.pem --key <data>/auth/cert/key.pem
```

Start the Python backend on the private port with the **same secret**:

```bash
ARCHIVE_X_PROXY_KEY=<same secret> TELEGRAM_ARCHIVE_HOST=127.0.0.1 TELEGRAM_ARCHIVE_PORT=18443 python -m telegram_archive
```

How it behaves:

- **Static files** come from `--dist` with the same cache rules and security headers as the Python server, plus gzip and
  ETag/304, but only for what is certainly the front door's: a file that exists in the web folder, or one of the app's own pages
  (`/`, `/photos`, `/videos`, `/performers`, `/studios`, `/watch`, `/operations`, `/transfers`, `/settings`, `/ai`, `/people`,
  `/login`). Everything else goes to Python, which decides: it has routes this server has never heard of (for example
  `/stream-cached/...`, found by the parity harness). Paths under `api`, `stream`, `static`, `ws`, `desktop`, `health`,
  `hls`, `app-next`, `test-bridge`, `openapi.json`, `docs` and `redoc` always go to Python.
- **Limits.** A client has 30 s to send its request headers (this also ends idle keep-alive connections), 10 s for the TLS
  handshake; at most 2048 connections; on shutdown requests in flight get 10 s to finish. HTTP/2 is used only when TLS
  negotiated it.
- **Client address.** Python only sees connections from the front door, so the front door sends the real address in
  `X-Archive-X-Peer`, signed with `X-Archive-X-Proxy-Key` (the shared secret). Python's `TrustedProxyMiddleware`
  (`telegram_archive/core/http_middleware.py`) then restores `request.client`; any `X-Forwarded-For` or peer headers a client
  sends itself are removed. This keeps the "only from this PC" checks (first-run setup, folder picker, test bridge) working.
- **The backend must be on this machine**: `--upstream` is refused unless it is a loopback address, because the proxy accepts
  the backend's self-signed certificate. Use `--upstream-plain` when the backend speaks plain HTTP (desktop mode).
- Responses are streamed, never buffered; WebSocket and other Upgrade requests are tunnelled; idle backend connections are
  kept and reused.

## Comparing against Python

`tools/parity/compare.py` sends the same GET requests to two servers and reports every difference:

```bash
python tools/parity/compare.py --a https://127.0.0.1:18443 --b https://127.0.0.1:8443 --user admin --password ... --discover --queries
```

- Without options: every GET route of the frozen API that has no path parameter.
- `--discover`: also the routes with path parameters (`/api/performers/{id}/...`, `/api/play/info/{channel}/{message}`,
  `/api/photos/{channel}/{message}/thumbnail`, ...), filled with real ids found on server A. Routes that are random, huge,
  or start Telegram work are skipped (`SKIP_PARAM_ROUTES`).
- `--queries`: also the list routes with the query variants the web app sends (`tools/parity/queries.txt`: every sort order,
  paging, search, filters, favourites, face scopes).
- It needs a **populated** database (the library answers nothing for an empty one), and a login with the same account on both.
- A summary of the status codes server A gave shows how much of the run is real answers and how much is errors.
- Python rate-limits `/login` (30 per minute), so do not run it many times in a row.

The frozen API description is `docs/api/openapi.json` (routes: `docs/api/routes.tsv`).

## Desktop app

`launcher-core` can start `ax-server` in front of the backend (`LaunchOptions.front_door`). The Tauri shell does it by default whenever
the binary and web app can be found (set `ARCHIVE_X_RUST_FRONT_DOOR=0` to turn it off); otherwise it starts exactly as before. A failure to
start the front door falls back to the plain backend.

## ax-db, sessions and the cross-site guard (steps 3 and 4)

Building blocks for the routes Rust answers itself. They are used only by routes you switch on (next section).

- **`ax-db`** opens the same SQLite file **read-only** (`query_only`, WAL, 5 s busy timeout) in a small pool and runs every
  query on a blocking thread. Python stays the only writer. It never creates a database.
- **Sessions** (`ax-db/src/sessions.rs`, `ax-server/src/auth.rs`): the `session` cookie is hashed with SHA-256 and looked up in the
  `web_sessions` table Python writes. A live session is let in; anything else gets Python's exact answer,
  `401 {"detail":"Authentication required"}`; a database that cannot be read gives `503`, never a pass. Python ends a session by
  deleting its row, so a logout takes effect in Rust at once. Python slides a session's expiry forward on every use but writes it to the
  database only every 10 minutes, so Rust can refuse a session in its last minutes a little early, and requests that Rust
  answers alone do not slide it: when the first route moves, the front door must tell Python about activity (a cheap authenticated
  call at most every 10 minutes per session).
- **Cross-site guard** (`ax-server/src/guard.rs`): a port of Python's `BrowserMutationGuardMiddleware`, checked against
  `tests/origin_vectors.json` (7,938 cases produced by the real Python middleware).
- **Schema contract.** `ax-db/schema/schema.json` is a snapshot of the 102 tables Python creates (`tools/parity/dump_schema.py`);
  `REQUIRED_COLUMNS` lists what the Rust code reads and a test checks it against the snapshot. When Python changes a table the
  snapshot check (Python test and CI step) fails, and the diff shows what moved.

## Tools in tools/parity

| Script | Use |
|--------|-----|
| `compare.py` | GET routes against two servers (see above) |
| `compare_writes.py` | the POST scenarios in `writes.json` against two servers that each have their own copy of the same database: compares the answers **and the rows each request added or removed, per table** (time columns ignored) |
| `seed_fixture.py` | builds the small deterministic database both need: 72 videos, 32 photos, 3 face groups (two builds are identical) |
| `dump_schema.py` | writes or checks the schema snapshot |
| `gen_origin_vectors.py` | writes or checks the guard test vectors |

Comparing two servers on fresh copies of the fixture:

```bash
python tools/parity/seed_fixture.py --data-dir /tmp/A
python tools/parity/seed_fixture.py --data-dir /tmp/B
# start one server per folder (ARCHIVE_X_DATA_DIR, TELEGRAM_ARCHIVE_PORT, TELEGRAM_ARCHIVE_PLAYER_PORT), set up the
# same login on both with POST /api/auth/setup {"username","password","confirmation"}, and run seed_fixture.py again
# once the servers are up (they re-create the library views at start, which hides the videos until the seed runs again)
python tools/parity/compare_writes.py --a https://127.0.0.1:18450 --a-data /tmp/A --b https://127.0.0.1:18451 --b-data /tmp/B --user admin --password ...
```

## Routes answered by Rust (step 6)

Off by default: with no `--native-routes`, every request that is not a web-app file goes to Python exactly as before.

```bash
ARCHIVE_X_PROXY_KEY=<secret> target/release/ax-server ... \
  --db <data>/database/archive.db --native-routes photos_channels      # or ARCHIVE_X_DB / ARCHIVE_X_NATIVE_ROUTES
```

- **Switching a route back to Python** needs no rebuild: remove its name from `--native-routes` (or use `none`) and restart the front door.
  An unknown name stops the start with the list of valid names (`native::ROUTES`).
- **`X-Served-By`** on every answer says who produced it: `rust` (a native route or a web-app file) or `python` (forwarded). It is also
  how `compare.py` reports how many of its requests Rust answered: `answered by (server B, X-Served-By): {...}`.
- **Same answer as Python.** The first route, `GET /api/photos/channels`, runs the same two SQL queries, produces the same compact JSON in the
  same column order, and is finished the way Python's response middleware finishes it: ETag (Adler-32 of the body), `304` on a matching
  `If-None-Match`, gzip from 1,024 bytes, `Cache-Control: private, no-cache` (`ax-server/src/optimize.rs`). Verified byte for byte against the
  running backend, including photos whose channel is not saved.
- **Login:** the same session rules as above (`401` with Python's text; `503` if the database cannot be read).
- **Session keep-alive:** a session used only on native routes would stop sliding (Python extends it on every request it answers). So, at most
  every 5 minutes per session, the front door makes one cheap call (`GET /api/app/ping`) to Python with the session cookie and the signed client address.
- **Still works when Python is down:** native routes and the web app keep answering; everything else gets a clear `502`.
- To add a route: write the handler in `native.rs`, add its name to `ROUTES` and `route_for`, add the tables and columns it reads to
  `ax_db::REQUIRED_COLUMNS`, add a test next to `tests/native_route.rs`, then run `compare.py` (and `compare_writes.py` for a write route) with the route on.

### Routes that can decline a request

A native route may hand a particular request back to Python (`handle` returns `None`), which then answers it exactly as if the route were off.
`performers_list` (`GET /api/performers`) is the example: Rust answers the plain catalogue (no search text, a `limit` that is plain digits or
letters, a non-empty table) and leaves to Python anything where Python's behaviour is subtle or where it writes: a search (`q=`, FTS and LIKE
fallbacks), `limit` written as `-3`, `1_000`, ` 7`, `+7` or `1.5` (Python's `int()` accepts several of these), a repeated parameter, an empty
catalogue (Python fills it in from old data) and an empty answer (Python re-checks on its main connection). Python's rule that an answer over
2 MiB is sent untouched (no ETag, no gzip, no Cache-Control) is copied, so the full 4.4 MB performer list is byte-identical too.

### Finding what to port: `compare.py --timing` and `seed_load.py`

`tools/parity/seed_load.py` adds a big library on top of the fixture (4,000 performers, 1,200 studios, 40,000 photos, 600 face groups). With
`compare.py --timing` it prints the slowest routes. On the small fixture everything takes under 10 ms and nothing stands out; on the big one:

| Route | Python |
|-------|--------|
| `/api/performers/duplicate-audit` | **33,000 ms** |
| `/api/photos/faces/groups` (default, with suggestions) | 1,200 ms |
| `/api/channels/{id}/videos`, `/api/studios/{id}/videos`, `/api/tags/{id}/videos` | 140 to 195 ms |
| `/api/performers`, `/api/library` (no channels) | 150 to 170 ms |
| `/api/library?channels=...` | 70 to 80 ms |

### Read settings matter more than the language (found by benchmarking)

The first Rust version of the face-groups route was **8 times slower than Python under 4 parallel clients** (4.1 against 30 requests per second) because
the Rust read connections used SQLite's defaults while Python's read connections (core/database.py) use a 32 MB page cache, a 256 MB memory map and
in-memory temporary tables. A heavy query over a big library re-reads pages again and again with the small defaults. `ax-db` now sets the same three
pragmas. After that the route is on par or slightly ahead (1.0 to 1.3 times), because the same SQLite queries dominate either way; the performer
list keeps its large advantage under concurrency (15 to 32 times with 4 to 8 clients) because Python serialises those requests on its database lock.
Lesson for every route that follows: measure single and concurrent requests on the big library before calling a port finished.

### The video lists (channel, studio, tag) and a slow query worth fixing in Python too

`GET /api/channels/{id}/videos`, `/api/studios/{id}/videos` and `/api/tags/{id}/videos` are answerable by Rust (`channel_videos`, `studio_videos`,
`tag_videos`). Ids must be plain integers (`-001001` is fine, `+5`, ` 5`, `1_0` go to Python, as does `performer_id=` on the studio list); the
performer list stays in Python (it writes the Unknown Performer profile and builds a provider URL with Python logic). Each list has its own small
differences in what it adds to every video (`media_id`, `local_performers` as a list or as raw text, the name preferring the title or the file name,
`channel_title`, `library_ready`), and these are copied exactly and tested.

**The finding.** The original queries join performers, local performers, studios and tags to every video in one statement, so the rows multiply:
on the big library, 18 videos produced **136,965 rows before grouping** (113 performers x 26 studios x 3 tags per video) and the channel list
took 190 ms. The Rust routes fetch each of the four lists with its own sub-query: the same query part takes **2.4 ms**. The lists are identical
(and in the same order) for all 72 videos on both test databases; SQLite does not promise an order for `GROUP_CONCAT(DISTINCT ...)`, so this
rests on observed behaviour and on the parity harness, not on a guarantee. **The same rewrite would fix the Python routes** (and `/api/library`)
with no Rust at all: `native.rs::name_lists` has the SQL.

Measured on the big library, one client: channel list 191 ms to 6 ms (32 times), 8 clients 7 to 385 requests per second (54 times). Caution
about the tag and studio lists: they read the `archive_x_library_media` view, which the server re-creates itself, and the cost of that view changed
between my measurements (Python took 175 ms, then 2 ms, with no change by me), so there is no reliable single number for them; in the slow state
Rust was 1.8 to 2.9 times faster, in the fast state about 2 times. The byte-for-byte parity held in both states.
