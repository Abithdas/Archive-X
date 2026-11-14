//! Archive X desktop shell.
//!
//! Flow: show the splash page → start the frozen backend (desktop mode, loopback, per-launch secret) → open the
//! one-time session URL in the same window → on exit, stop the backend gracefully (jobs are checkpointed).
//! The Archive X web UI loaded from the backend gets no Tauri IPC access (see capabilities/default.json).

use std::path::PathBuf;
use std::sync::Mutex;
use std::time::Duration;

use archive_x_launcher_core::{backend_exe_in, tools_dir_in, Backend, FrontDoorOptions, LaunchOptions};
use tauri::{Manager, RunEvent, Url};
use tauri_plugin_dialog::{DialogExt, MessageDialogKind};

struct BackendState(Mutex<Option<Backend>>);

/// Backend executable: ARCHIVE_X_BACKEND_EXE (development), otherwise the bundled one in the resource directory.
fn locate_backend(app: &tauri::AppHandle) -> Result<(PathBuf, Option<PathBuf>), String> {
    if let Ok(exe) = std::env::var("ARCHIVE_X_BACKEND_EXE") {
        return Ok((PathBuf::from(exe), std::env::var("ARCHIVE_X_TOOLS_DIR").ok().map(PathBuf::from)));
    }
    let res = app.path().resource_dir().map_err(|e| format!("no resource directory: {e}"))?;
    let exe = backend_exe_in(&res);
    if !exe.is_file() {
        return Err(format!("the bundled backend is missing: {}", exe.display()));
    }
    let tools = tools_dir_in(&res);
    Ok((exe, tools.is_dir().then_some(tools)))
}

/// On by default (ARCHIVE_X_RUST_FRONT_DOOR=0 turns it off): the Rust front door in front of the backend. It needs the `ax-server` binary
/// (ARCHIVE_X_FRONT_DOOR_EXE, or `rust/ax-server` in the resource directory) and the built web app (ARCHIVE_X_DIST, or
/// `web` in the resource directory). When anything is missing the app simply starts without it.
fn locate_front_door(app: &tauri::AppHandle) -> Option<FrontDoorOptions> {
    // On by default since the Windows build proved it end to end; ARCHIVE_X_RUST_FRONT_DOOR=0 turns it off.
    if std::env::var("ARCHIVE_X_RUST_FRONT_DOOR").map(|v| v == "0").unwrap_or(false) {
        return None;
    }
    let res = app.path().resource_dir().ok();
    let exe = std::env::var("ARCHIVE_X_FRONT_DOOR_EXE").ok().map(PathBuf::from).or_else(|| {
        let name = if cfg!(windows) { "ax-server.exe" } else { "ax-server" };
        res.as_ref().map(|r| r.join("rust").join(name))
    })?;
    let dist = std::env::var("ARCHIVE_X_DIST").ok().map(PathBuf::from).or_else(|| res.as_ref().map(|r| r.join("web")))?;
    (exe.is_file() && dist.join("index.html").is_file()).then_some(FrontDoorOptions { exe, dist_dir: dist })
}

fn fail(app: &tauri::AppHandle, message: String) {
    app.dialog()
        .message(message)
        .title("Archive X could not start")
        .kind(MessageDialogKind::Error)
        .blocking_show();
    app.exit(1);
}

fn start_backend(app: tauri::AppHandle) {
    let (exe, tools_dir) = match locate_backend(&app) {
        Ok(v) => v,
        Err(e) => return fail(&app, e),
    };
    let data_dir = std::env::var("ARCHIVE_X_DATA_DIR").ok().map(PathBuf::from);
    // ARCHIVE_X_LANDING overrides the page the window opens; the default is the React UI at "/".
    let landing = std::env::var("ARCHIVE_X_LANDING").ok();
    let front_door = locate_front_door(&app);
    let backend = match Backend::spawn(&LaunchOptions { exe, data_dir, tools_dir, landing, front_door, ..Default::default() }) {
        Ok(b) => b,
        Err(e) => return fail(&app, e.to_string()),
    };
    let url = backend.session_url();
    if let Some(window) = app.get_webview_window("main") {
        match Url::parse(&url) {
            Ok(u) => {
                let _ = window.navigate(u);
            }
            Err(e) => return fail(&app, format!("invalid session URL: {e}")),
        }
    }
    {
        let state = app.state::<BackendState>();
        *state.0.lock().unwrap() = Some(backend);
    }

    // Watch the backend: if it dies while the app is open, say so instead of showing a dead page.
    loop {
        std::thread::sleep(Duration::from_secs(2));
        let state = app.state::<BackendState>();
        let mut guard = state.0.lock().unwrap();
        let died: Option<String> = match guard.as_mut() {
            None => return, // shut down by the app
            Some(b) => {
                if b.is_running() {
                    None
                } else {
                    Some(b.log_tail().join("\n"))
                }
            }
        };
        drop(guard);
        if let Some(tail) = died {
            return fail(&app, format!("The Archive X backend stopped unexpectedly.\n\n{tail}"));
        }
    }
}

fn stop_backend(app: &tauri::AppHandle) {
    let state = app.state::<BackendState>();
    let taken = state.0.lock().unwrap().take();
    if let Some(mut backend) = taken {
        backend.shutdown(Duration::from_secs(25));
    }
}

pub fn run() {
    let app = tauri::Builder::default()
        .plugin(tauri_plugin_single_instance::init(|app, _args, _cwd| {
            if let Some(w) = app.get_webview_window("main") {
                let _ = w.unminimize();
                let _ = w.show();
                let _ = w.set_focus();
            }
        }))
        .plugin(tauri_plugin_window_state::Builder::default().build())
        .plugin(tauri_plugin_dialog::init())
        .manage(BackendState(Mutex::new(None)))
        .setup(|app| {
            let handle = app.handle().clone();
            std::thread::spawn(move || start_backend(handle));
            Ok(())
        })
        .build(tauri::generate_context!())
        .expect("error while building the Archive X desktop app");

    app.run(|handle, event| {
        if let RunEvent::ExitRequested { .. } = event {
            stop_backend(handle);
        }
    });
}
