//! Small pieces of Python behaviour the configuration code relies on: `int()` and `float()` of text, `repr()` of a string, `json.dumps`
//! with `indent=2`, `Path.expanduser().resolve()`, and the text of the errors those raise.

use crate::{ConfigError, Env};
use ax_core::scene_parser::digit_value;
use serde_json::Value;
use std::path::{Component, Path, PathBuf};
use unicode_properties::{GeneralCategory, UnicodeGeneralCategory};

/// What `str.strip()` removes: Unicode white space plus U+001C to U+001F.
pub fn is_py_space(c: char) -> bool {
    c.is_whitespace() || ('\u{1c}'..='\u{1f}').contains(&c)
}

pub fn py_strip(s: &str) -> &str {
    s.trim_matches(is_py_space)
}

/// The white space `int()` and `float()` skip around a number: Unicode white space, but not U+001C to U+001F, which `str.strip()` removes
/// and the number parsers refuse.
fn strip_for_number(s: &str) -> &str {
    s.trim_matches(char::is_whitespace)
}

/// `repr(str)`: single quotes unless the text holds a single quote and no double quote; control and non-printing characters escaped.
pub fn py_repr_str(s: &str) -> String {
    let quote = if s.contains('\'') && !s.contains('"') { '"' } else { '\'' };
    let mut out = String::new();
    out.push(quote);
    for c in s.chars() {
        match c {
            '\\' => out.push_str("\\\\"),
            '\n' => out.push_str("\\n"),
            '\r' => out.push_str("\\r"),
            '\t' => out.push_str("\\t"),
            c if c == quote => {
                out.push('\\');
                out.push(c);
            }
            c if (c as u32) < 0x20 || c as u32 == 0x7f => out.push_str(&format!("\\x{:02x}", c as u32)),
            c if (c as u32) < 0x7f => out.push(c),
            c => {
                let printable = !matches!(
                    c.general_category(),
                    GeneralCategory::Control
                        | GeneralCategory::Format
                        | GeneralCategory::Surrogate
                        | GeneralCategory::PrivateUse
                        | GeneralCategory::Unassigned
                        | GeneralCategory::LineSeparator
                        | GeneralCategory::ParagraphSeparator
                        | GeneralCategory::SpaceSeparator
                );
                if printable {
                    out.push(c);
                } else if (c as u32) < 0x100 {
                    out.push_str(&format!("\\x{:02x}", c as u32));
                } else if (c as u32) < 0x10000 {
                    out.push_str(&format!("\\u{:04x}", c as u32));
                } else {
                    out.push_str(&format!("\\U{:08x}", c as u32));
                }
            }
        }
    }
    out.push(quote);
    out
}

/// `int(text)`: white space around (see `strip_for_number`), an optional sign, decimal digits (any script) with single underscores between digits.
/// Values beyond 64 bits are held at the limit; the callers use them as port numbers.
pub fn py_int_str(text: &str) -> Result<i64, ConfigError> {
    let bad = || ConfigError::value(format!("invalid literal for int() with base 10: {}", py_repr_str(text)));
    let t = strip_for_number(text);
    let (negative, digits) = match t.strip_prefix('-') {
        Some(rest) => (true, rest),
        None => (false, t.strip_prefix('+').unwrap_or(t)),
    };
    if digits.is_empty() || !underscores_between_digits(digits) {
        return Err(bad());
    }
    let mut value: i128 = 0;
    for c in digits.chars().filter(|c| *c != '_') {
        let d = digit_value(c).ok_or_else(bad)?;
        value = (value * 10 + d as i128).min(i128::from(i64::MAX) + 1);
    }
    let value = if negative { -value } else { value };
    Ok(value.clamp(i128::from(i64::MIN), i128::from(i64::MAX)) as i64)
}

fn is_digit(c: char) -> bool {
    digit_value(c).is_some()
}

/// Every underscore has a digit on both sides.
fn underscores_between_digits(s: &str) -> bool {
    let chars: Vec<char> = s.chars().collect();
    chars.iter().enumerate().all(|(i, &c)| c != '_' || (i > 0 && i + 1 < chars.len() && is_digit(chars[i - 1]) && is_digit(chars[i + 1])))
}

