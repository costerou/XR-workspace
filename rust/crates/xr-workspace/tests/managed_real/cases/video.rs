//! `view_controls::video`'s parity cases on the scripted compositor (rust/crates/xr-workspace/VIEW_CONTROLS.md): a VR
//! video opened from a small file in the session, played and left, on both supervisors.
//!
//! `video_open` goes to the control socket as the panel sends it (the key helper's Ctrl+Alt+O asks a file picker
//! first, which a test must not open); Escape's `video_exit` goes through each run's own key helper. Both runs send
//! the renderer the same `video` frames (open, the quality, then close), the open's with one descriptor, the file
//! (`Observed::frames`, compared by `parity`), and answer the panel's `status` with the same `video` entry. The video
//! keys are not installed on either side until `binds` gives the Rust run the helper's command line (oracle.py).
//!
//! The file is a one-frame YUV4MPEG2 clip, which the real ffprobe reads on both sides (a file it refused would be
//! told with a desktop notification).

use std::fs;
use std::path::Path;
use std::time::{Duration, Instant};

use serde_json::{Value, json};

use super::{Session, Who, parity_acting};

/// `{"type":"file"...}`: the renderer's record of the open frame's descriptor.
fn frames_with(s: &Session, needle: &str, limit: Duration) -> bool {
    let end = Instant::now() + limit;
    while Instant::now() < end {
        let text = fs::read_to_string(s.root.join("run/renderer-frames.jsonl")).unwrap_or_default();
        if text.contains(needle) {
            return true;
        }
        std::thread::sleep(Duration::from_millis(20));
    }
    false
}

/// One control frame on the run's socket, as the panel sends it.
fn send(s: &Session, op: &str, payload: xr_wire::json::Value) {
    use std::io::Write;
    let id = std::time::SystemTime::now()
        .duration_since(std::time::UNIX_EPOCH)
        .unwrap()
        .as_nanos() as u64;
    let frame = xr_wire::run_control::Frame {
        request_id: id,
        run_id: "r1".into(),
        session: s.session_id(),
        op: op.into(),
        payload,
    };
    let mut c = std::os::unix::net::UnixStream::connect(s.control_socket()).unwrap();
    c.write_all(frame.encode(true).as_bytes()).unwrap();
}

/// A 16x8 one-frame clip named as a 360 degree top-bottom video.
fn clip(path: &Path) {
    let mut data = b"YUV4MPEG2 W16 H8 F30:1 Ip A1:1 C420jpeg\nFRAME\n".to_vec();
    data.extend(std::iter::repeat_n(0u8, 16 * 8 * 3 / 2));
    fs::write(path, data).unwrap();
}

fn play_and_leave(s: &Session, who: Who) -> Value {
    let path = s.root.join("home/clip_360_TB.y4m");
    clip(&path);
    let payload = xr_wire::json::Value::Object(vec![(
        "path".into(),
        xr_wire::json::Value::String(path.to_str().unwrap().into()),
    )]);
    send(s, "video_open", payload);
    let opened = frames_with(s, "\\\"action\\\":\\\"open\\\"", Duration::from_secs(10));
    let playing = s.status(Duration::from_secs(5));
    let helper = s.control(who, "video_exit", &[]);
    let closed = frames_with(s, "\\\"action\\\":\\\"close\\\"", Duration::from_secs(10));
    let after = s.status(Duration::from_secs(5));
    json!({"opened": opened, "closed": closed, "playing": playing, "helper": helper, "after": after})
}

#[test]
fn a_file_opens_with_its_descriptor_and_leaves() {
    let (run, _, [python, rust]) = parity_acting("stop", Some(play_and_leave));
    assert_eq!(python, rust);
    assert_eq!(
        (&rust["opened"], &rust["closed"], &rust["helper"]),
        (&json!(true), &json!(true), &json!(0)),
        "{rust}"
    );
    let video = &rust["playing"]["video"];
    assert_eq!(
        (&video["active"], &video["title"], &video["label"]),
        (&json!(true), &json!("clip_360_TB.y4m"), &json!("360° top-bottom 3D")),
        "{rust}"
    );
    assert_eq!(rust["after"]["video"]["active"], json!(false), "{rust}");
    // The open frame came with one descriptor, the file; nothing else did.
    let with_fds: Vec<&String> = run.frames.iter().filter(|f| !f.ends_with(" []")).collect();
    assert_eq!(with_fds.len(), 1, "{:#?}", run.frames);
    assert!(
        with_fds[0].contains("\"action\":\"open\"") && with_fds[0].contains("\"type\":\"file\""),
        "{with_fds:?}"
    );
    assert_eq!(run.result["stop_reason"], "interrupted");
}
