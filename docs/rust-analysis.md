# Rust migration plan: Archive-X (Python)

1 packages, 242 source files (100312 lines), 112 test files, 0 UI files, grouped into 22 units.

| Ready | Caution | Blocked | Keep in source language |
| ---: | ---: | ---: | ---: |
| 8 | 6 | 8 | 0 |

## Start here

Best first ports: safe to port (readiness) and worth porting (loops and string work, tests, size).

| # | Unit | Lines | Priority | Readiness | Tests | Bridge | Main risks |
| ---: | --- | ---: | ---: | ---: | ---: | --- | --- |
| 1 | `tools/brand/` | 44 | 82 | 93 | 0 | PyO3 | String offsets and slicing (1); Division and modulo (6) |
| 2 | `tools/bench/` | 131 | 81 | 77 | 0 | PyO3 | String offsets and slicing (4); Dynamic typing (5); Integer width and overflow (3) |
| 3 | `tools/sca/` | 169 | 80 | 83 | 0 | PyO3 | Hash iteration order (4); String offsets and slicing (1); Dynamic typing (1) |
| 4 | `tools/security/` | 106 | 78 | 92 | 0 | PyO3 | String offsets and slicing (1); Class inheritance (1); Exceptions (7) |
| 5 | `tools/release/` | 113 | 71 | 78 | 0 | PyO3 | Hash iteration order (4); String offsets and slicing (1); Regexes built at runtime (1) |
| 6 | `packaging/scripts/` | 56 | 65 | 100 | 0 | PyO3 | Exceptions (4); Number semantics (1) |

### `tools/brand/`

44 lines in 1 files, wave 0, priority 82, readiness 93, bridge **PyO3**.

- Leaf: imports no other unit.
- String offsets and slicing x1: `tools/brand/make_icons.py:35`
- Division and modulo x6: `tools/brand/make_icons.py:24`, `tools/brand/make_icons.py:24`, `tools/brand/make_icons.py:46`

### `tools/bench/`

131 lines in 1 files, wave 0, priority 81, readiness 77, bridge **PyO3**.

- Leaf: imports no other unit.
- String offsets and slicing x4: `tools/bench/stream_files.py:69`, `tools/bench/stream_files.py:80`, `tools/bench/stream_files.py:81`
- Dynamic typing x5: `tools/bench/stream_files.py:47`, `tools/bench/stream_files.py:49`, `tools/bench/stream_files.py:57`
- Integer width and overflow x3: `tools/bench/stream_files.py:113`, `tools/bench/stream_files.py:117`, `tools/bench/stream_files.py:122`
- Concurrency x2: `tools/bench/stream_files.py:54`, `tools/bench/stream_files.py:125`
- Exceptions x3: `tools/bench/stream_files.py:56`, `tools/bench/stream_files.py:58`, `tools/bench/stream_files.py:131`

### `tools/sca/`

169 lines in 2 files, wave 0, priority 80, readiness 83, bridge **PyO3**.

- Leaf: imports no other unit.
- Hash iteration order x4: `tools/sca/licenses.py:24`, `tools/sca/licenses.py:29`, `tools/sca/licenses.py:104`
- String offsets and slicing x1: `tools/sca/licenses.py:129`
- Dynamic typing x1: `tools/sca/licenses.py:114`
- Exceptions x1: `tools/sca/licenses.py:122`

### `tools/security/`

106 lines in 1 files, wave 0, priority 78, readiness 92, bridge **PyO3**.

- Leaf: imports no other unit.
- String offsets and slicing x1: `tools/security/dast.py:105`
- Class inheritance x1: `tools/security/dast.py:46`
- Exceptions x7: `tools/security/dast.py:41`, `tools/security/dast.py:53`, `tools/security/dast.py:61`
- Number semantics x2: `tools/security/dast.py:72`, `tools/security/dast.py:72`

### `tools/release/`

