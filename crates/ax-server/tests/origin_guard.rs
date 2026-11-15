//! Every vector (produced by the real Python middleware) must get the same verdict from the Rust port.
use ax_server::guard;

#[test]
fn the_rust_guard_agrees_with_the_python_middleware_on_every_vector() {
    let vectors: Vec<serde_json::Value> = serde_json::from_str(include_str!("origin_vectors.json")).unwrap();
    assert!(vectors.len() > 3000, "only {} vectors", vectors.len());
    let mut wrong = Vec::new();
    for v in &vectors {
        let (m, p, h) = (v["method"].as_str().unwrap(), v["path"].as_str().unwrap(), v["host"].as_str().unwrap());
        let origin = v["origin"].as_str();
        let want = v["allowed"].as_bool().unwrap();
        if guard::allowed(m, p, origin, h) != want {
            wrong.push(format!("{m} {p} origin={origin:?} host={h:?}: Python says allowed={want}"));
        }
    }
    assert!(
        wrong.is_empty(),
        "{} of {} vectors differ, for example:\n{}",
        wrong.len(),
        vectors.len(),
        wrong.iter().take(8).cloned().collect::<Vec<_>>().join("\n")
    );
}
