//! The clean pose feed: one seqlock slot of [`PoseRecord`] in POSIX shared memory,
//! `/dev/shm/xr-luma-pose`.
//!
//! Mirrors `luma::rt::SeqlockSlot`, `ShmMapping`, `SeqlockWriter` and `SeqlockReader`
//! (`spike/driver/include/luma/rt.hpp`, `src/rt.cpp`), byte for byte:
//!
//! ```text
//! offset  0  header  u64 magic "LUMRST1" (written last), u32 version 1, u32 payload bytes (40)
//! offset 64  slot    u64 seq (0 never written, odd while writing), 5 x u64 payload words
//! size 128
//! ```
//!
//! One writer, any number of readers. The writer never waits: a reader preempted or killed
//! mid-read holds nothing. Readers retry on a collision and give up on a writer that died mid-write.
//! The writer holds an exclusive `flock` for life, so a second writer is refused. An object of
//! another layout is replaced by a writer (readers still mapped to the old one see it go stale) and
//! refused by a reader. The object is mode 0600 and must belong to this user.

use core::mem::{align_of, offset_of, size_of};
use core::ptr::NonNull;
use core::sync::atomic::{AtomicU64, Ordering};
use std::ffi::CString;
use std::io;
use std::os::fd::{AsRawFd, FromRawFd, OwnedFd};

/// The shared memory object name, as `luma::rt::kPoseShmName`.
pub const POSE_SHM_NAME: &str = "/xr-luma-pose";
/// `PoseRecord::flags` bit: fusion is calibrated.
pub const POSE_CALIBRATED: u32 = 1 << 0;

/// One pose sample. A fixed wire format (`luma::rt::PoseRecord`).
#[repr(C)]
#[derive(Clone, Copy, Debug, PartialEq)]
pub struct PoseRecord {
    pub w: f32,
    pub x: f32,
    pub y: f32,
    pub z: f32,
    /// Sample time, CLOCK_MONOTONIC_RAW.
    pub t_raw_ns: u64,
    /// Add to `t_raw_ns` for CLOCK_MONOTONIC.
    pub raw_to_mono_ns: i64,
    pub seq: u32,
    pub flags: u32,
}

impl Default for PoseRecord {
    fn default() -> Self {
        Self {
            w: 1.0,
            x: 0.0,
            y: 0.0,
            z: 0.0,
            t_raw_ns: 0,
            raw_to_mono_ns: 0,
            seq: 0,
            flags: 0,
        }
    }
}

const WORDS: usize = size_of::<PoseRecord>().div_ceil(8);

/// `SeqlockSlot<PoseRecord>`.
#[repr(C, align(64))]
struct Slot {
    seq: AtomicU64,
    words: [AtomicU64; WORDS],
}

const SHM_MAGIC: u64 = 0x0031_5453_524D_554C; // "LUMRST1": changes with the layout of the header
const SHM_VERSION: u32 = 1;
const SLOT_OFFSET: usize = 64;
const TOTAL: usize = SLOT_OFFSET + size_of::<Slot>();
const PAYLOAD_BYTES: u32 = size_of::<PoseRecord>() as u32;
const READ_TRIES: usize = 4096;

// The C++ layout, asserted here; tests/cpp_interop.rs checks the same numbers against g++.
const _: () = {
    assert!(size_of::<PoseRecord>() == 40);
    assert!(align_of::<PoseRecord>() == 8);
    assert!(offset_of!(PoseRecord, w) == 0);
    assert!(offset_of!(PoseRecord, x) == 4);
    assert!(offset_of!(PoseRecord, y) == 8);
    assert!(offset_of!(PoseRecord, z) == 12);
    assert!(offset_of!(PoseRecord, t_raw_ns) == 16);
    assert!(offset_of!(PoseRecord, raw_to_mono_ns) == 24);
    assert!(offset_of!(PoseRecord, seq) == 32);
    assert!(offset_of!(PoseRecord, flags) == 36);
    assert!(WORDS == 5);
    assert!(size_of::<Slot>() == 64);
    assert!(offset_of!(Slot, seq) == 0);
    assert!(offset_of!(Slot, words) == 8);
    assert!(TOTAL == 128);
};

