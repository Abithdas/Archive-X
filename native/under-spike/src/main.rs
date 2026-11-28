//! The proof: open a video in the libmpv engine's "under" layer (or another, for a control run) inside a transparent window, and write down what the engine says.
//! Environment: SPIKE_AXMPV_DLL, SPIKE_MPV_DIR, SPIKE_VIDEO (a file), SPIKE_STATUS (a JSON file written every second), SPIKE_LAYER (under, popup, front, behind),
//! SPIKE_DECORATIONS=0 to take the title bar off.
#![cfg_attr(windows, windows_subsystem = "windows")]

use std::path::PathBuf;
use std::time::Duration;

use archive_x_native_player::{Engine, HwAccel, Options, Rect, VideoLayer};
use tauri::window::Color;
use tauri::Manager;

fn var(name: &str) -> Result<String, String> {
    std::env::var(name).map_err(|_| format!("{name} is not set"))
}

fn run(window: &tauri::WebviewWindow, status: &PathBuf) -> Result<(), String> {
    let dll = PathBuf::from(var("SPIKE_AXMPV_DLL")?);
    let dir = PathBuf::from(var("SPIKE_MPV_DIR")?);
    let video = var("SPIKE_VIDEO")?;
    let layer = match var("SPIKE_LAYER").unwrap_or_default().as_str() {
        "popup" => VideoLayer::Popup,
        "front" => VideoLayer::Front,
        "behind" => VideoLayer::Behind,
        _ => VideoLayer::Under,
    };
    std::thread::sleep(Duration::from_millis(2500)); // the window and the page are up
    let engine = Engine::load(&dll).map_err(|e| e.to_string())?;
    let player = engine.create_player(&dir, Options { hw_accel: HwAccel::None, headless: false, video_layer: layer }).map_err(|e| e.to_string())?;
    let size = window.inner_size().map_err(|e| e.to_string())?;
    #[cfg(windows)]
    let parent = window.hwnd().ok().map(|h| h.0 as isize);
    #[cfg(not(windows))]
    let parent: Option<isize> = None;
    player
        .open(&video, "under-spike", parent, Rect { x: 0, y: 0, w: size.width as i32, h: size.height as i32 })
        .map_err(|e| format!("open: {e}"))?;
    player.play().map_err(|e| format!("play: {e}"))?;
    for _ in 0..90 {
        std::thread::sleep(Duration::from_secs(1));
        let s = player.state();
        let pos = window.inner_position().map_err(|e| e.to_string())?;
        let sz = window.inner_size().map_err(|e| e.to_string())?;
        let doc = serde_json::json!({
            "ok": true, "layer": format!("{:?}", s.video_layer), "position": s.position, "duration": s.duration, "playing": s.playing, "opened": s.opened, "error": s.error,
            "window_visible": s.window_visible, "window_width": s.window_width, "window_height": s.window_height,
            "client_x": pos.x, "client_y": pos.y, "client_w": sz.width, "client_h": sz.height, "diagnostics": player.diagnostics(),
        });
        let _ = std::fs::write(status, doc.to_string());
    }
    Ok(())
}

fn main() {
    tauri::Builder::default()
        .setup(|app| {
            let window = app.get_webview_window("main").ok_or("no window")?;
            let _ = window.set_background_color(Some(Color(0, 0, 0, 0)));
            if std::env::var("SPIKE_DECORATIONS").as_deref() == Ok("0") {
                let _ = window.set_decorations(false);
            }
            let status = PathBuf::from(var("SPIKE_STATUS")?);
            let w = window.clone();
            std::thread::spawn(move || {
                if let Err(e) = run(&w, &status) {
                    let _ = std::fs::write(&status, serde_json::json!({ "ok": false, "error_text": e }).to_string());
                }
            });
            Ok(())
        })
        .run(tauri::generate_context!())
        .expect("the proof app failed");
}
