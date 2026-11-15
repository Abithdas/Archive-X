# 0002. One writer per table group

**Status:** accepted.

**Context.** Python and Rust open the same SQLite file (WAL mode). Two writers to the same tables would race and could corrupt the meaning of the data even when SQLite itself stays consistent.

**Decision.** Rust may read any table, read-only. A table is written by Python or by Rust, never both, and a whole table group changes owner in one release. Rust code that needs its own writable data (the performer name list) keeps it in its own database file.

**Consequences.** A write route moves together with every other writer of its tables. Until sign-in moves, Rust asks Python who is signed in, and gets the provider keys from Python over `/internal/` routes that need the launch secret.
