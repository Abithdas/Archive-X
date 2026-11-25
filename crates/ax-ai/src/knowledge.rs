//! What the assistant knows about Archive X itself (`ai/ai_knowledge.py`, `Knowledge`): the changelog, the README and the docs, cut into pieces and
//! searched by the words of a question. No embedding model, just SQLite and word scoring. The index is rebuilt only when those files change.
//!
//! Not ported: the pieces Python takes from the docstrings of the program's own modules (`_chunks_code` uses Python's `ast`); once the Python
//! backend is gone there are no such modules to read, so the index holds the changelog and the documents only.

use crate::pytext::{head, lstrip_chars, splitlines};
use crate::safety::redact_text;
use ax_core::scene_parser::py_strip;
use ax_db::rusqlite::{params, params_from_iter, Connection};
use serde_json::{json, Value};
use sha1::{Digest, Sha1};
use std::collections::BTreeMap;
use std::fs;
use std::path::{Path, PathBuf};
use std::time::UNIX_EPOCH;

pub const MAX_BODY: usize = 900;
const STOP: [&str; 31] = [
    "the", "a", "an", "and", "or", "of", "to", "in", "on", "for", "is", "are", "was", "be", "it", "this", "that", "with", "how", "what", "why", "do", "does", "did", "can", "i",
    "my", "me", "you", "your", "",
];

/// `(source, ref, title, body)`.
pub type Chunk = (String, String, String, String);

/// `Path.read_text(encoding="utf-8", errors="replace")`.
fn read_text(path: &Path) -> std::io::Result<String> {
    Ok(String::from_utf8_lossy(&fs::read(path)?).into_owned())
}

fn collapse_ws(s: &str) -> String {
    crate::pytext::split_words(s).collect::<Vec<_>>().join(" ")
}

/// The pieces of the changelog: one per top-level bullet of each release section.
pub fn chunks_changelog(text: &str) -> Vec<Chunk> {
    let mut out = Vec::new();
    let mut section = String::new();
    let mut bullet: Vec<String> = Vec::new();
    let flush = |bullet: &mut Vec<String>, section: &str, out: &mut Vec<Chunk>| {
        if !bullet.is_empty() {
            let body = py_strip(&bullet.join("\n")).to_string();
            let title = head(&collapse_ws(py_strip(lstrip_chars(&bullet[0], "- "))), 110);
            out.push(("changelog".into(), section.to_string(), title, head(&body, MAX_BODY)));
            bullet.clear();
        }
    };
    for line in splitlines(text) {
        if let Some(rest) = line.strip_prefix("## ") {
            flush(&mut bullet, &section, &mut out);
            section = py_strip(rest).to_string();
        } else if line.starts_with("- ") {
            flush(&mut bullet, &section, &mut out);
            bullet.push(line.to_string());
        } else if !bullet.is_empty() && !py_strip(line).is_empty() {
            bullet.push(py_strip(line).to_string());
        }
    }
    flush(&mut bullet, &section, &mut out);
    out
}

/// The pieces of a Markdown file: one per heading, with the heading as the title.
pub fn chunks_markdown(text: &str, rel: &str, stem: &str) -> Vec<Chunk> {
    let mut out = Vec::new();
    let mut title = stem.to_string();
    let mut body: Vec<&str> = Vec::new();
    let flush = |body: &mut Vec<&str>, title: &str, out: &mut Vec<Chunk>| {
        let t = py_strip(&body.join("\n")).to_string();
        if !t.is_empty() {
            out.push(("docs".into(), rel.to_string(), head(title, 110), head(&t, MAX_BODY + 300)));
        }
        body.clear();
    };
    for line in splitlines(text) {
        if line.starts_with('#') {
            flush(&mut body, &title, &mut out);
            let t = py_strip(lstrip_chars(line, "# "));
            title = if t.is_empty() { stem.to_string() } else { t.to_string() };
        } else {
            body.push(line);
        }
    }
    flush(&mut body, &title, &mut out);
    out
}

