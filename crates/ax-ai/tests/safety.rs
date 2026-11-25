//! Replays `safety_cases.json`, recorded from Python's `ai/ai_safety.py` by `tools/parity/gen_ai_safety_vectors.py`.

use ax_ai::safety::{is_secret_name, redact, redact_text, ExecError, Executor, LogFn, NotAllowed, PermissionBroker};
use serde_json::{json, Map, Value};
use std::cell::RefCell;
use std::fs;
use std::path::Path;
use std::rc::Rc;

fn data() -> Value {
    serde_json::from_str(&fs::read_to_string(Path::new(env!("CARGO_MANIFEST_DIR")).join("tests/safety_cases.json")).unwrap()).unwrap()
}

fn join(pieces: &Value) -> String {
    pieces.as_array().unwrap().iter().map(|p| p.as_str().unwrap()).collect()
}

/// The recorded values keep strings as pieces: `{"p": [..]}` text, `{"d": [[key pieces, value]..]}` object, `{"l": [..]}` list, `{"v": x}` other.
fn unchunk(v: &Value) -> Value {
    if let Some(p) = v.get("p") {
        return Value::String(join(p));
    }
    if let Some(d) = v.get("d") {
        return Value::Object(d.as_array().unwrap().iter().map(|kv| (join(&kv[0]), unchunk(&kv[1]))).collect());
    }
    if let Some(l) = v.get("l") {
        return Value::Array(l.as_array().unwrap().iter().map(unchunk).collect());
    }
    v["v"].clone()
}

#[test]
fn redact_text_matches_python() {
    let d = data();
    let mut bad = Vec::new();
    for case in d["texts"].as_array().unwrap() {
        let (input, want) = (join(&case[0]), join(&case[1]));
        let got = redact_text(&input);
        if got != want {
            bad.push(format!("{input:?}\n   want {want:?}\n   got  {got:?}"));
        }
    }
    assert!(bad.is_empty(), "{} texts differ:\n{}", bad.len(), bad.iter().take(8).cloned().collect::<Vec<_>>().join("\n"));
}

#[test]
fn secret_names_match_python() {
    let d = data();
    let bad: Vec<String> = d["names"]
        .as_array()
        .unwrap()
        .iter()
        .filter(|c| is_secret_name(&join(&c[0])) != c[1].as_bool().unwrap())
        .map(|c| format!("{:?}: Python says {}", join(&c[0]), c[1]))
        .collect();
    assert!(bad.is_empty(), "{} names differ:\n{}", bad.len(), bad.iter().take(10).cloned().collect::<Vec<_>>().join("\n"));
}

#[test]
fn redact_values_match_python() {
    let d = data();
    for (i, case) in d["values"].as_array().unwrap().iter().enumerate() {
        let (input, want) = (unchunk(&case[0]), unchunk(&case[1]));
        let got = redact(&input);
        assert_eq!(got.to_string(), want.to_string(), "value {i}");
    }
}

fn executor(name: &str) -> Executor {
    match name {
        "ok" => Box::new(|args| Ok(json!({"count": 3, "api_key": "k".repeat(12), "note": format!("bearer {}", "x".repeat(12)), "args": args}))),
        "echo" => Box::new(|args| Ok(Value::Object(args.clone()))),
        "fail_value" => Box::new(|_| Err(("ValueError".to_string(), "boom".to_string()) as ExecError)),
        "fail_secret" => Box::new(|_| Err(("RuntimeError".to_string(), format!("token={} failed", "q".repeat(10))))),
        // Python's str(KeyError("zzz…")) is the repr of the key
        "fail_long" => Box::new(|_| Err(("KeyError".to_string(), format!("'{}'", "z".repeat(500))))),
        other => panic!("unknown executor {other}"),
    }
}

