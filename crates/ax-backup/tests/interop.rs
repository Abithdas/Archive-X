//! Backups written by Python's `create_backup` (committed in `tests/fixtures`, made by `tools/parity/gen_backup_fixtures.py`) must be read
//! by this crate exactly as Python reads them; backups made here must be read by Python (`tools/parity/check_rust_backup.py` in CI, fed by the
//! files this test writes when `AX_BACKUP_INTEROP_OUT` is set).

use std::collections::BTreeMap;
use std::fs;
use std::path::{Path, PathBuf};

use ax_backup::{apply_retention, create_backup, inspect_backup, list_backups, stage_restore, Env};
use ax_core::vault::{derive_key, seal_with, unseal_with};
use serde_json::{json, Value};

fn expected() -> Value {
    serde_json::from_str(include_str!("fixtures/expected.json")).unwrap()
}

fn fixtures() -> PathBuf {
    Path::new(env!("CARGO_MANIFEST_DIR")).join("tests").join("fixtures")
}

fn with_env<T>(f: impl FnOnce(&Env) -> T) -> T {
    let exp = expected();
    let key = derive_key(exp["secret"].as_str().unwrap().as_bytes());
    let unseal = move |data: &[u8], purpose: &str| unseal_with(&key, data, purpose).map(|_| ()).map_err(|e| e.to_string());
    let now = || "2031-03-08T05:26:07+00:00".to_string();
    let stamp = || "20310308_052607".to_string();
    f(&Env { app_version: "test", data_manifest_version: 1, now_utc: &now, file_stamp: &stamp, unseal: &unseal })
}

fn summary(r: Result<Value, ax_backup::BackupError>) -> Value {
    match r {
        Ok(info) => json!({"ok": true, "encrypted": info["encrypted"], "type": info["manifest"]["backup_type"], "files": info["manifest"]["files"].as_array().unwrap().len()}),
        Err(e) => json!({"error": e.0}),
    }
}

#[test]
fn python_backups_are_inspected_with_python_s_answers() {
    let exp = expected();
    let password = exp["password"].as_str().unwrap();
    with_env(|env| {
        for (name, want) in exp["inspected"].as_object().unwrap() {
            let pw = (exp["backups"]["encrypted"]["file"] == *name).then_some(password);
            assert_eq!(&summary(inspect_backup(&fixtures().join(name), pw, env)), want, "{name}");
        }
        let enc = fixtures().join(exp["backups"]["encrypted"]["file"].as_str().unwrap());
        assert_eq!(summary(inspect_backup(&enc, None, env)), exp["extra"]["encrypted without a password"]);
        assert_eq!(summary(inspect_backup(&enc, Some("wrong"), env)), exp["extra"]["encrypted with a wrong password"]);
        assert_eq!(inspect_backup(&fixtures().join("missing.axbackup"), None, env).unwrap_err().0, "Backup file does not exist");
    });
}

#[test]
fn the_list_of_a_repository_matches_python_s() {
    let exp = expected();
    with_env(|env| {
        let listed = list_backups(&fixtures(), env);
        let want = exp["listed"].as_object().unwrap();
        assert_eq!(listed.len(), want.len());
        for row in listed {
            let name = row["name"].as_str().unwrap();
            for k in ["type", "encrypted", "verified", "files", "error"] {
                assert_eq!(row.get(k).cloned().unwrap_or(Value::Null), want[name][k], "{name} {k}");
            }
        }
    });
}

#[test]
fn staged_restores_unpack_exactly_the_manifest_files_and_refuse_what_python_refuses() {
    let exp = expected();
    let password = exp["password"].as_str().unwrap();
    with_env(|env| {
        for (label, b) in exp["backups"].as_object().unwrap() {
            let file = fixtures().join(b["file"].as_str().unwrap());
            let sandbox = tempfile::tempdir().unwrap();
            let data = sandbox.path().join("data");
            fs::create_dir_all(&data).unwrap();
            let result = stage_restore(&data, &file, b["password"].as_str().filter(|_| label == "encrypted").or(None).map(|_| password), env);
            if label == "media" {
                assert_eq!(result.unwrap_err().0, exp["media_refused"].as_str().unwrap());
                continue;
            }
            let info = result.unwrap_or_else(|e| panic!("{label}: {e}"));
            let stage = PathBuf::from(info["staging_path"].as_str().unwrap());
            let mut found: BTreeMap<String, String> = BTreeMap::new();
            fn walk(dir: &Path, base: &Path, out: &mut BTreeMap<String, String>) {
                for e in fs::read_dir(dir).unwrap() {
                    let p = e.unwrap().path();
                    if p.is_dir() {
                        walk(&p, base, out);
                    } else {
                        out.insert(p.strip_prefix(base).unwrap().to_string_lossy().replace('\\', "/"), ax_backup::sha256_file(&p).unwrap());
                    }
                }
            }
            walk(&stage, &stage, &mut found);
            found.remove("BACKUP_MANIFEST.json");
            let want: BTreeMap<String, String> = b["files"].as_object().unwrap().iter().map(|(k, v)| (k.clone(), v.as_str().unwrap().to_string())).collect();
            assert_eq!(found, want, "{label}");
            // the stale WAL and the cache are not there; the marker is
            assert!(!stage.join("database").join("archive.db-wal").exists());
            let marker: Value = serde_json::from_str(&fs::read_to_string(sandbox.path().join(".archive_x_pending_restore.json")).unwrap()).unwrap();
            assert_eq!(marker["staging_path"], info["staging_path"]);
            assert_eq!(marker["version"], 1);
            // the database is the snapshot and reads
            let conn = ax_db::rusqlite::Connection::open(stage.join("database").join("archive.db")).unwrap();
            let n: i64 = conn.query_row("SELECT COUNT(*) FROM notes", [], |r| r.get(0)).unwrap();
            assert_eq!(n, 2);
        }
    });
}

