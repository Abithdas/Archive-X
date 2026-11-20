//! `metadata/parser.py`: filename and caption cleanup, the key that pairs a video with its thumbnail, and the hints (studio, date, title,
//! resolution, scene code, cast) a scene name gives.
//!
//! Python's `re` and `str` have their own idea of a word, a digit and a space, and the parser leans on all three, so they are spelled out
//! here: a word character is Python's `isalnum` plus underscore (`tag_taxonomy::is_word_char`), `\s` also takes U+001C to U+001F, `\d` is
//! any decimal digit with its own value, and every position Python reports is a character index, not a byte offset. Not ported: the
//! optional `ftfy` mojibake repair (`requirements.txt` does not install it, and the Python code runs without it then).

use crate::text::casefold;
use fancy_regex::Regex;
use serde_json::{json, Value};
use std::sync::OnceLock;
use unicode_normalization::char::canonical_combining_class;
use unicode_normalization::UnicodeNormalization;
use unicode_properties::{GeneralCategory, UnicodeGeneralCategory};

/// Python's `\w`.
const W: &str = r"[\p{L}\p{N}_]";
/// Python's `\s`.
const S: &str = r"[\s\x1c-\x1f]";
/// `\b` between Python word characters.
fn boundary() -> String {
    format!("(?:(?<={W})(?!{W})|(?<!{W})(?={W}))")
}

/// Python's `re.IGNORECASE` also lets U+0130 and U+0131 stand for `i`, U+017F for `s` and U+212A for `k`; the `regex` crate's case folding
/// does not for the first two. A literal word written for `(?i)` goes through here so that every `i`, `s` and `k` takes them.
fn ci(lit: &str) -> String {
    lit.chars()
        .map(|c| match c.to_ascii_lowercase() {
            'i' if c.is_ascii_alphabetic() => "[iI\u{130}\u{131}]".to_string(),
            's' if c.is_ascii_alphabetic() => "[sS\u{17f}]".to_string(),
            'k' if c.is_ascii_alphabetic() => "[kK\u{212a}]".to_string(),
            _ => c.to_string(),
        })
        .collect()
}

/// Alternatives of literal words, each through [`ci`]; `[-_ ]?`-style pieces pass through (they have none of those letters).
fn words(list: &[&str]) -> String {
    list.iter().map(|w| ci(w)).collect::<Vec<_>>().join("|")
}

/// `[A-Za-z0-9]` as `re.I` sees it.
const ALNUM_CI: &str = "[A-Za-z0-9\u{130}\u{131}\u{17f}\u{212a}]";
/// `[^a-z0-9]` as `re.I` sees it.
const NOT_ALNUM_CI: &str = "[^a-z0-9\u{130}\u{131}]";

macro_rules! regex {
    ($name:ident, $pat:expr) => {
        fn $name() -> &'static Regex {
            static R: OnceLock<Regex> = OnceLock::new();
            R.get_or_init(|| Regex::new(&$pat).expect("pattern"))
        }
    };
}

