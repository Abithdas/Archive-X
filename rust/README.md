# Archive X: Rust side of the backend migration

See `docs/desktop/RUST-MIGRATION-PLAN.md`. This workspace is separate from `desktop/` so the Tauri build is unaffected.

## ax-server (phase 2: the front door)

Serves the built web app (`frontend/dist`) itself and forwards every other request to the Python backend, so route groups
can move to Rust one at a time.

```bash
cd rust
cargo test                       # 12 tests: config, static files, proxying, WebSocket, streaming, connection reuse
cargo build --release            # about 4 MB
ARCHIVE_X_PROXY_KEY=<random secret, 16+ chars> \
  target/release/ax-server --listen 0.0.0.0:8443 --upstream 127.0.0.1:18443 --dist ../frontend/dist \
  --cert <data>/auth/cert/cert.pem --key <data>/auth/cert/key.pem
```

Start the Python backend on the private port with the **same secret**:

```bash
ARCHIVE_X_PROXY_KEY=<same secret> TELEGRAM_ARCHIVE_HOST=127.0.0.1 TELEGRAM_ARCHIVE_PORT=18443 python run.py
```

How it behaves:

- **Static files** come from `--dist` with the same cache rules and security headers as the Python server, plus gzip and
  ETag/304, but only for what is certainly the front door's: a file that exists in the web folder, or one of the app's own pages
  (`/`, `/photos`, `/videos`, `/performers`, `/studios`, `/watch`, `/operations`, `/transfers`, `/settings`, `/ai`, `/people`,
  `/login`). Everything else goes to Python, which decides: it has routes this server has never heard of (for example
  `/stream-cached/...`, found by the parity harness). Paths under `api`, `stream`, `static`, `ws`, `desktop`, `health`, `ca.crt`,
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

The frozen API description is `docs/desktop/api/openapi.json` (routes: `docs/desktop/api/routes.tsv`).

## Desktop app

`launcher-core` can start `ax-server` in front of the backend (`LaunchOptions.front_door`). The Tauri shell does it only when
`ARCHIVE_X_RUST_FRONT_DOOR=1` and the binary and web app can be found; otherwise it starts exactly as before. A failure to
start the front door falls back to the plain backend.
