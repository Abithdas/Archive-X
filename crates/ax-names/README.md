# ax-names: the performer name list

A local copy of the performer names that StashDB and ThePornDB know, kept in its own database file (`performer_names.db`, owned by Rust), searched instantly and refreshed in the background.

**Owns:** the store and its schema; incremental refresh (StashDB by `updated_at`, resumable by page; ThePornDB weekly, in full); name matching (words in any order, aliases); the `Fetch` trait that the tests replace with a fake.

**Does not own:** the provider keys (the backend holds them; the front door asks for them) or the HTTP routes (`ax-server/src/names.rs`).
