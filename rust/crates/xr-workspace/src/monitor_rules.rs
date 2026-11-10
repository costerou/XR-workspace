//! monitor_rules.py: a monitor rule naming an XR desktop in the user's Hyprland config, and the one narrow edit that
//! removes it.
//!
//! A port of `tools/native_spike/monitor_rules.py`. Omarchy's per-monitor scale control acts on the focused monitor. On
//! an XR desktop it writes a rule for that `XR-n` into `monitors.lua`. The broker then refuses to create an output of
//! that name, so a start refuses with the rule's line ([`refuse_start`]). The panel's confirmed Recover takes it out
//! ([`clear`]) before the broker is asked to remove anything; Stop and the CLI's recover only name it ([`named`]).
//!
//! Only the one-line form Omarchy writes goes. Anything else (a multi-line table, say) was written by hand and is only
//! reported, with its line. A copy of the file stays beside it, and the swap is atomic, through any symlink, with the
//! file's mode kept. This is the one exception to "saved monitor configuration is never edited" (docs/technical.md,
//! Safety boundaries). Every function takes the file's path from its caller ([`hyprland::Client::monitors_config`] for
//! the live compositor), so a test never reaches a real one.
//!
//! The Python's two regular expressions are matched by hand here, with the same semantics: `\s` is Python's
//! `str.isspace`, and lines are split as `str.splitlines` splits them.
//!
//! [`hyprland::Client::monitors_config`]: crate::hyprland::Client::monitors_config

use std::ffi::OsStr;
use std::fmt::Display;
use std::fs::{self, File, OpenOptions};
use std::io::{self, Read, Write};
use std::os::unix::fs::{OpenOptionsExt, PermissionsExt};
use std::path::{Path, PathBuf};
use std::time::{SystemTime, UNIX_EPOCH};

/// The largest config read, in bytes.
pub const LIMIT: u64 = 1 << 20;

/// One rule naming an XR desktop: the desktop, its line (from 1), and whether it is the one-line form that may go.
#[derive(Clone, Debug, PartialEq, Eq)]
pub struct Rule {
    pub name: String,
    pub line: usize,
    pub removable: bool,
}

fn invalid(message: &str) -> io::Error {
    io::Error::new(io::ErrorKind::InvalidData, message)
}

/// `<XDG_CONFIG_HOME or HOME/.config>/hypr/monitors.lua`: the file Omarchy's monitor scale writes to. None when
/// neither is set (the Python asks the password database then; a user service always has `HOME`), and then nothing
/// is read or edited.
pub fn config_path(xdg_config_home: Option<&OsStr>, home: Option<&OsStr>) -> Option<PathBuf> {
    let base = match xdg_config_home.filter(|s| !s.is_empty()) {
        Some(base) => PathBuf::from(base),
        None => PathBuf::from(home.filter(|s| !s.is_empty())?).join(".config"),
    };
    Some(base.join("hypr").join("monitors.lua"))
}

/// `name` if it is an XR desktop's name: the only thing the Recover command line may ask to clear.
pub fn desktop(name: &str) -> io::Result<&str> {
    match xr_name(&name.chars().collect::<Vec<_>>(), 0) {
        Some((end, _)) if end == name.chars().count() => Ok(name),
        _ => Err(invalid("not an XR desktop")),
    }
}

/// Python's `str.isspace`, which is what `\s` matches in a `str` pattern.
fn py_space(c: char) -> bool {
    c.is_whitespace() || ('\x1c'..='\x1f').contains(&c)
}

/// The line breaks of Python's `str.splitlines`.
fn py_break(c: char) -> bool {
    matches!(
        c,
        '\n' | '\r'
            | '\x0b'
            | '\x0c'
            | '\x1c'
            | '\x1d'
            | '\x1e'
            | '\u{85}'
            | '\u{2028}'
            | '\u{2029}'
    )
}

