//! Private files: a directory opened one component at a time without following links, files read
//! from it without following links, and atomic private writes into it.
//!
//! These are the rules `ipc.private_dir`, `ipc.read_private_json`, `journal._safe_file`,
//! `runner._write_result` and `Journal._save` keep, in one place:
//!
//! - The directory: every path component opened `O_DIRECTORY | O_NOFOLLOW`, the leaf owned by us
//!   and mode 0700 (made 0700 when `create` is asked).
//! - A read: `O_NOFOLLOW | O_NONBLOCK` relative to the directory, then a regular file with one link,
//!   and when `private`, owned by us and mode 0600. At most `limit` bytes.
//! - A write: a new temporary name created `O_EXCL | O_NOFOLLOW` mode 0600 (and `fchmod` 0600, so the
//!   umask cannot widen it), the bytes written and `fsync`ed, renamed over the target, then the
//!   directory `fsync`ed. The rename replaces a link at the target; it never writes through one.
//!   [`PrivateDir::create`] renames with `RENAME_NOREPLACE`, for files that must not already exist
//!   (the consent).

use std::io;
use std::os::fd::{AsFd, OwnedFd};
use std::path::{Component, Path};

use rustix::fs::{self as rfs, AtFlags, FileType, Mode, OFlags, RenameFlags};
use rustix::io::Errno;

#[cfg(test)]
thread_local! {
    /// The write steps this thread took, in order, so tests can prove the sequence.
    pub(crate) static TRACE: std::cell::RefCell<Vec<&'static str>> = const { std::cell::RefCell::new(Vec::new()) };
}

fn step(_name: &'static str) {
    #[cfg(test)]
    TRACE.with(|t| t.borrow_mut().push(_name));
}

fn denied(message: &str) -> io::Error {
    io::Error::new(io::ErrorKind::PermissionDenied, message)
}

fn invalid(message: &str) -> io::Error {
    io::Error::new(io::ErrorKind::InvalidInput, message)
}

/// A plain file name inside the directory: no separator, not `.` or `..`, not empty.
fn file_name(name: &str) -> io::Result<&str> {
    if name.is_empty() || name == "." || name == ".." || name.contains('/') || name.contains('\0') {
        return Err(invalid("unsafe file name"));
    }
    Ok(name)
}

/// An owned, mode 0700 directory, opened without following any link on its path.
#[derive(Debug)]
pub struct PrivateDir {
    fd: OwnedFd,
}

impl PrivateDir {
    /// `ipc.private_dir`: opens `path` (made absolute) from `/`, one component at a time. With
    /// `create`, the last component is made mode 0700 if it does not exist.
    pub fn open(path: &Path, create: bool) -> io::Result<PrivateDir> {
        let path = std::path::absolute(path)?;
        let parts: Vec<_> = path.components().skip(1).collect();
        let dir_flags = OFlags::RDONLY | OFlags::DIRECTORY | OFlags::CLOEXEC;
        let mut fd = rfs::open("/", dir_flags, Mode::empty())?;
        for (i, part) in parts.iter().enumerate() {
            let Component::Normal(part) = part else { return Err(invalid("unsafe path")) };
            if create && i == parts.len() - 1 {
                match rfs::mkdirat(&fd, *part, Mode::from_raw_mode(0o700)) {
                    Ok(()) | Err(Errno::EXIST) => {}
                    Err(e) => return Err(e.into()),
                }
            }
            fd = rfs::openat(&fd, *part, dir_flags | OFlags::NOFOLLOW, Mode::empty())?;
        }
        let st = rfs::fstat(&fd)?;
        if st.st_uid != rustix::process::getuid().as_raw() || st.st_mode & 0o7777 != 0o700 {
            return Err(denied("directory must be owned and mode 0700"));
        }
        Ok(PrivateDir { fd })
    }

