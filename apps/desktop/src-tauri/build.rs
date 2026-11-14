fn main() {
    // The native player's commands (src/native.rs, COMMANDS) become permissions named allow-native-open and so on, so that a capability
    // can name exactly them. Keep this list and COMMANDS the same (a test in native.rs checks).
    const COMMANDS: &[&str] = &["native_available", "native_open", "native_command", "native_rect", "native_state", "native_tracks", "native_diagnostics"];
    tauri_build::try_build(tauri_build::Attributes::new().app_manifest(tauri_build::AppManifest::new().commands(COMMANDS))).expect("failed to run the Tauri build script");
}
