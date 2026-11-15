//! A local copy of the performer names that StashDB and ThePornDB know.
//!
//! Typing a name in "Who is this?" used to call the providers' live search on every keystroke, and their ordering (and their need for
//! whole words) decided what showed. This keeps id, name, aliases and a picture link for every performer on this PC, refreshed in the
//! background, so the search is instant, forgiving (part of a name, any word order, aliases, accents) and works offline. Only names are
//! stored; a performer's details and picture are fetched when somebody is picked.
//!
//! StashDB is refreshed incrementally (only what changed since the last run). ThePornDB has no "changed since", so it is re-read in full
//! about once a week. Both resume at the page where they stopped if the app is closed in the middle.
//!
//! This crate owns its own SQLite file; the Python database is never written from here.

use std::path::Path;
use std::sync::Mutex;
use std::time::{Duration, SystemTime, UNIX_EPOCH};

use rusqlite::{params, Connection};
use serde_json::{json, Value};
use unicode_normalization::UnicodeNormalization;

pub const STASH_PAGE: usize = 40; // StashDB allows at most 40 per page
pub const TPDB_PAGE: usize = 100;
pub const TPDB_FULL_EVERY_DAYS: f64 = 7.0;
const MAX_PAGES_PER_RUN: usize = 20_000;

const STASH_URL: &str = "https://stashdb.org/graphql";
const TPDB_URL: &str = "https://api.theporndb.net";
const STASH_QUERY: &str = "query($input: PerformerQueryInput!) { queryPerformers(input: $input) { count performers { id name disambiguation aliases gender images { url width height } deleted merged_into_id updated } } }";

/// Lower case, accents and punctuation gone, single spaces: "Zoë  O'Neil" becomes "zoe o neil".
pub fn spaced(value: &str) -> String {
    let folded: String = value.nfkd().filter(|c| c.is_ascii()).collect::<String>().to_lowercase();
    let mut out = String::with_capacity(folded.len());
    let mut gap = true;
    for c in folded.chars() {
        if c.is_ascii_alphanumeric() {
            out.push(c);
            gap = false;
        } else if !gap {
            out.push(' ');
            gap = true;
        }
    }
    out.trim_end().to_string()
}

fn now_secs() -> f64 {
    SystemTime::now().duration_since(UNIX_EPOCH).map(|d| d.as_secs_f64()).unwrap_or(0.0)
}

/// The keys the providers need; either may be missing.
#[derive(Clone, Debug, Default, PartialEq, Eq)]
pub struct Keys {
    pub stashdb: Option<String>,
    pub tpdb: Option<String>,
}

/// How the providers are reached. The real one uses HTTPS; tests use a fake.
pub trait Fetch: Send + Sync {
    fn stashdb(&self, key: &str, query: &str, variables: &Value) -> Result<Value, String>;
    fn tpdb(&self, token: &str, path: &str) -> Result<Value, String>;
}

pub struct Http {
    agent: ureq::Agent,
}

impl Default for Http {
    fn default() -> Self {
        Http { agent: ureq::AgentBuilder::new().timeout(Duration::from_secs(30)).user_agent("Archive-X").build() }
    }
}

fn describe(e: ureq::Error) -> String {
    match e {
        ureq::Error::Status(code, _) => format!("HTTP {code}"),
        other => other.to_string(),
    }
}

impl Fetch for Http {
    fn stashdb(&self, key: &str, query: &str, variables: &Value) -> Result<Value, String> {
        let body = json!({ "query": query, "variables": variables });
        let resp = self.agent.post(STASH_URL).set("APIKey", key).send_json(body).map_err(describe)?;
        resp.into_json::<Value>().map_err(|e| e.to_string())
    }

    fn tpdb(&self, token: &str, path: &str) -> Result<Value, String> {
        let url = format!("{TPDB_URL}/{}", path.trim_start_matches('/'));
        let resp = self.agent.get(&url).set("Authorization", &format!("Bearer {token}")).set("Accept", "application/json").call().map_err(describe)?;
        resp.into_json::<Value>().map_err(|e| e.to_string())
    }
}

/// One performer as the search returns it.
#[derive(Debug, Clone, PartialEq, Eq)]
pub struct Hit {
    pub provider: String,
    pub provider_id: String,
    pub name: String,
    pub aliases: Vec<String>,
    pub image: String,
    pub profile: String,
}

