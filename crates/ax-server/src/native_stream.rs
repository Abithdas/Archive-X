//! Video files the front door can send itself: `GET /stream/{folder}/{video}` for a local-folder video and
//! `GET /stream-cached/{channel}/{video}` for a Telegram video already saved whole on disk (routes_playback.py).
//!
//! These two are plain files, so there is nothing for the Python playback engine to do: no Telegram download, no read-ahead,
//! no playhead. Sending them from here takes Python off the path of every byte. Telegram videos that are not saved whole
//! (`/stream/{channel}/{video}` for a real channel) stay in Python, which owns the block cache and read-ahead.
//!
//! The rules are Python's: the SFW / NSFW mode hides a video of the other mode with 404 (core/content_guard.py), a local file is
//! sent only while it still lies inside its folder (core/safe_paths.py `inside`), and a cached file only once it is complete
//! (both writers rename a finished `.partial` into place). Anything unusual (several ranges, `If-Range`, a row or file that is
//! missing) is declined, so Python answers it exactly as before.

use std::io::SeekFrom;
use std::path::{Path, PathBuf};
use std::pin::Pin;
use std::task::{Context, Poll};
use std::time::UNIX_EPOCH;

use ax_db::rusqlite::OptionalExtension;
use bytes::{Bytes, BytesMut};
use http_body_util::{BodyExt, Empty};
use hyper::body::Frame;
use hyper::header::{HeaderValue, ACCEPT_RANGES, CACHE_CONTROL, CONTENT_LENGTH, CONTENT_RANGE, CONTENT_TYPE, ETAG, LAST_MODIFIED, RANGE, VARY};
use hyper::{HeaderMap, Method, Response, StatusCode};
use tokio::io::{AsyncRead, AsyncSeekExt, ReadBuf};

use crate::auth::json_error;
use crate::native::Native;
use crate::proxy::Body;

/// Local folders have channel ids below this (engines/local_scanner.py `LOCAL_BASE`).
pub(crate) const LOCAL_BASE: i64 = -9_000_000_000_000;
/// Bytes read from disk per frame sent.
const CHUNK: usize = 256 * 1024;

/// Which of the two file routes a path is for.
#[derive(Clone, Copy, PartialEq, Eq, Debug)]
pub(crate) enum FileKind {
    Local,
    Cached,
}

/// `/stream/{local folder}/{video}` or `/stream-cached/{channel}/{video}` with two plain integers; anything else is not ours.
pub(crate) fn match_path(path: &str) -> Option<(FileKind, i64, i64)> {
    let (kind, rest) = match path.strip_prefix("/stream/") {
        Some(r) => (FileKind::Local, r),
        None => (FileKind::Cached, path.strip_prefix("/stream-cached/")?),
    };
    let (c, m) = rest.split_once('/')?;
    let int = |v: &str| {
        let digits = v.strip_prefix('-').unwrap_or(v);
        (!digits.is_empty() && digits.len() <= 18 && digits.bytes().all(|b| b.is_ascii_digit())).then(|| v.parse::<i64>().ok()).flatten()
    };
    let (channel, message) = (int(c)?, int(m)?);
    // A real Telegram channel on /stream needs the playback engine: Python's.
    if kind == FileKind::Local && channel >= LOCAL_BASE {
        return None;
    }
    Some((kind, channel, message))
}

/// One `bytes=` range, Python's `parse_range` / Starlette's reading of it. `Ok(None)`: no header, the whole file.
/// `Err(true)`: unsatisfiable (416). `Err(false)`: a form this side does not handle (several ranges, garbage): Python's.
pub(crate) fn parse_range(header: Option<&str>, size: u64) -> Result<Option<(u64, u64)>, bool> {
    let Some(raw) = header else { return Ok(None) };
    let spec = raw.trim().strip_prefix("bytes=").ok_or(false)?.trim();
    if spec.contains(',') {
        return Err(false);
    }
    let (a, b) = spec.split_once('-').ok_or(false)?;
    let num = |s: &str| -> Result<Option<u64>, bool> {
        let s = s.trim();
        if s.is_empty() {
            Ok(None)
        } else if s.bytes().all(|c| c.is_ascii_digit()) && s.len() <= 19 {
            s.parse::<u64>().map(Some).map_err(|_| false)
        } else {
            Err(false)
        }
    };
    let (start, end) = match (num(a)?, num(b)?) {
        (Some(s), Some(e)) if s <= e => (s, e.min(size.saturating_sub(1))),
        (Some(_), Some(_)) => return Err(false),
        (Some(s), None) => (s, size.saturating_sub(1)),
        (None, Some(n)) if n > 0 => (size.saturating_sub(n), size.saturating_sub(1)),
        _ => return Err(false),
    };
    if size == 0 || start >= size {
        return Err(true);
    }
    Ok(Some((start, end)))
}

