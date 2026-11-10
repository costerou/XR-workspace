//! monitor_rules.rs against `rust/corpus/supervisor/monitor_rules.json` (made from monitor_rules.py by
//! `tests/native_spike/monitor_rules_corpus.py`), and the file-safety properties the corpus cannot show: the mode and
//! a symlink are kept, the size limit holds, and nothing that only names a rule writes the file.

use std::cell::{Cell, RefCell};
use std::fs;
use std::os::unix::fs::{PermissionsExt, symlink};
use std::path::Path;

use serde_json::{Value, json};

use super::*;
use crate::testdir::TestDir;

fn corpus() -> Value {
    let path = concat!(
        env!("CARGO_MANIFEST_DIR"),
        "/../../corpus/supervisor/monitor_rules.json"
    );
    serde_json::from_str(&fs::read_to_string(path).expect("corpus")).expect("json")
}

fn raw(case: &Value) -> Vec<u8> {
    if let Some(text) = case["raw"].as_str() {
        return text.as_bytes().to_vec();
    }
    let hex = case["raw_hex"].as_str().expect("raw or raw_hex");
    (0..hex.len())
        .step_by(2)
        .map(|i| u8::from_str_radix(&hex[i..i + 2], 16).expect("hex"))
        .collect()
}

fn names(v: &Value) -> Vec<String> {
    v.as_array()
        .expect("names")
        .iter()
        .map(|n| n.as_str().expect("name").to_owned())
        .collect()
}

fn rules(found: &[Rule]) -> Value {
    json!(
        found
            .iter()
            .map(|r| json!({"name": r.name, "line": r.line, "removable": r.removable}))
            .collect::<Vec<_>>()
    )
}

/// The backup's name holds the time: `<backup>` in the corpus.
fn backup_free(text: &str) -> String {
    let marker = "monitors.lua.bak.xr-rule-";
    let mut out = String::new();
    let mut rest = text;
    while let Some(at) = rest.find(marker) {
        out.push_str(&rest[..at]);
        out.push_str("<backup>");
        rest = rest[at + marker.len()..].trim_start_matches(|c: char| c.is_ascii_digit());
    }
    out + rest
}

fn scratch(dir: &TestDir, raw: &[u8]) -> std::path::PathBuf {
    for item in fs::read_dir(dir.path()).unwrap() {
        fs::remove_file(item.unwrap().path()).unwrap();
    }
    let path = dir.path().join("monitors.lua");
    fs::write(&path, raw).unwrap();
    path
}

fn listing(dir: &Path) -> Vec<String> {
    let mut names: Vec<String> = fs::read_dir(dir)
        .unwrap()
        .map(|e| backup_free(&e.unwrap().file_name().to_string_lossy()))
        .collect();
    names.sort();
    names
}

#[test]
fn every_case_equals_the_python() {
    let corpus = corpus();
    assert_eq!(corpus["limit"], json!(LIMIT));
    let dir = TestDir::new("monitor-rules");
    let mut wrong = vec![];
    let cases = corpus["cases"].as_array().expect("cases");
    assert!(cases.len() >= 30, "{} cases", cases.len());
    for case in cases {
        let name = case["name"].as_str().expect("name");
        let raw = raw(case);
        let mut check = |what: String, rust: Value, python: &Value| {
            if rust != *python {
                wrong.push(format!(
                    "{name}: {what}\n  python: {python}\n  rust:   {rust}"
                ));
            }
        };
        if let Ok(text) = std::str::from_utf8(&raw) {
            check("find".into(), rules(&find(text, None)), &case["find"]);
            let xr1 = ["XR-1".to_owned()];
            check(
                "find XR-1".into(),
                rules(&find(text, Some(&xr1))),
                &case["find_xr1"],
            );
        }
        for entry in case["remove"].as_array().expect("remove") {
            let wanted = names(&entry["names"]);
            let path = scratch(&dir, &raw);
            let rust = match remove(&path, &wanted) {
                Ok(None) => json!({"names": wanted, "result": null}),
                Ok(Some(backup)) => {
                    assert_eq!(
                        fs::read(&backup).unwrap(),
                        raw,
                        "{name}: the backup is the old file"
                    );
                    json!({"names": wanted, "result": fs::read_to_string(&path).unwrap()})
                }
                Err(e) => json!({"names": wanted, "error": e.to_string()}),
            };
            check(format!("remove {wanted:?}"), rust, entry);
        }
        for entry in case["clear"].as_array().expect("clear") {
            let wanted = names(&entry["names"]);
            let reload_error = entry["reload_error"].as_str();
            let path = scratch(&dir, &raw);
            let notes = RefCell::new(Vec::new());
            let reloads = Cell::new(0);
            let notify = |n: &str| notes.borrow_mut().push(backup_free(n));
            let reasons = clear(
                Some(&path),
                &wanted,
                || {
                    reloads.set(reloads.get() + 1);
                    reload_error.map_or(Ok(()), Err)
                },
                Some(&notify),
            );
            let rust = json!({
                "names": wanted,
                "reload_error": reload_error,
                "reasons": reasons,
                "notes": notes.into_inner(),
                "reloads": reloads.get(),
                "after": String::from_utf8_lossy(&fs::read(&path).unwrap()),
                "files": listing(dir.path()),
            });
            check(format!("clear {wanted:?} {reload_error:?}"), rust, entry);
        }
        let path = scratch(&dir, &raw);
        for entry in case["named"].as_array().expect("named") {
            let wanted = names(&entry["names"]);
            let rust = json!({"names": wanted, "reasons": named(Some(&path), &wanted)});
            check(format!("named {wanted:?}"), rust, entry);
        }
        check(
            "refuse_start".into(),
            json!(refuse_start(Some(&path)).err()),
            &case["refuse_start"],
        );
        assert_eq!(
            fs::read(&path).unwrap(),
            raw,
            "{name}: named or refuse_start wrote the file"
        );
        assert_eq!(listing(dir.path()), ["monitors.lua"], "{name}");
    }
    for entry in corpus["desktops"].as_array().expect("desktops") {
        let name = entry["name"].as_str().expect("name");
        let rust = match desktop(name) {
            Ok(n) => json!({"name": name, "ok": n == name}),
            Err(e) => json!({"name": name, "error": e.to_string()}),
        };
        if rust != *entry {
            wrong.push(format!("desktop {name:?}: python {entry}, rust {rust}"));
        }
    }
    assert!(
        wrong.is_empty(),
        "{} differences:\n{}",
        wrong.len(),
        wrong.join("\n")
    );
}