impl Hit {
    pub fn to_json(&self) -> Value {
        json!({ "provider": self.provider, "provider_id": self.provider_id, "name": self.name, "image": self.image, "profile": self.profile, "aliases": self.aliases })
    }
}

#[derive(Debug, Clone, Default)]
struct SyncState {
    status: String,
    run_cursor: String,
    run_page: i64,
    cursor: String,
    last_started: f64,
    last_finished: f64,
    last_full: f64,
    last_error: String,
}

/// The stored names. One connection behind a mutex: searches are short, and a refresh only holds it for a page at a time.
pub struct Store {
    conn: Mutex<Connection>,
}

#[derive(Debug, PartialEq, Eq)]
pub enum Outcome {
    Done { written: usize },
    Stopped { written: usize },
    Skipped(&'static str),
    Failed(String),
}

struct Row {
    id: String,
    name: String,
    aliases: Vec<String>,
    gender: String,
    image: String,
    updated: String,
    remove: bool,
}

impl Store {
    pub fn open(path: &Path) -> rusqlite::Result<Store> {
        if let Some(dir) = path.parent() {
            let _ = std::fs::create_dir_all(dir);
        }
        Store::from_connection(Connection::open(path)?)
    }

    pub fn in_memory() -> rusqlite::Result<Store> {
        Store::from_connection(Connection::open_in_memory()?)
    }

    fn from_connection(conn: Connection) -> rusqlite::Result<Store> {
        // Two opens of a new file can overlap (a status request and a refresh start together); wait for the other instead of failing with "database is locked".
        conn.busy_timeout(std::time::Duration::from_secs(10))?;
        conn.execute_batch(
            "PRAGMA journal_mode=WAL; PRAGMA synchronous=NORMAL;
             CREATE TABLE IF NOT EXISTS names (
                 provider TEXT NOT NULL, provider_id TEXT NOT NULL, name TEXT NOT NULL, norm TEXT NOT NULL,
                 aliases_norm TEXT NOT NULL DEFAULT '', aliases_json TEXT NOT NULL DEFAULT '[]',
                 image TEXT NOT NULL DEFAULT '', gender TEXT NOT NULL DEFAULT '', updated TEXT NOT NULL DEFAULT '',
                 PRIMARY KEY (provider, provider_id));
             CREATE INDEX IF NOT EXISTS idx_names_norm ON names(norm);
             CREATE TABLE IF NOT EXISTS sync (
                 provider TEXT PRIMARY KEY, status TEXT NOT NULL DEFAULT 'never', run_cursor TEXT NOT NULL DEFAULT '', run_page INTEGER NOT NULL DEFAULT 1,
                 cursor TEXT NOT NULL DEFAULT '', last_started REAL NOT NULL DEFAULT 0, last_finished REAL NOT NULL DEFAULT 0,
                 last_full REAL NOT NULL DEFAULT 0, last_error TEXT NOT NULL DEFAULT '');",
        )?;
        Ok(Store { conn: Mutex::new(conn) })
    }

    fn conn(&self) -> std::sync::MutexGuard<'_, Connection> {
        self.conn.lock().unwrap_or_else(|p| p.into_inner())
    }

    pub fn count(&self, provider: Option<&str>) -> i64 {
        let c = self.conn();
        match provider {
            Some(p) => c.query_row("SELECT COUNT(*) FROM names WHERE provider=?", [p], |r| r.get(0)).unwrap_or(0),
            None => c.query_row("SELECT COUNT(*) FROM names", [], |r| r.get(0)).unwrap_or(0),
        }
    }

    fn state(&self, provider: &str) -> SyncState {
        let c = self.conn();
        c.query_row("SELECT status,run_cursor,run_page,cursor,last_started,last_finished,last_full,last_error FROM sync WHERE provider=?", [provider], |r| {
            Ok(SyncState {
                status: r.get(0)?,
                run_cursor: r.get(1)?,
                run_page: r.get(2)?,
                cursor: r.get(3)?,
                last_started: r.get(4)?,
                last_finished: r.get(5)?,
                last_full: r.get(6)?,
                last_error: r.get(7)?,
            })
        })
        .unwrap_or(SyncState { status: "never".into(), run_page: 1, ..Default::default() })
    }

