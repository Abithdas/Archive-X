//! The decisions of `core/resource_manager.py` (`InteractiveResourceManager`), without the asyncio waiting.
//!
//! Python's manager is a gate: background work asks to start, and waits while playback is on, the operator paused the background, or a
//! limit is reached. What is decided is a handful of rules over a handful of counters; the waiting (a condition variable and a few
//! cancellable tasks) belongs to whatever runs the loops. This module holds the counters and the rules: [`ResourcePolicy::try_acquire_background`]
//! answers "may this start now?" exactly as the predicate inside Python's `acquire_background` loop does, and the caller waits on its own
//! notification when the answer is no.
//!
//! Not ported here: the governor calls (`set_playback_policy`, `playback_enter` ...) which are side effects for the caller to make from the
//! changes it sees, and the eight-second expiry watcher of interactive playback (a timer task).

use serde_json::{json, Map, Value};
use std::collections::BTreeMap;
use std::sync::Arc;

pub const CONTROL_MODES: [&str; 5] = ["automatic", "playback", "balanced", "background", "manual"];
pub const CATEGORIES: [&str; 5] = ["conversion", "thumbnail", "preview", "metadata", "upload"];

/// `(time.time(), time.monotonic())`.
pub type Clock = Arc<dyn Fn() -> (f64, f64) + Send + Sync>;

#[derive(Debug, PartialEq, Eq)]
pub enum ConfigError {
    InvalidMode,
    LimitRange(String),
    NotAnInteger,
}

impl std::fmt::Display for ConfigError {
    fn fmt(&self, f: &mut std::fmt::Formatter<'_>) -> std::fmt::Result {
        match self {
            ConfigError::InvalidMode => write!(f, "Invalid resource control mode"),
            ConfigError::LimitRange(k) => write!(f, "{k} limit must be between 1 and 8"),
            ConfigError::NotAnInteger => write!(f, "not an integer"),
        }
    }
}

/// Python's `int(value)` for a JSON value: numbers truncate toward zero, strings must be an integer literal.
fn py_int(v: &Value) -> Result<i64, ConfigError> {
    match v {
        Value::Bool(b) => Ok(i64::from(*b)),
        Value::Number(n) => n.as_i64().or_else(|| n.as_f64().filter(|f| f.is_finite()).map(|f| f.trunc() as i64)).ok_or(ConfigError::NotAnInteger),
        Value::String(s) => s.trim().replace('_', "").parse::<i64>().map_err(|_| ConfigError::NotAnInteger),
        _ => Err(ConfigError::NotAnInteger),
    }
}

fn lower_category(category: &str) -> String {
    let c = category.to_lowercase();
    if c.is_empty() {
        "generic".into()
    } else {
        c
    }
}

pub struct ResourcePolicy {
    clock: Clock,
    pub background_capacity: i64,
    pub media_workers: i64,
    hardware_profile: Value,
    cpu: i64,
    category_active: BTreeMap<String, i64>,
    category_waiting: BTreeMap<String, i64>,
    pub playback_streams: i64,
    pub interactive_playback: bool,
    last_playback_activity: f64,
    pub background_active: i64,
    pub background_waiting: i64,
    metadata_active: i64,
    non_metadata_active: i64,
    pub service_active: i64,
    control_mode: String,
    manual_limits: BTreeMap<String, i64>,
    manual_playback_gate: bool,
    override_expires_at: f64,
    override_label: String,
    pub background_paused: bool,
    pub background_pause_reason: String,
}

impl ResourcePolicy {
    /// `hardware_profile` is the dictionary Python gets (`background_capacity`, `media_workers`, `hardware_acceleration` ...).
    pub fn new(hardware_profile: Value, cpu_count: i64, clock: Clock) -> Self {
        let cpu = cpu_count.max(1);
        let profile = if hardware_profile.is_object() { hardware_profile } else { json!({}) };
        let get = |k: &str| profile.get(k).and_then(|v| py_int(v).ok()).unwrap_or(0);
        let suggested = get("background_capacity");
        let fallback = if cpu > 2 { cpu / 2 } else { 1 };
        let background_capacity = 1.max(6.min(if suggested != 0 { suggested } else { fallback }));
        let media_workers = 1.max(4.min(match get("media_workers") {
            0 => 1,
            n => n,
        }));
        let category_active = ["conversion", "thumbnail", "preview", "metadata", "upload"].iter().map(|k| (k.to_string(), 0)).collect();
        ResourcePolicy {
            clock,
            background_capacity,
            media_workers,
            hardware_profile: profile,
            cpu,
            category_active,
            category_waiting: BTreeMap::new(),
            playback_streams: 0,
            interactive_playback: false,
            last_playback_activity: 0.0,
            background_active: 0,
            background_waiting: 0,
            metadata_active: 0,
            non_metadata_active: 0,
            service_active: 0,
            control_mode: "automatic".into(),
            manual_limits: BTreeMap::new(),
            manual_playback_gate: true,
            override_expires_at: 0.0,
            override_label: String::new(),
            background_paused: false,
            background_pause_reason: String::new(),
        }
    }

