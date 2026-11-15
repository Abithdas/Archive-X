# ax-db: the database

Access to the Archive X SQLite file (WAL mode), shared with the Python backend.

**Owns:** a pool of read-only connections (`Db`, with the same cache and memory-map settings as Python's reader); the one writer (`Writer`, opened on first use, never creates the file, every write one `BEGIN IMMEDIATE` transaction); the session lookup (`sessions.rs`, the same token hash and expiry rules as the backend); the list of tables and columns the Rust side relies on (`REQUIRED_COLUMNS`, checked against `schema/schema.json`).

**Rule (docs/adr/0002, 0007):** a table is written by Python or by Rust, never both, apart from the exceptions listed in ADR 0007. Reading any table is always fine.
