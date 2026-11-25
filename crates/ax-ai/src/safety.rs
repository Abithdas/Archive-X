//! What the local assistant may see, and what it may do only with a person's say-so (`ai/ai_safety.py`).
//!
//! 1. **No secrets.** Keys, tokens, passwords, cookies and the like are removed from everything the model reads.
//! 2. **Actions need permission.** Reading is automatic. Anything that changes something is a *proposal* that waits for a person; a proposal
//!    expires, is used once, and every step is written to the log. An action that is not in the table is forbidden.
//!
//! Python's `re` is matched here, not the `regex` crate's idea of a word, a space or a letter case (see `ax_core::scene_parser`): the
//! patterns are rewritten with the same helpers so the same text is hidden.

use ax_core::scene_parser::{boundary, ci, words, S};
use fancy_regex::Regex;
use serde_json::{json, Map, Value};
use std::sync::OnceLock;

pub const HIDDEN: &str = "[hidden]";

/// `[A-Za-z]` as `re.IGNORECASE` sees it.
const ALPHA_CI: &str = "A-Za-z\u{130}\u{131}\u{17f}\u{212a}";

fn compile(pattern: &str) -> Regex {
    Regex::new(pattern).unwrap_or_else(|e| panic!("pattern {pattern}: {e}"))
}

/// How a replacement is made from what a pattern matched.
type Replace = fn(&fancy_regex::Captures) -> String;

struct Patterns {
    /// `(re, replacement)`: the replacement is built from the match by the function.
    list: Vec<(Regex, Replace)>,
    secret_name: Regex,
    not_secret: Regex,
}