impl Slot {
    fn store(&self, value: &PoseRecord) {
        // SAFETY: PoseRecord is repr(C), 40 bytes, no padding; [u64; 5] is 40 bytes.
        let raw: [u64; WORDS] = unsafe { core::mem::transmute(*value) };
        let s = self.seq.load(Ordering::Relaxed);
        self.seq.store(s.wrapping_add(1), Ordering::Relaxed);
        // Release on each word keeps the odd sequence ahead of it.
        for (word, v) in self.words.iter().zip(raw) {
            word.store(v, Ordering::Release);
        }
        self.seq.store(s.wrapping_add(2), Ordering::Release);
    }

    /// The record and how many tries failed before it (the C++ feed's `counts.retries`: a try that met the sequence
    /// odd or changed mid-copy). Giving up counts every try; never written counts none.
    fn load(&self) -> (Option<PoseRecord>, u32) {
        let mut retries = 0;
        for _ in 0..READ_TRIES {
            let before = self.seq.load(Ordering::Acquire);
            if before == 0 {
                return (None, retries);
            }
            if before & 1 != 0 {
                core::hint::spin_loop();
                retries += 1;
                continue;
            }
            // Acquire on each word keeps the second sequence load after it.
            let raw: [u64; WORDS] = core::array::from_fn(|i| self.words[i].load(Ordering::Acquire));
            if self.seq.load(Ordering::Relaxed) == before {
                // SAFETY: every bit pattern is a valid PoseRecord (floats and integers only).
                let r = unsafe { core::mem::transmute::<[u64; WORDS], PoseRecord>(raw) };
                return (Some(r), retries);
            }
            retries += 1;
        }
        (None, retries)
    }
}

/// A mapped object. The writer keeps the descriptor, and with it the lock.
struct Mapping {
    addr: NonNull<u8>,
    _fd: Option<OwnedFd>,
}

// SAFETY: the mapping is only touched through atomics (Slot) or once before sharing (header).
unsafe impl Send for Mapping {}
unsafe impl Sync for Mapping {}

impl Drop for Mapping {
    fn drop(&mut self) {
        // SAFETY: addr/TOTAL came from a successful mmap and nothing borrows it past self.
        unsafe { libc::munmap(self.addr.as_ptr().cast(), TOTAL) };
    }
}

impl Mapping {
    fn slot(&self) -> &Slot {
        // SAFETY: TOTAL bytes are mapped, page aligned, so SLOT_OFFSET is 64-byte aligned.
        unsafe { &*self.addr.as_ptr().add(SLOT_OFFSET).cast::<Slot>() }
    }
}

fn last_error(what: &str) -> io::Error {
    let e = io::Error::last_os_error();
    io::Error::new(e.kind(), format!("{what}: {e}"))
}

fn c_name(name: &str) -> io::Result<CString> {
    if !name.starts_with('/') || name[1..].contains('/') {
        return Err(io::Error::new(
            io::ErrorKind::InvalidInput,
            format!("bad shm name {name:?}"),
        ));
    }
    CString::new(name)
        .map_err(|_| io::Error::new(io::ErrorKind::InvalidInput, "shm name has a NUL"))
}

fn shm_open(name: &CString, flags: libc::c_int, mode: libc::mode_t) -> io::Result<OwnedFd> {
    // SAFETY: name is NUL-terminated.
    let fd = unsafe { libc::shm_open(name.as_ptr(), flags | libc::O_CLOEXEC, mode) };
    if fd < 0 {
        return Err(last_error(&format!("shm_open {}", name.to_string_lossy())));
    }
    // SAFETY: fd is a fresh descriptor we own.
    Ok(unsafe { OwnedFd::from_raw_fd(fd) })
}

/// Not somebody else's file planted under our name. Returns the size.
fn object_is_ours(fd: &OwnedFd) -> io::Result<usize> {
    // SAFETY: st is plain data; fstat fills it.
    let mut st: libc::stat = unsafe { core::mem::zeroed() };
    if unsafe { libc::fstat(fd.as_raw_fd(), &mut st) } != 0 {
        return Err(last_error("shm stat"));
    }
    if st.st_mode & libc::S_IFMT != libc::S_IFREG || st.st_uid != unsafe { libc::geteuid() } {
        return Err(io::Error::new(
            io::ErrorKind::PermissionDenied,
            "shm object is not this user's",
        ));
    }
    Ok(st.st_size as usize)
}

fn header_matches(fd: &OwnedFd) -> bool {
    let mut h = [0u8; 16];
    // SAFETY: h is 16 writable bytes.
    let n = unsafe { libc::pread(fd.as_raw_fd(), h.as_mut_ptr().cast(), h.len(), 0) };
    n == 16
        && u64::from_ne_bytes(h[0..8].try_into().unwrap()) == SHM_MAGIC
        && u32::from_ne_bytes(h[8..12].try_into().unwrap()) == SHM_VERSION
        && u32::from_ne_bytes(h[12..16].try_into().unwrap()) == PAYLOAD_BYTES
}

