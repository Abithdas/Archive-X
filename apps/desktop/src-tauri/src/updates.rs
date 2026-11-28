//! In-app updates: look for a newer release in the background, ask, download it, stop the backend and let the installer replace the app.
//!
//! Everything happens in Rust. The question is a native dialog and the download progress is shown in the window title. The state (looking,
//! up to date, a version is ready, downloading with its percent, installing, failed) is also kept here and read by Settings, Overview through
//! three commands (update_status, update_check, update_install: capabilities/page.json), which is also where an update that was
//! answered "Later" is started. A failed check is logged and ignored: with no
//! network, no release yet, or a private repository the app simply carries on. ARCHIVE_X_NO_UPDATE_CHECK=1 turns the check off.
//! Updates are accepted only when signed with the key whose public half is in tauri.conf.json (plugins.updater.pubkey).

use std::sync::{Mutex, OnceLock};
use std::time::Duration;

use serde::Serialize;
use tauri::{AppHandle, Manager};
use tauri_plugin_dialog::{DialogExt, MessageDialogButtons, MessageDialogKind};
use tauri_plugin_updater::UpdaterExt;

const FIRST_CHECK_AFTER: Duration = Duration::from_secs(20);
const CHECK_EVERY: Duration = Duration::from_secs(6 * 60 * 60);

/// Whether to ask about `available`: not when the person already said "Later" to exactly this version in this session.
pub fn should_ask(declined: Option<&str>, available: &str) -> bool {
    declined != Some(available)
}

/// "12.3 MB" / "840 KB" for the dialog and the title.
pub fn human_size(bytes: u64) -> String {
    if bytes >= 1_000_000 {
        format!("{:.1} MB", bytes as f64 / 1_000_000.0)
    } else {
        format!("{} KB", bytes.div_ceil(1000))
    }
}

/// What Overview shows. `phase` is one of: idle, checking, uptodate, available, downloading, installing, error.
#[derive(Clone, Debug, Serialize)]
#[serde(rename_all = "camelCase")]
pub struct UpdateState {
    pub phase: String,
    pub current: String,
    pub version: Option<String>,
    pub notes: Option<String>,
    pub received: u64,
    pub total: u64,
    pub percent: Option<u8>,
    pub error: Option<String>,
    /// Unix seconds of the last finished look for an update.
    pub checked_at: Option<u64>,
}

impl UpdateState {
    fn new(current: &str) -> Self {
        Self { phase: "idle".into(), current: current.into(), version: None, notes: None, received: 0, total: 0, percent: None, error: None, checked_at: None }
    }
}

/// The whole number of percent received, or None while the size is not known.
pub fn percent_of(received: u64, total: u64) -> Option<u8> {
    (total > 0).then(|| (received.saturating_mul(100) / total).min(100) as u8)
}

static STATE: OnceLock<Mutex<UpdateState>> = OnceLock::new();
static PENDING: Mutex<Option<tauri_plugin_updater::Update>> = Mutex::new(None);
static STOPPER: OnceLock<fn(&AppHandle)> = OnceLock::new();

fn state() -> &'static Mutex<UpdateState> {
    STATE.get_or_init(|| Mutex::new(UpdateState::new(env!("CARGO_PKG_VERSION"))))
}

fn change(f: impl FnOnce(&mut UpdateState)) -> UpdateState {
    let mut s = state().lock().unwrap_or_else(|e| e.into_inner());
    f(&mut s);
    s.clone()
}

fn now_secs() -> u64 {
    std::time::SystemTime::now().duration_since(std::time::UNIX_EPOCH).map(|d| d.as_secs()).unwrap_or(0)
}

pub fn spawn_checks(app: AppHandle, stop_backend: fn(&AppHandle)) {
    let _ = STOPPER.set(stop_backend);
    // The version the app was built as: tauri.conf.json, which a release rewrites. Cargo's own package version (0.1.0) was shown as the current version.
    let built = app.package_info().version.to_string();
    change(|s| s.current = built);
    if std::env::var("ARCHIVE_X_NO_UPDATE_CHECK").map(|v| v == "1").unwrap_or(false) {
        return;
    }
    std::thread::spawn(move || {
        std::thread::sleep(FIRST_CHECK_AFTER);
        let mut declined: Option<String> = None;
        loop {
            tauri::async_runtime::block_on(check_once(&app, &mut declined));
            std::thread::sleep(CHECK_EVERY);
        }
    });
}

/// Look for a newer release and record what was found. A failed look is recorded and otherwise ignored.
async fn look(app: &AppHandle) -> UpdateState {
    change(|s| {
        s.phase = "checking".into();
        s.error = None;
    });
    let updater = match app.updater() {
        Ok(u) => u,
        Err(e) => {
            return change(|s| {
                s.phase = "error".into();
                s.error = Some(format!("updates are unavailable: {e}"));
                s.checked_at = Some(now_secs());
            })
        }
    };
    match updater.check().await {
        Ok(Some(u)) => {
            let notes = u.body.clone().filter(|b| !b.trim().is_empty());
            let version = u.version.clone();
            *PENDING.lock().unwrap_or_else(|e| e.into_inner()) = Some(u);
            change(|s| {
                s.phase = "available".into();
                s.version = Some(version);
                s.notes = notes;
                s.error = None;
                s.checked_at = Some(now_secs());
            })
        }
        Ok(None) => {
            *PENDING.lock().unwrap_or_else(|e| e.into_inner()) = None;
            change(|s| {
                s.phase = "uptodate".into();
                s.version = None;
                s.notes = None;
                s.error = None;
                s.checked_at = Some(now_secs());
            })
        }
        Err(e) => {
            eprintln!("updates: check failed (ignored): {e}");
            change(|s| {
                s.phase = "error".into();
                s.error = Some(format!("could not look for an update: {e}"));
                s.checked_at = Some(now_secs());
            })
        }
    }
}

