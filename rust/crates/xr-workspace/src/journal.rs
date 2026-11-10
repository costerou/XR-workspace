//! The run's write-ahead ownership journal, a port of `tools/native_spike/journal.py`.
//!
//! Intent first: a change to the desktop is recorded (`prepare`, state `pending`) before it is made, and marked
//! `confirmed` only after its readback matched (`confirm`). Recovery (`crate::recovery`) then undoes what is left.
//!
//! The file format is frozen (docs/rust-rebuild-plan.md, phase 6): this module reads every journal the Python writes,
//! refuses every one the Python refuses, with the same reason, and writes byte for byte what the Python would, so either
//! supervisor can recover a run the other started. The locks are the same `flock`s on the same files, so the two
//! exclude each other too.
//!
//! Files live in a private directory (owned, mode 0700, reached without following a symlink); the journal and its
//! locks are owned regular single-link files of mode 0600. Every write goes to a fresh temporary file that is synced and
//! renamed over the journal, then the directory is synced.

use crate::pyjson;
use rustix::fd::{AsFd, OwnedFd};
use rustix::fs::{self, AtFlags, FileType, FlockOperation, Mode, OFlags};
use rustix::io::Errno;
use serde_json::{Map, Value, json};
use std::fmt;
use std::io::{self, Read, Write};
use std::path::{Component, Path, PathBuf};

/// Largest journal file either supervisor reads or writes.
pub const MAX_JOURNAL: usize = 1_048_576;
const SETTING: &str = "input:follow_mouse";
const MAX_I32: i64 = 2_147_483_647;