113 lines in 2 files, wave 0, priority 71, readiness 78, bridge **PyO3**.

- Leaf: imports no other unit.
- Hash iteration order x4: `tools/release/gen_notices.py:33`, `tools/release/gen_notices.py:53`, `tools/release/gen_notices.py:57`
- String offsets and slicing x1: `tools/release/write_version.py:18`
- Regexes built at runtime x1: `tools/release/gen_notices.py:57`
- Dynamic typing x3: `tools/release/gen_notices.py:51`, `tools/release/gen_notices.py:52`, `tools/release/gen_notices.py:69`
- Exceptions x3: `tools/release/gen_notices.py:45`, `tools/release/write_version.py:15`, `tools/release/write_version.py:28`

## Order by package

Packages come after the workspace packages they use. Inside a package, port by wave: a unit's imports are in earlier waves.

### 1. root (`.`, portable, 112 test files)

| Wave | Unit | Status | Lines | Readiness | Bridge | Main risks |
| ---: | --- | --- | ---: | ---: | --- | --- |
| 0 | `packaging/scripts/` | ready | 56 | 100 | PyO3 | Exceptions (4); Number semantics (1) |
| 0 | `tools/brand/` | ready | 44 | 93 | PyO3 | String offsets and slicing (1); Division and modulo (6) |
| 0 | `tools/security/` | ready | 106 | 92 | PyO3 | String offsets and slicing (1); Class inheritance (1) |
| 0 | `tools/sca/` | ready | 169 | 83 | PyO3 | Hash iteration order (4); String offsets and slicing (1) |
| 0 | `tools/release/` | ready | 113 | 78 | PyO3 | Hash iteration order (4); String offsets and slicing (1) |
| 0 | `tools/bench/` | ready | 131 | 77 | PyO3 | String offsets and slicing (4); Dynamic typing (5) |
| 0 | `telegram_archive/playback/controller/` | blocked | 260 | 56 | PyO3 | Hash iteration order (4); Dynamic code (eval, new Function, computed import) (2) |
| 0 | `telegram_archive/core/backup/` | caution | 439 | 42 | PyO3 | Hash iteration order (7); String offsets and slicing (4) |
| 0 | `telegram_archive/` | blocked | 1391 | 0 | PyO3 | Hash iteration order (4); String offsets and slicing (3) |
| 0 | `telegram_archive/ai/` | caution | 2681 | 0 | PyO3 | String offsets and slicing (75); Hash iteration order (15) |
| 0 | `telegram_archive/core/` | blocked | 17730 | 0 | PyO3 | Hash iteration order (189); String offsets and slicing (160) |
| 0 | `telegram_archive/engines/` | blocked | 22814 | 0 | PyO3 | Hash iteration order (245); String offsets and slicing (167) |
| 0 | `telegram_archive/jobs/` | blocked | 2759 | 0 | PyO3 | Hash iteration order (86); String offsets and slicing (24) |
| 0 | `telegram_archive/media_manager/` | blocked | 8760 | 0 | PyO3 | Hash iteration order (73); String offsets and slicing (62) |
| 0 | `telegram_archive/metadata/` | blocked | 8450 | 0 | PyO3 | Hash iteration order (147); String offsets and slicing (146) |
| 0 | `telegram_archive/operations/` | caution | 6672 | 0 | PyO3 | Hash iteration order (79); String offsets and slicing (28) |
| 0 | `telegram_archive/playback/` | caution | 7329 | 0 | PyO3 | Hash iteration order (81); String offsets and slicing (39) |
| 0 | `telegram_archive/telegram/` | caution | 1784 | 0 | PyO3 | Hash iteration order (7); String offsets and slicing (1) |
| 0 | `telegram_archive/web/` | blocked | 17901 | 0 | PyO3 | String offsets and slicing (199); Hash iteration order (198) |
| 1 | `packaging/backend/` | ready | 29 | 95 | PyO3 | String offsets and slicing (1); Dynamic typing (2) |
| 1 | `tools/` | ready | 87 | 83 | PyO3 | String offsets and slicing (2); Regexes built at runtime (1) |
| 1 | `tools/parity/` | caution | 607 | 26 | PyO3 | String offsets and slicing (16); Hash iteration order (9) |

