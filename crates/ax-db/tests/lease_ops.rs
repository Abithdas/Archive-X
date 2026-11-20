//! The execution, admission, lane-slot and resource-lease writes of `ax_db::leases` must do what Python's `Database` does, on the same fake
//! clock. Recorded from the Python original by `tools/parity/gen_lease_op_vectors.py`; times are compared exactly.

mod common;

use std::collections::{BTreeMap, HashMap};

use ax_db::jobs::{self, NewJob};
use ax_db::leases::{self, Admission, LaneRequest, NewResourceLease, Now, RetryPolicy};
use ax_db::rusqlite::Connection;
use common::{create_table, rows, schema};
use serde_json::{Map, Value};

#[derive(Default)]
struct Names {
    /// random job id -> scenario name
    jobs: HashMap<String, String>,
    /// execution and lease ids -> `<exec:x>` / `<lease:x>`
    others: HashMap<String, String>,
}

fn is_hex32(s: &str) -> bool {
    s.len() == 32 && s.bytes().all(|b| b.is_ascii_digit() || (b'a'..=b'f').contains(&b))
}

impl Names {
    fn text(&self, s: &str) -> String {
        let mut out = s.to_string();
        for (k, v) in &self.jobs {
            out = out.replace(k, &format!("<job:{v}>"));
        }
        for (k, v) in &self.others {
            out = out.replace(k, v);
        }
        // any other 32-hex word is a slot id
        let mut cooked = String::new();
        let mut word = String::new();
        for ch in out.chars().chain(std::iter::once('\0')) {
            if ch.is_ascii_alphanumeric() {
                word.push(ch);
            } else {
                cooked.push_str(&if is_hex32(&word) { "<slot>".to_string() } else { word.clone() });
                word.clear();
                if ch != '\0' {
                    cooked.push(ch);
                }
            }
        }
        cooked
    }

    fn value(&self, v: Value) -> Value {
        match v {
            Value::String(s) => Value::String(self.text(&s)),
            Value::Array(a) => Value::Array(a.into_iter().map(|x| self.value(x)).collect()),
            Value::Object(o) => Value::Object(o.into_iter().map(|(k, x)| (k, self.value(x))).collect()),
            other => other,
        }
    }

    fn job(&self, name: &str) -> String {
        self.jobs.iter().find(|(_, v)| v.as_str() == name).map(|(k, _)| k.clone()).unwrap_or_else(|| name.to_string())
    }

    fn exec(&self, name: &str) -> String {
        let tag = format!("<exec:{name}>");
        self.others.iter().find(|(_, v)| **v == tag).map(|(k, _)| k.clone()).unwrap_or_else(|| name.to_string())
    }
}

fn state(c: &Connection, names: &Names) -> Value {
    let key = |m: &Map<String, Value>, k: &str| m.get(k).map(|v| v.to_string()).unwrap_or_default();
    let mut out = Map::new();
    for table in ["jobs", "job_journal", "job_executions", "resource_leases", "execution_lane_slots"] {
        let mut r: Vec<Map<String, Value>> = rows(c, table)
            .into_iter()
            .map(|m| {
                let mut m: Map<String, Value> = m.into_iter().map(|(k, v)| (k, names.value(v))).collect();
                if table == "job_journal" {
                    m.shift_remove("id");
                    m.insert("event_id".into(), Value::from("<E>"));
                }
                if table == "execution_lane_slots" {
                    m.insert("slot_id".into(), Value::from("<slot>"));
                }
                m
            })
            .collect();
        match table {
            "jobs" => r.sort_by_key(|m| key(m, "id")),
            "job_journal" => r.sort_by_key(|m| (key(m, "job_id"), m["sequence"].as_i64().unwrap())),
            "job_executions" => r.sort_by_key(|m| (key(m, "job_id"), m["attempt"].as_i64().unwrap())),
            "resource_leases" => r.sort_by_key(|m| key(m, "resource_key")),
            _ => r.sort_by_key(|m| (key(m, "lane_id"), key(m, "job_id"), key(m, "acquired_at"), key(m, "state"), key(m, "lease_id"))),
        }
        out.insert(table.into(), Value::Array(r.into_iter().map(Value::Object).collect()));
    }
    Value::Object(out)
}

fn caps(v: &Value) -> BTreeMap<String, i64> {
    v.as_object().map(|o| o.iter().map(|(k, x)| (k.clone(), x.as_i64().unwrap_or(1))).collect()).unwrap_or_default()
}