/// The files the index is made of: `(label, path)`.
pub fn source_files(root: &Path) -> Vec<(String, PathBuf)> {
    let mut files = Vec::new();
    for name in ["CHANGELOG.md", "README.md"] {
        if root.join(name).is_file() {
            files.push((name.to_string(), root.join(name)));
        }
    }
    let docs = root.join("docs");
    if docs.is_dir() {
        let mut found = Vec::new();
        collect_md(&docs, &mut found);
        found.sort();
        for p in found {
            let rel = p.strip_prefix(&docs).expect("inside docs").to_path_buf();
            let size = fs::metadata(&p).map(|m| m.len()).unwrap_or(0);
            let first = rel.components().next().map(|c| c.as_os_str().to_string_lossy().into_owned());
            if size <= 300_000 && first.as_deref() != Some("archive") {
                files.push((format!("docs/{}", rel.to_string_lossy().replace('\\', "/")), p));
            }
        }
    }
    files
}

/// `Path.rglob("*.md")`: files and folders alike whose name ends in `.md`, at any depth (links to folders are not followed).
fn collect_md(dir: &Path, out: &mut Vec<PathBuf>) {
    let Ok(entries) = fs::read_dir(dir) else { return };
    for entry in entries.flatten() {
        let path = entry.path();
        if path.file_name().is_some_and(|n| n.to_string_lossy().ends_with(".md")) {
            out.push(path.clone());
        }
        if entry.file_type().is_ok_and(|t| t.is_dir()) {
            collect_md(&path, out);
        }
    }
}

/// A fingerprint of the files (names, sizes, modification times) that changes when any of them does.
pub fn signature(files: &[(String, PathBuf)]) -> std::io::Result<String> {
    let mut h = Sha1::new();
    for (rel, p) in files {
        let md = fs::metadata(p)?;
        let mtime_ns = md.modified()?.duration_since(UNIX_EPOCH).map(|d| d.as_nanos() as i128).unwrap_or_else(|e| -(e.duration().as_nanos() as i128));
        h.update(format!("{rel}|{}|{mtime_ns}\n", md.len()).as_bytes());
    }
    Ok(h.finalize().iter().map(|b| format!("{b:02x}")).collect())
}

/// Build the index if it is missing or the files changed since it was built. True when it was (re)built.
pub fn ensure(conn: &Connection, now: &dyn Fn() -> String, root: &Path) -> Result<bool, ax_db::rusqlite::Error> {
    let files = source_files(root);
    let Ok(sig) = signature(&files) else { return Ok(false) };
    let have: i64 = conn.query_row("SELECT COUNT(*) FROM ai_knowledge", [], |r| r.get(0))?;
    let stored = crate::settings::get(conn, "ai_kb_signature")?.and_then(|v| v.as_str().map(String::from)).unwrap_or_default();
    if have > 0 && stored == sig {
        return Ok(false);
    }
    rebuild(conn, now, root, Some(&files), Some(&sig))?;
    Ok(true)
}

/// Rebuild the index from the files; returns how many pieces each source has.
pub fn rebuild(
    conn: &Connection,
    now: &dyn Fn() -> String,
    root: &Path,
    files: Option<&[(String, PathBuf)]>,
    sig: Option<&str>,
) -> Result<BTreeMap<String, i64>, ax_db::rusqlite::Error> {
    let owned;
    let files = match files {
        Some(f) => f,
        None => {
            owned = source_files(root);
            &owned
        }
    };
    let mut rows: Vec<Chunk> = Vec::new();
    for (rel, path) in files {
        if rel.ends_with(".py") {
            continue; // see the module note
        }
        let Ok(text) = read_text(path) else { continue };
        if rel == "CHANGELOG.md" {
            rows.extend(chunks_changelog(&text));
        } else {
            let stem = path.file_stem().map(|s| s.to_string_lossy().into_owned()).unwrap_or_default();
            rows.extend(chunks_markdown(&text, rel, &stem));
        }
    }
    let stamp = now();
    conn.execute("DELETE FROM ai_knowledge", [])?;
    for (s, r, t, b) in &rows {
        conn.execute("INSERT INTO ai_knowledge(source,ref,title,body,updated_at) VALUES (?,?,?,?,?)", params![s, r, redact_text(t), redact_text(b), stamp])?;
    }
    let signature_text = match sig {
        Some(s) => s.to_string(),
        None => signature(files).unwrap_or_default(),
    };
    crate::settings::set(conn, "ai_kb_signature", &Value::String(signature_text))?;
    counts(conn)
}

