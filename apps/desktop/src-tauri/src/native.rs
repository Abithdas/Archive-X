//! The native video player inside the app window (native/axplayer, through crates/native-player).
//!
//! The web page drives it with the commands below. Only the app's own page may call them: capabilities/native-player.json allows exactly
//! these commands for the loopback address and nothing else (no files, no shell, no other windows).
//!
//! Layout: by default the video is its own borderless window owned by the app window: it sits exactly over the page's stage area, follows the
//! app when it moves, resizes, minimises or hides, and the page keeps its controls above and below it. On a real PC this is the one layer
//! that shows the picture; the alternatives are a child window in front of the web view or behind it (the web view is then made transparent
//! so the page could draw over the picture), and Windows often hides those ("airspace"). The page can switch layer while a video plays
//! (commands "front", "back" and "popup"); `ARCHIVE_X_NATIVE_VIDEO_FRONT=1` or `ARCHIVE_X_NATIVE_VIDEO_BEHIND=1` change the starting layer.

use std::path::PathBuf;
use std::sync::atomic::{AtomicU64, Ordering};
use std::sync::{Arc, Mutex};

use archive_x_native_player::{Engine, HwAccel, Options, Player, Rect, VideoLayer};
use serde::Serialize;
use tauri::window::Color;
use tauri::{AppHandle, Manager, State, WebviewWindow};

/// The colour the web view has when no video is showing (the same as `backgroundColor` in tauri.conf.json).
const PAGE_BACKGROUND: Color = Color(35, 40, 51, 255);
const TRANSPARENT: Color = Color(0, 0, 0, 0);

#[derive(Default)]
pub struct NativeState {
    inner: Mutex<Inner>,
    /// Bumped by every "close" and every new open. An open that finds it changed when it finishes was cancelled while it was loading (the page was
    /// left, or another video was chosen): its window is taken down at once instead of being left over the page.
    generation: AtomicU64,
}

#[derive(Default)]
struct Inner {
    engine: Option<Engine>,
    player: Option<Arc<Player>>,
}

/// Which engine plays: `ARCHIVE_X_NATIVE_ENGINE=mpv` picks libmpv (axmpv.dll and the folder with libmpv-2.dll), `lav` picks the LAV Filters (axplayer.dll). Unset, the LAV
/// engine is used when it is installed, and libmpv otherwise (an installer that has only libmpv, see packaging/scripts/fetch-mpv.sh).
fn engine_choice(has_lav: bool, has_mpv: bool) -> &'static str {
    match std::env::var("ARCHIVE_X_NATIVE_ENGINE").ok().as_deref() {
        Some("mpv") => "mpv",
        Some("lav") => "lav",
        _ if has_lav || !has_mpv => "lav",
        _ => "mpv",
    }
}

/// Where the engine and the folder with its decoders are: the bundled copies, or the paths in ARCHIVE_X_AXPLAYER_DLL / ARCHIVE_X_LAV_DIR (LAV) and
/// ARCHIVE_X_AXMPV_DLL / ARCHIVE_X_MPV_DIR (libmpv), for development.
fn locate(app: &AppHandle) -> Result<(PathBuf, PathBuf), String> {
    let resources = app.path().resource_dir().ok();
    let pick = |var: &str, rel: &[&str]| -> Option<PathBuf> {
        std::env::var(var).ok().map(PathBuf::from).or_else(|| resources.as_ref().map(|r| rel.iter().fold(r.clone(), |p, x| p.join(x))))
    };
    let lav_dll = pick("ARCHIVE_X_AXPLAYER_DLL", &["axplayer", "axplayer.dll"]);
    let lav_dir = pick("ARCHIVE_X_LAV_DIR", &["lav"]);
    let mpv_dll = pick("ARCHIVE_X_AXMPV_DLL", &["axplayer", "axmpv.dll"]);
    let mpv_dir = pick("ARCHIVE_X_MPV_DIR", &["mpv"]);
    let has_lav = lav_dll.as_ref().is_some_and(|p| p.is_file()) && lav_dir.as_ref().is_some_and(|d| d.join("LAVVideo.ax").is_file());
    let has_mpv = mpv_dll.as_ref().is_some_and(|p| p.is_file()) && mpv_dir.as_ref().is_some_and(|d| d.join("libmpv-2.dll").is_file());
    if engine_choice(has_lav, has_mpv) == "mpv" {
        let (dll, dir) = (mpv_dll.ok_or("no resource directory")?, mpv_dir.ok_or("no resource directory")?);
        if !dll.is_file() {
            return Err(format!("the libmpv player engine is not installed ({} is missing)", dll.display()));
        }
        if !dir.join("libmpv-2.dll").is_file() {
            return Err(format!("libmpv is not installed ({} has no libmpv-2.dll)", dir.display()));
        }
        return Ok((dll, dir));
    }
    let (dll, lav) = (lav_dll.ok_or("no resource directory")?, lav_dir.ok_or("no resource directory")?);
    if !dll.is_file() {
        return Err(format!("the native player engine is not installed ({} is missing)", dll.display()));
    }
    if !lav.join("LAVVideo.ax").is_file() {
        return Err(format!("the LAV decoders are not installed ({} has no LAVVideo.ax)", lav.display()));
    }
    Ok((dll, lav))
}

