//! The files a run and the installer leave on disk, each in the exact bytes its Python writer
//! produces. The plan freezes these formats (docs/rust-rebuild-plan.md, section 5), so either
//! implementation can read, recover and rewrite what the other wrote.
//!
//! | File | Python writer | `json.dumps` style | Read as |
//! |---|---|---|---|
//! | `journal.json` | `journal.Journal._save` | default, file order | private, 1 MiB, [`Journal`] |
//! | `<run_id>.json` (consent) | `panel_consent.write_consent` | compact, sorted | private, 16 KiB |
//! | `<run_id>.progress.json` | `runner._write_result` | sorted | private, 16 KiB |
//! | `<run_id>.result.json` | `runner._write_result` | sorted | private, 16 KiB |
//! | `<run_id>.recovery.json` | `__main__.report_recovery` (`_write_result`) | sorted | private, 16 KiB |
//! | `installed.json` | `install.install` | `indent=2` | 16 KiB |
//! | `quiet-driver.pin.json` | `install.install` | `indent=2` | 16 KiB |
//! | `pro2-bundle.pin.json` | `pro2_route.write_pin` | default | private, 16 KiB |
//!
//! "Private" means owned by us and mode 0600, as `read_private_json` requires; the installer's own
//! two pins are read by Python without that check, so they are read here without it too. Every
//! file is written mode 0600 by [`PrivateDir`].
//!
//! The journal is checked as `Journal._load` checks it. The consent, progress, result and
//! recovery files are checked only for their shape (an object naming its run): their full rules
//! belong to the controller and supervisor ports that act on them.

use std::collections::BTreeSet;
use std::io;

use crate::files::PrivateDir;
use crate::json::{self, Error, Style, Value};

/// Largest journal `Journal._load` reads and `_save` writes.
pub const JOURNAL_LIMIT: usize = 1 << 20;
/// `read_private_json`'s default limit.
pub const FILE_LIMIT: usize = 16384;

/// One of the on-disk formats.
#[derive(Clone, Copy, Debug, PartialEq, Eq)]
pub enum Format {
    Journal,
    Consent,
    Progress,
    Result,
    Recovery,
    Installed,
    ShimPin,
    Pro2Pin,
}

impl Format {
    pub fn style(self) -> Style {
        match self {
            Format::Journal | Format::Pro2Pin => Style::DEFAULT,
            Format::Consent => Style::COMPACT_SORTED,
            Format::Progress | Format::Result | Format::Recovery => Style::SORTED,
            Format::Installed | Format::ShimPin => Style::INDENT2,
        }
    }

    pub fn limit(self) -> usize {
        if self == Format::Journal { JOURNAL_LIMIT } else { FILE_LIMIT }
    }

    /// Whether its reader requires an owned mode 0600 file.
    pub fn private(self) -> bool {
        !matches!(self, Format::Installed | Format::ShimPin)
    }

    /// The bytes the Python writer produces for `value`, after checking its shape.
    pub fn encode(self, value: &Value) -> Result<Vec<u8>, Error> {
        self.check(value)?;
        let raw = json::encode(value, self.style()).into_bytes();
        if raw.len() > self.limit() {
            return Err(Error("file too large".into()));
        }
        Ok(raw)
    }

    /// `raw` parsed strictly, after checking its size and shape.
    pub fn decode(self, raw: &[u8]) -> Result<Value, Error> {
        if raw.len() > self.limit() {
            return Err(Error("file too large".into()));
        }
        let value = json::parse_strict(raw)?;
        self.check(&value)?;
        Ok(value)
    }