## PyPI dependencies

| Package | Units | Rust | Fit | Note |
| --- | ---: | --- | --- | --- |
| `fastapi` | 3 | - | none | Web framework: keep the app in Python. |
| `starlette` | 2 | - | none | Web framework: keep the app in Python. |
| `telethon` | 6 | ? | unknown |  |
| `cv2` | 2 | ? | unknown |  |
| `faster-whisper` | 2 | ? | unknown |  |
| `argon2` | 1 | ? | unknown |  |
| `compare` | 1 | ? | unknown |  |
| `cryptg` | 1 | ? | unknown |  |
| `defusedxml` | 1 | ? | unknown |  |
| `ftfy` | 1 | ? | unknown |  |
| `libtorrent` | 1 | ? | unknown |  |
| `onnxruntime` | 1 | ? | unknown |  |
| `playwright` | 1 | ? | unknown |  |
| `psutil` | 1 | ? | unknown |  |
| `qrcode` | 1 | ? | unknown |  |
| `tokenizers` | 1 | ? | unknown |  |
| `uvicorn` | 1 | ? | unknown |  |
| `pil` | 9 | image | partial |  |
| `numpy` | 5 | ndarray | partial | Broadcasting and dtype rules differ; check numerics. |
| `cryptography` | 2 | ring, RustCrypto crates | partial |  |
| `av` | 1 | ffmpeg-next | partial | Binds the C libraries. For pure Rust, remade-ffmpeg covers a subset of codecs (and has an ffmpeg-compatible CLI): check its codec table. |
| `aiohttp` | 4 | reqwest | good |  |
| `requests` | 2 | reqwest | good |  |
| `urllib3` | 1 | reqwest | good |  |

## Blocked

- `telegram_archive/`: `fastapi` has no Rust equivalent.
- `telegram_archive/core/`: `fastapi` has no Rust equivalent.
- `telegram_archive/engines/`: Uses dynamic code (eval, new Function or computed imports).
- `telegram_archive/jobs/`: Uses dynamic code (eval, new Function or computed imports).
- `telegram_archive/media_manager/`: Uses dynamic code (eval, new Function or computed imports).
- `telegram_archive/metadata/`: Uses dynamic code (eval, new Function or computed imports).
- `telegram_archive/playback/controller/`: Uses dynamic code (eval, new Function or computed imports).
- `telegram_archive/web/`: `fastapi` has no Rust equivalent.

## Risk guide