    fn save(&self, provider: &str, s: &SyncState) {
        let err: String = s.last_error.chars().take(500).collect();
        let _ = self.conn().execute(
            "INSERT OR REPLACE INTO sync(provider,status,run_cursor,run_page,cursor,last_started,last_finished,last_full,last_error) VALUES(?,?,?,?,?,?,?,?,?)",
            params![provider, s.status, s.run_cursor, s.run_page, s.cursor, s.last_started, s.last_finished, s.last_full, err],
        );
    }

    fn store_rows(&self, provider: &str, rows: &[Row]) -> usize {
        let mut written = 0;
        let mut c = self.conn();
        let Ok(tx) = c.transaction() else { return 0 };
        for r in rows {
            if r.id.is_empty() {
                continue;
            }
            if r.remove {
                let _ = tx.execute("DELETE FROM names WHERE provider=? AND provider_id=?", params![provider, r.id]);
                continue;
            }
            if r.name.trim().is_empty() {
                continue;
            }
            let aliases_norm: Vec<String> = r.aliases.iter().map(|a| spaced(a)).collect();
            let ok = tx
                .execute(
                    "INSERT OR REPLACE INTO names(provider,provider_id,name,norm,aliases_norm,aliases_json,image,gender,updated) VALUES(?,?,?,?,?,?,?,?,?)",
                    params![provider, r.id, r.name.trim(), spaced(&r.name), aliases_norm.join("|"), json!(r.aliases).to_string(), r.image, r.gender, r.updated],
                )
                .is_ok();
            if ok {
                written += 1;
            }
        }
        let _ = tx.commit();
        written
    }

    // ----------------------------------------------------------------------------------------------------------------------------
    // Search
    // ----------------------------------------------------------------------------------------------------------------------------

    /// Performers whose name or alias contains every typed word (in any order), best matches first.
    pub fn search(&self, query: &str, limit: usize) -> Vec<Hit> {
        let words: Vec<String> = spaced(query).split(' ').filter(|w| !w.is_empty()).take(6).map(str::to_string).collect();
        if words.is_empty() || words.iter().map(|w| w.len()).sum::<usize>() < 2 {
            return Vec::new();
        }
        let phrase = words.join(" ");
        let clause = words.iter().map(|_| "(norm LIKE ? OR aliases_norm LIKE ?)").collect::<Vec<_>>().join(" AND ");
        let sql = format!("SELECT provider,provider_id,name,norm,aliases_norm,aliases_json,image FROM names WHERE {clause} ORDER BY (norm LIKE ?) DESC, length(norm) LIMIT 400");
        let mut args: Vec<String> = Vec::new();
        for w in &words {
            args.push(format!("%{w}%"));
            args.push(format!("%{w}%"));
        }
        args.push(format!("{phrase}%"));
        let c = self.conn();
        let Ok(mut stmt) = c.prepare(&sql) else { return Vec::new() };
        let rows = stmt.query_map(rusqlite::params_from_iter(args.iter()), |r| {
            Ok((
                r.get::<_, String>(0)?,
                r.get::<_, String>(1)?,
                r.get::<_, String>(2)?,
                r.get::<_, String>(3)?,
                r.get::<_, String>(4)?,
                r.get::<_, String>(5)?,
                r.get::<_, String>(6)?,
            ))
        });
        let Ok(rows) = rows else { return Vec::new() };
        let mut ranked: Vec<(u8, u8, usize, Hit)> = Vec::new();
        for (provider, id, name, norm, aliases_norm, aliases_json, image) in rows.flatten() {
            let mut names = vec![norm.clone()];
            names.extend(aliases_norm.split('|').filter(|a| !a.is_empty()).map(str::to_string));
            let close = closeness(&words, &names);
            let aliases: Vec<String> = serde_json::from_str::<Vec<String>>(&aliases_json).unwrap_or_default();
            let profile = if provider == "stashdb" { format!("https://stashdb.org/performers/{id}") } else { String::new() };
            let provider_rank = if provider == "stashdb" { 0 } else { 1 };
            ranked.push((close, provider_rank, norm.len(), Hit { provider, provider_id: id, name, aliases, image, profile }));
        }
        ranked.sort_by_key(|r| (r.0, r.1, r.2));
        let mut seen = std::collections::HashSet::new();
        let mut out = Vec::new();
        for (_, _, _, hit) in ranked {
            if !seen.insert((spaced(&hit.name), hit.provider.clone())) {
                continue;
            }
            out.push(hit);
            if out.len() >= limit {
                break;
            }
        }
        out
    }

    // ----------------------------------------------------------------------------------------------------------------------------
    // Status
    // ----------------------------------------------------------------------------------------------------------------------------