fn lock(fd: &OwnedFd, name: &str) -> io::Result<()> {
    // SAFETY: plain syscall on an owned descriptor.
    if unsafe { libc::flock(fd.as_raw_fd(), libc::LOCK_EX | libc::LOCK_NB) } != 0 {
        return Err(io::Error::new(
            io::ErrorKind::AddrInUse,
            format!("{name} already has a writer"),
        ));
    }
    Ok(())
}

fn map(fd: &OwnedFd, prot: libc::c_int) -> io::Result<NonNull<u8>> {
    // SAFETY: a fresh shared mapping of TOTAL bytes of an object at least that size.
    let addr = unsafe {
        libc::mmap(
            core::ptr::null_mut(),
            TOTAL,
            prot,
            libc::MAP_SHARED | libc::MAP_POPULATE,
            fd.as_raw_fd(),
            0,
        )
    };
    if addr == libc::MAP_FAILED {
        return Err(last_error("shm map"));
    }
    Ok(NonNull::new(addr.cast()).expect("mmap returned null"))
}

/// The only writer of a pose seqlock object.
pub struct PoseWriter {
    map: Mapping,
}

impl PoseWriter {
    /// Opens or creates `name` (e.g. [`POSE_SHM_NAME`]) and takes the writer lock. A sequence left
    /// odd by a writer killed mid-write is repaired.
    pub fn create(name: &str) -> io::Result<Self> {
        let cname = c_name(name)?;
        let mut fd = shm_open(&cname, libc::O_RDWR | libc::O_CREAT, 0o600)?;
        let size = object_is_ours(&fd)?;
        lock(&fd, name)?;
        let mut fresh = false;
        if size != TOTAL || !header_matches(&fd) {
            if size != 0 {
                // Another layout or an unfinished one: a new object, so readers still mapped to
                // the old one see its last value go stale instead of a SIGBUS or garbage.
                // SAFETY: NUL-terminated name.
                unsafe { libc::shm_unlink(cname.as_ptr()) };
                drop(fd);
                fd = shm_open(&cname, libc::O_RDWR | libc::O_CREAT | libc::O_EXCL, 0o600)?;
                lock(&fd, name)?;
            }
            // SAFETY: plain syscall on an owned descriptor.
            if unsafe { libc::ftruncate(fd.as_raw_fd(), TOTAL as libc::off_t) } != 0 {
                return Err(last_error("shm size"));
            }
            fresh = true;
        }
        let addr = map(&fd, libc::PROT_READ | libc::PROT_WRITE)?;
        // SAFETY: best effort, and small enough for the default limit.
        unsafe { libc::mlock(addr.as_ptr().cast(), TOTAL) };
        let map = Mapping {
            addr,
            _fd: Some(fd),
        };
        if fresh {
            // ftruncate zeroed the slot. Header: version and size first, magic last.
            // SAFETY: the header is ours alone until the magic is published.
            unsafe {
                let base = map.addr.as_ptr();
                base.add(8).cast::<u32>().write(SHM_VERSION);
                base.add(12).cast::<u32>().write(PAYLOAD_BYTES);
                (*base.cast::<AtomicU64>()).store(SHM_MAGIC, Ordering::Release);
            }
        } else {
            let seq = &map.slot().seq;
            let s = seq.load(Ordering::Acquire);
            if s & 1 != 0 {
                seq.store(s.wrapping_add(1), Ordering::Release); // the last writer died mid-write
            }
        }
        Ok(Self { map })
    }

    /// Hot: no allocation, no system call, never waits.
    pub fn publish(&self, record: &PoseRecord) {
        self.map.slot().store(record);
    }
}

/// A reader of a pose seqlock object. Any number may exist.
pub struct PoseReader {
    map: Mapping,
}

impl PoseReader {
    /// Refuses an object that is missing, not this user's, or of another layout.
    pub fn open(name: &str) -> io::Result<Self> {
        let fd = shm_open(&c_name(name)?, libc::O_RDONLY, 0)?;
        let size = object_is_ours(&fd)?;
        if size != TOTAL || !header_matches(&fd) {
            return Err(io::Error::new(
                io::ErrorKind::InvalidData,
                format!("{name} has another layout"),
            ));
        }
        let addr = map(&fd, libc::PROT_READ)?;
        Ok(Self {
            map: Mapping { addr, _fd: None },
        })
    }