/// `str.splitlines(keepends=True)`: each line with its break (`\r\n` is one break).
fn lines_keeping_ends(text: &str) -> Vec<&str> {
    let mut lines = Vec::new();
    let mut start = 0;
    let mut chars = text.char_indices().peekable();
    while let Some((at, c)) = chars.next() {
        if py_break(c) {
            let mut end = at + c.len_utf8();
            if c == '\r' && chars.peek().is_some_and(|&(_, n)| n == '\n') {
                chars.next();
                end += 1;
            }
            lines.push(&text[start..end]);
            start = end;
        }
    }
    if start < text.len() {
        lines.push(&text[start..]);
    }
    lines
}

/// A line without its break.
fn strip_break(line: &str) -> &str {
    let line = line.strip_suffix("\r\n").unwrap_or(line);
    match line.chars().last() {
        Some(c) if py_break(c) => &line[..line.len() - c.len_utf8()],
        _ => line,
    }
}

fn spaces(s: &[char], mut i: usize) -> usize {
    while i < s.len() && py_space(s[i]) {
        i += 1;
    }
    i
}

fn literal(s: &[char], i: usize, text: &str) -> Option<usize> {
    let mut at = i;
    for c in text.chars() {
        if s.get(at) != Some(&c) {
            return None;
        }
        at += 1;
    }
    Some(at)
}

fn quote(s: &[char], i: usize) -> Option<usize> {
    matches!(s.get(i), Some('"' | '\'')).then_some(i + 1)
}

/// `XR-[1-9][0-9]*` at `i`: its end and the name.
fn xr_name(s: &[char], i: usize) -> Option<(usize, String)> {
    let mut at = literal(s, i, "XR-")?;
    if !matches!(s.get(at), Some('1'..='9')) {
        return None;
    }
    while matches!(s.get(at), Some('0'..='9')) {
        at += 1;
    }
    Some((at, s[i..at].iter().collect()))
}

/// `output\s*=\s*["'](XR-[1-9][0-9]*)["']` at `i`: its end and the desktop.
fn naming_at(s: &[char], i: usize) -> Option<(usize, String)> {
    let at = literal(s, i, "output")?;
    let at = literal(s, spaces(s, at), "=")?;
    let at = quote(s, spaces(s, at))?;
    let (at, name) = xr_name(s, at)?;
    Some((quote(s, at)?, name))
}

/// NAMING.search: the first rule key in `s`.
fn naming(s: &[char]) -> Option<String> {
    (0..s.len()).find_map(|i| naming_at(s, i).map(|(_, name)| name))
}

/// ONE_LINE.fullmatch: `\s*hl\.monitor\(\s*\{\s*output\s*=\s*["']XR-n["']\s*(?:,[^{}]*)?\}\s*\)\s*(?:--.*)?`, the
/// whole line one rule for an XR desktop with nothing but a comment after it. Each step of it is deterministic.
fn one_line(s: &[char]) -> bool {
    let rule = || -> Option<usize> {
        let at = literal(s, spaces(s, 0), "hl.monitor(")?;
        let at = literal(s, spaces(s, at), "{")?;
        let (at, _) = naming_at(s, spaces(s, at))?;
        let mut at = spaces(s, at);
        if s.get(at) == Some(&',') {
            at += 1;
            while s.get(at).is_some_and(|c| !matches!(c, '{' | '}')) {
                at += 1;
            }
        }
        let at = literal(s, at, "}")?;
        let at = spaces(s, literal(s, spaces(s, at), ")")?);
        // `.` matches anything but a newline, and a line holds none.
        Some(if literal(s, at, "--").is_some() {
            s.len()
        } else {
            at
        })
    };
    rule() == Some(s.len())
}

/// Rules naming an XR desktop in a config's text, commented-out ones aside, lines counted from 1. `names`: only
/// these desktops.
pub fn find(text: &str, names: Option<&[String]>) -> Vec<Rule> {
    let mut found = Vec::new();
    for (index, line) in lines_keeping_ends(text).into_iter().enumerate() {
        let line = strip_break(line);
        let code = line.split("--").next().unwrap_or("");
        let Some(name) = naming(&code.chars().collect::<Vec<_>>()) else {
            continue;
        };
        if names.is_none_or(|names| names.contains(&name)) {
            found.push(Rule {
                name,
                line: index + 1,
                removable: one_line(&line.chars().collect::<Vec<_>>()),
            });
        }
    }
    found
}

