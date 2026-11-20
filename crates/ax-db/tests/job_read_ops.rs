//! The job queries and checkpoints of `ax_db::job_reads` must answer what Python's `Database` answers, on the same fake clock. Recorded by
//! `tools/parity/gen_job_read_vectors.py`.

mod common;

use std::collections::HashMap;

use ax_db::job_reads::{self, Checkpoint};
use ax_db::jobs::{self, NewJob};
use ax_db::leases::{self, Now};
use ax_db::rusqlite::Connection;
use common::{create_table, rows, schema};
use serde_json::{Map, Value};

#[derive(Default)]
struct Names {
    jobs: HashMap<String, String>,
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
        let mut cooked = String::new();
        let mut word = String::new();
        for ch in out.chars().chain(std::iter::once('\0')) {
            if ch.is_ascii_alphanumeric() {
                word.push(ch);
            } else {
                cooked.push_str(&if is_hex32(&word) { "<id>".to_string() } else { word.clone() });
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
            Value::Object(o) => Value::Object(o.into_iter().map(|(k, x)| (self.text(&k), self.value(x))).collect()),
            other => other,
        }
    }

    fn job(&self, name: &str) -> String {
        self.jobs.iter().find(|(_, v)| v.as_str() == name).map(|(k, _)| k.clone()).unwrap_or_else(|| name.to_string())
    }
}

fn state(c: &Connection, names: &Names) -> Value {
    let key = |m: &Map<String, Value>, k: &str| m.get(k).map(|v| v.to_string()).unwrap_or_default();
    let mut out = Map::new();
    for table in ["jobs", "job_journal", "job_checkpoints", "job_activity_events"] {
        let mut r: Vec<Map<String, Value>> = rows(c, table)
            .into_iter()
            .map(|m| {
                let mut m: Map<String, Value> = m.into_iter().map(|(k, v)| (k, names.value(v))).collect();
                if table == "job_journal" {
                    m.shift_remove("id");
                    m.insert("event_id".into(), Value::from("<E>"));
                }
                m
            })
            .collect();
        match table {
            "jobs" => r.sort_by_key(|m| key(m, "id")),
            "job_journal" => r.sort_by_key(|m| (key(m, "job_id"), m["sequence"].as_i64().unwrap())),
            "job_checkpoints" => r.sort_by_key(|m| key(m, "job_id")),
            _ => r.sort_by_key(|m| m["id"].as_i64().unwrap()),
        }
        out.insert(table.into(), Value::Array(r.into_iter().map(Value::Object).collect()));
    }
    Value::Object(out)
}

fn strings(v: &Value) -> Vec<String> {
    v.as_array().unwrap().iter().map(|x| x.as_str().unwrap().to_string()).collect()
}

fn maps(rows: Vec<Map<String, Value>>) -> Value {
    Value::Array(rows.into_iter().map(Value::Object).collect())
}

fn opt(v: &Value) -> Option<String> {
    match v {
        Value::Null => None,
        Value::String(s) => Some(s.clone()),
        other => Some(other.to_string()),
    }
}

