//! The decisions of `core/governor.py` (`TransferGovernor`): pacing, FloodWait learning, lane limits and permit admission, without the
//! threads, conditions and sleeps.
//!
//! Python's governor is the one gate every Telegram-heavy operation passes. It is a pile of counters and limits that a handful of events
//! move (a FloodWait, a network error, a finished range, playback starting). Here those are plain methods on one struct; each permit is a
//! `try_*` that answers "may it start now?" with the same test Python's wait loop makes, and the caller waits on its own notification
//! when the answer is no. Time comes from a [`Clock`]; log lines are collected in [`Governor::logs`] for the caller to write.
//!
//! Not ported: the spacing the `telegram_*_slot` context managers sleep for between requests (`_rpc_next_at`, `_range_next_at` and the
//! playback equivalents: a caller computes its own delay from `rpc_interval`), the transfer and fingerprint semaphores (a bare counter
//! against a constant), and everything that sleeps.

use crate::resource_policy::{py_int, Clock};
use serde_json::{json, Map, Value};
use std::collections::BTreeMap;

const MIB20: i64 = 20 * 1024 * 1024;

/// The `config` values the governor reads.
#[derive(Clone, Debug)]
pub struct GovernorConfig {
    pub playback_rpc_concurrency: i64,
    pub playback_rpc_interval: f64,
    pub background_range_concurrency: i64,
    pub playback_range_concurrency: i64,
    pub playback_emergency_concurrency: i64,
    pub playback_deadline_seconds: f64,
}

impl Default for GovernorConfig {
    fn default() -> Self {
        GovernorConfig {
            playback_rpc_concurrency: 4,
            playback_rpc_interval: 0.02,
            background_range_concurrency: 8,
            playback_range_concurrency: 6,
            playback_emergency_concurrency: 10,
            playback_deadline_seconds: 3.0,
        }
    }
}

/// Python's `round(x, ndigits)` on the exactly-printed value.
fn round_to(x: f64, digits: usize) -> f64 {
    format!("{x:.digits$}").parse().unwrap_or(x)
}

/// `max(0, r)` where `0` is an int: an int 0 unless `r` is positive.
fn max_int0(r: f64) -> Value {
    if r > 0.0 {
        json!(r)
    } else {
        json!(0)
    }
}

/// `max(0.0, r)`.
fn max_float0(r: f64) -> f64 {
    if r > 0.0 {
        r
    } else {
        0.0
    }
}

/// A Python number that starts as the int `0` and becomes a float once something is stored in it.
#[derive(Clone, Copy, Default)]
struct Num(Option<f64>);

impl Num {
    fn get(self) -> f64 {
        self.0.unwrap_or(0.0)
    }
    fn set(&mut self, v: f64) {
        self.0 = Some(v);
    }
    fn json(self) -> Value {
        match self.0 {
            Some(v) => json!(v),
            None => json!(0),
        }
    }
}

#[derive(Default)]
struct State {
    max_rpc: i64,
    active_rpc: i64,
    max_ranges: i64,
    active_ranges: i64,
    max_playback_ranges: i64,
    active_playback_ranges: i64,
    flood_wait_until: Num,
    cooldown_until: Num,
    bytes_per_second: Num,
    last_error: String,
    total_rpc_requests: i64,
    total_rpc_success: i64,
    total_rpc_floodwaits: i64,
    last_rpc_latency_ms: Num,
    last_rpc_at: Num,
    playback_active: bool,
    playback_streams: i64,
    playback_range_waiting: i64,
    playback_range_reservations: i64,
    playback_range_requests: i64,
    playback_range_deadline_misses: i64,
    last_playback_range_wait_ms: Num,
    total_playback_range_wait_ms: Num,
}

/// Held while a playback range is in flight (`telegram_playback_range_slot`).
pub struct PlaybackRangeLease {
    _private: (),
}

pub struct Governor {
    clock: Clock,
    cfg: GovernorConfig,
    state: State,
    pub logs: Vec<(String, String)>, // (level, message)
    active_rpc_slots: i64,
    active_playback_rpc_slots: i64,
    playback_rpc_max: i64,
    rpc_interval: f64,
    rpc_interval_min: f64,
    rpc_interval_max: f64,
    rpc_latency_samples: Vec<f64>,
    active_range_slots: i64,
    active_playback_range_slots: i64,
    global_range_active: i64,
    global_range_active_small: i64,
    global_range_active_large: i64,
    telegram_small_file_limit: i64,
    telegram_large_file_limit: i64,
    telegram_small_range_limit: i64,
    telegram_large_range_limit: i64,
    telegram_global_range_limit: i64,
    active_file_small: i64,
    active_file_large: i64,
    active_file_by_dc: BTreeMap<(i64, String), i64>,
    lane_pause_until: BTreeMap<&'static str, f64>,
    lane_reduction: BTreeMap<&'static str, i64>,
    lane_reduced_until: BTreeMap<&'static str, f64>,
    lane_floodwaits: BTreeMap<&'static str, i64>,
    channel_floodwaits: BTreeMap<i64, i64>,
    file_reference_refreshes: i64,
    file_reference_refreshes_by_channel: BTreeMap<i64, i64>,
    upload_x: i64,
    upload_y: i64,
    upload_active_parts: i64,
    upload_pause_until: f64,
    upload_last_flood_mono: f64,
    upload_last_ramp_mono: f64,
    upload_floodwait_count: i64,
    upload_last_wait_seconds: f64,
    upload_last_part_success_mono: f64,
    global_playback_waiters: i64,
    playback_range_limit: i64,
    playback_lane_target: i64,
    playback_lane_emergency: i64,
    playback_range_waiting: i64,
    background_paused: bool,
    range_interval: f64,
    range_interval_min: f64,
    range_interval_max: f64,
    range_samples: Vec<f64>,
    range_success_count: i64,
    lane_floor: BTreeMap<&'static str, f64>,
    lane_flood_at: BTreeMap<&'static str, f64>,
    lane_learn_block_until: BTreeMap<&'static str, f64>,
    flood_floor_hold_seconds: f64,
    flood_floor_half_life_seconds: f64,
    recent_floodwaits: Vec<Value>,
    playback_blocks_background: bool,
}