/// Why a journal call failed. `Invalid` and `Permission` carry the Python's message (its `ValueError` and
/// `PermissionError`), `Missing` is its `FileNotFoundError('recovery journal missing')`.
#[derive(Debug)]
pub enum Error {
    Invalid(String),
    Permission(&'static str),
    Missing,
    Io(io::Error),
}

impl fmt::Display for Error {
    fn fmt(&self, f: &mut fmt::Formatter) -> fmt::Result {
        match self {
            Error::Invalid(text) => f.write_str(text),
            Error::Permission(text) => f.write_str(text),
            Error::Missing => f.write_str("recovery journal missing"),
            Error::Io(e) => e.fmt(f),
        }
    }
}

impl std::error::Error for Error {}

impl From<Errno> for Error {
    fn from(e: Errno) -> Self {
        Error::Io(e.into())
    }
}

impl From<io::Error> for Error {
    fn from(e: io::Error) -> Self {
        Error::Io(e)
    }
}

fn invalid<T>(text: &str) -> Result<T, Error> {
    Err(Error::Invalid(text.to_owned()))
}

// ---- Identifier checks, as ipc.token, hyprland.INSTANCE and uuid.UUID do them ----

fn is_token(s: &str) -> bool {
    let b = s.as_bytes();
    (1..=128).contains(&b.len())
        && b[0].is_ascii_alphanumeric()
        && b[1..]
            .iter()
            .all(|&c| c.is_ascii_alphanumeric() || b"_.:-".contains(&c))
}

/// `ipc.token`.
fn token(value: &Value) -> Result<&str, Error> {
    match value.as_str() {
        Some(s) if is_token(s) => Ok(s),
        _ => invalid("invalid identifier"),
    }
}

fn lower_hex(s: &str, len: usize) -> bool {
    s.len() == len && s.bytes().all(|c| matches!(c, b'0'..=b'9' | b'a'..=b'f'))
}

fn digits(s: &str, min: usize, max: usize) -> bool {
    (min..=max).contains(&s.len()) && s.bytes().all(|c| c.is_ascii_digit())
}

/// `hyprland.INSTANCE`: `[0-9a-f]{40}_[0-9]+_[0-9]+`.
fn is_instance(s: &str) -> bool {
    let mut parts = s.splitn(3, '_');
    let (Some(hash), Some(a), Some(b)) = (parts.next(), parts.next(), parts.next()) else {
        return false;
    };
    lower_hex(hash, 40) && digits(a, 1, usize::MAX) && digits(b, 1, usize::MAX)
}

/// `[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}`: the only form `str(uuid.UUID(s)) == s` accepts.
fn is_canonical_uuid(s: &str) -> bool {
    let groups: Vec<&str> = s.split('-').collect();
    groups.len() == 5
        && groups
            .iter()
            .zip([8, 4, 4, 4, 12])
            .all(|(g, n)| lower_hex(g, n))
}

/// The check `str(uuid.UUID(s)) != s`: Ok(true) when `s` is a UUID in canonical form, Ok(false) when it is one written
/// another way, and the UUID parser's error when it is none.
fn canonical_uuid(s: &str) -> Result<bool, Error> {
    if is_canonical_uuid(s) {
        return Ok(true);
    }
    let hex = s.replace("urn:", "").replace("uuid:", "");
    let hex = hex.trim_matches(|c| c == '{' || c == '}').replace('-', "");
    if hex.len() == 32 && hex.bytes().all(|c| c.is_ascii_hexdigit()) {
        Ok(false)
    } else {
        invalid("badly formed hexadecimal UUID string")
    }
}

/// A random (version 4) UUID, as `uuid.uuid4()` writes it.
fn uuid4() -> Result<String, Error> {
    let mut b = [0u8; 16];
    let mut filled = 0;
    while filled < b.len() {
        filled += rustix::rand::getrandom(&mut b[filled..], rustix::rand::GetRandomFlags::empty())?;
    }
    b[6] = (b[6] & 0x0f) | 0x40;
    b[8] = (b[8] & 0x3f) | 0x80;
    let h: String = b.iter().map(|x| format!("{x:02x}")).collect();
    Ok(format!(
        "{}-{}-{}-{}-{}",
        &h[..8],
        &h[8..12],
        &h[12..16],
        &h[16..20],
        &h[20..]
    ))
}

/// An int in Python's sense (`type(v) is int`): not a bool and not a float.
fn int(value: &Value) -> Option<i128> {
    value
        .as_i64()
        .map(i128::from)
        .or_else(|| value.as_u64().map(i128::from))
}

fn keys(map: &Map<String, Value>, want: &[&str]) -> bool {
    map.len() == want.len() && want.iter().all(|k| map.contains_key(*k))
}

// ---- Records ----

/// One change a run makes to the desktop, as the journal holds it.
#[derive(Clone, Debug, PartialEq, Eq)]
pub enum Operation {
    /// `input:follow_mouse` set from `before` to `applied` (each 0 to 3).
    Setting {
        key: String,
        before: i64,
        applied: i64,
    },
    /// Workspace `workspace` moved from monitor `before` to `applied`.
    Workspace {
        workspace: i64,
        before: String,
        applied: String,
    },
    /// Headless output `XR-<index + 1>` created with ownership UUID `id`.
    Output {
        id: String,
        name: String,
        index: i64,
    },
}

impl Operation {
    /// The record's operation object, its keys in the order the Python supervisor writes them.
    pub fn to_value(&self) -> Value {
        match self {
            Operation::Output { id, name, index } => {
                json!({"kind": "output", "id": id, "name": name, "index": index, "before": null})
            }
            Operation::Setting {
                key,
                before,
                applied,
            } => {
                json!({"kind": "setting", "key": key, "before": before, "applied": applied})
            }
            Operation::Workspace {
                workspace,
                before,
                applied,
            } => {
                json!({"kind": "workspace", "workspace": workspace, "before": before, "applied": applied})
            }
        }
    }

