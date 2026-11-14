//! Plays real videos through axplayer.dll with the Rust wrapper. Windows only; needs the built DLL, the LAV folder and a test video:
//!   AXPLAYER_DLL=...\axplayer.dll AXPLAYER_LAV=...\lav AXPLAYER_URL=http://127.0.0.1:8777/clip.mp4 cargo test -p archive-x-native-player -- --ignored
#![cfg(windows)]

use std::path::PathBuf;
use std::time::{Duration, Instant};

use archive_x_native_player::{Engine, Error, HwAccel, Options, Player, Rect, VideoLayer};

fn env(name: &str) -> PathBuf {
    PathBuf::from(std::env::var(name).unwrap_or_else(|_| panic!("set {name}")))
}

fn wait_for(p: &Player, secs: u64, f: impl Fn(&archive_x_native_player::State) -> bool) -> bool {
    let end = Instant::now() + Duration::from_secs(secs);
    while Instant::now() < end {
        if f(&p.state()) {
            return true;
        }
        std::thread::sleep(Duration::from_millis(50));
    }
    f(&p.state())
}

fn headless() -> Options {
    Options { hw_accel: HwAccel::None, headless: true, video_layer: VideoLayer::Front }
}

#[test]
#[ignore]
fn plays_pauses_seeks_and_ends() {
    let engine = Engine::load(&env("AXPLAYER_DLL")).expect("load the DLL");
    let player = engine.create_player(&env("AXPLAYER_LAV"), headless()).expect("create the player");
    let url = std::env::var("AXPLAYER_URL").expect("set AXPLAYER_URL");
    player.open(&url, "axplayer-rust-test", None, Rect { x: 0, y: 0, w: 640, h: 360 }).expect("open");

    let s = player.state();
    assert!(s.opened);
    assert!((s.duration - 8.0).abs() < 0.6, "duration {}", s.duration);
    assert_eq!((s.video_width, s.video_height), (640, 360));
    assert!(!player.decoder().is_empty(), "the decoder is named");

    player.play().unwrap();
    assert!(wait_for(&player, 20, |s| s.position >= 1.5), "position advances");
    player.pause().unwrap();
    std::thread::sleep(Duration::from_millis(400));
    let held = player.state().position;
    std::thread::sleep(Duration::from_millis(1000));
    assert!(player.state().position - held < 0.3, "holds while paused");

    let tracks = player.audio_tracks().unwrap();
    assert!(!tracks.is_empty(), "the test video has sound");
    player.select_audio_track(0).unwrap();
    player.set_volume(50).unwrap();
    player.set_muted(true).unwrap();
    player.set_muted(false).unwrap();

    player.seek(4.4).unwrap();
    player.play().unwrap();
    assert!(wait_for(&player, 20, |s| s.position >= 4.9), "continues from the new place");
    assert!(wait_for(&player, 40, |s| s.ended), "the end is reported");
    assert_eq!(player.state().error, 0);

    player.close();
    assert!(!player.state().opened);
}

#[test]
#[ignore]
fn a_missing_filter_folder_is_a_clear_error() {
    let engine = Engine::load(&env("AXPLAYER_DLL")).expect("load the DLL");
    let err = match engine.create_player(&std::env::temp_dir().join("no-such-lav-folder"), headless()) {
        Ok(_) => panic!("expected an error"),
        Err(e) => e,
    };
    match err {
        Error::Engine { message, .. } => assert!(message.contains("LAVSplitter.ax"), "message: {message}"),
        other => panic!("unexpected error: {other}"),
    }
}

#[test]
#[ignore]
fn an_address_that_does_not_answer_fails_with_a_reason() {
    let engine = Engine::load(&env("AXPLAYER_DLL")).expect("load the DLL");
    let player = engine.create_player(&env("AXPLAYER_LAV"), headless()).expect("create the player");
    let err = player.open("http://127.0.0.1:9/nothing.mp4", "axplayer-rust-test", None, Rect::default()).expect_err("nothing listens there");
    assert!(err.to_string().contains("failed"), "{err}");
    assert!(!player.state().opened);
}

#[test]
fn loading_a_dll_that_is_not_there_says_so() {
    match Engine::load(&std::env::temp_dir().join("no-such-axplayer.dll")) {
        Err(Error::Load(m)) => assert!(m.contains("no-such-axplayer.dll"), "{m}"),
        Ok(_) => panic!("loaded a DLL that does not exist"),
        Err(other) => panic!("unexpected: {other}"),
    }
}