    /// `None` if nothing was ever written, or the writer died mid-write. Never hangs.
    pub fn read(&self) -> Option<PoseRecord> {
        self.read_counted().0
    }

    /// [`read`](Self::read), and how many copies it retried because the writer was mid-write: 0 for a clean read,
    /// up to 4,096 for one that gave up on a sequence held odd.
    pub fn read_counted(&self) -> (Option<PoseRecord>, u32) {
        self.map.slot().load()
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    use std::sync::Arc;
    use std::sync::atomic::AtomicBool;

    struct Name(String);
    impl Name {
        fn new(tag: &str) -> Self {
            Self(format!("/xr-pose-test-{}-{tag}", std::process::id()))
        }
    }
    impl Drop for Name {
        fn drop(&mut self) {
            let c = CString::new(self.0.as_str()).unwrap();
            unsafe { libc::shm_unlink(c.as_ptr()) };
        }
    }

    /// Every field from one counter, so a mix of two writes is detectable.
    fn record(i: u64) -> PoseRecord {
        let lo = i as u32;
        PoseRecord {
            w: f32::from_bits(lo & 0x3fff_ffff),
            x: f32::from_bits(lo.wrapping_add(1) & 0x3fff_ffff),
            y: f32::from_bits(lo.wrapping_add(2) & 0x3fff_ffff),
            z: f32::from_bits(lo.wrapping_add(3) & 0x3fff_ffff),
            t_raw_ns: i,
            raw_to_mono_ns: -(i as i64),
            seq: lo,
            flags: lo ^ 0x5a5a_5a5a,
        }
    }

    #[test]
    fn round_trip() {
        let name = Name::new("rt");
        let w = PoseWriter::create(&name.0).unwrap();
        let r = PoseReader::open(&name.0).unwrap();
        assert_eq!(r.read_counted(), (None, 0), "never written: no retries");
        w.publish(&record(42));
        assert_eq!(
            r.read_counted(),
            (Some(record(42)), 0),
            "no writer racing: no retries"
        );
    }

    #[test]
    fn object_is_private_to_the_user() {
        use std::os::unix::fs::{MetadataExt, PermissionsExt};
        let name = Name::new("mode");
        let _w = PoseWriter::create(&name.0).unwrap();
        let meta = std::fs::metadata(format!("/dev/shm{}", name.0)).unwrap();
        assert_eq!(meta.permissions().mode() & 0o777, 0o600);
        assert_eq!(meta.size(), 128);
    }

    #[test]
    fn second_writer_is_refused_until_the_first_goes() {
        let name = Name::new("two");
        let first = PoseWriter::create(&name.0).unwrap();
        let err = PoseWriter::create(&name.0).err().unwrap();
        assert!(err.to_string().contains("already has a writer"), "{err}");
        drop(first);
        PoseWriter::create(&name.0).unwrap();
    }

    #[test]
    fn reader_of_nothing_says_why() {
        let err = PoseReader::open(&Name::new("absent").0).err().unwrap();
        assert!(err.to_string().contains("shm_open"), "{err}");
    }

    #[test]
    fn bad_names_are_refused() {
        for n in ["xr-luma-pose", "/a/b", "/a\0b"] {
            assert_eq!(
                PoseWriter::create(n).err().unwrap().kind(),
                io::ErrorKind::InvalidInput
            );
        }
    }

    fn write_object(name: &str, bytes: &[u8]) {
        let c = CString::new(name).unwrap();
        let fd = shm_open(&c, libc::O_RDWR | libc::O_CREAT, 0o600).unwrap();
        let mut f = std::fs::File::from(fd);
        std::io::Write::write_all(&mut f, bytes).unwrap();
    }

    #[test]
    fn reader_refuses_and_writer_replaces_another_layout() {
        let name = Name::new("layout");
        // A valid header for a 24-byte payload (C++ SeqlockSlot<std::array<uint64_t, 3>>).
        let mut other = vec![0u8; 128];
        other[0..8].copy_from_slice(&SHM_MAGIC.to_ne_bytes());
        other[8..12].copy_from_slice(&1u32.to_ne_bytes());
        other[12..16].copy_from_slice(&24u32.to_ne_bytes());
        write_object(&name.0, &other);
        let err = PoseReader::open(&name.0).err().unwrap();
        assert!(err.to_string().contains("another layout"), "{err}");
        let w = PoseWriter::create(&name.0).unwrap();
        w.publish(&record(5));
        assert_eq!(PoseReader::open(&name.0).unwrap().read(), Some(record(5)));
    }

    #[test]
    fn new_writer_repairs_a_sequence_left_odd() {
        let name = Name::new("odd");
        PoseWriter::create(&name.0).unwrap().publish(&record(1));
        let r = PoseReader::open(&name.0).unwrap();
        // Poke the real layout: one publish leaves seq 2 at offset 64.
        let c = CString::new(name.0.as_str()).unwrap();
        let fd = shm_open(&c, libc::O_RDWR, 0).unwrap();
        let addr = map(&fd, libc::PROT_READ | libc::PROT_WRITE).unwrap();
        let seq = unsafe { &*addr.as_ptr().add(SLOT_OFFSET).cast::<AtomicU64>() };
        assert_eq!(seq.load(Ordering::SeqCst), 2);
        seq.store(3, Ordering::SeqCst);
        assert_eq!(r.read(), None, "odd forever reads as nothing, and returns");
        assert_eq!(
            r.read_counted(),
            (None, READ_TRIES as u32),
            "every try met the writer mid-write"
        );
        let w = PoseWriter::create(&name.0).unwrap();
        assert_eq!(seq.load(Ordering::SeqCst), 4);
        w.publish(&record(9));
        assert_eq!(r.read(), Some(record(9)), "the old reader keeps working");
        unsafe { libc::munmap(addr.as_ptr().cast(), TOTAL) };
    }

    // A writer publishing flat out bumps the sequence mid-copy sooner or later: the read that copies again says so,
    // and still returns one whole write.
    #[test]
    fn retries_are_counted_against_a_racing_writer() {
        let name = Name::new("retry");
        let writer = PoseWriter::create(&name.0).unwrap();
        writer.publish(&record(1));
        let r = PoseReader::open(&name.0).unwrap();
        let stop = Arc::new(AtomicBool::new(false));
        let w = {
            let stop = stop.clone();
            std::thread::spawn(move || {
                let mut i = 1u64;
                while !stop.load(Ordering::Relaxed) {
                    i += 1;
                    writer.publish(&record(i));
                }
            })
        };
        let deadline = std::time::Instant::now() + std::time::Duration::from_secs(5);
        let mut retried = 0u64;
        while retried == 0 && std::time::Instant::now() < deadline {
            let (p, n) = r.read_counted();
            if let Some(p) = p {
                assert_eq!(p, record(p.t_raw_ns), "torn read");
            }
            retried += u64::from(n);
        }
        stop.store(true, Ordering::Relaxed);
        w.join().unwrap();
        assert!(retried > 0, "no read met the writer mid-write in 5 s");
    }

    // Torn-read stress through real shared memory: one writer at full speed, readers on their own
    // mappings that check every record is one whole write and never goes backwards.
    #[test]
    fn readers_never_see_a_torn_write() {
        let name = Name::new("torn");
        let writer = PoseWriter::create(&name.0).unwrap();
        writer.publish(&record(1));
        let stop = Arc::new(AtomicBool::new(false));
        let readers: Vec<_> = (0..3)
            .map(|_| {
                let r = PoseReader::open(&name.0).unwrap();
                std::thread::spawn(move || {
                    let (mut ok, mut last) = (0u64, 0u64);
                    let deadline =
                        std::time::Instant::now() + std::time::Duration::from_millis(1500);
                    while std::time::Instant::now() < deadline {
                        if let Some(p) = r.read() {
                            assert_eq!(p, record(p.t_raw_ns), "torn read");
                            assert!(p.t_raw_ns >= last, "went backwards");
                            last = p.t_raw_ns;
                            ok += 1;
                        }
                    }
                    ok
                })
            })
            .collect();
        let w = {
            let stop = stop.clone();
            std::thread::spawn(move || {
                let mut i = 1u64;
                while !stop.load(Ordering::Relaxed) {
                    i += 1;
                    writer.publish(&record(i));
                }
                i
            })
        };
        let reads: Vec<u64> = readers.into_iter().map(|r| r.join().unwrap()).collect();
        stop.store(true, Ordering::Relaxed);
        let writes = w.join().unwrap();
        assert!(writes > 1000, "writer barely ran: {writes}");
        assert!(
            reads.iter().all(|&n| n > 0),
            "a reader never got a record: {reads:?}"
        );
    }
}