    /// The file `name`, at most `limit` bytes. Refuses a link, anything not a regular single-link
    /// file, and when `private`, a file not owned by us with mode 0600.
    pub fn read(&self, name: &str, limit: usize, private: bool) -> io::Result<Vec<u8>> {
        let flags = OFlags::RDONLY | OFlags::NOFOLLOW | OFlags::NONBLOCK | OFlags::CLOEXEC;
        let fd = rfs::openat(&self.fd, file_name(name)?, flags, Mode::empty())?;
        let st = rfs::fstat(&fd)?;
        if FileType::from_raw_mode(st.st_mode) != FileType::RegularFile || st.st_nlink != 1 {
            return Err(denied("must be a regular single-link file"));
        }
        if private && (st.st_uid != rustix::process::getuid().as_raw() || st.st_mode & 0o7777 != 0o600) {
            return Err(denied("must be owned regular single-link mode 0600"));
        }
        let mut raw = Vec::new();
        let mut buf = vec![0u8; (st.st_size as usize).min(limit) + 1];
        loop {
            let n = match rustix::io::read(&fd, &mut buf[..]) {
                Err(Errno::INTR) => continue,
                n => n?,
            };
            if n == 0 {
                break;
            }
            raw.extend_from_slice(&buf[..n]);
            if raw.len() > limit {
                return Err(invalid("file too large"));
            }
        }
        Ok(raw)
    }

    /// Atomically replaces `name` with `bytes`, mode 0600.
    pub fn replace(&self, name: &str, bytes: &[u8]) -> io::Result<()> {
        self.write(file_name(name)?, bytes, RenameFlags::empty())
    }

    /// Atomically writes `name`, mode 0600, refusing if it already exists.
    pub fn create(&self, name: &str, bytes: &[u8]) -> io::Result<()> {
        self.write(file_name(name)?, bytes, RenameFlags::NOREPLACE)
    }

    fn write(&self, name: &str, bytes: &[u8], rename: RenameFlags) -> io::Result<()> {
        let tmp = temporary_name(name)?;
        let flags = OFlags::WRONLY | OFlags::CREATE | OFlags::EXCL | OFlags::NOFOLLOW | OFlags::CLOEXEC;
        let fd = rfs::openat(&self.fd, tmp.as_str(), flags, Mode::from_raw_mode(0o600))?;
        step("create temporary 0600");
        let written = (|| -> io::Result<()> {
            rfs::fchmod(&fd, Mode::from_raw_mode(0o600))?;
            step("fchmod 0600");
            write_all(fd.as_fd(), bytes)?;
            step("write");
            rfs::fsync(&fd)?;
            step("fsync file");
            drop(fd);
            rfs::renameat_with(&self.fd, tmp.as_str(), &self.fd, name, rename)?;
            step("rename");
            Ok(())
        })();
        if let Err(e) = written {
            let _ = rfs::unlinkat(&self.fd, tmp.as_str(), AtFlags::empty());
            step("unlink temporary");
            return Err(e);
        }
        rfs::fsync(&self.fd)?;
        step("fsync directory");
        Ok(())
    }
}

impl AsFd for PrivateDir {
    fn as_fd(&self) -> std::os::fd::BorrowedFd<'_> {
        self.fd.as_fd()
    }
}

/// `.<name>.<16 hex>.tmp`, as `runner._write_result` names its temporary files.
fn temporary_name(name: &str) -> io::Result<String> {
    let mut random = [0u8; 8];
    let mut filled = 0;
    while filled < random.len() {
        filled += rustix::rand::getrandom(&mut random[filled..], rustix::rand::GetRandomFlags::empty())?;
    }
    let hex: String = random.iter().map(|b| format!("{b:02x}")).collect();
    Ok(format!(".{name}.{hex}.tmp"))
}