    pub fn status(&self, keys: &Keys) -> Value {
        let mut providers = serde_json::Map::new();
        for (name, configured) in [("stashdb", keys.stashdb.is_some()), ("theporndb", keys.tpdb.is_some())] {
            let s = self.state(name);
            providers.insert(
                name.to_string(),
                json!({ "configured": configured, "status": s.status, "count": self.count(Some(name)), "last_finished": iso(s.last_finished),
                        "last_error": s.last_error, "page": s.run_page }),
            );
        }
        let total = self.count(None);
        json!({ "ok": true, "total": total, "ready": total > 0, "providers": providers })
    }

    // ----------------------------------------------------------------------------------------------------------------------------
    // Refresh
    // ----------------------------------------------------------------------------------------------------------------------------

    /// Read the performers StashDB changed since the last run (everything, the first time). Resumable.
    pub fn sync_stashdb(&self, fetch: &dyn Fetch, key: &str, should_stop: &dyn Fn() -> bool, pause: Duration) -> Outcome {
        let st = self.state("stashdb");
        let resuming = st.status == "running";
        let full = st.cursor.is_empty();
        let run_cursor = if resuming && !st.run_cursor.is_empty() {
            st.run_cursor.clone()
        } else if full {
            "__full__".to_string()
        } else {
            st.cursor.clone()
        };
        let mut page = if resuming { st.run_page.max(1) } else { 1 };
        let mut s = SyncState { status: "running".into(), run_cursor: run_cursor.clone(), run_page: page, last_started: now_secs(), last_error: String::new(), ..st.clone() };
        self.save("stashdb", &s);
        let mut newest = st.cursor.clone();
        let mut written = 0;
        for _ in 0..MAX_PAGES_PER_RUN {
            if should_stop() {
                s.run_page = page;
                self.save("stashdb", &s);
                return Outcome::Stopped { written };
            }
            let mut input = json!({ "page": page, "per_page": STASH_PAGE, "sort": "UPDATED_AT", "direction": "ASC" });
            if run_cursor != "__full__" {
                input["updated_at"] = json!({ "value": run_cursor, "modifier": "GREATER_THAN" });
            }
            let rows = match fetch.stashdb(key, STASH_QUERY, &json!({ "input": input })).and_then(|d| stash_rows(&d)) {
                Ok(r) => r,
                Err(e) => return self.fail("stashdb", s, page, e),
            };
            if rows.is_empty() {
                break;
            }
            written += self.store_rows("stashdb", &rows);
            if let Some(m) = rows.iter().map(|r| r.updated.as_str()).filter(|u| !u.is_empty()).max() {
                if m > newest.as_str() {
                    newest = m.to_string();
                }
            }
            page += 1;
            s.run_page = page;
            self.save("stashdb", &s);
            if rows.len() < STASH_PAGE {
                break;
            }
            std::thread::sleep(pause);
        }
        s.status = "idle".into();
        s.cursor = if newest.is_empty() { iso(now_secs()) } else { newest };
        s.run_cursor = String::new();
        s.run_page = 1;
        s.last_finished = now_secs();
        if full {
            s.last_full = s.last_finished;
        }
        self.save("stashdb", &s);
        Outcome::Done { written }
    }

    /// Read every ThePornDB performer (about once a week). Resumable page by page.
    pub fn sync_tpdb(&self, fetch: &dyn Fetch, token: &str, force: bool, should_stop: &dyn Fn() -> bool, pause: Duration) -> Outcome {
        let st = self.state("theporndb");
        let due = force || st.status == "running" || st.last_full == 0.0 || (now_secs() - st.last_full) / 86400.0 >= TPDB_FULL_EVERY_DAYS;
        if !due {
            return Outcome::Skipped("up to date");
        }
        let mut page = if st.status == "running" { st.run_page.max(1) } else { 1 };
        let mut s = SyncState { status: "running".into(), run_page: page, last_started: now_secs(), last_error: String::new(), ..st };
        self.save("theporndb", &s);
        let mut written = 0;
        let mut last_page = 0i64;
        for _ in 0..MAX_PAGES_PER_RUN {
            if should_stop() {
                s.run_page = page;
                self.save("theporndb", &s);
                return Outcome::Stopped { written };
            }
            let data = match fetch.tpdb(token, &format!("performers?page={page}&per_page={TPDB_PAGE}")) {
                Ok(d) => d,
                Err(e) => return self.fail("theporndb", s, page, e),
            };
            let (rows, last) = tpdb_rows(&data);
            last_page = last_page.max(last);
            if rows.is_empty() {
                break;
            }
            written += self.store_rows("theporndb", &rows);
            page += 1;
            s.run_page = page;
            self.save("theporndb", &s);
            if last_page > 0 && page > last_page {
                break;
            }
            std::thread::sleep(pause);
        }
        s.status = "idle".into();
        s.run_page = 1;
        s.last_finished = now_secs();
        s.last_full = s.last_finished;
        self.save("theporndb", &s);
        Outcome::Done { written }
    }

