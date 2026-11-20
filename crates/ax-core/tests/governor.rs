//! The Rust governor policy must decide like Python's `TransferGovernor`. The cases are recorded by `tools/parity/gen_governor_vectors.py`:
//! the same operations, the log lines each one wrote and the full snapshot after each.

use ax_core::governor_policy::{Governor, GovernorConfig, PlaybackRangeLease};
use serde_json::{json, Value};
use std::sync::{Arc, Mutex};

const WALL0: f64 = 1_930_713_967.0;
const MONO0: f64 = 5_000.0;

fn cases() -> Value {
    serde_json::from_str(include_str!("governor_cases.json")).unwrap()
}

fn opt_i64(v: &Value) -> Option<i64> {
    v.as_i64()
}

fn granted(ok: bool) -> Value {
    json!(if ok { "granted" } else { "waits" })
}

#[test]
fn the_config_is_the_one_python_ran_with() {
    let c = cases();
    let cfg = &c["config"];
    let d = GovernorConfig::default();
    assert_eq!(cfg["TELEGRAM_BACKGROUND_RANGE_CONCURRENCY"], d.background_range_concurrency);
    assert_eq!(cfg["PLAYBACK_FOREGROUND_RANGE_CONCURRENCY"], d.playback_range_concurrency);
    assert_eq!(cfg["PLAYBACK_FOREGROUND_RPC_CONCURRENCY"], d.playback_rpc_concurrency);
    assert_eq!(cfg["PLAYBACK_FOREGROUND_EMERGENCY_CONCURRENCY"], d.playback_emergency_concurrency);
    assert_eq!(cfg["PLAYBACK_FOREGROUND_RPC_INTERVAL_SECONDS"].as_f64().unwrap(), d.playback_rpc_interval);
    assert_eq!(cfg["TRANSFER_GOVERNOR_PLAYBACK_DEADLINE_SECONDS"].as_f64().unwrap(), d.playback_deadline_seconds);
}

#[test]
fn the_flood_lane_of_a_request_matches_python() {
    for case in cases()["flood_lane_for"].as_array().unwrap() {
        let request = case["request"].as_str();
        let lane = Governor::flood_lane_for(request, case["playback"].as_bool().unwrap());
        assert_eq!(lane, case["lane"].as_str().unwrap(), "{case}");
    }
}

#[test]
fn every_recorded_operation_gives_the_recorded_answer_logs_and_state() {
    let cases = cases();
    let mut total = 0;
    for (name, steps) in cases["scenarios"].as_object().unwrap() {
        let time = Arc::new(Mutex::new((WALL0, MONO0)));
        let t = time.clone();
        let mut g = Governor::new(GovernorConfig::default(), Arc::new(move || *t.lock().unwrap()));
        let mut held: Vec<PlaybackRangeLease> = Vec::new();
        for (i, step) in steps.as_array().unwrap().iter().enumerate() {
            total += 1;
            let at = format!("{name} step {i} {}", step["op"]);
            let mut result = Value::Null;
            let f = |k: &str| step[k].as_f64().unwrap();
            let s = |k: &str| step[k].as_str();
            match step["op"].as_str().unwrap() {
                "advance" => {
                    let mut x = time.lock().unwrap();
                    x.0 += f("wall");
                    x.1 += f("mono");
                }
                "pause" => g.set_background_paused(step["paused"].as_bool().unwrap()),
                "policy" => g.set_playback_policy(step["blocks"].as_bool().unwrap()),
                "p_enter" => g.playback_enter(),
                "p_exit" => g.playback_exit(),
                "clear_wait" => result = json!(if g.playback_clear_must_wait() { "waits" } else { "passes" }),
                "limits" => g.set_telegram_download_limits(Some(&step["small"]), Some(&step["large"])),
                "file_take" => {
                    result = match g.try_file_operation_slot(opt_i64(&step["size"]), opt_i64(&step["dc"]), step["allow"].as_bool().unwrap()) {
                        Some((dc, kind)) => json!(["granted", [dc, kind]]),
                        None => json!("waits"),
                    }
                }
                "file_release" => g.release_file_operation_slot(&(step["dc"].as_i64().unwrap(), s("kind").unwrap().to_string())),
                "dl_flood" => g.note_download_floodwait(f("seconds"), s("lane").unwrap_or(""), s("request"), step["channel"].as_i64().unwrap(), step["message"].as_i64().unwrap()),
                "lane_wait" => result = json!(if g.download_lane_wait_remaining(s("lane").unwrap()) > 0.0 { "waits" } else { "passes" }),
                "scan_flood" => g.note_channel_scan_floodwait(step["channel"].as_i64().unwrap()),
                "channel_count" => result = json!(g.channel_download_floodwaits(step["channel"].as_i64().unwrap())),
                "ref_refresh" => g.note_file_reference_refresh(step["channel"].as_i64().unwrap(), step["message"].as_i64().unwrap()),
                "global_take" => {
                    result = match g.try_global_range_slot(opt_i64(&step["size"]), step["playback"].as_bool().unwrap(), step["allow"].as_bool().unwrap()) {
                        Some(c) => json!(["granted", c]),
                        None => json!("waits"),
                    }
                }
                "global_release" => g.release_global_range_slot(s("cls").unwrap()),
                "gw_register" => g.register_global_playback_waiter(),
                "gw_release" => g.release_global_playback_waiter(),
                "lanes" => g.configure_playback_lanes(step["normal"].as_i64().unwrap(), opt_i64(&step["emergency"])),
                "emergency" => g.set_playback_emergency(step["on"].as_bool().unwrap()),
                "floodwait" => g.note_floodwait(f("seconds"), s("kind"), s("request")),
                "neterr" => g.note_network_error(s("message").unwrap()),
                "range_ok" => g.note_range_success(step["amount"].as_i64().unwrap(), f("elapsed")),
                "range_fail" => g.note_range_failure(),
                "rpc_ok" => g.note_rpc_success(f("elapsed")),
                "rpc_permit" => result = granted(g.try_rpc_permit()),
                "rpc_release" => g.release_rpc_permit(),
                "prpc_take" => result = granted(g.try_playback_rpc_permit()),
                "prpc_release" => g.release_playback_rpc_permit(),
                "range_permit" => result = granted(g.try_range_permit(step["playback"].as_bool().unwrap())),
                "range_release" => g.release_range_permit(),
                "pr_enter" => match g.playback_range_enter(step["deadline"].as_f64()) {
                    Some(lease) => {
                        held.push(lease);
                        result = json!("granted");
                    }
                    None => result = json!("waits"),
                },
                "pr_exit" => match held.pop() {
                    Some(lease) => {
                        g.playback_range_exit(lease);
                        result = json!("exited");
                    }
                    None => result = json!("none held"),
                },
                "up_take" => result = granted(g.try_upload_permit()),
                "up_release" => g.release_upload_permit(),
                "up_conn" => g.note_upload_connection_failure(step["queues"].as_i64().unwrap(), s("error").unwrap()),
                "up_part" => g.note_upload_part_success(),
                "up_flood" => g.note_upload_floodwait(f("seconds"), s("request")),
                "snapshot" => {}
                other => panic!("{other}"),
            }
            assert_eq!(result, step["result"], "{at}: result");
            let logs: Vec<Value> = g.logs.drain(..).map(|(l, m)| json!([l, m])).collect();
            assert_eq!(Value::Array(logs), step["logs"], "{at}: logs");
            assert_eq!(g.snapshot(), step["snapshot"], "{at}: snapshot");
        }
    }
    assert!(total > 400, "{total} steps");
}
