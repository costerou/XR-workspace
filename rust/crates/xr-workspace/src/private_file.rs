//! Private files and identifiers, as `tools/native_spike/ipc.py` checks them.
//!
//! `read_private_json` is `ipc.read_private_json`: an owned, regular, single-link, mode 0600 file opened
//! no-follow, at most `limit` bytes, parsed as strict JSON (no repeated keys; NaN and Infinity are not JSON).
//! The strict parser here is local until `xr-wire` carries the shared one; it then replaces this one.

use std::fmt;
use std::fs::File;
use std::io::{self, Read};
use std::os::unix::fs::{MetadataExt, OpenOptionsExt};
use std::path::Path;

use serde::de::{self, Deserialize, Deserializer, MapAccess, SeqAccess, Visitor};
use serde_json::{Map, Value};

/// The limit `ipc.read_private_json` uses by default (consents, progress and result files).
pub const DEFAULT_LIMIT: u64 = 16384;

pub fn invalid(message: impl Into<String>) -> io::Error {
    io::Error::new(io::ErrorKind::InvalidData, message.into())
}

/// `ipc.token`: `[A-Za-z0-9][A-Za-z0-9_.:-]{0,127}`. Run ids and sessions name units, folders and files.
pub fn token(value: &str) -> io::Result<&str> {
    let bytes = value.as_bytes();
    let ok = !bytes.is_empty()
        && bytes.len() <= 128
        && bytes[0].is_ascii_alphanumeric()
        && bytes[1..]
            .iter()
            .all(|b| b.is_ascii_alphanumeric() || b"_.:-".contains(b));
    if ok {
        Ok(value)
    } else {
        Err(invalid("invalid identifier"))
    }
}

/// The file at `path`, checked and parsed as `ipc.read_private_json` does.
pub fn read_private_json(path: &Path, limit: u64) -> io::Result<Value> {
    let mut file = File::options()
        .read(true)
        .custom_flags(libc::O_NOFOLLOW | libc::O_NONBLOCK | libc::O_CLOEXEC)
        .open(path)?;
    let meta = file.metadata()?;
    // SAFETY: getuid has no preconditions and cannot fail.
    let uid = unsafe { libc::getuid() };
    if !meta.file_type().is_file()
        || meta.uid() != uid
        || meta.mode() & 0o7777 != 0o600
        || meta.nlink() != 1
    {
        return Err(io::Error::new(
            io::ErrorKind::PermissionDenied,
            "consent must be owned regular single-link mode 0600",
        ));
    }
    let mut raw = Vec::with_capacity(meta.len().min(limit) as usize + 1);
    (&mut file).take(limit + 1).read_to_end(&mut raw)?;
    if raw.len() as u64 > limit {
        return Err(invalid("consent too large"));
    }
    strict_json(&raw)
}

/// `ipc.private_dir`: every component opened no-follow, the leaf owned by this user and mode 0700.
pub fn private_dir(path: &Path) -> io::Result<File> {
    use std::os::fd::{AsRawFd, FromRawFd};
    use std::os::unix::ffi::OsStrExt;
    if !path.is_absolute() {
        return Err(invalid("unsafe path"));
    }
    let open_at = |dir: libc::c_int, name: &std::ffi::CStr| -> io::Result<File> {
        // SAFETY: `name` is NUL-terminated and `dir` is a directory descriptor this function owns (or AT_FDCWD for "/").
        let fd = unsafe {
            libc::openat(
                dir,
                name.as_ptr(),
                libc::O_RDONLY | libc::O_DIRECTORY | libc::O_NOFOLLOW | libc::O_CLOEXEC,
            )
        };
        if fd < 0 {
            return Err(io::Error::last_os_error());
        }
        // SAFETY: `fd` was just opened and is owned by nobody else.
        Ok(unsafe { File::from_raw_fd(fd) })
    };
    let mut dir = open_at(libc::AT_FDCWD, c"/")?;
    for part in path.components().skip(1) {
        let std::path::Component::Normal(name) = part else {
            return Err(invalid("unsafe path"));
        };
        let name = std::ffi::CString::new(name.as_bytes()).map_err(|_| invalid("unsafe path"))?;
        dir = open_at(dir.as_raw_fd(), &name)?;
    }
    let meta = dir.metadata()?;
    // SAFETY: getuid has no preconditions and cannot fail.
    if meta.uid() != unsafe { libc::getuid() } || meta.mode() & 0o7777 != 0o700 {
        return Err(io::Error::new(
            io::ErrorKind::PermissionDenied,
            "directory must be owned and mode 0700",
        ));
    }
    Ok(dir)
}