- **String offsets and slicing** (High): JS indexes strings in UTF-16 code units, Rust in UTF-8 bytes. Offsets that cross the boundary must be converted, or non-ASCII text gets wrong ranges silently.
- **Regex features the `regex` crate lacks** (High): Lookaround, backreferences and the `y` flag are not in the `regex` crate. Use `fancy-regex` for those patterns, or rewrite them, and test them on non-ASCII text: `\w`, `\b` and case-insensitive matching differ between engines even where a pattern compiles. JS `/i` without `u` never matches a non-ASCII letter to an ASCII one, Rust `(?i)` does (`ſ` matches `s`, the Kelvin sign `K` matches `k`): write `(?i-u)` or compare with `eq_ignore_ascii_case`.
- **Regexes built at runtime** (High): Patterns come from data or config in JS syntax. Rust regex syntax differs, so user-visible behavior can change. Pin it with tests on real patterns.
- **Dynamic code (eval, new Function, computed import)** (High): eval, new Function and computed imports have no Rust equivalent. Keep this part in JS or replace it with an explicit registry.
- **Dynamic typing** (Medium): Values whose shape is checked at runtime need an explicit Rust enum or `serde_json::Value`.
- **Exceptions** (Low): Thrown errors become `Result`. Errors crossing the bridge must map back to the same JS error shape.
- **Async and promises** (Medium): Promises become futures. Bridge with napi-rs `async fn` or `wasm-bindgen-futures`, or keep the async shell in JS and port the sync core.
- **Generators** (Medium): Lazy generators become iterators, or return a collected Vec across the bridge.
- **Class inheritance** (Medium): Class hierarchies become traits plus composition; `instanceof` checks become enums.
- **Reflection and prototypes** (Medium): Proxy, Reflect, Symbol and prototype tricks need a redesign, not a translation.
- **Number semantics** (Low): JS numbers are f64; bitwise ops truncate to i32; parseInt is lenient. Pick Rust integer types deliberately. Printing differs: `${n}` gives `1e+21`, `1e-7` and `0` for -0 where Rust's `{}` does not, so use `js::number_to_string`. `Math.round` rounds halves up (-2.5 gives -2), Rust's `round` away from zero: use `js::math_round`. `toFixed` rounds exact halves up ((2.5).toFixed(0) is "3"), Rust's `{:.0}` to even.
- **Integer width and overflow** (Medium): Python ints never overflow; C, Go and Java ints wrap or are undefined on overflow. Pick i64, i128 or a bignum crate deliberately, and use wrapping_* where the source wraps.
- **Division and modulo** (Medium): Python `//` and `%` floor toward negative infinity; Rust `/` and `%` truncate: use `div_euclid`/`rem_euclid` or explicit floor math. C division by zero is undefined.
- **Hash iteration order** (High): Set and map iteration order comes from the source language's hash function. A port cannot reproduce it: sort before output, or make the order part of the contract.
- **Concurrency** (Medium): Threads, goroutines and locks need a design in Rust (channels, Send/Sync), not a line-by-line translation; keep concurrency in the caller where possible.
- **Runtime changes to code** (High): Code that rewrites classes or modules at runtime has no Rust equivalent: keep it in the source language and port the stable parts.

## Whole app

Shape: **server**.

| Entry point | Kind | Found from |
| --- | --- | --- |
| `packaging/backend/entry.py` | cli | `if __name__ == "__main__"` at packaging/backend/entry.py:33 |
| `packaging/scripts/make_latest_json.py` | cli | `if __name__ == "__main__"` at packaging/scripts/make_latest_json.py:64 |
| `telegram_archive/__main__.py` | cli | `if __name__ == "__main__"` at telegram_archive/__main__.py:5 |
| `telegram_archive/app.py` | server | `FastAPI(` at telegram_archive/app.py:583 |
| `telegram_archive/core/native_media_worker.py` | cli | `if __name__ == "__main__"` at telegram_archive/core/native_media_worker.py:78 |
| `telegram_archive/main.py` | cli | `if __name__ == "__main__"` at telegram_archive/main.py:173 |
| `telegram_archive/media_manager/mm_thumbnail_engine.py` | server | `HTTPServer(` at telegram_archive/media_manager/mm_thumbnail_engine.py:1284 |
| `telegram_archive/media_manager/telegram_media_manager.py` | cli | `if __name__ == "__main__"` at telegram_archive/media_manager/telegram_media_manager.py:1231 |
| `tools/brand/make_icons.py` | cli | `if __name__ == "__main__"` at tools/brand/make_icons.py:53 |
| `tools/parity/compare.py` | cli | `if __name__ == "__main__"` at tools/parity/compare.py:244 |
| `tools/parity/compare_writes.py` | cli | `if __name__ == "__main__"` at tools/parity/compare_writes.py:126 |
| `tools/parity/dump_schema.py` | cli | `if __name__ == "__main__"` at tools/parity/dump_schema.py:48 |
| `tools/parity/gen_origin_vectors.py` | cli | `if __name__ == "__main__"` at tools/parity/gen_origin_vectors.py:65 |
| `tools/parity/seed_fixture.py` | cli | `if __name__ == "__main__"` at tools/parity/seed_fixture.py:153 |
| `tools/release/gen_notices.py` | cli | `if __name__ == "__main__"` at tools/release/gen_notices.py:105 |
| `tools/release/write_version.py` | cli | `if __name__ == "__main__"` at tools/release/write_version.py:26 |
| `tools/sca/check_action_pins.py` | cli | `if __name__ == "__main__"` at tools/sca/check_action_pins.py:41 |
| `tools/sca/licenses.py` | cli | `if __name__ == "__main__"` at tools/sca/licenses.py:162 |
| `tools/security/dast.py` | cli | `if __name__ == "__main__"` at tools/security/dast.py:121 |

