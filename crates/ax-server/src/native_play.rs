//! Watch progress: `POST /api/play/progress` (the player saves where you are every few seconds) and `GET /api/play/history/{channel}/{message}`.
//!
//! This is the first table Rust writes (`watch_history`, docs/adr/0007): a port of `save_play_progress` in routes_experience.py. Anything
//! the Python handler would treat in a special way (a body that is not an object, a number written as text, a missing field) is declined,
//! so Python answers it exactly as before.

use ax_db::rusqlite::{params, OptionalExtension};
use hyper::{HeaderMap, Response, StatusCode};
use serde_json::{json, Value};

use crate::auth::json_error;
use crate::native::{rows_with, Native};
use crate::optimize::json_response;
use crate::proxy::Body;

struct Progress {
    channel: String,
    message: i64,
    position: f64,
    duration: f64,
    delta: f64,
}

/// `max(0.0, float(value or 0))` for the plain cases: absent, null or a JSON number. Text and booleans are Python's to read.
fn non_negative(v: Option<&Value>) -> Option<f64> {
    match v {
        None | Some(Value::Null) => Some(0.0),
        Some(Value::Number(n)) => n.as_f64().map(|f| f.max(0.0)),
        _ => None,
    }
}

fn parse(body: &[u8]) -> Option<Progress> {
    let v: Value = serde_json::from_slice(body).ok()?;
    let o = v.as_object()?;
    let channel = match o.get("channel_id")? {
        Value::String(s) => s.clone(),
        Value::Number(n) if n.is_i64() => n.to_string(),
        _ => return None,
    };
    let message = o.get("message_id")?.as_i64()?;
    Some(Progress { channel, message, position: non_negative(o.get("position"))?, duration: non_negative(o.get("duration"))?, delta: non_negative(o.get("watch_delta"))? })
}

/// `datetime.now(timezone.utc).isoformat(timespec="seconds")`, e.g. `2025-06-01T12:30:05+00:00`.
pub(crate) fn now_iso() -> String {
    let secs = std::time::SystemTime::now().duration_since(std::time::UNIX_EPOCH).map(|d| d.as_secs() as i64).unwrap_or(0);
    let (days, rem) = (secs.div_euclid(86400), secs.rem_euclid(86400));
    let z = days + 719_468;
    let era = z.div_euclid(146_097);
    let doe = z.rem_euclid(146_097);
    let yoe = (doe - doe / 1460 + doe / 36_524 - doe / 146_096) / 365;
    let doy = doe - (365 * yoe + yoe / 4 - yoe / 100);
    let mp = (5 * doy + 2) / 153;
    let d = doy - (153 * mp + 2) / 5 + 1;
    let m = if mp < 10 { mp + 3 } else { mp - 9 };
    let y = yoe + era * 400 + i64::from(m <= 2);
    format!("{y:04}-{m:02}-{d:02}T{:02}:{:02}:{:02}+00:00", rem / 3600, rem % 3600 / 60, rem % 60)
}

impl Native {
    pub(crate) async fn play_progress(&self, body: &[u8], request: &HeaderMap) -> Option<Response<Body>> {
        let p = parse(body)?;
        let now = now_iso();
        let stored = self
            .writer
            .write(move |c| {
                let Some(media_id) = c
                    .query_row("SELECT id FROM media WHERE source_channel=? AND source_message_id=?", params![p.channel, p.message], |r| r.get::<_, i64>(0))
                    .optional()?
                else {
                    return Ok(None);
                };
                let existing: Option<(i64, f64)> = c
                    .query_row("SELECT COALESCE(play_count,0), COALESCE(total_watch_seconds,0) FROM watch_history WHERE media_id=?", [media_id], |r| {
                        Ok((r.get(0)?, r.get(1)?))
                    })
                    .optional()?;
                let (mut play_count, mut total) = existing.unwrap_or((0, 0.0));
                // A small delta is supplied every few seconds; large jumps are ignored so a seek does not inflate the watch time.
                if p.delta <= 20.0 {
                    total += p.delta;
                }
                let completed = i64::from(p.duration != 0.0 && p.position >= (p.duration - 10.0).max(0.0));
                if existing.is_none() {
                    play_count = 1;
                } else if p.position < 5.0 && p.delta > 0.0 {
                    play_count += 1;
                }
                c.execute(
                    "INSERT INTO watch_history(media_id,position,duration,play_count,total_watch_seconds,last_played_at,completed,updated_at) VALUES(?,?,?,?,?,?,?,?) \
                     ON CONFLICT(media_id) DO UPDATE SET position=excluded.position,duration=excluded.duration,play_count=excluded.play_count,\
                     total_watch_seconds=excluded.total_watch_seconds,last_played_at=excluded.last_played_at,completed=excluded.completed,updated_at=excluded.updated_at",
                    params![media_id, p.position, p.duration, play_count, total, now, completed, now],
                )?;
                Ok(Some(completed != 0))
            })
            .await;
        let payload = match stored {
            Ok(Some(completed)) => json!({ "ok": true, "stored": true, "completed": completed }),
            Ok(None) => json!({ "ok": false, "stored": false }),
            Err(e) => {
                tracing::error!("play progress: {e}");
                return Some(json_error(StatusCode::SERVICE_UNAVAILABLE, "The watch progress could not be saved"));
            }
        };
        Some(json_response(request, serde_json::to_vec(&payload).unwrap_or_default()))
    }

