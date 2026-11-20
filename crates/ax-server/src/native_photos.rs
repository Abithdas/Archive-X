//! Photo files the front door can send itself: `GET /api/photos/{channel}/{message}/thumbnail|preview|image` (routes_library.py).
//!
//! The Photos page asks for dozens of these at once. When the file is already on disk there is nothing for Python to do, so it is sent from
//! here, with the headers and the ETag of Starlette's `FileResponse`. Anything else goes to Python, which owns the rest of the story: a
//! derivative that has to be rendered, an original that has to be fetched from Telegram, a row whose cache path has to be recorded, a
//! `Range` request, an unusual mime type.

use std::path::{Path, PathBuf};
use std::time::UNIX_EPOCH;

use ax_db::rusqlite::OptionalExtension;
use bytes::BytesMut;
use http_body_util::BodyExt;
use hyper::header::{HeaderValue, ACCEPT_RANGES, CACHE_CONTROL, CONTENT_LENGTH, CONTENT_TYPE, ETAG, IF_RANGE, LAST_MODIFIED, RANGE};
use hyper::{HeaderMap, Response, StatusCode};
use sha2::{Digest, Sha256};

use crate::auth::json_error;
use crate::native::Native;
use crate::native_stream::FileBody;
use crate::proxy::Body;

#[derive(Clone, Copy, PartialEq, Eq, Debug)]
pub(crate) enum PhotoFile {
    Thumbnail,
    Preview,
    Image,
}

impl PhotoFile {
    pub(crate) fn from_name(name: &str) -> Option<PhotoFile> {
        match name {
            "thumbnail" => Some(PhotoFile::Thumbnail),
            "preview" => Some(PhotoFile::Preview),
            "image" => Some(PhotoFile::Image),
            _ => None,
        }
    }
}

/// `photo_cache.photo_key`.
fn photo_key(channel: &str, message: i64) -> String {
    let digest = Sha256::digest(format!("{channel}:{message}").as_bytes());
    digest.iter().map(|b| format!("{b:02x}")).collect()
}

/// What Starlette's `FileResponse` puts in `ETag`: the MD5 of `"<st_mtime as a Python float>-<st_size>"`.
fn etag_of(meta: &std::fs::Metadata) -> Option<String> {
    let since = meta.modified().ok()?.duration_since(UNIX_EPOCH).ok()?;
    let mtime = since.as_secs() as f64 + f64::from(since.subsec_nanos()) * 1e-9; // CPython: sec + nsec * 1e-9
    let digest = md5::compute(format!("{:?}-{}", mtime, meta.len()));
    Some(format!("\"{digest:x}\""))
}

/// A cached file counts once it has more than `min` bytes (the thumbnail and the preview must be over 512).
fn file_over(path: &Path, min: u64) -> Option<std::fs::Metadata> {
    std::fs::metadata(path).ok().filter(|m| m.is_file() && m.len() > min)
}

/// `photo_cache.resolve_database_cache_path` for a path inside the app's own trees; `None` for anything else (a local folder the user
/// added, or a row that points elsewhere), which Python decides.
fn resolve_in_roots(value: &str, user_root: &Path, cache_root: &Path) -> Option<PathBuf> {
    let text = value.trim();
    if text.is_empty() || text.starts_with('~') {
        return None;
    }
    let mut candidate = PathBuf::from(text);
    if !candidate.is_absolute() {
        candidate = user_root.join(candidate);
    }
    let resolved = ax_core::safe_paths::realpath(&candidate).ok()?;
    let (user, cache) = (ax_core::safe_paths::realpath(user_root).ok()?, ax_core::safe_paths::realpath(cache_root).ok()?);
    (resolved.starts_with(&user) || resolved.starts_with(&cache)).then_some(resolved)
}

impl Native {
    /// The user-data and cache folders, asked of the backend once.
    async fn photo_roots(&self) -> Option<(PathBuf, PathBuf)> {
        if let Some(r) = self.photo_roots.lock().unwrap().clone() {
            return Some(r);
        }
        let v = self.proxy.internal_json("/internal/provider-keys").await.ok()?;
        let user = PathBuf::from(v.get("user_data_dir")?.as_str().filter(|s| !s.is_empty())?);
        let cache = PathBuf::from(v.get("cache_dir")?.as_str().filter(|s| !s.is_empty())?);
        *self.photo_roots.lock().unwrap() = Some((user.clone(), cache.clone()));
        Some((user, cache))
    }

