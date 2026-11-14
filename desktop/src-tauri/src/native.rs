//! The native video player inside the app window (desktop/axplayer, through desktop/native-player).
//!
//! The web page drives it with the commands below. Only the app's own page may call them: capabilities/native-player.json allows exactly
//! these commands for the loopback address and nothing else (no files, no shell, no other windows).
//!
//! Layout: the video is a child window placed BEHIND the web view. While a video is open the web view's background is made transparent, so
//! the page shows the video through its transparent areas and can draw controls and captions over it. If a PC does not show the video
//! that way, `ARCHIVE_X_NATIVE_VIDEO_ON_TOP=1` puts the video window in front instead (the page then keeps its controls outside the video).

use std::path::PathBuf;
use std::sync::Mutex;

use archive_x_native_player::{Engine, HwAccel, Options, Player, Rect};
use serde::Serialize;
use tauri::window::Color;
use tauri::{AppHandle, Manager, State, WebviewWindow};

/// The colour the web view has when no video is showing (the same as `backgroundColor` in tauri.conf.json).
const PAGE_BACKGROUND: Color = Color(35, 40, 51, 255);
const TRANSPARENT: Color = Color(0, 0, 0, 0);

#[derive(Default)]
pub struct NativeState(Mutex<Inner>);

#[derive(Default)]
struct Inner {
    engine: Option<Engine>,
    player: Option<Player>,
}

/// Where the engine and the LAV filters are: the bundled copies, or the paths in ARCHIVE_X_AXPLAYER_DLL / ARCHIVE_X_LAV_DIR (development).
fn locate(app: &AppHandle) -> Result<(PathBuf, PathBuf), String> {
    let resources = app.path().resource_dir().ok();
    let dll = std::env::var("ARCHIVE_X_AXPLAYER_DLL")
        .ok()
        .map(PathBuf::from)
        .or_else(|| resources.as_ref().map(|r| r.join("axplayer").join("axplayer.dll")))
        .ok_or("no resource directory")?;
    let lav = std::env::var("ARCHIVE_X_LAV_DIR")
        .ok()
        .map(PathBuf::from)
        .or_else(|| resources.as_ref().map(|r| r.join("lav")))
        .ok_or("no resource directory")?;
    if !dll.is_file() {
        return Err(format!("the native player engine is not installed ({} is missing)", dll.display()));
    }
    if !lav.join("LAVVideo.ax").is_file() {
        return Err(format!("the LAV decoders are not installed ({} has no LAVVideo.ax)", lav.display()));
    }
    Ok((dll, lav))
}

fn hw_from(code: Option<i32>) -> HwAccel {
    match code {
        Some(0) => HwAccel::None,
        Some(1) => HwAccel::Cuda,
        Some(2) => HwAccel::QuickSync,
        Some(4) => HwAccel::Dxva2Native,
        Some(5) => HwAccel::D3d11,
        _ => HwAccel::Dxva2CopyBack,
    }
}

fn video_on_top() -> bool {
    std::env::var("ARCHIVE_X_NATIVE_VIDEO_ON_TOP").map(|v| v == "1").unwrap_or(false)
}

#[derive(Serialize)]
#[serde(rename_all = "camelCase")]
pub struct Availability {
    available: bool,
    /// Why not, in a sentence, when it is not.
    reason: String,
}

#[derive(Serialize)]
#[serde(rename_all = "camelCase")]
pub struct Opened {
    duration: f64,
    width: i32,
    height: i32,
    decoder: String,
    video_on_top: bool,
}

#[derive(Serialize)]
#[serde(rename_all = "camelCase")]
pub struct Snapshot {
    position: f64,
    duration: f64,
    playing: bool,
    ended: bool,
    buffering: bool,
    width: i32,
    height: i32,
    /// 0, or the HRESULT of a failure that stopped playback.
    error: i32,
    opened: bool,
    message: String,
}

#[derive(Serialize)]
pub struct Track {
    name: String,
    selected: bool,
}

#[tauri::command]
pub async fn native_available(app: AppHandle) -> Availability {
    match locate(&app) {
        Ok(_) if cfg!(windows) => Availability { available: true, reason: String::new() },
        Ok(_) => Availability { available: false, reason: "the native player works on Windows only".into() },
        Err(reason) => Availability { available: false, reason },
    }
}

/// Loads a video and puts its window at the given place (pixels of the app window's client area). Does not start playing.
#[tauri::command]
#[allow(clippy::too_many_arguments)]
pub async fn native_open(app: AppHandle, window: WebviewWindow, state: State<'_, NativeState>, url: String, x: i32, y: i32, w: i32, h: i32, hw: Option<i32>) -> Result<Opened, String> {
    let (dll, lav) = locate(&app)?;
    let mut inner = state.0.lock().map_err(|_| "the player is busy".to_string())?;
    inner.player = None; // a new video gets a new player (this closes the old one)
    if inner.engine.is_none() {
        inner.engine = Some(Engine::load(&dll).map_err(|e| e.to_string())?);
    }
    let engine = inner.engine.clone().ok_or("the engine is not loaded")?;
    let on_top = video_on_top();
    let player = engine
        .create_player(&lav, Options { hw_accel: hw_from(hw), headless: false, video_on_top: on_top })
        .map_err(|e| e.to_string())?;
    #[cfg(windows)]
    let parent = window.hwnd().ok().map(|h| h.0 as isize);
    #[cfg(not(windows))]
    let parent: Option<isize> = None;
    player.open(&url, "Archive X", parent, Rect { x, y, w, h }).map_err(|e| e.to_string())?;
    if !on_top {
        // The page shows the video through its transparent areas.
        let _ = window.set_background_color(Some(TRANSPARENT));
    }
    let s = player.state();
    let opened = Opened { duration: s.duration, width: s.video_width, height: s.video_height, decoder: player.decoder(), video_on_top: on_top };
    inner.player = Some(player);
    Ok(opened)
}