    /// `validate_operation`: the same checks in the same order, so a refusal gives the same reason.
    pub fn from_value(value: &Value) -> Result<Operation, Error> {
        let Some(op) = value.as_object() else {
            return invalid("invalid operation");
        };
        let fields: &[&str] = match op.get("kind").and_then(Value::as_str) {
            Some("setting") => &["kind", "key", "before", "applied"],
            Some("workspace") => &["kind", "workspace", "before", "applied"],
            Some("output") => &["kind", "id", "name", "index", "before"],
            _ => return invalid("unsupported operation"),
        };
        if !keys(op, fields) {
            return invalid("unsupported operation");
        }
        match op["kind"].as_str() {
            Some("setting") => {
                if op["key"].as_str() != Some(SETTING) {
                    return invalid("unsupported setting");
                }
                let value = |k: &str| int(&op[k]).filter(|v| (0..=3).contains(v));
                let (Some(before), Some(applied)) = (value("before"), value("applied")) else {
                    return invalid("invalid setting value");
                };
                Ok(Operation::Setting {
                    key: SETTING.to_owned(),
                    before: before as i64,
                    applied: applied as i64,
                })
            }
            Some("workspace") => {
                let Some(workspace) =
                    int(&op["workspace"]).filter(|w| 0 < *w && *w <= MAX_I32 as i128)
                else {
                    return invalid("invalid workspace");
                };
                let before = token(&op["before"])?.to_owned();
                let applied = token(&op["applied"])?.to_owned();
                Ok(Operation::Workspace {
                    workspace: workspace as i64,
                    before,
                    applied,
                })
            }
            _ => {
                let id = match op["id"].as_str() {
                    Some(id) if canonical_uuid(id)? && op["before"].is_null() => id.to_owned(),
                    _ => return invalid("invalid output ownership"),
                };
                let Some(index) = int(&op["index"]).filter(|i| (0..=MAX_I32 as i128).contains(i))
                else {
                    return invalid("invalid output index");
                };
                let name = token(&op["name"])?.to_owned();
                if name != format!("XR-{}", index + 1) {
                    return invalid("output name does not match journal index");
                }
                Ok(Operation::Output {
                    id,
                    name,
                    index: index as i64,
                })
            }
        }
    }

    pub fn is_output(&self) -> bool {
        matches!(self, Operation::Output { .. })
    }
}

/// How far a record got.
#[derive(Clone, Copy, Debug, PartialEq, Eq)]
pub enum State {
    /// Intent written; the change may or may not have happened.
    Pending,
    /// The change was read back as made.
    Confirmed,
    /// Undone, or ended some other proven way. Recovery leaves it alone.
    Done,
}

impl State {
    fn name(self) -> &'static str {
        match self {
            State::Pending => "pending",
            State::Confirmed => "confirmed",
            State::Done => "done",
        }
    }
}

/// What the readback of a change showed, for `confirm`.
#[derive(Clone, Debug, PartialEq, Eq)]
pub enum Observed {
    Output {
        id: String,
        name: String,
        physical: bool,
    },
    Setting {
        value: i64,
    },
    Workspace {
        output: String,
    },
}

/// One journal record. `raw` is the object as read or written, kept so that it is written back in its own key order.
#[derive(Clone, Debug)]
pub struct Record {
    pub id: String,
    pub operation: Operation,
    pub state: State,
    raw: Map<String, Value>,
}

impl Record {
    pub(crate) fn set_state(&mut self, state: State) {
        self.state = state;
        self.raw.insert("state".to_owned(), state.name().into());
    }
}

/// An output record not yet done (`Journal::live_outputs`).
#[derive(Clone, Debug, PartialEq, Eq)]
pub struct LiveOutput {
    pub id: String,
    pub name: String,
    pub index: i64,
}

// ---- Broker scope ----

const BROKER: &[&str] = &["instance", "output", "recovery_output", "epoch", "fallback"];
const ISOLATED_BROKER: &[&str] = &[
    "mode",
    "instance",
    "output",
    "recovery_output",
    "epoch",
    "fallback",
    "target",
    "boot_id",
    "runtime",
    "proof",
];