    fn fail(&self, provider: &str, mut s: SyncState, page: i64, error: String) -> Outcome {
        s.status = "error".into();
        s.run_page = page;
        s.last_error = error.clone();
        self.save(provider, &s);
        Outcome::Failed(error)
    }

    /// Refresh every provider that has a key.
    pub fn refresh(&self, fetch: &dyn Fetch, keys: &Keys, force: bool, should_stop: &dyn Fn() -> bool, pause: Duration) -> Vec<(&'static str, Outcome)> {
        let mut out = Vec::new();
        if let Some(k) = &keys.stashdb {
            out.push(("stashdb", self.sync_stashdb(fetch, k, should_stop, pause)));
        }
        if let Some(t) = &keys.tpdb {
            out.push(("theporndb", self.sync_tpdb(fetch, t, force, should_stop, pause)));
        }
        out
    }
}

fn closeness(words: &[String], names: &[String]) -> u8 {
    let phrase = words.join(" ");
    if names.contains(&phrase) {
        return 0;
    }
    // Starts with what was typed, ending at a word ("stacy" in "Stacy Cruz"), before starting inside a longer word ("Stacyann").
    if names.iter().any(|n| n.strip_prefix(phrase.as_str()).is_some_and(|rest| rest.starts_with(' '))) {
        return 1;
    }
    if names.iter().any(|n| n.starts_with(&phrase)) {
        return 2;
    }
    if names.iter().any(|n| words.iter().all(|w| n.split(' ').any(|tok| tok.starts_with(w.as_str())))) {
        return 3;
    }
    4
}

fn iso(secs: f64) -> String {
    if secs <= 0.0 {
        return String::new();
    }
    // Civil date from days since the epoch (proleptic Gregorian), so no date library is needed for one timestamp format.
    let total = secs as i64;
    let (days, rem) = (total.div_euclid(86400), total.rem_euclid(86400));
    let z = days + 719_468;
    let era = z.div_euclid(146_097);
    let doe = z.rem_euclid(146_097);
    let yoe = (doe - doe / 1460 + doe / 36_524 - doe / 146_096) / 365;
    let y = yoe + era * 400;
    let doy = doe - (365 * yoe + yoe / 4 - yoe / 100);
    let mp = (5 * doy + 2) / 153;
    let d = doy - (153 * mp + 2) / 5 + 1;
    let m = if mp < 10 { mp + 3 } else { mp - 9 };
    let y = if m <= 2 { y + 1 } else { y };
    format!("{y:04}-{m:02}-{d:02}T{:02}:{:02}:{:02}Z", rem / 3600, (rem % 3600) / 60, rem % 60)
}

fn text(v: Option<&Value>) -> String {
    match v {
        Some(Value::String(s)) => s.trim().to_string(),
        Some(Value::Number(n)) => n.to_string(),
        _ => String::new(),
    }
}

fn aliases_of(v: Option<&Value>) -> Vec<String> {
    match v {
        Some(Value::Array(a)) => a.iter().filter_map(|x| x.as_str()).map(|s| s.trim().to_string()).filter(|s| !s.is_empty()).collect(),
        Some(Value::String(s)) => s.split(',').map(|x| x.trim().to_string()).filter(|x| !x.is_empty()).collect(),
        _ => Vec::new(),
    }
}

fn http_url(v: &str) -> String {
    let v = v.trim();
    if v.starts_with("http://") || v.starts_with("https://") {
        v.to_string()
    } else {
        String::new()
    }
}

/// The picture to show: the first http(s) link among a performer's images (a bare link, or objects with a `url`).
fn first_image(images: Option<&Value>) -> String {
    match images {
        Some(Value::Array(list)) => list
            .iter()
            .filter_map(|i| match i {
                Value::String(s) => Some(http_url(s)),
                Value::Object(o) => o.get("url").and_then(Value::as_str).map(http_url),
                _ => None,
            })
            .find(|u| !u.is_empty())
            .unwrap_or_default(),
        _ => String::new(),
    }
}

