# In-app updates

The desktop app looks for a newer release by itself, asks, and installs it. No downloading or clicking through an installer.

## What the person sees

20 seconds after start (then every 6 hours while the app is open) the app reads
`https://github.com/Abithdas/Archive-X/releases/latest/download/latest.json`. When its version is newer:

1. A native dialog: "Archive X 30.1.0 is ready (you have 30.0.0). What's new … Update now? [Update now] [Later]".
2. **Update now**: the download progress shows in the window title, the backend is stopped cleanly (jobs are checkpointed), the
   installer runs in passive mode (a progress bar, no questions) and the app restarts. Library, settings and the Telegram login live
   outside the install folder and are kept.
3. **Later**: asked again for the next version, or after the next start.

A failed check (no network, no release yet, repository still private) is written to the log and ignored.
`ARCHIVE_X_NO_UPDATE_CHECK=1` turns the check off.

## Publishing an update

1. GitHub: Actions → "Desktop app (Windows)" → Run workflow → **release_version** = the new number, higher than the last
   (for example `30.1.0`). Or push a tag `desktop-v30.1.0`.
2. The workflow sets that version in `tauri.conf.json`, builds everything, signs the installer with the update key, and publishes a
   GitHub Release `desktop-v30.1.0` with `Archive-X_30.1.0_x64-setup.exe`, its `.sig`, the `.msi` and `latest.json`
   (`desktop/scripts/make_latest_json.py`; the last five commit subjects become the "what's new" text).
3. Installed apps see it at their next check. A run without release_version only uploads the installers as a build artifact.

## Setup (once)

- The update key pair was made with `tauri signer generate`. The **public** key is in `desktop/src-tauri/tauri.conf.json`
  (`plugins.updater.pubkey`); the app installs only updates signed by the matching private key.
- Repository secrets (Settings → Secrets and variables → Actions): `TAURI_SIGNING_PRIVATE_KEY` (the whole key file text) and
  `TAURI_SIGNING_PRIVATE_KEY_PASSWORD`. Without them a plain build still works and a release stops with a clear message.
- **Keep the private key and its password.** Lose them and installed apps can never be updated again; they would need a manual
  install of a build with a new public key.
- The repository must be **public** for installed apps to read releases. A private repository answers them with 404 and the check
  fails quietly. Nothing needs to change in the app when it is made public.

## Limits

- Each update downloads the whole installer (the frozen Python backend is inside it); Tauri has no delta updates.
- The first updater-enabled version has to be installed by hand once; updates start from the one after it.
- Windows SmartScreen still warns about an unknown publisher until an Authenticode certificate is bought; updates themselves are
  verified by the update key, not by that certificate.
- Not verified here: a real update on Windows (needs two releases and a public repository). Verified: the shell compiles with the
  plugin, the unit tests, and the `latest.json` generator.
