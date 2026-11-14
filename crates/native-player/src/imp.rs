//! Windows: load axplayer.dll and call its C functions.
use std::ffi::{c_char, c_void, CStr};
use std::os::windows::ffi::OsStrExt;
use std::path::Path;
use std::ptr;
use std::sync::Arc;

use crate::{AudioTrack, Error, Options, Rect, State, VideoLayer};

#[repr(C)]
struct AxOptions {
    hw_accel: i32,
    headless: i32,
    video_layer: i32,
}

#[repr(C)]
#[derive(Default)]
struct AxState {
    position: f64,
    duration: f64,
    playing: i32,
    ended: i32,
    buffering: i32,
    video_width: i32,
    video_height: i32,
    error: i32,
    opened: i32,
    win_visible: i32,
    win_width: i32,
    win_height: i32,
    layer: i32,
    frames_drawn: i32,
    frames_dropped: i32,
    screen_distinct: i32,
    screen_luma_range: i32,
}

type Handle = *mut c_void;
type CreateFn = unsafe extern "C" fn(*const u16, *const AxOptions, *mut Handle) -> i32;
type DestroyFn = unsafe extern "C" fn(Handle);
type OpenFn = unsafe extern "C" fn(Handle, *const u16, *const u16, *mut c_void, i32, i32, i32, i32) -> i32;
type VoidFn = unsafe extern "C" fn(Handle);
type CmdFn = unsafe extern "C" fn(Handle) -> i32;
type SeekFn = unsafe extern "C" fn(Handle, f64) -> i32;
type IntFn = unsafe extern "C" fn(Handle, i32) -> i32;
type RectFn = unsafe extern "C" fn(Handle, i32, i32, i32, i32) -> i32;
type CountFn = unsafe extern "C" fn(Handle) -> i32;
type NameFn = unsafe extern "C" fn(Handle, i32, *mut u16, i32) -> i32;
type StateFn = unsafe extern "C" fn(Handle, *mut AxState);
type DecoderFn = unsafe extern "C" fn(Handle, *mut u16, i32) -> i32;
type ErrorFn = unsafe extern "C" fn(Handle) -> *const c_char;

#[derive(Clone, Copy)]
struct Fns {
    create: CreateFn,
    destroy: DestroyFn,
    open: OpenFn,
    close: VoidFn,
    play: CmdFn,
    pause: CmdFn,
    stop: CmdFn,
    seek: SeekFn,
    set_rate: SeekFn,
    set_volume: IntFn,
    set_muted: IntFn,
    set_rect: RectFn,
    set_layer: IntFn,
    diagnostics: DecoderFn,
    hide: VoidFn,
    track_count: CountFn,
    track_name: NameFn,
    select_track: IntFn,
    get_state: StateFn,
    decoder_name: DecoderFn,
    last_error: ErrorFn,
}

pub struct Library {
    _lib: libloading::Library,
    f: Fns,
}

impl Library {
    pub fn load(dll: &Path) -> Result<Library, Error> {
        // SAFETY: loading the engine DLL runs its start-up code; it is our own DLL, and its functions are declared with the exact signatures
        // of native/axplayer/include/axplayer.h.
        unsafe {
            let lib = libloading::Library::new(dll).map_err(|e| Error::Load(format!("{}: {e}", dll.display())))?;
            macro_rules! sym {
                ($name:literal, $ty:ty) => {
                    *lib.get::<$ty>(concat!($name, "\0").as_bytes()).map_err(|e| Error::Load(format!("{}: {e}", $name)))?
                };
            }
            let f = Fns {
                create: sym!("ax_create", CreateFn),
                destroy: sym!("ax_destroy", DestroyFn),
                open: sym!("ax_open", OpenFn),
                close: sym!("ax_close", VoidFn),
                play: sym!("ax_play", CmdFn),
                pause: sym!("ax_pause", CmdFn),
                stop: sym!("ax_stop", CmdFn),
                seek: sym!("ax_seek", SeekFn),
                set_rate: sym!("ax_set_rate", SeekFn),
                set_volume: sym!("ax_set_volume", IntFn),
                set_muted: sym!("ax_set_muted", IntFn),
                set_rect: sym!("ax_set_rect", RectFn),
                set_layer: sym!("ax_set_layer", IntFn),
                diagnostics: sym!("ax_diagnostics", DecoderFn),
                hide: sym!("ax_hide", VoidFn),
                track_count: sym!("ax_audio_track_count", CountFn),
                track_name: sym!("ax_audio_track_name", NameFn),
                select_track: sym!("ax_select_audio_track", IntFn),
                get_state: sym!("ax_get_state", StateFn),
                decoder_name: sym!("ax_decoder_name", DecoderFn),
                last_error: sym!("ax_last_error", ErrorFn),
            };
            Ok(Library { _lib: lib, f })
        }
    }
}

