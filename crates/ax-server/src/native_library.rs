//! `GET /api/library`: the Videos page's list, answered by the front door (a port of `library()` in telegram_archive/web/routes_settings.py).
//!
//! Handled here: the structured filters (channels, resolution, studio, performer, tag, dates, favourites, one message id), the eight sort
//! orders and paging. Declined (the request goes on to Python): any search text (`q`, which uses the full-text index and its LIKE fallbacks) and
//! any parameter written in a way Python's `int()` or `str` handling would read differently from the plain forms handled here.

use std::collections::HashMap;

use ax_db::rusqlite::types::Value as Sql;
use ax_db::rusqlite::Connection;
use hyper::{HeaderMap, Response, StatusCode};
use serde_json::{Map, Value};

use crate::auth::json_error;
use crate::native::{csv_list, merged_performers, name_lists, query_params, rows_with, strings, truthy, Native};
use crate::optimize::json_response;
use crate::proxy::Body;

struct Request {
    page: i64,
    per: i64,
    channels: Vec<String>,
    sort: &'static str,
    resolution: String,
    studio: String,
    performer: String,
    tag: String,
    date_from: String,
    date_to: String,
    favorites: bool,
    message_id: Option<i64>,
}

/// A plain integer as Python's `int()` reads it (optional minus, ASCII digits); anything else is Python's to judge.
fn plain_int(v: &str) -> Option<i64> {
    let digits = v.strip_prefix('-').unwrap_or(v);
    if digits.is_empty() || digits.len() > 9 || !digits.bytes().all(|b| b.is_ascii_digit()) {
        return None;
    }
    v.parse().ok()
}

const SORTS: &[(&str, &str)] = &[
    ("date_desc", "COALESCE(NULLIF(m.upload_date,''),m.created_at) DESC, m.id DESC"),
    ("date_asc", "COALESCE(NULLIF(m.upload_date,''),m.created_at) ASC, m.id ASC"),
    ("size_desc", "m.size DESC, m.id DESC"),
    ("dur_desc", "m.duration DESC, m.id DESC"),
    ("name_asc", "m.filename COLLATE NOCASE ASC, m.id ASC"),
    (
        "studio_asc",
        "(SELECT MIN(s.name) FROM video_studios vs JOIN studios s ON s.id=vs.studio_id WHERE vs.video_id=m.id) COLLATE NOCASE ASC, m.upload_date DESC, m.id DESC",
    ),
    (
        "performer_asc",
        "(SELECT MIN(p.name) FROM video_performers vp JOIN performers p ON p.id=vp.performer_id WHERE vp.video_id=m.id) COLLATE NOCASE ASC, m.upload_date DESC, m.id DESC",
    ),
    (
        "tag_asc",
        "(SELECT MIN(t.name) FROM video_tags vt JOIN tags t ON t.id=vt.tag_id WHERE vt.video_id=m.id) COLLATE NOCASE ASC, m.upload_date DESC, m.id DESC",
    ),
];