fn write_all(fd: std::os::fd::BorrowedFd<'_>, mut bytes: &[u8]) -> io::Result<()> {
    while !bytes.is_empty() {
        match rustix::io::write(fd, bytes) {
            Ok(0) => return Err(io::ErrorKind::WriteZero.into()),
            Ok(n) => bytes = &bytes[n..],
            Err(Errno::INTR) => {}
            Err(e) => return Err(e.into()),
        }
    }
    Ok(())
}

#[cfg(test)]
pub(crate) mod tests {
    use super::*;
    use std::fs;
    use std::os::unix::fs::{MetadataExt, PermissionsExt, symlink};
    use std::path::PathBuf;

    /// A fresh mode 0700 directory, removed on drop.
    pub(crate) struct Scratch(pub PathBuf);

    impl Scratch {
        pub(crate) fn new(tag: &str) -> Scratch {
            static COUNT: std::sync::atomic::AtomicUsize = std::sync::atomic::AtomicUsize::new(0);
            let n = COUNT.fetch_add(1, std::sync::atomic::Ordering::Relaxed);
            let path = std::env::temp_dir().join(format!("xr-wire-{tag}-{}-{n}", std::process::id()));
            let _ = fs::remove_dir_all(&path);
            fs::create_dir(&path).unwrap();
            fs::set_permissions(&path, fs::Permissions::from_mode(0o700)).unwrap();
            Scratch(path)
        }
        pub(crate) fn dir(&self) -> PrivateDir {
            PrivateDir::open(&self.0, false).unwrap()
        }
    }

    impl Drop for Scratch {
        fn drop(&mut self) {
            let _ = fs::remove_dir_all(&self.0);
        }
    }

    fn mode(path: &Path) -> u32 {
        fs::symlink_metadata(path).unwrap().mode() & 0o7777
    }

    pub(crate) fn names(path: &Path) -> Vec<String> {
        let mut names: Vec<_> = fs::read_dir(path).unwrap().map(|e| e.unwrap().file_name().into_string().unwrap()).collect();
        names.sort();
        names
    }

