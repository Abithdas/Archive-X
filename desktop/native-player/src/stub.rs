//! Other systems: nothing to play with.
use std::path::Path;
use std::sync::Arc;

use crate::{AudioTrack, Error, Options, Rect, State};

pub struct Library;
pub struct Player;

impl Library {
    pub fn load(_dll: &Path) -> Result<Library, Error> { Err(Error::Unsupported) }
}

impl Player {
    pub fn create(_lib: Arc<Library>, _lav_dir: &Path, _options: Options) -> Result<Player, Error> { Err(Error::Unsupported) }
    pub fn open(&self, _url: &str, _ua: &str, _parent: Option<isize>, _rect: Rect) -> Result<(), Error> { Err(Error::Unsupported) }
    pub fn close(&self) {}
    pub fn play(&self) -> Result<(), Error> { Err(Error::Unsupported) }
    pub fn pause(&self) -> Result<(), Error> { Err(Error::Unsupported) }
    pub fn stop(&self) -> Result<(), Error> { Err(Error::Unsupported) }
    pub fn seek(&self, _s: f64) -> Result<(), Error> { Err(Error::Unsupported) }
    pub fn set_rate(&self, _r: f64) -> Result<(), Error> { Err(Error::Unsupported) }
    pub fn set_volume(&self, _p: u8) -> Result<(), Error> { Err(Error::Unsupported) }
    pub fn set_muted(&self, _m: bool) -> Result<(), Error> { Err(Error::Unsupported) }
    pub fn set_rect(&self, _r: Rect) -> Result<(), Error> { Err(Error::Unsupported) }
    pub fn set_video_layer(&self, _layer: crate::VideoLayer) -> Result<(), Error> { Err(Error::Unsupported) }
    pub fn diagnostics(&self) -> String { String::new() }
    pub fn hide(&self) {}
    pub fn audio_tracks(&self) -> Result<Vec<AudioTrack>, Error> { Err(Error::Unsupported) }
    pub fn select_audio_track(&self, _i: usize) -> Result<(), Error> { Err(Error::Unsupported) }
    pub fn state(&self) -> State { State::default() }
    pub fn decoder(&self) -> String { String::new() }
    pub fn last_error(&self) -> String { String::new() }
}
