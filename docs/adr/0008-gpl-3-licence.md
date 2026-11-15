# 0008. Licence: GPL-3.0-or-later

**Status:** accepted (the owner chose it).

**Context.** The repository is public. The installer bundles LAV Filters and a GPL build of FFmpeg, which are distributed under the GPL, so whoever receives the installer must be able to get the corresponding source and use Archive X under compatible terms.

**Decision.** Archive X is licensed GPL-3.0-or-later. `LICENSE` holds the FSF text, every manifest says `GPL-3.0-or-later`, the installer ships `LICENSE` and `THIRD-PARTY-NOTICES.md` in `resources/licenses`, and `tools/release/gen_notices.py` regenerates the notices (Rust crates, web packages, Python packages, and the bundled programs with links to their source and the pinned versions).

**Consequences.** All dependencies found so far are permissive or dual-licensed with a permissive option; none conflicts with the GPL. A new dependency under a licence that does not fit (GPL-2.0-only, AGPL, a non-commercial licence) must be refused or replaced; the dependency scan already reports licences. Anyone may modify and redistribute Archive X, but a distributed derivative must stay GPL and offer its source.
