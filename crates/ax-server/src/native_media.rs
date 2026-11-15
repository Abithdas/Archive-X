//! Library favourites: `POST /api/library/{id}/favorite` and `POST /api/library/bulk-favorite` (routes_library.py).
//!
//! Both write the `favorite` column of `media` (ADR 0007, second amendment). Anything the Python handler would answer with an error or
//! read in a special way (a body that is not an object, ids written as text, more than 500 ids) is declined, so Python answers it as before.

use std::collections::HashSet;

use ax_db::rusqlite::{params_from_iter, OptionalExtension};
use hyper::{HeaderMap, Response, StatusCode};
use serde_json::{json, Value};

use crate::auth::json_error;
use crate::native::Native;
use crate::native_play::now_iso;
use crate::optimize::json_response;
use crate::proxy::Body;

const MAX_IDS: usize = 500;

impl Native {
    /// Flip the favourite mark of one video; 404 when the row is gone.
    pub(crate) async fn library_favorite_toggle(&self, media_id: i64, request: &HeaderMap) -> Option<Response<Body>> {
        let result = self
            .writer
            .write(move |c| {
                c.query_row("UPDATE media SET favorite = CASE WHEN favorite THEN 0 ELSE 1 END WHERE id=? RETURNING favorite", [media_id], |r| r.get::<_, Option<i64>>(0))
                    .optional()
            })
            .await;
        match result {
            Ok(Some(value)) => Some(json_response(request, serde_json::to_vec(&json!({ "ok": true, "favorite": value.unwrap_or(0) != 0 })).unwrap_or_default())),
            Ok(None) => Some(json_error(StatusCode::NOT_FOUND, "Not found")),
            Err(e) => {
                tracing::error!("library favourite: {e}");
                Some(json_error(StatusCode::SERVICE_UNAVAILABLE, "The favourite could not be saved"))
            }
        }
    }

    /// Set the favourite mark of up to 500 videos at once. Ids that do not exist are reported, not an error.
    pub(crate) async fn library_bulk_favorite(&self, body: &[u8], request: &HeaderMap) -> Option<Response<Body>> {
        let Ok(Value::Object(obj)) = serde_json::from_slice::<Value>(body) else { return None };
        let Some(Value::Array(raw)) = obj.get("media_ids") else { return None };
        let Some(Value::Bool(favorite)) = obj.get("favorite") else { return None };
        let favorite = *favorite;
        // Python takes int(value) of anything; only plain JSON integers are handled here.
        let mut requested = Vec::new();
        for v in raw {
            requested.push(v.as_i64()?); // floats and text give None
        }
        let mut seen = HashSet::new();
        let ids: Vec<i64> = requested.into_iter().filter(|v| *v > 0 && seen.insert(*v)).collect();
        if ids.is_empty() || ids.len() > MAX_IDS || raw.len() > MAX_IDS {
            return None; // the 400 and 413 answers are Python's
        }
        let now = now_iso();
        let wanted = ids.clone();
        let result = self
            .writer
            .write(move |c| {
                let marks = vec!["?"; wanted.len()].join(",");
                let found: HashSet<i64> = {
                    let mut stmt = c.prepare(&format!("SELECT id FROM media WHERE id IN ({marks})"))?;
                    let rows = stmt.query_map(params_from_iter(wanted.iter()), |r| r.get::<_, i64>(0))?;
                    rows.collect::<Result<_, _>>()?
                };
                // SQLite walks an IN list in key order, so Python lists the found ids ascending
                let mut valid: Vec<i64> = found.into_iter().collect();
                valid.sort_unstable();
                if !valid.is_empty() {
                    let marks = vec!["?"; valid.len()].join(",");
                    let mut args: Vec<ax_db::rusqlite::types::Value> = vec![i64::from(favorite).into(), now.into()];
                    args.extend(valid.iter().map(|v| (*v).into()));
                    c.execute(&format!("UPDATE media SET favorite=?, updated_at=? WHERE id IN ({marks})"), params_from_iter(args))?;
                }
                Ok(valid)
            })
            .await;
        match result {
            Ok(valid) => {
                let set: HashSet<i64> = valid.iter().copied().collect();
                let missing: Vec<i64> = ids.iter().copied().filter(|v| !set.contains(v)).collect();
                let payload = json!({ "ok": true, "favorite": favorite, "updated": valid.len(), "media_ids": valid, "missing_ids": missing });
                Some(json_response(request, serde_json::to_vec(&payload).unwrap_or_default()))
            }
            Err(e) => {
                tracing::error!("bulk favourite: {e}");
                Some(json_error(StatusCode::SERVICE_UNAVAILABLE, "The favourites could not be saved"))
            }
        }
    }
}
