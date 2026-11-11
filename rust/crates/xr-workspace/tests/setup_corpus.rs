//! rust/corpus/setup/discard.json (tests/native_spike/setup_corpus.py), replayed against `view_controls::setup`: for
//! each saved `profiles.json`, the run's desktops and the broker's refusal, whether the reset is asked at all, what it
//! logs, and the file it leaves, byte for byte.

use std::fs;
use std::os::unix::fs::PermissionsExt;
use std::path::Path;

use serde_json::Value;
use xr_workspace::geometry::Desktop;
use xr_workspace::managed::Error;
use xr_workspace::view_controls::setup;

/// The log with the folder and the temporary file's name as the corpus has them.
fn normal(log: &str, dir: &Path) -> String {
    let text = log.replace(&dir.display().to_string(), "DIR");
    let mut out = String::new();
    let mut rest = text.as_str();
    while let Some(i) = rest.find(".profiles.") {
        out += &rest[..i];
        let tail = &rest[i + ".profiles.".len()..];
        let end = tail
            .find(|c: char| c == '.' || c == '/' || c == '\'')
            .unwrap_or(tail.len());
        if tail[end..].starts_with(".tmp") && end > 0 {
            out += ".profiles.TMP.tmp";
            rest = &tail[end + 4..];
        } else {
            out += ".profiles.";
            rest = tail;
        }
    }
    out + rest
}

#[test]
fn every_case_matches_the_python() {
    let corpus: Value = serde_json::from_str(
        &fs::read_to_string(
            Path::new(env!("CARGO_MANIFEST_DIR")).join("../../corpus/setup/discard.json"),
        )
        .unwrap(),
    )
    .unwrap();
    let cases = corpus["cases"].as_array().unwrap();
    assert!(cases.len() >= 19);
    for (i, case) in cases.iter().enumerate() {
        let name = case["name"].as_str().unwrap();
        let dir = std::env::temp_dir().join(format!("xr-setup-corpus-{}-{i}", std::process::id()));
        let _ = fs::remove_dir_all(&dir);
        fs::create_dir_all(&dir).unwrap();
        let path = dir.join("profiles.json");
        if let Some(before) = case["before"].as_str() {
            fs::write(&path, before).unwrap();
        }
        let desktops: Vec<Desktop> = case["desktops"]
            .as_array()
            .unwrap()
            .iter()
            .map(|d| Desktop::from_value(d).unwrap())
            .collect();
        let asked = setup::is_saved_setup(&desktops);
        assert_eq!(asked, case["asked"], "{name}: asked");
        let mut log = String::new();
        if case["read_only"] == true {
            fs::set_permissions(&dir, fs::Permissions::from_mode(0o500)).unwrap();
        }
        if asked {
            let error = Error(case["error"].as_str().unwrap().into());
            setup::setup_failed(
                &path,
                case["model"].as_str().unwrap(),
                &desktops,
                &error,
                &mut |line| log += &format!("xr-workspace: {line}\n"),
            );
        }
        fs::set_permissions(&dir, fs::Permissions::from_mode(0o700)).unwrap();
        assert_eq!(
            normal(&log, &dir),
            case["log"].as_str().unwrap(),
            "{name}: log"
        );
        let after = fs::read_to_string(&path).ok();
        assert_eq!(
            after.as_deref(),
            case["after"].as_str(),
            "{name}: profiles.json"
        );
        if after.is_some() && case["before"] != case["after"] {
            assert_eq!(
                fs::metadata(&path).unwrap().permissions().mode() & 0o777,
                0o600,
                "{name}: mode"
            );
        }
        let left: Vec<_> = fs::read_dir(&dir)
            .unwrap()
            .map(|e| e.unwrap().file_name())
            .collect();
        assert!(
            left.len() <= 1,
            "{name}: a temporary file was left: {left:?}"
        );
        fs::remove_dir_all(&dir).unwrap();
    }
}
