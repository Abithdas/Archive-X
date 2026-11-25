//! The assistant's notebook (`ai/ai_knowledge.py`, `Findings`): problems and ideas it noticed, each of which a person can accept, reject or mark
//! fixed. A rejected finding is never written again, so the assistant stops repeating itself.
//!
//! The functions take the connection and a clock, as the other database ports do; each call is one step, as in Python where each method locks
//! and commits.

use crate::pytext::{head, split_words};
use crate::safety::redact_text;
use ax_db::rusqlite::{params, Connection, OptionalExtension};
use serde_json::{json, Map, Value};

pub const FINDING_KINDS: [&str; 3] = ["issue", "idea", "note"];
pub const STATUSES: [&str; 4] = ["OPEN", "ACCEPTED", "REJECTED", "FIXED"];
pub const MAX_OPEN_FINDINGS: i64 = 200;

/// The key a title is known by: lower-case ASCII letters and digits, runs of anything else as one space.
pub fn title_key(title: &str) -> String {
    let lower = title.to_lowercase();
    let mut out = String::new();
    let mut gap = false;
    for c in lower.chars() {
        if c.is_ascii_lowercase() || c.is_ascii_digit() {
            if gap && !out.is_empty() {
                out.push(' ');
            }
            gap = false;
            out.push(c);
        } else {
            gap = true;
        }
    }
    // cut after the strip, as Python does: a space can be the last character kept
    head(&out, 120)
}

/// Write a problem or idea in the notebook. A rejected one is not written again; a repeat only refreshes the detail.
pub fn record(conn: &Connection, now: &dyn Fn() -> String, kind: &str, title: &str, detail: &str) -> Result<Value, ax_db::rusqlite::Error> {
    let collapsed = split_words(title).collect::<Vec<_>>().join(" ");
    let t = head(&redact_text(&collapsed), 140);
    let key = title_key(&t);
    if key.chars().count() < 6 {
        return Ok(json!({"recorded": false, "reason": "A finding needs a clear title."}));
    }
    let lowered = kind.to_lowercase();
    let k = if FINDING_KINDS.contains(&lowered.as_str()) { lowered } else { "note".to_string() };
    let d = head(&redact_text(detail), 1200);
    let existing: Option<(i64, String)> = conn.query_row("SELECT id,status FROM ai_findings WHERE title_key=?", [&key], |r| Ok((r.get(0)?, r.get(1)?))).optional()?;
    if let Some((id, status)) = existing {
        if status == "REJECTED" {
            return Ok(json!({"recorded": false, "reason": "You rejected this earlier, so it is not suggested again."}));
        }
        conn.execute("UPDATE ai_findings SET detail=?,updated_at=? WHERE id=?", params![d, now(), id])?;
        return Ok(json!({"recorded": true, "id": id, "status": status, "repeat": true}));
    }
    let open_now: i64 = conn.query_row("SELECT COUNT(*) FROM ai_findings WHERE status='OPEN'", [], |r| r.get(0))?;
    if open_now >= MAX_OPEN_FINDINGS {
        return Ok(json!({"recorded": false, "reason": "The notebook is full of open findings: review some first."}));
    }
    let (created, updated) = (now(), now());
    conn.execute(
        "INSERT INTO ai_findings(kind,title,title_key,detail,status,created_at,updated_at) VALUES (?,?,?,?,'OPEN',?,?)",
        params![k, t, key, d, created, updated],
    )?;
    Ok(json!({"recorded": true, "id": conn.last_insert_rowid(), "status": "OPEN", "repeat": false}))
}

/// The notebook, newest first, optionally of one status. An unknown status lists everything; the limit is held between 1 and 200.
pub fn list(conn: &Connection, status: Option<&str>, limit: i64) -> Result<Vec<Value>, ax_db::rusqlite::Error> {
    let n = limit.clamp(1, 200);
    let sql_all = "SELECT id,kind,title,detail,status,created_at,updated_at FROM ai_findings ORDER BY id DESC LIMIT ?";
    let sql_one = "SELECT id,kind,title,detail,status,created_at,updated_at FROM ai_findings WHERE status=? ORDER BY id DESC LIMIT ?";
    let row = |r: &ax_db::rusqlite::Row| -> ax_db::rusqlite::Result<Value> {
        let mut m = Map::new();
        for (i, name) in ["id", "kind", "title", "detail", "status", "created_at", "updated_at"].iter().enumerate() {
            m.insert((*name).to_string(), if i == 0 { json!(r.get::<_, i64>(i)?) } else { json!(r.get::<_, String>(i)?) });
        }
        Ok(Value::Object(m))
    };
    match status.filter(|s| STATUSES.contains(s)) {
        Some(s) => conn.prepare(sql_one)?.query_map(params![s, n], row)?.collect(),
        None => conn.prepare(sql_all)?.query_map(params![n], row)?.collect(),
    }
}

/// A person's decision on a finding. False when there is no such finding; an unknown status is an error with Python's text.
pub fn decide(conn: &Connection, now: &dyn Fn() -> String, finding_id: i64, status: &str) -> Result<bool, String> {
    if !STATUSES.contains(&status) {
        return Err("Unknown status".to_string());
    }
    let changed = conn.execute("UPDATE ai_findings SET status=?,updated_at=? WHERE id=?", params![status, now(), finding_id]).map_err(|e| e.to_string())?;
    Ok(changed > 0)
}

/// How many findings have each status (all four are always present, in the order of `STATUSES`).
pub fn counts(conn: &Connection) -> Result<Map<String, Value>, ax_db::rusqlite::Error> {
    let mut have: std::collections::HashMap<String, i64> = std::collections::HashMap::new();
    let mut stmt = conn.prepare("SELECT status,COUNT(*) FROM ai_findings GROUP BY status")?;
    for row in stmt.query_map([], |r| Ok((r.get::<_, String>(0)?, r.get::<_, i64>(1)?)))? {
        let (s, n) = row?;
        have.insert(s, n);
    }
    Ok(STATUSES.iter().map(|s| ((*s).to_string(), json!(have.get(*s).copied().unwrap_or(0)))).collect())
}