fn ext() -> String {
    format!("(?:{})", words(&["mp4", "m4v", "mkv", "mov", "avi", "wmv", "webm", "ts", "flv", "mpg", "mpeg"]))
}
fn quality_words() -> String {
    words(&["2160p", "1440p", "1080p", "720p", "576p", "480p", "4k", "uhd", "fhd", "hd"])
}
regex!(thumb_suffix, format!(r"(?i)(?:[._ ]{e})?(?:[._ ](?:{img}))$", e = ext(), img = format!("{}|{}|{}", ci("jpe?g"), ci("png"), ci("webp"))));
regex!(ext_re, format!(r"(?i)\.{}$", ext()));
regex!(res_re, format!(r"(?i)(?<!{a})({q})(?!{a})", a = ALNUM_CI, q = quality_words()));
regex!(fused_quality, format!(r"(?i)({q})(?={d}(?:{n}|$))", q = quality_words(), d = ci("date"), n = NOT_ALNUM_CI));
regex!(date_full, r"(?<!\d)((?:19|20)\d{2})[.\-_ ](\d{1,2})[.\-_ ](\d{1,2})(?!\d)");
regex!(date_short, r"(?<!\d)(\d{2})[.\-_](\d{2})[.\-_](\d{2})(?!\d)");
regex!(
    noise,
    format!(
        r"(?i){b}({w}|{wd}|{ss}){b}",
        b = boundary(),
        w = words(&[
            "xxx",
            "prt",
            "x264",
            "x265",
            "h264",
            "h265",
            "hevc",
            "avc",
            "aac",
            "web",
            "webrip",
            "bluray",
            "brrip",
            "proper",
            "repack",
            "remux",
            "sample",
            "final",
            "uncensored",
            "censored",
            "4320p",
            "2160p",
            "1440p",
            "1080p",
            "720p",
            "576p",
            "480p",
            "4k",
            "uhd",
            "fhd",
            "10bit",
            "8bit",
            "mp4",
            "mkv",
            "avi",
            "mov",
            "wmv",
            "webm"
        ]),
        wd = format!("{}[-_ ]?{}", ci("web"), ci("dl")),
        ss = format!("{}[-_ ]?{}", ci("split"), ci("scenes?"))
    )
);
regex!(scene_code, r"(?<![A-Z0-9])([A-Z]{2,10})[.\-_]?(\d{2,6})(?!\d)");
regex!(whitespace, format!("{S}+"));
regex!(non_word_or_underscore, r"[^\p{L}\p{N}]+");
regex!(bracket, format!(r"^\[([^\]]{{1,60}})\]{S}*([^\-]{{1,80}}){S}+-{S}+(.+)$"));
regex!(year_in_brackets, r"(?<!\d)[(\[]((?:19|20)\d{2})[)\]](?!\d)");
regex!(cast_sep, format!(r"(?i){S}*(?:,|&|{b}and{b}){S}*", b = boundary()));
regex!(dash_sep, format!(r"{S}+-{S}+"));
regex!(camel_split, r"(?<=[a-z])(?=[A-Z])|(?<=[A-Z])(?=[A-Z][a-z])");
regex!(
    tech_words,
    format!(
        r"(?i){b}(?:{w}|{wd}){b}",
        b = boundary(),
        w = words(&[
            "4320p", "2160p", "1440p", "1080p", "720p", "576p", "480p", "4k", "uhd", "fhd", "x264", "x265", "h264", "h265", "hevc", "avc", "web", "webrip", "bluray", "brrip",
            "xxx", "prt"
        ]),
        wd = format!("{}[-_ ]?{}", ci("web"), ci("dl"))
    )
);
regex!(cast_word, r"[A-Za-zÀ-ÿ][A-Za-zÀ-ÿ'’.-]*");
regex!(dot_underscore, r"[._]+");

const GENERIC_SCENE_CODE_PREFIXES: [&str; 10] = ["VID", "VIDEO", "CLIP", "MOVIE", "FILE", "UNKNOWN", "OPAQUE", "DOWNLOAD", "PART", "CHUNK"];

/// The captures of the first match, as owned `(start, end, text)` byte spans per group.
type Groups = Vec<Option<(usize, usize)>>;

fn caps_of(re: &Regex, text: &str, from: usize) -> Option<Groups> {
    let caps = re.captures_from_pos(text, from).ok().flatten()?;
    Some((0..caps.len()).map(|i| caps.get(i).map(|m| (m.start(), m.end()))).collect())
}

fn is_py_space(c: char) -> bool {
    c.is_whitespace() || ('\x1c'..='\x1f').contains(&c)
}

/// `str.strip()`.
fn py_strip(s: &str) -> &str {
    s.trim_matches(is_py_space)
}

/// `str.strip(chars)`.
fn strip_chars<'a>(s: &'a str, chars: &str) -> &'a str {
    s.trim_matches(|c| chars.contains(c))
}