pub fn counts(conn: &Connection) -> Result<BTreeMap<String, i64>, ax_db::rusqlite::Error> {
    let mut stmt = conn.prepare("SELECT source,COUNT(*) FROM ai_knowledge GROUP BY source")?;
    let rows = stmt.query_map([], |r| Ok((r.get::<_, String>(0)?, r.get::<_, i64>(1)?)))?;
    rows.collect()
}

/// `re.findall(r"[a-z0-9_]{2,}", text)`.
fn find_words(text: &str) -> Vec<String> {
    let mut out = Vec::new();
    let mut cur = String::new();
    for c in text.chars().chain(std::iter::once('\0')) {
        if c.is_ascii_lowercase() || c.is_ascii_digit() || c == '_' {
            cur.push(c);
        } else {
            if cur.len() >= 2 {
                out.push(std::mem::take(&mut cur));
            }
            cur.clear();
        }
    }
    out
}

/// The pieces of Archive X's own text that mention the words of `query`, best first.
pub fn search(conn: &Connection, query: &str, limit: Option<i64>) -> Result<Vec<Value>, ax_db::rusqlite::Error> {
    let mut words: Vec<String> = Vec::new();
    for w in find_words(&query.to_lowercase()) {
        if !words.contains(&w) && !STOP.contains(&w.as_str()) {
            words.push(w);
        }
    }
    words.truncate(8);
    if words.is_empty() {
        return Ok(Vec::new());
    }
    let clauses = vec!["LOWER(title) LIKE ? OR LOWER(body) LIKE ?"; words.len()].join(" OR ");
    let mut params: Vec<String> = Vec::new();
    for w in &words {
        params.push(format!("%{w}%"));
        params.push(format!("%{w}%"));
    }
    let sql = format!("SELECT source,ref,title,body FROM ai_knowledge WHERE {clauses} LIMIT 400");
    let rows: Vec<(String, String, String, String)> = conn
        .prepare(&sql)?
        .query_map(params_from_iter(params.iter()), |r| Ok((r.get(0)?, r.get(1)?, r.get(2)?, r.get(3)?)))?
        .collect::<Result<_, _>>()?;
    let mut scored: Vec<(usize, i64, String, String, String, String)> = Vec::new();
    for (source, rf, title, body) in rows {
        let (tl, bl) = (title.to_lowercase(), body.to_lowercase());
        let score: i64 =
            words.iter().map(|w| (if tl.contains(w.as_str()) { 3 } else { 0 }) + (bl.matches(w.as_str()).count() as i64).min(4)).sum::<i64>() + i64::from(source != "code");
        let hits = words.iter().filter(|w| tl.contains(w.as_str()) || bl.contains(w.as_str())).count();
        scored.push((hits, score, source, rf, title, body));
    }
    scored.sort_by(|a, b| b.0.cmp(&a.0).then(b.1.cmp(&a.1)));
    // `int(limit or 6)`: nothing or zero means six; anything else is held between 1 and 10
    let n = match limit {
        None | Some(0) => 6,
        Some(l) => l.clamp(1, 10) as usize,
    };
    let mut out = Vec::new();
    for (_h, _s, source, rf, title, body) in scored.into_iter().take(n) {
        let low = body.to_lowercase();
        // the position is a character index in the lower-cased text, applied to the original: the two can differ in length
        let at = words.iter().filter_map(|w| low.find(w.as_str()).map(|b| low[..b].chars().count())).min().unwrap_or(0);
        let start = at.saturating_sub(80);
        let text: String = body.chars().skip(start).take(320).collect();
        out.push(json!({"source": source, "where": rf, "title": title, "text": py_strip(&text)}));
    }
    Ok(out)
}