#[cfg(test)]
mod engine_tests {
    use super::engine_choice;

    #[test]
    fn lav_is_used_when_installed_and_libmpv_only_when_it_is_all_there_is() {
        std::env::remove_var("ARCHIVE_X_NATIVE_ENGINE");
        assert_eq!(engine_choice(true, true), "lav");
        assert_eq!(engine_choice(true, false), "lav");
        assert_eq!(engine_choice(false, true), "mpv");
        assert_eq!(engine_choice(false, false), "lav"); // nothing installed: the LAV message says what is missing
    }
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

/// The video's own window above the app, unless ARCHIVE_X_NATIVE_VIDEO_FRONT=1 (a child window in front of the page) or
/// ARCHIVE_X_NATIVE_VIDEO_BEHIND=1 (behind it). The own window is the default because it is the one layer that shows the picture on a
/// real PC: the web view paints over, or hides, a child window placed in the same window.
fn start_layer() -> VideoLayer {
    let on = |name: &str| std::env::var(name).map(|v| v == "1").unwrap_or(false);
    if on("ARCHIVE_X_NATIVE_VIDEO_UNDER") {
        VideoLayer::Under
    } else if on("ARCHIVE_X_NATIVE_VIDEO_BEHIND") {
        VideoLayer::Behind
    } else if on("ARCHIVE_X_NATIVE_VIDEO_FRONT") {
        VideoLayer::Front
    } else {
        VideoLayer::Popup
    }
}

/// Only the behind layer needs the web view to be see-through.
fn background_for(layer: VideoLayer) -> Option<Color> {
    Some(if matches!(layer, VideoLayer::Behind | VideoLayer::Under) { TRANSPARENT } else { PAGE_BACKGROUND })
}

fn layer_name(layer: VideoLayer) -> &'static str {
    match layer {
        VideoLayer::Behind => "behind",
        VideoLayer::Front => "front",
        VideoLayer::Popup => "popup",
        VideoLayer::Under => "under",
    }
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
    layer: &'static str,
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
    /// The video window: shown, its real size in pixels, and whether it is in front of the page.
    window_visible: bool,
    window_width: i32,
    window_height: i32,
    /// "behind", "front" or "popup".
    layer: &'static str,
    /// Frames the renderer says it drew and dropped (-1: unknown).
    frames_drawn: i32,
    frames_dropped: i32,
    /// What is really on screen where the video window is: distinct colours (1 or 2 = flat) and brightness range.
    screen_distinct: i32,
    screen_luma_range: i32,
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
pub async fn native_open(
    app: AppHandle,
    window: WebviewWindow,
    state: State<'_, NativeState>,
    url: String,
    x: i32,
    y: i32,
    w: i32,
    h: i32,
    hw: Option<i32>,
) -> Result<Opened, String> {
    let (dll, lav) = locate(&app)?;
    // The lock is held only for short steps. Opening a stream can take many seconds (a slow source), and everything else (hide, close, the
    // position the page asks for four times a second) used to wait behind it: the video could not be hidden or closed, and the page seemed stuck.
    let (engine, mine) = {
        let mut inner = state.inner.lock().map_err(|_| "the player is busy".to_string())?;
        retire(inner.player.take()); // a new video gets a new player (this closes the old one)
        let mine = state.generation.fetch_add(1, Ordering::SeqCst) + 1;
        if inner.engine.is_none() {
            inner.engine = Some(Engine::load(&dll).map_err(|e| e.to_string())?);
        }
        (inner.engine.clone().ok_or("the engine is not loaded")?, mine)
    };
    let layer = start_layer();
    let player = engine.create_player(&lav, Options { hw_accel: hw_from(hw), headless: false, video_layer: layer }).map_err(|e| e.to_string())?;
    #[cfg(windows)]
    let parent = window.hwnd().ok().map(|h| h.0 as isize);
    #[cfg(not(windows))]
    let parent: Option<isize> = None;
    player.open(&url, "Archive X", parent, Rect { x, y, w, h }).map_err(|e| e.to_string())?;
    let s = player.state();
    let opened = Opened { duration: s.duration, width: s.video_width, height: s.video_height, decoder: player.decoder(), layer: layer_name(layer) };
    let mut inner = state.inner.lock().map_err(|_| "the player is busy".to_string())?;
    if state.generation.load(Ordering::SeqCst) != mine {
        retire(Some(Arc::new(player)));
        return Err("the video was closed while it was opening".into());
    }
    let _ = window.set_background_color(background_for(layer));
    inner.player = Some(Arc::new(player));
    Ok(opened)
}

/// Take a player down without making anybody wait: the picture goes at once, and the rest (stopping the stream can take seconds when the
/// source is slow) happens on its own thread. Waiting here froze the app's window, which shares an input queue with the video window.
fn retire(player: Option<Arc<Player>>) {
    let Some(player) = player else { return };
    player.hide();
    std::thread::spawn(move || {
        let _held = player; // dropped here, on this thread
    });
}

/// play, pause, stop, seek (seconds), rate (1.0 = normal), volume (0 to 100), mute (1 or 0), audio (track number), front, back, popup, hide, close.
#[tauri::command]
pub async fn native_command(window: WebviewWindow, state: State<'_, NativeState>, command: String, value: Option<f64>) -> Result<(), String> {
    let mut inner = state.inner.lock().map_err(|_| "the player is busy".to_string())?;
    if command == "hide" {
        if let Some(p) = inner.player.as_ref() {
            p.hide();
        }
        return Ok(());
    }
    if command == "close" {
        state.generation.fetch_add(1, Ordering::SeqCst);
        retire(inner.player.take());
        let _ = window.set_background_color(Some(PAGE_BACKGROUND));
        return Ok(());
    }
    let p = inner.player.clone().ok_or("no video is open")?;
    drop(inner); // engine calls (a seek on a slow stream) run without the lock, so hide and close never wait behind them
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
        "front" | "back" | "popup" | "under" => {
            let layer = match command.as_str() {
                "front" => VideoLayer::Front,
                "back" => VideoLayer::Behind,
                "under" => VideoLayer::Under,
                _ => VideoLayer::Popup,
            };
            let r = p.set_video_layer(layer);
            let _ = window.set_background_color(background_for(layer));
            r
        }
        other => return Err(format!("unknown command: {other}")),
    };
    result.map_err(|e| e.to_string())
}

