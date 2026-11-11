//! Twin of spike/tests/video_owner_events_test.cpp (ctest `video_owner_events`): mpv's command replies never fabricate
//! property readback; errors and a reload's unknowns are kept.

use serde_json::Value;
use xr_renderer::video::readback::Readback;

#[test]
fn replies_never_fabricate_readback() {
    let mut r = Readback::default();
    r.reply(true, Some(""));
    assert_eq!(r.json()["deband_applied"], Value::Null);
    assert_eq!(r.json()["paused"], Value::Null);
    r.reply(false, Some("native seek rejected"));
    assert_eq!(r.command_errors, 1);
    assert_eq!(r.command_error, "native seek rejected");
    r.flag("deband", true);
    r.flag("pause", true);
    r.number("time-pos", 12.5);
    r.number("duration", 80.0);
    assert_eq!(r.json()["deband_applied"], true);
    assert_eq!(r.json()["paused"], true);
    assert_eq!(r.json()["position_s"], 12.5);
    let before = r.revision;
    r.number("time-pos", f64::NAN);
    assert_eq!(r.revision, before, "a NaN observation changes nothing");
    r.reply(false, Some(&"x".repeat(800)));
    assert_eq!(r.command_error.len(), 256);
    r.restart();
    assert_eq!(r.json()["paused"], Value::Null);
    assert_eq!(r.json()["position_s"], Value::Null);
    assert_eq!(r.json()["duration_s"], Value::Null);
    assert_eq!(
        r.json()["deband_applied"],
        true,
        "deband is the player's setting and survives a reload"
    );
}