### Every dependency

| Dependency | Declared | Used by | Rust | Fit | Note |
| --- | --- | --- | --- | --- | --- |
| `fastapi` | yes | 3 | - | none | Web framework: keep the app in Python. |
| `starlette` | no | 2 | - | none | Web framework: keep the app in Python. |
| `telethon` | yes | 6 | ? | unknown |  |
| `cv2` | no | 2 | ? | unknown |  |
| `faster-whisper` | yes | 2 | ? | unknown |  |
| `argon2` | no | 1 | ? | unknown |  |
| `compare` | no | 1 | ? | unknown |  |
| `cryptg` | yes | 1 | ? | unknown |  |
| `defusedxml` | no | 1 | ? | unknown |  |
| `ftfy` | no | 1 | ? | unknown |  |
| `libtorrent` | no | 1 | ? | unknown |  |
| `onnxruntime` | yes | 1 | ? | unknown |  |
| `playwright` | no | 1 | ? | unknown |  |
| `psutil` | no | 1 | ? | unknown |  |
| `qrcode` | yes | 1 | ? | unknown |  |
| `tokenizers` | yes | 1 | ? | unknown |  |
| `uvicorn` | yes | 1 | ? | unknown |  |
| `argon2-cffi` | yes | 0 | ? | unknown |  |
| `opencv-python-headless` | yes | 0 | ? | unknown |  |
| `python-socks` | yes | 0 | ? | unknown |  |
| `pil` | no | 9 | image | partial |  |
| `numpy` | no | 5 | ndarray | partial | Broadcasting and dtype rules differ; check numerics. |
| `cryptography` | yes | 2 | ring, RustCrypto crates | partial |  |
| `av` | yes | 1 | ffmpeg-next | partial | Binds the C libraries. For pure Rust, remade-ffmpeg covers a subset of codecs (and has an ffmpeg-compatible CLI): check its codec table. |
| `pillow` | yes | 0 | image | partial |  |
| `aiohttp` | yes | 4 | reqwest | good |  |
| `requests` | no | 2 | reqwest | good |  |
| `urllib3` | no | 1 | reqwest | good |  |

### Pieces, in port order

- wave 0: `packaging/scripts/` (56 lines)
- wave 0: `telegram_archive/`, `telegram_archive/ai/`, `telegram_archive/core/`, `telegram_archive/core/backup/`, `telegram_archive/engines/`, `telegram_archive/jobs/`, `telegram_archive/media_manager/`, `telegram_archive/metadata/`, `telegram_archive/operations/`, `telegram_archive/playback/`, `telegram_archive/playback/controller/`, `telegram_archive/telegram/`, `telegram_archive/web/`, tangled: one piece (98970 lines)
- wave 0: `tools/bench/` (131 lines)
- wave 0: `tools/brand/` (44 lines)
- wave 0: `tools/release/` (113 lines)
- wave 0: `tools/sca/` (169 lines)
- wave 0: `tools/security/` (106 lines)
- wave 1: `packaging/backend/` (29 lines)
- wave 1: `tools/` (87 lines)
- wave 1: `tools/parity/` (607 lines)

