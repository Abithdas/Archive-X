# Software composition analysis (SCA)

Date: 2026-11-14. Scope: everything Archive X is built from, and what is shipped in the installer.
Repeat it any time: the **Dependency scan (SCA)** workflow (`.github/workflows/sca.yml`) runs weekly, on every change to a dependency list,
and on demand. Dependabot (`.github/dependabot.yml`) proposes the updates.

## Result in one table

| Ecosystem | Packages | Known vulnerabilities before | After | Licenses |
|---|---|---|---|---|
| Python backend (`requirements.txt`, resolved fresh as the installer does) | 65 | **49 reported** (46 distinct advisories) in Pillow 12.3.0 (42) and cryptography 46.0.7 (4 advisories, 7 entries) | **0** | 63 permissive, 2 weak copyleft (MPL-2.0: `certifi`, `tqdm`), none denied |
| Frontend (npm) | 326 (55 shipped) | 3, all in the test runner (`vitest`, `tinypool`, `@vitest/mocker`); **0 in shipped packages** | **0** | 54 shipped packages: all permissive |
| Rust front door (`rust/`) | 135 | **1** (RUSTSEC-2026-0459, SQLite in `libsqlite3-sys` 0.30.1) + 1 unmaintained crate (`rustls-pemfile`) | **0, no warnings** | 133: all permissive |
| Rust desktop app (`desktop/`) | 502 | 0 | 0 | 494 permissive, 5 MPL-2.0 (Tauri's HTML/CSS parser crates), none denied |
| GitHub Actions (`.github/workflows`) | 6 actions, 23 uses (7 and 34 with the new scan workflow) | all referenced by movable tags (`@v4`, `@stable`) | **all pinned to commits** | n/a |
| Downloaded binaries | LAV Filters 0.83, ffmpeg | LAV: no checksum | LAV: SHA-256 and source commit pinned | see "Not covered" |

## Method and tools

| Check | Tool | Data source |
|---|---|---|
| Python vulnerabilities | `pip-audit` 2.10.1, `-r requirements.txt` (resolves the newest allowed versions, as the installer build does) | PyPI advisory data (PYSEC / GHSA) |
| npm vulnerabilities | `npm audit` (npm 10.9.9), all dependencies and `--omit=dev` | GitHub Advisory Database |
| Rust vulnerabilities | `cargo audit` 0.23.0 on both `Cargo.lock` files | RustSec advisory database (commit 4ef2df8) |
| Licenses | `tools/sca/licenses.py` (Rust: `cargo metadata`; npm: `license-checker-rseidelsohn`; Python: PyPI metadata) | package metadata |
| Whether a flagged crate is in the Windows build | `cargo tree --target x86_64-pc-windows-msvc -i <crate>` | the lockfile |
| Pins | `tools/sca/check_action_pins.py` | the workflow files |
| SBOM | `pip-audit -f cyclonedx-json`, `npm sbom` (CycloneDX) | produced by the CI job as downloadable artifacts |

## Findings and what was done

### 1. Pillow 12.3.0: 42 advisories (fixed in 13.0.0). Fixed.
Mostly memory-safety bugs in image decoders (heap overflows and out-of-bounds reads in TIFF, EPS, JPEG, DDS, PNG, GIF and PDF
handling, plus denial of service). **This is the one that matters here:** the app calls `Image.open` in 54 places and the images come from
Telegram channels, that is from other people. The old range `Pillow>=10,<13` excluded the only fixed version, so nothing could have fixed
it by updating.
Change: `Pillow>=13,<14`. Checked: the app's own image tests (derivatives, photo cache, thumbnails), QR code creation (`qrcode[pil]`),
JPEG/WEBP/PNG round trips, and the code for removed Pillow APIs (none found).

### 2. cryptography 46.0.7: 4 advisories (7 entries; fixed in 48.0.1, 49.0.0, 50.0.0). Fixed.
Certificate-chain validation issues, PKCS#7 decrypt reporting, and a statically linked OpenSSL in the wheel. The app creates its own
certificates and does not validate foreign chains, so exposure is low; the bundled OpenSSL does serve the HTTP/3 listener. The old range
`cryptography>=42,<47` again excluded every fixed version, and `pyopenssl` 26.2.0 (pulled in by HTTP/3) capped it below 49.
Change: `cryptography>=50,<51`; pip then picks `pyopenssl` 26.4.0. Checked: `pip check` is clean, certificate creation
(`core/certs.py`: CA and leaf, loaded by Python's `ssl`), and the Python tests.

### 3. SQLite in the Rust front door (RUSTSEC-2026-0459, CVE-2026-119375). Fixed.
`rusqlite` 0.32 bundled SQLite through `libsqlite3-sys` 0.30.1. The front door only reads the local database, so exposure was low.
Change: `rusqlite` 0.42 (`libsqlite3-sys` 0.40). One API change (`column_names()` is now an iterator); all 58 Rust tests pass.
Also replaced the unmaintained `rustls-pemfile` by `rustls-pki-types`' own PEM reading (the successor its maintainers name).

### 4. Test runner chain (`vitest` 3 to 5.1.0). Fixed.
`tinypool` (critical: prototype pollution leading to code execution) and `@vitest/mocker` (path traversal) affect whoever runs the tests with
untrusted input; none of it is in the app. All 1,179 frontend tests pass on the new version.

### 5. Warnings that do not apply to the Windows installer. Documented.
`cargo audit` on `desktop/` reports unmaintained or unsound crates (`glib`, `zvariant`, `zbus_names`, `proc-macro-error`). They come from
Tauri's Linux (GTK and D-Bus) stack; `cargo tree --target x86_64-pc-windows-msvc` shows none of them is compiled into the Windows build.

### 6. Build pipeline. Hardened.
- Third-party actions were referenced by tags that their owners can move; a compromised action would have run with our secrets
  (the update-signing key). All 34 uses are now pinned to the commit the tag pointed to, the tag kept as a comment; a test fails if one
  is added unpinned.
- `dtolnay/rust-toolchain` takes its Rust version from its ref name, so pinned uses state `toolchain: stable` explicitly.
- LAV Filters 0.83: the download is checked against a pinned SHA-256 (the same in every CI run), and the headers it is built against against a
  pinned commit; a different file is refused.

## Not covered (be aware)

These cannot be seen by the tools above. They are the real residual risk.

1. **Native libraries inside Python wheels.** `av` and `opencv-python-headless` bundle their own FFmpeg; `onnxruntime`, `ctranslate2` and
   others bundle native code. `pip-audit` sees the Python package version, not the C libraries inside. Keep these packages current.
2. **LAV Filters 0.83** carries its own FFmpeg libraries (`avcodec-lav-63.dll` and others). Nothing here scans them; follow LAV releases and
   update the pinned version and checksum together (`packaging/scripts/fetch-lav.sh`).
3. **ffmpeg.exe (BtbN)** is fetched from the rolling `latest` release and checked against the checksum published beside it. That catches a damaged
   download, not a compromised release. For a release build, pin `FFMPEG_URL` and `FFMPEG_SHA256` as repository variables.
4. **Loose Python version ranges.** The installer build installs the newest versions inside the ranges, so what ships can differ from what was
   scanned on another day. The weekly scan narrows that; a hashed lock file (`pip-compile --generate-hashes`) would remove it
   (see `BACKEND-IMPROVEMENTS.md`, item D3).
5. **The embedded Python, PyInstaller's bootloader, WebView2.** The Python version in the frozen backend comes from the CI runner's
   setup; WebView2 is updated by Microsoft. Neither is scanned here.
6. **Models and data.** The speech and nudity-detection models are downloaded at run time from their hosts; they are data, not code, and are not
   covered.
7. **Database coverage.** Advisory databases lag and miss things; "0 known" is not "none".

## Licenses worth knowing about (not legal advice)

- **Permissive** (MIT, Apache-2.0, BSD, ISC, Zlib, Unlicense, 0BSD, CC0, PSF): nearly everything.
- **Weak copyleft, file-level (MPL-2.0):** `certifi`, `tqdm`, and Tauri's `cssparser`, `selectors`, `dtoa-short`, `option-ext`. Fine to use
  unmodified; changes to those files would have to be shared.
- **Not in the dependency metadata, but shipped:** LAV Filters (GPL-2.0-or-later), the BtbN ffmpeg build (GPL), FFmpeg inside the `av` and
  OpenCV wheels (LGPL). Fine for personal use. Before making the repository or installers public, the installer needs the licence texts and a
  source offer for these (see `docs/native-player.md`, section 5).
- **`UNLICENSED`** appears once: the project itself (`archive-x-frontend`, private). Choose a licence for the project before publishing it.

## How to repeat it

```bash
python -m pip install pip-audit && pip-audit -r requirements.txt
cd frontend && npm ci && npm audit --omit=dev && npm audit
cargo install cargo-audit --locked && (cd rust && cargo audit) && (cd desktop && cargo audit)
python tools/sca/licenses.py rust rust        # also: rust desktop, npm frontend, python pip-audit.json
python tools/sca/check_action_pins.py
```

## Open alerts reviewed on 2026-11-19 (not changed in code)
- **Dependabot #7, `glib` 0.18.5 (RUSTSEC-2024-0429, fixed in 0.20.0).** `glib` is pulled in only by Tauri's Linux GTK 3 stack (`muda`, `gtk`, `atk`). Archive X ships for Windows only: `cargo tree -i glib@0.18.5 --target x86_64-pc-windows-msvc` prints nothing. Tauri 2 pins `gtk` 0.18, which needs `glib` 0.18, so there is no patched version to move to. Dismiss as "vulnerable code is not used", and re-check when Tauri moves to a newer GTK binding.
- **CodeQL, clear-text logging, `telegram_archive/main.py`.** When the app is started by hand (no launcher secret in the environment) it prints the one-time address to open the desktop session, secret included, to that console. The launcher sets the secret itself and never prints it.
- **CodeQL, user-controlled path, `routes_local.py`, `routes_telegram.py`, `routes_jobs.py`.** The local-folder picker and the channel and file routes take a path on purpose: this is a single-user app on the person's own PC, behind its login, and choosing a folder is the feature.
- **CodeQL, error details, eight routes.** Diagnostics and status routes (behind the login) return stored status and error text, such as a model's last error. They return no stack traces.