/// `ipc.strict_json`: one JSON value, no repeated object key at any depth.
pub fn strict_json(raw: &[u8]) -> io::Result<Value> {
    let mut de = serde_json::Deserializer::from_slice(raw);
    let value = Strict::deserialize(&mut de).map_err(|e| invalid(e.to_string()))?;
    de.end().map_err(|e| invalid(e.to_string()))?;
    Ok(value.0)
}

struct Strict(Value);

impl<'de> Deserialize<'de> for Strict {
    fn deserialize<D: Deserializer<'de>>(deserializer: D) -> Result<Self, D::Error> {
        deserializer.deserialize_any(StrictVisitor).map(Strict)
    }
}

struct StrictVisitor;

impl<'de> Visitor<'de> for StrictVisitor {
    type Value = Value;

    fn expecting(&self, f: &mut fmt::Formatter) -> fmt::Result {
        f.write_str("a JSON value")
    }
    fn visit_bool<E>(self, v: bool) -> Result<Value, E> {
        Ok(Value::Bool(v))
    }
    fn visit_i64<E>(self, v: i64) -> Result<Value, E> {
        Ok(v.into())
    }
    fn visit_u64<E>(self, v: u64) -> Result<Value, E> {
        Ok(v.into())
    }
    fn visit_f64<E: de::Error>(self, v: f64) -> Result<Value, E> {
        serde_json::Number::from_f64(v)
            .map(Value::Number)
            .ok_or_else(|| E::custom("nonfinite number"))
    }
    fn visit_str<E>(self, v: &str) -> Result<Value, E> {
        Ok(Value::String(v.to_owned()))
    }
    fn visit_string<E>(self, v: String) -> Result<Value, E> {
        Ok(Value::String(v))
    }
    fn visit_unit<E>(self) -> Result<Value, E> {
        Ok(Value::Null)
    }
    fn visit_seq<A: SeqAccess<'de>>(self, mut seq: A) -> Result<Value, A::Error> {
        let mut items = Vec::new();
        while let Some(Strict(item)) = seq.next_element()? {
            items.push(item);
        }
        Ok(Value::Array(items))
    }
    fn visit_map<A: MapAccess<'de>>(self, mut map: A) -> Result<Value, A::Error> {
        let mut out = Map::new();
        while let Some(key) = map.next_key::<String>()? {
            let Strict(value) = map.next_value()?;
            if out.insert(key, value).is_some() {
                return Err(de::Error::custom("duplicate key"));
            }
        }
        Ok(Value::Object(out))
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    use std::os::unix::fs::PermissionsExt;

    #[test]
    fn token_matches_ipc() {
        for ok in [
            "a",
            "0cdcc55380fe06c9edf1f1fc620a6e13",
            "a_b.c:d-e",
            &"x".repeat(128),
        ] {
            assert!(token(ok).is_ok(), "{ok}");
        }
        for bad in ["", "-a", ".a", "a/b", "a b", "../x", &"x".repeat(129), "é"] {
            assert!(token(bad).is_err(), "{bad}");
        }
    }

    #[test]
    fn strict_json_refuses_what_python_refuses() {
        assert_eq!(
            strict_json(br#"{"a":[1,2.5,{"b":null}]}"#).unwrap()["a"][2]["b"],
            Value::Null
        );
        for bad in [
            &br#"{"a":1,"a":2}"#[..],
            br#"{"x":{"a":1,"a":1}}"#,
            b"NaN",
            b"[Infinity]",
            b"{} {}",
            b"",
        ] {
            assert!(
                strict_json(bad).is_err(),
                "{}",
                String::from_utf8_lossy(bad)
            );
        }
    }

    #[test]
    fn private_file_checks() {
        let dir = crate::testdir::TestDir::new("private");
        let path = dir.path().join("f.json");
        std::fs::write(&path, b"{\"run_id\":\"r\"}").unwrap();
        std::fs::set_permissions(&path, std::fs::Permissions::from_mode(0o644)).unwrap();
        assert_eq!(
            read_private_json(&path, DEFAULT_LIMIT).unwrap_err().kind(),
            io::ErrorKind::PermissionDenied
        );
        std::fs::set_permissions(&path, std::fs::Permissions::from_mode(0o600)).unwrap();
        assert_eq!(
            read_private_json(&path, DEFAULT_LIMIT).unwrap()["run_id"],
            "r"
        );
        assert!(read_private_json(&path, 4).is_err(), "over the limit");
        let link = dir.path().join("link.json");
        std::os::unix::fs::symlink(&path, &link).unwrap();
        assert!(
            read_private_json(&link, DEFAULT_LIMIT).is_err(),
            "symlinks are not followed"
        );
        let hard = dir.path().join("hard.json");
        std::fs::hard_link(&path, &hard).unwrap();
        assert!(
            read_private_json(&path, DEFAULT_LIMIT).is_err(),
            "two links"
        );
    }
}