fn stash_rows(data: &Value) -> Result<Vec<Row>, String> {
    if data.get("errors").is_some() && data.get("data").map(|d| d.is_null()).unwrap_or(true) {
        let message = data["errors"][0]["message"].as_str().unwrap_or("error");
        return Err(format!("StashDB: {}", message.chars().take(200).collect::<String>()));
    }
    let root = data.pointer("/data/queryPerformers").ok_or("StashDB returned something unexpected")?;
    let list = root.get("performers").and_then(Value::as_array).cloned().unwrap_or_default();
    Ok(list
        .iter()
        .map(|p| {
            let gone = p.get("deleted").and_then(Value::as_bool).unwrap_or(false) || !text(p.get("merged_into_id")).is_empty();
            Row {
                id: text(p.get("id")),
                name: text(p.get("name")),
                aliases: aliases_of(p.get("aliases")),
                gender: text(p.get("gender")),
                image: first_image(p.get("images")),
                updated: text(p.get("updated")),
                remove: gone,
            }
        })
        .collect())
}

fn tpdb_rows(data: &Value) -> (Vec<Row>, i64) {
    let list = match data.get("data") {
        Some(Value::Array(a)) => a.clone(),
        _ => data.get("performers").and_then(Value::as_array).cloned().unwrap_or_default(),
    };
    let last = data.pointer("/meta/last_page").and_then(Value::as_i64).unwrap_or(0);
    let rows = list
        .iter()
        .map(|p| {
            let image = ["image", "thumbnail", "poster"]
                .iter()
                .find_map(|k| match p.get(*k) {
                    Some(Value::String(s)) if !http_url(s).is_empty() => Some(http_url(s)),
                    Some(Value::Object(o)) => o.get("url").and_then(Value::as_str).map(http_url).filter(|u| !u.is_empty()),
                    _ => None,
                })
                .unwrap_or_else(|| first_image(p.get("posters").or_else(|| p.get("images"))));
            Row {
                id: ["id", "_id", "uuid"].iter().map(|k| text(p.get(*k))).find(|v| !v.is_empty()).unwrap_or_default(),
                name: ["name", "full_name"].iter().map(|k| text(p.get(*k))).find(|v| !v.is_empty()).unwrap_or_default(),
                aliases: aliases_of(p.get("aliases")),
                gender: text(p.get("gender")),
                image,
                updated: String::new(),
                remove: false,
            }
        })
        .collect();
    (rows, last)
}

#[cfg(test)]
mod tests {
    use super::*;
    use std::sync::atomic::{AtomicUsize, Ordering};
    use std::sync::Mutex as StdMutex;

    struct Fake {
        stash: StdMutex<Vec<Value>>,        // responses in order
        stash_inputs: StdMutex<Vec<Value>>, // the inputs asked for
        tpdb_pages: usize,
        tpdb_calls: AtomicUsize,
    }

    impl Fake {
        fn new(stash: Vec<Value>) -> Fake {
            Fake { stash: StdMutex::new(stash), stash_inputs: StdMutex::new(vec![]), tpdb_pages: 2, tpdb_calls: AtomicUsize::new(0) }
        }
    }

    impl Fetch for Fake {
        fn stashdb(&self, _key: &str, _q: &str, v: &Value) -> Result<Value, String> {
            self.stash_inputs.lock().unwrap().push(v["input"].clone());
            let mut s = self.stash.lock().unwrap();
            if s.is_empty() {
                return Ok(page_of(vec![]));
            }
            Ok(s.remove(0))
        }
        fn tpdb(&self, _t: &str, path: &str) -> Result<Value, String> {
            self.tpdb_calls.fetch_add(1, Ordering::SeqCst);
            let page: usize = path.split("page=").nth(1).and_then(|p| p.split('&').next()).and_then(|p| p.parse().ok()).unwrap();
            let rows: Vec<Value> = (0..3)
                .map(|i| json!({ "id": format!("t{page}-{i}"), "name": format!("Tp {page} {i}"), "aliases": ["Al"], "image": "https://i/x.jpg" }))
                .collect();
            Ok(json!({ "data": rows, "meta": { "current_page": page, "last_page": self.tpdb_pages } }))
        }
    }

