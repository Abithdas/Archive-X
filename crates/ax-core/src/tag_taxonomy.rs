//! Port of `telegram_archive/metadata/tag_taxonomy.py`: catalogue-wide tag filtering, ranking and derived categories.
//!
//! Read-only presentation logic: nothing here creates or changes tags. The normalisation follows Python exactly: NFKC, full Unicode
//! case folding, `&` to " and ", `_` to a space, then every run of characters that Python's `\w` does not match becomes one space.
//! (Python's `\w` is "alphanumeric by `str.isalnum()`, or underscore"; the `regex` crate's `\w` also accepts combining marks, so it is
//! not used for this.) Numbers are rounded the way Python's `round(x, 5)` does it.

use std::collections::HashMap;
use std::sync::OnceLock;

use regex::Regex;
use serde_json::{json, Map, Value};
use unicode_normalization::UnicodeNormalization;
use unicode_properties::{GeneralCategory, UnicodeGeneralCategory};

/// Format, quality or near-content-free labels (`GENERIC_TAGS`), sorted. Generated from the Python source.
const GENERIC_TAGS: &[&str] = &[
    "24fps",
    "25fps",
    "30fps",
    "4k",
    "50fps",
    "60fps",
    "8k",
    "adult",
    "all sex",
    "avi",
    "free",
    "full hd",
    "full sex",
    "h264",
    "h265",
    "hd",
    "hevc",
    "mkv",
    "movie",
    "mp4",
    "new",
    "official",
    "online",
    "porn",
    "porn video",
    "scene",
    "sd",
    "sex",
    "sexy",
    "uhd",
    "uncensored",
    "video",
    "web dl",
    "webdl",
    "x264",
    "x265",
    "xxx",
];

/// Tag-to-category rules (`_CATEGORY_TERMS`), in the Python order. Generated from the Python source.
const CATEGORY_TERMS: &[(&str, &[&str])] = &[
    ("Anal", &["anal", "rimming", "rimjob", "anal oral", "analingus"]),
    ("Oral", &["blowjob", "blow job", "deepthroat", "deep throat", "fellatio", "oral sex", "oral"]),
    ("Group", &["threesome", "foursome", "orgy", "gangbang", "gang bang", "group sex", "double penetration", "double penetr", " dp "]),
    ("Lesbian", &["lesbian", "girl girl", "girl-girl", "girls with girls", "tribbing", "scissoring", "female female"]),
    ("Masturbation", &["masturbat", "handjob", "hand job", "fingering", "solo play", "dildo", "vibrator"]),
    ("BDSM & Fetish", &["bdsm", "bondage", "spanking", "domination", "dominant", "submissive", "fetish", "latex", "stockings", "pegging"]),
    ("Foot & Feet", &["footjob", "foot job", "feet", "foot fetish", "foot worship"]),
    ("Sex Toys", &["dildo", "vibrator", "sex toy", "toys", "anal plug"]),
    ("Cumshot", &["cumshot", "cum shot", "facial", "creampie", "cream pie", "swallowing", "cum inside", "internal cum"]),
    ("POV", &["pov", "p o v", "point of view"]),
    ("Amateur", &["amateur", "homemade", "home made", "self shot", "selfshot"]),
    ("Roleplay", &["roleplay", "role play", "cosplay", "schoolgirl", "teacher", "nurse", "maid", "secretary"]),
    ("Outdoor & Public", &["outdoor", "public", "pool", "beach", "car sex", "park", "outdoors"]),
    ("Sex Positions", &["doggystyle", "doggy style", "cowgirl", "missionary", "reverse cowgirl", "spooning", "standing position", "69 position"]),
    (
        "Body & Appearance",
        &["big ass", "big tits", "big boobs", "busty", "petite", "milf", "mature", "bbw", "blonde", "brunette", "redhead", "curvy", "big dick", "small tits", "small boobs"],
    ),
];

/// One input row: a tag name with either one video it is on (`video_id`) or a precomputed count (`video_count`).
#[derive(Debug, Clone, Default)]
pub struct TagRow {
    pub name: String,
    pub video_id: Option<i64>,
    pub video_count: i64,
}

