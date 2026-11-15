# native-player: the video engine

A Rust wrapper for `axplayer.dll` (C++, `native/axplayer`): DirectShow with LAV Filters and the Windows renderer, in its own window or a layer of the app window. The DLL is loaded at run time, so the crate compiles everywhere and plays only on Windows (`stub.rs` elsewhere).

See docs/native-player.md and docs/adr/0004.