    fn wall(&self) -> f64 {
        (self.clock)().0
    }
    fn mono(&self) -> f64 {
        (self.clock)().1
    }

    // ---- configuration and background pause ----------------------------------------------------------------------------------------

    /// `configure`. The two-state arguments are `None` for "not given". Like Python, a failure part-way leaves the earlier parts applied.
    pub fn configure(
        &mut self,
        mode: Option<&str>,
        limits: Option<&Map<String, Value>>,
        playback_gate: Option<bool>,
        expires_seconds: Option<&Value>,
        label: &str,
    ) -> Result<(), ConfigError> {
        if let Some(mode) = mode {
            let mode = mode.trim().to_lowercase();
            if !CONTROL_MODES.contains(&mode.as_str()) {
                return Err(ConfigError::InvalidMode);
            }
            self.control_mode = mode;
        }
        if let Some(limits) = limits {
            let mut clean = BTreeMap::new();
            for key in CATEGORIES {
                if let Some(v) = limits.get(key).filter(|v| !v.is_null()) {
                    let value = py_int(v)?;
                    if !(1..=8).contains(&value) {
                        return Err(ConfigError::LimitRange(key.into()));
                    }
                    clean.insert(key.to_string(), value);
                }
            }
            self.manual_limits = clean;
        }
        if let Some(g) = playback_gate {
            self.manual_playback_gate = g;
        }
        if let Some(v) = expires_seconds {
            let seconds = py_int(v)?.max(0);
            self.override_expires_at = if seconds != 0 { self.wall() + seconds as f64 } else { 0.0 };
        }
        self.override_label = label.chars().take(120).collect();
        Ok(())
    }

    pub fn pause_background(&mut self, reason: &str) {
        self.background_paused = true;
        let reason = if reason.is_empty() { "Paused by operator" } else { reason };
        self.background_pause_reason = reason.chars().take(160).collect();
    }

    pub fn resume_background(&mut self) {
        self.background_paused = false;
        self.background_pause_reason.clear();
    }

    /// The control mode, after an expired temporary override has been dropped.
    pub fn effective_mode(&mut self) -> String {
        if self.override_expires_at != 0.0 && self.wall() >= self.override_expires_at {
            self.override_expires_at = 0.0;
            self.override_label.clear();
            self.control_mode = "automatic".into();
            self.manual_playback_gate = true;
        }
        self.control_mode.clone()
    }

    // ---- category limits and status ------------------------------------------------------------------------------------------------

    pub fn playback_blocks_background(&mut self) -> bool {
        match self.effective_mode().as_str() {
            "automatic" | "playback" => true,
            "manual" => self.manual_playback_gate,
            _ => false,
        }
    }

    pub fn playback_blocks_category(&mut self, category: &str) -> bool {
        // PLAYBACK_COMPATIBLE_CATEGORIES: CPU/local conversion only
        if lower_category(category) == "conversion" {
            return false;
        }
        self.playback_blocks_background()
    }

    fn category_limit_auto(&self, category: &str) -> i64 {
        match category {
            "thumbnail" => self.media_workers,
            "preview" => 1,
            "conversion" => 1.max(self.media_workers.min(2)),
            "metadata" => 1.max(self.background_capacity.min(3)),
            "upload" => 1.max(self.background_capacity),
            _ => self.background_capacity,
        }
    }

    pub fn configured_category_limit(&mut self, category: &str) -> i64 {
        let category = lower_category(category);
        if self.effective_mode() != "manual" {
            return self.category_limit_auto(&category);
        }
        self.manual_limits.get(&category).copied().unwrap_or_else(|| self.category_limit_auto(&category))
    }