/// Python's `str.isalnum()` plus underscore: the characters `\w` matches in a Unicode `str` pattern.
pub fn is_word_char(c: char) -> bool {
    use GeneralCategory::*;
    c == '_'
        || matches!(
            c.general_category(),
            UppercaseLetter | LowercaseLetter | TitlecaseLetter | ModifierLetter | OtherLetter | DecimalNumber | LetterNumber | OtherNumber
        )
}

/// `normalize_tag_key`.
pub fn normalize_tag_key(value: &str) -> String {
    let nfkc: String = value.nfkc().collect();
    let folded = caseless::default_case_fold_str(&nfkc).replace('&', " and ").replace('_', " ");
    let mut out = String::with_capacity(folded.len());
    let mut in_gap = false;
    for c in folded.chars() {
        if is_word_char(c) {
            out.push(c);
            in_gap = false;
        } else if !in_gap {
            out.push(' ');
            in_gap = true;
        }
    }
    out.trim_matches(|c: char| c.is_whitespace()).to_string()
}

fn generic_keys() -> &'static std::collections::HashSet<String> {
    static KEYS: OnceLock<std::collections::HashSet<String>> = OnceLock::new();
    KEYS.get_or_init(|| GENERIC_TAGS.iter().map(|t| normalize_tag_key(t)).collect())
}

fn generic_patterns() -> &'static [Regex] {
    static RES: OnceLock<Vec<Regex>> = OnceLock::new();
    RES.get_or_init(|| {
        [
            r"^(?:240|360|480|540|576|720|1080|1440|2160|4320)\s?p$",
            r"^(?:h\.?264|h\.?265|hevc|x26[45])(?:\s+(?:encode|rip))?$",
            r"^(?:mp4|mkv|avi|mov|wmv|webm)$",
            r"^(?:\d{3,4}\s?p)(?:\s+(?:hd|hevc|x26[45]))?$",
        ]
        .iter()
        .map(|p| Regex::new(&format!("(?i){p}")).expect("a fixed pattern"))
        .collect()
    })
}

/// `is_generic_tag`.
pub fn is_generic_tag(value: &str) -> bool {
    let key = normalize_tag_key(value);
    key.is_empty() || generic_keys().contains(&key) || generic_patterns().iter().any(|p| p.is_match(&key))
}

/// `categories_for_tag`: every category that has a term inside the tag (a tag can be in several).
pub fn categories_for_tag(value: &str) -> Vec<&'static str> {
    let key = format!(" {} ", normalize_tag_key(value));
    if key.trim().is_empty() {
        return Vec::new();
    }
    let mut found = Vec::new();
    for (category, terms) in CATEGORY_TERMS {
        for term in *terms {
            let norm = format!(" {} ", normalize_tag_key(term));
            if !norm.trim().is_empty() && key.contains(&norm) {
                found.push(*category);
                break;
            }
        }
    }
    found
}

/// Python's `round(x, 5)`: the exactly-printed value rounded to 5 places (Rust's formatting is exact, ties to even).
fn round5(x: f64) -> f64 {
    format!("{x:.5}").parse().unwrap_or(x)
}

#[derive(Default)]
struct Group {
    /// name -> video ids, in the order names first appeared
    variant_ids: Vec<(String, std::collections::BTreeSet<i64>)>,
    ids: std::collections::BTreeSet<i64>,
    /// name -> highest precomputed count, in the order names first appeared
    counts: Vec<(String, i64)>,
}

impl Group {
    fn ids_for(&mut self, name: &str) -> &mut std::collections::BTreeSet<i64> {
        if let Some(i) = self.variant_ids.iter().position(|(n, _)| n == name) {
            return &mut self.variant_ids[i].1;
        }
        self.variant_ids.push((name.to_string(), Default::default()));
        &mut self.variant_ids.last_mut().expect("just pushed").1
    }
    fn raise_count(&mut self, name: &str, count: i64) {
        match self.counts.iter_mut().find(|(n, _)| n == name) {
            Some((_, c)) => *c = (*c).max(count),
            None => self.counts.push((name.to_string(), count.max(0))), // Python's defaultdict starts at 0
        }
    }
}