/// `float(text)`: like `int`, plus a fraction, an exponent, and `inf`, `infinity` and `nan` in any letter case.
pub fn py_float_str(text: &str) -> Result<f64, ConfigError> {
    let bad = || ConfigError::value(format!("could not convert string to float: {}", py_repr_str(text)));
    let t = strip_for_number(text);
    if t.is_empty() || !underscores_between_digits(t) {
        return Err(bad());
    }
    let ascii: String = t.chars().filter(|c| *c != '_').map(|c| digit_value(c).and_then(|d| char::from_digit(d, 10)).unwrap_or(c)).collect();
    if !ascii.is_ascii() {
        return Err(bad());
    }
    ascii.parse::<f64>().map_err(|_| bad())
}

/// The name of the Python type of a JSON value.
pub fn py_type_name(v: &Value) -> &'static str {
    match v {
        Value::Null => "NoneType",
        Value::Bool(_) => "bool",
        Value::Number(n) if n.is_f64() => "float",
        Value::Number(_) => "int",
        Value::String(_) => "str",
        Value::Array(_) => "list",
        Value::Object(_) => "dict",
    }
}

/// Python truthiness of a JSON value.
pub fn py_truthy(v: &Value) -> bool {
    match v {
        Value::Null => false,
        Value::Bool(b) => *b,
        Value::Number(n) => n.as_f64() != Some(0.0),
        Value::String(s) => !s.is_empty(),
        Value::Array(a) => !a.is_empty(),
        Value::Object(o) => !o.is_empty(),
    }
}

/// `str(value or "")` for a value read from a settings file: nothing for a falsy value, the text for a string, the digits for an
/// integer, `True` for true. A non-zero float, a list or an object has no faithful text here and is refused.
pub fn py_str_or_empty(v: &Value) -> Result<String, ConfigError> {
    if !py_truthy(v) {
        return Ok(String::new());
    }
    match v {
        Value::String(s) => Ok(s.clone()),
        Value::Bool(_) => Ok("True".into()),
        Value::Number(n) if !n.is_f64() => Ok(n.to_string()),
        other => Err(ConfigError::new("TypeError", format!("a {} where a path was expected is not supported", py_type_name(other)))),
    }
}

/// `Path(value)` where the value came from JSON: a string is fine, anything else is the TypeError Python raises.
pub fn path_arg(v: &Value) -> Result<&str, ConfigError> {
    match v {
        Value::String(s) => Ok(s),
        other => Err(ConfigError::new(
            "TypeError",
            format!("argument should be a str or an os.PathLike object where __fspath__ returns a str, not '{}'", py_type_name(other)),
        )),
    }
}

/// `Path` joins and normalises without touching the disk: repeated separators and `.` go, `..` stays.
pub fn lexical(p: impl AsRef<Path>) -> PathBuf {
    p.as_ref().components().collect()
}

/// `Path(text).expanduser()`: `~` and `~/…` become the home folder.
pub fn expanduser(text: &str, env: &Env) -> Result<PathBuf, ConfigError> {
    let p = Path::new(text);
    let mut parts = p.components();
    match parts.next() {
        Some(Component::Normal(first)) if first == "~" => Ok(lexical(env.home.join(parts.as_path()))),
        Some(Component::Normal(first)) if first.to_string_lossy().starts_with('~') => Err(ConfigError::runtime("a ~user path is not supported")),
        _ => Ok(lexical(p)),
    }
}

/// `Path(text).expanduser().resolve()`: absolute against the working folder, `..` applied, links followed as far as they exist.
pub fn resolve(text: &str, env: &Env) -> Result<PathBuf, ConfigError> {
    let p = expanduser(text, env)?;
    let abs = if p.as_os_str().is_empty() {
        env.cwd.clone()
    } else if p.is_absolute() {
        p
    } else {
        env.cwd.join(p)
    };
    resolve_path(&abs)
}

/// `Path(path).resolve()` for a path that is already absolute.
pub fn resolve_path(abs: &Path) -> Result<PathBuf, ConfigError> {
    ax_core::safe_paths::realpath(abs).map_err(|e| ConfigError::os(&e, abs))
}

/// `json.dumps(value, indent=2, ensure_ascii=...)`.
pub fn dumps_indent2(value: &Value, ensure_ascii: bool) -> String {
    let mut out = String::new();
    write_value(&mut out, value, 0, ensure_ascii);
    out
}

