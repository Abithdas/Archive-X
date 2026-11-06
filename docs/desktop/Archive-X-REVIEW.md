# Archive X v29.5.400 — Code review (bugs and security)

**Scope and method.** I read the launcher, `main.py`, `app.py`, auth, sessions, middleware, TLS and certificate handling, the
test bridge, the file-serving and fetch routes, the backup and restore path, the playback feed token, secret storage, and the
`.bat` and `.ps1` scripts. I also grepped all 182 Python files for risky patterns (`shell=True`, `eval`/`exec`, `pickle`,
SQL built with f-strings, unverified TLS, outbound fetches), compiled everything, and ran pyflakes.
This is a **static review of a 165k-line codebase**. I did not run the app, and I did not read every route handler in full.
Treat "no finding" as "nothing found in what I read", not as a clean bill of health.

**Overall.** The security posture is better than most hobby projects.
- Passwords use PBKDF2 (260k iterations).
- Session tokens are stored hashed, in HttpOnly SameSite=Strict cookies.
- Login is rate-limited by socket address.
- First-run admin setup is loopback-only.
- The Telegram session and API credentials are encrypted at rest.
- Mutating requests get an Origin check, and the WebSocket checks Origin too.
- The grep found no `shell=True`, `os.system`, `pickle`, `yaml.load` or `eval` in the app code.
- SQL values are bound as parameters. Every f-string table or column name I traced comes from a hard-coded whitelist.

The issues below are in priority order.

## Bugs

### B1 — Admin accounts with a non-ASCII username can never log in (Medium, confirmed)
`web/routes_system.py` (`login_submit`) calls `hmac.compare_digest(username, str(stored_username))` on `str` values.
Python raises `TypeError` for non-ASCII `str` arguments. I confirmed this with `compare_digest('José','José')`.
`/api/auth/setup` accepts any username up to 80 characters, so someone who sets up as `José` gets a 500 error on every login
and is locked out. Because the exception is raised before `login_limiter.record()`, those failed requests are never counted.
*Fix:* compare `username.encode()` with `stored_username.encode()`, or validate the setup username as ASCII.

### B2 — Malformed JSON bodies cause 500 errors (Low)
`login_submit` and `setup_auth` call `data.get(...)` on whatever `request.json()` returns. A JSON list or a non-JSON body
raises `AttributeError` or a decode error, and the user sees a 500 instead of a 400.
*Fix:* check `isinstance(data, dict)` and catch decode errors.

### B3 — Every authenticated request can wait on the database lock (Low–Medium, by inspection)
`SessionStore.get()` holds its own lock while it does a SQLite lookup under `db.lock`. When the single shared DB connection is
busy (a long job commit or backfill), every `require_auth()` call queues behind it, which can freeze the UI.
*Fix:* do the DB lookup outside `self.lock`, or cache hits and only then take the lock.

### B4 — Error hiding (Low, maintainability)
There are 1,716 `except Exception` blocks, and 542 of them are directly followed by `pass`.
Failures such as session persistence and log writes vanish silently.

### B5 — Duplicate import (trivial)
In `engines/delivery_pipeline.py`, `send_split_local_video` is imported at module level and again inside the function.
There are also three f-strings with no placeholders (`main.py:156,159`, `torrent_engine.py:2035`, `library_health.py:51`).

### B6 — `exec()`-assembled module (Maintainability risk)
`media_manager/telegram_media_manager.py` `exec()`s six `mm_*.py` fragments into one namespace.
Linters report about 100 "undefined names" there, which are not real bugs. But name collisions and load-order mistakes cannot be
caught statically, and the six files cannot be imported or tested on their own.

### B7 — Undeclared dependency (Low)
`core/http_client.py` imports `requests` (guarded by `try`), but it is not in `requirements.txt`. It works only if another
package pulls it in. Only `telethon` is pinned exactly, and there is no lock file or hash pinning.

## Security findings

