# 0010. The front door sends plain video files itself

**Status:** accepted.

**Context.** The Rust streaming-gateway plan (block cache, read-ahead, Telegram fetches in Rust) would duplicate what the Python playback engine already does well: 512 KiB segments, a sparse disk cache, read-ahead that follows the playhead, a scheduler that cancels stale reads on a seek, and FloodWait back-off. But two kinds of playback involve no Telegram at all: a video from a local folder, and a Telegram video already saved whole (`/stream-cached`). Python still read those files and sent every byte.

**Decision.** `crates/ax-server/src/native_stream.rs` answers `GET`/`HEAD /stream/{local folder}/{video}` and `/stream-cached/{channel}/{video}` (routes `local_stream`, `cached_stream`) by reading the file with byte ranges, a chunk at a time. Telegram streaming stays in Python.

The rules are Python's, and anything unusual is declined so Python answers as before:
- The SFW / NSFW mode is read on every request; a video of the other mode answers 404 (same as `core/content_guard.py`).
- A local file is sent only while its real path lies inside its folder (same as `core/safe_paths.py`).
- A finished copy counts once it is over 1 KB (both writers rename a finished `.partial` into place).
- Several ranges, `If-Range`, a missing row or file, a Telegram channel on `/stream`: declined.

**Result** (64 MB file, real front door and backend, `tools/bench/stream_files.py`): local-folder sequential throughput about 360 MB/s to about 1,950 MB/s, a 1 MB seek 3.6 ms to 1.6 ms. Playback needs only a few MB/s, so the gain is CPU taken off Python rather than something to see while watching.

**Not done, on purpose.** Serving Telegram cache hits from Rust would share one cache and one playhead between two programs; it waits for measurements that show Python is the bottleneck.