/// play, pause, stop, seek (seconds), rate (1.0 = normal), volume (0 to 100), mute (1 or 0), audio (track number), close.
#[tauri::command]
pub async fn native_command(window: WebviewWindow, state: State<'_, NativeState>, command: String, value: Option<f64>) -> Result<(), String> {
    let mut inner = state.0.lock().map_err(|_| "the player is busy".to_string())?;
    if command == "close" {
        inner.player = None;
        let _ = window.set_background_color(Some(PAGE_BACKGROUND));
        return Ok(());
    }
    let p = inner.player.as_ref().ok_or("no video is open")?;
    let v = value.unwrap_or(0.0);
    let result = match command.as_str() {
        "play" => p.play(),
        "pause" => p.pause(),
        "stop" => p.stop(),
        "seek" => p.seek(v),
        "rate" => p.set_rate(v),
        "volume" => p.set_volume(v.clamp(0.0, 100.0) as u8),
        "mute" => p.set_muted(v != 0.0),
        "audio" => p.select_audio_track(v.max(0.0) as usize),
        other => return Err(format!("unknown command: {other}")),
    };
    result.map_err(|e| e.to_string())
}

/// Moves the video window (the page's player area changed: resize, scroll, zoom).
#[tauri::command]
pub async fn native_rect(state: State<'_, NativeState>, x: i32, y: i32, w: i32, h: i32) -> Result<(), String> {
    let inner = state.0.lock().map_err(|_| "the player is busy".to_string())?;
    match inner.player.as_ref() {
        Some(p) => p.set_rect(Rect { x, y, w, h }).map_err(|e| e.to_string()),
        None => Ok(()),
    }
}

#[tauri::command]
pub async fn native_state(state: State<'_, NativeState>) -> Result<Snapshot, String> {
    let inner = state.0.lock().map_err(|_| "the player is busy".to_string())?;
    let p = inner.player.as_ref().ok_or("no video is open")?;
    let s = p.state();
    Ok(Snapshot {
        position: s.position,
        duration: s.duration,
        playing: s.playing,
        ended: s.ended,
        buffering: s.buffering,
        width: s.video_width,
        height: s.video_height,
        error: s.error,
        opened: s.opened,
        message: p.last_error(),
    })
}

#[tauri::command]
pub async fn native_tracks(state: State<'_, NativeState>) -> Result<Vec<Track>, String> {
    let inner = state.0.lock().map_err(|_| "the player is busy".to_string())?;
    let p = inner.player.as_ref().ok_or("no video is open")?;
    Ok(p.audio_tracks().map_err(|e| e.to_string())?.into_iter().map(|t| Track { name: t.name, selected: t.selected }).collect())
}

/// On exit: stop the video and release the engine (DirectShow wants an orderly teardown).
pub fn shutdown(app: &AppHandle) {
    if let Some(state) = app.try_state::<NativeState>() {
        if let Ok(mut inner) = state.0.lock() {
            inner.player = None;
        }
    }
}

/// The command names; build.rs keeps its own copy (it cannot use this module) and the test below checks they agree.
#[cfg(test)]
const COMMANDS: &[&str] = &["native_available", "native_open", "native_command", "native_rect", "native_state", "native_tracks"];

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn hardware_codes_map_to_lavs_options() {
        assert_eq!(hw_from(Some(0)), HwAccel::None);
        assert_eq!(hw_from(Some(4)), HwAccel::Dxva2Native);
        assert_eq!(hw_from(Some(5)), HwAccel::D3d11);
        assert_eq!(hw_from(None), HwAccel::Dxva2CopyBack);
        assert_eq!(hw_from(Some(99)), HwAccel::Dxva2CopyBack);
    }

    /// build.rs makes permissions from its own copy of the list, and the capability file names them: all three must agree.
    #[test]
    fn commands_build_script_and_capability_agree() {
        let build = include_str!("../build.rs");
        let capability = include_str!("../capabilities/native-player.json");
        for name in COMMANDS {
            assert!(build.contains(&format!("\"{name}\"")), "build.rs lacks {name}");
            let permission = format!("allow-{}", name.replace('_', "-"));
            assert!(capability.contains(&format!("\"{permission}\"")), "the capability lacks {permission}");
        }
        assert_eq!(capability.matches("\"allow-").count(), COMMANDS.len(), "the capability allows something that is not a player command");
    }
}
