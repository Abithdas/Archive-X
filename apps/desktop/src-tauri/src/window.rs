//! The window's own buttons. The Archive X page draws a top bar with minimise, maximise and close in it (like a browser's tab strip), so the
//! native title bar goes while that bar is showing and comes back if the page is not there (the splash and error pages keep it).
//!
//! Each command acts on the window it was called from, and the page that may call them is the one served by the local backend
//! (capabilities/native-player.json): they move, shrink, grow and close that one window, and nothing else.

use tauri::WebviewWindow;

fn text(e: tauri::Error) -> String {
    e.to_string()
}

/// `custom` true: the page draws the window's buttons, so the native title bar is hidden. False: the native one is back.
#[tauri::command]
pub fn window_chrome(window: WebviewWindow, custom: bool) -> Result<(), String> {
    window.set_decorations(!custom).map_err(text)
}

#[tauri::command]
pub fn window_minimize(window: WebviewWindow) -> Result<(), String> {
    window.minimize().map_err(text)
}

/// Maximise, or restore when it already is. Returns whether the window is maximised afterwards.
#[tauri::command]
pub fn window_toggle_maximize(window: WebviewWindow) -> Result<bool, String> {
    if window.is_maximized().map_err(text)? {
        window.unmaximize().map_err(text)?;
    } else {
        window.maximize().map_err(text)?;
    }
    window.is_maximized().map_err(text)
}

#[tauri::command]
pub fn window_is_maximized(window: WebviewWindow) -> Result<bool, String> {
    window.is_maximized().map_err(text)
}

/// The same as the native close button: the app's normal shutdown follows (lifecycle.rs).
#[tauri::command]
pub fn window_close(window: WebviewWindow) -> Result<(), String> {
    window.close().map_err(text)
}

/// Start moving the window; the page calls it when the bar is pressed.
#[tauri::command]
pub fn window_drag(window: WebviewWindow) -> Result<(), String> {
    window.start_dragging().map_err(text)
}
