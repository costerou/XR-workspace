//! The run's write-ahead ownership journal, a port of `tools/native_spike/journal.py`.
//!
//! Intent first: a change to the desktop is recorded (`prepare`, state `pending`) before it is made, and marked
//! `confirmed` only after its readback matched (`confirm`). Recovery (`crate::recovery`) then undoes what is left.
//!
//! The file format is frozen (docs/rust-rebuild-plan.md, phase 6) and owned by [`xr_wire::disk::Journal`], which
//! checks a journal as `Journal._load` does and writes the bytes `_save` writes, so either supervisor can recover a run
//! the other started. This module adds what the Python's `Journal` class adds around it: the private directory, the
//! same `flock`s on the same files (so the two supervisors exclude each other too), and the calls a run makes.
//!
//! Files live in a private directory (owned, mode 0700, reached without following a symlink); the journal and its
//! locks are owned regular single-link files of mode 0600. Every write replaces the journal atomically.

use rustix::fd::{AsFd, OwnedFd};
use rustix::fs::{self, FileType, FlockOperation, Mode, OFlags};
use std::fmt;
use std::io;
use std::path::{Path, PathBuf};
use xr_wire::disk::{self, JOURNAL_LIMIT};
use xr_wire::files::PrivateDir;
use xr_wire::json::{self, Value};

pub use xr_wire::disk::State;

const UNSAFE_FILE: &str = "journal/lock must be owned regular mode 0600 single-link file";

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

impl From<rustix::io::Errno> for Error {
    fn from(e: rustix::io::Errno) -> Self {
        Error::Io(e.into())
    }
}

impl From<io::Error> for Error {
    fn from(e: io::Error) -> Self {
        Error::Io(e)
    }
}

impl From<json::Error> for Error {
    /// The Python's text: `_pairs` raises "duplicate key", and the corpus reports every other decoding failure as
    /// "invalid JSON" (the decoder's own wording differs between the two languages).
    fn from(e: json::Error) -> Self {
        Error::Invalid(if e.0.contains("duplicate key") {
            "duplicate key".into()
        } else if e.0.starts_with("invalid JSON") {
            "invalid JSON".into()
        } else {
            e.0
        })
    }
}

fn invalid<T>(text: &str) -> Result<T, Error> {
    Err(Error::Invalid(text.to_owned()))
}