### Decisions for the user

**D1** (shape): The app is a server, entered through packaging/backend/entry.py, packaging/scripts/make_latest_json.py, telegram_archive/__main__.py, telegram_archive/app.py, telegram_archive/core/native_media_worker.py, telegram_archive/main.py, telegram_archive/media_manager/mm_thumbnail_engine.py, telegram_archive/media_manager/telegram_media_manager.py, tools/brand/make_icons.py, tools/parity/compare.py, tools/parity/compare_writes.py, tools/parity/dump_schema.py, tools/parity/gen_origin_vectors.py, tools/parity/seed_fixture.py, tools/release/gen_notices.py, tools/release/write_version.py, tools/sca/check_action_pins.py, tools/sca/licenses.py, tools/security/dast.py. Port every entry point, in that shape?

> Yes: an axum front door (`app front-door`) takes the server over first

**D2** (blocked telegram_archive/): telegram_archive/ is blocked: `fastapi` has no Rust equivalent. Rework that part in Rust, or keep the unit outside?

> Research the blocker first; then port it with the piece that uses it

**D3** (blocked telegram_archive/core/): telegram_archive/core/ is blocked: `fastapi` has no Rust equivalent. Rework that part in Rust, or keep the unit outside?

> Research the blocker first; then port it with the piece that uses it

**D4** (blocked telegram_archive/engines/): telegram_archive/engines/ is blocked: Uses dynamic code (eval, new Function or computed imports). Rework that part in Rust, or keep the unit outside?

> Research the blocker first; then port it with the piece that uses it

**D5** (blocked telegram_archive/jobs/): telegram_archive/jobs/ is blocked: Uses dynamic code (eval, new Function or computed imports). Rework that part in Rust, or keep the unit outside?

> Research the blocker first; then port it with the piece that uses it

**D6** (blocked telegram_archive/media_manager/): telegram_archive/media_manager/ is blocked: Uses dynamic code (eval, new Function or computed imports). Rework that part in Rust, or keep the unit outside?

> Research the blocker first; then port it with the piece that uses it

**D7** (blocked telegram_archive/metadata/): telegram_archive/metadata/ is blocked: Uses dynamic code (eval, new Function or computed imports). Rework that part in Rust, or keep the unit outside?

> Research the blocker first; then port it with the piece that uses it

**D8** (blocked telegram_archive/playback/controller/): telegram_archive/playback/controller/ is blocked: Uses dynamic code (eval, new Function or computed imports). Rework that part in Rust, or keep the unit outside?

> Research the blocker first; then port it with the piece that uses it

**D9** (blocked telegram_archive/web/): telegram_archive/web/ is blocked: `fastapi` has no Rust equivalent. Rework that part in Rust, or keep the unit outside?

> Research the blocker first; then port it with the piece that uses it

**D10** (dependency fastapi): No crate does what fastapi does (used by 3 units; Web framework: keep the app in Python). Reimplement what the app uses, or drop the feature?

> Reimplement only the part the app uses, proved by its recorded cases

**D11** (dependency starlette): No crate does what starlette does (used by 2 units; Web framework: keep the app in Python). Reimplement what the app uses, or drop the feature?

> Reimplement only the part the app uses, proved by its recorded cases

**D12** (dependency telethon): Which crate replaces telethon (used by 6 units)?

> Research it against primary sources into rust/research/telethon.md before porting what uses it

**D13** (dependency cv2): Which crate replaces cv2 (used by 2 units)?

> Research it against primary sources into rust/research/cv2.md before porting what uses it

**D14** (dependency faster-whisper): Which crate replaces faster-whisper (used by 2 units)?

> Research it against primary sources into rust/research/faster-whisper.md before porting what uses it

**D15** (dependency argon2): Which crate replaces argon2 (used by 1 unit)?