    pub fn status(&mut self) -> Value {
        let mode = self.effective_mode();
        let now = self.wall();
        let remaining = if self.override_expires_at != 0.0 { ((self.override_expires_at - now) as i64).max(0) } else { 0 };
        let label = match mode.as_str() {
            "automatic" => "Automatic",
            "playback" => "Playback Priority",
            "balanced" => "Balanced",
            "background" => "Background Priority",
            "manual" => "Manual",
            other => other,
        };
        let mut gates = Map::new();
        for c in ["metadata", "thumbnail", "preview", "conversion", "upload", "generic"] {
            gates.insert(c.into(), json!(self.playback_blocks_category(c)));
        }
        let mut limits = Map::new();
        for c in ["conversion", "thumbnail", "preview", "metadata", "upload"] {
            limits.insert(c.into(), json!(self.configured_category_limit(c)));
        }
        json!({
            "mode": mode,
            "mode_label": label,
            "playback_gate": self.playback_blocks_background(),
            "playback_category_gates": gates,
            "manual_limits": self.manual_limits,
            "effective_limits": limits,
            "override_seconds_remaining": remaining,
            "override_label": self.override_label,
            "background_paused": self.background_paused,
            "background_pause_reason": self.background_pause_reason,
        })
    }

    // ---- playback tracking ---------------------------------------------------------------------------------------------------------

    pub fn playback_active(&self) -> bool {
        self.playback_streams > 0 || self.interactive_playback
    }

    /// Returns true when playback just became active (the caller tells the governor).
    pub fn playback_enter(&mut self) -> bool {
        let was = self.playback_active();
        self.playback_streams += 1;
        self.last_playback_activity = self.mono();
        !was
    }

    /// Returns true when playback just ended.
    pub fn playback_exit(&mut self) -> bool {
        self.playback_streams = 0.max(self.playback_streams - 1);
        !self.playback_active()
    }

    pub fn interactive_playback_enter(&mut self) -> bool {
        let was = self.playback_active();
        self.interactive_playback = true;
        self.last_playback_activity = self.mono();
        !was
    }

    pub fn interactive_playback_heartbeat(&mut self) {
        if self.interactive_playback {
            self.last_playback_activity = self.mono();
        }
    }

    pub fn interactive_playback_exit(&mut self) -> bool {
        self.interactive_playback = false;
        !self.playback_active()
    }

    /// `reconcile_playback`: leases the manager holds beyond what the app reports are released; returns how many.
    pub fn reconcile_playback(&mut self, expected_streams: i64) -> i64 {
        let mut expected = 0.max(expected_streams);
        let current = 0.max(self.playback_streams);
        if self.interactive_playback && expected > 0 {
            expected = expected.max(1);
        }
        if self.interactive_playback && current <= 1 {
            return 0;
        }
        if current <= expected {
            return 0;
        }
        self.playback_streams = expected;
        current - expected
    }

    pub fn note_playback_activity(&mut self) {
        self.last_playback_activity = self.mono();
    }

    // ---- background admission ------------------------------------------------------------------------------------------------------

    /// Would `cooperative_background_checkpoint` have to wait?
    pub fn checkpoint_must_wait(&mut self, category: &str) -> bool {
        let category = lower_category(category);
        if !self.playback_active() || !self.playback_blocks_category(&category) {
            return self.background_paused;
        }
        self.background_paused || (self.playback_streams > 0 && self.playback_blocks_category(&category))
    }

    /// Would `acquire_service` have to wait?
    pub fn service_must_wait(&self, block_playback: bool) -> bool {
        self.background_paused || (block_playback && self.playback_active())
    }

    pub fn acquire_service(&mut self) {
        self.service_active += 1;
    }

    pub fn release_service(&mut self) {
        self.service_active = 0.max(self.service_active - 1);
    }

    fn reserved_class_capacity(&self) -> i64 {
        let capacity = 1.max(self.background_capacity);
        if capacity >= 2 {
            1.max(capacity - 1)
        } else {
            1
        }
    }

    /// Count a request that is about to wait (Python does this on entry to `acquire_background`).
    pub fn begin_waiting(&mut self, category: &str) {
        let category = lower_category(category);
        self.background_waiting += 1;
        *self.category_waiting.entry(category).or_insert(0) += 1;
    }

    /// Undo `begin_waiting` (Python's `finally`: when the request was admitted, gave up, or was cancelled).
    pub fn end_waiting(&mut self, category: &str) {
        let category = lower_category(category);
        self.background_waiting = 0.max(self.background_waiting - 1);
        let n = 0.max(self.category_waiting.get(&category).copied().unwrap_or(0) - 1);
        if n <= 0 {
            self.category_waiting.remove(&category);
        } else {
            self.category_waiting.insert(category, n);
        }
    }

