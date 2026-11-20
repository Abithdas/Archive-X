//! The pure decisions of the Rust job manager must equal Python's `jobs/manager.py`. Recorded by `tools/parity/gen_manager_policy_vectors.py`.

use ax_jobs::manager_policy as mp;
use serde_json::{json, Map, Value};

fn cases() -> Value {
    serde_json::from_str(include_str!("manager_policy_cases.json")).unwrap()
}

fn job(v: &Value) -> Map<String, Value> {
    v.as_object().cloned().unwrap_or_default()
}

/// The recorded answer (`{"ok": ..}` or `{"error": ..}`) against ours.
fn check<T: Into<Value>>(got: Result<T, mp::PyError>, want: &Value, what: &str) {
    let got = match got {
        Ok(v) => json!({"ok": v.into()}),
        Err(e) => json!({"error": e.0}),
    };
    assert_eq!(&got, want, "{what}");
}

fn strs(v: Vec<String>) -> Value {
    json!(v)
}

#[test]
fn categories_lanes_manual_flag_and_completion_of_every_job_shape() {
    let cases = cases();
    let rows = cases["jobs"].as_array().unwrap();
    assert!(rows.len() > 700, "{} jobs", rows.len());
    for row in rows {
        let j = job(&row["job"]);
        let what = row["job"].to_string();
        check(mp::job_resource_category(&j), &row["category"], &format!("category {what}"));
        check(mp::execution_keys(&j).map(strs), &row["keys"], &format!("keys {what}"));
        check(mp::execution_lane_keys(&j).map(strs), &row["lane_keys"], &format!("lane keys {what}"));
        let job_type = j.get("job_type").and_then(Value::as_str).unwrap_or("");
        check(mp::unified_operations(job_type, j.get("detail")), &row["operations"], &format!("operations {what}"));
        check(mp::job_is_manual(&j), &row["manual"], &format!("manual {what}"));
        check(mp::pipeline_completion_status(Some(&j)).map(|(s, o)| json!([s, o])), &row["completion"], &format!("completion {what}"));
    }
}

#[test]
fn lane_conflicts_match() {
    let cases = cases();
    let rows = cases["conflicts"].as_array().unwrap();
    assert!(rows.len() > 50);
    for row in rows {
        check(mp::execution_lanes_conflict(&job(&row["a"]), &job(&row["b"])), &row["result"], &format!("{} / {}", row["a"], row["b"]));
    }
}

#[test]
fn lane_capacity_under_drain_and_pressure_matches() {
    let cases = cases();
    for (lane, base) in cases["capacities"].as_object().unwrap() {
        assert_eq!(mp::EXECUTION_LANE_CAPACITIES.iter().find(|(l, _)| l == lane).map(|(_, c)| *c), base.as_i64(), "{lane}");
    }
    assert_eq!(mp::EXECUTION_LANE_CAPACITIES.len(), cases["capacities"].as_object().unwrap().len());
    for row in cases["capacity"].as_array().unwrap() {
        let got = mp::execution_lane_capacity(row["lane"].as_str().unwrap(), row["drain"].as_bool().unwrap(), row["pressure"].as_str());
        assert_eq!(json!({"ok": got}), row["result"], "{row}");
    }
}

#[test]
fn coalesced_metadata_jobs_merge_like_python() {
    let cases = cases();
    for row in cases["merge"].as_array().unwrap() {
        let others: Vec<Map<String, Value>> = row["others"].as_array().unwrap().iter().map(job).collect();
        check(mp::merge_metadata_detail(&job(&row["keep"]), &others), &row["result"], &format!("{row}"));
    }
}

#[test]
fn pipeline_completion_matches() {
    let cases = cases();
    for row in cases["completion"].as_array().unwrap() {
        let j = row["job"].as_object().cloned();
        check(mp::pipeline_completion_status(j.as_ref()).map(|(s, o)| json!([s, o])), &row["result"], &format!("{row}"));
    }
}
