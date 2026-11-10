//! The Rust side of the cross-language fuzz (`tests/native_spike/wire_fuzz.py`): reads frames on
//! stdin, each ending in a newline, and prints one JSON line per frame with the three verdicts the
//! harness compares against Python and C++:
//!
//! * `json`: `xr_wire::json::parse` on the frame without its newline (`"accepted"` or `"refused"`),
//!   or `"skipped"` past the 16 KiB the strict-JSON layer is held to.
//! * `run`: a run's control socket, one [`Queue`] for all frames (each taken frame popped at once):
//!   `"accepted"`, `"json"` when the JSON layer refused it, or the error text.
//! * `renderer`: one [`Stream`] fed every byte at once, as `control_wire_check --each` is:
//!   `{"accepted": request_id}` or the refusal's `code`, `detail`, `op` and `id`.
//!
//! usage: wire_fuzz_verdicts RUN_ID SESSION < frames

use std::io::{Read, Write};

use serde_json::json;
use xr_wire::control::Stream;
use xr_wire::json::{Limits, MAX_DEPTH, parse};
use xr_wire::run_control::{MAX_FRAME, Queue};

const JSON_LIMITS: Limits = Limits {
    max_bytes: MAX_FRAME,
    max_depth: MAX_DEPTH,
};

fn main() {
    let args: Vec<String> = std::env::args().collect();
    if args.len() != 3 {
        eprintln!("usage: wire_fuzz_verdicts RUN_ID SESSION < frames");
        std::process::exit(2);
    }
    let mut input = Vec::new();
    std::io::stdin().read_to_end(&mut input).unwrap();
    if !input.is_empty() && input.last() != Some(&b'\n') {
        eprintln!("the last frame has no newline");
        std::process::exit(2);
    }
    let frames: Vec<&[u8]> = input.split_inclusive(|&b| b == b'\n').collect();

    let mut stream = Stream::new(&args[1], &args[2]).expect("identity tokens");
    let renderer = stream.feed(&input);
    if renderer.len() != frames.len() {
        eprintln!("{} frames but {} renderer verdicts", frames.len(), renderer.len());
        std::process::exit(1);
    }
    let mut queue = Queue::new(&args[1], &args[2]).expect("identity tokens");

    let mut out = std::io::BufWriter::new(std::io::stdout().lock());
    for (frame, verdict) in frames.iter().zip(&renderer) {
        let body = &frame[..frame.len() - 1];
        let json_ok = parse(body, JSON_LIMITS).is_ok();
        let json = if body.len() > JSON_LIMITS.max_bytes {
            "skipped"
        } else if json_ok {
            "accepted"
        } else {
            "refused"
        };
        let run = match queue.push(frame) {
            Ok(_) => {
                queue.pop();
                "accepted".to_string()
            }
            Err(e) if e.0 != "invalid frame" && !json_ok => "json".to_string(),
            Err(e) => e.0,
        };
        let renderer = match verdict {
            Ok(message) => json!({"accepted": message.request_id}),
            Err(r) => json!({
                "code": r.refusal.code,
                "detail": r.refusal.detail,
                "id": r.request_id,
                "op": r.op.as_deref().unwrap_or(""),
            }),
        };
        let line = json!({"json": json, "run": run, "renderer": renderer});
        writeln!(out, "{line}").unwrap();
    }
}