/// engines/local_videos.py `mime_of`.
fn mime_of(path: &Path) -> &'static str {
    match path.extension().and_then(|e| e.to_str()).map(str::to_ascii_lowercase).as_deref() {
        Some("mp4" | "m4v") => "video/mp4",
        Some("mov") => "video/quicktime",
        Some("webm") => "video/webm",
        Some("mkv") => "video/x-matroska",
        Some("avi") => "video/x-msvideo",
        Some("wmv") => "video/x-ms-wmv",
        Some("ts" | "m2ts") => "video/mp2t",
        _ => mime_guess::from_path(path).first_raw().unwrap_or("application/octet-stream"),
    }
}

/// playback/stream_server.py `content_disposition`: an ASCII name plus the exact one, percent-encoded.
fn content_disposition(name: &str) -> String {
    let ascii: String = name.chars().map(|c| if c.is_ascii_alphanumeric() || "._ ()[]-".contains(c) { c } else { '_' }).take(150).collect();
    let ascii = if ascii.is_empty() { "video.mp4".to_string() } else { ascii };
    let quoted = percent_encoding::utf8_percent_encode(&name.chars().take(200).collect::<String>(), percent_encoding::NON_ALPHANUMERIC).to_string();
    format!("inline; filename=\"{ascii}\"; filename*=UTF-8''{quoted}")
}

/// `inside(root, file)` from core/safe_paths.py: the real path of `file`, only if it lies in the real path of `root`.
fn inside(root: &Path, file: &Path) -> Option<PathBuf> {
    let root = std::fs::canonicalize(root).ok()?;
    let real = std::fs::canonicalize(file).ok()?;
    (real != root && real.starts_with(&root) && real.is_file()).then_some(real)
}

/// What the database says about one video, read in one go.
struct Row {
    /// The video exists in `main.media` (whatever the mode).
    exists: bool,
    /// ...and the current mode shows it (the filtered `media` view).
    visible: bool,
    /// For a local video: its file and its folder.
    local: Option<(PathBuf, PathBuf)>,
}

impl Native {
    /// The folder Python keeps finished playback copies in (config.PLAYBACK_ASSET_DIR), asked once.
    async fn asset_dir(&self) -> Option<PathBuf> {
        if let Some(p) = self.asset_dir.lock().unwrap().clone() {
            return Some(p);
        }
        let v = self.proxy.internal_json("/internal/provider-keys").await.ok()?;
        let p = PathBuf::from(v.get("playback_asset_dir")?.as_str().filter(|s| !s.is_empty())?);
        *self.asset_dir.lock().unwrap() = Some(p.clone());
        Some(p)
    }

    async fn video_row(&self, kind: FileKind, channel: i64, message: i64) -> Option<Row> {
        self.db
            .read(move |c| {
                let ch = channel.to_string();
                let exists = c
                    .query_row("SELECT 1 FROM main.media WHERE source_channel=?1 AND source_message_id=?2 LIMIT 1", (&ch, message), |_| Ok(()))
                    .optional()?
                    .is_some();
                let visible = exists
                    && c.query_row("SELECT 1 FROM media WHERE source_channel=?1 AND source_message_id=?2 LIMIT 1", (&ch, message), |_| Ok(()))
                        .optional()?
                        .is_some();
                let local = if kind == FileKind::Local {
                    let file: Option<String> =
                        c.query_row("SELECT path FROM main.local_video_files WHERE channel_id=?1 AND message_id=?2", (&ch, message), |r| r.get(0)).optional()?;
                    let root: Option<String> = c.query_row("SELECT path FROM main.local_sources WHERE id=?1", [LOCAL_BASE - channel], |r| r.get(0)).optional()?;
                    file.zip(root).map(|(f, r)| (PathBuf::from(f), PathBuf::from(r)))
                } else {
                    None
                };
                Ok(Row { exists, visible, local })
            })
            .await
            .map_err(|e| tracing::warn!("file stream lookup: {e}"))
            .ok()
    }

