# 0003. New features are written in Rust

**Status:** accepted 14 November 2026.

**Context.** The Python backend is being replaced. Features written in Python now would have to be ported later.

**Decision.** New backend features are written in Rust (the performer name list and face learning were first). Python gets bug fixes and deletions only. The one exception so far is the missing-pictures fetcher, which uses the Python provider code and writes the Python database; it moves in the providers phase.

**Consequences.** Some features are delayed until the Rust side has what they need (login, the writer for a table group). Python is not tidied or refactored.