    fn performer(id: usize, name: &str, updated: &str) -> Value {
        json!({ "id": format!("s{id}"), "name": name, "aliases": [], "gender": "FEMALE", "images": [{ "url": format!("https://img/{id}.jpg") }], "deleted": false, "merged_into_id": null, "updated": updated })
    }

    fn page_of(rows: Vec<Value>) -> Value {
        json!({ "data": { "queryPerformers": { "count": rows.len(), "performers": rows } } })
    }

    fn never() -> bool {
        false
    }

    fn store_with(names: &[(&str, &str, &[&str])]) -> Store {
        let s = Store::in_memory().unwrap();
        let rows: Vec<Row> = names
            .iter()
            .map(|(id, n, al)| Row {
                id: id.to_string(),
                name: n.to_string(),
                aliases: al.iter().map(|a| a.to_string()).collect(),
                gender: String::new(),
                image: "x".into(),
                updated: String::new(),
                remove: false,
            })
            .collect();
        s.store_rows("stashdb", &rows);
        s
    }

    #[test]
    fn spaced_folds_case_accents_and_punctuation() {
        assert_eq!(spaced("Zoë  O'Neil"), "zoe o neil");
        assert_eq!(spaced("  --Ava_Stone.. "), "ava stone");
        assert_eq!(spaced(""), "");
    }

    #[test]
    fn search_finds_partial_names_in_any_order_and_through_aliases() {
        let s = store_with(&[("1", "Stacy Cruz", &["Stacey Cruise"]), ("2", "Anastasia Star", &[]), ("3", "Stacy Adams", &[])]);
        let names = |q: &str| s.search(q, 8).into_iter().map(|h| h.name).collect::<Vec<_>>();
        assert!(names("sta").contains(&"Stacy Cruz".to_string()));
        assert!(names("stacy").contains(&"Stacy Cruz".to_string()));
        assert_eq!(names("cruz stacy"), vec!["Stacy Cruz"]);
        assert_eq!(names("stacey cru"), vec!["Stacy Cruz"]); // an alias
        assert!(names("zzz").is_empty() && names("s").is_empty());
    }

    #[test]
    fn search_puts_exact_then_prefix_matches_first_and_limits() {
        let s = store_with(&[("1", "Anna Stacy", &[]), ("2", "Stacy", &[]), ("3", "Stacy Cruz", &[]), ("4", "Stacyann", &[])]);
        let names: Vec<_> = s.search("stacy", 3).into_iter().map(|h| h.name).collect();
        assert_eq!(names.len(), 3);
        assert_eq!(names[0], "Stacy");
        assert_eq!(names[1], "Stacy Cruz");
    }

    #[test]
    fn hits_carry_the_profile_link_and_picture() {
        let s = store_with(&[("abc", "Ava Stone", &[])]);
        let h = &s.search("ava", 8)[0];
        assert_eq!((h.provider.as_str(), h.provider_id.as_str(), h.image.as_str()), ("stashdb", "abc", "x"));
        assert_eq!(h.profile, "https://stashdb.org/performers/abc");
    }

    #[test]
    fn stashdb_first_run_reads_everything_then_only_changes() {
        let s = Store::in_memory().unwrap();
        let first: Vec<Value> = (1..=STASH_PAGE).map(|i| performer(i, &format!("Name {i}"), "2026-01-05T00:00:00Z")).collect();
        let fake = Fake::new(vec![page_of(first), page_of(vec![performer(41, "Last One", "2026-02-01T00:00:00Z")])]);
        let out = s.sync_stashdb(&fake, "k", &never, Duration::ZERO);
        assert_eq!(out, Outcome::Done { written: 41 });
        assert_eq!(s.count(Some("stashdb")), 41);
        assert!(fake.stash_inputs.lock().unwrap()[0].get("updated_at").is_none(), "the first run asks for everything");
        assert_eq!(s.state("stashdb").cursor, "2026-02-01T00:00:00Z");

        let fake2 = Fake::new(vec![page_of(vec![
            performer(7, "Name 7 Renamed", "2026-03-01T00:00:00Z"),
            json!({ "id": "s8", "name": "Gone", "aliases": [], "deleted": true, "updated": "2026-03-02T00:00:00Z" }),
        ])]);
        assert_eq!(s.sync_stashdb(&fake2, "k", &never, Duration::ZERO), Outcome::Done { written: 1 });
        assert_eq!(fake2.stash_inputs.lock().unwrap()[0]["updated_at"], json!({ "value": "2026-02-01T00:00:00Z", "modifier": "GREATER_THAN" }));
        assert_eq!(s.count(Some("stashdb")), 40, "the deleted performer is gone");
        assert_eq!(s.search("renamed", 8)[0].name, "Name 7 Renamed");
    }