    /// `None`: Python answers this request.
    pub(crate) async fn stream_file(&self, kind: FileKind, channel: i64, message: i64, method: &Method, headers: &HeaderMap) -> Option<Response<Body>> {
        // A form of Range this side does not read (several ranges, garbage) or a conditional range: Python's, before anything is opened.
        if headers.contains_key(hyper::header::IF_RANGE) || parse_range(headers.get(RANGE).and_then(|v| v.to_str().ok()), u64::MAX) == Err(false) {
            return None;
        }
        let row = self.video_row(kind, channel, message).await?;
        // core/content_guard.py: a video that exists but belongs to the other mode is "not there".
        if row.exists && !row.visible {
            return Some(json_error(StatusCode::NOT_FOUND, "Not Found"));
        }
        let (path, name, mime, cache, extra): (PathBuf, String, &'static str, &'static str, (&'static str, &'static str)) = match kind {
            FileKind::Local => {
                let (file, root) = row.local?;
                let real = tokio::task::spawn_blocking(move || inside(&root, &file)).await.ok()??;
                let name = real.file_name()?.to_string_lossy().into_owned();
                let mime = mime_of(&real);
                (real, name, mime, "private, max-age=30", ("x-archive-x-playback-delivery", "local-file"))
            }
            FileKind::Cached => {
                if !row.exists {
                    return None;
                }
                let dir = self.asset_dir().await?.join(channel.to_string()).join(message.to_string());
                // routes_playback.py `_cached_asset_path`: the direct copy first, then the browser-compatible one; each counts once over 1 KB.
                let found = tokio::task::spawn_blocking(move || {
                    [dir.join("direct").join("video.bin"), dir.join("video.mp4")]
                        .into_iter()
                        .find(|p| std::fs::metadata(p).map(|m| m.is_file() && m.len() > 1024).unwrap_or(false))
                })
                .await
                .ok()??;
                let name = found.file_name()?.to_string_lossy().into_owned();
                (found, name, "video/mp4", "private, max-age=86400", ("x-playback-cache", "1"))
            }
        };
        Some(self.send_file(&path, &name, mime, cache, extra, method, headers).await.unwrap_or_else(|| json_error(StatusCode::NOT_FOUND, "Not Found")))
    }

    #[allow(clippy::too_many_arguments)]
    async fn send_file(
        &self,
        path: &Path,
        name: &str,
        mime: &'static str,
        cache: &'static str,
        extra: (&'static str, &'static str),
        method: &Method,
        headers: &HeaderMap,
    ) -> Option<Response<Body>> {
        let mut file = tokio::fs::File::open(path).await.ok()?;
        let meta = file.metadata().await.ok()?;
        let size = meta.len();
        let mtime = meta.modified().ok().and_then(|t| t.duration_since(UNIX_EPOCH).ok()).map(|d| d.as_secs()).unwrap_or(0);
        let range = match parse_range(headers.get(RANGE).and_then(|v| v.to_str().ok()), size) {
            Ok(r) => r,
            Err(true) => {
                let mut r = json_error(StatusCode::RANGE_NOT_SATISFIABLE, "Range Not Satisfiable");
                r.headers_mut().insert(CONTENT_RANGE, HeaderValue::from_str(&format!("bytes */{size}")).ok()?);
                return Some(r);
            }
            Err(false) => return None, // not reached: checked in `stream_file`
        };
        let (status, start, end) = match range {
            Some((s, e)) => (StatusCode::PARTIAL_CONTENT, s, e),
            None => (StatusCode::OK, 0, size.saturating_sub(1)),
        };
        let length = if size == 0 { 0 } else { end - start + 1 };
        let body: Body = if *method == Method::HEAD || length == 0 {
            Empty::new().map_err(|n| match n {}).boxed()
        } else {
            if start > 0 {
                file.seek(SeekFrom::Start(start)).await.ok()?;
            }
            FileBody { file, remaining: length, buf: BytesMut::new() }.boxed()
        };
        let mut resp = Response::builder()
            .status(status)
            .header(CONTENT_TYPE, mime)
            .header(CONTENT_LENGTH, length)
            .header(ACCEPT_RANGES, "bytes")
            .header(CACHE_CONTROL, cache)
            .header(VARY, "Range")
            .header(ETAG, format!("\"{mtime:x}-{size:x}\""))
            .header(LAST_MODIFIED, httpdate::fmt_http_date(UNIX_EPOCH + std::time::Duration::from_secs(mtime)))
            .header("content-disposition", content_disposition(name))
            .header(extra.0, extra.1)
            .body(body)
            .ok()?;
        if status == StatusCode::PARTIAL_CONTENT {
            resp.headers_mut().insert(CONTENT_RANGE, HeaderValue::from_str(&format!("bytes {start}-{end}/{size}")).ok()?);
        }
        Some(resp)
    }
}

/// A part of a file, read from disk a chunk at a time as the client takes it (nothing is held in memory beyond one chunk).
struct FileBody {
    file: tokio::fs::File,
    remaining: u64,
    buf: BytesMut,
}

impl hyper::body::Body for FileBody {
    type Data = Bytes;
    type Error = Box<dyn std::error::Error + Send + Sync>;