fn validate_isolated(c: &Map<String, Value>) -> Result<(), Error> {
    if c["mode"].as_str() != Some("isolated-vm") {
        return invalid("invalid broker mode");
    }
    for key in ["instance", "output", "recovery_output", "epoch"] {
        token(&c[key])?;
    }
    for key in ["fallback", "target"] {
        if !c[key].as_str().is_some_and(|s| lower_hex(s, 48)) {
            return invalid("invalid pair handle");
        }
    }
    if c["target"] == c["fallback"] {
        return invalid("pair handles alias");
    }
    if !c["boot_id"].as_str().is_some_and(is_canonical_uuid) {
        return invalid("invalid boot identity");
    }
    if !c["runtime"].as_str().is_some_and(|s| s.starts_with('/')) {
        return invalid("invalid runtime");
    }
    let Some(proof) = c["proof"]
        .as_object()
        .filter(|p| keys(p, &["target", "fallback"]))
    else {
        return invalid("invalid proof");
    };
    let mut ends = Vec::new();
    for (key, name) in [
        ("target", &c["output"]),
        ("fallback", &c["recovery_output"]),
    ] {
        let side = proof[key]
            .as_object()
            .filter(|s| keys(s, &["name", "device", "connector"]));
        let device_ok = |d: &str| {
            d.split_once(':').is_some_and(|(major, minor)| {
                digits(major, 1, 10) && !major.starts_with('0') && digits(minor, 1, 10)
            })
        };
        let Some(side) = side.filter(|s| {
            &s["name"] == name
                && s["device"].as_str().is_some_and(device_ok)
                && int(&s["connector"]).is_some_and(|n| 0 < n && n <= 4_294_967_295)
        }) else {
            return invalid("invalid proof");
        };
        ends.push((side["device"].clone(), side["connector"].clone()));
    }
    if ends[0] == ends[1] {
        return invalid("proof outputs alias");
    }
    Ok(())
}

/// `validate_broker`: the ownership broker's scope a journal is bound to.
pub fn validate_broker(context: &Value) -> Result<Value, Error> {
    let map = context.as_object();
    match map {
        Some(c) if keys(c, ISOLATED_BROKER) => validate_isolated(c)?,
        Some(c) if keys(c, BROKER) => {
            for value in c.values() {
                token(value)?;
            }
        }
        _ => return invalid("invalid broker context"),
    }
    let c = map.expect("checked above");
    if !c["instance"].as_str().is_some_and(is_instance) || c["output"] == c["recovery_output"] {
        return invalid("invalid broker scope");
    }
    Ok(context.clone())
}

// ---- Files ----

fn absolute(path: &Path) -> Result<PathBuf, Error> {
    Ok(if path.is_absolute() {
        path.to_owned()
    } else {
        std::env::current_dir()?.join(path)
    })
}

/// `ipc.private_dir`: every component opened without following a symlink, the leaf owned and mode 0700.
fn private_dir(path: &Path) -> Result<OwnedFd, Error> {
    let mut fd = fs::open(
        "/",
        OFlags::RDONLY | OFlags::DIRECTORY | OFlags::CLOEXEC,
        Mode::empty(),
    )?;
    for part in absolute(path)?.components() {
        match part {
            Component::RootDir | Component::CurDir => continue,
            Component::Normal(name) => {
                fd = fs::openat(
                    &fd,
                    name,
                    OFlags::RDONLY | OFlags::DIRECTORY | OFlags::NOFOLLOW | OFlags::CLOEXEC,
                    Mode::empty(),
                )?;
            }
            _ => return invalid("unsafe path"),
        }
    }
    let st = fs::fstat(&fd)?;
    if st.st_uid != rustix::process::getuid().as_raw() || st.st_mode & 0o7777 != 0o700 {
        return Err(Error::Permission("directory must be owned and mode 0700"));
    }
    Ok(fd)
}

/// `journal._safe_file`.
fn safe_file(fd: impl AsFd) -> Result<(), Error> {
    let st = fs::fstat(fd)?;
    if FileType::from_raw_mode(st.st_mode) != FileType::RegularFile
        || st.st_uid != rustix::process::getuid().as_raw()
        || st.st_mode & 0o7777 != 0o600
        || st.st_nlink != 1
    {
        return Err(Error::Permission(
            "journal/lock must be owned regular mode 0600 single-link file",
        ));
    }
    Ok(())
}

fn split(path: &Path) -> Result<(PathBuf, String), Error> {
    let name = path
        .file_name()
        .and_then(|n| n.to_str())
        .ok_or_else(|| Error::Invalid("unsafe path".into()))?;
    let parent = path
        .parent()
        .filter(|p| !p.as_os_str().is_empty())
        .unwrap_or(Path::new("."));
    Ok((parent.to_owned(), name.to_owned()))
}

