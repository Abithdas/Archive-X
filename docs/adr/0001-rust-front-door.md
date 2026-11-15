# 0001. A Rust front door in front of the Python backend

**Status:** accepted, built and shipped (30.1.0 onward).

**Context.** The backend is about 100,000 lines of Python with few tests. A rewrite followed by one switch would be the highest-risk way to move to Rust.

**Decision.** A small Rust server (`crates/ax-server`) sits on the app's port. It serves the web app's files and the routes already ported, and forwards every other request to the Python backend, which keeps running behind it. Routes move across one at a time, each switched on only after `tools/parity/compare.py` shows the same answers as Python.

**Consequences.** Any step can be switched off without a new build. The HTTP API, the database file and the data folder must stay compatible until Python is gone. Two processes run during the whole migration, and the front door must keep the same login and cross-site rules as Python.
