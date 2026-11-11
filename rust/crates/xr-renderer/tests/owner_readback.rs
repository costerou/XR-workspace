//! Twin of spike/tests/owner_readback_test.cpp (ctest `owner_readback`): the bounded owner snapshot stream sends a
//! change, refreshes an unchanged line once a second, retries after a resend and rejects a line over the limit. The
//! Rust renderer builds the line in `take_owner_readback` (keys sorted, as nlohmann writes them) and paces it with
//! `ReadbackLimiter`.

use serde_json::{Value, json};
use xr_renderer::capture::ReadbackLimiter;

/// OwnerReadbackStream::next: the line for `snapshot` when it is due.
fn next(stream: &mut ReadbackLimiter, snapshot: &Value, now_ms: u64) -> Option<String> {
    let line = json!({ "owner_readback": snapshot }).to_string();
    if !stream.due(&line, now_ms) {
        return None;
    }
    stream.sent(&line, now_ms);
    Some(line)
}

#[test]
fn bounded_owner_snapshot() {
    let mut stream = ReadbackLimiter::default();
    // Keys in nlohmann's sorted order, whichever map serde_json was built with.
    let mut s = json!({ "immersive": null, "outputs": [], "video": null });
    let first = next(&mut stream, &s, 0).expect("first snapshot sent");
    let parsed: Value = serde_json::from_str(&first).unwrap();
    assert_eq!(parsed["owner_readback"], s);
    assert!(next(&mut stream, &s, 10).is_none());
    assert!(next(&mut stream, &s, 999).is_none());
    assert!(
        next(&mut stream, &s, 1000).is_some(),
        "an unchanged line is refreshed once a second"
    );
    s["outputs"] = json!([{ "height": 0, "name": "XR-3", "ready": false, "width": 0 }]);
    assert!(
        next(&mut stream, &s, 1050).is_some(),
        "a change is sent at once"
    );
    s["video"] = Value::from("x".repeat(16384));
    assert!(
        next(&mut stream, &s, 2000).is_none(),
        "a line over the limit is never sent"
    );
    stream.resend();
    s["video"] = Value::Null;
    assert!(
        next(&mut stream, &s, 2001).is_some(),
        "a resend sends the line again"
    );
}