/// `build_tag_taxonomy`: one normalised tag map from rows of name and video (or count). Names that differ only in case or punctuation
/// share a key and their videos are unioned, so a video that carries both spellings is counted once.
pub fn build_tag_taxonomy(total_videos: i64, rows: &[TagRow]) -> Value {
    let total = total_videos.max(0);
    let mut order: Vec<String> = Vec::new();
    let mut groups: HashMap<String, Group> = HashMap::new();
    for row in rows {
        let name = row.name.trim();
        let key = normalize_tag_key(name);
        if key.is_empty() {
            continue;
        }
        let group = groups.entry(key.clone()).or_insert_with(|| {
            order.push(key.clone());
            Group::default()
        });
        if let Some(vid) = row.video_id {
            group.ids_for(name).insert(vid);
            group.ids.insert(vid);
        } else if row.video_count != 0 {
            group.raise_count(name, row.video_count);
        }
    }

    let mut tag_map = Map::new();
    let mut ranked: Vec<Value> = Vec::new();
    let (mut excluded_generic, mut excluded_common) = (0i64, 0i64);
    for key in &order {
        let group = &groups[key];
        let mut variant_counts: Vec<(String, i64)> = group.variant_ids.iter().map(|(n, ids)| (n.clone(), ids.len() as i64)).collect();
        for (name, count) in &group.counts {
            match variant_counts.iter_mut().find(|(n, _)| n == name) {
                Some((_, c)) => *c = (*c).max(*count),
                None => variant_counts.push((name.clone(), (*count).max(0))),
            }
        }
        if variant_counts.is_empty() {
            continue;
        }
        // most videos first, then the shorter name, then the name folded; the first of equals (insertion order) wins
        let folded = |n: &str| caseless::default_case_fold_str(n);
        let canonical = variant_counts
            .iter()
            .min_by(|a, b| b.1.cmp(&a.1).then(a.0.chars().count().cmp(&b.0.chars().count())).then_with(|| folded(&a.0).cmp(&folded(&b.0))))
            .map(|(n, _)| n.clone())
            .expect("not empty");
        let mut aliases: Vec<&String> = variant_counts.iter().map(|(n, _)| n).filter(|n| **n != canonical).collect();
        aliases.sort();
        let mut count = if group.ids.is_empty() { variant_counts.iter().map(|(_, c)| *c).max().unwrap_or(0) } else { group.ids.len() as i64 };
        count = if total != 0 { total.min(count.max(0)) } else { count.max(0) };
        let prevalence = if total != 0 { count as f64 / total as f64 } else { 0.0 };
        let generic = is_generic_tag(&canonical);
        let common = total != 0 && count * 10 >= total * 9;
        let categories = categories_for_tag(&canonical);
        let rarity = if total > 1 {
            (((total + 1) as f64 / (count + 1) as f64).ln() / ((total + 1) as f64).ln()).clamp(0.0, 1.0)
        } else if count != 0 {
            0.0
        } else {
            1.0
        };
        let usefulness = if categories.is_empty() { 0.22 } else { 0.65 };
        let score = round5(0.72 * rarity + 0.28 * usefulness);
        let display = !generic && !common;
        if generic {
            excluded_generic += 1;
        }
        if common {
            excluded_common += 1;
        }
        let entry = json!({
            "key": key,
            "name": canonical,
            "aliases": aliases,
            "video_count": count,
            "prevalence": round5(prevalence),
            "rarity_score": round5(rarity),
            "usefulness_score": usefulness,
            "rank_score": score,
            "categories": categories,
            "generic": generic,
            "common": common,
            "display": display,
        });
        if display {
            ranked.push(entry.clone());
        }
        tag_map.insert(key.clone(), entry);
    }
    let sort_key = |v: &Value| {
        (
            v["rank_score"].as_f64().unwrap_or(0.0),
            v["video_count"].as_i64().unwrap_or(0),
            caseless::default_case_fold_str(v["name"].as_str().unwrap_or("")),
        )
    };
    ranked.sort_by(|a, b| {
        let (ka, kb) = (sort_key(a), sort_key(b));
        kb.0.partial_cmp(&ka.0).unwrap_or(std::cmp::Ordering::Equal).then(ka.1.cmp(&kb.1)).then(ka.2.cmp(&kb.2))
    });
    json!({
        "total_videos": total,
        "common_threshold": 0.9,
        "tag_map": tag_map,
        "ranked_tags": ranked,
        "excluded_generic_count": excluded_generic,
        "excluded_common_count": excluded_common,
    })
}