fn wide(s: &str) -> Vec<u16> {
    s.encode_utf16().chain(std::iter::once(0)).collect()
}

fn wide_path(p: &Path) -> Vec<u16> {
    p.as_os_str().encode_wide().chain(std::iter::once(0)).collect()
}

fn text(buf: &[u16]) -> String {
    let n = buf.iter().position(|&c| c == 0).unwrap_or(buf.len());
    String::from_utf16_lossy(&buf[..n])
}

pub struct Player {
    lib: Arc<Library>,
    handle: Handle,
}

// SAFETY: the engine forwards every call to its own worker thread and guards what it shares, so its handle can be used from any thread.
unsafe impl Send for Player {}
unsafe impl Sync for Player {}

impl Player {
    pub fn create(lib: Arc<Library>, lav_dir: &Path, options: Options) -> Result<Player, Error> {
        let opt = AxOptions { hw_accel: options.hw_accel as i32, headless: options.headless as i32, video_layer: options.video_layer as i32 };
        let dir = wide_path(lav_dir);
        let mut handle: Handle = ptr::null_mut();
        // SAFETY: pointers are valid for the call; `handle` receives the new player (even when creating failed, so its message can be read).
        let rc = unsafe { (lib.f.create)(dir.as_ptr(), &opt, &mut handle) };
        let player = Player { lib, handle };
        if rc != 0 || handle.is_null() {
            let message = if handle.is_null() { String::new() } else { player.last_error() };
            return Err(Error::Engine { code: rc, message });   // dropping `player` destroys the handle
        }
        Ok(player)
    }

    fn check(&self, rc: i32) -> Result<(), Error> {
        if rc == 0 { Ok(()) } else { Err(Error::Engine { code: rc, message: self.last_error() }) }
    }

    pub fn open(&self, url: &str, ua: &str, parent: Option<isize>, rect: Rect) -> Result<(), Error> {
        let (url, ua) = (wide(url), wide(ua));
        let parent = parent.unwrap_or(0) as *mut c_void;
        // SAFETY: valid, NUL-terminated buffers for the duration of the call.
        let rc = unsafe { (self.lib.f.open)(self.handle, url.as_ptr(), ua.as_ptr(), parent, rect.x, rect.y, rect.w, rect.h) };
        self.check(rc)
    }
    pub fn close(&self) { unsafe { (self.lib.f.close)(self.handle) } }
    pub fn play(&self) -> Result<(), Error> { self.check(unsafe { (self.lib.f.play)(self.handle) }) }
    pub fn pause(&self) -> Result<(), Error> { self.check(unsafe { (self.lib.f.pause)(self.handle) }) }
    pub fn stop(&self) -> Result<(), Error> { self.check(unsafe { (self.lib.f.stop)(self.handle) }) }
    pub fn seek(&self, seconds: f64) -> Result<(), Error> { self.check(unsafe { (self.lib.f.seek)(self.handle, seconds) }) }
    pub fn set_rate(&self, rate: f64) -> Result<(), Error> { self.check(unsafe { (self.lib.f.set_rate)(self.handle, rate) }) }
    pub fn set_volume(&self, percent: u8) -> Result<(), Error> { self.check(unsafe { (self.lib.f.set_volume)(self.handle, percent as i32) }) }
    pub fn set_muted(&self, muted: bool) -> Result<(), Error> { self.check(unsafe { (self.lib.f.set_muted)(self.handle, muted as i32) }) }
    pub fn set_rect(&self, r: Rect) -> Result<(), Error> { self.check(unsafe { (self.lib.f.set_rect)(self.handle, r.x, r.y, r.w, r.h) }) }
    pub fn set_video_layer(&self, layer: VideoLayer) -> Result<(), Error> { self.check(unsafe { (self.lib.f.set_layer)(self.handle, layer as i32) }) }