fn parse(params: &HashMap<String, String>) -> Option<Request> {
    if params.get("q").map(|q| !q.trim().is_empty()).unwrap_or(false) {
        return None; // searching: the full-text index and the LIKE fallbacks stay in Python for now
    }
    let page = match params.get("page") {
        None => 0,
        Some(v) => plain_int(v)?.max(0),
    };
    let per = match params.get("per") {
        None => 48,
        Some(v) => plain_int(v)?.clamp(1, 48),
    };
    // Python splits on "," and strips each piece with str.strip(); non-ASCII text is left to Python.
    let raw_channels = params.get("channels").map(String::as_str).unwrap_or("");
    if !raw_channels.is_ascii() {
        return None;
    }
    let channels: Vec<String> = raw_channels.split(',').map(|x| x.trim()).filter(|x| !x.is_empty()).map(str::to_string).collect();
    let sort_name = params.get("sort").map(String::as_str).unwrap_or("date_desc");
    let sort = SORTS.iter().find(|(n, _)| *n == sort_name).map(|(n, _)| *n).unwrap_or("date_desc");
    let text = |k: &str| params.get(k).map(|v| v.trim().to_string()).unwrap_or_default();
    for k in ["studio", "performer", "tag"] {
        if !text(k).is_ascii() {
            return None; // str.strip() and LIKE both fold non-ASCII differently from this side
        }
    }
    let message = text("message_id");
    let message_id = {
        let digits = message.trim_start_matches('-');
        if !message.is_ascii() || message.starts_with("--") {
            return None;
        }
        if !digits.is_empty() && digits.bytes().all(|b| b.is_ascii_digit()) {
            Some(plain_int(&message)?)
        } else {
            None
        }
    };
    Some(Request {
        page,
        per,
        channels,
        sort,
        resolution: params.get("resolution").cloned().unwrap_or_default(),
        studio: text("studio"),
        performer: text("performer"),
        tag: text("tag"),
        date_from: params.get("date_from").cloned().unwrap_or_default(),
        date_to: params.get("date_to").cloned().unwrap_or_default(),
        favorites: params.get("favorites").map(String::as_str) == Some("1"),
        message_id,
    })
}

fn like(v: &str) -> Sql {
    Sql::Text(format!("%{v}%"))
}

/// The WHERE clause and its values, in the order the Python handler builds them.
fn where_clause(r: &Request) -> (String, Vec<Sql>) {
    let mut w: Vec<String> = vec!["1=1".into()];
    let mut p: Vec<Sql> = Vec::new();
    if !r.channels.is_empty() {
        w.push(format!("m.source_channel IN ({})", vec!["?"; r.channels.len()].join(",")));
        p.extend(r.channels.iter().map(|c| Sql::Text(c.clone())));
    }
    let res = match r.resolution.as_str() {
        "2160" => Some("m.height>=2160"),
        "1440" => Some("m.height>=1440 AND m.height<2160"),
        "1080" => Some("m.height>=1080 AND m.height<1440"),
        "720" => Some("m.height>=720 AND m.height<1080"),
        "480" => Some("m.height>0 AND m.height<720"),
        _ => None,
    };
    if let Some(cond) = res {
        w.push(cond.into());
    }
    if !r.studio.is_empty() {
        w.push("m.id IN (SELECT vs.video_id FROM video_studios vs JOIN studios s ON s.id=vs.studio_id WHERE s.name LIKE ?)".into());
        p.push(like(&r.studio));
    }
    if !r.performer.is_empty() {
        w.push(
            "(m.id IN (SELECT vp.video_id FROM video_performers vp JOIN performers p ON p.id=vp.performer_id WHERE p.name LIKE ?) \
             OR m.id IN (SELECT lvp.video_id FROM local_video_performers lvp JOIN performers p ON p.id=lvp.performer_id WHERE p.name LIKE ?))"
                .into(),
        );
        p.push(like(&r.performer));
        p.push(like(&r.performer));
    }
    if !r.tag.is_empty() {
        w.push("m.id IN (SELECT vt.video_id FROM video_tags vt JOIN tags t ON t.id=vt.tag_id WHERE t.name LIKE ?)".into());
        p.push(like(&r.tag));
    }
    let eff = "COALESCE(NULLIF(m.upload_date,''),m.created_at)";
    if !r.date_from.is_empty() {
        w.push(format!("({eff} IS NULL OR {eff} >= ?)"));
        p.push(Sql::Text(r.date_from.clone()));
    }
    if !r.date_to.is_empty() {
        w.push(format!("({eff} IS NULL OR {eff} <= ?)"));
        p.push(Sql::Text(format!("{}T99", r.date_to)));
    }
    if r.favorites {
        w.push("m.favorite = 1".into());
    }
    if let Some(id) = r.message_id {
        w.push("m.source_message_id = ?".into());
        p.push(Sql::Integer(id));
    }
    (w.join(" AND "), p)
}

