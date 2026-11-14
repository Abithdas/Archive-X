//! Finishes a JSON answer the way the Python backend's `ResponseOptimizationMiddleware` does, so a route answered here is
//! indistinguishable from the same route answered by Python: an ETag (Adler-32 of the body), `304` for an exact
//! `If-None-Match`, gzip (level 5) from 1,024 bytes when the client accepts it, and `Cache-Control: private, no-cache`.

use std::io::Write;

use bytes::Bytes;
use http_body_util::{BodyExt, Full};
use hyper::header::{ACCEPT_ENCODING, CACHE_CONTROL, CONTENT_ENCODING, CONTENT_LENGTH, CONTENT_TYPE, ETAG, IF_NONE_MATCH, VARY};
use hyper::{HeaderMap, Response, StatusCode};

use crate::proxy::Body;

const MIN_COMPRESS: usize = 1024;

/// zlib's Adler-32, which is what `zlib.adler32` in Python computes.
pub fn adler32(data: &[u8]) -> u32 {
    const MOD: u32 = 65521;
    let (mut a, mut b) = (1u32, 0u32);
    for chunk in data.chunks(5552) {
        for &byte in chunk {
            a += byte as u32;
            b += a;
        }
        a %= MOD;
        b %= MOD;
    }
    (b << 16) | a
}

fn body(bytes: Bytes) -> Body {
    Full::new(bytes).map_err(|n| match n {}).boxed()
}

/// A `200` JSON answer for `payload`, or the `304` the client's `If-None-Match` earns.
pub fn json_response(request: &HeaderMap, payload: Vec<u8>) -> Response<Body> {
    let etag = format!("\"{:08x}\"", adler32(&payload));
    let sent = request.get(IF_NONE_MATCH).and_then(|v| v.to_str().ok()).unwrap_or("");
    if !sent.is_empty() && sent.trim() == etag {
        return Response::builder()
            .status(StatusCode::NOT_MODIFIED)
            .header(CONTENT_TYPE, "application/json")
            .header(ETAG, etag)
            .header(CACHE_CONTROL, "private, no-cache")
            .body(body(Bytes::new()))
            .unwrap();
    }
    // Python only looks for the word "gzip" anywhere in the header, case-insensitively.
    let wants_gzip = request.get(ACCEPT_ENCODING).and_then(|v| v.to_str().ok()).map(|v| v.to_ascii_lowercase().contains("gzip")).unwrap_or(false);
    let mut builder = Response::builder().status(StatusCode::OK).header(CONTENT_TYPE, "application/json");
    let out = if wants_gzip && payload.len() >= MIN_COMPRESS {
        let mut enc = flate2::write::GzEncoder::new(Vec::with_capacity(payload.len() / 3), flate2::Compression::new(5));
        enc.write_all(&payload).ok();
        builder = builder.header(CONTENT_ENCODING, "gzip").header(VARY, "Accept-Encoding");
        Bytes::from(enc.finish().unwrap_or_default())
    } else {
        Bytes::from(payload)
    };
    builder.header(ETAG, etag).header(CACHE_CONTROL, "private, no-cache").header(CONTENT_LENGTH, out.len()).body(body(out)).unwrap()
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn adler32_matches_zlib() {
        // python3 -c "import zlib;print(format(zlib.adler32(b'Wikipedia'),'08x'))"
        assert_eq!(adler32(b"Wikipedia"), 0x11E60398);
        assert_eq!(adler32(b""), 1);
        let big = vec![0xffu8; 100_000];
        assert_eq!(adler32(&big), 0x149a302c, "long input (computed with Python's zlib)");
    }
}