/// The file's text: at most LIMIT bytes, UTF-8.
fn read(path: &Path) -> io::Result<String> {
    let mut raw = Vec::new();
    File::open(path)?.take(LIMIT + 1).read_to_end(&mut raw)?;
    if raw.len() as u64 > LIMIT {
        return Err(invalid("monitor config too large"));
    }
    match std::str::from_utf8(&raw) {
        Ok(_) => Ok(String::from_utf8(raw).unwrap_or_default()),
        Err(e) => Err(invalid(&utf8_message(&raw, e))),
    }
}

/// Python's UnicodeDecodeError text, so a reason reads the same whichever side wrote it.
fn utf8_message(raw: &[u8], e: std::str::Utf8Error) -> String {
    let at = e.valid_up_to();
    let lead = raw[at];
    let codec = "'utf-8' codec can't decode";
    match e.error_len() {
        None if raw.len() - at > 1 => format!(
            "{codec} bytes in position {at}-{}: unexpected end of data",
            raw.len() - 1
        ),
        None => format!("{codec} byte 0x{lead:02x} in position {at}: unexpected end of data"),
        Some(_) if (0xc2..=0xf4).contains(&lead) => {
            format!("{codec} byte 0x{lead:02x} in position {at}: invalid continuation byte")
        }
        Some(_) => format!("{codec} byte 0x{lead:02x} in position {at}: invalid start byte"),
    }
}

/// find() on the file at `path`; nothing when there is no file.
pub fn leftovers(names: Option<&[String]>, path: &Path) -> io::Result<Vec<Rule>> {
    match read(path) {
        Ok(text) => Ok(find(&text, names)),
        Err(e) if e.kind() == io::ErrorKind::NotFound => Ok(Vec::new()),
        Err(e) => Err(e),
    }
}

fn file_name(path: &Path) -> String {
    path.file_name()
        .map(|n| n.to_string_lossy().into_owned())
        .unwrap_or_default()
}

/// What a rule stops, and what to do about it.
pub fn describe(rule: &Rule, path: &Path) -> String {
    format!(
        "{} line {} names {}, so that desktop cannot be used: remove that line, then run hyprctl reload",
        file_name(path),
        rule.line,
        rule.name
    )
}

/// A new file (never through a symlink, never over an existing one) holding `text`, with `mode`, synced.
fn write_new(path: &Path, text: &str, mode: u32) -> io::Result<()> {
    let mut file = OpenOptions::new()
        .write(true)
        .create_new(true)
        .mode(0o600)
        .custom_flags(libc::O_NOFOLLOW)
        .open(path)?;
    file.set_permissions(fs::Permissions::from_mode(mode))?;
    file.write_all(text.as_bytes())?;
    file.sync_all()
}

fn token_hex() -> io::Result<String> {
    let mut bytes = [0u8; 8];
    rustix::rand::getrandom(&mut bytes, rustix::rand::GetRandomFlags::empty())?;
    Ok(bytes.iter().map(|b| format!("{b:02x}")).collect())
}