    /// `None`: Python answers this request.
    pub(crate) async fn photo_file(&self, kind: PhotoFile, channel: i64, message: i64, headers: &HeaderMap) -> Option<Response<Body>> {
        if headers.contains_key(RANGE) || headers.contains_key(IF_RANGE) {
            return None;
        }
        let (user_root, cache_root) = self.photo_roots().await?;
        let channel_text = channel.to_string();
        let ch = channel_text.clone();
        let row = self
            .db
            .read(move |c| {
                let visible: Option<(Option<String>, Option<String>)> = c
                    .query_row("SELECT mime_type,local_path FROM photo_library_items WHERE channel_id=?1 AND message_id=?2", (&ch, message), |r| Ok((r.get(0)?, r.get(1)?)))
                    .optional()?;
                let hidden = visible.is_none()
                    && c.query_row("SELECT 1 FROM main.photo_library_items WHERE channel_id=?1 AND message_id=?2 LIMIT 1", (&ch, message), |_| Ok(()))
                        .optional()?
                        .is_some();
                Ok((visible, hidden))
            })
            .await
            .map_err(|e| tracing::warn!("photo file lookup: {e}"))
            .ok()?;
        let (mime, local_path) = match row {
            // content_guard: a photo that exists but belongs to the other mode is "not there" before the route is reached
            (None, true) => return Some(json_error(StatusCode::NOT_FOUND, "Not found")),
            (None, false) => return Some(json_error(StatusCode::NOT_FOUND, "Photo is not in the Archive X photo index")),
            (Some(r), _) => r,
        };
        let key = photo_key(&channel_text, message);
        let photos = cache_root.join("photo_library");
        let blocking = move || -> Option<(PathBuf, &'static str, &'static str, bool)> {
            match kind {
                PhotoFile::Thumbnail => {
                    // the 640 px thumbnail, once it exists and is more than a stub
                    let thumb = photos.join("thumbnails").join(format!("{key}.w640.jpg"));
                    file_over(&thumb, 512)?;
                    Some((thumb, "image/jpeg", "private, max-age=3600", false))
                }
                PhotoFile::Preview | PhotoFile::Image => {
                    // A row with no recorded cache path is one Python fills in (it writes the mapping): not here.
                    let original = resolve_in_roots(local_path.as_deref().unwrap_or(""), &user_root, &cache_root)?;
                    let original_meta = file_over(&original, 0)?;
                    if kind == PhotoFile::Image {
                        let mime = mime.as_deref().filter(|m| m.starts_with("image/"))?;
                        let mime: &'static str = match mime {
                            "image/jpeg" => "image/jpeg",
                            "image/png" => "image/png",
                            "image/webp" => "image/webp",
                            "image/gif" => "image/gif",
                            _ => return None, // other types: Python
                        };
                        return Some((original, mime, "private, max-age=3600", true));
                    }
                    let preview = photos.join("previews").join(format!("{key}.jpg"));
                    let preview_meta = file_over(&preview, 512)?;
                    // the preview must not be older than the original
                    (preview_meta.modified().ok()? >= original_meta.modified().ok()?).then_some(())?;
                    Some((preview, "image/jpeg", "private, max-age=86400", true))
                }
            }
        };
        let (path, content_type, cache, inline) = tokio::task::spawn_blocking(blocking).await.ok()??;
        send_photo(&path, content_type, cache, inline).await
    }
}

/// Starlette's `FileResponse` for a whole file: type, length, `Accept-Ranges`, `Last-Modified`, `ETag`, plus the route's own headers.
async fn send_photo(path: &Path, content_type: &'static str, cache: &'static str, inline: bool) -> Option<Response<Body>> {
    let file = tokio::fs::File::open(path).await.ok()?;
    let meta = file.metadata().await.ok()?;
    let size = meta.len();
    let mtime = meta.modified().ok()?.duration_since(UNIX_EPOCH).ok()?.as_secs();
    let mut resp = Response::builder()
        .status(StatusCode::OK)
        .header(CACHE_CONTROL, cache)
        .header("x-content-type-options", "nosniff")
        .header(CONTENT_TYPE, content_type)
        .header(ACCEPT_RANGES, "bytes")
        .header(CONTENT_LENGTH, size)
        .header(LAST_MODIFIED, httpdate::fmt_http_date(UNIX_EPOCH + std::time::Duration::from_secs(mtime)))
        .header(ETAG, etag_of(&meta)?)
        .body(FileBody { file, remaining: size, buf: BytesMut::new() }.boxed())
        .ok()?;
    if inline {
        resp.headers_mut().insert("content-disposition", HeaderValue::from_static("inline"));
    }
    Some(resp)
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn the_key_is_the_sha256_of_channel_colon_message() {
        // python: hashlib.sha256(b"-1001:1").hexdigest()
        assert_eq!(photo_key("-1001", 1).len(), 64);
        assert_eq!(photo_key("-1001", 1), photo_key("-1001", 1));
        assert_ne!(photo_key("-1001", 1), photo_key("-1001", 2));
    }

    #[test]
    fn paths_are_resolved_inside_the_app_trees_only() {
        let dir = tempfile::tempdir().unwrap();
        let user = dir.path().join("data");
        let cache = user.join("cache");
        std::fs::create_dir_all(&cache).unwrap();
        assert!(resolve_in_roots("cache/photo_library/originals/x.bin", &user, &cache).is_some(), "relative to the user-data folder");
        assert!(resolve_in_roots(&cache.join("a.bin").display().to_string(), &user, &cache).is_some(), "absolute but inside");
        assert!(resolve_in_roots("../elsewhere/x.bin", &user, &cache).is_none(), "leaves the tree");
        assert!(resolve_in_roots("/etc/passwd", &user, &cache).is_none());
        assert!(resolve_in_roots("", &user, &cache).is_none());
        assert!(resolve_in_roots("~/x", &user, &cache).is_none(), "Python expands ~; left to Python");
    }
}
