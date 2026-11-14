//! Safe Rust access to `axplayer.dll` (desktop/axplayer): the native video engine.
//!
//! The DLL is loaded when asked for, not linked, so this crate builds on every system and the app can fall back to the web player when the
//! DLL is missing. On systems other than Windows [`Engine::load`] always answers [`Error::Unsupported`].
//!
//! Every call is forwarded to the engine's own worker thread, so a [`Player`] may be used from any thread.

use std::fmt;
use std::path::Path;
use std::sync::Arc;

/// Which hardware decoding LAV Video may use (LAV's own numbers). Software decoding is used whenever the hardware cannot do a video.
#[derive(Clone, Copy, Debug, PartialEq, Eq)]
pub enum HwAccel {
    None = 0,
    Cuda = 1,
    QuickSync = 2,
    /// Works with every renderer; the default.
    Dxva2CopyBack = 3,
    /// Needs a renderer that shares Direct3D 9 surfaces (the Enhanced Video Renderer does).
    Dxva2Native = 4,
    D3d11 = 5,
}

#[derive(Clone, Copy, Debug, PartialEq, Eq)]
pub struct Options {
    pub hw_accel: HwAccel,
    /// No window and no sound device (null renderers): for automated tests.
    pub headless: bool,
    /// false (default): the video window sits behind the web page, which shows the video through its transparent areas; true: in front.
    pub video_on_top: bool,
}

impl Default for Options {
    fn default() -> Self {
        Options { hw_accel: HwAccel::Dxva2CopyBack, headless: false, video_on_top: false }
    }
}

/// Where the video window sits inside its parent window, in pixels of the parent.
#[derive(Clone, Copy, Debug, Default, PartialEq, Eq)]
pub struct Rect {
    pub x: i32,
    pub y: i32,
    pub w: i32,
    pub h: i32,
}

/// What the engine reports; read without waiting.
#[derive(Clone, Copy, Debug, Default, PartialEq)]
pub struct State {
    pub position: f64,
    pub duration: f64,
    pub playing: bool,
    pub ended: bool,
    pub buffering: bool,
    pub video_width: i32,
    pub video_height: i32,
    /// 0, or the HRESULT of a failure that stopped playback.
    pub error: i32,
    pub opened: bool,
}

#[derive(Clone, Debug, PartialEq, Eq)]
pub struct AudioTrack {
    pub name: String,
    pub selected: bool,
}

#[derive(Debug)]
pub enum Error {
    /// Not Windows, or the engine is not available on this system.
    Unsupported,
    /// The DLL could not be loaded or lacks a function.
    Load(String),
    /// The engine refused: the HRESULT and what it said.
    Engine { code: i32, message: String },
}

impl fmt::Display for Error {
    fn fmt(&self, f: &mut fmt::Formatter<'_>) -> fmt::Result {
        match self {
            Error::Unsupported => write!(f, "the native player is not available on this system"),
            Error::Load(m) => write!(f, "could not load the native player: {m}"),
            Error::Engine { code, message } => write!(f, "{message} (0x{:08X})", *code as u32),
        }
    }
}

impl std::error::Error for Error {}

#[cfg(windows)]
mod imp;
#[cfg(not(windows))]
mod stub;
#[cfg(windows)]
use imp as sys;
#[cfg(not(windows))]
use stub as sys;

/// The loaded DLL. Cheap to clone; players keep it loaded.
#[derive(Clone)]
pub struct Engine(Arc<sys::Library>);

impl Engine {
    /// Loads `axplayer.dll` from the given path.
    pub fn load(dll: &Path) -> Result<Engine, Error> {
        sys::Library::load(dll).map(|l| Engine(Arc::new(l)))
    }

    /// Creates a player whose filters (LAVSplitter.ax, LAVVideo.ax, LAVAudio.ax) are loaded from `lav_dir`.
    pub fn create_player(&self, lav_dir: &Path, options: Options) -> Result<Player, Error> {
        sys::Player::create(self.0.clone(), lav_dir, options).map(Player)
    }
}

/// One video player. Dropping it stops playback and releases everything.
pub struct Player(sys::Player);

impl Player {
    /// Loads a video from an address (http, https or a file path). `parent` is the HWND to put the video window in (None: a hidden window, or
    /// none when headless). Playback starts with [`Player::play`].
    pub fn open(&self, url: &str, user_agent: &str, parent: Option<isize>, rect: Rect) -> Result<(), Error> { self.0.open(url, user_agent, parent, rect) }
    pub fn close(&self) { self.0.close() }
    pub fn play(&self) -> Result<(), Error> { self.0.play() }
    pub fn pause(&self) -> Result<(), Error> { self.0.pause() }
    pub fn stop(&self) -> Result<(), Error> { self.0.stop() }
    pub fn seek(&self, seconds: f64) -> Result<(), Error> { self.0.seek(seconds) }
    /// 1.0 is normal speed.
    pub fn set_rate(&self, rate: f64) -> Result<(), Error> { self.0.set_rate(rate) }
    /// 0 to 100.
    pub fn set_volume(&self, percent: u8) -> Result<(), Error> { self.0.set_volume(percent.min(100)) }
    pub fn set_muted(&self, muted: bool) -> Result<(), Error> { self.0.set_muted(muted) }
    pub fn set_rect(&self, rect: Rect) -> Result<(), Error> { self.0.set_rect(rect) }
    pub fn audio_tracks(&self) -> Result<Vec<AudioTrack>, Error> { self.0.audio_tracks() }
    pub fn select_audio_track(&self, index: usize) -> Result<(), Error> { self.0.select_audio_track(index) }
    pub fn state(&self) -> State { self.0.state() }
    /// What decodes the video right now (for example "avcodec"); empty before a video is loaded.
    pub fn decoder(&self) -> String { self.0.decoder() }
    pub fn last_error(&self) -> String { self.0.last_error() }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[cfg(not(windows))]
    #[test]
    fn off_windows_the_engine_is_simply_unsupported() {
        assert!(matches!(Engine::load(Path::new("axplayer.dll")), Err(Error::Unsupported)));
    }

    #[test]
    fn errors_read_naturally() {
        assert_eq!(Error::Unsupported.to_string(), "the native player is not available on this system");
        let e = Error::Engine { code: 0x80004005u32 as i32, message: "opening the address failed".into() };
        assert_eq!(e.to_string(), "opening the address failed (0x80004005)");
    }

    #[test]
    fn defaults_ask_for_hardware_decoding_with_a_window() {
        let o = Options::default();
        assert_eq!(o.hw_accel, HwAccel::Dxva2CopyBack);
        assert!(!o.headless);
        assert!(!o.video_on_top);
        assert_eq!(HwAccel::D3d11 as i32, 5);
    }
}
