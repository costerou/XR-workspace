//! The renderer's own control checks, for bytes the real supervisor sent: the Rust twin of
//! `spike/tests/control_wire_check.cpp` (tests/native_spike/test_control_wire.py and the fuzz and
//! corpus suites run either through `CONTROL_WIRE_CHECK`). Reads everything on stdin; one JSON line
//! per frame refused, then a last line with the totals.
//!
//! usage: control_wire_check RUN_ID SESSION < frames

use std::io::{Read as _, Write as _};
use std::process::ExitCode;

use serde_json::json;
use xr_renderer::ipc::ControlStream;

fn main() -> ExitCode {
    let args: Vec<String> = std::env::args().skip(1).collect();
    let [run_id, session] = args.as_slice() else {
        eprintln!("usage: control_wire_check RUN_ID SESSION < frames");
        return ExitCode::from(2);
    };
    let Some(mut stream) = ControlStream::new(run_id, session, false) else {
        eprintln!("control_wire_check: RUN_ID and SESSION must be tokens");
        return ExitCode::from(2);
    };
    let mut input = Vec::new();
    if std::io::stdin().read_to_end(&mut input).is_err() {
        return ExitCode::from(2);
    }
    let mut out = std::io::stdout().lock();
    stream.feed(&input, &mut |verdict| {
        if let Err(r) = verdict {
            // Keys in the C++ tool's (sorted) order.
            let line = json!({
                "code": r.refusal.code,
                "detail": r.refusal.detail,
                "id": r.request_id,
                "op": r.op.as_deref().unwrap_or(""),
            });
            let _ = writeln!(out, "{line}");
        }
    });
    let totals = json!({"accepted": stream.accepted(), "refused": stream.refused()});
    let _ = writeln!(out, "{totals}");
    ExitCode::SUCCESS
}