// Values are compared as text on purpose: JSON objects compare equal whatever their key order, and the order is part of what Python produced.
#[allow(clippy::cmp_owned)]
fn run_scenario(sc: &Value) -> Result<(), String> {
    let now = Rc::new(RefCell::new(1_900_000_000.0_f64));
    let log: Rc<RefCell<Vec<Value>>> = Rc::new(RefCell::new(Vec::new()));
    let counter = Rc::new(RefCell::new(0u32));
    let clock = {
        let now = now.clone();
        Box::new(move || *now.borrow())
    };
    let ids = {
        let counter = counter.clone();
        Box::new(move || {
            *counter.borrow_mut() += 1;
            format!("id{:04}", counter.borrow())
        })
    };
    let logger: LogFn = {
        let log = log.clone();
        Box::new(move |m, l, e, d| log.borrow_mut().push(json!([m, l, e, d])))
    };
    let mut broker = PermissionBroker::new(Some(logger), clock, ids);
    let mut made: Vec<String> = Vec::new();
    let mut steps: Vec<Value> = Vec::new();
    let t = |now: &Rc<RefCell<f64>>| *now.borrow();
    for op in sc["ops"].as_array().unwrap() {
        let result: Result<Value, NotAllowed> = match op["op"].as_str().unwrap() {
            "register" => broker.register(op["action"].as_str().unwrap(), executor(op["executor"].as_str().unwrap())).map(|_| json!("registered")),
            "propose" => {
                let items: Vec<String> = op["items"].as_array().unwrap().iter().map(|i| i.as_str().unwrap().to_string()).collect();
                let args: Map<String, Value> = op["args"].as_object().unwrap().clone();
                let r = broker.propose(op["action"].as_str().unwrap(), op["summary"].as_str().unwrap(), &items, &args).map(|p| (p.id.clone(), p.public(t(&now))));
                r.map(|(id, public)| {
                    made.push(id);
                    public
                })
            }
            "decide" => broker
                .decide(&made[op["n"].as_u64().unwrap() as usize], op["approve"].as_bool().unwrap(), op["typed"].as_str().unwrap())
                .map(|p| p.public(t(&now))),
            "run" => broker.run(&made[op["n"].as_u64().unwrap() as usize]).map(|p| p.public(t(&now))),
            "get" => Ok(broker.get(&made[op["n"].as_u64().unwrap() as usize]).map_or(Value::Null, |p| p.public(t(&now)))),
            "pending" => Ok(json!(broker.pending().iter().map(|p| p.id.clone()).collect::<Vec<_>>())),
            "decide_unknown" => broker.decide("nope", true, "").map(|_| Value::Null),
            "run_unknown" => broker.run("nope").map(|_| Value::Null),
            "advance" => {
                *now.borrow_mut() += op["seconds"].as_f64().unwrap();
                Ok(json!("advanced"))
            }
            other => panic!("{other}"),
        };
        // an operation that Python raised for gives no step of its own when it is one of the *_unknown probes
        match (op["op"].as_str().unwrap(), result) {
            ("decide_unknown" | "run_unknown", Err(e)) => steps.push(json!({"error": e.0})),
            (_, Ok(v)) => steps.push(json!({"ok": v})),
            (_, Err(e)) => steps.push(json!({"error": e.0})),
        }
    }
    let state: Vec<Value> = broker
        .proposals()
        .iter()
        .map(|p| {
            json!([
                p.id,
                p.action,
                p.status,
                p.needs_typed,
                p.created,
                p.expires,
                p.summary,
                p.items,
                p.args,
                p.result,
                p.history.iter().map(|(t, s)| json!([t, s])).collect::<Vec<_>>(),
                p.public(t(&now))
            ])
        })
        .collect();
    let expect = &sc["expect"];
    let got = json!({"steps": steps, "state": state, "log": *log.borrow()});
    for key in ["steps", "log", "state"] {
        if got[key].to_string() != expect[key].to_string() {
            let (g, w) = (got[key].as_array().unwrap(), expect[key].as_array().unwrap());
            let at = (0..g.len().max(w.len())).find(|&i| g.get(i) != w.get(i)).unwrap_or(0);
            return Err(format!("{key} differ at {at}:\n   want {}\n   got  {}", w.get(at).unwrap_or(&Value::Null), g.get(at).unwrap_or(&Value::Null)));
        }
    }
    Ok(())
}

#[test]
fn broker_matches_python() {
    let d = data();
    let mut failures = Vec::new();
    for sc in d["broker"].as_array().unwrap() {
        if let Err(e) = run_scenario(sc) {
            failures.push(format!("{}: {e}", sc["name"]));
        }
    }
    assert!(failures.is_empty(), "{}", failures.join("\n"));
}