/// Moves the video window (the page's player area changed: resize, scroll, zoom).
#[tauri::command]
pub async fn native_rect(state: State<'_, NativeState>, x: i32, y: i32, w: i32, h: i32) -> Result<(), String> {
    let player = state.inner.lock().map_err(|_| "the player is busy".to_string())?.player.clone();
    match player {
        Some(p) => p.set_rect(Rect { x, y, w, h }).map_err(|e| e.to_string()),
        None => Ok(()),
    }
}

#[tauri::command]
pub async fn native_state(state: State<'_, NativeState>) -> Result<Snapshot, String> {
    let p = state.inner.lock().map_err(|_| "the player is busy".to_string())?.player.clone().ok_or("no video is open")?;
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
        window_visible: s.window_visible,
        window_width: s.window_width,
        window_height: s.window_height,
        layer: layer_name(s.video_layer),
        frames_drawn: s.frames_drawn,
        frames_dropped: s.frames_dropped,
        screen_distinct: s.screen_distinct,
        screen_luma_range: s.screen_luma_range,
    })
}

/// A plain-text report of the player's windows, renderer counters and what is on screen, for the page to show and send to support.
#[tauri::command]
pub async fn native_diagnostics(state: State<'_, NativeState>) -> Result<String, String> {
    let p = state.inner.lock().map_err(|_| "the player is busy".to_string())?.player.clone().ok_or("no video is open")?;
    Ok(p.diagnostics())
}

#[tauri::command]
pub async fn native_tracks(state: State<'_, NativeState>) -> Result<Vec<Track>, String> {
    let p = state.inner.lock().map_err(|_| "the player is busy".to_string())?.player.clone().ok_or("no video is open")?;
    Ok(p.audio_tracks().map_err(|e| e.to_string())?.into_iter().map(|t| Track { name: t.name, selected: t.selected }).collect())
}

/// On exit: stop the video and release the engine (DirectShow wants an orderly teardown).
pub fn shutdown(app: &AppHandle) {
    if let Some(state) = app.try_state::<NativeState>() {
        if let Ok(mut inner) = state.inner.lock() {
            inner.player = None;
        }
    }
}

/// The names of every command the page may call (the player's, the updater's and the window buttons', see updates.rs and window.rs); build.rs keeps its own copy (it cannot use this module) and the test below checks they agree.
#[cfg(test)]
const COMMANDS: &[&str] = &[
    "native_available",
    "native_open",
    "native_command",
    "native_rect",
    "native_state",
    "native_tracks",
    "native_diagnostics",
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
        assert_eq!(capability.matches("\"allow-").count(), COMMANDS.len(), "the capability allows something that is not one of these commands");
    }
}