const OMARCHY_LINE: &str =
    "hl.monitor({ output = \"XR-1\", mode = \"preferred\", position = \"auto\", scale = 1.25 })\n";

#[test]
fn removal_goes_through_a_symlink_and_keeps_the_mode() {
    let dir = TestDir::new("monitor-rules-link");
    let dotfiles = dir.path().join("dotfiles");
    fs::create_dir(&dotfiles).unwrap();
    let target = dotfiles.join("monitors.lua");
    let before = format!("a = 1\n{OMARCHY_LINE}b = 2\n");
    fs::write(&target, &before).unwrap();
    fs::set_permissions(&target, fs::Permissions::from_mode(0o640)).unwrap();
    let link = dir.path().join("monitors.lua");
    symlink(&target, &link).unwrap();

    let backup = remove(&link, &["XR-1".to_owned()])
        .unwrap()
        .expect("removed");
    assert!(
        fs::symlink_metadata(&link)
            .unwrap()
            .file_type()
            .is_symlink(),
        "the link stays a link"
    );
    assert_eq!(fs::read_link(&link).unwrap(), target);
    assert_eq!(fs::read_to_string(&target).unwrap(), "a = 1\nb = 2\n");
    assert_eq!(
        backup.parent().unwrap(),
        dotfiles,
        "the copy is beside the real file"
    );
    assert_eq!(fs::read_to_string(&backup).unwrap(), before);
    for path in [&target, &backup] {
        assert_eq!(
            fs::metadata(path).unwrap().permissions().mode() & 0o7777,
            0o640,
            "{path:?}"
        );
    }
    let mut left: Vec<_> = fs::read_dir(&dotfiles)
        .unwrap()
        .map(|e| e.unwrap().file_name())
        .collect();
    left.sort();
    assert_eq!(left.len(), 2, "no temporary file stays: {left:?}");
}

#[test]
fn a_config_over_the_limit_is_never_edited() {
    let dir = TestDir::new("monitor-rules-limit");
    let path = dir.path().join("monitors.lua");
    let at_limit = format!(
        "{}\n{}",
        "-".repeat(LIMIT as usize - OMARCHY_LINE.len() - 1),
        OMARCHY_LINE
    );
    assert_eq!(at_limit.len() as u64, LIMIT);
    fs::write(&path, &at_limit).unwrap();
    assert_eq!(
        leftovers(None, &path).unwrap().len(),
        1,
        "a file of exactly LIMIT bytes is read"
    );
    let over = format!("-{at_limit}");
    fs::write(&path, &over).unwrap();
    let xr1 = ["XR-1".to_owned()];
    assert_eq!(
        remove(&path, &xr1).unwrap_err().to_string(),
        "monitor config too large"
    );
    let reloads = Cell::new(0);
    let reasons = clear(
        Some(&path),
        &xr1,
        || {
            reloads.set(reloads.get() + 1);
            Ok::<(), String>(())
        },
        None,
    );
    assert_eq!(
        reasons,
        ["could not take the rule for an XR desktop out of monitors.lua: monitor config too large"]
    );
    assert_eq!(reloads.get(), 0);
    assert_eq!(
        refuse_start(Some(&path)),
        Ok(()),
        "an unreadable config proves nothing"
    );
    assert!(named(Some(&path), &xr1).is_empty());
    assert_eq!(fs::read_to_string(&path).unwrap(), over);
}

#[test]
fn no_path_or_no_desktops_touches_nothing() {
    let dir = TestDir::new("monitor-rules-none");
    let path = dir.path().join("monitors.lua");
    fs::write(&path, OMARCHY_LINE).unwrap();
    let never = || -> Result<(), String> { panic!("no reload") };
    assert!(clear(None, &["XR-1".to_owned()], never, None).is_empty());
    assert!(clear(Some(&path), &[], never, None).is_empty());
    assert!(named(None, &["XR-1".to_owned()]).is_empty());
    assert_eq!(refuse_start(None), Ok(()));
    let missing = dir.path().join("absent.lua");
    assert!(clear(Some(&missing), &["XR-1".to_owned()], never, None).is_empty());
    assert_eq!(refuse_start(Some(&missing)), Ok(()));
    assert_eq!(fs::read_to_string(&path).unwrap(), OMARCHY_LINE);
}

#[test]
fn the_config_path_follows_the_xdg_rules() {
    use std::ffi::OsStr;
    let p = |x: Option<&str>, h: Option<&str>| config_path(x.map(OsStr::new), h.map(OsStr::new));
    assert_eq!(
        p(Some("/c"), Some("/h")).unwrap(),
        Path::new("/c/hypr/monitors.lua")
    );
    assert_eq!(
        p(Some(""), Some("/h")).unwrap(),
        Path::new("/h/.config/hypr/monitors.lua")
    );
    assert_eq!(
        p(None, Some("/h")).unwrap(),
        Path::new("/h/.config/hypr/monitors.lua")
    );
    assert_eq!(p(None, None), None);
}