/// The value of a decimal digit (`int('٣') == 3`): a decimal-digit block is ten consecutive code points, the first of them zero.
fn digit_value(c: char) -> Option<u32> {
    if c.general_category() != GeneralCategory::DecimalNumber {
        return None;
    }
    let mut run = 0u32;
    let mut cp = c as u32;
    while cp > 0 {
        cp -= 1;
        match char::from_u32(cp) {
            Some(prev) if prev.general_category() == GeneralCategory::DecimalNumber => run += 1,
            _ => break,
        }
    }
    Some(run % 10)
}

/// `int(text)` for text made of decimal digits.
fn to_int(text: &str) -> i64 {
    text.chars().fold(0i64, |acc, c| acc * 10 + digit_value(c).unwrap_or(0) as i64)
}

/// Character index of a byte offset.
fn cidx(s: &str, byte: usize) -> usize {
    s[..byte].chars().count()
}

/// Byte offset of a character index (the end of the string for an index past it).
fn bidx(s: &str, ch: usize) -> usize {
    s.char_indices().nth(ch).map_or(s.len(), |(i, _)| i)
}

/// `clean_text`: NFC, no-break spaces as spaces, runs of white space as one space, trimmed.
pub fn clean_text(s: &str) -> String {
    if s.is_empty() {
        return String::new();
    }
    let nfc: String = s.nfc().collect();
    let spaced = nfc.replace('\u{a0}', " ");
    py_strip(&whitespace().replace_all(&spaced, " ")).to_string()
}

/// `canonical_key`: the key that pairs a video with its thumbnail.
pub fn canonical_key(name: &str) -> String {
    let s = clean_text(name);
    let s = thumb_suffix().replace(&s, "").into_owned();
    let s = ext_re().replace(&s, "").into_owned();
    let stripped: String = s.nfkd().filter(|c| canonical_combining_class(*c) == 0).collect();
    let folded = casefold(&stripped);
    let spaced = non_word_or_underscore().replace_all(&folded, " ");
    py_strip(&whitespace().replace_all(&spaced, " ")).to_string()
}

/// `split_fused_quality_before_date`.
pub fn split_fused_quality_before_date(value: &str) -> String {
    fused_quality().replace_all(value, " $1 ").into_owned()
}

/// `is_generic_scene_code`.
pub fn is_generic_scene_code(value: &str) -> bool {
    let compact: String = casefold(value).chars().filter(|c| c.is_ascii_lowercase() || c.is_ascii_digit()).collect();
    let prefix: String = compact.chars().take_while(|c| !c.is_ascii_digit()).collect::<String>().to_uppercase();
    !prefix.is_empty() && GENERIC_SCENE_CODE_PREFIXES.contains(&prefix.as_str())
}

fn valid_date(y: i64, m: i64, d: i64) -> Option<String> {
    ((1990..=2100).contains(&y) && (1..=12).contains(&m) && (1..=31).contains(&d)).then(|| format!("{y:04}-{m:02}-{d:02}"))
}

/// The hints of `parse_scene_name`.
#[derive(Debug, Clone, PartialEq, Default)]
pub struct SceneHints {
    pub original: String,
    pub studio: Option<String>,
    pub date: Option<String>,
    pub year: Option<i64>,
    pub title: Option<String>,
    pub resolution: Option<String>,
    pub code: Option<String>,
    pub performers: Vec<String>,
    pub performer_candidates: Vec<String>,
}

impl SceneHints {
    pub fn to_json(&self) -> Value {
        json!({
            "original": self.original, "studio": self.studio, "date": self.date, "year": self.year, "title": self.title,
            "resolution": self.resolution, "code": self.code, "performers": self.performers, "performer_candidates": self.performer_candidates,
        })
    }
}

fn group(text: &str, g: &Groups, i: usize) -> String {
    g[i].map(|(a, b)| text[a..b].to_string()).unwrap_or_default()
}