    fn check(self, value: &Value) -> Result<(), Error> {
        let keys = value.keys().ok_or_else(|| Error("not an object".into()))?;
        let run_id = value.get("run_id");
        match self {
            Format::Journal => Journal::from_value(value, None).map(|_| ()),
            Format::Consent => {
                if value.get("schema").and_then(Value::as_int) != Some(1) {
                    return Err(Error("invalid consent schema".into()));
                }
                token(run_id)
            }
            // `invalid.result.json` names no run: its `run_id` is null.
            Format::Result if run_id == Some(&Value::Null) => Ok(()),
            Format::Progress | Format::Result | Format::Recovery => token(run_id),
            Format::Installed => {
                let ok: [BTreeSet<&str>; 2] =
                    [["plugin", "renderer"].into(), ["plugin", "renderer", "luma_driver"].into()];
                if !ok.contains(&keys) {
                    return Err(Error("malformed installed.json".into()));
                }
                keys.iter().try_for_each(|k| artifact(value.get(k)))
            }
            Format::ShimPin => artifact(Some(value)),
            Format::Pro2Pin => {
                let ok = keys == ["schema", "model", "bundle", "manifest_sha256"].into()
                    && value.get("schema").and_then(Value::as_int) == Some(1)
                    && value.get("model").and_then(Value::as_str) == Some("pro2")
                    && value.get("bundle").and_then(Value::as_str).is_some()
                    && value.get("manifest_sha256").and_then(Value::as_str).is_some_and(|s| hex(s, 64));
                if ok { Ok(()) } else { Err(Error("Pro 2 pin file is invalid".into())) }
            }
        }
    }
}

/// `install._check_entry`'s shape: `{"path": str, "sha256": 64 lowercase hex}`.
fn artifact(value: Option<&Value>) -> Result<(), Error> {
    let ok = value.is_some_and(|v| {
        v.keys() == Some(["path", "sha256"].into())
            && v.get("path").and_then(Value::as_str).is_some_and(|p| p.starts_with('/'))
            && v.get("sha256").and_then(Value::as_str).is_some_and(|s| hex(s, 64))
    });
    if ok { Ok(()) } else { Err(Error("malformed pin".into())) }
}

fn hex(s: &str, len: usize) -> bool {
    s.len() == len && s.bytes().all(|b| matches!(b, b'0'..=b'9' | b'a'..=b'f'))
}

/// `ipc.token`: `[A-Za-z0-9][A-Za-z0-9_.:-]{0,127}`.
pub fn is_token(s: &str) -> bool {
    let b = s.as_bytes();
    !b.is_empty()
        && b.len() <= 128
        && b[0].is_ascii_alphanumeric()
        && b[1..].iter().all(|c| c.is_ascii_alphanumeric() || matches!(c, b'_' | b'.' | b':' | b'-'))
}

fn token(value: Option<&Value>) -> Result<(), Error> {
    if value.and_then(Value::as_str).is_some_and(is_token) { Ok(()) } else { Err(Error("invalid identifier".into())) }
}

/// `str(uuid.UUID(s)) == s`: lowercase, hyphenated 8-4-4-4-12.
fn is_uuid(s: &str) -> bool {
    let b = s.as_bytes();
    b.len() == 36
        && b.iter().enumerate().all(|(i, c)| match i {
            8 | 13 | 18 | 23 => *c == b'-',
            _ => matches!(c, b'0'..=b'9' | b'a'..=b'f'),
        })
}

impl PrivateDir {
    /// The file `name` in `format`, read with that format's rules.
    pub fn read_json(&self, name: &str, format: Format) -> io::Result<Value> {
        let raw = self.read(name, format.limit(), format.private())?;
        format.decode(&raw).map_err(|e| io::Error::new(io::ErrorKind::InvalidData, e))
    }

    /// Atomically writes `value` to `name` in `format`, mode 0600. A consent is created, never
    /// replaced, as `write_consent` creates it.
    pub fn write_json(&self, name: &str, format: Format, value: &Value) -> io::Result<()> {
        let raw = format.encode(value).map_err(|e| io::Error::new(io::ErrorKind::InvalidData, e))?;
        if format == Format::Consent { self.create(name, &raw) } else { self.replace(name, &raw) }
    }
}

/// A journal record's state.
#[derive(Clone, Copy, Debug, PartialEq, Eq)]
pub enum State {
    Pending,
    Confirmed,
    Done,
}

impl State {
    pub fn as_str(self) -> &'static str {
        match self {
            State::Pending => "pending",
            State::Confirmed => "confirmed",
            State::Done => "done",
        }
    }
}