    pub(crate) async fn play_history(&self, (channel, message): (i64, i64), request: &HeaderMap) -> Option<Response<Body>> {
        let read = self
            .db
            .read(move |c| {
                rows_with(
                    c,
                    "SELECT wh.* FROM watch_history wh JOIN media m ON m.id=wh.media_id WHERE m.source_channel=? AND m.source_message_id=?",
                    params![channel.to_string(), message],
                )
            })
            .await;
        match read {
            Ok(rows) => {
                let payload = match rows.into_iter().next() {
                    Some(row) => Value::Object(row),
                    None => json!({ "position": 0, "duration": 0, "play_count": 0, "total_watch_seconds": 0, "completed": false }),
                };
                Some(json_response(request, serde_json::to_vec(&payload).unwrap_or_default()))
            }
            Err(e) => {
                tracing::error!("play history: {e}");
                Some(json_error(StatusCode::SERVICE_UNAVAILABLE, "The watch history could not be read"))
            }
        }
    }
}

impl Native {
    /// `POST /api/entities/{performer|studio}/{id}/favorite`: flip the favourite mark (`save` of routes_experience.py).
    pub(crate) async fn entity_favorite_toggle(&self, kind: &'static str, id: i64, request: &HeaderMap) -> Option<Response<Body>> {
        let now = now_iso();
        let table = if kind == "performer" { "performers" } else { "studios" };
        let result = self
            .writer
            .write(move |c| {
                let exists = c.query_row("SELECT 1 FROM entity_favorites WHERE entity_type=? AND entity_id=?", params![kind, id], |_| Ok(())).optional()?.is_some();
                if exists {
                    c.execute("DELETE FROM entity_favorites WHERE entity_type=? AND entity_id=?", params![kind, id])?;
                    return Ok(Some(false));
                }
                if c.query_row(&format!("SELECT 1 FROM {table} WHERE id=?"), [id], |_| Ok(())).optional()?.is_none() {
                    return Ok(None);
                }
                c.execute("INSERT INTO entity_favorites(entity_type,entity_id,created_at) VALUES(?,?,?)", params![kind, id, now])?;
                Ok(Some(true))
            })
            .await;
        match result {
            Ok(Some(favorite)) => Some(json_response(
                request,
                serde_json::to_vec(&json!({ "ok": true, "favorite": favorite, "entity_type": kind, "entity_id": id })).unwrap_or_default(),
            )),
            Ok(None) => Some(json_error(StatusCode::NOT_FOUND, "Entity not found")),
            Err(e) => {
                tracing::error!("favourite: {e}");
                Some(json_error(StatusCode::SERVICE_UNAVAILABLE, "The favourite could not be saved"))
            }
        }
    }

    pub(crate) async fn entity_favorite_status(&self, kind: &'static str, id: i64, request: &HeaderMap) -> Option<Response<Body>> {
        let read = self
            .db
            .read(move |c| Ok(c.query_row("SELECT 1 FROM entity_favorites WHERE entity_type=? AND entity_id=?", params![kind, id], |_| Ok(())).optional()?.is_some()))
            .await;
        match read {
            Ok(favorite) => Some(json_response(request, serde_json::to_vec(&json!({ "favorite": favorite })).unwrap_or_default())),
            Err(e) => {
                tracing::error!("favourite status: {e}");
                Some(json_error(StatusCode::SERVICE_UNAVAILABLE, "The favourite could not be read"))
            }
        }
    }
}