fn library_payload(c: &Connection, r: &Request) -> ax_db::rusqlite::Result<Value> {
    let (w, params) = where_clause(r);
    let order = SORTS.iter().find(|(n, _)| *n == r.sort).map(|(_, o)| *o).unwrap_or(SORTS[0].1);
    let total: i64 = c.query_row(&format!("SELECT COUNT(*) n FROM archive_x_library_media m WHERE {w}"), ax_db::rusqlite::params_from_iter(params.iter()), |row| row.get(0))?;
    // Page the media ids first, then add the name lists for just those (as the Python handler does).
    let mut page_params = params.clone();
    page_params.push(Sql::Integer(r.per));
    page_params.push(Sql::Integer(r.page * r.per));
    let ids: Vec<i64> = {
        let mut stmt = c.prepare(&format!("SELECT m.id FROM archive_x_library_media m WHERE {w} ORDER BY {order} LIMIT ? OFFSET ?"))?;
        let rows = stmt.query_map(ax_db::rusqlite::params_from_iter(page_params.iter()), |row| row.get::<_, i64>(0))?;
        rows.collect::<Result<_, _>>()?
    };
    let rows = if ids.is_empty() {
        Vec::new()
    } else {
        let sql = format!(
            "SELECT m.*, c.title AS channel_title, {} FROM archive_x_library_media m LEFT JOIN channels c ON c.telegram_id=m.source_channel WHERE m.id IN ({}) GROUP BY m.id ORDER BY {order}",
            name_lists("performers", "studios", "tags"),
            vec!["?"; ids.len()].join(",")
        );
        rows_with(c, &sql, ax_db::rusqlite::params_from_iter(ids.iter()))?
    };
    let items: Vec<Value> = rows.into_iter().map(format_row).collect();
    let ready = items.iter().filter(|i| truthy(i.get("library_ready"))).count();
    Ok(serde_json::json!({
        "total": total, "ready_count": ready, "page": r.page, "per": r.per, "items": items,
        "catalogued_only": true, "published_only": false, "snapshot_ready_only": false
    }))
}

/// `fmt_row` of the Python handler, key for key and in the same order.
fn format_row(mut d: Map<String, Value>) -> Value {
    let performers = merged_performers(&d);
    let local = csv_list(d.get("local_performers"));
    let studios = csv_list(d.get("studios"));
    let tags = csv_list(d.get("tags"));
    d.insert("performers".into(), strings(performers));
    d.insert("local_performers".into(), strings(local));
    d.insert("studios".into(), strings(studios.clone()));
    d.insert("tags".into(), strings(tags));
    d.insert("studio".into(), Value::String(studios.first().cloned().unwrap_or_default()));
    let text = |d: &Map<String, Value>, k: &str| d.get(k).and_then(Value::as_str).unwrap_or("").to_string();
    let filename = text(&d, "filename");
    d.insert("telegram_filename".into(), Value::String(filename.clone()));
    d.insert("api_title".into(), Value::String(text(&d, "title").trim().to_string()));
    let source = d.get("metadata_source").cloned();
    let cached = truthy(source.as_ref()) && !matches!(source.as_ref().and_then(Value::as_str), Some("none") | Some(""));
    d.insert("metadata_cached".into(), Value::Bool(cached));
    let int_of = |v: Option<&Value>| v.and_then(Value::as_i64).unwrap_or(0);
    let media_id = int_of(d.get("id"));
    d.insert("media_id".into(), Value::from(media_id));
    let channel = match d.get("source_channel") {
        Some(Value::String(s)) => s.clone(),
        Some(Value::Null) | None => String::new(),
        Some(other) => other.to_string(),
    };
    d.insert("_cid".into(), Value::String(channel));
    let date = ["upload_date", "created_at"].iter().map(|k| text(&d, k)).find(|v| !v.is_empty()).unwrap_or_default();
    d.insert("date".into(), Value::String(date));
    let message = int_of(d.get("source_message_id"));
    let id = if message != 0 { message } else { media_id };
    d.insert("id".into(), Value::from(id));
    let title = text(&d, "title");
    let name = if !title.is_empty() {
        title
    } else if !filename.is_empty() {
        filename
    } else {
        format!("video {id}")
    };
    d.insert("name".into(), Value::String(name));
    let thumb = if truthy(d.get("thumbnail_message_id")) {
        d["thumbnail_message_id"].clone()
    } else if message != 0 {
        Value::from(message)
    } else {
        Value::from(id)
    };
    d.insert("thumbnail_id".into(), thumb);
    Value::Object(d)
}

