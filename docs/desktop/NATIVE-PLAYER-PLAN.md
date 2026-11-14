# Native player pane: MPC-HC's decoding parts inside Archive X

Goal: play every video MPC-HC can play, with Windows hardware decoding and no lag, **inside the Archive X window**, without copying
MPC-HC's code. MPC-HC itself is a shell around DirectShow filters; the decoding is done by the **LAV Filters** (LAV Splitter, LAV Video
Decoder, LAV Audio Decoder, built on FFmpeg) and a video renderer. Those are separate components any Windows program can load.

Status: **plan only, nothing built.** Nothing here can be tested in the Linux sandbox; every phase ends with a check on the Windows PC.
Items marked *(verify)* are from memory and must be checked against the component's source before coding.

## 1. How it fits together

```
React page (WebView2)  ──commands──▶  Tauri shell (Rust)  ──C API──▶  axplayer.dll (C++, new)
   controls, captions,                  player thread                    DirectShow graph:
   seek preview, markers                (COM, message loop)              stream URL → LAV Splitter Source
        ▲                                                                 ├─ LAV Video Decoder (D3D11/DXVA2) → EVR → child window
        └──────── time / state / errors (events) ────────────────────────┘└─ LAV Audio Decoder → Windows audio renderer
```

- **Source**: the same tokenised stream address the "Open in MPC-HC" button already hands to MPC-HC (`playback/vlc_launcher.py`).
  MPC-HC's built-in filters *are* LAV, so MPC-HC playing that address today is good evidence LAV will too.
- **Decoder settings**: hardware decoding is **off by default** in LAV; it must be switched on through `ILAVVideoSettings`
  (`SetRuntimeConfig(TRUE)` so nothing is read from or written to the registry, then `SetHWAccel(...)`) *(verify names)*.
- **Renderer**: start with Windows' own **EVR** (Enhanced Video Renderer; no extra download, works with DXVA2). Later option:
  **MPC Video Renderer** (D3D11, HDR), a separate GPL-3 filter.
- **Why a small C++ DLL** instead of pure Rust: LAV's settings interfaces are C++ COM headers with dozens of methods. Including the
  real headers in C++ is safe; re-declaring their layout by hand in Rust breaks silently if one method is out of order. The DLL exposes
  a flat C API (about 15 functions); Rust only does FFI.

## 2. The one hard question: drawing over the video

A native video window sits on top of the web page, so the page's controls cannot overlay it. Two ways out, decided by the Phase 1 spike:

- **A. Video underneath a transparent web page** (preferred). The video window is placed *behind* the WebView2 window; the WebView2
  background is made transparent and the page leaves a transparent hole where the video is. Controls, live captions, seek preview and
  markers then draw over the video exactly as today. Tauri mpv plugins use this arrangement on Windows; whether it holds with EVR and
  our window setup is unproven.
- **B. Controls around the video.** The video window is on top; the control bar sits below it and captions in a strip under the video.
  Always works; loses the over-the-video look, and the auto-hiding controls.

## 3. Phases

Each phase is a separate installer build tried on the PC. Stop and reassess if a phase's check fails.

### Phase 0. Prove the parts without writing code (an hour, on the PC)
1. Install LAV Filters (the installer from the LAV Filters GitHub releases) and GraphStudioNext.
2. In Archive X, right-click "Open in MPC-HC" flow once and copy the stream address (or take it from the log).
3. GraphStudioNext → Render URL with that address. Check: LAV Splitter Source, LAV Video Decoder and LAV Audio Decoder appear, video
   plays, seeking works. In LAV Video's properties turn on D3D11 or DXVA2 (native) and confirm "Active decoder" shows it.
4. Try the three videos the in-app player fails on.
- **Pass**: all play and seek. **Fail**: note which and why (that is what MPC-HC adds beyond LAV; plan changes).

### Phase 1. Spike: a video window inside the app (S)
1. New folder `desktop/axplayer/` (CMake, MSVC): `axplayer.dll` with `ax_open(hwnd_parent, url)`, `ax_play`, `ax_pause`, `ax_close`.
   It builds the graph with the *installed* LAV (by CLSID *(verify)*: LAV Splitter Source `{B98D13E7-55DB-4385-A33D-09FD1BA26338}`,
   LAV Video `{EE30215D-164F-4A92-A4EB-9D4C13390F9F}`, LAV Audio `{E8E73B6B-4CB3-44A4-BE99-4F7BCB96E491}`), EVR and the default audio
   renderer, and gives EVR a child window of the Tauri window (`IMFVideoDisplayControl::SetVideoWindow` / `SetVideoPosition`).
2. Rust: a dedicated player thread (COM initialised, own message loop) that owns the graph; a temporary debug menu item "Play test URL".
3. Try arrangement A: make the WebView2 background transparent, put the video window behind it, load a test page with a transparent
   rectangle and a button drawn over it.
- **Check on the PC**: video plays inside the window; does the button show over the video (A works) or not (fall back to B)? Does
  resizing, maximising and moving the window keep the video in place?

### Phase 2. A complete player engine (M)
C API and Rust wrapper for everything the web player does:

| Feature | DirectShow piece |
|---|---|
| Play, pause, stop | `IMediaControl` |
| Position, duration, seek, speed | `IMediaSeeking` (100 ns units; `SetRate`) |
| End of video, errors | `IMediaEventEx` (`EC_COMPLETE`, `EC_ERRORABORT`) posted to the player thread |
| Volume, mute | `IBasicAudio` (hundredths of a decibel, not linear) |
| Audio track choice | `IAMStreamSelect` on LAV Splitter |
| Video size, aspect | `IMFVideoDisplayControl::GetNativeVideoSize`, `SetAspectRatioMode` |
| Hardware decoding on, with software fallback | `ILAVVideoSettings` *(verify)* |

Also: a position timer (4 per second) that reports time to the page; clean teardown on close, on a new video and on app exit;
buffering state (`EC_BUFFERING_DATA`); a log line saying which decoder is active.
- **Check**: a Rust integration test on Windows CI that plays a generated 5 s clip from a local HTTP server and asserts position
  advances, seek lands, and the end event fires (needs LAV on the runner: Phase 3 makes that possible).

### Phase 3. Ship LAV with the app, no system install (S)
1. `desktop/scripts/fetch-lav.sh`: download the LAV x64 zip from its GitHub release, **pinned by SHA-256** like ffmpeg, into
   `desktop/src-tauri/resources/lav/`.
2. Load the filters **without registering them**: load `LAVSplitter.ax`, `LAVVideo.ax`, `LAVAudio.ax` from that folder and create the
   filters through each file's `DllGetClassObject` (this is how MPC-HC uses its own bundled copy). The user's system and other
   players are untouched.
3. Bundle the folder and `axplayer.dll` in `tauri.conf.json`; build `axplayer.dll` in `desktop.yml` (the runner has MSVC and CMake).
- **Check**: on a PC with no LAV installed, the app plays the test videos.

### Phase 4. Connect the web page (M)
1. The web UI has **no access to the shell today, on purpose**. Add one narrow capability for the app's own loopback address that
   allows only the player commands (`player_open`, `player_command`, `player_rect`) and the player events. Nothing else is exposed.
2. `frontend/src/features/watch/nativePlayer.ts`: the same interface the page uses for `<video>` (play, pause, seek, time, duration,
   volume, rate, events), backed by those commands. `Player.tsx` picks it when running in the desktop app and the engine is present;
   the browser/phone version keeps `<video>`.
3. Keep the video window aligned with the page: send the player element's rectangle on resize, scroll, zoom and fullscreen
   (in device pixels; the page zoom must be divided out, the same trap as the seek-preview bug).
4. Fullscreen, keyboard shortcuts, double-click, and focus returning to the page after clicking the video.
- **Check**: every control of the current player works on a native-played video; switching videos and closing the page never leaves
  a video window or sound behind.

### Phase 5. The features drawn on the video (M)
- With arrangement A these keep working unchanged: live captions (the server makes them from the stream's audio), seek preview
  (server-made frames), markers, skip animation, resume and history (fed by the reported time).
- With arrangement B: move the control bar under the video and captions into a strip; remove auto-hide.
- Embedded subtitle tracks: keep the current server-extracted subtitles shown by the page (no DirectShow subtitle filter needed).
- Alternate audio tracks: switch inside the graph (`IAMStreamSelect`) instead of the server-built audio file.

### Phase 6. Fallback rules and polish (S)
- Default: native engine in the desktop app; automatic fall back to the web player if the graph cannot be built or fails, with one
  log line saying why. A setting to choose the engine.
- Decoder order: D3D11 → DXVA2 → software, chosen per video; HDR and 10-bit left to the later renderer option.
- Remove the "Open in MPC-HC" suggestion from error screens that the native engine now covers (keep the button).

## 4. Risks

| Risk | What happens then |
|---|---|
| Arrangement A does not work with EVR + WebView2 | Use B, or try the D3D11 renderer / windowless presentation in a second spike |
| The stream address needs something only MPC-HC does (redirects, headers) | Found in Phase 0; fix on the server side |
| COM threading mistakes (hangs on close) | Single owner thread for the graph; every call goes through it |
| LAV update breaks the settings interface | Pinned version and hash; interface checked at build time by including LAV's own headers |
| WebView2 and the video window fight over keyboard focus | Video window never takes focus; clicks are forwarded |
| GPU driver differences | Software decoding fallback; log the active decoder |

## 5. Licences (not legal advice)

LAV Filters are GPL (v2 or later); MPC Video Renderer is GPL-3. Loading them inside the app and shipping them in the installer means
the installer must include the licence texts and offer the corresponding source, and it is widely read as requiring the app that
loads them to be under a GPL-compatible licence if the app is distributed. For personal use this changes nothing; decide before
making the repository or installers public.

## 6. Size, and a cheaper route to the same result

Phases 1 to 6 are roughly as much work as everything done on the Rust front door so far, and all of it can only be verified on the
PC. **libmpv** (the mpv player as a library) gives the same outcome with less custom code: one DLL, it also uses FFmpeg and Windows
hardware decoding, it is designed to be embedded in another program's window, and Tauri plugins for it exist. Its licence is LGPL or
GPL depending on the build. If the aim is "plays everything, smoothly, inside the app" rather than "the MPC-HC parts specifically",
run Phase 0 and the Phase 1 spike for both and pick the one that passes arrangement A.

## 7. First three steps

1. Phase 0 on the PC (no code): confirm LAV alone plays the stream address and the three problem videos with hardware decoding.
2. Phase 1 spike build: video window inside the app, and the answer to A or B.
3. Decide: continue with LAV + EVR, or switch the engine to libmpv, based on the spike.