    fn trace() -> Vec<&'static str> {
        TRACE.with(|t| std::mem::take(&mut *t.borrow_mut()))
    }

    const WRITE: [&str; 6] = ["create temporary 0600", "fchmod 0600", "write", "fsync file", "rename", "fsync directory"];

    #[test]
    fn replace_is_private_fsynced_and_renamed() {
        let s = Scratch::new("replace");
        let dir = s.dir();
        trace();
        dir.replace("a.json", b"one").unwrap();
        assert_eq!(trace(), WRITE);
        dir.replace("a.json", b"two").unwrap();
        assert_eq!(trace(), WRITE);
        assert_eq!(fs::read(s.0.join("a.json")).unwrap(), b"two");
        assert_eq!(mode(&s.0.join("a.json")), 0o600);
        assert_eq!(names(&s.0), ["a.json"], "no temporary file is left");
    }

    #[test]
    fn create_refuses_an_existing_file_and_leaves_it_alone() {
        let s = Scratch::new("create");
        let dir = s.dir();
        trace();
        dir.create("run.json", b"first").unwrap();
        assert_eq!(trace(), WRITE);
        assert_eq!(mode(&s.0.join("run.json")), 0o600);
        let err = dir.create("run.json", b"second").unwrap_err();
        assert_eq!(err.raw_os_error(), Some(Errno::EXIST.raw_os_error()));
        assert_eq!(trace(), ["create temporary 0600", "fchmod 0600", "write", "fsync file", "unlink temporary"]);
        assert_eq!(fs::read(s.0.join("run.json")).unwrap(), b"first");
        assert_eq!(names(&s.0), ["run.json"]);
    }

    #[test]
    fn a_failed_rename_removes_the_temporary_file() {
        let s = Scratch::new("fail");
        fs::create_dir(s.0.join("busy")).unwrap();
        let dir = s.dir();
        trace();
        assert!(dir.replace("busy", b"x").is_err());
        assert_eq!(trace().last(), Some(&"unlink temporary"));
        assert_eq!(names(&s.0), ["busy"]);
    }

    #[test]
    fn writes_never_follow_a_link() {
        let s = Scratch::new("link");
        let outside = Scratch::new("outside");
        fs::write(outside.0.join("victim"), b"keep").unwrap();
        symlink(outside.0.join("victim"), s.0.join("journal.json")).unwrap();
        s.dir().replace("journal.json", b"new").unwrap();
        assert_eq!(fs::read(outside.0.join("victim")).unwrap(), b"keep");
        assert!(fs::symlink_metadata(s.0.join("journal.json")).unwrap().is_file());
        assert_eq!(fs::read(s.0.join("journal.json")).unwrap(), b"new");
    }

    #[test]
    fn reads_refuse_links_shared_files_and_wrong_modes() {
        let s = Scratch::new("read");
        let dir = s.dir();
        dir.replace("ok", b"{}").unwrap();
        assert_eq!(dir.read("ok", 2, true).unwrap(), b"{}");
        assert_eq!(dir.read("ok", 1, true).unwrap_err().kind(), io::ErrorKind::InvalidInput, "over the limit");
        symlink(s.0.join("ok"), s.0.join("link")).unwrap();
        assert_eq!(dir.read("link", 99, true).unwrap_err().raw_os_error(), Some(Errno::LOOP.raw_os_error()));
        fs::write(s.0.join("wide"), b"{}").unwrap();
        fs::set_permissions(s.0.join("wide"), fs::Permissions::from_mode(0o644)).unwrap();
        assert_eq!(dir.read("wide", 99, true).unwrap_err().kind(), io::ErrorKind::PermissionDenied);
        assert_eq!(dir.read("wide", 99, false).unwrap(), b"{}", "pins Python reads without the mode check");
        fs::hard_link(s.0.join("ok"), s.0.join("second")).unwrap();
        assert_eq!(dir.read("ok", 99, true).unwrap_err().kind(), io::ErrorKind::PermissionDenied);
        fs::create_dir(s.0.join("sub")).unwrap();
        assert!(dir.read("sub", 99, false).is_err());
        for bad in ["", ".", "..", "a/b", "../ok"] {
            assert_eq!(dir.read(bad, 99, false).unwrap_err().kind(), io::ErrorKind::InvalidInput, "{bad:?}");
            assert!(dir.replace(bad, b"x").is_err(), "{bad:?}");
        }
    }

    #[test]
    fn the_directory_is_private_and_reached_without_links() {
        let s = Scratch::new("dir");
        let made = s.0.join("state");
        PrivateDir::open(&made, true).unwrap();
        assert_eq!(mode(&made), 0o700);
        PrivateDir::open(&made, true).unwrap();
        symlink(&made, s.0.join("alias")).unwrap();
        assert_eq!(PrivateDir::open(&s.0.join("alias"), false).unwrap_err().raw_os_error(), Some(Errno::NOTDIR.raw_os_error()));
        fs::create_dir(s.0.join("real")).unwrap();
        fs::create_dir(s.0.join("real/leaf")).unwrap();
        fs::set_permissions(s.0.join("real/leaf"), fs::Permissions::from_mode(0o700)).unwrap();
        symlink(s.0.join("real"), s.0.join("hop")).unwrap();
        assert!(PrivateDir::open(&s.0.join("hop/leaf"), false).is_err(), "a link in the middle");
        PrivateDir::open(&s.0.join("real/leaf"), false).unwrap();
        fs::set_permissions(s.0.join("real/leaf"), fs::Permissions::from_mode(0o755)).unwrap();
        assert_eq!(PrivateDir::open(&s.0.join("real/leaf"), false).unwrap_err().kind(), io::ErrorKind::PermissionDenied);
        assert!(PrivateDir::open(&s.0.join("real/../real/leaf"), false).is_err());
    }
}