fn open_lock(dir: &OwnedFd, name: &str) -> Result<OwnedFd, Error> {
    let fd = fs::openat(
        dir,
        name,
        OFlags::RDWR | OFlags::CREATE | OFlags::NOFOLLOW | OFlags::NONBLOCK | OFlags::CLOEXEC,
        Mode::from_raw_mode(0o600),
    )?;
    safe_file(&fd)?;
    Ok(fd)
}

/// `managed_owner`: the run's lifetime lock (`journal.json.owner`), taken without waiting. While one supervisor
/// holds it, the other cannot open the same run for recovery. Held until dropped.
pub struct ManagedOwner {
    _lock: OwnedFd,
}

impl ManagedOwner {
    /// Fails with `Error::Io` of kind `WouldBlock` when another process holds it.
    pub fn acquire(path: impl AsRef<Path>) -> Result<ManagedOwner, Error> {
        let (parent, name) = split(path.as_ref())?;
        let dir = private_dir(&parent)?;
        let lock = open_lock(&dir, &format!("{name}.owner"))?;
        fs::flock(&lock, FlockOperation::NonBlockingLockExclusive)?;
        Ok(ManagedOwner { _lock: lock })
    }
}

/// A journal transaction: the directory and the journal's `.lock`, held exclusively. Every journal call runs inside
/// one, and recovery holds one throughout.
pub struct Transaction {
    dir: OwnedFd,
    _lock: OwnedFd,
}

// ---- The journal ----

/// A run's journal: `run_id` and `session` are its identity, checked on every load.
pub struct Journal {
    pub path: PathBuf,
    parent: PathBuf,
    name: String,
    pub run_id: String,
    pub session: String,
    pub records: Vec<Record>,
    pub broker: Option<Value>,
    exists: bool,
}

impl Journal {
    /// `Journal.open`: creates an empty journal unless one exists, or with `existing_only` (recovery) refuses with
    /// `Error::Missing`.
    pub fn open(
        path: impl AsRef<Path>,
        run_id: &str,
        session: &str,
        existing_only: bool,
    ) -> Result<Journal, Error> {
        let path = path.as_ref().to_owned();
        let run_id = token(&run_id.into())?.to_owned();
        let session = token(&session.into())?.to_owned();
        let (parent, name) = split(&path)?;
        let mut journal = Journal {
            path,
            parent,
            name,
            run_id,
            session,
            records: Vec::new(),
            broker: None,
            exists: false,
        };
        let tx = journal.begin()?;
        if !journal.exists {
            if existing_only {
                return Err(Error::Missing);
            }
            journal.save(&tx)?;
        }
        Ok(journal)
    }

    /// Takes the journal's lock and reloads it from disk.
    pub fn begin(&mut self) -> Result<Transaction, Error> {
        let dir = private_dir(&self.parent)?;
        let lock = open_lock(&dir, &format!("{}.lock", self.name))?;
        fs::flock(&lock, FlockOperation::LockExclusive)?;
        let tx = Transaction { dir, _lock: lock };
        self.load(&tx)?;
        Ok(tx)
    }