### S1 — Server-side request forgery in artwork fetching (Medium, authenticated)
`web/routes.py` `_proxy_entity_image` fetches `image_url` and `provider_image_url` from `routes_entities.py`. The only check is
that the URL starts with `http` or `https`. There is no block on loopback, private or link-local addresses, and `urllib` follows
redirects. A logged-in user (or anything that can make the admin's browser send the request) can make the server request
`http://127.0.0.1:…`, other LAN hosts or a cloud-metadata address. The response is returned only if it decodes as an image, which
limits data theft but not blind probing. The decoder also accepts `image/svg+xml`; please check that SVG artwork is never served
inline from the app origin, because the CSP allows `'unsafe-inline'` scripts.
*Fix:* resolve the host and reject private ranges (re-check after redirects), cap redirects, and either reject SVG or serve it with
`Content-Disposition: attachment` and `sandbox` CSP.

### S2 — The local root CA is unprotected and unconstrained (Medium)
`core/certs.py` writes `ca-key.pem` with no encryption and no explicit file mode, so it gets the OS default permissions. The CA
certificate has no `NameConstraints`, and `/ca.crt` is public by design so phones can trust it. Anyone who can read that key
(another local user, a backup, a sync tool) can mint a certificate for *any* domain that every device trusting the CA will accept.
*Fix:* restrict the key to the owner (0600 or an NTFS ACL), add a `NameConstraints` extension limited to the server's IP
addresses and `localhost`, and consider a shorter CA lifetime than 10 years.

### S3 — Player feed uses plain HTTP and URL-borne tokens (Medium for LAN, by design)
The player feed on `0.0.0.0:8444+` is unencrypted. Its token (`c`, `t`) is a bearer link that stays valid for 7 days by default.
On a shared or hostile LAN it can be sniffed and replayed to stream media. Tokens in query strings also end up in logs: Hypercorn
runs with `accesslog="-"`, and Uvicorn logs requests at info level.
*Fix:* bind the feed to loopback unless an external player on another device is configured, shorten the TTL (or bind the token to
the client), and strip `t=` from logs.

### S4 — Loopback-only checks trust the socket peer (Low–Medium)
First-run setup (`/api/auth/setup`) and the live test bridge both check `request.client.host` against loopback. If the app is
put behind a reverse proxy or tunnel on the same machine, every remote request appears to come from `127.0.0.1`. A remote
visitor could then claim the admin account on first run. The `TELEGRAM_ARCHIVE_ALLOW_REMOTE_SETUP` and
`TELEGRAM_ARCHIVE_TEST_BRIDGE_REMOTE` switches also weaken this. The setup page should say so explicitly.

### S5 — Rate limiter trusts `X-Forwarded-For` (Low)
`APIRateLimitMiddleware._client_ip()` takes the first `X-Forwarded-For` value without checking for a trusted proxy. Any client
can rotate the header to avoid the 30-per-minute limit on `/login`, `/api/auth/setup` and `/health`. The separate
`LoginRateLimiter` (8 attempts per 5 minutes) uses the real socket address, so password guessing is still limited. This only
defeats the outer limiter.

### S6 — Session handling (Low)
- Sessions last 30 days and slide forward on every request.
- There is no rotation on login and no binding to client details.
- The Origin guard lets requests with no `Origin` header through. That is acceptable for SameSite=Strict cookies, but there is no CSRF token behind it.
- The CSP allows `script-src 'unsafe-inline'` and `connect-src https:`, so it gives little protection if any XSS exists.
*Fix:* consider shorter idle expiry and nonce-based scripts.

### S7 — Backup restore takes an arbitrary server path (Low, admin only)
`/api/backup/restore` passes a client-supplied path to `stage_restore`. The restore itself is checksum-verified and unpacked with
`zipfile.extractall` (which neutralizes `..` member names), and it stages the result for the next start. Because the path is not
limited to the backup folder, an admin can point it at any readable zip.
Backup passwords are held in the process (`PENDING_BACKUP_PASSWORDS`).

### S8 — Launcher scripts and downloads (Info)
- The `.bat` runs PowerShell with `-ExecutionPolicy Bypass`. That only applies to that process and is normal for this kind of launcher.
- `setup_ai_prerequisites.ps1` downloads a llama.cpp release from the GitHub API and checks it against the published SHA-256 digest. It downloads a Hugging Face model pinned to a revision.
- `setup_dependency_check.py` runs `pip install` at start-up with `--prefer-binary`. Version ranges are open except Telethon, so builds are not reproducible.
- `main.py` deletes a fixed list of "obsolete" module files from the package directory at start-up. The list is hard-coded, but this is surprising behavior for an app.
- `_create_unverified_context()` is used only for the app's own `127.0.0.1` readiness probe and the loopback test-bridge self-call. That is acceptable.

## Suggested order of work
1. B1 (a real lock-out bug, one-line fix) and B2.
2. S1 (private-address check plus SVG handling).
3. S2 (key permissions and NameConstraints).
4. S3 and S4 (feed binding and TTL, and a note or check for reverse-proxy setups).
5. B3, S5, S6 and the broad `except … pass` cleanup.

## Addendum — findings from the Graphify knowledge graph
Graphify (AST only, no LLM) indexed 244 files into 6,739 nodes and 17,498 edges (91% extracted, 9% inferred).
- **God nodes** match the architecture doc: `Database` (109 edges), `PlaybackEngine` (84), `TransferGovernor` (72), `JobManager` (70).
- **Three circular imports**: `core/internal_media.py` ↔ `media_manager/telegram_media_manager.py`; the same pair via `engines/telegram_sender.py`; and `core/workspace_guard.py` → `operations/automation.py` → `engines/uploader.py` → back. These explain the many function-level imports and the `__import__(...)` hacks, and they are fragile on startup order.
- Graph: `Archive-X-graphify/graph.html` (aggregated into 242 communities because it exceeds 5,000 nodes), `GRAPH_REPORT.md`, `graph.json`.

---

## Second pass (graph-guided)
This pass followed the dependency graph to the places the first pass skipped: every HTTP and WebSocket route, every process-launch
site, the torrent and automation file deletion, the qBittorrent supervisor, the AI tool layer and the test bridge.

### New finding

**S9 — Command injection in the Windows folder-picker fallback (Medium, authenticated and loopback-only)**
`web/routes_telegram.py` (`automation_pick_folder`, about line 862) falls back to `powershell.exe -Command <script>` when
`tkinter` fails. It builds the script by embedding the client-supplied `title` (up to 120 characters) with `psquote()`, which only
doubles the ASCII `'`. PowerShell also treats `‘` `’` `‚` `‛` (U+2018, U+2019, U+201A, U+201B) as single-quote characters, so a
title such as `x’; Start-Process calc; ‘` ends the string and runs the rest as PowerShell code. It needs a logged-in session and a
request from the PC itself, and it only triggers when Tk is unavailable, so the practical risk is limited. Even so, it turns a
stolen or hijacked admin session into code execution on the host.
*Fix:* do not interpolate. Pass `title`, the start path and the flags as environment variables or arguments and read them with
`$env:…`, or use `-EncodedCommand` with a fixed script. Also escape all four quote characters if you keep the interpolation.

### Checked and found sound
- **Route authentication.** I parsed all 325 route handlers. 28 had no `require_auth` in their own body. I checked each group: they authenticate through a helper (`_hls_auth`, `_automation_control_current`, `_ensure_entity_metadata`, the Telegram login functions the compatibility aliases call) or are intentionally public (`/health`, `/login`, `/ca.crt`, static assets). The public routes serve only static assets, the CA certificate, the login page and `/health`, which returns just the version and whether Telethon is available. The test-bridge routes use their own scoped tokens plus a loopback check.
- **Process launching.** Every `subprocess` and `create_subprocess_exec` call passes an argument list, with no `shell=True`. The VLC and MPC arguments are built server-side from signed URLs.
- **qBittorrent.** The supervisor binds it to `127.0.0.1`, enables `LocalHostAuth`, and only controls torrents in the Archive X category.
- **Destructive file operations.** "Clear all" refuses the filesystem root, the home directory and the data directory. For custom download folders it deletes only files it recorded itself. Whole-directory clearing applies only to app-owned folders.
- **AI assistant.** The local model gets seven read-only tools with bounded arguments and a validated job-id pattern, and the model runs on loopback. Two small issues: `fn(**args)` turns unexpected model-supplied keys into a `TypeError` (a 500 rather than a clean error), and `LIKE` patterns don't escape `%` and `_`.
- **Test bridge.** It needs an app login, a loopback caller, and then a short-lived scoped token. Only allowlisted path prefixes are reachable.

### Updated priority list
1. B1 (non-ASCII username lock-out) and S9 (PowerShell injection).
2. S1 (artwork SSRF and SVG), S2 (CA key and name constraints), S3 (plain-HTTP player feed).
3. S4 (reverse-proxy trust for the loopback checks), B2, B3, S5, S6.
4. Structure: break the three circular imports, replace the `exec()`-assembled `media_manager`, and remove the blanket `except … pass` blocks.