    /// The predicate of the `acquire_background` loop; takes the slot when nothing blocks it.
    pub fn try_take_background(&mut self, category: &str) -> bool {
        let category = lower_category(category);
        let is_metadata = category == "metadata";
        let class_active = if is_metadata { self.metadata_active } else { self.non_metadata_active };
        let blocked = self.background_paused
            || (self.playback_blocks_category(&category) && self.playback_active())
            || self.background_active >= self.background_capacity
            || self.category_active.get(&category).copied().unwrap_or(0) >= self.configured_category_limit(&category)
            || class_active >= self.reserved_class_capacity();
        if blocked {
            return false;
        }
        self.background_active += 1;
        *self.category_active.entry(category).or_insert(0) += 1;
        if is_metadata {
            self.metadata_active += 1;
        } else {
            self.non_metadata_active += 1;
        }
        true
    }

    pub fn release_background(&mut self, category: &str) {
        let category = lower_category(category);
        self.background_active = 0.max(self.background_active - 1);
        if let Some(n) = self.category_active.get_mut(&category) {
            *n = 0.max(*n - 1);
        }
        if category == "metadata" {
            self.metadata_active = 0.max(self.metadata_active - 1);
        } else {
            self.non_metadata_active = 0.max(self.non_metadata_active - 1);
        }
    }

    // ---- policy and snapshot -------------------------------------------------------------------------------------------------------

    pub fn media_policy(&mut self) -> Value {
        let snapshot_workers = 1.max(4.min(if self.media_workers != 0 { self.media_workers } else { 1 }));
        let can_parallel = snapshot_workers >= 3 && self.background_capacity >= 2 && !self.playback_active() && !self.background_paused;
        let video_concurrency: i64 = if can_parallel { 2 } else { 1 };
        let playback_priority = self.playback_active() || self.background_paused;
        let frame_workers = if playback_priority { 1 } else { 1.max(2.min(snapshot_workers / video_concurrency)) };
        let range_concurrency = if playback_priority {
            1
        } else if video_concurrency > 1 {
            1.max(2.min(snapshot_workers / video_concurrency))
        } else {
            1.max(4.min(snapshot_workers))
        };
        let accel = self.hardware_profile.get("hardware_acceleration").filter(|v| truthy(v)).cloned().unwrap_or_else(|| json!({}));
        json!({
            "thumbnail_workers": self.configured_category_limit("thumbnail"),
            "preview_workers": self.configured_category_limit("preview"),
            "snapshot_video_concurrency": video_concurrency,
            "snapshot_frame_workers": frame_workers,
            "snapshot_range_concurrency": range_concurrency,
            "conversion_workers": self.configured_category_limit("conversion"),
            "metadata_workers": self.configured_category_limit("metadata"),
            "upload_workers": self.configured_category_limit("upload"),
            "hardware_acceleration": accel,
        })
    }

    pub fn snapshot(&mut self) -> Value {
        let age = if self.last_playback_activity != 0.0 { json!((self.mono() - self.last_playback_activity).max(0.0)) } else { Value::Null };
        let reserved = self.reserved_class_capacity();
        json!({
            "playback_active": self.playback_active(),
            "playback_streams": self.playback_streams,
            "interactive_playback": self.interactive_playback,
            "background_active": self.background_active,
            "background_waiting": self.background_waiting,
            "background_waiting_by_category": self.category_waiting,
            "metadata_active": self.metadata_active,
            "non_metadata_active": self.non_metadata_active,
            "metadata_capacity": reserved,
            "non_metadata_capacity": reserved,
            "metadata_reserved_capacity": if self.background_capacity >= 2 { 1 } else { 0 },
            "service_active": self.service_active,
            "background_capacity": self.background_capacity,
            "media_workers": self.media_workers,
            "category_active": self.category_active,
            "media_policy": self.media_policy(),
            "playback_last_activity_age": age,
            "cpu_count": self.cpu,
            "hardware": self.hardware_profile,
            "control": self.status(),
        })
    }
}

/// Python truthiness of a JSON value.
fn truthy(v: &Value) -> bool {
    match v {
        Value::Null => false,
        Value::Bool(b) => *b,
        Value::Number(n) => n.as_f64().is_some_and(|f| f != 0.0),
        Value::String(s) => !s.is_empty(),
        Value::Array(a) => !a.is_empty(),
        Value::Object(o) => !o.is_empty(),
    }
}