/// `str(value or '').strip()[:max]` for the plain cases: absent, null or text. The control characters 0x1c to 0x1f count as white space in
/// Python but not here, so text holding them is Python's to read.
fn clipped(v: Option<&Value>, max: usize) -> Option<String> {
    let text = match v {
        None | Some(Value::Null) => "",
        Some(Value::String(s)) => s.as_str(),
        _ => return None,
    };
    if text.chars().any(|c| ('\u{1c}'..='\u{1f}').contains(&c)) {
        return None;
    }
    Some(text.trim().chars().take(max).collect())
}

impl Native {
    pub(crate) async fn markers_list(&self, media_id: i64, request: &HeaderMap) -> Option<Response<Body>> {
        let read = self.db.read(move |c| rows_with(c, "SELECT * FROM scene_markers WHERE media_id=? ORDER BY position,id", [media_id])).await;
        match read {
            Ok(rows) => Some(json_response(request, serde_json::to_vec(&rows).unwrap_or_default())),
            Err(e) => {
                tracing::error!("markers: {e}");
                Some(json_error(StatusCode::SERVICE_UNAVAILABLE, "The markers could not be read"))
            }
        }
    }

    pub(crate) async fn marker_create(&self, media_id: i64, body: &[u8], request: &HeaderMap) -> Option<Response<Body>> {
        let v: Value = serde_json::from_slice(body).ok()?;
        let o = v.as_object()?;
        let position = non_negative(o.get("position"))?;
        let (title, note, tag) = (clipped(o.get("title"), 160)?, clipped(o.get("note"), 500)?, clipped(o.get("tag"), 80)?);
        let now = now_iso();
        let made = {
            let (title, note, tag, now) = (title.clone(), note.clone(), tag.clone(), now.clone());
            self.writer
                .write(move |c| {
                    if c.query_row("SELECT 1 FROM media WHERE id=?", [media_id], |_| Ok(())).optional()?.is_none() {
                        return Ok(None);
                    }
                    c.execute(
                        "INSERT INTO scene_markers(media_id,position,title,note,tag,created_at,updated_at) VALUES(?,?,?,?,?,?,?)",
                        params![media_id, position, title, note, tag, now, now],
                    )?;
                    Ok(Some(c.last_insert_rowid()))
                })
                .await
        };
        match made {
            Ok(Some(id)) => {
                let marker = json!({ "id": id, "media_id": media_id, "position": position, "title": title, "note": note, "tag": tag, "created_at": now, "updated_at": now });
                Some(json_response(request, serde_json::to_vec(&json!({ "ok": true, "marker": marker })).unwrap_or_default()))
            }
            Ok(None) => Some(json_error(StatusCode::NOT_FOUND, "Media not found")),
            Err(e) => {
                tracing::error!("marker create: {e}");
                Some(json_error(StatusCode::SERVICE_UNAVAILABLE, "The marker could not be saved"))
            }
        }
    }

    pub(crate) async fn marker_delete(&self, (media_id, marker_id): (i64, i64), request: &HeaderMap) -> Option<Response<Body>> {
        let now = now_iso();
        let done = self
            .writer
            .write(move |c| {
                let source: Option<Option<String>> =
                    c.query_row("SELECT source FROM scene_markers WHERE id=? AND media_id=?", params![marker_id, media_id], |r| r.get(0)).optional()?;
                c.execute("DELETE FROM scene_markers WHERE id=? AND media_id=?", params![marker_id, media_id])?;
                // A deliberate removal of a provider marker is remembered, so the automatic repair of an empty list does not bring it back.
                if let Some(source) = source {
                    if source.as_deref().filter(|s| !s.is_empty()).unwrap_or("local").to_lowercase() == "provider" {
                        c.execute(
                            "INSERT INTO provider_sync_state(provider,entity_type,last_sync_at,status,error,request_count,updated_at) VALUES(?,?,?,?,?,?,?) \
                             ON CONFLICT(provider,entity_type) DO UPDATE SET status=excluded.status,error=excluded.error,updated_at=excluded.updated_at",
                            params![
                                "theporndb",
                                format!("scene_markers:{media_id}"),
                                now,
                                "USER_MODIFIED",
                                "Provider markers were manually removed; use explicit refresh to re-import them.",
                                0,
                                now
                            ],
                        )?;
                    }
                }
                Ok(())
            })
            .await;
        match done {
            Ok(()) => Some(json_response(request, serde_json::to_vec(&json!({ "ok": true })).unwrap_or_default())),
            Err(e) => {
                tracing::error!("marker delete: {e}");
                Some(json_error(StatusCode::SERVICE_UNAVAILABLE, "The marker could not be removed"))
            }
        }
    }
}
