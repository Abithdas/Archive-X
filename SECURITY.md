# Security policy

Archive X is a desktop app that runs on your own PC and keeps its data there. This page says which versions get security fixes, how to report a
problem privately, and what is and is not covered.

## Supported versions

Only the **latest release** (the newest `desktop-v*` on the [Releases page](https://github.com/Abithdas/Archive-X/releases)) gets security
fixes. The installed app checks for updates itself; updates are signed, and the app installs only an update that matches its signature
(see `docs/updates.md`). If you are on an older version, update first and check whether the problem is still there.

## Reporting a vulnerability

**Please do not describe a vulnerability in a public issue, pull request or discussion.**

Use GitHub's private reporting: open the repository's **Security** tab, choose **Report a vulnerability**, and describe:

- what you found and where (the page, the route, the file or the feature);
- the steps to reproduce it, and the version of Archive X;
- what an attacker could do with it, and what they would need first (for example "access to the local network" or "a crafted Telegram message").

If the button is not there, open an ordinary issue that says only that you have a security report and asks for a private way to send it. Leave
the details out of it.

This is a small project looked after by one person, so replies are best effort. You will get an acknowledgement, and the report will be handled
as fast as its severity calls for. Please give a reasonable time to fix a problem before you make it public.

## What is in scope

- the web interface and its API (login, sessions, the setup step, anything that reads or changes your library or settings);
- the front door and the desktop app (the update check, opening files and links, what the web page may ask the shell to do);
- how media from Telegram and metadata collected from external APIs are handled (image and video parsing, file names, downloads, API keys kept on this PC, and what is sent to those APIs);
- the local AI assistant: it must never see keys, tokens, passwords or cookies, and an action that changes or deletes anything needs your
  permission first (`telegram_archive/core/ai_safety.py`). A way around either rule is a vulnerability;
- the installer and the update files published with each release.

## What is not in scope

- Telegram itself and the third-party APIs Archive X collects metadata from (their servers, accounts, rate limits and the data they return);
- problems that need you to already control the PC or its Windows account, or to hand over your own login;
- the Windows SmartScreen warning when you run the installer: the installer is not yet signed with a code-signing certificate, which is known;
- reports that only a scanner produced, with no way to show that the code in question is reachable in Archive X.

## How dependencies are watched

Dependabot proposes updates to the Python, npm, Rust and GitHub Actions dependencies, and the *Dependency scan (SCA)* workflow
(`.github/workflows/sca.yml`) runs weekly and on every change to a dependency list. The last full report is `docs/security/SCA-REPORT.md`.
A flagged package that is not part of what is shipped (for example a Linux-only library that the Windows build does not contain) is noted rather
than patched.
