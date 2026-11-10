//! A recording to run instead of the glasses, and the link that plays it. A port of the replay half of
//! `spike/driver/src/daemon.cpp`.
//!
//! `load_capture` reads the plain "LUMACAP0" container the tests write: after the magic, per record a little-endian u64
//! t_ns, u32 size, u8 kind, then the payload. The real `.lcap` capture ("LUMACAP1", docs/capture-format.md) is not read
//! here yet: it is refused with a clear reason, so nothing replays a half-understood file.

use crate::daemon::{Link, ReadResult};
use std::cell::Cell;
use std::fs;
use std::io;
use std::path::Path;
use std::rc::Rc;
use std::time::Duration;

#[repr(u8)]
#[derive(Clone, Copy, Debug, PartialEq, Eq)]
pub enum CaptureKind {
    FromDevice = 0,
    ToDevice = 1,
    DeviceGone = 2,
    DeviceBack = 3,
}

impl CaptureKind {
    fn from_byte(b: u8) -> Option<CaptureKind> {
        Some(match b {
            0 => CaptureKind::FromDevice,
            1 => CaptureKind::ToDevice,
            2 => CaptureKind::DeviceGone,
            3 => CaptureKind::DeviceBack,
            _ => return None,
        })
    }
}

#[derive(Clone, Debug, PartialEq, Eq)]
pub struct CaptureRecord {
    pub t_ns: u64,
    pub kind: CaptureKind,
    pub payload: Vec<u8>,
}

const MAGIC: &[u8; 8] = b"LUMACAP0";
const MAX_PAYLOAD: usize = 1 << 16;

/// `Err` says what is wrong, naming the file.
pub fn load_capture(path: &Path) -> Result<Vec<CaptureRecord>, String> {
    let shown = path.display();
    let bytes = fs::read(path).map_err(|_| format!("cannot open {shown}"))?;
    if bytes.len() < MAGIC.len() {
        return Err(format!("not a capture: {shown}"));
    }
    let (magic, mut rest) = bytes.split_at(MAGIC.len());
    if magic == b"LUMACAP1" {
        return Err(format!(
            "LUMACAP1 captures are not read by the Rust driver yet: {shown}"
        ));
    }
    if magic != MAGIC {
        return Err(format!("not a capture: {shown}"));
    }
    let mut records: Vec<CaptureRecord> = Vec::new();
    while !rest.is_empty() {
        if rest.len() < 13 {
            return Err(format!("capture cut short in a record header: {shown}"));
        }
        let t_ns = u64::from_le_bytes(rest[..8].try_into().unwrap());
        let size = u32::from_le_bytes(rest[8..12].try_into().unwrap()) as usize;
        let kind = CaptureKind::from_byte(rest[12]);
        rest = &rest[13..];
        let Some(kind) = kind.filter(|_| size <= MAX_PAYLOAD) else {
            return Err(format!("capture record invalid: {shown}"));
        };
        if rest.len() < size {
            return Err(format!("capture cut short in a payload: {shown}"));
        }
        let (payload, after) = rest.split_at(size);
        rest = after;
        if records.last().is_some_and(|last| t_ns < last.t_ns) {
            return Err(format!("capture goes back in time: {shown}"));
        }
        records.push(CaptureRecord {
            t_ns,
            kind,
            payload: payload.to_vec(),
        });
    }
    Ok(records)
}

pub fn save_capture(path: &Path, records: &[CaptureRecord]) -> io::Result<()> {
    let mut out = Vec::with_capacity(
        MAGIC.len() + records.iter().map(|r| 13 + r.payload.len()).sum::<usize>(),
    );
    out.extend_from_slice(MAGIC);
    for r in records {
        out.extend_from_slice(&r.t_ns.to_le_bytes());
        out.extend_from_slice(&(r.payload.len() as u32).to_le_bytes());
        out.push(r.kind as u8);
        out.extend_from_slice(&r.payload);
    }
    fs::write(path, out)
}

/// Plays a capture on a simulated clock (`clock`), so its silences and drops trip the same watchdog as a real one and a
/// long capture runs in moments. `realtime` also sleeps through the recorded gaps. Commands are counted and go nowhere:
/// a replay never touches a USB device.
pub struct ReplayLink {
    records: Vec<CaptureRecord>,
    next: usize,
    now: Rc<Cell<u64>>,
    commands: u64,
    realtime: bool,
    absent: bool,
}

impl ReplayLink {
    pub fn new(records: Vec<CaptureRecord>, realtime: bool) -> Self {
        let start = records.first().map_or(0, |r| r.t_ns);
        ReplayLink {
            records,
            next: 0,
            now: Rc::new(Cell::new(start)),
            commands: 0,
            realtime,
            absent: false,
        }
    }

    /// The simulated clock, for the daemon's `now_ns` while the daemon holds the link.
    pub fn clock(&self) -> Rc<Cell<u64>> {
        Rc::clone(&self.now)
    }

    pub fn now_ns(&self) -> u64 {
        self.now.get()
    }

    pub fn commands_swallowed(&self) -> u64 {
        self.commands
    }

    fn advance_to(&mut self, t: u64) {
        let now = self.now.get();
        if t <= now {
            return;
        }
        if self.realtime {
            std::thread::sleep(Duration::from_nanos(t - now));
        }
        self.now.set(t);
    }

    fn skip_commands(&mut self) {
        while self.next < self.records.len()
            && self.records[self.next].kind == CaptureKind::ToDevice
        {
            self.next += 1;
        }
    }

    fn deadline(&self, timeout: Duration) -> u64 {
        self.now.get() + timeout.as_nanos() as u64
    }
}

impl Link for ReplayLink {
    fn open(&mut self) -> Result<(), String> {
        self.skip_commands();
        if self.absent || self.next >= self.records.len() {
            return Err("the capture has the device away".to_owned());
        }
        Ok(())
    }

    fn read(&mut self, into: &mut [u8], timeout: Duration) -> ReadResult {
        self.skip_commands();
        let Some(r) = self.records.get(self.next) else {
            return ReadResult::END;
        };
        let (t_ns, kind) = (r.t_ns, r.kind);
        let deadline = self.deadline(timeout);
        if t_ns > deadline {
            self.advance_to(deadline);
            return ReadResult::TIMEOUT;
        }
        self.advance_to(t_ns);
        self.next += 1;
        match kind {
            CaptureKind::DeviceGone => {
                self.absent = true;
                ReadResult::GONE
            }
            CaptureKind::DeviceBack => ReadResult::TIMEOUT,
            _ => {
                let payload = &self.records[self.next - 1].payload;
                let n = payload.len().min(into.len());
                into[..n].copy_from_slice(&payload[..n]);
                ReadResult::data(n, t_ns)
            }
        }
    }

    fn write(&mut self, _bytes: &[u8]) -> bool {
        self.commands += 1;
        true
    }

    fn close(&mut self) {}

    fn wait_for_device(&mut self, timeout: Duration) -> bool {
        let deadline = self.deadline(timeout);
        while self.absent {
            self.skip_commands();
            let Some(r) = self.records.get(self.next) else {
                break;
            };
            if r.t_ns > deadline {
                break;
            }
            if r.kind == CaptureKind::DeviceBack {
                let t = r.t_ns;
                self.advance_to(t);
                self.next += 1;
                self.absent = false;
                return true;
            }
            self.next += 1; // anything else recorded while the device was away cannot have come from it
        }
        self.advance_to(deadline);
        false
    }

    fn exhausted(&self) -> bool {
        self.next >= self.records.len()
    }
}