/// `parse_scene_name`.
pub fn parse_scene_name(name: &str) -> SceneHints {
    let raw = clean_text(name);
    let without_ext = ext_re().replace(&raw, "").into_owned();
    let mut base = thumb_suffix().replace(&without_ext, "").into_owned();
    let mut out = SceneHints { original: raw, ..Default::default() };

    base = split_fused_quality_before_date(&base);

    if let Some(g) = caps_of(res_re(), &base, 0) {
        let r = group(&base, &g, 1).to_lowercase();
        out.resolution = Some(match r.as_str() {
            "4k" | "uhd" => "2160p".to_string(),
            "fhd" => "1080p".to_string(),
            "hd" => "720p".to_string(),
            other => other.to_string(),
        });
        base = res_re().replace_all(&base, " ").into_owned();
    }

    // a scene code is never looked for inside a date: mask the date with as many spaces as it has characters
    let date_peek = caps_of(date_full(), &base, 0).or_else(|| caps_of(date_short(), &base, 0));
    let mut code_search_text = base.clone();
    if let Some(g) = &date_peek {
        let (ds, de) = g[0].unwrap();
        code_search_text = format!("{}{}{}", &base[..ds], " ".repeat(base[ds..de].chars().count()), &base[de..]);
    }
    let upper = code_search_text.to_uppercase();
    if let Some(g) = caps_of(scene_code(), &upper, 0) {
        let letters = group(&upper, &g, 1);
        let digits = group(&upper, &g, 2);
        if !is_generic_scene_code(&format!("{letters}-{digits}")) {
            out.code = Some(format!("{letters}-{digits}"));
        }
        // Python applies the character positions of the upper-cased text to `base`, which differ when a letter grows (ß -> SS)
        let (s, e) = g[0].unwrap();
        let (cs, ce) = (cidx(&upper, s), cidx(&upper, e));
        let (bs, be) = (bidx(&base, cs), bidx(&base, ce));
        base = format!("{} {}", &base[..bs], &base[be..]);
    }

    if let Some(g) = caps_of(bracket(), &base, 0) {
        let studio_text = py_strip(&group(&base, &g, 1)).to_string();
        let performer_text = py_strip(&group(&base, &g, 2)).to_string();
        let remainder = py_strip(&group(&base, &g, 3)).to_string();
        out.studio = Some(studio_text);
        if !performer_text.is_empty() {
            let mut parts = Vec::new();
            let mut last = 0;
            for m in cast_sep().find_iter(&performer_text).flatten() {
                parts.push(performer_text[last..m.start()].to_string());
                last = m.end();
            }
            parts.push(performer_text[last..].to_string());
            out.performers = parts.iter().map(|p| py_strip(p).to_string()).filter(|p| !p.is_empty()).collect();
        }
        base = format!("{performer_text} - {remainder}");
    }

    if let Some(g) = caps_of(year_in_brackets(), &base, 0) {
        out.year = Some(to_int(&group(&base, &g, 1)));
        let (a, b) = g[0].unwrap();
        base = format!("{} {}", &base[..a], &base[b..]);
    }

    let mut date_m = caps_of(date_full(), &base, 0);
    if let Some(g) = &date_m {
        out.date = valid_date(to_int(&group(&base, g, 1)), to_int(&group(&base, g, 2)), to_int(&group(&base, g, 3)));
    } else {
        date_m = caps_of(date_short(), &base, 0);
        if let Some(g) = &date_m {
            out.date = valid_date(2000 + to_int(&group(&base, g, 1)), to_int(&group(&base, g, 2)), to_int(&group(&base, g, 3)));
        }
    }
    let title_src: String;
    if let (Some(g), Some(_)) = (&date_m, &out.date) {
        let (ds, de) = g[0].unwrap();
        let trim = " .-_[]()";
        let before = strip_chars(&base[..ds], trim).to_string();
        let after = strip_chars(&base[de..], trim).to_string();
        if !before.is_empty() && !after.is_empty() && out.studio.is_none() && before.split('.').count() <= 2 && before.chars().count() <= 40 {
            out.studio = Some(py_strip(&before.replace(['.', '_'], " ")).to_string());
        }
        let mut src = if !after.is_empty() {
            after.clone()
        } else if out.studio.is_some() {
            String::new()
        } else {
            before.clone()
        };
        if !before.is_empty() && !after.is_empty() && out.studio.is_some() && out.date.is_some() {
            let suffix = dot_underscore().replace_all(&after, " ").into_owned();
            let suffix = camel_split().replace_all(&suffix, " ").into_owned();
            let technical_markers = noise().is_match(&suffix).unwrap_or(false);
            let suffix = tech_words().replace_all(&suffix, " ").into_owned();
            let words: Vec<String> = cast_word().find_iter(&suffix).flatten().map(|m| m.as_str().to_string()).collect();
            let trimmed = |w: &str| strip_chars(w, ".-").to_string();
            let long_enough = words.iter().take(2).all(|w| trimmed(w).chars().count() >= 2);
            if (words.len() >= 4 || (words.len() == 2 && technical_markers)) && long_enough {
                let tentative = format!("{} {}", trimmed(&words[0]), trimmed(&words[1]));
                out.performer_candidates = vec![tentative.clone()];
                let joined = tentative
                    .split(is_py_space)
                    .filter(|w| !w.is_empty())
                    .map(|w| {
                        w.chars()
                            .map(|c| if c.is_ascii_alphabetic() { ci(&c.to_string()) } else { fancy_regex::escape(&c.to_string()).into_owned() })
                            .collect::<String>()
                    })
                    .collect::<Vec<_>>()
                    .join(r"[^\p{L}\p{N}]+");
                let pattern = format!(r"(?i)^{S}*{joined}(?={NOT_ALNUM_CI}|$)");
                if let Ok(re) = Regex::new(&pattern) {
                    if let Ok(Some(m)) = re.find(&src) {
                        src = format!("{} {}", &src[..m.start()], &src[m.end()..]);
                    }
                }
            }
        }
        title_src = src;
    } else {
        // `re.split(r"\s+-\s+", base, maxsplit=1)`
        let first = dash_sep().find(&base).ok().flatten();
        match first {
            Some(m) if base[..m.start()].chars().count() <= 40 => {
                let head = base[..m.start()].to_string();
                let tail = base[m.end()..].to_string();
                if out.studio.is_none() {
                    out.studio = Some(py_strip(&head).to_string());
                }
                if out.performers.is_empty() && out.studio.is_some() && py_strip(&head) != out.studio.as_deref().unwrap() {
                    out.performers = vec![py_strip(&head).to_string()];
                }
                title_src = tail;
            }
            _ => title_src = base.clone(),
        }
    }
    let cleaned = noise().replace_all(&title_src.replace(['.', '_'], " "), " ").into_owned();
    let title = whitespace().replace_all(&cleaned, " ").into_owned();
    let title = strip_chars(&title, " -");
    out.title = (!title.is_empty()).then(|| title.to_string());
    out
}

/// `fingerprint.oshash_from_parts`: the OpenSubtitles hash of a file from its size and its first and last 64 KiB.
pub fn oshash_from_parts(size: i64, head: &[u8], tail: &[u8]) -> Result<String, String> {
    const CHUNK: i64 = 64 * 1024;
    if size <= 0 {
        return Err("empty file".into());
    }
    let want = size.clamp(0, CHUNK) as usize;
    if head.len() != want || tail.len() != want {
        return Err(format!("incomplete read: got {}+{} bytes, need {want}+{want}", head.len(), tail.len()));
    }
    fn sum_words(buf: &[u8]) -> u64 {
        let mut total = 0u64;
        for chunk in buf.chunks(8) {
            let mut word = [0u8; 8];
            word[..chunk.len()].copy_from_slice(chunk);
            total = total.wrapping_add(u64::from_le_bytes(word));
        }
        total
    }
    let value = (size as u64).wrapping_add(sum_words(head)).wrapping_add(sum_words(tail));
    Ok(format!("{value:016x}"))
}
