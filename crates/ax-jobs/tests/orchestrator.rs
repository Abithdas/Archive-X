//! The Rust orchestrator must decide and write like Python's `JobOrchestrator`. Recorded by `tools/parity/gen_orchestrator_vectors.py`:
//! operations on a fake clock against a real database, the result of each and the rows it changed.

use std::collections::{BTreeMap, HashMap, HashSet};
use std::sync::{Arc, Mutex};

use ax_core::governor_policy::{Governor, GovernorConfig};
use ax_core::resource_policy::ResourcePolicy;
use ax_db::jobs::{self, NewJob};
use ax_db::leases::{self, LaneRequest, Now};
use ax_db::rusqlite::{types::ValueRef, Connection};
use ax_jobs::orchestrator::{FailureInfo, ManagerView, Orchestrator};
use serde_json::{json, Map, Value};

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

fn rows(c: &Connection, table: &str) -> Vec<Map<String, Value>> {
    let mut st = c.prepare(&format!("SELECT * FROM {table}")).unwrap();
    let cols: Vec<String> = st.column_names().map(|s| s.to_string()).collect();
    st.query_map([], |r| {
        let mut m = Map::new();
        for (i, name) in cols.iter().enumerate() {
            m.insert(
                name.clone(),
                match r.get_ref(i)? {
                    ValueRef::Null => Value::Null,
                    ValueRef::Integer(v) => Value::from(v),
                    ValueRef::Real(v) => Value::from(v),
                    ValueRef::Text(t) => Value::String(String::from_utf8_lossy(t).to_string()),
                    ValueRef::Blob(_) => Value::Null,
                },
            );
        }
        Ok(m)
    })
    .unwrap()
    .map(|r| r.unwrap())
    .collect()
}

const TABLES: [&str; 5] = ["jobs", "job_journal", "job_executions", "resource_leases", "execution_lane_slots"];

fn state(c: &Connection, names: &Names) -> BTreeMap<String, Value> {
    let key = |m: &Map<String, Value>, k: &str| m.get(k).map(|v| v.to_string()).unwrap_or_default();
    let mut out = BTreeMap::new();
    for table in TABLES {
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
        out.insert(table.to_string(), Value::Array(r.into_iter().map(Value::Object).collect()));
    }
    out
}

struct Manager {
    lanes_by_type: Value,
    category_by_type: Value,
    caps: Value,
    running: HashSet<String>,
    cancelled: HashSet<String>,
    paused_jobs: HashSet<String>,
    paused: bool,
    lane_drain: bool,
}

impl ManagerView for Manager {
    fn is_running(&self, id: &str) -> bool {
        self.running.contains(id)
    }
    fn is_cancelled(&self, id: &str) -> bool {
        self.cancelled.contains(id)
    }
    fn is_job_paused(&self, id: &str) -> bool {
        self.paused_jobs.contains(id)
    }
    fn paused(&self) -> bool {
        self.paused
    }
    fn lane_drain(&self) -> bool {
        self.lane_drain
    }
    fn lane_keys(&self, job: &Map<String, Value>) -> Vec<String> {
        let t = job.get("job_type").and_then(Value::as_str).unwrap_or("");
        self.lanes_by_type.get(t).and_then(Value::as_array).map(|a| a.iter().map(|x| x.as_str().unwrap().to_string()).collect()).unwrap_or_default()
    }
    fn lane_capacities(&self, lanes: &[String]) -> BTreeMap<String, i64> {
        lanes.iter().map(|l| (l.clone(), self.caps.get(l).and_then(Value::as_i64).unwrap_or(1))).collect()
    }
    fn resource_category(&self, job: &Map<String, Value>) -> String {
        let t = job.get("job_type").and_then(Value::as_str).unwrap_or("");
        self.category_by_type.get(t).and_then(Value::as_str).unwrap_or("generic").to_string()
    }
    fn worker_id(&self) -> String {
        "archive-x-worker".into()
    }
}

