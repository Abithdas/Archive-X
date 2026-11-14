//! In-app updates: look for a newer release in the background, ask, download it, stop the backend and let the installer replace the app.
//!
//! Everything happens in Rust. The web UI loaded from the local backend gets no Tauri access (see capabilities/default.json), so the
//! question is a native dialog and the download progress is shown in the window title. A failed check is logged and ignored: with no
//! network, no release yet, or a private repository the app simply carries on. ARCHIVE_X_NO_UPDATE_CHECK=1 turns the check off.
//! Updates are accepted only when signed with the key whose public half is in tauri.conf.json (plugins.updater.pubkey).

use std::time::Duration;

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

pub fn spawn_checks(app: AppHandle, stop_backend: fn(&AppHandle)) {
    if std::env::var("ARCHIVE_X_NO_UPDATE_CHECK").map(|v| v == "1").unwrap_or(false) {
        return;
    }
    std::thread::spawn(move || {
        std::thread::sleep(FIRST_CHECK_AFTER);
        let mut declined: Option<String> = None;
        loop {
            tauri::async_runtime::block_on(check_once(&app, &mut declined, stop_backend));
            std::thread::sleep(CHECK_EVERY);
        }
    });
}

async fn check_once(app: &AppHandle, declined: &mut Option<String>, stop_backend: fn(&AppHandle)) {
    let updater = match app.updater() {
        Ok(u) => u,
        Err(e) => return eprintln!("updates: unavailable: {e}"),
    };
    let update = match updater.check().await {
        Ok(Some(u)) => u,
        Ok(None) => return,
        Err(e) => return eprintln!("updates: check failed (ignored): {e}"),
    };
    if !should_ask(declined.as_deref(), &update.version) {
        return;
    }
    let notes = update.body.clone().filter(|b| !b.trim().is_empty()).map(|b| format!("\n\n{}", b.trim())).unwrap_or_default();
    let text = format!(
        "Archive X {} is ready (you have {}).{}\n\nUpdate now? Your library, settings and Telegram login are kept. The app restarts when it is done.",
        update.version, update.current_version, notes
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
        *declined = Some(update.version.clone());
        return;
    }

    let window = app.get_webview_window("main");
    let say = move |t: &str| {
        if let Some(w) = &window {
            let _ = w.set_title(t);
        }
    };
    let mut received: u64 = 0;
    let stopper = app.clone();
    let result = update
        .download_and_install(
            |chunk, total| {
                received += chunk as u64;
                match total {
                    Some(t) if t > 0 => say(&format!("Archive X: downloading update {}% ({})", (received * 100 / t).min(100), human_size(t))),
                    _ => say(&format!("Archive X: downloading update ({})", human_size(received))),
                }
            },
            // Downloaded and verified: the installer is about to replace the files, so stop the backend first (it checkpoints jobs).
            || stop_backend(&stopper),
        )
        .await;
    match result {
        Ok(()) => app.restart(), // Windows exits for the installer before getting here; other systems restart now
        Err(e) => {
            eprintln!("updates: install failed: {e}");
            let shown = app.clone();
            let _ = tauri::async_runtime::spawn_blocking(move || {
                shown
                    .dialog()
                    .message(format!("The update could not be installed:\n\n{e}\n\nArchive X keeps working as before. You can try again later."))
                    .title("Archive X update")
                    .kind(MessageDialogKind::Error)
                    .blocking_show()
            })
            .await;
        }
    }
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
    fn sizes_read_naturally() {
        assert_eq!(human_size(0), "0 KB");
        assert_eq!(human_size(1), "1 KB");
        assert_eq!(human_size(840_001), "841 KB");
        assert_eq!(human_size(12_345_678), "12.3 MB");
    }
}