/// One journal record: the change, written before it is made, and how far it got.
#[derive(Clone, Debug, PartialEq)]
pub struct Record {
    pub id: String,
    /// A checked operation, kept as written (its key order is the writer's).
    pub operation: Value,
    pub state: State,
}

/// The write-ahead journal, `journal.json`.
#[derive(Clone, Debug, PartialEq)]
pub struct Journal {
    pub run_id: String,
    pub session: String,
    pub records: Vec<Record>,
    /// The ownership broker's context, kept as written.
    pub broker: Option<Value>,
}

impl Journal {
    /// `Journal._load`: the journal in `raw`, which must belong to `run_id` and `session`.
    pub fn parse(raw: &[u8], run_id: &str, session: &str) -> Result<Journal, Error> {
        if raw.len() > JOURNAL_LIMIT {
            return Err(Error("journal too large".into()));
        }
        Journal::from_value(&json::parse_strict(raw)?, Some((run_id, session)))
    }

    /// The bytes `Journal._save` writes.
    pub fn to_bytes(&self) -> Result<Vec<u8>, Error> {
        let value = self.to_value();
        Journal::from_value(&value, None)?;
        let raw = json::encode(&value, Style::DEFAULT).into_bytes();
        if raw.len() > JOURNAL_LIMIT {
            return Err(Error("journal too large".into()));
        }
        Ok(raw)
    }

    fn to_value(&self) -> Value {
        let records = self
            .records
            .iter()
            .map(|r| {
                Value::Object(vec![
                    ("id".into(), Value::String(r.id.clone())),
                    ("operation".into(), r.operation.clone()),
                    ("state".into(), Value::String(r.state.as_str().into())),
                ])
            })
            .collect();
        let mut entries = vec![
            ("schema".into(), Value::Number(1.into())),
            ("run_id".into(), Value::String(self.run_id.clone())),
            ("session".into(), Value::String(self.session.clone())),
            ("operations".into(), Value::Array(records)),
        ];
        if let Some(broker) = &self.broker {
            entries.push(("broker".into(), broker.clone()));
        }
        Value::Object(entries)
    }

    fn from_value(data: &Value, identity: Option<(&str, &str)>) -> Result<Journal, Error> {
        let fail = |m: &str| Err(Error(m.into()));
        let keys = data.keys().unwrap_or_default();
        let fields: BTreeSet<&str> = ["schema", "run_id", "session", "operations"].into();
        let with_broker: BTreeSet<&str> = ["schema", "run_id", "session", "operations", "broker"].into();
        if !matches!(data, Value::Object(_)) || (keys != fields && keys != with_broker) {
            return fail("invalid journal");
        }
        let (run_id, session) = (data.get("run_id").and_then(Value::as_str), data.get("session").and_then(Value::as_str));
        let (Some(run_id), Some(session)) = (run_id, session) else { return fail("wrong journal identity") };
        // Journal.open checks both with token() before it ever reads.
        if data.get("schema").and_then(Value::as_int) != Some(1) || !is_token(run_id) || !is_token(session) {
            return fail("wrong journal identity");
        }
        if identity.is_some_and(|(r, s)| (r, s) != (run_id, session)) {
            return fail("wrong journal identity");
        }
        let Some(Value::Array(operations)) = data.get("operations") else { return fail("invalid records") };
        let mut ids = BTreeSet::new();
        let mut records = Vec::new();
        for record in operations {
            if record.keys() != Some(["id", "operation", "state"].into()) {
                return fail("invalid record");
            }
            let id = record.get("id").and_then(Value::as_str).filter(|id| is_uuid(id));
            let Some(id) = id.filter(|id| ids.insert(*id)) else { return fail("invalid record id") };
            let state = match record.get("state").and_then(Value::as_str) {
                Some("pending") => State::Pending,
                Some("confirmed") => State::Confirmed,
                Some("done") => State::Done,
                _ => return fail("invalid state"),
            };
            let operation = record.get("operation").expect("keys checked");
            validate_operation(operation)?;
            records.push(Record { id: id.into(), operation: operation.clone(), state });
        }
        let broker = data.get("broker").map(|b| validate_broker(b).map(|()| b.clone())).transpose()?;
        Ok(Journal { run_id: run_id.into(), session: session.into(), records, broker })
    }
}