/// `ipc.token`.
fn token(s: &str) -> Result<String, Error> {
    if disk::is_token(s) {
        Ok(s.to_owned())
    } else {
        invalid("invalid identifier")
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

/// `==` on two decoded JSON values as Python compares the dicts: objects without regard to key order.
fn same(a: &Value, b: &Value) -> bool {
    match (a, b) {
        (Value::Object(x), Value::Object(y)) => {
            x.len() == y.len()
                && x.iter()
                    .all(|(k, v)| y.iter().any(|(k2, v2)| k == k2 && same(v, v2)))
        }
        (Value::Array(x), Value::Array(y)) => {
            x.len() == y.len() && x.iter().zip(y).all(|(v, v2)| same(v, v2))
        }
        _ => a == b,
    }
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
        let s = |v: &str| Value::String(v.to_owned());
        let n = |v: i64| Value::Number(v.into());
        let fields: Vec<(&str, Value)> = match self {
            Operation::Output { id, name, index } => vec![
                ("kind", s("output")),
                ("id", s(id)),
                ("name", s(name)),
                ("index", n(*index)),
                ("before", Value::Null),
            ],
            Operation::Setting {
                key,
                before,
                applied,
            } => vec![
                ("kind", s("setting")),
                ("key", s(key)),
                ("before", n(*before)),
                ("applied", n(*applied)),
            ],
            Operation::Workspace {
                workspace,
                before,
                applied,
            } => vec![
                ("kind", s("workspace")),
                ("workspace", n(*workspace)),
                ("before", s(before)),
                ("applied", s(applied)),
            ],
        };
        Value::Object(fields.into_iter().map(|(k, v)| (k.to_owned(), v)).collect())
    }

    /// The typed form of an operation `xr_wire` has already checked.
    fn from_checked(op: &Value) -> Result<Operation, Error> {
        let s = |k: &str| op.get(k).and_then(Value::as_str).map(str::to_owned);
        let n = |k: &str| {
            op.get(k)
                .and_then(Value::as_int)
                .and_then(|v| i64::try_from(v).ok())
        };
        let parsed = match op.get("kind").and_then(Value::as_str) {
            Some("setting") => {
                s("key")
                    .zip(n("before").zip(n("applied")))
                    .map(|(key, (before, applied))| Operation::Setting {
                        key,
                        before,
                        applied,
                    })
            }
            Some("workspace") => n("workspace").zip(s("before").zip(s("applied"))).map(
                |(workspace, (before, applied))| Operation::Workspace {
                    workspace,
                    before,
                    applied,
                },
            ),
            Some("output") => s("id")
                .zip(s("name").zip(n("index")))
                .map(|(id, (name, index))| Operation::Output { id, name, index }),
            _ => None,
        };
        parsed.ok_or_else(|| Error::Invalid("unsupported operation".into()))
    }

    pub fn is_output(&self) -> bool {
        matches!(self, Operation::Output { .. })
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

/// One journal record. `value` is the operation object as read or written and `keys` the record's own key order, kept
/// so it is written back as it was read.
#[derive(Clone, Debug)]
pub struct Record {
    pub id: String,
    pub operation: Operation,
    pub state: State,
    value: Value,
    keys: [&'static str; 3],
}

impl Record {
    fn from_disk(record: disk::Record) -> Result<Record, Error> {
        Ok(Record {
            operation: Operation::from_checked(&record.operation)?,
            id: record.id,
            state: record.state,
            value: record.operation,
            keys: record.keys,
        })
    }

    fn to_disk(&self) -> disk::Record {
        disk::Record {
            id: self.id.clone(),
            operation: self.value.clone(),
            state: self.state,
            keys: self.keys,
        }
    }
}

/// An output record not yet done (`Journal::live_outputs`).
#[derive(Clone, Debug, PartialEq, Eq)]
pub struct LiveOutput {
    pub id: String,
    pub name: String,
    pub index: i64,
}

// ---- Files ----

/// `journal._safe_file`.
fn safe_file(fd: impl AsFd) -> Result<(), Error> {
    let st = fs::fstat(fd)?;
    if FileType::from_raw_mode(st.st_mode) != FileType::RegularFile
        || st.st_uid != rustix::process::getuid().as_raw()
        || st.st_mode & 0o7777 != 0o600
        || st.st_nlink != 1
    {
        return Err(Error::Permission(UNSAFE_FILE));
    }
    Ok(())
}

/// `ipc.private_dir`, with the Python's message when the directory is not private.
fn private_dir(path: &Path) -> Result<PrivateDir, Error> {
    PrivateDir::open(path, false).map_err(|e| match e.kind() {
        io::ErrorKind::PermissionDenied if e.raw_os_error().is_none() => {
            Error::Permission("directory must be owned and mode 0700")
        }
        _ => Error::Io(e),
    })
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

fn open_lock(dir: &PrivateDir, name: &str) -> Result<OwnedFd, Error> {
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
    dir: PrivateDir,
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
    /// The ownership broker's scope, kept as written.
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
        let run_id = token(run_id)?;
        let session = token(session)?;
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
        // PrivateDir::read opens without following a link and refuses an unsafe file before reading; its own messages
        // (not owned, too large) become the Python's.
        let raw = match tx.dir.read(&self.name, JOURNAL_LIMIT, true) {
            Err(e) if e.kind() == io::ErrorKind::NotFound => {
                self.exists = false;
                self.records.clear();
                self.broker = None;
                return Ok(());
            }
            Err(e) if e.raw_os_error().is_none() => {
                return Err(match e.kind() {
                    io::ErrorKind::PermissionDenied => Error::Permission(UNSAFE_FILE),
                    io::ErrorKind::InvalidInput => Error::Invalid("journal too large".into()),
                    _ => Error::Io(e),
                });
            }
            other => other?,
        };
        self.exists = true;
        let journal = disk::Journal::parse(&raw, &self.run_id, &self.session)?;
        self.records = journal
            .records
            .into_iter()
            .map(Record::from_disk)
            .collect::<Result<_, _>>()?;
        self.broker = journal.broker;
        Ok(())
    }

    fn to_disk(&self) -> disk::Journal {
        disk::Journal {
            run_id: self.run_id.clone(),
            session: self.session.clone(),
            records: self.records.iter().map(Record::to_disk).collect(),
            broker: self.broker.clone(),
        }
    }

    /// `_save`, inside `tx`.
    pub fn save(&self, tx: &Transaction) -> Result<(), Error> {
        let raw = self.to_disk().to_bytes()?;
        tx.dir.replace(&self.name, &raw)?;
        Ok(())
    }

    /// `validate_broker` and `validate_operation`, as xr-wire checks them for a write: a journal holding only `broker`
    /// or `operation`.
    fn check(&self, broker: Option<&Value>, operation: Option<&Value>) -> Result<(), Error> {
        let records = operation
            .map(|op| disk::Record {
                id: "00000000-0000-4000-8000-000000000000".into(),
                operation: op.clone(),
                state: State::Pending,
                keys: disk::Record::KEYS,
            })
            .into_iter()
            .collect();
        disk::Journal {
            run_id: self.run_id.clone(),
            session: self.session.clone(),
            records,
            broker: broker.cloned(),
        }
        .to_bytes()?;
        Ok(())
    }

    /// `bind_broker`: binds an empty journal to the broker scope `context`, once.
    pub fn bind_broker(&mut self, context: &Value) -> Result<(), Error> {
        self.check(Some(context), None)?;
        let tx = self.begin()?;
        if self.broker.as_ref().is_some_and(|b| same(b, context)) {
            return Ok(());
        }
        if self.broker.is_some() || !self.records.is_empty() {
            return invalid("cannot rebind existing journal authority");
        }
        self.broker = Some(context.clone());
        self.save(&tx)
    }

    /// `prepare`: the intent of `operation` (an operation object, as [`Operation::to_value`] gives), written before it
    /// is made. Returns the record's id.
    pub fn prepare(&mut self, operation: &Value) -> Result<String, Error> {
        self.check(None, Some(operation))?;
        let typed = Operation::from_checked(operation)?;
        let id = uuid4()?;
        let tx = self.begin()?;
        self.records.push(Record {
            id: id.clone(),
            operation: typed,
            state: State::Pending,
            value: operation.clone(),
            keys: disk::Record::KEYS,
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
        record.state = State::Confirmed;
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
            State::Confirmed => record.state = State::Done,
        }
        self.save(&tx)
    }

    /// `compositor_ended`: the compositor of this journal's scope exited, and every record ended with it.
    pub fn compositor_ended(&mut self) -> Result<(), Error> {
        let tx = self.begin()?;
        for record in &mut self.records {
            record.state = State::Done;
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
        let id = uuid4().unwrap();
        let groups: Vec<usize> = id.split('-').map(str::len).collect();
        assert_eq!(groups, [8, 4, 4, 4, 12]);
        assert!(&id[14..15] == "4" && "89ab".contains(&id[19..20]));
        assert!(token("XR-1").is_ok() && token("-a").is_err());
    }

    #[test]
    fn broker_equality_ignores_key_order() {
        let a = json::parse_strict(br#"{"a": 1, "b": {"c": "x", "d": [1]}}"#).unwrap();
        let b = json::parse_strict(br#"{"b": {"d": [1], "c": "x"}, "a": 1}"#).unwrap();
        let c = json::parse_strict(br#"{"b": {"d": [2], "c": "x"}, "a": 1}"#).unwrap();
        assert!(same(&a, &b) && !same(&a, &c));
    }
}
