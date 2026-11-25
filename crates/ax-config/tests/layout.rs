//! Replays `layout_cases.json`, recorded from the real Python `config.py` and `main.py` by `tools/parity/gen_layout_vectors.py`.
//! Each case lays out files in a temporary folder, starts up, runs its operations and compares the answers and the whole file tree.
//! The recording is of paths with `/`, so this runs on Unix only.
#![cfg(unix)]

use ax_config::startup::{remove_obsolete_files, start, write_default_config};
use ax_config::{desktop, location, secrets::SecretsStore, settings::Settings, DataFolder, Env};
use ax_core::vault::Vault;
use regex::Regex;
use serde_json::{json, Map, Value};
use sha2::{Digest, Sha256};
use std::fs;
use std::path::{Path, PathBuf};

fn hex(bytes: &[u8]) -> String {
    bytes.iter().map(|b| format!("{b:02x}")).collect()
}

fn build(base: &Path, files: &Map<String, Value>) {
    let root = base.display().to_string();
    for (rel, what) in files {
        let path = base.join(rel);
        let sub = |s: &str| s.replace("{root}", &root);
        if let Some(obj) = what.as_object() {
            if let Some(target) = obj.get("symlink").and_then(Value::as_str) {
                fs::create_dir_all(path.parent().unwrap()).unwrap();
                std::os::unix::fs::symlink(sub(target), &path).unwrap();
            } else if obj.get("dir").and_then(Value::as_bool) == Some(true) {
                fs::create_dir_all(&path).unwrap();
            } else if let Some(h) = obj.get("hex").and_then(Value::as_str) {
                fs::create_dir_all(path.parent().unwrap()).unwrap();
                let bytes: Vec<u8> = (0..h.len()).step_by(2).map(|i| u8::from_str_radix(&h[i..i + 2], 16).unwrap()).collect();
                fs::write(&path, bytes).unwrap();
            }
        } else {
            fs::create_dir_all(path.parent().unwrap()).unwrap();
            fs::write(&path, sub(what.as_str().unwrap())).unwrap();
        }
    }
}

struct Normalizer {
    root: String,
    time_fields: Regex,
    stamp: Regex,
}

impl Normalizer {
    fn text(&self, s: &str) -> String {
        s.replace(&self.root, "{root}")
    }

    fn value(&self, v: &Value) -> Value {
        match v {
            Value::String(s) => Value::String(self.text(s)),
            Value::Array(a) => Value::Array(a.iter().map(|x| self.value(x)).collect()),
            Value::Object(o) => Value::Object(o.iter().map(|(k, x)| (k.clone(), self.value(x))).collect()),
            other => other.clone(),
        }
    }

    fn snapshot(&self, base: &Path) -> Vec<(String, String, String)> {
        let mut rows = Vec::new();
        self.walk(base, base, &mut rows);
        rows.sort();
        rows
    }

    fn walk(&self, base: &Path, dir: &Path, rows: &mut Vec<(String, String, String)>) {
        for entry in fs::read_dir(dir).unwrap().flatten() {
            let path = entry.path();
            let rel = path.strip_prefix(base).unwrap().to_string_lossy().replace('\\', "/");
            let rel = self.stamp.replace_all(&rel, "PreRestore <ts>").to_string();
            let md = fs::symlink_metadata(&path).unwrap();
            if md.file_type().is_symlink() {
                let target = fs::read_link(&path).unwrap().display().to_string();
                rows.push((rel, "link".into(), self.text(&target)));
            } else if md.is_dir() {
                rows.push((rel, "dir".into(), String::new()));
                self.walk(base, &path, rows);
            } else {
                let raw = fs::read(&path).unwrap();
                if raw.starts_with(b"ARCHIVE-X-AESGCM-1\n") {
                    rows.push((rel, "file".into(), "sealed".into())); // random nonce: the bytes differ every time
                    continue;
                }
                let content = match String::from_utf8(raw.clone()) {
                    Ok(text) => {
                        let text = self.time_fields.replace_all(&self.text(&text), "\"$1\": \"<time>\"").to_string();
                        if text.len() <= 300 {
                            text
                        } else {
                            format!("sha256:{}", hex(&Sha256::digest(text.as_bytes())))
                        }
                    }
                    Err(_) if raw.len() <= 64 => format!("hex:{}", hex(&raw)),
                    Err(_) => format!("sha256:{}", hex(&Sha256::digest(&raw))),
                };
                rows.push((rel, "file".into(), content));
            }
        }
    }
}