/// `journal.validate_operation`.
fn validate_operation(op: &Value) -> Result<(), Error> {
    let fail = |m: &str| Err(Error(m.into()));
    let int = |k: &str| op.get(k).and_then(Value::as_int);
    let str_token = |k: &str| op.get(k).and_then(Value::as_str).is_some_and(is_token);
    let kind = op.get("kind").and_then(Value::as_str);
    let fields: BTreeSet<&str> = match kind {
        Some("setting") => ["kind", "key", "before", "applied"].into(),
        Some("workspace") => ["kind", "workspace", "before", "applied"].into(),
        Some("output") => ["kind", "id", "name", "index", "before"].into(),
        _ => return fail("unsupported operation"),
    };
    if op.keys() != Some(fields) {
        return fail("unsupported operation");
    }
    match kind {
        Some("setting") => {
            if op.get("key").and_then(Value::as_str) != Some("input:follow_mouse") {
                return fail("unsupported setting");
            }
            if !["before", "applied"].iter().all(|k| int(k).is_some_and(|v| (0..=3).contains(&v))) {
                return fail("invalid setting value");
            }
        }
        Some("workspace") => {
            if !int("workspace").is_some_and(|w| 0 < w && w <= 2147483647) {
                return fail("invalid workspace");
            }
            if !str_token("before") || !str_token("applied") {
                return fail("invalid identifier");
            }
        }
        _ => {
            if !op.get("id").and_then(Value::as_str).is_some_and(is_uuid) || op.get("before") != Some(&Value::Null) {
                return fail("invalid output ownership");
            }
            let Some(index) = int("index").filter(|i| (0..=2147483647).contains(i)) else {
                return fail("invalid output index");
            };
            if !str_token("name") {
                return fail("invalid identifier");
            }
            if op.get("name").and_then(Value::as_str) != Some(format!("XR-{}", index + 1).as_str()) {
                return fail("output name does not match journal index");
            }
        }
    }
    Ok(())
}

/// `hyprland.INSTANCE`: `[0-9a-f]{40}_[0-9]+_[0-9]+`.
fn is_instance(s: &str) -> bool {
    let mut parts = s.split('_');
    let ok = |p: Option<&str>, f: fn(&str) -> bool| p.is_some_and(f);
    ok(parts.next(), |p| hex(p, 40))
        && ok(parts.next(), |p| !p.is_empty() && p.bytes().all(|b| b.is_ascii_digit()))
        && ok(parts.next(), |p| !p.is_empty() && p.bytes().all(|b| b.is_ascii_digit()))
        && parts.next().is_none()
}

/// `journal.validate_broker`: the plain context, or the isolated VM's with its pair proof.
fn validate_broker(context: &Value) -> Result<(), Error> {
    let fail = |m: &str| Err(Error(m.into()));
    let s = |k: &str| context.get(k).and_then(Value::as_str);
    let keys = context.keys();
    let isolated: BTreeSet<&str> =
        ["mode", "instance", "output", "recovery_output", "epoch", "fallback", "target", "boot_id", "runtime", "proof"].into();
    let plain: BTreeSet<&str> = ["instance", "output", "recovery_output", "epoch", "fallback"].into();
    if keys.as_ref() == Some(&isolated) {
        validate_isolated(context)?;
    } else if keys.as_ref() == Some(&plain) {
        if !plain.iter().all(|k| s(k).is_some_and(is_token)) {
            return fail("invalid identifier");
        }
    } else {
        return fail("invalid broker context");
    }
    if !s("instance").is_some_and(is_instance) || s("output") == s("recovery_output") {
        return fail("invalid broker scope");
    }
    Ok(())
}

