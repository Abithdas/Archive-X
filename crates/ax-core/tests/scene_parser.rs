//! The Rust scene-name parser must give what `metadata/parser.py` gives, and `oshash_from_parts` what `metadata/fingerprint.py` gives.
//! Recorded by `tools/parity/gen_scene_parser_vectors.py`.

use ax_core::scene_parser::*;
use serde_json::Value;

fn cases() -> Value {
    serde_json::from_str(include_str!("scene_parser_cases.json")).unwrap()
}

#[test]
fn every_recorded_name_gives_the_recorded_text_key_and_hints() {
    let cases = cases();
    let names = cases["names"].as_array().unwrap();
    assert!(names.len() > 1500);
    let mut failures = Vec::new();
    for case in names {
        let name = case["name"].as_str().unwrap();
        if clean_text(name) != case["clean"].as_str().unwrap() {
            failures.push(format!("clean {name:?}: {:?} vs {}", clean_text(name), case["clean"]));
        }
        if canonical_key(name) != case["key"].as_str().unwrap() {
            failures.push(format!("key {name:?}: {:?} vs {}", canonical_key(name), case["key"]));
        }
        let got = parse_scene_name(name).to_json();
        if got != case["parsed"] {
            failures.push(format!("parse {name:?}:\n   rust   {got}\n   python {}", case["parsed"]));
        }
    }
    assert!(failures.is_empty(), "{} of {} differ:\n{}", failures.len(), names.len(), failures.iter().take(15).cloned().collect::<Vec<_>>().join("\n"));
}

#[test]
fn generic_codes_and_fused_quality_match() {
    let cases = cases();
    for c in cases["generic"].as_array().unwrap() {
        let v = c["value"].as_str().unwrap_or("");
        assert_eq!(is_generic_scene_code(v), c["result"].as_bool().unwrap(), "{v:?}");
    }
    for c in cases["fused"].as_array().unwrap() {
        let v = c["value"].as_str().unwrap();
        assert_eq!(split_fused_quality_before_date(v), c["result"].as_str().unwrap(), "{v:?}");
    }
}

#[test]
fn oshash_matches_pythons() {
    let cases = cases();
    for c in cases["oshash"].as_array().unwrap() {
        let size = c["size"].as_i64().unwrap();
        if let Some(seed) = c.get("seed").and_then(Value::as_u64) {
            let f = |i: i128| ((i * 31 + (i >> 8) * 7 + seed as i128) & 0xff) as u8;
            let want = size.min(65536) as i128;
            let head: Vec<u8> = (0..want).map(f).collect();
            let tail: Vec<u8> = ((size as i128 - want)..size as i128).map(f).collect();
            assert_eq!(oshash_from_parts(size, &head, &tail).unwrap(), c["result"].as_str().unwrap(), "size {size}");
        } else {
            let (hl, tl) = (c["head_len"].as_u64().unwrap() as usize, c["tail_len"].as_u64().unwrap() as usize);
            let got = oshash_from_parts(size, &vec![0; hl], &vec![0; tl]);
            assert_eq!(got.unwrap_err(), c["result"]["error"].as_str().unwrap(), "size {size} {hl}+{tl}");
        }
    }
}
