fn main() {
    // The updater's, the window buttons' and open_external's commands (src/updates.rs; src/window.rs) become permissions named allow-update-check and so on, so that a capability
    // can name exactly them.
    const COMMANDS: &[&str] = &[
        "update_status",
        "update_check",
        "update_install",
        "window_chrome",
        "window_minimize",
        "window_toggle_maximize",
        "window_is_maximized",
        "window_close",
        "window_drag",
        "open_external",
    ];
    tauri_build::try_build(tauri_build::Attributes::new().app_manifest(tauri_build::AppManifest::new().commands(COMMANDS))).expect("failed to run the Tauri build script");
}