fn validate_isolated(context: &Value) -> Result<(), Error> {
    let fail = |m: &str| Err(Error(m.into()));
    let s = |k: &str| context.get(k).and_then(Value::as_str);
    if s("mode") != Some("isolated-vm") {
        return fail("invalid broker mode");
    }
    if !["instance", "output", "recovery_output", "epoch"].iter().all(|k| s(k).is_some_and(is_token)) {
        return fail("invalid identifier");
    }
    if !["fallback", "target"].iter().all(|k| s(k).is_some_and(|v| hex(v, 48))) {
        return fail("invalid pair handle");
    }
    if s("target") == s("fallback") {
        return fail("pair handles alias");
    }
    if !s("boot_id").is_some_and(is_uuid) {
        return fail("invalid boot identity");
    }
    if !s("runtime").is_some_and(|r| r.starts_with('/')) {
        return fail("invalid runtime");
    }
    let proof = context.get("proof").filter(|p| p.keys() == Some(["target", "fallback"].into()));
    let Some(proof) = proof else { return fail("invalid proof") };
    let mut sides = Vec::new();
    for (key, name) in [("target", s("output")), ("fallback", s("recovery_output"))] {
        let side = proof.get(key).expect("keys checked");
        let device = side.get("device").and_then(Value::as_str);
        let connector = side.get("connector").and_then(Value::as_int);
        let ok = side.keys() == Some(["name", "device", "connector"].into())
            && side.get("name").and_then(Value::as_str) == name
            && device.is_some_and(is_device)
            && connector.is_some_and(|c| 0 < c && c <= 4294967295);
        if !ok {
            return fail("invalid proof");
        }
        sides.push((device, connector));
    }
    if sides[0] == sides[1] {
        return fail("proof outputs alias");
    }
    Ok(())
}

/// `[1-9][0-9]{0,9}:[0-9]{1,10}`.
fn is_device(s: &str) -> bool {
    let digits = |p: &str| p.bytes().all(|b| b.is_ascii_digit());
    s.split_once(':').is_some_and(|(major, minor)| {
        (1..=10).contains(&major.len())
            && digits(major)
            && !major.starts_with('0')
            && (1..=10).contains(&minor.len())
            && digits(minor)
    })
}

#[cfg(test)]
mod tests {
    use super::*;
    use crate::files::tests::{Scratch, names};
    use std::fs;
    use std::os::unix::fs::MetadataExt;
    use std::path::{Path, PathBuf};

    /// The phase 0 corpus, written by the shipped Python writers (tests/native_spike/state_file_corpus.py).
    fn corpus() -> PathBuf {
        Path::new(env!("CARGO_MANIFEST_DIR")).join("../../../tests/native_spike/fixtures/state_files")
    }

    fn format_of(name: &str) -> Format {
        match name {
            "journal.json" | "journal.before.json" => Format::Journal,
            "installed.json" => Format::Installed,
            "quiet-driver.pin.json" => Format::ShimPin,
            "pro2-bundle.pin.json" => Format::Pro2Pin,
            _ if name.ends_with(".progress.json") => Format::Progress,
            _ if name.ends_with(".result.json") => Format::Result,
            _ if name.ends_with(".recovery.json") => Format::Recovery,
            _ => Format::Consent,
        }
    }

    /// Every fixture file, by folder: runs/<scenario>, consents, pins/<install>.
    fn fixtures() -> Vec<(PathBuf, String)> {
        let root = corpus();
        let mut folders = vec![root.join("consents")];
        for group in ["runs", "pins"] {
            folders.extend(fs::read_dir(root.join(group)).unwrap().map(|e| e.unwrap().path()));
        }
        let mut files = Vec::new();
        for folder in folders {
            for name in names(&folder) {
                files.push((folder.clone(), name));
            }
        }
        files
    }