impl Native {
    pub(crate) async fn library_list(&self, query: Option<&str>, request: &HeaderMap) -> Option<Response<Body>> {
        let req = parse(&query_params(query)?)?;
        match self.db.read(move |c| library_payload(c, &req)).await {
            Ok(payload) => Some(json_response(request, serde_json::to_vec(&payload).unwrap_or_default())),
            Err(e) => {
                tracing::error!("library: {e}");
                Some(json_error(StatusCode::SERVICE_UNAVAILABLE, "The library could not be read"))
            }
        }
    }
}

impl Native {
    /// `GET /api/tags` without a search text: every tag that has a video in the library, most used first (`brief=1` keeps three fields).
    pub(crate) async fn tags_list(&self, query: Option<&str>, request: &HeaderMap) -> Option<Response<Body>> {
        let params = query_params(query)?;
        if params.get("q").map(|q| !q.trim().is_empty()).unwrap_or(false) {
            return None;
        }
        let brief = params.get("brief").map(String::as_str) == Some("1");
        let read = self.db.read(move |c| {
            let rows = rows_with(
                c,
                "SELECT t.*, COUNT(DISTINCT CASE WHEN vt.video_id IN (SELECT id FROM archive_x_library_media) THEN vt.video_id END) video_count \
                 FROM tags t LEFT JOIN video_tags vt ON vt.tag_id=t.id GROUP BY t.id HAVING video_count > 0 ORDER BY video_count DESC,t.name",
                [],
            )?;
            Ok(rows)
        });
        match read.await {
            Ok(rows) => {
                let items: Vec<Value> = rows
                    .into_iter()
                    .map(|d| {
                        if brief {
                            let mut m = Map::new();
                            for k in ["id", "name", "video_count"] {
                                m.insert(k.into(), d.get(k).cloned().unwrap_or(Value::Null));
                            }
                            Value::Object(m)
                        } else {
                            Value::Object(d)
                        }
                    })
                    .collect();
                Some(json_response(request, serde_json::to_vec(&items).unwrap_or_default()))
            }
            Err(e) => {
                tracing::error!("tags: {e}");
                Some(json_error(StatusCode::SERVICE_UNAVAILABLE, "The tags could not be read"))
            }
        }
    }
}

impl Native {
    /// `GET /api/studios/{id}/metadata`: the studio row and what each provider told us about it.
    pub(crate) async fn studio_metadata(&self, id: i64, request: &HeaderMap) -> Option<Response<Body>> {
        let read = self.db.read(move |c| {
            let Some(entity) = rows_with(c, "SELECT * FROM studios WHERE id=?", [id])?.into_iter().next() else { return Ok(None) };
            let providers = rows_with(
                c,
                "SELECT provider,provider_id,normalized,fetched_at,updated_at FROM entity_metadata WHERE entity_type='studio' AND entity_id=? ORDER BY provider",
                [id],
            )?;
            Ok(Some(serde_json::json!({ "entity": entity, "providers": providers })))
        });
        match read.await {
            Ok(Some(v)) => Some(json_response(request, serde_json::to_vec(&v).unwrap_or_default())),
            Ok(None) => Some(json_error(StatusCode::NOT_FOUND, "Studio not found")),
            Err(e) => {
                tracing::error!("studio metadata: {e}");
                Some(json_error(StatusCode::SERVICE_UNAVAILABLE, "The studio could not be read"))
            }
        }
    }
}