    /// Hide the video window now, without waiting for the engine (which may be busy stopping a stream).
    pub fn hide(&self) {
        // SAFETY: the engine posts the request to the window's own thread and does not wait; the handle is valid until Drop.
        unsafe { (self.lib.f.hide)(self.handle) }
    }

    pub fn diagnostics(&self) -> String {
        let mut buf = vec![0u16; 8192];
        // SAFETY: the buffer is 8192 wide characters and the engine is told so.
        let n = unsafe { (self.lib.f.diagnostics)(self.handle, buf.as_mut_ptr(), buf.len() as i32) };
        String::from_utf16_lossy(&buf[..(n.max(0) as usize).min(buf.len())])
    }

    pub fn audio_tracks(&self) -> Result<Vec<AudioTrack>, Error> {
        let count = unsafe { (self.lib.f.track_count)(self.handle) };
        let mut out = Vec::new();
        for i in 0..count.max(0) {
            let mut buf = [0u16; 256];
            // SAFETY: the buffer is 256 wide characters and the engine is told so.
            let rc = unsafe { (self.lib.f.track_name)(self.handle, i, buf.as_mut_ptr(), buf.len() as i32) };
            if rc < 0 {
                return Err(Error::Engine { code: rc, message: self.last_error() });
            }
            out.push(AudioTrack { name: text(&buf), selected: rc == 1 });
        }
        Ok(out)
    }

    pub fn select_audio_track(&self, index: usize) -> Result<(), Error> {
        self.check(unsafe { (self.lib.f.select_track)(self.handle, index as i32) })
    }

    pub fn state(&self) -> State {
        let mut s = AxState::default();
        // SAFETY: `s` has the layout the engine writes.
        unsafe { (self.lib.f.get_state)(self.handle, &mut s) };
        State {
            position: s.position,
            duration: s.duration,
            playing: s.playing != 0,
            ended: s.ended != 0,
            buffering: s.buffering != 0,
            video_width: s.video_width,
            video_height: s.video_height,
            error: s.error,
            opened: s.opened != 0,
            window_visible: s.win_visible != 0,
            window_width: s.win_width,
            window_height: s.win_height,
            video_layer: VideoLayer::from_code(s.layer),
            frames_drawn: s.frames_drawn,
            frames_dropped: s.frames_dropped,
            screen_distinct: s.screen_distinct,
            screen_luma_range: s.screen_luma_range,
        }
    }

    pub fn decoder(&self) -> String {
        let mut buf = [0u16; 128];
        unsafe { (self.lib.f.decoder_name)(self.handle, buf.as_mut_ptr(), buf.len() as i32) };
        text(&buf)
    }

    pub fn last_error(&self) -> String {
        // SAFETY: the engine returns a NUL-terminated string valid until the next call on this thread; it is copied at once.
        unsafe {
            let p = (self.lib.f.last_error)(self.handle);
            if p.is_null() { String::new() } else { CStr::from_ptr(p).to_string_lossy().into_owned() }
        }
    }
}

impl Drop for Player {
    fn drop(&mut self) {
        if !self.handle.is_null() {
            // SAFETY: the handle came from ax_create and is destroyed exactly once.
            unsafe { (self.lib.f.destroy)(self.handle) };
        }
    }
}