    #[test]
    fn a_stopped_refresh_resumes_at_the_page_it_reached() {
        let s = Store::in_memory().unwrap();
        let full = |p: usize| page_of((0..STASH_PAGE).map(|i| performer(p * 100 + i, &format!("P{p}-{i}"), "2026-01-01T00:00:00Z")).collect());
        let fake = Fake::new(vec![full(1), full(2)]);
        let calls = AtomicUsize::new(0);
        let stop_after_one = || calls.fetch_add(1, Ordering::SeqCst) >= 1;
        assert!(matches!(s.sync_stashdb(&fake, "k", &stop_after_one, Duration::ZERO), Outcome::Stopped { .. }));
        assert_eq!(s.state("stashdb").status, "running");
        assert_eq!(s.state("stashdb").run_page, 2);
        let fake2 = Fake::new(vec![page_of(vec![performer(900, "End", "2026-01-02T00:00:00Z")])]);
        s.sync_stashdb(&fake2, "k", &never, Duration::ZERO);
        assert_eq!(fake2.stash_inputs.lock().unwrap()[0]["page"], json!(2), "carried on, did not start over");
        assert_eq!(s.state("stashdb").status, "idle");
    }

    #[test]
    fn a_failed_refresh_is_recorded_and_can_be_retried() {
        let s = Store::in_memory().unwrap();
        let fake = Fake::new(vec![json!({ "errors": [{ "message": "Bad filter" }] })]);
        match s.sync_stashdb(&fake, "k", &never, Duration::ZERO) {
            Outcome::Failed(e) => assert!(e.contains("Bad filter")),
            other => panic!("{other:?}"),
        }
        assert_eq!(s.state("stashdb").status, "error");
        assert!(s.status(&Keys { stashdb: Some("k".into()), tpdb: None })["providers"]["stashdb"]["last_error"].as_str().unwrap().contains("Bad filter"));
    }

    #[test]
    fn tpdb_reads_every_page_and_skips_until_a_week_has_passed() {
        let s = Store::in_memory().unwrap();
        let fake = Fake::new(vec![]);
        assert_eq!(s.sync_tpdb(&fake, "t", false, &never, Duration::ZERO), Outcome::Done { written: 6 });
        assert_eq!(s.count(Some("theporndb")), 6);
        assert_eq!(s.sync_tpdb(&fake, "t", false, &never, Duration::ZERO), Outcome::Skipped("up to date"));
        assert_eq!(fake.tpdb_calls.load(Ordering::SeqCst), 2);
        assert_eq!(s.sync_tpdb(&fake, "t", true, &never, Duration::ZERO), Outcome::Done { written: 6 }, "force reads again");
    }

    #[test]
    fn refresh_only_touches_providers_with_a_key() {
        let s = Store::in_memory().unwrap();
        let fake = Fake::new(vec![]);
        assert!(s.refresh(&fake, &Keys::default(), false, &never, Duration::ZERO).is_empty());
        let out = s.refresh(&fake, &Keys { stashdb: None, tpdb: Some("t".into()) }, false, &never, Duration::ZERO);
        assert_eq!(out.len(), 1);
        assert_eq!(out[0].0, "theporndb");
    }

    #[test]
    fn iso_formats_known_moments() {
        assert_eq!(iso(0.0), "");
        assert_eq!(iso(1_767_225_600.0), "2026-01-01T00:00:00Z");
        assert_eq!(iso(1_772_323_199.0), "2026-02-28T23:59:59Z");
    }

    #[test]
    fn the_store_survives_being_reopened_from_disk() {
        let dir = tempfile::tempdir().unwrap();
        let path = dir.path().join("names.db");
        {
            let s = Store::open(&path).unwrap();
            s.store_rows(
                "stashdb",
                &[Row { id: "1".into(), name: "Ava Stone".into(), aliases: vec![], gender: String::new(), image: String::new(), updated: String::new(), remove: false }],
            );
        }
        assert_eq!(Store::open(&path).unwrap().search("ava", 8).len(), 1);
    }
}