    fn load(&mut self, tx: &Transaction) -> Result<(), Error> {
        let fd = match fs::openat(
            &tx.dir,
            self.name.as_str(),
            OFlags::RDONLY | OFlags::NOFOLLOW | OFlags::NONBLOCK | OFlags::CLOEXEC,
            Mode::empty(),
        ) {
            Err(Errno::NOENT) => {
                self.exists = false;
                self.records.clear();
                self.broker = None;
                return Ok(());
            }
            other => other?,
        };
        self.exists = true;
        safe_file(&fd)?;
        let mut raw = Vec::new();
        std::fs::File::from(fd)
            .take(MAX_JOURNAL as u64 + 1)
            .read_to_end(&mut raw)?;
        if raw.len() > MAX_JOURNAL {
            return invalid("journal too large");
        }
        let data = pyjson::loads(&raw).map_err(|e| Error::Invalid(e.to_owned()))?;
        let fields = ["schema", "run_id", "session", "operations"];
        let Some(data) = data
            .as_object()
            .filter(|d| keys(d, &fields) || (keys(d, &[fields.as_slice(), &["broker"]].concat())))
        else {
            return invalid("invalid journal");
        };
        if int(&data["schema"]) != Some(1)
            || data["run_id"].as_str() != Some(&self.run_id)
            || data["session"].as_str() != Some(&self.session)
        {
            return invalid("wrong journal identity");
        }
        let Some(raw_records) = data["operations"].as_array() else {
            return invalid("invalid records");
        };
        let mut records: Vec<Record> = Vec::with_capacity(raw_records.len());
        for raw in raw_records {
            let Some(raw) = raw
                .as_object()
                .filter(|r| keys(r, &["id", "operation", "state"]))
            else {
                return invalid("invalid record");
            };
            let id = match raw["id"].as_str() {
                Some(id) if canonical_uuid(id)? && !records.iter().any(|r| r.id == id) => {
                    id.to_owned()
                }
                _ => return invalid("invalid record id"),
            };
            let state = match raw["state"].as_str() {
                Some("pending") => State::Pending,
                Some("confirmed") => State::Confirmed,
                Some("done") => State::Done,
                _ => return invalid("invalid state"),
            };
            let operation = Operation::from_value(&raw["operation"])?;
            records.push(Record {
                id,
                operation,
                state,
                raw: raw.clone(),
            });
        }
        self.broker = data.get("broker").map(validate_broker).transpose()?;
        self.records = records;
        Ok(())
    }

    /// `_save`, inside `tx`.
    pub fn save(&self, tx: &Transaction) -> Result<(), Error> {
        let mut data = Map::new();
        data.insert("schema".into(), 1.into());
        data.insert("run_id".into(), self.run_id.clone().into());
        data.insert("session".into(), self.session.clone().into());
        data.insert(
            "operations".into(),
            self.records
                .iter()
                .map(|r| Value::Object(r.raw.clone()))
                .collect(),
        );
        if let Some(broker) = &self.broker {
            data.insert("broker".into(), validate_broker(broker)?);
        }
        let raw = pyjson::dumps(&Value::Object(data));
        if raw.len() > MAX_JOURNAL {
            return invalid("journal too large");
        }
        let temp = format!(".journal-{}", uuid4()?);
        let fd = fs::openat(
            &tx.dir,
            temp.as_str(),
            OFlags::WRONLY | OFlags::CREATE | OFlags::EXCL | OFlags::NOFOLLOW | OFlags::CLOEXEC,
            Mode::from_raw_mode(0o600),
        )?;
        let written = (|| -> Result<(), Error> {
            let mut file = std::fs::File::from(fd);
            file.write_all(raw.as_bytes())?;
            file.sync_all()?;
            fs::renameat(&tx.dir, temp.as_str(), &tx.dir, self.name.as_str())?;
            fs::fsync(&tx.dir)?;
            Ok(())
        })();
        match fs::unlinkat(&tx.dir, temp.as_str(), AtFlags::empty()) {
            Ok(()) | Err(Errno::NOENT) => {}
            Err(e) if written.is_ok() => return Err(e.into()),
            Err(_) => {}
        }
        written
    }

    /// `bind_broker`: binds an empty journal to the broker scope `context`, once.
    pub fn bind_broker(&mut self, context: &Value) -> Result<(), Error> {
        let context = validate_broker(context)?;
        let tx = self.begin()?;
        if self.broker.as_ref() == Some(&context) {
            return Ok(());
        }
        if self.broker.is_some() || !self.records.is_empty() {
            return invalid("cannot rebind existing journal authority");
        }
        self.broker = Some(context);
        self.save(&tx)
    }

    /// `prepare`: the intent of `operation`, written before it is made. Returns the record's id.
    pub fn prepare(&mut self, operation: &Operation) -> Result<String, Error> {
        let value = operation.to_value();
        let operation = Operation::from_value(&value)?;
        let id = uuid4()?;
        let tx = self.begin()?;
        let Value::Object(op) = value else {
            unreachable!()
        };
        let mut raw = Map::new();
        raw.insert("id".into(), id.clone().into());
        raw.insert("operation".into(), Value::Object(op));
        raw.insert("state".into(), State::Pending.name().into());
        self.records.push(Record {
            id: id.clone(),
            operation,
            state: State::Pending,
            raw,
        });
        self.save(&tx)?;
        Ok(id)
    }