    fn poll_frame(mut self: Pin<&mut Self>, cx: &mut Context<'_>) -> Poll<Option<Result<Frame<Bytes>, Self::Error>>> {
        let this = &mut *self;
        if this.remaining == 0 {
            return Poll::Ready(None);
        }
        let want = (this.remaining.min(CHUNK as u64)) as usize;
        if this.buf.capacity() < want {
            this.buf.reserve(want - this.buf.len());
        }
        // Read straight into the spare capacity of the buffer.
        let spare = this.buf.spare_capacity_mut();
        let mut rb = ReadBuf::uninit(&mut spare[..want]);
        match Pin::new(&mut this.file).poll_read(cx, &mut rb) {
            Poll::Pending => Poll::Pending,
            Poll::Ready(Err(e)) => Poll::Ready(Some(Err(Box::new(e)))),
            Poll::Ready(Ok(())) => {
                let n = rb.filled().len();
                if n == 0 {
                    // The file got shorter while being sent: end the body; the client sees fewer bytes than Content-Length.
                    return Poll::Ready(Some(Err("file ended early".into())));
                }
                // SAFETY: `poll_read` initialised the first `n` bytes of the spare capacity.
                unsafe { this.buf.set_len(this.buf.len() + n) };
                this.remaining -= n as u64;
                Poll::Ready(Some(Ok(Frame::data(this.buf.split().freeze()))))
            }
        }
    }

    fn is_end_stream(&self) -> bool {
        self.remaining == 0
    }

    fn size_hint(&self) -> hyper::body::SizeHint {
        hyper::body::SizeHint::with_exact(self.remaining)
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn only_local_folders_and_cached_copies_are_ours() {
        assert_eq!(match_path("/stream/-9000000000001/3"), Some((FileKind::Local, -9_000_000_000_001, 3)));
        assert_eq!(match_path("/stream-cached/-1001234/7"), Some((FileKind::Cached, -1_001_234, 7)));
        assert_eq!(match_path("/stream/-1001234/7"), None, "a Telegram channel needs the playback engine");
        assert_eq!(match_path("/stream/-9000000000000/1"), None, "LOCAL_BASE itself is not a folder");
        assert_eq!(match_path("/stream/-9000000000001/x"), None);
        assert_eq!(match_path("/stream/-9000000000001/1/extra"), None);
        assert_eq!(match_path("/stream-compat/-1/1"), None);
    }

    #[test]
    fn ranges_read_like_python() {
        assert_eq!(parse_range(None, 100), Ok(None));
        assert_eq!(parse_range(Some("bytes=0-9"), 100), Ok(Some((0, 9))));
        assert_eq!(parse_range(Some("bytes=90-"), 100), Ok(Some((90, 99))));
        assert_eq!(parse_range(Some("bytes=-10"), 100), Ok(Some((90, 99))));
        assert_eq!(parse_range(Some("bytes=-500"), 100), Ok(Some((0, 99))));
        assert_eq!(parse_range(Some("bytes=50-500"), 100), Ok(Some((50, 99))));
        assert_eq!(parse_range(Some("bytes=100-"), 100), Err(true));
        assert_eq!(parse_range(Some("bytes=0-1,5-6"), 100), Err(false));
        assert_eq!(parse_range(Some("bytes=9-1"), 100), Err(false));
        assert_eq!(parse_range(Some("items=0-1"), 100), Err(false));
        assert_eq!(parse_range(Some("bytes=a-"), 100), Err(false));
    }

    #[test]
    fn a_file_outside_its_folder_is_refused() {
        let dir = std::env::temp_dir().join(format!("ax-inside-{}", std::process::id()));
        let (root, other) = (dir.join("root"), dir.join("other"));
        std::fs::create_dir_all(&root).unwrap();
        std::fs::create_dir_all(&other).unwrap();
        std::fs::write(root.join("a.mp4"), b"x").unwrap();
        std::fs::write(other.join("b.mp4"), b"x").unwrap();
        assert!(inside(&root, &root.join("a.mp4")).is_some());
        assert!(inside(&root, &root.join("../other/b.mp4")).is_none());
        assert!(inside(&root, &other.join("b.mp4")).is_none());
        assert!(inside(&root, &root).is_none());
        let _ = std::fs::remove_dir_all(&dir);
    }

    #[test]
    fn disposition_is_ascii_safe() {
        assert_eq!(content_disposition("a b.mp4"), "inline; filename=\"a b.mp4\"; filename*=UTF-8''a%20b%2Emp4");
        assert!(content_disposition("ñ.mp4").starts_with("inline; filename=\"_.mp4\""));
    }
}