    #[test]
    fn every_fixture_reads_and_rewrites_byte_identically() {
        let files = fixtures();
        let mut counts = std::collections::BTreeMap::new();
        let scratch = Scratch::new("corpus");
        let dir = scratch.dir();
        for (folder, name) in &files {
            let path = folder.join(name);
            let raw = fs::read(&path).unwrap();
            let format = format_of(name);
            *counts.entry(format!("{format:?}")).or_insert(0) += 1;
            let value = format.decode(&raw).unwrap_or_else(|e| panic!("{}: {e}", path.display()));
            assert_eq!(format.encode(&value).unwrap(), raw, "{}", path.display());
            if format == Format::Journal {
                let run_id = format!("fx-{}", folder.file_name().unwrap().to_str().unwrap());
                let session = value.get("session").and_then(Value::as_str).unwrap();
                let journal = Journal::parse(&raw, &run_id, session).unwrap();
                assert_eq!(journal.to_bytes().unwrap(), raw, "{}", path.display());
                assert!(Journal::parse(&raw, "fx-other", session).is_err());
            }
            // The same bytes through the private atomic writer, and back through the private reader.
            dir.write_json(name, format, &value).unwrap();
            let written = scratch.0.join(name);
            assert_eq!(fs::read(&written).unwrap(), raw, "{}", path.display());
            assert_eq!(fs::metadata(&written).unwrap().mode() & 0o7777, 0o600);
            assert_eq!(dir.read_json(name, format).unwrap(), value);
            fs::remove_file(&written).unwrap();
        }
        assert!(names(&scratch.0).is_empty());
        // Every format is in the corpus; the counts are its size when this test was written.
        println!("fixtures per format: {counts:?}");
        assert_eq!(counts.len(), 8, "{counts:?}");
        // Nothing in the corpus is skipped: every file but index.json was read above.
        fn walk(dir: &Path) -> usize {
            fs::read_dir(dir).unwrap().map(|e| e.unwrap().path())
                .map(|p| if p.is_dir() { walk(&p) } else { 1 }).sum()
        }
        assert_eq!(files.len(), walk(&corpus()) - 1);
    }

    fn journal(edit: impl FnOnce(&mut String)) -> Result<Journal, Error> {
        let mut raw = fs::read_to_string(corpus().join("runs/in-flight-mid-create/journal.json")).unwrap();
        edit(&mut raw);
        Journal::parse(raw.as_bytes(), "fx-in-flight-mid-create", &"5e".repeat(32))
    }