    /// `confirm`: the readback of record `id` showed its change made.
    pub fn confirm(&mut self, id: &str, observed: &Observed) -> Result<(), Error> {
        let tx = self.begin()?;
        let Some(record) = self
            .records
            .iter_mut()
            .find(|r| r.id == id && r.state != State::Done)
        else {
            return invalid("unknown/finished operation");
        };
        let matches = match (&record.operation, observed) {
            (
                Operation::Output { id, name, .. },
                Observed::Output {
                    id: seen_id,
                    name: seen_name,
                    physical,
                },
            ) => id == seen_id && name == seen_name && !physical,
            (Operation::Setting { applied, .. }, Observed::Setting { value }) => applied == value,
            (Operation::Workspace { applied, .. }, Observed::Workspace { output }) => {
                applied == output
            }
            _ => false,
        };
        if !matches {
            return invalid("mutation not observed");
        }
        record.set_state(State::Confirmed);
        self.save(&tx)
    }

    /// `output_removed`: a desktop removed during the run, after the broker proved it gone; recovery leaves it alone.
    pub fn output_removed(&mut self, output_id: &str) -> Result<(), Error> {
        let tx = self.begin()?;
        let Some(record) = self
            .records
            .iter_mut()
            .find(|r| matches!(&r.operation, Operation::Output { id, .. } if id == output_id))
        else {
            return invalid("unknown output");
        };
        match record.state {
            State::Done => return Ok(()),
            State::Pending => return invalid("output removal before its creation was confirmed"),
            State::Confirmed => record.set_state(State::Done),
        }
        self.save(&tx)
    }

    /// `compositor_ended`: the compositor of this journal's scope exited, and every record ended with it.
    pub fn compositor_ended(&mut self) -> Result<(), Error> {
        let tx = self.begin()?;
        for record in &mut self.records {
            record.set_state(State::Done);
        }
        self.save(&tx)
    }

    /// Every output this run created, removed since or not.
    pub fn output_names(&mut self) -> Result<Vec<String>, Error> {
        let _tx = self.begin()?;
        Ok(self
            .records
            .iter()
            .filter_map(|r| match &r.operation {
                Operation::Output { name, .. } => Some(name.clone()),
                _ => None,
            })
            .collect())
    }

    /// Output records not yet done, in journal order.
    pub fn live_outputs(&mut self) -> Result<Vec<LiveOutput>, Error> {
        let _tx = self.begin()?;
        Ok(self
            .records
            .iter()
            .filter(|r| r.state != State::Done)
            .filter_map(|r| match &r.operation {
                Operation::Output { id, name, index } => Some(LiveOutput {
                    id: id.clone(),
                    name: name.clone(),
                    index: *index,
                }),
                _ => None,
            })
            .collect())
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn identifiers() {
        assert!(is_token("XR-1") && is_token(&"a".repeat(128)) && !is_token(&"a".repeat(129)));
        assert!(!is_token("") && !is_token("-a") && !is_token("a b") && !is_token("é"));
        assert!(
            is_instance(&format!("{}_1_2", "a".repeat(40)))
                && !is_instance(&format!("{}_1_", "a".repeat(40)))
        );
        assert!(!is_instance(&format!("{}_1_2_3", "a".repeat(40))));
        assert!(canonical_uuid("0b6f8a52-6a6e-4f3c-9d55-1f0d3f9a0c01").unwrap());
        assert!(!canonical_uuid("0B6F8A52-6A6E-4F3C-9D55-1F0D3F9A0C01").unwrap());
        assert!(!canonical_uuid("urn:uuid:0b6f8a526a6e4f3c9d551f0d3f9a0c01").unwrap());
        assert!(canonical_uuid("XR-1").is_err());
        let id = uuid4().unwrap();
        assert!(is_canonical_uuid(&id) && &id[14..15] == "4" && "89ab".contains(&id[19..20]));
    }
}