fn answer<T>(norm: &Normalizer, r: Result<T, ax_config::ConfigError>, ok: impl FnOnce(T) -> Value) -> Value {
    match r {
        Ok(v) => json!({"ok": norm.value(&ok(v))}),
        Err(e) => json!({"error": [e.kind, norm.text(&e.message)]}),
    }
}

fn candidates(secret: &str) -> Vec<Option<String>> {
    vec![Some(secret.to_string()), Some(String::new()), None, Some(format!("{secret}x")), Some(secret[..secret.len() - 1].to_string())]
}

#[allow(clippy::too_many_arguments)]
fn run_op(op: &Value, env: &Env, folder: &DataFolder, base: &Path, frozen: bool, norm: &Normalizer) -> Value {
    let root = base.display().to_string();
    let arg = |k: &str| op[k].as_str().unwrap().replace("{root}", &root);
    match op["op"].as_str().unwrap() {
        "state" => answer(norm, location::get_data_location_state(env, folder), |s| s.to_json()),
        "request_move" => answer(norm, location::request_data_move(env, folder, &arg("target")), |v| v),
        "cancel" => answer(norm, location::cancel_pending_data_move(env, folder), |v| v),
        "migrate" => answer(norm, ax_config::legacy::migrate_legacy_data(env, folder), |v| json!(v)),
        "paths" => answer(norm, Ok::<_, ax_config::ConfigError>(folder.named_paths(env)), |m| json!(m)),
        "settings" => answer(norm, Settings::from_env(env), |s| {
            let num = |f: f64| if f == f64::INFINITY { json!("inf") } else { json!(f) };
            json!({"HOST": s.host, "PORT": s.port, "LOCAL_PLAYER_PORT": s.local_player_port, "LOCAL_PLAYER_HOST": s.local_player_host,
                   "METADATA_CACHE_TTL_SECONDS": num(s.metadata_cache_ttl_seconds), "TELEGRAM_BACKGROUND_CONNECT_TIMEOUT_SECONDS": num(s.telegram_background_connect_timeout_seconds)})
        }),
        "default_config" => answer(norm, write_default_config(folder), |_| Value::Null),
        "obsolete" => answer(norm, remove_obsolete_files(&base.join("pkg"), frozen), |v| json!(v)),
        "desktop" => {
            let secret = desktop::launch_secret(env);
            let landing = desktop::landing(env);
            let matches: Vec<bool> = candidates(&secret).iter().map(|c| desktop::secret_matches(c.as_deref(), &secret)).collect();
            answer(norm, Ok::<_, ax_config::ConfigError>(()), |_| {
                json!({"landing": landing, "enabled": desktop::enabled(env), "port": desktop::requested_port(env), "handoff": desktop::handoff_page(&landing),
                       "cookie": desktop::cookie_value(&secret), "matches": matches})
            })
        }
        "secrets" => {
            let vault = Vault {
                secret: env.get("ARCHIVE_X_ENCRYPTION_KEY").map(String::from),
                key_dir: location::data_location_file(env).parent().unwrap().to_path_buf(),
                encrypted_user_files: vec![folder.paths.secrets_path.clone()],
            };
            let store = SecretsStore::new(vault, folder.paths.secrets_path.clone());
            let mut after_each = Vec::new();
            for step in op["steps"].as_array().unwrap() {
                if step[0] == "set" {
                    store.set(step[1].as_str().unwrap(), step[2].clone()).unwrap();
                }
                after_each.push(Value::Object(store.load().unwrap()));
            }
            let keys: Vec<&str> = op["keys"].as_array().unwrap().iter().map(|k| k.as_str().unwrap()).collect();
            let is_secret: Vec<bool> = keys.iter().map(|k| ax_config::secrets::is_secret_key(k)).collect();
            let get: Vec<Value> = keys.iter().map(|k| store.get(k, json!("dflt")).unwrap()).collect();
            json!({"ok": {"after_each": after_each, "is_secret": is_secret, "get": get}})
        }
        other => panic!("unknown op {other}"),
    }
}