fn opt(v: &Value) -> Option<String> {
    v.as_str().map(str::to_string)
}

/// One recorded operation: its result (as the recorded JSON) or the text of the error.
fn apply(c: &Connection, now: Now, op: &Value, names: &mut Names) -> Result<Value, String> {
    let kind = op["op"].as_str().unwrap();
    let job = |names: &Names| names.job(op["job"].as_str().unwrap_or(""));
    let err = |names: &Names, e: jobs::JobError| names.text(&e.to_string());
    let sql = |names: &Names, e: ax_db::rusqlite::Error| names.text(&e.to_string());
    Ok(match kind {
        "advance" => Value::Null,
        "create" => {
            let id = jobs::create_job(c, op["type"].as_str().unwrap(), &NewJob::default()).map_err(|e| err(names, e))?;
            names.jobs.insert(id, op["name"].as_str().unwrap().to_string());
            Value::Null
        }
        "acquire_execution" => {
            let got = leases::acquire_job_execution(c, now, &job(names), op["worker"].as_str().unwrap(), op["lease_seconds"].as_i64().unwrap()).map_err(|e| err(names, e))?;
            let name = op["as"].as_str().unwrap();
            names.others.insert(got.execution_id.clone(), format!("<exec:{name}>"));
            names.others.insert(got.lease_id.clone(), format!("<lease:{name}>"));
            names.value(serde_json::json!({ "execution_id": got.execution_id, "lease_id": got.lease_id, "attempt": got.attempt, "lease_expires_at": got.lease_expires_at }))
        }
        "heartbeat" => {
            let ok = leases::heartbeat_job_execution(
                c,
                now,
                &job(names),
                &names.exec(op["execution"].as_str().unwrap()),
                op["worker_state"].as_str().unwrap(),
                op["lease_seconds"].as_i64().unwrap(),
            )
            .map_err(|e| err(names, e))?;
            Value::Bool(ok)
        }
        "finish" => {
            leases::finish_job_execution(
                c,
                now,
                &job(names),
                &names.exec(op["execution"].as_str().unwrap()),
                op["status"].as_str().unwrap(),
                op["failure_class"].as_str().unwrap(),
            )
            .map_err(|e| err(names, e))?;
            Value::Null
        }
        "interrupt_stale" => {
            let got = leases::interrupt_stale_job_execution(c, now, &job(names)).map_err(|e| err(names, e))?;
            names.value(got.map(Value::from).unwrap_or(Value::Null))
        }
        "expired" => names.value(Value::Array(leases::expired_job_executions(c, now).map_err(|e| sql(names, e))?.into_iter().map(Value::Object).collect())),
        "retry_policy" => {
            let p = RetryPolicy {
                retry_count: op.get("retry_count").and_then(Value::as_i64),
                max_retries: op.get("max_retries").and_then(Value::as_i64),
                failure_class: op.get("failure_class").and_then(opt),
                next_run_at: op.get("next_run_at").map(opt),
            };
            leases::set_job_retry_policy(c, &job(names), &p).map_err(|e| err(names, e))?;
            Value::Null
        }
        "admission" => {
            let a = Admission {
                reason: op.get("reason").and_then(opt).unwrap_or_default(),
                next_run_at: op.get("next_run_at").and_then(opt),
                priority: op.get("priority").and_then(Value::as_i64),
                operation: op.get("operation").and_then(opt),
                detail: op.get("detail").and_then(opt),
                payload: None,
            };
            leases::set_job_admission(c, now, &job(names), op["state"].as_str().unwrap(), &a).map_err(|e| err(names, e))?;
            Value::Null
        }
        "snapshot" => names.value(leases::job_orchestration_snapshot(c).map_err(|e| sql(names, e))?),
        "lane_acquire" => {
            let lanes: Vec<String> = op["lanes"].as_array().unwrap().iter().map(|l| l.as_str().unwrap().to_string()).collect();
            let exec = names.exec(op["execution"].as_str().unwrap());
            let jid = job(names);
            let lease = opt(&op["lease_id"]);
            let req = LaneRequest { job_id: &jid, execution_id: &exec, lane_ids: &lanes, lease_id: lease.as_deref(), lease_seconds: op["lease_seconds"].as_i64().unwrap() };
            Value::Bool(leases::acquire_execution_lane_slots(c, now, &req, &caps(&op["caps"])).map_err(|e| err(names, e))?)
        }
        "lane_release" => {
            let exec = op.get("execution").and_then(Value::as_str).map(|e| names.exec(e));
            let jid = op.get("job").and_then(Value::as_str).map(|j| names.job(j));
            Value::from(leases::release_execution_lane_slots(c, exec.as_deref(), jid.as_deref(), op["state"].as_str().unwrap()).map_err(|e| sql(names, e))? as i64)
        }
        "lane_expire" => Value::from(leases::expire_execution_lane_slots(c, now).map_err(|e| sql(names, e))? as i64),
        "lane_snapshot" => names.value(leases::execution_lane_snapshot(c, now, &caps(&op["caps"])).map_err(|e| sql(names, e))?),
        "resource_acquire" => {
            let lease = NewResourceLease {
                owner_launch_id: op["owner"].as_str().unwrap().to_string(),
                resource_class: op.get("class").and_then(opt).unwrap_or_else(|| "BACKGROUND".into()),
                resource_type: op.get("type").and_then(opt),
                reserved_amount: op["amount"].as_f64().unwrap_or(0.0),
                lease_seconds: op.get("lease_seconds").and_then(Value::as_i64).unwrap_or(45),
            };
            let jid = op["job"].as_str().map(|j| names.job(j));
            let got = leases::acquire_resource_lease(c, now, op["key"].as_str().unwrap(), jid.as_deref(), &lease).map_err(|e| err(names, e))?;
            names.others.insert(got["lease_id"].as_str().unwrap().to_string(), format!("<lease:{}>", op["as"].as_str().unwrap()));
            names.value(got)
        }
        "resource_invalidate" => Value::from(leases::invalidate_stale_resource_leases(c, now, op["owner"].as_str().unwrap()).map_err(|e| sql(names, e))? as i64),
        "resource_expire" => names.value(Value::Array(leases::expire_resource_leases(c, now).map_err(|e| sql(names, e))?.into_iter().map(Value::Object).collect())),
        "resource_snapshot" => names.value(leases::resource_lease_snapshot(c, now).map_err(|e| sql(names, e))?),
        other => panic!("unknown operation {other}"),
    })
}