fn patterns() -> &'static Patterns {
    static P: OnceLock<Patterns> = OnceLock::new();
    P.get_or_init(|| {
        let b = boundary();
        let alnum = format!("[{ALPHA_CI}0-9");
        let kw = words(&[
            "api[_-]?key",
            "api[_-]?hash",
            "token",
            "secret",
            "passw(?:or)?d",
            "passwd",
            "passphrase",
            "authorization",
            "cookie",
            "session[_-]?(?:id|string)?",
            "private[_-]?key",
        ]);
        let ns = format!("[^{S_NEG},;\"'}}\\]]", S_NEG = r"\s\x1c-\x1f");
        let list: Vec<(Regex, Replace)> = vec![
            (compile(&format!(r"(?i){b}({bb})(?:{S})+{alnum}._~+/=-]{{8,}}", bb = words(&["bearer", "basic"]))), |c| format!("{} {HIDDEN}", &c[1])),
            (compile(&format!(r#"(?i){b}({kw}){b}({S}*["']?{S}*[:=]{S}*["']?){ns}+"#)), |c| format!("{}{}{HIDDEN}", &c[1], &c[2])),
            (compile(r"(?<=//)[^/\s:@]+:[^/\s@]+(?=@)"), |_| HIDDEN.to_string()),
            (compile(&format!(r"{b}\d{{6,}}:[A-Za-z0-9_-]{{30,}}{b}")), |_| HIDDEN.to_string()),
            (compile(r"-----BEGIN [A-Z ]*PRIVATE KEY-----[\s\S]*?-----END [A-Z ]*PRIVATE KEY-----"), |_| HIDDEN.to_string()),
            (compile(&format!(r"{b}eyJ[A-Za-z0-9_-]{{8,}}\.[A-Za-z0-9_-]{{8,}}\.[A-Za-z0-9_-]{{8,}}{b}")), |_| HIDDEN.to_string()),
            (compile(&format!(r"{b}[A-Fa-f0-9]{{32,}}{b}")), |_| HIDDEN.to_string()),
            (compile(&format!(r"{b}(?=[A-Za-z0-9]*\d)(?=[A-Za-z0-9]*[a-z])(?=[A-Za-z0-9]*[A-Z])[A-Za-z0-9]{{40,}}{b}")), |_| HIDDEN.to_string()),
        ];
        let sep = r"[_\s\x1c-\x1f-]";
        let lb = format!("(?<![{ALPHA_CI}])");
        let la = format!("(?![{ALPHA_CI}])");
        let secret_name = compile(&format!(
            "(?i)({api}{sep}?({key}|{hash})|{lb}{api}{sep}?{id}|{token}|{secret}|{lb}{pass}({word}|{wd}|{phrase})?{la}|{cookie}|{session}|{authori}[{sz}]{ation}|{credential}|\
             {private}{sep}?{key}|{signing}{sep}?({key}|{secret})|{lb}({salt}|{otp}|{pin}|2fa){la}|{string}{sep}?{session}|{bearer})",
            api = ci("api"),
            key = ci("key"),
            hash = ci("hash"),
            id = ci("id"),
            token = ci("token"),
            secret = ci("secret"),
            pass = ci("pass"),
            word = ci("word"),
            wd = ci("wd"),
            phrase = ci("phrase"),
            cookie = ci("cookie"),
            session = ci("session"),
            authori = ci("authori"),
            ation = ci("ation"),
            sz = "sz\u{17f}",
            credential = ci("credential"),
            private = ci("private"),
            signing = ci("signing"),
            salt = ci("salt"),
            otp = ci("otp"),
            pin = ci("pin"),
            string = ci("string"),
            bearer = ci("bearer"),
        ));
        let not_secret = compile(&format!(
            "(?i)(?:^|_)(?:{w})s?(?=\\n?\\z)|^(?:{p})_",
            w = words(&["count", "total", "num", "number", "size", "limit", "max", "min", "per_second", "used", "ms", "seconds", "rate"]),
            p = words(&["max", "min", "num", "total"]),
        ));
        Patterns { list, secret_name, not_secret }
    })
}

/// Whether a field or setting name holds a secret by its name (`api_key`, `password`, `session_cookie`, …).
pub fn is_secret_name(name: &str) -> bool {
    let p = patterns();
    !name.is_empty() && !p.not_secret.is_match(name).unwrap_or(false) && p.secret_name.is_match(name).unwrap_or(false)
}

/// The text with anything that looks like a secret replaced by `[hidden]`.
pub fn redact_text(text: &str) -> String {
    let mut out = text.to_string();
    for (re, replacement) in &patterns().list {
        let mut result = String::with_capacity(out.len());
        let mut last = 0;
        for caps in re.captures_iter(&out).flatten() {
            let whole = caps.get(0).expect("group 0");
            result.push_str(&out[last..whole.start()]);
            result.push_str(&replacement(&caps));
            last = whole.end();
        }
        result.push_str(&out[last..]);
        out = result;
    }
    out
}

/// A copy of `value` safe to show the model: secret-named fields are hidden whole, other text is scrubbed.
pub fn redact(value: &Value) -> Value {
    redact_at(value, 0)
}

fn redact_at(value: &Value, depth: usize) -> Value {
    if depth > 12 {
        return Value::String(HIDDEN.into());
    }
    match value {
        Value::Object(map) => Value::Object(
            map.iter()
                .map(|(k, v)| {
                    let empty = matches!(v, Value::Null | Value::Bool(false)) || v.as_str() == Some("") || v.as_f64() == Some(0.0);
                    (k.clone(), if is_secret_name(k) && !empty { Value::String(HIDDEN.into()) } else { redact_at(v, depth + 1) })
                })
                .collect(),
        ),
        Value::Array(items) => Value::Array(items.iter().map(|v| redact_at(v, depth + 1)).collect()),
        Value::String(s) => Value::String(redact_text(s)),
        other => other.clone(),
    }
}

// ---------------------------------------------------------------- permissions

#[derive(Debug, Clone, Copy, PartialEq, Eq, PartialOrd, Ord)]
pub enum Tier {
    /// Looks at things: automatic.
    Read = 0,
    /// Changes something that can be undone: one Allow.
    Reversible = 1,
    /// Deletes, moves, overwrites, sends: explicit confirmation of the exact items.
    Destructive = 2,
    /// Never, whatever anyone clicks.
    Forbidden = 3,
}

impl Tier {
    pub fn name(self) -> &'static str {
        match self {
            Tier::Read => "READ",
            Tier::Reversible => "REVERSIBLE",
            Tier::Destructive => "DESTRUCTIVE",
            Tier::Forbidden => "FORBIDDEN",
        }
    }
}

/// What each action is allowed to be. An action that is not listed is forbidden: new powers have to be added here on purpose.
pub const ACTION_TIERS: [(&str, Tier); 15] = [
    ("start_scan", Tier::Reversible),
    ("queue_job", Tier::Reversible),
    ("rerun_metadata_match", Tier::Reversible),
    ("change_setting", Tier::Reversible),
    ("apply_suggestion", Tier::Reversible),
    ("apply_code_patch", Tier::Destructive),
    ("delete_media", Tier::Destructive),
    ("delete_performer", Tier::Destructive),
    ("move_media", Tier::Destructive),
    ("send_to_telegram", Tier::Destructive),
    ("bulk_edit", Tier::Destructive),
    ("read_secret", Tier::Forbidden),
    ("run_shell", Tier::Forbidden),
    ("run_sql", Tier::Forbidden),
    ("fetch_url", Tier::Forbidden),
];

pub fn tier_of(action: &str) -> Tier {
    ACTION_TIERS.iter().find(|(a, _)| *a == action).map_or(Tier::Forbidden, |(_, t)| *t)
}

pub const PENDING: &str = "PENDING";
pub const APPROVED: &str = "APPROVED";
pub const DECLINED: &str = "DECLINED";
pub const EXPIRED: &str = "EXPIRED";
pub const DONE: &str = "DONE";
pub const FAILED: &str = "FAILED";
pub const TTL_SECONDS: f64 = 10.0 * 60.0;
pub const MAX_ITEMS_SHOWN: usize = 50;

/// Settings the assistant may never change, even with permission: the ones that hold a secret or open the app up.
fn protected_setting(name: &str) -> bool {
    static R: OnceLock<Regex> = OnceLock::new();
    R.get_or_init(|| {
        let alt = ["key", "token", "secret", "pass", "cookie", "session", "credential", "web_username", "weblogin", "auth", "tls", "cert", "endpoint"];
        compile(&format!("(?i)({})", words(&alt)))
    })
    .is_match(name)
    .unwrap_or(false)
}

/// Anything the broker refuses; the message is plain words for the person.
#[derive(Debug, Clone, PartialEq, Eq)]
pub struct NotAllowed(pub String);

impl std::fmt::Display for NotAllowed {
    fn fmt(&self, f: &mut std::fmt::Formatter<'_>) -> std::fmt::Result {
        f.write_str(&self.0)
    }
}
impl std::error::Error for NotAllowed {}

fn refuse<T>(msg: impl Into<String>) -> Result<T, NotAllowed> {
    Err(NotAllowed(msg.into()))
}

#[derive(Debug, Clone)]
pub struct Proposal {
    pub id: String,
    pub action: String,
    pub tier: Tier,
    pub summary: String,
    pub items: Vec<String>,
    pub args: Map<String, Value>,
    pub created: f64,
    pub expires: f64,
    pub status: &'static str,
    /// The exact words that must be typed to confirm (more than one destructive item).
    pub needs_typed: String,
    pub result: Value,
    pub history: Vec<(f64, String)>,
}

impl Proposal {
    /// What the page shows: never the raw arguments (they may hold paths or ids the person need not see). `now` is the wall clock.
    pub fn public(&self, now: f64) -> Value {
        json!({
            "id": self.id, "action": self.action, "tier": self.tier.name(), "summary": self.summary,
            "items": self.items.iter().take(MAX_ITEMS_SHOWN).collect::<Vec<_>>(),
            "more": self.items.len().saturating_sub(MAX_ITEMS_SHOWN), "status": self.status, "needs_typed": self.needs_typed,
            "expires_in": ((self.expires - now) as i64).max(0),
        })
    }
}

/// What an executor reports when it fails: the name of the error type and its text.
pub type ExecError = (String, String);
pub type Executor = Box<dyn Fn(&Map<String, Value>) -> Result<Value, ExecError>>;
/// `(message, level, event, details)`.
pub type LogFn = Box<dyn Fn(&str, &str, &str, &Value)>;

/// Holds the assistant's proposals until a person decides. Nothing runs without an APPROVED, unexpired, unused proposal.
pub struct PermissionBroker {
    log: Option<LogFn>,
    clock: Box<dyn Fn() -> f64>,
    new_id: Box<dyn FnMut() -> String>,
    items: Vec<Proposal>,
    executors: Vec<(String, Executor)>,
}

fn truncate_chars(s: &str, n: usize) -> String {
    s.chars().take(n).collect()
}

impl PermissionBroker {
    pub fn new(log: Option<LogFn>, clock: Box<dyn Fn() -> f64>, new_id: Box<dyn FnMut() -> String>) -> Self {
        PermissionBroker { log, clock, new_id, items: Vec::new(), executors: Vec::new() }
    }

    /// With the system clock and random 16-digit hex ids.
    pub fn with_system(log: Option<LogFn>) -> Self {
        let clock = || std::time::SystemTime::now().duration_since(std::time::UNIX_EPOCH).map(|d| d.as_secs_f64()).unwrap_or(0.0);
        let new_id = || {
            let mut b = [0u8; 8];
            getrandom::getrandom(&mut b).expect("the system random source is available");
            b.iter().map(|x| format!("{x:02x}")).collect()
        };
        Self::new(log, Box::new(clock), Box::new(new_id))
    }

    pub fn register(&mut self, action: &str, executor: Executor) -> Result<(), NotAllowed> {
        if tier_of(action) == Tier::Forbidden {
            return refuse(format!("'{action}' can never be given to the assistant"));
        }
        self.executors.retain(|(a, _)| a != action);
        self.executors.push((action.to_string(), executor));
        Ok(())
    }

    fn audit(&mut self, index: usize, step: &str, extra: &str) {
        let now = (self.clock)();
        let p = &mut self.items[index];
        p.history.push((now, step.to_string()));
        if let Some(log) = &self.log {
            let message = format!("AI action {step}: {} ({}) {}{extra}", p.action, p.tier.name(), truncate_chars(&p.summary, 160));
            let details = json!({"id": p.id, "action": p.action, "tier": p.tier.name(), "items": p.items.len()});
            log(&message, if step != "refused" { "INFO" } else { "WARN" }, &format!("ai.action.{step}"), &details);
        }
    }

    fn expire(&mut self) {
        let now = (self.clock)();
        for i in 0..self.items.len() {
            let p = &mut self.items[i];
            if (p.status == PENDING || p.status == APPROVED) && now >= p.expires {
                p.status = EXPIRED;
                self.audit(i, "expired", "");
            }
        }
    }

    fn index_of(&mut self, id: &str) -> Option<usize> {
        self.expire();
        self.items.iter().position(|p| p.id == id)
    }

    // ---- the assistant proposes
    pub fn propose(&mut self, action: &str, summary: &str, items: &[String], args: &Map<String, Value>) -> Result<&Proposal, NotAllowed> {
        let tier = tier_of(action);
        let shown: Vec<String> = items.iter().map(|i| truncate_chars(i, 200)).collect();
        let id = (self.new_id)();
        let created = (self.clock)();
        let expires = (self.clock)() + TTL_SECONDS;
        let probe = Proposal {
            id,
            action: action.to_string(),
            tier,
            summary: truncate_chars(&redact_text(summary), 300),
            items: shown.clone(),
            args: args.clone(),
            created,
            expires,
            status: PENDING,
            needs_typed: String::new(),
            result: Value::Null,
            history: Vec::new(),
        };
        // The refusals are written to the log too, so the probe is held for the audit line and dropped again.
        self.items.push(probe);
        let index = self.items.len() - 1;
        let refusal = if tier == Tier::Forbidden {
            Some(("", "The assistant is not allowed to do that."))
        } else if action == "change_setting" && protected_setting(args.get("name").map(py_str).unwrap_or_default().as_str()) {
            Some((" (protected setting)", "That setting holds a secret or controls access; the assistant cannot change it."))
        } else {
            None
        };
        if let Some((extra, message)) = refusal {
            self.audit(index, "refused", extra);
            self.items.pop();
            return refuse(message);
        }
        if tier == Tier::Destructive && shown.is_empty() {
            self.items.pop();
            return refuse("A destructive action has to say exactly which items it touches.");
        }
        let mut proposal = self.items.pop().expect("the probe");
        if tier == Tier::Destructive && shown.len() > 1 {
            proposal.needs_typed = format!("{} {}", action.split('_').next().unwrap_or("").to_uppercase(), shown.len());
        }
        self.expire();
        self.items.push(proposal);
        let index = self.items.len() - 1;
        self.audit(index, "proposed", "");
        Ok(&self.items[index])
    }

    /// Every proposal ever made, oldest first, without expiring anything (what the page lists).
    pub fn proposals(&self) -> &[Proposal] {
        &self.items
    }

    pub fn pending(&mut self) -> Vec<&Proposal> {
        self.expire();
        self.items.iter().filter(|p| p.status == PENDING).collect()
    }

    pub fn get(&mut self, id: &str) -> Option<&Proposal> {
        let i = self.index_of(id)?;
        Some(&self.items[i])
    }

    // ---- the person decides
    pub fn decide(&mut self, id: &str, approve: bool, typed: &str) -> Result<&Proposal, NotAllowed> {
        let Some(i) = self.index_of(id) else { return refuse("That request is not there any more.") };
        let status = self.items[i].status;
        if status != PENDING {
            return refuse(format!("That request is already {}.", status.to_lowercase()));
        }
        if !approve {
            self.items[i].status = DECLINED;
            self.audit(i, "declined", "");
            return Ok(&self.items[i]);
        }
        let needs = self.items[i].needs_typed.clone();
        if !needs.is_empty() && ax_core::scene_parser::py_strip(typed).to_uppercase() != needs {
            return refuse(format!("To confirm, type exactly: {needs}"));
        }
        self.items[i].status = APPROVED;
        self.audit(i, "approved", "");
        Ok(&self.items[i])
    }

    // ---- running what was approved
    /// Run an approved proposal once. The result (or the failure) is kept on it.
    pub fn run(&mut self, id: &str) -> Result<&Proposal, NotAllowed> {
        let Some(i) = self.index_of(id) else { return refuse("That request is not there any more.") };
        let status = self.items[i].status;
        if status != APPROVED {
            return refuse(if status == PENDING { "That request has not been approved.".to_string() } else { format!("That request is {}.", status.to_lowercase()) });
        }
        let action = self.items[i].action.clone();
        let Some(pos) = self.executors.iter().position(|(a, _)| *a == action) else { return refuse("Nothing is set up to carry that out.") };
        self.items[i].status = DONE; // marked first: it can never run twice, even if the executor fails or is slow
        let outcome = (self.executors[pos].1)(&self.items[i].args.clone());
        match outcome {
            Ok(value) => {
                self.items[i].result = redact(&value);
                self.audit(i, "done", "");
            }
            Err((kind, message)) => {
                self.items[i].status = FAILED;
                let error = truncate_chars(&redact_text(&format!("{kind}: {message}")), 300);
                self.items[i].result = json!({ "error": error });
                self.audit(i, "failed", &format!(" {error}"));
            }
        }
        Ok(&self.items[i])
    }
}

/// `str(value)` of a JSON value, for the setting name Python reads with `str((args or {}).get("name", ""))`.
fn py_str(v: &Value) -> String {
    match v {
        Value::String(s) => s.clone(),
        Value::Null => "None".into(),
        Value::Bool(true) => "True".into(),
        Value::Bool(false) => "False".into(),
        other => other.to_string(),
    }
}