const UPLOAD_LEVELS: [(i64, i64); 6] = [(4, 1), (6, 1), (6, 2), (8, 2), (8, 3), (8, 4)];

fn lane_name(lane: &str) -> &'static str {
    match lane.to_lowercase().as_str() {
        "playback" => "playback",
        "fingerprint" => "fingerprint",
        _ => "range",
    }
}

fn map_of<T: Copy>(keys: [&'static str; 3], v: T) -> BTreeMap<&'static str, T> {
    keys.iter().map(|k| (*k, v)).collect()
}

impl Governor {
    pub fn new(cfg: GovernorConfig, clock: Clock) -> Self {
        let mono = (clock)().1;
        let playback_rpc_max = 1.max(cfg.playback_rpc_concurrency);
        let range_limit = 1.max(cfg.background_range_concurrency);
        let playback_range_limit = 1.max(cfg.playback_range_concurrency);
        let lanes = ["range", "playback", "fingerprint"];
        let state = State { max_rpc: 1, max_ranges: range_limit, max_playback_ranges: 4, ..State::default() };
        Governor {
            playback_rpc_max,
            playback_range_limit,
            playback_lane_target: playback_range_limit,
            playback_lane_emergency: playback_range_limit.max(cfg.playback_emergency_concurrency),
            state,
            clock,
            cfg,
            logs: Vec::new(),
            active_rpc_slots: 0,
            active_playback_rpc_slots: 0,
            rpc_interval: 0.12,
            rpc_interval_min: 0.06,
            rpc_interval_max: 4.0,
            rpc_latency_samples: Vec::new(),
            active_range_slots: 0,
            active_playback_range_slots: 0,
            global_range_active: 0,
            global_range_active_small: 0,
            global_range_active_large: 0,
            telegram_small_file_limit: 3,
            telegram_large_file_limit: 2,
            telegram_small_range_limit: range_limit,
            telegram_large_range_limit: range_limit,
            telegram_global_range_limit: range_limit,
            active_file_small: 0,
            active_file_large: 0,
            active_file_by_dc: BTreeMap::new(),
            lane_pause_until: map_of(lanes, 0.0),
            lane_reduction: map_of(lanes, 0),
            lane_reduced_until: map_of(lanes, 0.0),
            lane_floodwaits: map_of(lanes, 0),
            channel_floodwaits: BTreeMap::new(),
            file_reference_refreshes: 0,
            file_reference_refreshes_by_channel: BTreeMap::new(),
            upload_x: 4,
            upload_y: 1,
            upload_active_parts: 0,
            upload_pause_until: 0.0,
            upload_last_flood_mono: 0.0,
            upload_last_ramp_mono: mono,
            upload_floodwait_count: 0,
            upload_last_wait_seconds: 0.0,
            upload_last_part_success_mono: 0.0,
            global_playback_waiters: 0,
            playback_range_waiting: 0,
            background_paused: false,
            range_interval: 0.08,
            range_interval_min: 0.02,
            range_interval_max: 4.0,
            range_samples: Vec::new(),
            range_success_count: 0,
            lane_floor: [("rpc", 0.0), ("range", 0.0)].into_iter().collect(),
            lane_flood_at: [("rpc", 0.0), ("range", 0.0)].into_iter().collect(),
            lane_learn_block_until: [("rpc", 0.0), ("range", 0.0)].into_iter().collect(),
            flood_floor_hold_seconds: 600.0,
            flood_floor_half_life_seconds: 600.0,
            recent_floodwaits: Vec::new(),
            playback_blocks_background: true,
        }
    }

    fn wall(&self) -> f64 {
        (self.clock)().0
    }
    fn mono(&self) -> f64 {
        (self.clock)().1
    }
    fn log(&mut self, message: String, level: &str) {
        self.logs.push((level.to_string(), message));
    }

    // ---- setup, background pause and playback policy -------------------------------------------------------------------------------

    pub fn set_background_paused(&mut self, paused: bool) {
        self.background_paused = paused;
    }

    pub fn set_playback_policy(&mut self, blocks_background: bool) {
        self.playback_blocks_background = blocks_background;
    }

    pub fn playback_enter(&mut self) {
        self.state.playback_streams += 1;
        self.state.playback_active = self.state.playback_streams > 0;
    }

    pub fn playback_exit(&mut self) {
        self.state.playback_streams = 0.max(self.state.playback_streams - 1);
        self.state.playback_active = self.state.playback_streams > 0;
    }

    /// Would `wait_for_playback_clear` have to wait?
    pub fn playback_clear_must_wait(&self) -> bool {
        self.state.playback_active && self.playback_blocks_background
    }

    pub fn background_paused(&self) -> bool {
        self.background_paused
    }

    // ---- download limits -----------------------------------------------------------------------------------------------------------

    fn restore_download_lanes(&mut self) {
        let now = self.wall();
        for lane in ["range", "playback", "fingerprint"] {
            let reduced = self.lane_reduced_until[lane];
            if reduced != 0.0 && now >= reduced {
                self.lane_reduction.insert(lane, 0);
                self.lane_reduced_until.insert(lane, 0.0);
            }
            let pause = self.lane_pause_until[lane];
            if pause != 0.0 && now >= pause {
                self.lane_pause_until.insert(lane, 0.0);
            }
        }
    }

    pub fn set_telegram_download_limits(&mut self, small: Option<&Value>, large: Option<&Value>) {
        let clamp = |v: Option<&Value>, fallback: i64| -> i64 {
            match v.and_then(|v| py_int(v).ok()) {
                Some(n) => 1.max(64.min(n)),
                None => fallback,
            }
        };
        self.telegram_small_file_limit = clamp(small, self.telegram_small_file_limit);
        self.telegram_large_file_limit = clamp(large, self.telegram_large_file_limit);
        let background = 1.max(16.min(self.cfg.background_range_concurrency));
        self.telegram_small_range_limit = background;
        self.telegram_large_range_limit = background;
        self.telegram_global_range_limit = background;
        self.state.max_ranges = background;
    }

    // ---- file slots and download flood handling ------------------------------------------------------------------------------------

    /// `_acquire_file_operation_slot`. `None`: it would wait.
    pub fn try_file_operation_slot(&mut self, file_size: Option<i64>, dc_id: Option<i64>, allow_during_playback: bool) -> Option<(i64, String)> {
        let size = 0.max(file_size.unwrap_or(0));
        let kind = if size < MIB20 { "small" } else { "large" };
        let dc = 0.max(dc_id.unwrap_or(0));
        if self.background_paused || (!allow_during_playback && self.playback_clear_must_wait()) {
            return None;
        }
        let limit = if kind == "small" { self.telegram_small_file_limit } else { self.telegram_large_file_limit };
        let key = (dc, kind.to_string());
        let active_dc = self.active_file_by_dc.get(&key).copied().unwrap_or(0);
        let playback_active = self.playback_clear_must_wait();
        if active_dc < limit && (allow_during_playback || !playback_active) {
            self.active_file_by_dc.insert(key.clone(), active_dc + 1);
            if kind == "small" {
                self.active_file_small += 1;
            } else {
                self.active_file_large += 1;
            }
            return Some(key);
        }
        None
    }

    pub fn release_file_operation_slot(&mut self, key: &(i64, String)) {
        if key.1 == "small" {
            self.active_file_small = 0.max(self.active_file_small - 1);
        } else {
            self.active_file_large = 0.max(self.active_file_large - 1);
        }
        let active = 0.max(self.active_file_by_dc.get(key).copied().unwrap_or(0) - 1);
        if active != 0 {
            self.active_file_by_dc.insert(key.clone(), active);
        } else {
            self.active_file_by_dc.remove(key);
        }
    }

    pub fn note_download_floodwait(&mut self, seconds: f64, lane: &str, request: Option<&str>, channel_id: i64, message_id: i64) {
        let lane = lane_name(lane);
        let seconds = if seconds == 0.0 { 1.0 } else { seconds }.clamp(1.0, 86400.0);
        let now = self.wall();
        self.restore_download_lanes();
        let until = self.lane_pause_until[lane].max(now + seconds);
        self.lane_pause_until.insert(lane, until);
        let reduced = self.lane_reduced_until[lane].max(now + 300.0);
        self.lane_reduced_until.insert(lane, reduced);
        let max_reduction = match lane {
            "range" => 0.max(self.telegram_global_range_limit - 1),
            "playback" => 0.max(self.playback_range_limit - 1),
            _ => 0,
        };
        let r = max_reduction.min(self.lane_reduction[lane] + 1);
        self.lane_reduction.insert(lane, r);
        *self.lane_floodwaits.get_mut(lane).unwrap() += 1;
        if channel_id != 0 {
            *self.channel_floodwaits.entry(channel_id).or_insert(0) += 1;
        }
        let source = match request.filter(|r| !r.is_empty()) {
            Some(r) => format!("{r}, {lane} lane"),
            None => format!("{lane} lane"),
        };
        let location = if channel_id != 0 { format!(", channel {channel_id} message {message_id}") } else { String::new() };
        self.log(format!("Telegram asked to slow down — resuming in {seconds:.0}s ({source}{location}); only this download lane paused"), "WARN");
    }

    /// Seconds `wait_download_lane_async` would still wait for this lane (0 or less: none).
    pub fn download_lane_wait_remaining(&mut self, lane: &str) -> f64 {
        let lane = lane_name(lane);
        self.restore_download_lanes();
        self.lane_pause_until[lane] - self.wall()
    }

    pub fn note_file_reference_refresh(&mut self, channel_id: i64, message_id: i64) {
        self.file_reference_refreshes += 1;
        if channel_id != 0 {
            *self.file_reference_refreshes_by_channel.entry(channel_id).or_insert(0) += 1;
        }
        self.log(format!("Refreshed expired Telegram file reference for channel {channel_id} message {message_id}"), "INFO");
    }

    pub fn note_channel_scan_floodwait(&mut self, channel_id: i64) {
        if channel_id == 0 {
            return;
        }
        *self.channel_floodwaits.entry(channel_id).or_insert(0) += 1;
    }

    pub fn channel_download_floodwaits(&self, channel_id: i64) -> i64 {
        self.channel_floodwaits.get(&channel_id).copied().unwrap_or(0)
    }

    // ---- global range slots and playback lanes -------------------------------------------------------------------------------------

    /// `_acquire_global_range_slot`: the size class that was taken, or `None` when it would wait.
    pub fn try_global_range_slot(&mut self, file_size: Option<i64>, playback: bool, allow_during_playback: bool) -> Option<&'static str> {
        if playback {
            return Some("playback");
        }
        let size = 0.max(file_size.unwrap_or(0));
        let size_class = if 0 < size && size < MIB20 { "small" } else { "large" };
        self.restore_download_lanes();
        let class_limit = 1.max(self.telegram_global_range_limit - self.lane_reduction["range"]);
        let playback_waiting = self.global_playback_waiters > 0;
        let playback_active = self.playback_clear_must_wait();
        let background_blocked = !playback && !allow_during_playback && (playback_active || playback_waiting);
        let by_class = if size_class == "small" { self.global_range_active_small } else { self.global_range_active_large };
        if self.global_range_active < self.telegram_global_range_limit && by_class < class_limit && (playback || !playback_waiting) && !background_blocked {
            self.global_range_active += 1;
            if size_class == "small" {
                self.global_range_active_small += 1;
            } else {
                self.global_range_active_large += 1;
            }
            return Some(size_class);
        }
        None
    }

    pub fn release_global_range_slot(&mut self, size_class: &str) {
        if size_class == "playback" {
            return;
        }
        self.global_range_active = 0.max(self.global_range_active - 1);
        if size_class == "small" {
            self.global_range_active_small = 0.max(self.global_range_active_small - 1);
        } else {
            self.global_range_active_large = 0.max(self.global_range_active_large - 1);
        }
    }

    pub fn register_global_playback_waiter(&mut self) {
        self.global_playback_waiters += 1;
    }

    pub fn release_global_playback_waiter(&mut self) {
        self.global_playback_waiters = 0.max(self.global_playback_waiters - 1);
    }

    pub fn configure_playback_lanes(&mut self, normal: i64, emergency: Option<i64>) {
        let normal = 1.max(normal);
        let emergency = normal.max(match emergency {
            Some(0) | None => normal,
            Some(e) => e,
        });
        self.playback_lane_target = normal;
        self.playback_lane_emergency = emergency;
        if self.wall() >= self.state.flood_wait_until.get() {
            self.playback_range_limit = normal;
            self.playback_rpc_max = normal;
        }
        self.state.max_playback_ranges = self.playback_range_limit;
    }

    pub fn set_playback_emergency(&mut self, emergency: bool) {
        let flood_active = self.wall() < self.state.flood_wait_until.get();
        let (range_limit, rpc_limit) = if flood_active {
            (2, 2)
        } else if emergency {
            let l = 1.max(self.playback_lane_emergency);
            (l, l)
        } else {
            let l = 1.max(self.playback_lane_target);
            (l, l)
        };
        self.playback_range_limit = range_limit;
        self.playback_rpc_max = rpc_limit;
        self.state.max_playback_ranges = range_limit;
    }

    // ---- lane learning and range outcomes ------------------------------------------------------------------------------------------

    /// Current learned minimum spacing for a background lane (seconds).
    pub fn lane_floor(&self, lane: &str) -> f64 {
        let mut floor = self.lane_floor.get(lane).copied().unwrap_or(0.0);
        if floor <= 0.0 {
            return 0.0;
        }
        let age = self.mono() - self.lane_flood_at.get(lane).copied().unwrap_or(0.0);
        let extra = age - self.flood_floor_hold_seconds;
        if extra > 0.0 {
            floor *= 0.5f64.powf(extra / self.flood_floor_half_life_seconds.max(1.0));
        }
        floor
    }

    fn learn_floor(&mut self, lane: &'static str, interval_before: f64, interval_max: f64, seconds: f64) -> bool {
        let now = self.mono();
        if now < self.lane_learn_block_until[lane] {
            return false;
        }
        self.lane_learn_block_until.insert(lane, now + seconds);
        let current = self.lane_floor(lane);
        self.lane_floor.insert(lane, interval_max.min(current.max(interval_before * 1.5)));
        self.lane_flood_at.insert(lane, now);
        true
    }

    /// `flood_lane_for`.
    pub fn flood_lane_for(request_name: Option<&str>, playback: bool) -> &'static str {
        if playback {
            return "playback";
        }
        match request_name.unwrap_or("") {
            "GetFileRequest" | "GetCdnFileRequest" | "GetFile" => "range",
            "SaveFilePartRequest" | "SaveBigFilePartRequest" => "upload",
            _ => "rpc",
        }
    }

    pub fn note_floodwait(&mut self, seconds: f64, kind: Option<&str>, request: Option<&str>) {
        let kind = kind.filter(|k| !k.is_empty());
        let request = request.filter(|r| !r.is_empty());
        let now = self.wall();
        self.state.total_rpc_floodwaits += 1;
        let until = self.state.flood_wait_until.get().max(now + seconds);
        self.state.flood_wait_until.set(until);
        self.state.max_rpc = 1;
        self.state.max_ranges = 1;
        self.playback_range_limit = 1.max(self.playback_range_limit.min(2));
        self.playback_rpc_max = 1.max(self.playback_rpc_max.min(2));
        self.state.max_playback_ranges = self.playback_range_limit;
        self.state.last_error = format!("FloodWait {seconds:.0}s") + &request.map(|r| format!(" ({r})")).unwrap_or_default();
        self.recent_floodwaits.push(json!({"at": now, "seconds": seconds, "lane": kind.unwrap_or("unknown"), "request": request.unwrap_or("")}));
        if self.recent_floodwaits.len() > 50 {
            let cut = self.recent_floodwaits.len() - 50;
            self.recent_floodwaits.drain(..cut);
        }
        let mut slow_range = kind != Some("rpc");
        let mut slow_rpc = kind != Some("range");
        if kind == Some("range") && !self.learn_floor("range", self.range_interval, self.range_interval_max, seconds) {
            slow_range = false;
        }
        if slow_range {
            self.range_interval = self.range_interval_max.min((self.range_interval * 2.0).max(0.50));
        }
        if kind == Some("rpc") && !self.learn_floor("rpc", self.rpc_interval, self.rpc_interval_max, seconds) {
            slow_rpc = false;
        }
        if slow_rpc {
            self.rpc_interval = self.rpc_interval_max.min((self.rpc_interval * 2.0).max(0.50));
        }
        let text = match (request, kind) {
            (Some(r), Some(k)) => format!(" ({r}, {k})"),
            (Some(r), None) => format!(" ({r})"),
            (None, Some(k)) => format!(" ({k})"),
            (None, None) => String::new(),
        };
        self.log(format!("Telegram FloodWait: {seconds:.0}s{text}"), "WARN");
    }

    pub fn note_network_error(&mut self, message: &str) {
        let now = self.wall();
        let until = self.state.cooldown_until.get().max(now + 5.0);
        self.state.cooldown_until.set(until);
        self.state.max_rpc = 1;
        self.state.max_ranges = 1;
        self.playback_range_limit = 1.max(self.playback_range_limit.min(2));
        self.playback_rpc_max = 1.max(self.playback_rpc_max.min(2));
        self.state.max_playback_ranges = self.playback_range_limit;
        self.state.last_error = message.chars().take(300).collect();
        self.range_interval = self.range_interval_max.min((self.range_interval * 1.5).max(0.15));
        self.rpc_interval = self.rpc_interval_max.min((self.rpc_interval * 1.5).max(0.20));
        self.log(format!("Network cooldown: {message}"), "WARN");
    }

    pub fn note_range_success(&mut self, amount: i64, elapsed: f64) {
        if amount <= 0 || elapsed <= 0.0 {
            return;
        }
        let bps = amount as f64 / elapsed;
        let b = self.state.bytes_per_second.get();
        self.state.bytes_per_second.set(if b != 0.0 { 0.75 * b + 0.25 * bps } else { bps });
        let flood_active = self.wall() < self.state.flood_wait_until.get();
        self.range_samples.push(bps);
        if self.range_samples.len() > 12 {
            let cut = self.range_samples.len() - 12;
            self.range_samples.drain(..cut);
        }
        self.range_success_count += 1;
        if !flood_active && self.range_samples.len() >= 6 && self.range_success_count % 6 == 0 {
            let recent = self.range_samples[self.range_samples.len() - 6..].iter().sum::<f64>() / 6.0;
            let current = self.state.bytes_per_second.get();
            if current != 0.0 && recent >= current * 0.80 {
                self.range_interval = self.range_interval_min.max(self.lane_floor("range")).max(self.range_interval * 0.90);
            }
        }
        if self.wall() >= self.state.flood_wait_until.get() && self.state.max_ranges == 1 {
            // recover the background range slot gradually (Python sets 2, not the configured limit)
            self.state.max_ranges = 2;
        }
        if self.wall() >= self.state.flood_wait_until.get() {
            let target = 1.max(self.playback_lane_target);
            if self.playback_range_limit < target {
                self.playback_range_limit += 1;
            }
            if self.playback_rpc_max < target {
                self.playback_rpc_max += 1;
            }
            self.state.max_playback_ranges = self.playback_range_limit;
        }
    }

    pub fn note_range_failure(&mut self) {
        self.range_interval = self.range_interval_max.min((self.range_interval * 1.20).max(0.10));
    }

    // ---- RPC permits ---------------------------------------------------------------------------------------------------------------

    /// `_acquire_rpc_permit`.
    pub fn try_rpc_permit(&mut self) -> bool {
        let limit = 1.max(self.state.max_rpc);
        if self.active_rpc_slots < limit {
            self.active_rpc_slots += 1;
            self.state.active_rpc = self.active_rpc_slots;
            return true;
        }
        false
    }

    pub fn release_rpc_permit(&mut self) {
        self.active_rpc_slots = 0.max(self.active_rpc_slots - 1);
        self.state.active_rpc = self.active_rpc_slots;
    }

    pub fn try_playback_rpc_permit(&mut self) -> bool {
        if self.active_playback_rpc_slots < self.playback_rpc_max {
            self.active_playback_rpc_slots += 1;
            return true;
        }
        false
    }

    pub fn release_playback_rpc_permit(&mut self) {
        self.active_playback_rpc_slots = 0.max(self.active_playback_rpc_slots - 1);
    }

    /// Seconds a request must wait for a Telegram-wide FloodWait or network cooldown (0 or less: none).
    pub fn cooldown_remaining(&self) -> f64 {
        self.state.flood_wait_until.get().max(self.state.cooldown_until.get()) - self.wall()
    }

    pub fn note_rpc_success(&mut self, elapsed: f64) {
        let latency_ms = (elapsed * 1000.0).max(0.0);
        self.state.total_rpc_success += 1;
        self.state.last_rpc_latency_ms.set(round_to(latency_ms, 1));
        let flood_active = self.wall() < self.state.flood_wait_until.get();
        if !flood_active {
            self.rpc_latency_samples.push(latency_ms);
            if self.rpc_latency_samples.len() > 20 {
                let cut = self.rpc_latency_samples.len() - 20;
                self.rpc_latency_samples.drain(..cut);
            }
            let n = self.rpc_latency_samples.len();
            if n >= 8 && n.is_multiple_of(4) {
                self.rpc_interval = self.rpc_interval_min.max(self.lane_floor("rpc")).max(self.rpc_interval * 0.95);
            }
        }
    }

    // ---- range permits -------------------------------------------------------------------------------------------------------------

    /// `_acquire_range_permit`.
    pub fn try_range_permit(&mut self, playback: bool) -> bool {
        if !playback && (self.background_paused || self.playback_range_waiting > 0) {
            return false;
        }
        let mut limit = 1.max(self.state.max_ranges);
        if !playback {
            limit = 1.max(limit - 1);
        }
        if self.active_range_slots < limit {
            self.active_range_slots += 1;
            self.state.active_ranges = self.active_range_slots;
            return true;
        }
        false
    }

    pub fn release_range_permit(&mut self) {
        self.active_range_slots = 0.max(self.active_range_slots - 1);
        self.state.active_ranges = self.active_range_slots;
    }

    fn try_playback_range_permit(&mut self) -> bool {
        self.restore_download_lanes();
        let reduction = self.lane_reduction["playback"];
        let limit = 1.max(self.playback_range_limit - reduction);
        if self.active_playback_range_slots < limit {
            self.active_playback_range_slots += 1;
            self.state.active_playback_ranges = self.active_playback_range_slots;
            return true;
        }
        false
    }

    fn release_playback_range_permit(&mut self) {
        self.active_playback_range_slots = 0.max(self.active_playback_range_slots - 1);
        self.state.active_playback_ranges = self.active_playback_range_slots;
    }

    /// `telegram_playback_range_slot`, entering: `None` when it would have to wait (and Python's `finally` has then undone the counters).
    pub fn playback_range_enter(&mut self, deadline_seconds: Option<f64>) -> Option<PlaybackRangeLease> {
        let deadline = self.cfg.playback_deadline_seconds;
        let started = self.mono();
        self.state.playback_range_requests += 1;
        self.playback_range_waiting += 1;
        self.state.playback_range_waiting = self.playback_range_waiting;
        self.state.playback_range_reservations += 1;
        self.register_global_playback_waiter();
        let blocked = self.cooldown_remaining() > 0.0 || !self.try_playback_range_permit();
        if blocked {
            // the `finally` of the context manager
            self.release_global_playback_waiter();
            if self.playback_range_waiting > 0 {
                self.playback_range_waiting = 0.max(self.playback_range_waiting - 1);
            }
            self.state.playback_range_waiting = self.playback_range_waiting;
            self.state.playback_range_reservations = 0.max(self.state.playback_range_reservations - 1);
            return None;
        }
        // the global slot of a playback request is "playback": taken at once
        self.release_global_playback_waiter();
        let wait_ms = ((self.mono() - started) * 1000.0).max(0.0);
        self.state.last_playback_range_wait_ms.set(round_to(wait_ms, 1));
        let total = self.state.total_playback_range_wait_ms.get() + wait_ms;
        self.state.total_playback_range_wait_ms.set(total);
        if wait_ms > deadline_seconds.map_or(deadline, |d| d.max(0.0)) * 1000.0 {
            self.state.playback_range_deadline_misses += 1;
        }
        self.playback_range_waiting = 0.max(self.playback_range_waiting - 1);
        self.state.playback_range_waiting = self.playback_range_waiting;
        self.state.playback_range_reservations = 0.max(self.state.playback_range_reservations - 1);
        Some(PlaybackRangeLease { _private: () })
    }

    /// Leaving the context manager of an admitted playback range.
    pub fn playback_range_exit(&mut self, _lease: PlaybackRangeLease) {
        if self.playback_range_waiting > 0 {
            self.playback_range_waiting = 0.max(self.playback_range_waiting - 1);
        }
        self.state.playback_range_waiting = self.playback_range_waiting;
        self.release_playback_range_permit();
    }

    // ---- upload permits and ramping ------------------------------------------------------------------------------------------------

    fn maybe_ramp_upload(&mut self) {
        let now = self.mono();
        if self.wall() < self.upload_pause_until {
            return;
        }
        if self.upload_active_parts <= 0 {
            return;
        }
        if self.upload_last_flood_mono != 0.0 && now - self.upload_last_flood_mono < 30.0 {
            return;
        }
        if now - self.upload_last_ramp_mono < 30.0 {
            return;
        }
        let current = (self.upload_x, self.upload_y);
        if current == (8, 1) {
            self.upload_x = 8;
            self.upload_y = 2;
            self.upload_last_ramp_mono = now;
            return;
        }
        // Python: an unknown pair has index -1 and so moves to the first level
        let index: i64 = UPLOAD_LEVELS.iter().position(|l| *l == current).map_or(-1, |i| i as i64);
        if ((index + 1) as usize) < UPLOAD_LEVELS.len() {
            let next = UPLOAD_LEVELS[(index + 1) as usize];
            self.upload_x = next.0;
            self.upload_y = next.1;
            self.upload_last_ramp_mono = now;
        }
    }

    fn effective_upload_xy(&mut self) -> (i64, i64) {
        self.maybe_ramp_upload();
        if self.state.playback_active {
            return (2, 1);
        }
        (2.max(self.upload_x), 1.max(self.upload_y))
    }

    /// `_acquire_upload_permit`: `None` when it would wait, else true.
    pub fn try_upload_permit(&mut self) -> bool {
        self.maybe_ramp_upload();
        let remaining = self.upload_pause_until - self.wall();
        if remaining > 0.0 {
            return false;
        }
        let (x, y) = self.effective_upload_xy();
        if self.upload_active_parts < x * y {
            if self.upload_active_parts == 0 && self.upload_last_part_success_mono <= 0.0 {
                self.upload_last_ramp_mono = self.mono();
            }
            self.upload_active_parts += 1;
            return true;
        }
        false
    }

    pub fn release_upload_permit(&mut self) {
        self.upload_active_parts = 0.max(self.upload_active_parts - 1);
    }

    pub fn note_upload_connection_failure(&mut self, available_queues: i64, error: &str) {
        let now = self.mono();
        let available = 1.max(4.min(if available_queues == 0 { 1 } else { available_queues }));
        self.upload_y = self.upload_y.min(available);
        self.upload_last_flood_mono = now;
        self.upload_last_ramp_mono = now;
        let tail = if error.is_empty() { String::new() } else { format!(": {}", error.chars().take(300).collect::<String>()) };
        self.log(format!("Telegram parallel upload connection unavailable; using {available} queue(s){tail}"), "WARN");
    }

    pub fn note_upload_part_success(&mut self) {
        self.upload_last_part_success_mono = self.mono();
    }

    pub fn note_upload_floodwait(&mut self, seconds: f64, request: Option<&str>) {
        let seconds = if seconds == 0.0 { 1.0 } else { seconds }.max(1.0);
        let (now_wall, now_mono) = (self.wall(), self.mono());
        let already_paused = self.upload_pause_until > now_wall;
        self.upload_pause_until = self.upload_pause_until.max(now_wall + seconds);
        self.upload_floodwait_count += 1;
        self.upload_last_wait_seconds = seconds;
        self.upload_last_flood_mono = now_mono;
        self.upload_last_ramp_mono = now_mono;
        if !already_paused {
            if self.upload_y > 1 {
                self.upload_y -= 1;
            } else if self.upload_x > 6 {
                self.upload_x = 6;
            } else if self.upload_x > 4 {
                self.upload_x = 4;
            } else if self.upload_x > 2 {
                self.upload_x = 2;
            }
        }
        let tail = request.filter(|r| !r.is_empty()).map(|r| format!(" ({r})")).unwrap_or_default();
        self.log(format!("Telegram upload FloodWait: {seconds:.0}s{tail}; upload lane paused; API and playback lanes unchanged"), "WARN");
    }

    // ---- status --------------------------------------------------------------------------------------------------------------------

    pub fn snapshot(&mut self) -> Value {
        let wall = self.wall();
        let s = &self.state;
        let mut d = Map::new();
        let mut put = |k: &str, v: Value| {
            d.insert(k.to_string(), v);
        };
        put("max_rpc", json!(s.max_rpc));
        put("active_rpc", json!(s.active_rpc));
        put("max_ranges", json!(s.max_ranges));
        put("active_ranges", json!(s.active_ranges));
        put("max_playback_ranges", json!(s.max_playback_ranges));
        put("active_playback_ranges", json!(s.active_playback_ranges));
        put("max_thumbnail_workers", json!(1));
        put("flood_wait_until", s.flood_wait_until.json());
        put("cooldown_until", s.cooldown_until.json());
        put("bytes_per_second", s.bytes_per_second.json());
        put("last_error", json!(s.last_error));
        put("total_rpc_requests", json!(s.total_rpc_requests));
        put("total_rpc_success", json!(s.total_rpc_success));
        put("total_rpc_floodwaits", json!(s.total_rpc_floodwaits));
        put("last_rpc_latency_ms", s.last_rpc_latency_ms.json());
        put("last_rpc_at", s.last_rpc_at.json());
        put("playback_active", json!(s.playback_active));
        put("playback_streams", json!(s.playback_streams));
        put("playback_range_waiting", json!(s.playback_range_waiting));
        put("playback_range_reservations", json!(s.playback_range_reservations));
        put("playback_range_requests", json!(s.playback_range_requests));
        put("playback_range_deadline_misses", json!(s.playback_range_deadline_misses));
        put("last_playback_range_wait_ms", s.last_playback_range_wait_ms.json());
        put("total_playback_range_wait_ms", s.total_playback_range_wait_ms.json());
        put("range_interval_seconds", json!(round_to(self.range_interval, 3)));
        put(
            "range_throughput_bps",
            match s.bytes_per_second.0 {
                Some(v) => json!(round_to(v, 1)),
                None => json!(0),
            },
        );
        put("flood_wait_remaining", max_int0(s.flood_wait_until.get() - wall));
        put("cooldown_remaining", max_int0(s.cooldown_until.get() - wall));
        let playback_active = s.playback_active;
        put(
            "traffic_mode",
            json!(if playback_active && self.playback_blocks_background {
                "PLAYBACK_PRIORITY"
            } else if playback_active {
                "BALANCED"
            } else {
                "NORMAL"
            }),
        );
        put("playback_gate", json!(self.playback_blocks_background));
        put("playback_range_reserved", json!(s.playback_range_reservations > 0));
        put("playback_range_waiting", json!(s.playback_range_waiting));
        put("playback_range_deadline_seconds", json!(self.cfg.playback_deadline_seconds));
        put("rpc_interval_seconds", json!(round_to(self.rpc_interval, 3)));
        let (rf, gf) = (round_to(self.lane_floor("rpc"), 3), round_to(self.lane_floor("range"), 3));
        put("rpc_floor_seconds", json!(rf));
        put("range_floor_seconds", json!(gf));
        let n = self.recent_floodwaits.len();
        put("recent_floodwaits", Value::Array(self.recent_floodwaits[n.saturating_sub(10)..].to_vec()));
        put("rpc_active", json!(self.active_rpc_slots));
        put("playback_rpc_active", json!(self.active_playback_rpc_slots));
        put("transfer_active", json!(0));
        put("transfer_capacity", json!(3));
        put("playback_rpc_max", json!(self.playback_rpc_max));
        put("playback_range_limit", json!(self.playback_range_limit));
        put("playback_range_active", json!(self.active_playback_range_slots));
        put("telegram_range_active_global", json!(self.global_range_active));
        put("telegram_range_limit_global", json!(self.telegram_global_range_limit));
        put("telegram_range_active_small", json!(self.global_range_active_small));
        put("telegram_range_active_large", json!(self.global_range_active_large));
        put("telegram_range_limit_small", json!(self.telegram_small_range_limit));
        put("telegram_range_limit_large", json!(self.telegram_large_range_limit));
        put("telegram_file_limit_small", json!(self.telegram_small_file_limit));
        put("telegram_file_limit_large", json!(self.telegram_large_file_limit));
        put("telegram_file_active_small", json!(self.active_file_small));
        put("telegram_file_active_large", json!(self.active_file_large));
        let by_dc: Map<String, Value> = self.active_file_by_dc.iter().filter(|(_, v)| **v != 0).map(|((dc, kind), v)| (format!("{dc}:{kind}"), json!(v))).collect();
        put("telegram_file_active_by_dc", Value::Object(by_dc));
        // `_restore_download_lanes_locked` runs here in Python, before the values below are read
        self.restore_download_lanes();
        let max_pause = self.lane_pause_until.values().cloned().fold(f64::MIN, f64::max);
        let mut out = d;
        out.insert("download_flood_wait_remaining".into(), json!(max_float0(round_to(max_pause - wall, 1))));
        out.insert(
            "download_flood_wait_message".into(),
            json!(if max_pause > wall { format!("Telegram asked to slow down — resuming in {}s", ((max_pause - wall).max(0.0) + 0.999) as i64) } else { String::new() }),
        );
        out.insert("download_range_floodwaits".into(), json!(self.lane_floodwaits["range"]));
        out.insert("download_playback_floodwaits".into(), json!(self.lane_floodwaits["playback"]));
        out.insert("download_range_concurrency_reduction".into(), json!(self.lane_reduction["range"]));
        out.insert("download_playback_concurrency_reduction".into(), json!(self.lane_reduction["playback"]));
        out.insert("download_fingerprint_floodwaits".into(), json!(self.lane_floodwaits["fingerprint"]));
        out.insert("download_fingerprint_flood_wait_remaining".into(), json!(max_float0(round_to(self.lane_pause_until["fingerprint"] - wall, 1))));
        out.insert("file_reference_refreshes".into(), json!(self.file_reference_refreshes));
        out.insert("download_floodwaits_by_channel".into(), Value::Object(self.channel_floodwaits.iter().map(|(k, v)| (k.to_string(), json!(v))).collect()));
        out.insert(
            "file_reference_refreshes_by_channel".into(),
            Value::Object(self.file_reference_refreshes_by_channel.iter().map(|(k, v)| (k.to_string(), json!(v))).collect()),
        );
        out.insert("telegram_playback_waiters_global".into(), json!(self.global_playback_waiters));
        out.insert("rpc_queueing".into(), json!(0.max(self.state.active_rpc - self.active_rpc_slots)));
        self.maybe_ramp_upload();
        let (ux, uy) = self.effective_upload_xy();
        out.insert("upload_x".into(), json!(ux));
        out.insert("upload_y".into(), json!(uy));
        out.insert("upload_configured_x".into(), json!(self.upload_x));
        out.insert("upload_configured_y".into(), json!(self.upload_y));
        out.insert("upload_active_parts".into(), json!(self.upload_active_parts));
        out.insert("upload_max_parts".into(), json!(ux * uy));
        out.insert("upload_floodwaits".into(), json!(self.upload_floodwait_count));
        out.insert("upload_last_wait_seconds".into(), json!(self.upload_last_wait_seconds));
        out.insert("upload_flood_wait_remaining".into(), max_int0(round_to(self.upload_pause_until - wall, 1)));
        out.insert("upload_lane_mode".into(), json!(if self.state.playback_active { "PLAYBACK_LIMITED" } else { "ADAPTIVE" }));
        Value::Object(out)
    }
}
