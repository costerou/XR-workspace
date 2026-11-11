//! `view_controls::immersive`'s parity cases on the scripted compositor (rust/crates/xr-workspace/VIEW_CONTROLS.md).
//!
//! - A depth engine installed beside the pinned renderer (a fake `xr-depth-engine`, written by a child process): both
//!   runs make the depth memory and hand it to the renderer (`--depth-fd`), and neither starts the engine, since no
//!   desktop is 3D. The 3D ops reach the feature only through the view (`view.rs`, still a stub on both sides), so
//!   the `immersive` frames and the engine's stdin are level 1's (tests/immersive_corpus.rs).
//! - A run with the consent's 3D scope whose renderer ends as the glasses go side by side (R1 held, `reply_immersive`):
//!   `ButtonSbs.follow_by_hand` sees the wide mode, so the exit is expected (`keep_renderer`'s `switching`). The run
//!   holds without settling, puts the wide mode back (`_restore_output`), and starts the next renderer.

use std::fs;
use std::time::Duration;

use serde_json::{Value, json};

use super::{Session, Start, Who, parity_run, wait_for};

/// A depth engine that would say what it was asked, if it ever ran.
const ENGINE: &str = "#!/usr/bin/python3\nimport json, os, sys\n\
with open(os.path.join(os.environ['XDG_RUNTIME_DIR'], 'engine.jsonl'), 'a') as f: f.write(json.dumps(sys.argv[1:]) + '\\n')\n\
print('ready', flush=True)\nfor line in sys.stdin: pass\n";

#[test]
fn an_installed_depth_engine_gives_every_renderer_the_depth_memory() {
    let start = Start {
        beside_renderer: vec![("xr-depth-engine", ENGINE.to_owned())],
        ..Start::default()
    };
    let (run, rs, _) = parity_run("stop", &start, None);
    assert_eq!(run.renderers.len(), 1);
    assert!(
        run.renderers[0].contains(" --depth-fd FD"),
        "{:?}",
        run.renderers
    );
    assert!(
        !rs.root.join("run/engine.jsonl").exists(),
        "nothing 3D, no engine"
    );
    assert_eq!(run.result["stop_reason"], "interrupted");
}

#[test]
fn no_engine_no_depth_memory() {
    let (run, _) = super::parity("stop");
    assert!(
        !run.renderers[0].contains("--depth-fd"),
        "{:?}",
        run.renderers
    );
}

/// The first renderer ends as the glasses go wide; the next one comes up; a second later (so the next look at the
/// glasses' mode is due on both clocks), the case's Stop.
fn through_the_switch(s: &Session, _: Who) -> Value {
    let run = s.root.join("run");
    assert!(wait_for(
        &run.join("fake-glasses-wide"),
        Duration::from_secs(10)
    ));
    fs::remove_file(run.join("renderer-started")).unwrap();
    let back = wait_for(&run.join("renderer-started"), Duration::from_secs(20));
    std::thread::sleep(Duration::from_millis(1200));
    json!({"back": back})
}

#[test]
fn a_renderer_ending_as_the_glasses_switch_mode_is_held_as_expected() {
    let start = Start {
        consent: json!({"mode_switch": {"width": 1920, "height": 1200, "refresh_hz": 120},
                        "sbs_mode": {"width": 3840, "height": 1200, "refresh_hz": [90, 60]}}),
        ..Start::default()
    };
    let (run, _, [python, rust]) = parity_run("switch", &start, Some(through_the_switch));
    assert_eq!(python, rust);
    assert_eq!(rust["back"], true);
    assert_eq!(run.renderers.len(), 2, "a second renderer after the switch");
    assert_eq!(
        run.result["usb_holds"],
        json!([{"seconds": true, "resumed": true}])
    );
    assert_eq!(run.result["stop_reason"], "interrupted");
    // The wide mode put back for the next renderer (`_restore_output`), not the session mode.
    assert!(
        run.trace
            .iter()
            .any(|r| r.contains("mode = \"3840x1200@90\"")),
        "{:#?}",
        run.trace
    );
}