/// The removable rules for `names` out of the file at `path` (through any symlink), and nothing else: every other
/// byte stays. The copy left beside it, or None when there was nothing to remove.
pub fn remove(path: &Path, names: &[String]) -> io::Result<Option<PathBuf>> {
    let target = fs::canonicalize(path)?;
    let text = read(&target)?;
    let gone: Vec<usize> = find(&text, Some(names))
        .into_iter()
        .filter(|rule| rule.removable)
        .map(|rule| rule.line)
        .collect();
    if gone.is_empty() {
        return Ok(None);
    }
    let mode = fs::metadata(&target)?.permissions().mode() & 0o7777;
    let name = file_name(&target);
    let now = SystemTime::now()
        .duration_since(UNIX_EPOCH)
        .map_err(io::Error::other)?
        .as_nanos();
    let backup = target.with_file_name(format!("{name}.bak.xr-rule-{now}"));
    write_new(&backup, &text, mode)?;
    let kept: String = lines_keeping_ends(&text)
        .into_iter()
        .enumerate()
        .filter(|(index, _)| !gone.contains(&(index + 1)))
        .map(|(_, line)| line)
        .collect();
    let temporary = target.with_file_name(format!(".{name}.{}.tmp", token_hex()?));
    let swapped = write_new(&temporary, &kept, mode).and_then(|()| fs::rename(&temporary, &target));
    if swapped.is_err() {
        let _ = fs::remove_file(&temporary);
    }
    swapped.map(|()| Some(backup))
}

/// What stands in the file for `names`, a run's outputs, as reasons. Changes nothing. Never fails: an unreadable file
/// names nothing.
pub fn named(path: Option<&Path>, names: &[String]) -> Vec<String> {
    let Some(path) = path.filter(|_| !names.is_empty()) else {
        return Vec::new();
    };
    leftovers(Some(names), path)
        .map(|found| found.iter().map(|rule| describe(rule, path)).collect())
        .unwrap_or_default()
}

/// The panel's confirmed Recover, before the broker is asked to remove `names`, a run's outputs: their one-line rules
/// go and Hyprland reloads (`reload`). What this could not do comes back as reasons; it never fails, as recovery runs
/// regardless. `notify` hears what was removed and where the copy is.
pub fn clear<E: Display>(
    path: Option<&Path>,
    names: &[String],
    reload: impl FnOnce() -> Result<(), E>,
    notify: Option<&dyn Fn(&str)>,
) -> Vec<String> {
    let Some(path) = path.filter(|_| !names.is_empty()) else {
        return Vec::new();
    };
    let mut found = Vec::new();
    let attempt = (|| -> Result<Option<PathBuf>, String> {
        found = leftovers(Some(names), path).map_err(|e| e.to_string())?;
        let mut backup = None;
        if found.iter().any(|rule| rule.removable) {
            backup = remove(path, names).map_err(|e| e.to_string())?;
        }
        if backup.is_some() {
            reload().map_err(|e| e.to_string())?;
        }
        Ok(backup)
    })();
    let backup = match attempt {
        Ok(backup) => backup,
        Err(why) => {
            let mut which: Vec<&str> = found.iter().map(|rule| rule.name.as_str()).collect();
            which.sort_unstable();
            which.dedup();
            let which = if which.is_empty() {
                "an XR desktop".to_owned()
            } else {
                which.join(", ")
            };
            return vec![format!(
                "could not take the rule for {which} out of {}: {why}",
                file_name(path)
            )];
        }
    };
    if let (Some(backup), Some(notify)) = (&backup, notify) {
        let mut removed: Vec<&str> = found
            .iter()
            .filter(|rule| rule.removable)
            .map(|rule| rule.name.as_str())
            .collect();
        removed.sort_unstable();
        removed.dedup();
        notify(&format!(
            "Removed the {} line Omarchy's scale control added to {}. A copy is in {}.",
            removed.join(" and "),
            file_name(path),
            file_name(backup)
        ));
    }
    found
        .iter()
        .filter(|rule| !rule.removable)
        .map(|rule| describe(rule, path))
        .collect()
}

/// A start, before anything is loaded: a rule naming any XR desktop stops it with that rule's line. A config this
/// cannot read proves nothing, so it stops nothing. Changes nothing.
pub fn refuse_start(path: Option<&Path>) -> Result<(), String> {
    let Some(path) = path else { return Ok(()) };
    match leftovers(None, path) {
        Ok(found) => match found.first() {
            Some(rule) => Err(describe(rule, path)),
            None => Ok(()),
        },
        Err(_) => Ok(()),
    }
}

#[cfg(test)]
mod tests;
