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

/// Opens a web address in the person's own browser. The page's `target="_blank"` links do nothing inside the app window, so the page asks here.
/// Only plain http(s) addresses are accepted, and the program is started with the address as one argument (no shell), so nothing in it is interpreted.
#[tauri::command]
pub fn open_external(url: String) -> Result<(), String> {
    let u = url.trim();
    let plain = (u.starts_with("https://") || u.starts_with("http://"))
        && u.len() <= 2000
        && !u.chars().any(|c| c.is_control() || c.is_whitespace() || matches!(c, '"' | '<' | '>' | '^' | '`' | '|' | '\\'));
    if !plain {
        return Err("That is not a web address that can be opened.".into());
    }
    #[cfg(windows)]
    {
        use std::os::windows::process::CommandExt;
        std::process::Command::new("rundll32")
            .args(["url.dll,FileProtocolHandler", u])
            .creation_flags(0x0800_0000)
            .spawn()
            .map(|_| ())
            .map_err(|e| e.to_string())
    }
    #[cfg(not(windows))]
    {
        std::process::Command::new("xdg-open").arg(u).spawn().map(|_| ()).map_err(|e| e.to_string())
    }
}

#[cfg(test)]
mod open_external_tests {
    use super::open_external;

    #[test]
    fn only_plain_web_addresses_are_opened() {
        for bad in ["file:///c:/windows/system32/calc.exe", "javascript:alert(1)", "calc.exe", "https://a.example/x y", "https://a.example/\"&calc", "https://a.example/a|b", ""] {
            assert!(open_external(bad.into()).is_err(), "{bad}");
        }
    }
}