/// A data folder like the one the Python fixtures were made from.
fn data_folder(root: &Path, secret: &str) -> PathBuf {
    let data = root.join("data");
    for d in ["database", "state", "assets/cache", "logs", "auth", "cache"] {
        fs::create_dir_all(data.join(d)).unwrap();
    }
    let conn = ax_db::rusqlite::Connection::open(data.join("database").join("archive.db")).unwrap();
    conn.execute_batch("CREATE TABLE notes(id INTEGER PRIMARY KEY, body TEXT); INSERT INTO notes(body) VALUES ('alpha'),('beta é 日本');")
        .unwrap();
    drop(conn);
    fs::write(data.join("database").join("archive.db-wal"), b"stale wal frames").unwrap();
    fs::write(data.join("settings.json"), "{\"theme\": \"dark\"}\n").unwrap();
    fs::write(data.join("PERSISTENT_DATA_MANIFEST.json"), "{\"manifest_version\": 1}\n").unwrap();
    fs::write(data.join("state").join("last.json"), "{\"a\": 1}").unwrap();
    fs::write(data.join("assets").join("pic.bin"), (0..=255u8).cycle().take(256 * 40).collect::<Vec<_>>()).unwrap();
    fs::write(data.join("logs").join("app.log"), "line one\nline two\n").unwrap();
    let key = derive_key(secret.as_bytes());
    fs::write(data.join("auth").join("credentials.json"), seal_with(&key, br#"{"api_id": 1}"#, "archive-x-credentials-v1")).unwrap();
    fs::write(data.join("cache").join("big.bin"), vec![b'x'; 1000]).unwrap();
    fs::write(data.join("assets").join("cache").join("skipped.bin"), b"y").unwrap();
    data
}

#[test]
fn backups_made_here_verify_and_hold_what_python_s_hold() {
    let exp = expected();
    let password = exp["password"].as_str().unwrap();
    let sandbox = tempfile::tempdir().unwrap();
    let data = data_folder(sandbox.path(), exp["secret"].as_str().unwrap());
    let repo = sandbox.path().join("repo");
    let out_dir = std::env::var_os("AX_BACKUP_INTEROP_OUT").map(PathBuf::from);
    with_env(|env| {
        for (label, kind, pw) in [("full", "full_application", None), ("config", "configuration", None), ("encrypted", "full_application", Some(password))] {
            let stamp = label.to_string();
            let stamp_fn = move || stamp.clone();
            let env2 = Env { app_version: env.app_version, data_manifest_version: env.data_manifest_version, now_utc: env.now_utc, file_stamp: &stamp_fn, unseal: env.unseal };
            let made = create_backup(&data, &repo, kind, pw, &env2).unwrap_or_else(|e| panic!("{label}: {e}"));
            assert_eq!(made["verified"], true);
            let got: BTreeMap<String, String> = made["manifest"]["files"]
                .as_array()
                .unwrap()
                .iter()
                .map(|f| (f["path"].as_str().unwrap().to_string(), f["sha256"].as_str().unwrap().to_string()))
                .collect();
            let want = exp["backups"][label]["files"].as_object().unwrap();
            assert_eq!(
                got.keys().collect::<Vec<_>>(),
                want.keys().collect::<Vec<_>>().into_iter().collect::<std::collections::BTreeSet<_>>().into_iter().collect::<Vec<_>>(),
                "{label}: the same files as Python's"
            );
            // files other than the database (rewritten by the SQLite backup) and the sealed credentials (random nonce) hash alike
            for (path, hash) in &got {
                if path != "database/archive.db" && ax_core::vault::protected_purpose(path).is_none() {
                    assert_eq!(hash, want[path].as_str().unwrap(), "{label}: {path}");
                }
            }
            let file = PathBuf::from(made["path"].as_str().unwrap());
            assert_eq!(inspect_backup(&file, pw, env).unwrap()["encrypted"], pw.is_some());
            if let Some(out) = &out_dir {
                fs::create_dir_all(out).unwrap();
                fs::copy(&file, out.join(file.file_name().unwrap())).unwrap();
            }
        }
        assert!(repo.read_dir().unwrap().all(|e| !e.unwrap().file_name().to_string_lossy().ends_with(".tmp")), "no temporary file is left");
        // refusals
        let inside = data.join("backups");
        assert_eq!(create_backup(&data, &inside, "full_application", None, env).unwrap_err().0, "Backup repository cannot be inside Archive X data");
        assert_eq!(create_backup(&data, &repo, "other", None, env).unwrap_err().0, "Unsupported backup type");
        // an unsealed credentials file is refused
        fs::write(data.join("auth").join("credentials.json"), b"plain").unwrap();
        let e = create_backup(&data, &repo, "full_application", None, env).unwrap_err().0;
        assert!(e.starts_with("Backup refused an unencrypted credentials/session file."), "{e}");
        assert!(repo.read_dir().unwrap().all(|e| !e.unwrap().file_name().to_string_lossy().ends_with(".tmp")), "the failed attempt cleaned up");
    });
}

#[test]
fn retention_keeps_the_same_files_as_python() {
    let exp = expected();
    // UTC calendar parts (Python ran with TZ=UTC): iso year and week, year, month
    let parts = |ts: f64| {
        let days = (ts as i64).div_euclid(86_400);
        let (y, m, _d) = civil(days);
        let wd = (days + 3).rem_euclid(7); // Monday = 0 (1970-01-01 was a Thursday)
        let thursday = days - wd + 3;
        let (ty, _, _) = civil(thursday);
        let jan1 = days_from_civil(ty, 1, 1);
        (ty, (thursday - jan1) / 7 + 1, y, m)
    };
    let cases = exp["retention"].as_array().unwrap();
    assert!(cases.len() >= 30);
    for case in cases {
        let dir = tempfile::tempdir().unwrap();
        for (i, t) in case["times"].as_array().unwrap().iter().enumerate() {
            let p = dir.path().join(format!("b{i:03}.axbackup"));
            fs::write(&p, b"x").unwrap();
            let when = std::time::UNIX_EPOCH + std::time::Duration::from_secs(t.as_u64().unwrap());
            fs::File::options().write(true).open(&p).unwrap().set_modified(when).unwrap();
        }
        let n = |k: &str, d: i64| ax_backup::retention_count(case[k].as_i64().unwrap(), d);
        let r = apply_retention(dir.path(), n("daily", 7), n("weekly", 4), n("monthly", 3), &parts);
        let mut kept: Vec<i64> = fs::read_dir(dir.path()).unwrap().map(|e| e.unwrap().file_name().to_string_lossy()[1..4].parse().unwrap()).collect();
        kept.sort();
        let want: Vec<i64> = case["kept"].as_array().unwrap().iter().map(|v| v.as_i64().unwrap()).collect();
        assert_eq!(kept, want, "{} {}/{}/{}", case["case"], case["daily"], case["weekly"], case["monthly"]);
        assert_eq!(r, case["result"], "{}", case["case"]);
    }
}

fn civil(days: i64) -> (i64, i64, i64) {
    let z = days + 719_468;
    let era = z.div_euclid(146_097);
    let doe = z.rem_euclid(146_097);
    let yoe = (doe - doe / 1_460 + doe / 36_524 - doe / 146_096) / 365;
    let y = yoe + era * 400;
    let doy = doe - (365 * yoe + yoe / 4 - yoe / 100);
    let mp = (5 * doy + 2) / 153;
    let d = doy - (153 * mp + 2) / 5 + 1;
    let m = if mp < 10 { mp + 3 } else { mp - 9 };
    (if m <= 2 { y + 1 } else { y }, m, d)
}

fn days_from_civil(y: i64, m: i64, d: i64) -> i64 {
    let y = if m <= 2 { y - 1 } else { y };
    let era = y.div_euclid(400);
    let yoe = y - era * 400;
    let doy = (153 * (if m > 2 { m - 3 } else { m + 9 }) + 2) / 5 + d - 1;
    let doe = yoe * 365 + yoe / 4 - yoe / 100 + doy;
    era * 146_097 + doe - 719_468
}