type Clock = Arc<Mutex<(i64, u32, f64)>>;

fn names_of(v: &Value) -> HashSet<String> {
    v.as_array().unwrap().iter().map(|x| x.as_str().unwrap().to_string()).collect()
}

#[test]
fn every_recorded_scenario_replays_to_the_same_results_errors_and_rows() {
    let cases: Value = serde_json::from_str(include_str!("orchestrator_cases.json")).unwrap();
    let mut total = 0;
    for (scenario, case) in cases["scenarios"].as_object().unwrap() {
        let c = Connection::open_in_memory().unwrap();
        ax_db::schema::create_fresh(&c).unwrap();
        let clock: Clock = Arc::new(Mutex::new((0, 0, 0.0)));
        let (c1, c2) = (clock.clone(), clock.clone());
        let orch_state = Orchestrator::new(
            Box::new(move || {
                let g = c1.lock().unwrap();
                Now { secs: g.0, micros: g.1 }
            }),
            Box::new(move || c2.lock().unwrap().2),
        );
        let mut orch = orch_state;
        let rclock = {
            let k = clock.clone();
            Arc::new(move || {
                let g = k.lock().unwrap();
                (g.0 as f64 + f64::from(g.1) / 1e6, g.2)
            })
        };
        let mut resources = case["resources"].as_bool().unwrap().then(|| ResourcePolicy::new(cases["hardware"].clone(), 4, rclock.clone()));
        let mut governor = case["governor"].as_bool().unwrap().then(|| Governor::new(GovernorConfig::default(), rclock.clone()));
        let mut mgr = Manager {
            lanes_by_type: cases["lanes_by_type"].clone(),
            category_by_type: cases["category_by_type"].clone(),
            caps: cases["caps"].clone(),
            running: HashSet::new(),
            cancelled: HashSet::new(),
            paused_jobs: HashSet::new(),
            paused: false,
            lane_drain: false,
        };
        let mut names = Names::default();
        let mut expected: BTreeMap<String, Value> = BTreeMap::new();
        for (i, step) in case["steps"].as_array().unwrap().iter().enumerate() {
            total += 1;
            let (secs, micros, mono) = (step["clock"]["secs"].as_i64().unwrap(), step["clock"]["micros"].as_u64().unwrap() as u32, step["clock"]["mono"].as_f64().unwrap());
            *clock.lock().unwrap() = (secs, micros, mono);
            jobs::set_clock_for_tests(Some(secs));
            let op = &step["op"];
            let kind = op["op"].as_str().unwrap();
            c.execute_batch("BEGIN IMMEDIATE").unwrap();
            let job = |names: &Names| names.job(op["job"].as_str().unwrap_or(""));
            let outcome: Result<Value, String> = (|| {
                let t = |names: &Names, e: &dyn std::fmt::Display| names.text(&e.to_string());
                Ok(match kind {
                    "advance" | "mgr" => {
                        if kind == "mgr" {
                            if let Some(v) = op.get("running") {
                                mgr.running = names_of(v).into_iter().map(|n| names.job(&n)).collect();
                            }
                            if let Some(v) = op.get("cancelled") {
                                mgr.cancelled = names_of(v).into_iter().map(|n| names.job(&n)).collect();
                            }
                            if let Some(v) = op.get("paused_jobs") {
                                mgr.paused_jobs = names_of(v).into_iter().map(|n| names.job(&n)).collect();
                            }
                            if let Some(v) = op.get("paused") {
                                mgr.paused = v.as_bool().unwrap();
                            }
                            if let Some(v) = op.get("lane_drain") {
                                mgr.lane_drain = v.as_bool().unwrap();
                            }
                        }
                        Value::Null
                    }
                    "create" => {
                        let id = jobs::create_job(&c, op["type"].as_str().unwrap(), &NewJob::default()).map_err(|e| t(&names, &e))?;
                        names.jobs.insert(id, op["name"].as_str().unwrap().to_string());
                        Value::Null
                    }
                    "set_fields" => {
                        jobs::update_job(&c, &job(&names), op["fields"].as_object().unwrap()).map_err(|e| t(&names, &e))?;
                        Value::Null
                    }
                    "next_run" => {
                        let p = leases::RetryPolicy { next_run_at: Some(op["value"].as_str().map(str::to_string)), ..Default::default() };
                        leases::set_job_retry_policy(&c, &job(&names), &p).map_err(|e| t(&names, &e))?;
                        Value::Null
                    }
                    "depend" => {
                        let on: Vec<String> = op["on"].as_array().unwrap().iter().map(|n| names.job(n.as_str().unwrap())).collect();
                        jobs::add_job_dependencies(&c, &job(&names), &on, op["type"].as_str().unwrap()).map_err(|e| t(&names, &e))?;
                        Value::Null
                    }
                    "transition" => {
                        jobs::transition_job(&c, &job(&names), op["status"].as_str().unwrap(), op["fields"].as_object().unwrap()).map_err(|e| t(&names, &e))?;
                        Value::Null
                    }
                    "prepare" => {
                        let (ok, reason) = orch.prepare(&c, &mgr, resources.as_mut(), governor.as_mut(), &job(&names)).map_err(|e| t(&names, &e))?;
                        json!([ok, reason])
                    }
                    "waiting" => {
                        orch.waiting(&c, &job(&names), op["reason"].as_str().unwrap(), op["operation"].as_str().unwrap(), op["retry_after"].as_f64())
                            .map_err(|e| t(&names, &e))?;
                        Value::Null
                    }
                    "ready" => {
                        orch.ready(&c, &job(&names)).map_err(|e| t(&names, &e))?;
                        Value::Null
                    }
                    "start" => {
                        let got = orch.start_execution(&c, &mgr, &job(&names)).map_err(|e| t(&names, &e))?;
                        let name = op["as"].as_str().unwrap();
                        names.others.insert(got.execution_id.clone(), format!("<exec:{name}>"));
                        names.others.insert(got.lease_id.clone(), format!("<lease:{name}>"));
                        names.value(json!({"execution_id": got.execution_id, "lease_id": got.lease_id, "attempt": got.attempt, "lease_expires_at": got.lease_expires_at}))
                    }
                    "lane_acquire" => {
                        let lanes: Vec<String> = op["lanes"].as_array().unwrap().iter().map(|l| l.as_str().unwrap().to_string()).collect();
                        let exec = names.exec(op["execution"].as_str().unwrap());
                        let jid = job(&names);
                        let req = LaneRequest { job_id: &jid, execution_id: &exec, lane_ids: &lanes, lease_id: None, lease_seconds: 45 };
                        let caps: BTreeMap<String, i64> = cases["caps"].as_object().unwrap().iter().map(|(k, v)| (k.clone(), v.as_i64().unwrap())).collect();
                        Value::Bool(leases::acquire_execution_lane_slots(&c, Now { secs, micros }, &req, &caps).map_err(|e| t(&names, &e))?)
                    }
                    "lane_release" => {
                        let exec = names.exec(op["execution"].as_str().unwrap());
                        Value::from(leases::release_execution_lane_slots(&c, Some(&exec), None, "RELEASED").map_err(|e| t(&names, &e))? as i64)
                    }
                    "heartbeat" => {
                        let ex = names.exec(op["execution"].as_str().unwrap());
                        Value::Bool(orch.heartbeat_execution(&c, &job(&names), &ex, op["worker_state"].as_str().unwrap()).map_err(|e| t(&names, &e))?)
                    }
                    "finish" => {
                        let ex = names.exec(op["execution"].as_str().unwrap());
                        orch.finish_execution(&c, &job(&names), &ex, op["status"].as_str().unwrap(), op["failure_class"].as_str().unwrap())
                            .map_err(|e| t(&names, &e))?;
                        Value::Null
                    }
                    "fail" => {
                        let info = FailureInfo {
                            type_name: op["type_name"].as_str().unwrap(),
                            message: op["text"].as_str().unwrap(),
                            is_timeout: op["is_timeout"].as_bool().unwrap(),
                            is_os_error: op["is_os_error"].as_bool().unwrap(),
                            is_input_error: op["is_input_error"].as_bool().unwrap(),
                        };
                        let (retry, class, delay) = orch.schedule_failure(&c, &job(&names), &info, op["jitter"].as_u64().unwrap() as u32).map_err(|e| t(&names, &e))?;
                        json!([retry, class, delay])
                    }
                    "recover" => names.value(Value::Array(orch.recover_expired_leases(&c).map_err(|e| t(&names, &e))?.into_iter().map(Value::from).collect())),
                    "snapshot" => names.value(orch.snapshot(&c).map_err(|e| t(&names, &e))?),
                    "eff" => {
                        let j = ax_db::job_reads::get_job(&c, &job(&names)).unwrap().unwrap();
                        json!(orch.effective_priority(&c, &mgr, &j))
                    }
                    "res" => {
                        let r = resources.as_mut().unwrap();
                        match op["call"].as_str().unwrap() {
                            "pause" => r.pause_background(op["reason"].as_str().unwrap()),
                            "resume" => r.resume_background(),
                            "p_enter" => {
                                r.playback_enter();
                            }
                            "p_exit" => {
                                r.playback_exit();
                            }
                            "configure" => {
                                r.configure(
                                    op.get("mode").and_then(Value::as_str),
                                    op.get("limits").and_then(Value::as_object),
                                    op.get("playback_gate").and_then(Value::as_bool),
                                    None,
                                    "",
                                )
                                .unwrap();
                            }
                            "take" => {
                                let cat = op["category"].as_str().unwrap();
                                r.begin_waiting(cat);
                                let got = r.try_take_background(cat);
                                r.end_waiting(cat);
                                // Python waits half a second and gives up with an empty TimeoutError
                                if !got {
                                    return Err(String::new());
                                }
                            }
                            "release" => r.release_background(op["category"].as_str().unwrap()),
                            "snapshot" => return Ok(r.snapshot()),
                            other => panic!("{other}"),
                        }
                        Value::Null
                    }
                    "gov" => {
                        let g = governor.as_mut().unwrap();
                        match op["call"].as_str().unwrap() {
                            "floodwait" => g.note_floodwait(op["seconds"].as_f64().unwrap(), op["kind"].as_str(), None),
                            "neterr" => g.note_network_error(op["message"].as_str().unwrap()),
                            "transfer" => g.set_transfer_active(op["active"].as_i64().unwrap()),
                            other => panic!("{other}"),
                        }
                        Value::Null
                    }
                    other => panic!("unknown operation {other}"),
                })
            })();
            c.execute_batch(if outcome.is_ok() || kind == "create" { "COMMIT" } else { "ROLLBACK" }).unwrap();
            let label = format!("{scenario}, step {i} ({op})");
            let (got_result, got_error) = match outcome {
                Ok(v) => (v, None),
                Err(e) => (Value::Null, Some(e)),
            };
            assert_eq!(got_error.as_deref(), step["error"].as_str(), "{label}: error");
            assert_eq!(got_result, step["result"], "{label}: result");
            for (table, rows) in step["state_changes"].as_object().unwrap() {
                expected.insert(table.clone(), rows.clone());
            }
            let got = state(&c, &names);
            for table in TABLES {
                assert_eq!(&got[table], expected.get(table).unwrap_or(&Value::Array(vec![])), "{label}: {table}");
            }
        }
    }
    jobs::set_clock_for_tests(None);
    assert!(total > 250, "{total} steps");
}