    #[test]
    fn the_journal_is_refused_as_journal_load_refuses_it() {
        let state = journal(|_| {}).unwrap();
        assert_eq!(state.records.iter().map(|r| r.state).collect::<Vec<_>>(), [State::Confirmed, State::Pending]);
        let id1 = "00000000-0000-4000-a000-000000000003";
        let id2 = "00000000-0000-4000-a000-000000000006";
        for (why, edit) in [
            ("schema", ("\"schema\": 1", "\"schema\": 2")),
            ("schema as float", ("\"schema\": 1", "\"schema\": 1.0")),
            ("schema as bool", ("\"schema\": 1", "\"schema\": true")),
            ("unknown key", ("\"schema\": 1", "\"schema\": 1, \"extra\": 0")),
            ("repeated key", ("\"schema\": 1", "\"schema\": 1, \"schema\": 1")),
            ("state", ("\"pending\"", "\"started\"")),
            ("repeated record id", (id2, id1)),
            ("record id case", (id1, "00000000-0000-4000-A000-000000000003")),
            ("name and index", ("\"XR-2\"", "\"XR-3\"")),
            ("index", ("\"index\": 1", "\"index\": -1")),
            ("output before", ("\"index\": 1, \"before\": null", "\"index\": 1, \"before\": \"x\"")),
            ("operation kind", ("\"kind\": \"output\"", "\"kind\": \"window\"")),
            ("broker scope", ("\"recovery_output\": \"DP-1\"", "\"recovery_output\": \"DP-2\"")),
            ("broker instance", ("\"instance\": \"0123", "\"instance\": \"x0123")),
            ("broker key", ("\"fallback\": \"fallback-1\"", "\"fallback\": \"fallback-1\", \"mode\": \"x\"")),
            ("not JSON", ("}}", "}")),
        ] {
            assert!(journal(|raw| *raw = raw.replacen(edit.0, edit.1, 1)).is_err(), "{why}");
        }
        assert!(Journal::parse(&vec![b' '; JOURNAL_LIMIT + 1], "fx-in-flight-mid-create", &"5e".repeat(32)).is_err());
        // Setting and workspace records, as Journal.prepare writes them.
        let setting = r#"{"kind": "setting", "key": "input:follow_mouse", "before": 1, "applied": 0}"#;
        let workspace = r#"{"kind": "workspace", "workspace": 7, "before": "DP-1", "applied": "XR-1"}"#;
        for (op, ok) in [
            (setting, true),
            (workspace, true),
            (&setting.replace("\"applied\": 0", "\"applied\": 4"), false),
            (&setting.replace("follow_mouse", "sensitivity"), false),
            (&workspace.replace("\"workspace\": 7", "\"workspace\": 0"), false),
            (&workspace.replace("\"XR-1\"", "\"-XR\""), false),
        ] {
            let result = journal(|raw| *raw = raw.replacen(r#"{"kind": "output", "id": "00000000-0000-4000-8000-000000000002", "name": "XR-2", "index": 1, "before": null}"#, op, 1));
            assert_eq!(result.is_ok(), ok, "{op}");
        }
    }

    #[test]
    fn an_isolated_broker_needs_its_pair_proof() {
        let isolated = r#"{"mode": "isolated-vm", "instance": "0123456789abcdef0123456789abcdef01234567_1700000000_4242", "output": "DP-2", "recovery_output": "DP-1", "epoch": "epoch-1", "fallback": "FB", "target": "TG", "boot_id": "01234567-89ab-cdef-0123-456789abcdef", "runtime": "/run/user/1000", "proof": {"target": {"name": "DP-2", "device": "226:1", "connector": 90}, "fallback": {"name": "DP-1", "device": "226:1", "connector": 91}}}"#
            .replace("FB", &"f".repeat(48))
            .replace("TG", &"a".repeat(48));
        let plain = r#"{"instance": "0123456789abcdef0123456789abcdef01234567_1700000000_4242", "output": "DP-2", "recovery_output": "DP-1", "epoch": "epoch-1", "fallback": "fallback-1"}"#;
        for (broker, ok) in [
            (isolated.clone(), true),
            (isolated.replace("isolated-vm", "vm"), false),
            (isolated.replace("\"connector\": 91", "\"connector\": 90"), false),
            (isolated.replace("226:1\", \"connector\": 91", "026:1\", \"connector\": 91"), false),
            (isolated.replace("\"/run/user/1000\"", "\"run\""), false),
            (isolated.replace(&"a".repeat(48), &"f".repeat(48)), false),
            (isolated.replace("\"name\": \"DP-1\"", "\"name\": \"DP-3\""), false),
        ] {
            let result = journal(|raw| *raw = raw.replacen(plain, &broker, 1));
            assert_eq!(result.is_ok(), ok, "{broker}");
            if let Ok(j) = result {
                // Kept in its own key order, so the rewrite is the same bytes.
                assert!(String::from_utf8(j.to_bytes().unwrap()).unwrap().contains(&broker));
            }
        }
    }

    #[test]
    fn shapes_are_checked_before_writing() {
        let bad = [
            (Format::Consent, r#"{"schema": 1, "run_id": "../x"}"#),
            (Format::Consent, r#"{"schema": 2, "run_id": "x"}"#),
            (Format::Progress, r#"{"phase": "run"}"#),
            (Format::Recovery, r#"[]"#),
            (Format::Installed, r#"{"plugin": {"path": "/a", "sha256": "ab"}, "renderer": {"path": "/b", "sha256": "ab"}}"#),
            (Format::ShimPin, r#"{"path": "rel", "sha256": "SHA"}"#),
            (Format::Pro2Pin, r#"{"schema": 1, "model": "luma", "bundle": "/b", "manifest_sha256": "SHA"}"#),
        ];
        for (format, raw) in bad {
            let raw = raw.replace("SHA", &"a".repeat(64));
            assert!(format.decode(raw.as_bytes()).is_err(), "{format:?} {raw}");
        }
        assert!(Format::Result.decode(br#"{"run_id": null, "complete": false}"#).is_ok());
        assert!(Format::Result.decode(&vec![b' '; FILE_LIMIT + 1]).is_err());
    }
}