// Values are compared as text on purpose: objects compare equal whatever their key order, and the order is part of what Python produced.
#[allow(clippy::cmp_owned)]
fn run_case(case: &Value) -> Vec<String> {
    let mut problems = Vec::new();
    let tmp = tempfile::tempdir().unwrap();
    let base: PathBuf = fs::canonicalize(tmp.path()).unwrap();
    fs::create_dir_all(base.join("home")).unwrap();
    fs::create_dir_all(base.join("cwd")).unwrap();
    build(&base, case["files"].as_object().unwrap());
    let root = base.display().to_string();
    let vars = case["env"].as_object().unwrap().iter().map(|(k, v)| (k.clone(), v.as_str().unwrap().replace("{root}", &root)));
    let mut env = Env::new(vars, base.join("home"), base.join("cwd"), base.join("app"));
    env.windows = false;
    let norm = Normalizer {
        root: root.clone(),
        time_fields: Regex::new(r#""(generated_at|updated_at|started_at)": "[^"]*""#).unwrap(),
        stamp: Regex::new(r"PreRestore \d{8}_\d{6}").unwrap(),
    };
    let frozen = case.get("frozen").and_then(Value::as_bool).unwrap_or(false);
    let expect = &case["expect"];
    let started = start(&env, &|_| {});
    let startup = match &started {
        Ok(_) => json!({"ok": null}),
        Err(e) => json!({"error": [e.kind, norm.text(&e.message)]}),
    };
    if startup.to_string() != expect["startup"].to_string() {
        problems.push(format!("start-up: expected {} got {}", expect["startup"], startup));
    }
    if let Ok(started) = &started {
        let ops = case["ops"].as_array().unwrap();
        for (i, op) in ops.iter().enumerate() {
            let got = run_op(op, &env, &started.folder, &base, frozen, &norm);
            if got.to_string() != expect["ops"][i].to_string() {
                problems.push(format!("op {i} {}: expected {} got {}", op["op"], expect["ops"][i], got));
            }
        }
    }
    let tree: Vec<(String, String, String)> = case["tree"]
        .as_array()
        .unwrap()
        .iter()
        .map(|r| (r[0].as_str().unwrap().into(), r[1].as_str().unwrap().into(), r[2].as_str().unwrap().into()))
        .collect();
    let got = norm.snapshot(&base);
    if got != tree {
        let missing: Vec<_> = tree.iter().filter(|r| !got.contains(r)).take(5).collect();
        let extra: Vec<_> = got.iter().filter(|r| !tree.contains(r)).take(5).collect();
        problems.push(format!("file tree differs; expected but not found: {missing:?}; found but not expected: {extra:?}"));
    }
    problems
}

#[test]
fn matches_python() {
    let text = fs::read_to_string(Path::new(env!("CARGO_MANIFEST_DIR")).join("tests/layout_cases.json")).unwrap();
    let cases: Vec<Value> = serde_json::from_str(&text).unwrap();
    assert!(cases.len() > 100);
    let mut failures = Vec::new();
    for case in &cases {
        let problems = run_case(case);
        if !problems.is_empty() {
            failures.push(format!("{}:\n  {}", case["name"], problems.join("\n  ")));
        }
    }
    assert!(failures.is_empty(), "{} of {} cases differ from Python:\n{}", failures.len(), cases.len(), failures.join("\n"));
}
