//! The Rust resource policy must decide like Python's `InteractiveResourceManager`. The cases are recorded by
//! `tools/parity/gen_resource_policy_vectors.py`: the same operations, with the full snapshot after each one.

use ax_core::resource_policy::ResourcePolicy;
use serde_json::{json, Map, Value};
use std::sync::{Arc, Mutex};

fn cases() -> Value {
    serde_json::from_str(include_str!("resource_policy_cases.json")).unwrap()
}

const WALL0: f64 = 1_930_713_967.0;
const MONO0: f64 = 5_000.0;

#[test]
fn every_recorded_operation_gives_the_recorded_answer_and_state() {
    let cases = cases();
    let mut steps_total = 0;
    for (name, specs) in cases.as_object().unwrap() {
        for (n, spec) in specs.as_array().unwrap().iter().enumerate() {
            let time = Arc::new(Mutex::new((WALL0, MONO0)));
            let t = time.clone();
            let mut rm = ResourcePolicy::new(spec["hardware"].clone(), spec["cpu"].as_i64().unwrap(), Arc::new(move || *t.lock().unwrap()));
            // requests left waiting by `park`: their categories
            let mut parked: Vec<String> = Vec::new();
            for (i, step) in spec["steps"].as_array().unwrap().iter().enumerate() {
                steps_total += 1;
                let at = format!("{name}[{n}] step {i} {}", step["op"]);
                let category = step.get("category").and_then(Value::as_str).unwrap_or("");
                let mut result = Value::Null;
                match step["op"].as_str().unwrap() {
                    "advance" => {
                        let mut g = time.lock().unwrap();
                        g.0 += step["wall"].as_f64().unwrap();
                        g.1 += step["mono"].as_f64().unwrap();
                    }
                    "configure" => {
                        let limits: Option<&Map<String, Value>> = step.get("limits").and_then(Value::as_object);
                        let expires = step.get("expires_seconds");
                        let r = rm.configure(
                            step.get("mode").and_then(Value::as_str),
                            limits,
                            step.get("playback_gate").and_then(Value::as_bool),
                            expires,
                            step.get("label").and_then(Value::as_str).unwrap_or(""),
                        );
                        if let Err(e) = r {
                            result = json!({"error": e.to_string()});
                        }
                    }
                    "pause" => rm.pause_background(step["reason"].as_str().unwrap()),
                    "resume" => rm.resume_background(),
                    "p_enter" => {
                        rm.playback_enter();
                    }
                    "p_exit" => {
                        rm.playback_exit();
                    }
                    "i_enter" => {
                        rm.interactive_playback_enter();
                    }
                    "i_heartbeat" => rm.interactive_playback_heartbeat(),
                    "i_exit" => {
                        rm.interactive_playback_exit();
                    }
                    "reconcile" => result = json!(rm.reconcile_playback(step["expected"].as_i64().unwrap())),
                    "note" => rm.note_playback_activity(),
                    "checkpoint" => result = json!(if rm.checkpoint_must_wait(category) { "waits" } else { "passes" }),
                    "service" => {
                        if rm.service_must_wait(step["block_playback"].as_bool().unwrap()) {
                            result = json!("waits");
                        } else {
                            rm.acquire_service();
                            result = json!("granted");
                        }
                    }
                    "release_service" => rm.release_service(),
                    "take" => {
                        // Python counts the request as waiting while it asks, whether or not it is admitted
                        rm.begin_waiting(category);
                        let got = rm.try_take_background(category);
                        rm.end_waiting(category);
                        result = json!(if got { "granted" } else { "waits" });
                    }
                    "release" => rm.release_background(category),
                    "park" => {
                        // a parked request waits (the scenarios only park requests that cannot be admitted)
                        rm.begin_waiting(category);
                        parked.push(category.to_string());
                    }
                    "unpark" => {
                        for c in parked.drain(..) {
                            rm.end_waiting(&c);
                        }
                    }
                    "snapshot" => {}
                    other => panic!("{other}"),
                }
                assert_eq!(result, step["result"], "{at}: result");
                assert_eq!(rm.snapshot(), step["snapshot"], "{at}: snapshot");
            }
        }
    }
    assert!(steps_total > 150, "{steps_total} steps");
}