#[test]
fn every_recorded_scenario_replays_to_the_same_rows_results_and_errors() {
    let cases: Value = serde_json::from_str(include_str!("lease_ops_cases.json")).unwrap();
    let snapshot = schema();
    for (scenario, case) in cases.as_object().unwrap() {
        let c = Connection::open_in_memory().unwrap();
        create_table(&c, &snapshot, "jobs", "");
        create_table(&c, &snapshot, "job_journal", "UNIQUE(job_id, sequence)");
        create_table(&c, &snapshot, "job_dependencies", "");
        create_table(&c, &snapshot, "job_executions", "");
        create_table(&c, &snapshot, "resource_leases", "");
        c.execute_batch("CREATE UNIQUE INDEX idx_resource_leases_lease_id ON resource_leases(lease_id) WHERE lease_id IS NOT NULL").unwrap();
        create_table(&c, &snapshot, "execution_lane_slots", "");
        let mut names = Names::default();
        for (i, step) in case["steps"].as_array().unwrap().iter().enumerate() {
            let now = Now { secs: step["clock"]["secs"].as_i64().unwrap(), micros: step["clock"]["micros"].as_u64().unwrap() as u32 };
            jobs::set_clock_for_tests(Some(now.secs));
            c.execute_batch("BEGIN IMMEDIATE").unwrap();
            let result = apply(&c, now, &step["op"], &mut names);
            // Python commits what each method did, and a false answer from the lane slots still keeps the expired slots it deleted;
            // only a raised error undoes the step
            c.execute_batch(if result.is_ok() || step["op"]["op"] == "create" { "COMMIT" } else { "ROLLBACK" }).unwrap();
            let (got_result, got_error) = match result {
                Ok(v) => (v, None),
                Err(e) => (Value::Null, Some(e)),
            };
            let label = format!("{scenario}, step {i} ({})", step["op"]);
            assert_eq!(got_error.as_deref(), step["error"].as_str(), "{label}: error");
            assert_eq!(got_result, step["result"], "{label}: result");
            let got = state(&c, &names);
            for table in ["jobs", "job_journal", "job_executions", "resource_leases", "execution_lane_slots"] {
                assert_eq!(got[table], step["state"][table], "{label}: {table}");
            }
        }
    }
    jobs::set_clock_for_tests(None);
}
