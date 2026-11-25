//! Python's own ideas of white space and lines, which the notebook and knowledge code lean on.

use ax_core::scene_parser::is_py_space;

/// `str.split()` with no argument: runs of white space (Unicode white space and U+001C to U+001F) separate the words.
pub fn split_words(s: &str) -> impl Iterator<Item = &str> {
    s.split(is_py_space).filter(|w| !w.is_empty())
}

/// `str.splitlines()`: lines end at `\n`, `\r`, `\r\n`, vertical tab, form feed, U+001C to U+001E, U+0085, U+2028 and U+2029.
pub fn splitlines(s: &str) -> Vec<&str> {
    let mut out = Vec::new();
    let mut start = 0;
    let mut iter = s.char_indices().peekable();
    while let Some((i, c)) = iter.next() {
        let breaks = matches!(c, '\n' | '\r' | '\u{b}' | '\u{c}' | '\u{1c}' | '\u{1d}' | '\u{1e}' | '\u{85}' | '\u{2028}' | '\u{2029}');
        if breaks {
            out.push(&s[start..i]);
            let mut end = i + c.len_utf8();
            if c == '\r' {
                if let Some(&(j, '\n')) = iter.peek() {
                    iter.next();
                    end = j + 1;
                }
            }
            start = end;
        }
    }
    if start < s.len() {
        out.push(&s[start..]);
    }
    out
}

/// `text[:n]` by characters.
pub fn head(s: &str, n: usize) -> String {
    s.chars().take(n).collect()
}

/// `str.lstrip(chars)`.
pub fn lstrip_chars<'a>(s: &'a str, chars: &str) -> &'a str {
    s.trim_start_matches(|c| chars.contains(c))
}

/// `repr(float)`: the shortest text that reads back as the same number; an exponent below 1e-4 and from 1e16 up, with at least two digits.
fn float_repr(x: f64) -> String {
    if x == 0.0 {
        return if x.is_sign_negative() { "-0.0".into() } else { "0.0".into() };
    }
    let sci = format!("{:e}", x.abs());
    let (mantissa, exp) = sci.split_once('e').expect("exponent form");
    let exp: i32 = exp.parse().expect("exponent");
    let digits: String = mantissa.chars().filter(|c| *c != '.').collect();
    let decpt = exp + 1;
    let sign = if x < 0.0 { "-" } else { "" };
    if decpt <= -4 || decpt > 16 {
        let frac = if digits.len() > 1 { format!(".{}", &digits[1..]) } else { String::new() };
        format!("{sign}{}{frac}e{}{:02}", &digits[..1], if exp < 0 { '-' } else { '+' }, exp.abs())
    } else if decpt <= 0 {
        format!("{sign}0.{}{digits}", "0".repeat((-decpt) as usize))
    } else if decpt as usize >= digits.len() {
        format!("{sign}{digits}{}.0", "0".repeat(decpt as usize - digits.len()))
    } else {
        format!("{sign}{}.{}", &digits[..decpt as usize], &digits[decpt as usize..])
    }
}

fn dump_string(out: &mut String, s: &str, ascii: bool) {
    out.push('"');
    for c in s.chars() {
        match c {
            '"' => out.push_str("\\\""),
            '\\' => out.push_str("\\\\"),
            '\n' => out.push_str("\\n"),
            '\r' => out.push_str("\\r"),
            '\t' => out.push_str("\\t"),
            '\u{8}' => out.push_str("\\b"),
            '\u{c}' => out.push_str("\\f"),
            c if (c as u32) < 0x20 => out.push_str(&format!("\\u{:04x}", c as u32)),
            c if ascii && !c.is_ascii() => {
                let mut units = [0u16; 2];
                for unit in c.encode_utf16(&mut units) {
                    out.push_str(&format!("\\u{unit:04x}"));
                }
            }
            c => out.push(c),
        }
    }
    out.push('"');
}

/// `json.dumps(value, ensure_ascii=ascii)`: `", "` between items, `": "` after keys.
pub fn dumps(value: &serde_json::Value, ascii: bool) -> String {
    use serde_json::Value;
    match value {
        Value::Null => "null".into(),
        Value::Bool(b) => b.to_string(),
        Value::Number(n) if n.is_f64() => float_repr(n.as_f64().expect("float")),
        Value::Number(n) => n.to_string(),
        Value::String(s) => {
            let mut out = String::new();
            dump_string(&mut out, s, ascii);
            out
        }
        Value::Array(a) => format!("[{}]", a.iter().map(|v| dumps(v, ascii)).collect::<Vec<_>>().join(", ")),
        Value::Object(o) => format!(
            "{{{}}}",
            o.iter()
                .map(|(k, v)| {
                    let mut key = String::new();
                    dump_string(&mut key, k, ascii);
                    format!("{key}: {}", dumps(v, ascii))
                })
                .collect::<Vec<_>>()
                .join(", ")
        ),
    }
}

#[cfg(test)]
mod tests {
    #[test]
    fn json_text_is_dumped_like_python() {
        use serde_json::json;
        assert_eq!(super::dumps(&json!("é😀\u{8}\u{c}\n"), true), "\"\\u00e9\\ud83d\\ude00\\b\\f\\n\"");
        assert_eq!(super::dumps(&json!("é😀"), false), "\"é😀\"");
        assert_eq!(super::dumps(&json!({"a": [1, 2.5, null, true], "b": {}}), false), "{\"a\": [1, 2.5, null, true], \"b\": {}}");
    }

    #[test]
    fn floats_print_like_python() {
        for (x, want) in [
            (0.1, "0.1"),
            (1e16, "1e+16"),
            (1e15, "1000000000000000.0"),
            (1e-5, "1e-05"),
            (0.0001, "0.0001"),
            (123456789.123, "123456789.123"),
            (-0.0, "-0.0"),
            (1.5e300, "1.5e+300"),
            (100.0, "100.0"),
            (2.5e-7, "2.5e-07"),
            (12345678901234567890.0, "1.2345678901234567e+19"),
            (0.5, "0.5"),
            (-2.0, "-2.0"),
            (1.0000000000000002, "1.0000000000000002"),
        ] {
            assert_eq!(super::float_repr(x), want, "{x:e}");
        }
    }

    use super::*;

    #[test]
    fn lines_split_like_python() {
        assert_eq!(splitlines("a\nb\r\nc\rd\u{2028}e\u{85}f\u{b}g\n"), vec!["a", "b", "c", "d", "e", "f", "g"]);
        assert_eq!(splitlines(""), Vec::<&str>::new());
        assert_eq!(splitlines("\n"), vec![""]);
        assert_eq!(splitlines("a\n\nb"), vec!["a", "", "b"]);
        assert_eq!(splitlines("a\r\r\nb"), vec!["a", "", "b"]);
    }

    #[test]
    fn words_split_like_python() {
        assert_eq!(split_words(" a\u{1c}b\u{a0}\u{3000}c ").collect::<Vec<_>>(), vec!["a", "b", "c"]);
    }
}