> Research it against primary sources into rust/research/argon2.md before porting what uses it

**D16** (dependency compare): Which crate replaces compare (used by 1 unit)?

> Research it against primary sources into rust/research/compare.md before porting what uses it

**D17** (dependency cryptg): Which crate replaces cryptg (used by 1 unit)?

> Research it against primary sources into rust/research/cryptg.md before porting what uses it

**D18** (dependency defusedxml): Which crate replaces defusedxml (used by 1 unit)?

> Research it against primary sources into rust/research/defusedxml.md before porting what uses it

**D19** (dependency ftfy): Which crate replaces ftfy (used by 1 unit)?

> Research it against primary sources into rust/research/ftfy.md before porting what uses it

**D20** (dependency libtorrent): Which crate replaces libtorrent (used by 1 unit)?

> Research it against primary sources into rust/research/libtorrent.md before porting what uses it

**D21** (dependency onnxruntime): Which crate replaces onnxruntime (used by 1 unit)?

> Research it against primary sources into rust/research/onnxruntime.md before porting what uses it

**D22** (dependency playwright): Which crate replaces playwright (used by 1 unit)?

> Research it against primary sources into rust/research/playwright.md before porting what uses it

**D23** (dependency psutil): Which crate replaces psutil (used by 1 unit)?

> Research it against primary sources into rust/research/psutil.md before porting what uses it

**D24** (dependency qrcode): Which crate replaces qrcode (used by 1 unit)?

> Research it against primary sources into rust/research/qrcode.md before porting what uses it

**D25** (dependency tokenizers): Which crate replaces tokenizers (used by 1 unit)?

> Research it against primary sources into rust/research/tokenizers.md before porting what uses it

**D26** (dependency uvicorn): Which crate replaces uvicorn (used by 1 unit)?

> Research it against primary sources into rust/research/uvicorn.md before porting what uses it

**D27** (dependency argon2-cffi): Which crate replaces argon2-cffi (declared, no unit imports it)?

> Research it against primary sources into rust/research/argon2-cffi.md before porting what uses it

**D28** (dependency opencv-python-headless): Which crate replaces opencv-python-headless (declared, no unit imports it)?

> Research it against primary sources into rust/research/opencv-python-headless.md before porting what uses it

**D29** (dependency python-socks): Which crate replaces python-socks (declared, no unit imports it)?

> Research it against primary sources into rust/research/python-socks.md before porting what uses it

**D30** (dependency pil): pil maps to image only in part (used by 9 units). Use it?

> Use image, and record cases for the behavior it does not cover

**D31** (dependency numpy): numpy maps to ndarray only in part (used by 5 units; Broadcasting and dtype rules differ; check numerics). Use it?

> Use ndarray, and record cases for the behavior it does not cover

**D32** (dependency cryptography): cryptography maps to ring, RustCrypto crates only in part (used by 2 units). Use it?

> Use ring, RustCrypto crates, and record cases for the behavior it does not cover

**D33** (dependency av): av maps to ffmpeg-next only in part (used by 1 unit; Binds the C libraries. For pure Rust, remade-ffmpeg covers a subset of codecs (and has an ffmpeg-compatible CLI): check its codec table). Use it?

> Use ffmpeg-next, and record cases for the behavior it does not cover

**D34** (dependency pillow): pillow maps to image only in part (declared, no unit imports it). Use it?

> Use image, and record cases for the behavior it does not cover

**D35** (tangled units): telegram_archive/, telegram_archive/ai/, telegram_archive/core/, telegram_archive/core/backup/, telegram_archive/engines/, telegram_archive/jobs/, telegram_archive/media_manager/, telegram_archive/metadata/, telegram_archive/operations/, telegram_archive/playback/, telegram_archive/playback/controller/, telegram_archive/telegram/, telegram_archive/web/ import each other (98970 lines). Port them as one piece?

> Yes: one piece in wave 0, no glue between its parts