fn apply(c: &Connection, now: Now, op: &Value, names: &mut Names) -> Result<Value, String> {
    let job = |names: &Names| names.job(op["job"].as_str().unwrap_or(""));
    let err = |names: &Names, e: jobs::JobError| names.text(&e.to_string());
    let sql = |names: &Names, e: ax_db::rusqlite::Error| names.text(&e.to_string());
    Ok(match op["op"].as_str().unwrap() {
        "advance" => Value::Null,
        "create" => {
            let a = op.get("args").cloned().unwrap_or(Value::Null);
            let new = NewJob { resource_requirements: a.get("resource_requirements").cloned(), ..NewJob::default() };
            let id = jobs::create_job(c, op["type"].as_str().unwrap(), &new).map_err(|e| err(names, e))?;
            names.jobs.insert(id.clone(), op["name"].as_str().unwrap().to_string());
            if let Some(deps) = a.get("depends_on").and_then(Value::as_array) {
                let deps: Vec<String> = deps.iter().map(|d| names.job(d.as_str().unwrap())).collect();
                jobs::add_job_dependencies(c, &id, &deps, "SUCCESS").map_err(|e| err(names, e))?;
            }
            Value::Null
        }
        "transition" => {
            let fields = op["fields"].as_object().cloned().unwrap_or_default();
            jobs::transition_job(c, &job(names), op["status"].as_str().unwrap(), &fields).map_err(|e| err(names, e))?;
            Value::Null
        }
        "acquire" => {
            let got = leases::acquire_job_execution(c, now, &job(names), op["worker"].as_str().unwrap(), op["lease_seconds"].as_i64().unwrap()).map_err(|e| err(names, e))?;
            let n = op["as"].as_str().unwrap();
            names.others.insert(got.execution_id, format!("<exec:{n}>"));
            names.others.insert(got.lease_id, format!("<lease:{n}>"));
            Value::Null
        }
        "finish" => {
            let tag = format!("<exec:{}>", op["execution"].as_str().unwrap());
            let ex = names.others.iter().find(|(_, v)| **v == tag).map(|(k, _)| k.clone()).unwrap();
            leases::finish_job_execution(c, now, &job(names), &ex, "COMPLETED", "").map_err(|e| err(names, e))?;
            Value::Null
        }
        "activity" => {
            c.execute(
                "INSERT INTO job_activity_events(job_id,level,event,operation,message,details,created_at) VALUES(?,?,?,?,?,?,?)",
                ax_db::rusqlite::params![
                    job(names),
                    op["level"].as_str(),
                    op["event"].as_str(),
                    op["operation"].as_str(),
                    op["message"].as_str(),
                    op["details"].as_str(),
                    jobs::now_iso()
                ],
            )
            .map_err(|e| sql(names, e))?;
            Value::Null
        }
        "telemetry" => {
            c.execute(
                "INSERT INTO job_transfer_telemetry(job_id,telemetry,updated_at) VALUES(?,?,?)",
                ax_db::rusqlite::params![job(names), op["telemetry"].as_str(), op["updated_at"].as_str()],
            )
            .map_err(|e| sql(names, e))?;
            Value::Null
        }
        "jobs" => names.value(maps(job_reads::jobs(c, op["limit"].as_i64().unwrap()).map_err(|e| sql(names, e))?)),
        "active" => names.value(maps(job_reads::active_jobs(c, op.get("type").and_then(Value::as_str)).map_err(|e| sql(names, e))?)),
        "dashboard" => names.value(job_reads::job_dashboard_snapshot(c).map_err(|e| sql(names, e))?),
        "get_job" => names.value(job_reads::get_job(c, &job(names)).map_err(|e| sql(names, e))?.map(Value::Object).unwrap_or(Value::Null)),
        "latest_activity" => Value::from(job_reads::latest_job_activity_id(c, &job(names)).map_err(|e| sql(names, e))?),
        "recent" => {
            let ids: Vec<String> = strings(&op["jobs"]).into_iter().map(|n| names.job(&n)).collect();
            names.value(Value::Array(
                job_reads::recent_job_activity(c, &ids, op["limit"].as_i64().unwrap(), op.get("before").and_then(Value::as_i64)).map_err(|e| sql(names, e))?,
            ))
        }
        "telemetry_get" => {
            let ids: Vec<String> = strings(&op["jobs"]).into_iter().map(|n| names.job(&n)).collect();
            names.value(Value::Object(job_reads::get_job_transfer_telemetry(c, &ids).map_err(|e| sql(names, e))?))
        }
        "checkpoint_get" => names.value(job_reads::get_job_checkpoint(c, &job(names)).map_err(|e| sql(names, e))?.map(Value::Object).unwrap_or(Value::Null)),
        "checkpoint_save" => {
            let a = &op["args"];
            let cp = Checkpoint {
                payload: a.get("payload").filter(|v| !v.is_null()).cloned(),
                stage: a.get("stage").and_then(opt),
                item_id: a.get("item_id").and_then(opt),
                item_index: a.get("item_index").and_then(Value::as_i64),
                total_items: a.get("total_items").and_then(Value::as_i64),
                current_operation: a.get("current_operation").and_then(opt),
                last_completed_operation: a.get("last_completed_operation").and_then(opt),
                input_fingerprint: a.get("input_fingerprint").and_then(opt),
                output_fingerprint: a.get("output_fingerprint").and_then(opt),
                retry_count: a.get("retry_count").and_then(Value::as_i64),
                update_fields: a.get("update_fields").and_then(Value::as_object).cloned().unwrap_or_default(),
            };
            job_reads::save_job_checkpoint(c, &job(names), &cp).map_err(|e| err(names, e))?;
            Value::Null
        }
        other => panic!("unknown operation {other}"),
    })
}

#[test]
fn every_recorded_scenario_answers_and_writes_like_python() {
    let cases: Value = serde_json::from_str(include_str!("job_read_ops_cases.json")).unwrap();
    let snapshot = schema();
    for (scenario, case) in cases.as_object().unwrap() {
        let c = Connection::open_in_memory().unwrap();
        create_table(&c, &snapshot, "jobs", "");
        create_table(&c, &snapshot, "job_journal", "UNIQUE(job_id, sequence)");
        create_table(&c, &snapshot, "job_dependencies", "");
        create_table(&c, &snapshot, "job_executions", "");
        create_table(&c, &snapshot, "resource_leases", "");
        create_table(&c, &snapshot, "execution_lane_slots", "");
        create_table(&c, &snapshot, "job_checkpoints", "");
        create_table(&c, &snapshot, "job_transfer_telemetry", "");
        create_table(&c, &snapshot, "job_activity_events", "");
        let mut names = Names::default();
        for (i, step) in case["steps"].as_array().unwrap().iter().enumerate() {
            let now = Now { secs: step["clock"]["secs"].as_i64().unwrap(), micros: 0 };
            jobs::set_clock_for_tests(Some(now.secs));
            c.execute_batch("BEGIN IMMEDIATE").unwrap();
            let result = apply(&c, now, &step["op"], &mut names);
            let keep = result.is_ok() || step["op"]["op"] == "create";
            c.execute_batch(if keep { "COMMIT" } else { "ROLLBACK" }).unwrap();
            let (got_result, got_error) = match result {
                Ok(v) => (v, None),
                Err(e) => (Value::Null, Some(e)),
            };
            let label = format!("{scenario}, step {i} ({})", step["op"]);
            assert_eq!(got_error.as_deref(), step["error"].as_str(), "{label}: error");
            assert_eq!(got_result, step["result"], "{label}: result");
            let got = state(&c, &names);
            for table in ["jobs", "job_journal", "job_checkpoints", "job_activity_events"] {
                assert_eq!(got[table], step["state"][table], "{label}: {table}");
            }
        }
    }
    jobs::set_clock_for_tests(None);
}