fn write_string(out: &mut String, s: &str, ensure_ascii: bool) {
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
            c if ensure_ascii && !c.is_ascii() => {
                let mut units = [0u16; 2];
                for unit in c.encode_utf16(&mut units) {
                    out.push_str(&format!("\\u{:04x}", unit));
                }
            }
            c => out.push(c),
        }
    }
    out.push('"');
}

fn write_value(out: &mut String, v: &Value, depth: usize, ensure_ascii: bool) {
    let pad = |out: &mut String, n: usize| out.push_str(&" ".repeat(n * 2));
    match v {
        Value::String(s) => write_string(out, s, ensure_ascii),
        Value::Array(a) if a.is_empty() => out.push_str("[]"),
        Value::Object(o) if o.is_empty() => out.push_str("{}"),
        Value::Array(a) => {
            out.push_str("[\n");
            for (i, item) in a.iter().enumerate() {
                pad(out, depth + 1);
                write_value(out, item, depth + 1, ensure_ascii);
                out.push_str(if i + 1 < a.len() { ",\n" } else { "\n" });
            }
            pad(out, depth);
            out.push(']');
        }
        Value::Object(o) => {
            out.push_str("{\n");
            for (i, (k, item)) in o.iter().enumerate() {
                pad(out, depth + 1);
                write_string(out, k, ensure_ascii);
                out.push_str(": ");
                write_value(out, item, depth + 1, ensure_ascii);
                out.push_str(if i + 1 < o.len() { ",\n" } else { "\n" });
            }
            pad(out, depth);
            out.push('}');
        }
        other => out.push_str(&other.to_string()),
    }
}

/// `html.escape(text, quote=True)`.
pub fn html_escape(s: &str) -> String {
    s.replace('&', "&amp;").replace('<', "&lt;").replace('>', "&gt;").replace('"', "&quot;").replace('\'', "&#x27;")
}

/// `json.dumps(text)`: a JSON string with every non-ASCII character escaped.
pub fn json_string_ascii(s: &str) -> String {
    let mut out = String::new();
    write_string(&mut out, s, true);
    out
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn ints() {
        assert_eq!(py_int_str(" 8_0 ").unwrap(), 80);
        assert_eq!(py_int_str("+5").unwrap(), 5);
        assert_eq!(py_int_str("٣٤").unwrap(), 34);
        assert!(py_int_str("\u{1c}42").is_err());
        assert_eq!(py_int_str("\u{a0}42\u{3000}").unwrap(), 42);
        assert!(py_int_str("8__0").is_err());
        assert!(py_int_str("80_").is_err());
        assert!(py_int_str("").is_err());
        assert!(py_int_str("1.5").is_err());
        assert_eq!(py_int_str("99999999999999999999999").unwrap(), i64::MAX);
    }

    #[test]
    fn floats() {
        assert_eq!(py_float_str("1_0.5").unwrap(), 10.5);
        assert_eq!(py_float_str(" .5e2 ").unwrap(), 50.0);
        assert!(py_float_str("1,5").is_err());
        assert!(py_float_str("_1").is_err());
        assert!(py_float_str("1_.5").is_err());
        assert!(py_float_str("NaN").unwrap().is_nan());
        assert_eq!(py_float_str("-Infinity").unwrap(), f64::NEG_INFINITY);
    }

    #[test]
    fn repr() {
        assert_eq!(py_repr_str("abc"), "'abc'");
        assert_eq!(py_repr_str("it's"), "\"it's\"");
        assert_eq!(py_repr_str("it's \"q\""), "'it\\'s \"q\"'");
        assert_eq!(py_repr_str("a\tb\u{1}\u{a0}é"), "'a\\tb\\x01\\xa0é'");
    }

    #[test]
    fn pretty_json() {
        let v: Value = serde_json::from_str(r#"{"a":[],"b":{"c":[1,"é"]},"d":{}}"#).unwrap();
        assert_eq!(dumps_indent2(&v, true), "{\n  \"a\": [],\n  \"b\": {\n    \"c\": [\n      1,\n      \"\\u00e9\"\n    ]\n  },\n  \"d\": {}\n}");
        assert!(dumps_indent2(&v, false).contains("\"é\""));
        assert_eq!(json_string_ascii("😀"), "\"\\ud83d\\ude00\"");
    }
}