/// Download the version that was found, check its signature, stop the backend and let the installer run. Progress goes to the state and the title.
async fn install(app: &AppHandle) {
    let Some(update) = PENDING.lock().unwrap_or_else(|e| e.into_inner()).clone() else {
        change(|s| {
            s.phase = "error".into();
            s.error = Some("there is no update to install: look for one first".into());
        });
        return;
    };
    if matches!(state().lock().unwrap_or_else(|e| e.into_inner()).phase.as_str(), "downloading" | "installing") {
        return; // already on its way (the dialog and the button can both start it)
    }
    change(|s| {
        s.phase = "downloading".into();
        s.received = 0;
        s.total = 0;
        s.percent = Some(0);
        s.error = None;
    });
    let window = app.get_webview_window("main");
    let say = move |t: &str| {
        if let Some(w) = &window {
            let _ = w.set_title(t);
        }
    };
    let mut received: u64 = 0;
    let stopper = app.clone();
    let stop = STOPPER.get().copied();
    let result = update
        .download_and_install(
            |chunk, total| {
                received += chunk as u64;
                let total = total.unwrap_or(0);
                let pct = percent_of(received, total);
                change(|s| {
                    s.received = received;
                    s.total = total;
                    s.percent = pct;
                });
                match pct {
                    Some(p) => say(&format!("Archive X: downloading update {p}% ({})", human_size(total))),
                    None => say(&format!("Archive X: downloading update ({})", human_size(received))),
                }
            },
            // Downloaded and verified: the installer is about to replace the files, so stop the backend first (it checkpoints jobs).
            || {
                change(|s| {
                    s.phase = "installing".into();
                    s.percent = Some(100);
                });
                say("Archive X: installing the update");
                if let Some(stop) = stop {
                    stop(&stopper)
                }
            },
        )
        .await;
    match result {
        Ok(()) => app.restart(), // Windows exits for the installer before getting here; other systems restart now
        Err(e) => {
            eprintln!("updates: install failed: {e}");
            change(|s| {
                s.phase = "error".into();
                s.error = Some(format!("the update could not be installed: {e}"));
            });
            say("Archive X");
        }
    }
}

async fn check_once(app: &AppHandle, declined: &mut Option<String>) {
    let found = look(app).await;
    let (Some(version), "available") = (found.version.clone(), found.phase.as_str()) else { return };
    if !should_ask(declined.as_deref(), &version) {
        return;
    }
    let notes = found.notes.clone().map(|b| format!("\n\n{}", b.trim())).unwrap_or_default();
    let text = format!(
        "Archive X {} is ready (you have {}).{}\n\nUpdate now? Your library, settings and Telegram login are kept. The app restarts when it is done. (\"Later\" leaves it in Settings, Overview.)",
        version, found.current, notes
    );
    let asker = app.clone();
    let yes = tauri::async_runtime::spawn_blocking(move || {
        asker
            .dialog()
            .message(text)
            .title("Archive X update")
            .kind(MessageDialogKind::Info)
            .buttons(MessageDialogButtons::OkCancelCustom("Update now".into(), "Later".into()))
            .blocking_show()
    })
    .await
    .unwrap_or(false);
    if !yes {
        *declined = Some(version);
        return;
    }
    install(app).await;
}

/// What the update is doing now.
#[tauri::command]
pub async fn update_status() -> UpdateState {
    change(|_| {})
}

/// Look for an update now.
#[tauri::command]
pub async fn update_check(app: AppHandle) -> UpdateState {
    if matches!(state().lock().unwrap_or_else(|e| e.into_inner()).phase.as_str(), "downloading" | "installing") {
        return change(|_| {});
    }
    look(&app).await
}

/// Start downloading and installing the version that was found. Returns at once; progress is read with update_status.
#[tauri::command]
pub async fn update_install(app: AppHandle) -> UpdateState {
    let bg = app.clone();
    tauri::async_runtime::spawn(async move { install(&bg).await });
    change(|_| {})
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn asks_again_only_for_a_different_version() {
        assert!(should_ask(None, "30.1.0"));
        assert!(!should_ask(Some("30.1.0"), "30.1.0"));
        assert!(should_ask(Some("30.1.0"), "30.2.0"));
    }

    #[test]
    fn percent_is_whole_and_never_past_100() {
        assert_eq!(percent_of(0, 0), None);
        assert_eq!(percent_of(5, 0), None);
        assert_eq!(percent_of(1, 3), Some(33));
        assert_eq!(percent_of(3, 3), Some(100));
        assert_eq!(percent_of(9, 3), Some(100));
        assert_eq!(percent_of(u64::MAX, 2), Some(100));
    }

    #[test]
    fn the_state_goes_to_the_page_in_camel_case() {
        let mut s = UpdateState::new("30.4.3");
        s.phase = "downloading".into();
        s.percent = percent_of(50, 200);
        s.checked_at = Some(7);
        let v = serde_json::to_value(&s).unwrap();
        assert_eq!(v["phase"], "downloading");
        assert_eq!(v["percent"], 25);
        assert_eq!(v["checkedAt"], 7);
        assert_eq!(v["current"], "30.4.3");
        assert!(v["version"].is_null());
    }

    #[test]
    fn sizes_read_naturally() {
        assert_eq!(human_size(0), "0 KB");
        assert_eq!(human_size(1), "1 KB");
        assert_eq!(human_size(840_001), "841 KB");
        assert_eq!(human_size(12_345_678), "12.3 MB");
    }
}
