//! Do this run's keybinds clash with the desktop's own? Checked offline with Hyprland's own config loader. The port
//! of `tools/native_spike/bind_check.py`; its module docstring explains the check.
//!
//! The user's Hyprland config is loaded by `Hyprland --verify-config` in a throwaway HOME holding a copy of
//! `~/.config/hypr` and the Omarchy state the config reads, so nothing outside that directory is written. A harness
//! (`harness_lua`) wraps `hl.bind` / `hl.unbind` to record every chord the config binds, then runs this run's own
//! generated bind Lua the same way. The run's chords (`run_chords`) are then compared with the desktop's and with
//! each other. The running compositor is never contacted.
//!
//! The generated bind Lua belongs to the Lua generators' port, so `harness_lua` and `check` take it as `chunks`.
//!
//! Exit status: 0 no clash, 1 clash found, 2 could not check (no Hyprland binary, config or harness error).

use std::collections::{BTreeMap, BTreeSet};
use std::io::{self, Read};
use std::os::unix::fs::{DirBuilderExt, PermissionsExt};
use std::path::{Path, PathBuf};
use std::process::{Command, Stdio};
use std::time::{Duration, Instant};

use super::keybinds::{self, KeyMap, chord_label};
use super::{py_repr, py_strip};

/// A chord with its spelling taken out: (modifiers, key). `normalize` makes it.
pub type Normal = (BTreeSet<String>, String);

/// Omarchy state its Hyprland config reads at load (themes, toggles, layouts); copied, never linked.
pub const STATE_DIRS: [&str; 7] = [
    "current",
    "toggles",
    "settings",
    "workspace-layouts",
    "monitor-workspaces",
    "monitor-workspaces-merged",
    "indicators",
];
/// Bind Lua is generated with placeholder paths; only its shape matters here.
pub const DUMMY_ARGS: [&str; 5] = [
    "/usr/bin/python3",
    "/nonexistent/xr_control.py",
    "/nonexistent/control.sock",
    "bindcheck",
    "bindcheck",
];

/// The placement panel's modal binds (view.MENU_BINDS).
const MENU_BINDS: [(&str, &str); 13] = [
    ("up", "menu_field_prev"),
    ("down", "menu_field_next"),
    ("left", "menu_dec"),
    ("right", "menu_inc"),
    ("SHIFT + left", "menu_dec_big"),
    ("SHIFT + right", "menu_inc_big"),
    ("Tab", "menu_desktop_next"),
    ("SHIFT + Tab", "menu_desktop_prev"),
    ("R", "menu_reset"),
    ("Escape", "menu_back"),
    ("U", "menu_ultrawide"),
    ("N", "menu_add"),
    ("SHIFT + Delete", "menu_delete"),
];
/// The options menu's keys, in the placement panel's table (view.OPTIONS_BINDS).
const OPTIONS_BINDS: [(&str, &str); 12] = [
    ("up", "binds_key 0 111"),
    ("down", "binds_key 0 116"),
    ("left", "binds_key 0 113"),
    ("right", "binds_key 0 114"),
    ("Tab", "binds_key 0 23"),
    ("SHIFT + Tab", "binds_key 1 23"),
    ("Escape", "binds_key 0 9"),
    ("Return", "binds_key 0 36"),
    ("space", "binds_key 0 65"),
    ("BackSpace", "binds_key 0 22"),
    ("Prior", "binds_key 0 112"),
    ("Next", "binds_key 0 117"),
];
/// The overview free pointer's left button (resize.RESIZE_BUTTON, resize.RESIZE_OPS): press, and release.
const RESIZE_BUTTON: &str = "mouse:272";
const RESIZE_OPS: [&str; 2] = ["resize_press", "resize_release"];
/// The overview input lock's other buttons and wheel (input_lock.OTHER_BUTTONS, OTHER_BUTTON_OP, WHEEL).
const OTHER_BUTTONS: [&str; 7] = [
    "mouse:273",
    "mouse:274",
    "mouse:275",
    "mouse:276",
    "mouse:277",
    "mouse:278",
    "mouse:279",
];
const OTHER_BUTTON_OP: &str = "resize_release";
const WHEEL: [&str; 4] = ["mouse_up", "mouse_down", "mouse_left", "mouse_right"];

/// 'SUPER + SHIFT + R' and 'shift + win + r' -> ({SHIFT, SUPER}, "r"). A digit and its keycode are one key ('1' is
/// code:10 ... '0' is code:19), as are XKB's Prior / Next and Page_Up / Page_Down.
pub fn normalize(chord: &str) -> Result<Normal, String> {
    let parts: Vec<&str> = chord.split('+').map(py_strip).collect();
    let last = parts[parts.len() - 1];
    if last.is_empty() {
        return Err(format!("unreadable chord: {}", py_repr(chord)));
    }
    let mut key = last.to_lowercase();
    match key.as_str() {
        "prior" => key = "page_up".into(),
        "next" => key = "page_down".into(),
        _ => {}
    }
    if let Some(d) = key
        .chars()
        .next()
        .filter(|c| key.len() == 1 && c.is_ascii_digit())
    {
        let i = (d as u32 - '0' as u32 + 9) % 10;
        key = format!("code:{}", 10 + i);
    }
    let mods = parts[..parts.len() - 1]
        .iter()
        .filter(|p| !p.is_empty())
        .map(|p| {
            let up = p.to_uppercase();
            match up.as_str() {
                "WIN" | "LOGO" | "MOD4" | "META" => "SUPER".into(),
                "CONTROL" => "CTRL".into(),
                "MOD1" => "ALT".into(),
                _ => up,
            }
        })
        .collect();
    Ok((mods, key))
}

/// One bind this project installs during a run: (set name, chord, op).
pub type RunChord = (&'static str, String, String);

/// Every bind this project installs during a run: the run and send binds with `keymap`'s keys (the key bind
/// panel's), then the placement panel's, the options menu's, the free pointer's and the input lock's.
pub fn run_chords(keymap: &KeyMap) -> Vec<RunChord> {
    let mut out: Vec<RunChord> = Vec::new();
    for op in keybinds::RUN_OPS {
        out.push(("run", keymap.chord(op).to_string(), op.to_string()));
    }
    for op in keybinds::SEND_OPS {
        out.push(("send", keymap.chord(op).to_string(), op.to_string()));
    }
    out.extend(
        MENU_BINDS
            .iter()
            .map(|(c, op)| ("menu", c.to_string(), op.to_string())),
    );
    out.extend(
        OPTIONS_BINDS
            .iter()
            .map(|(c, op)| ("options", c.to_string(), op.to_string())),
    );
    out.extend(
        RESIZE_OPS
            .iter()
            .map(|op| ("pointer", RESIZE_BUTTON.to_string(), op.to_string())),
    );
    out.extend(
        OTHER_BUTTONS
            .iter()
            .map(|b| ("lock", b.to_string(), OTHER_BUTTON_OP.to_string())),
    );
    out.extend(
        WHEEL
            .iter()
            .map(|w| ("lock", w.to_string(), "nothing".to_string())),
    );
    out
}

fn lua_string(s: &str) -> String {
    format!(
        "\"{}\"",
        s.replace('\\', "\\\\")
            .replace('"', "\\\"")
            .replace('\n', "\\n")
    )
}

/// The harness `Hyprland --verify-config` runs: the desktop config at `config` with every bind recorded to `out`,
/// then this project's generated bind Lua (`chunks`, each in its own block).
pub fn harness_lua(config: &str, out: &str, chunks: &[String]) -> String {
    let ours: Vec<String> = chunks.iter().map(|c| format!("do {c} end")).collect();
    let ours = ours.join("\n");
    let (out, config) = (lua_string(out), lua_string(config));
    format!(
        r#"local out = assert(io.open({out}, "w"))
local phase = "desktop"
local function clean(s) return (tostring(s):gsub("[\t\n]", " ")) end
local bind, unbind = hl.bind, hl.unbind
-- Hyprland 0.56.2 --verify-config segfaults on exit freeing the timers a config made (CConfigManager::cleanTimers);
-- verify mode never fires timers, so inert stand-ins keep the verdict.
local inert = setmetatable({{}}, {{__index = function() return function() end end}})
hl.timer = function() return inert end
hl.bind = function(keys, ...) out:write(phase, "\tbind\t", clean(keys), "\n") return bind(keys, ...) end
hl.unbind = function(keys, ...) out:write(phase, "\tunbind\t", clean(keys), "\n") return unbind(keys, ...) end
local ok, err = pcall(dofile, {config})
out:write("desktop\tloaded\t", ok and "ok" or clean(err), "\n")
out:flush()
phase = "run"
-- Verify mode has no event manager: a submap switch (the key bind panel's) would post to it and crash Hyprland 0.56.2.
-- Only our generated Lua runs from here: dispatches do nothing, and a submap switch is kept in Lua so the panel's own
-- check that it entered its submap (keybinds.panel_on_lua) still holds.
local submap = ""
local dsp_submap = hl.dsp.submap
hl.dsp.submap = function(name) dsp_submap(name) return {{ xr_submap = name }} end
hl.get_cursor_pos = function() return nil end  -- verify mode has no input manager; the window memory's evals read it
hl.dispatch = function(d) if type(d) == "table" and d.xr_submap then submap = d.xr_submap == "reset" and "" or d.xr_submap end return true end
hl.get_current_submap = function() return submap end
local ran, why = pcall(function()
{ours}
end)
out:write("run\tloaded\t", ran and "ok" or clean(why), "\n")
out:close()
"#
    )
}

/// One line of the harness's records file: (phase, kind, value).
pub type Record = (String, String, String);

/// The records file's lines with at least two tabs, each split at its first two.
pub fn records(text: &str) -> Vec<Record> {
    py_splitlines(text)
        .into_iter()
        .filter(|line| line.matches('\t').count() >= 2)
        .map(|line| {
            let mut it = line.splitn(3, '\t');
            let mut next = || it.next().unwrap_or_default().to_string();
            (next(), next(), next())
        })
        .collect()
}

/// Python's `str.splitlines()`.
fn py_splitlines(text: &str) -> Vec<&str> {
    let mut out = Vec::new();
    let mut start = 0;
    let mut chars = text.char_indices().peekable();
    while let Some((i, c)) = chars.next() {
        let end = match c {
            '\r' => {
                if let Some(&(_, '\n')) = chars.peek() {
                    chars.next();
                    i + 2
                } else {
                    i + 1
                }
            }
            '\n' | '\u{b}' | '\u{c}' | '\u{1c}' | '\u{1d}' | '\u{1e}' | '\u{85}' | '\u{2028}'
            | '\u{2029}' => i + c.len_utf8(),
            _ => continue,
        };
        out.push(&text[start..i]);
        start = end;
    }
    if start < text.len() {
        out.push(&text[start..]);
    }
    out
}

/// Chords the desktop config leaves bound: binds minus later unbinds of the same chord. Each with the spellings
/// that bound it, in order.
pub fn desktop_chords(records: &[Record]) -> BTreeMap<Normal, Vec<String>> {
    let mut bound: BTreeMap<Normal, Vec<String>> = BTreeMap::new();
    for (phase, kind, value) in records {
        if phase != "desktop" || (kind != "bind" && kind != "unbind") {
            continue;
        }
        let Ok(key) = normalize(value) else { continue };
        if kind == "bind" {
            bound.entry(key).or_default().push(value.clone());
        } else {
            bound.remove(&key);
        }
    }
    bound
}

/// Human-readable clash lines (empty: none). A press/release pair on one button is one bind, not a clash.
pub fn clashes(records: &[Record], keymap: &KeyMap) -> Vec<String> {
    let desktop = desktop_chords(records);
    let mut problems = Vec::new();
    let mut seen: BTreeMap<Normal, Vec<(&'static str, String)>> = BTreeMap::new();
    for (place, chord, op) in run_chords(keymap) {
        let Ok(key) = normalize(&chord) else { continue };
        if let Some(found) = desktop.get(&key) {
            problems.push(format!(
                "{chord} ({place}: {op}) is already bound by the desktop config as {}",
                found[0]
            ));
        }
        for (other_place, other_op) in seen.get(&key).into_iter().flatten() {
            let pair = op != *other_op
                && RESIZE_OPS.contains(&op.as_str())
                && RESIZE_OPS.contains(&other_op.as_str());
            // The run, send, panel and free-pointer sets can all be live at once (a panel open, the pointer free). The
            // options menu's keys replace the panel's in one table (view.OPTIONS_BINDS).
            let menu_options = [place, *other_place] == ["menu", "options"]
                || [place, *other_place] == ["options", "menu"];
            if !pair && !menu_options {
                problems.push(format!(
                    "{chord} is bound twice in this project ({other_place}: {other_op}, {place}: {op})"
                ));
            }
        }
        seen.entry(key).or_default().push((place, op));
    }
    problems
}

/// What `check` needs from outside: the Hyprland binary, the config, HOME, and the saved key binds.
#[derive(Clone, Debug, Default)]
pub struct CheckArgs {
    /// The Hyprland Lua config (default `<home>/.config/hypr/hyprland.lua`).
    pub config: Option<PathBuf>,
    /// The Hyprland binary (default: `Hyprland` on PATH).
    pub hyprland: Option<PathBuf>,
    /// HOME (default: $HOME).
    pub home: Option<PathBuf>,
    /// The key bind panel's saved keys (default `keybinds::config_path()`).
    pub keybinds: Option<PathBuf>,
    pub timeout: Option<Duration>,
}

/// (exit status, report lines). `chunks` gives this project's generated bind Lua for a keymap (the harness's chunk
/// list: bind_check.harness_lua in the Python).
pub fn check(args: &CheckArgs, chunks: impl Fn(&KeyMap) -> Vec<String>) -> (i32, Vec<String>) {
    let keymap = keybinds::load(&args.keybinds.clone().unwrap_or_else(keybinds::config_path));
    let home = args
        .home
        .clone()
        .unwrap_or_else(|| PathBuf::from(std::env::var_os("HOME").unwrap_or_default()));
    let config = args
        .config
        .clone()
        .unwrap_or_else(|| home.join(".config/hypr/hyprland.lua"));
    let Some(binary) = args.hyprland.clone().or_else(|| which("Hyprland")) else {
        return (
            2,
            vec!["Hyprland not found; cannot run --verify-config".into()],
        );
    };
    if !config.is_file() {
        return (
            2,
            vec![format!("no Hyprland config at {}", config.display())],
        );
    }
    let parent = config.parent().unwrap_or(Path::new("/"));
    let tmp = match isolated_home(&home, parent) {
        Ok(tmp) => tmp,
        Err(e) => return (2, vec![format!("could not copy the Hyprland config: {e}")]),
    };
    let ran = verify(
        &tmp,
        &config,
        &binary,
        &chunks(&keymap),
        args.timeout.unwrap_or(Duration::from_secs(60)),
    );
    let _ = std::fs::remove_dir_all(&tmp);
    let (records, verdict, code) = match ran {
        Ok(Ok(found)) => found,
        Ok(Err(why)) => return (2, vec![format!("harness did not run: {why}")]),
        Err(e) => return (2, vec![format!("could not run {}: {e}", binary.display())]),
    };
    let loaded = |phase: &str| {
        records
            .iter()
            .filter(|(p, k, _)| p == phase && k == "loaded")
            .map(|(_, _, v)| v.as_str())
            .next_back()
    };
    if loaded("desktop") != Some("ok") {
        return (
            2,
            vec![format!(
                "desktop config failed to load: {}",
                loaded("desktop").unwrap_or("no result")
            )],
        );
    }
    if loaded("run") != Some("ok") {
        return (
            2,
            vec![format!(
                "this project's bind Lua failed to load: {}",
                loaded("run").unwrap_or("no result")
            )],
        );
    }
    let first = py_splitlines(&verdict)
        .first()
        .copied()
        .filter(|_| !verdict.is_empty())
        .unwrap_or("no verdict");
    let mut report = vec![
        format!("Hyprland --verify-config: {first} ({code})"),
        format!(
            "desktop config: {} chords bound",
            desktop_chords(&records).len()
        ),
    ];
    let problems = clashes(&records, &keymap);
    if code != 0 {
        report.push("Hyprland reported config errors (above); results may be incomplete".into());
        return (2, report);
    }
    if !problems.is_empty() {
        report.extend(problems);
        return (1, report);
    }
    let labels: BTreeSet<String> = run_chords(&keymap)
        .iter()
        .filter(|(w, _, _)| *w == "menu")
        .map(|(_, c, _)| chord_label(c))
        .collect();
    let labels = labels.into_iter().collect::<Vec<_>>().join(", ");
    let sends = keybinds::SEND_OPS
        .iter()
        .map(|op| chord_label(keymap.chord(op)))
        .collect::<Vec<_>>()
        .join(", ");
    let custom = if keymap.overrides.is_empty() {
        String::new()
    } else {
        format!(", {} changed in the key bind panel", keymap.overrides.len())
    };
    report.push(format!(
        "no clash: {} run binds{custom} (typing toggle {}, key bind panel {}), send-window binds ({sends}), placement \
         panel binds ({labels}), the free pointer's left button (and the unified cursor's non-consuming click binds) \
         and the input lock's other buttons and wheel",
        keybinds::RUN_OPS.len(),
        chord_label(keymap.chord(keybinds::TYPING_OP)),
        chord_label(keymap.chord(keybinds::PANEL_OP)),
    ));
    (0, report)
}

/// `Hyprland --verify-config` on the harness in `tmp`: (records, verdict, exit status), or why the harness did not
/// run (no records file).
#[allow(clippy::type_complexity)]
fn verify(
    tmp: &Path,
    config: &Path,
    binary: &Path,
    chunks: &[String],
    timeout: Duration,
) -> io::Result<Result<(Vec<Record>, String, i32), String>> {
    let copied = tmp
        .join(".config/hypr")
        .join(config.file_name().unwrap_or_default());
    let (out, harness) = (tmp.join("binds.tsv"), tmp.join("harness.lua"));
    std::fs::write(
        &harness,
        harness_lua(&copied.to_string_lossy(), &out.to_string_lossy(), chunks),
    )?;
    let mut cmd = Command::new(binary);
    cmd.args(["--verify-config", "-c"])
        .arg(&harness)
        .env_clear()
        .current_dir(tmp);
    cmd.env("HOME", tmp).env(
        "PATH",
        std::env::var_os("PATH").unwrap_or_else(|| "/usr/bin:/bin".into()),
    );
    cmd.env("XDG_CONFIG_HOME", tmp.join(".config"))
        .env("XDG_STATE_HOME", tmp.join(".local/state"));
    cmd.env("XDG_RUNTIME_DIR", tmp).env("LANG", "C.UTF-8");
    // Omarchy's config reads these; a made-up instance matches no compositor socket (verify mode opens none anyway).
    let omarchy = std::env::var_os("OMARCHY_PATH").filter(|p| !p.is_empty());
    cmd.env(
        "OMARCHY_PATH",
        omarchy.unwrap_or_else(|| "/usr/share/omarchy".into()),
    );
    cmd.env("HYPRLAND_INSTANCE_SIGNATURE", "xr-bind-check");
    let (stdout, stderr, code) = run(cmd, timeout)?;
    let verdict = py_strip(
        stdout
            .split_once("Config parsing result:")
            .map_or(stdout.as_str(), |(_, v)| v),
    )
    .to_string();
    let text = match std::fs::read(&out) {
        Ok(bytes) => String::from_utf8_lossy(&bytes).into_owned(),
        Err(e) if e.kind() == io::ErrorKind::NotFound => {
            return Ok(Err(if verdict.is_empty() {
                py_strip(&stderr).to_string()
            } else {
                verdict
            }));
        }
        Err(e) => return Err(e),
    };
    Ok(Ok((records(&text), verdict, code)))
}

/// Runs `cmd` with no stdin: (stdout, stderr, exit status). Killed after `timeout`.
fn run(mut cmd: Command, timeout: Duration) -> io::Result<(String, String, i32)> {
    let mut child = cmd
        .stdin(Stdio::null())
        .stdout(Stdio::piped())
        .stderr(Stdio::piped())
        .spawn()?;
    let drain = |pipe: Option<Box<dyn Read + Send>>| {
        std::thread::spawn(move || {
            let mut buf = Vec::new();
            if let Some(mut p) = pipe {
                let _ = p.read_to_end(&mut buf);
            }
            String::from_utf8_lossy(&buf).into_owned()
        })
    };
    let out = drain(
        child
            .stdout
            .take()
            .map(|p| Box::new(p) as Box<dyn Read + Send>),
    );
    let err = drain(
        child
            .stderr
            .take()
            .map(|p| Box::new(p) as Box<dyn Read + Send>),
    );
    let deadline = Instant::now() + timeout;
    let status = loop {
        if let Some(status) = child.try_wait()? {
            break status;
        }
        if Instant::now() >= deadline {
            let _ = child.kill();
            let _ = child.wait();
            return Err(io::Error::new(
                io::ErrorKind::TimedOut,
                format!("no verdict in {} s", timeout.as_secs()),
            ));
        }
        std::thread::sleep(Duration::from_millis(20));
    };
    let code = status.code().unwrap_or_else(|| {
        use std::os::unix::process::ExitStatusExt;
        -status.signal().unwrap_or(0)
    });
    Ok((
        out.join().unwrap_or_default(),
        err.join().unwrap_or_default(),
        code,
    ))
}

fn which(name: &str) -> Option<PathBuf> {
    use std::os::unix::fs::MetadataExt;
    let path = std::env::var_os("PATH")?;
    std::env::split_paths(&path)
        .map(|dir| dir.join(name))
        .find(|p| std::fs::metadata(p).is_ok_and(|m| m.is_file() && m.mode() & 0o111 != 0))
}

/// A temporary HOME (mode 0700, under the temporary directory) with copies of the Hyprland config folder and the
/// Omarchy state it reads. Symbolic links are copied as links.
fn isolated_home(home: &Path, config_dir: &Path) -> io::Result<PathBuf> {
    let tmp = make_temp_dir()?;
    let made = (|| {
        copy_tree(config_dir, &tmp.join(".config/hypr"))?;
        let state = home.join(".local/state/omarchy");
        let into = tmp.join(".local/state/omarchy");
        std::fs::create_dir_all(&into)?;
        for name in STATE_DIRS {
            let (src, dst) = (state.join(name), into.join(name));
            match std::fs::symlink_metadata(&src) {
                Ok(m) if m.is_dir() => copy_tree(&src, &dst)?,
                Ok(_) => copy_entry(&src, &dst)?,
                Err(e) if e.kind() == io::ErrorKind::NotFound => {}
                Err(e) => return Err(e),
            }
        }
        Ok(())
    })();
    match made {
        Ok(()) => Ok(tmp),
        Err(e) => {
            let _ = std::fs::remove_dir_all(&tmp);
            Err(e)
        }
    }
}

fn make_temp_dir() -> io::Result<PathBuf> {
    use std::sync::atomic::{AtomicU32, Ordering};
    static NEXT: AtomicU32 = AtomicU32::new(0);
    let nanos = std::time::SystemTime::now()
        .duration_since(std::time::UNIX_EPOCH)
        .map_or(0, |d| d.subsec_nanos());
    for _ in 0..100 {
        let n = NEXT.fetch_add(1, Ordering::Relaxed);
        let path = std::env::temp_dir().join(format!(
            "xr-bind-check-{}-{nanos:x}-{n}",
            std::process::id()
        ));
        match std::fs::DirBuilder::new().mode(0o700).create(&path) {
            Ok(()) => return Ok(path),
            Err(e) if e.kind() == io::ErrorKind::AlreadyExists => continue,
            Err(e) => return Err(e),
        }
    }
    Err(io::Error::new(
        io::ErrorKind::AlreadyExists,
        "no free temporary directory name",
    ))
}

/// `src` (a directory) copied to `dst`, which must not exist yet; its parents are made.
fn copy_tree(src: &Path, dst: &Path) -> io::Result<()> {
    if let Some(parent) = dst.parent() {
        std::fs::create_dir_all(parent)?;
    }
    std::fs::create_dir(dst)?;
    for entry in std::fs::read_dir(src)? {
        let entry = entry?;
        let (from, to) = (entry.path(), dst.join(entry.file_name()));
        if entry.file_type()?.is_dir() {
            copy_tree(&from, &to)?;
        } else {
            copy_entry(&from, &to)?;
        }
    }
    std::fs::set_permissions(dst, std::fs::metadata(src)?.permissions())
}

/// One file (with its mode) or symbolic link (as a link).
fn copy_entry(src: &Path, dst: &Path) -> io::Result<()> {
    let meta = std::fs::symlink_metadata(src)?;
    if meta.file_type().is_symlink() {
        std::os::unix::fs::symlink(std::fs::read_link(src)?, dst)
    } else {
        std::fs::copy(src, dst)?;
        std::fs::set_permissions(
            dst,
            std::fs::Permissions::from_mode(meta.permissions().mode()),
        )
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    use crate::supervisor::corpus::{self, s};
    use crate::testdir::TestDir;
    use serde_json::{Value, json};

    fn keymap(v: &Value) -> KeyMap {
        KeyMap::new(
            v.as_object()
                .unwrap()
                .iter()
                .map(|(k, v)| (k.as_str(), s(v))),
        )
    }

    fn normal_json((mods, key): &Normal) -> Value {
        json!([mods, key])
    }

    #[test]
    fn normalize_matches_python() {
        let c = corpus::load("bind_check.json");
        for case in c["normalize"].as_array().unwrap() {
            let got = match normalize(s(&case["text"])) {
                Ok(n) => json!({"value": normal_json(&n)}),
                Err(e) => json!({"error": e}),
            };
            let mut want = case.clone();
            want.as_object_mut().unwrap().remove("text");
            assert_eq!(got, want, "{}", case["text"]);
        }
        assert_eq!(c["state_dirs"], json!(STATE_DIRS));
        assert_eq!(c["dummy_args"], json!(DUMMY_ARGS));
    }

    #[test]
    fn run_chords_desktop_chords_and_clashes_match_python() {
        let c = corpus::load("bind_check.json");
        for case in c["run_chords"].as_array().unwrap() {
            let got: Vec<Value> = run_chords(&keymap(&case["overrides"]))
                .iter()
                .map(|(w, c, o)| json!([w, c, o]))
                .collect();
            assert_eq!(Value::from(got), case["chords"], "{}", case["overrides"]);
        }
        for case in c["records"].as_array().unwrap() {
            let lines: Vec<&str> = case["lines"].as_array().unwrap().iter().map(s).collect();
            let recs = records(&lines.join("\n"));
            let chords: Vec<Value> = desktop_chords(&recs)
                .iter()
                .map(|((m, k), v)| json!([m, k, v]))
                .collect();
            assert_eq!(
                Value::from(chords),
                case["desktop_chords"],
                "{}",
                case["name"]
            );
            for clash in case["clashes"].as_array().unwrap() {
                assert_eq!(
                    json!(clashes(&recs, &keymap(&clash["overrides"]))),
                    clash["problems"],
                    "{}",
                    case["name"]
                );
            }
        }
    }

    #[test]
    fn harness_matches_python() {
        let c = corpus::load("bind_check.json");
        for case in c["harness"].as_array().unwrap() {
            let chunks: Vec<String> = case["chunks"]
                .as_array()
                .unwrap()
                .iter()
                .map(|v| s(v).to_string())
                .collect();
            assert_eq!(
                harness_lua(s(&case["config"]), s(&case["out"]), &chunks),
                s(&case["lua"])
            );
        }
    }

    fn sh(text: &str) -> String {
        format!("'{}'", text.replace('\'', "'\\''"))
    }

    /// check() against the corpus's stand-in Hyprland: a script that writes the harness's records file itself.
    #[test]
    fn check_matches_python() {
        let c = corpus::load("bind_check.json");
        let dir = TestDir::new("bind-check");
        let tmp = dir.path();
        let home = tmp.join("home");
        std::fs::create_dir_all(home.join(".config/hypr")).unwrap();
        let config = home.join(".config/hypr/hyprland.lua");
        std::fs::write(&config, "-- stand-in\n").unwrap();
        let state = home.join(".local/state/omarchy");
        std::fs::create_dir_all(state.join("current")).unwrap();
        std::fs::write(state.join("current/theme"), "x").unwrap();
        std::fs::write(state.join("toggles"), "file, not a directory").unwrap();
        let fake = tmp.join("Hyprland");
        let kfile = tmp.join("keybinds.json");
        let harness_seen = tmp.join("harness_seen");
        for case in c["check"].as_array().unwrap() {
            let mut args = CheckArgs {
                config: Some(config.clone()),
                hyprland: Some(fake.clone()),
                home: Some(home.clone()),
                keybinds: Some(kfile.clone()),
                timeout: None,
            };
            if case["name"] == "no_config" {
                args.config = Some(home.join(s(&case["config"])));
                args.keybinds = Some(tmp.join("none.json"));
            } else {
                let write = match case["records"].as_array() {
                    Some(lines) => {
                        let lines: Vec<&str> = lines.iter().map(s).collect();
                        format!(
                            "out=$(sed -n '1s/^local out = assert(io.open(\"\\(.*\\)\", \"w\"))$/\\1/p' \"$3\")\nprintf %s {} > \"$out\"\n",
                            sh(&(lines.join("\n") + "\n"))
                        )
                    }
                    None => String::new(),
                };
                let copy = format!("cp \"$3\" {}\n", sh(&harness_seen.to_string_lossy()));
                std::fs::write(
                    &fake,
                    format!("#!/bin/sh\n{copy}{write}printf %s {}\necho \"stderr says no\" >&2\nexit {}\n", sh(s(&case["stdout"])), case["code"]),
                )
                .unwrap();
                std::fs::set_permissions(&fake, std::fs::Permissions::from_mode(0o755)).unwrap();
                match case["keybinds"].as_str() {
                    Some(text) => std::fs::write(&kfile, text).unwrap(),
                    None => {
                        let _ = std::fs::remove_file(&kfile);
                    }
                }
            }
            let (status, report) = check(&args, |km| {
                vec![format!("-- {} changed", km.overrides.len())]
            });
            let report: Vec<String> = report
                .iter()
                .map(|l| l.replace(&tmp.to_string_lossy().into_owned(), "<tmp>"))
                .collect();
            assert_eq!(json!(status), case["status"], "{}", case["name"]);
            assert_eq!(json!(report), case["report"], "{}", case["name"]);
            if case["name"] != "no_config" {
                // The harness ran with the chunks for the saved keymap, and the throwaway HOME is gone.
                let seen = std::fs::read_to_string(&harness_seen).unwrap();
                let changed = if case["keybinds"].is_null() { 0 } else { 2 };
                assert!(
                    seen.contains(&format!("do -- {changed} changed end")),
                    "{}",
                    case["name"]
                );
                let first = seen.lines().next().unwrap();
                let records_file = first.split('"').nth(1).unwrap();
                assert!(
                    !Path::new(records_file).parent().unwrap().exists(),
                    "{}",
                    case["name"]
                );
            }
        }
    }

    #[test]
    fn isolated_home_copies_config_and_state() {
        let dir = TestDir::new("bind-check-home");
        let home = dir.path().join("home");
        std::fs::create_dir_all(home.join(".config/hypr/sub")).unwrap();
        std::fs::write(home.join(".config/hypr/hyprland.lua"), "x").unwrap();
        std::os::unix::fs::symlink("hyprland.lua", home.join(".config/hypr/sub/link")).unwrap();
        let state = home.join(".local/state/omarchy");
        std::fs::create_dir_all(state.join("current")).unwrap();
        std::fs::write(state.join("current/theme"), "t").unwrap();
        std::fs::write(state.join("toggles"), "f").unwrap();
        std::os::unix::fs::symlink("/nonexistent", state.join("settings")).unwrap();
        let tmp = isolated_home(&home, &home.join(".config/hypr")).unwrap();
        let mode = std::fs::metadata(&tmp).unwrap().permissions().mode() & 0o777;
        let read = |p: &str| std::fs::read_to_string(tmp.join(p)).unwrap();
        let link = std::fs::read_link(tmp.join(".config/hypr/sub/link")).unwrap();
        let settings = std::fs::read_link(tmp.join(".local/state/omarchy/settings")).unwrap();
        std::fs::remove_dir_all(&tmp).unwrap();
        assert_eq!(mode, 0o700);
        let _ = read;
        assert_eq!(link, Path::new("hyprland.lua"));
        assert_eq!(settings, Path::new("/nonexistent"));
    }

    #[test]
    fn splitlines_matches_python() {
        assert_eq!(
            py_splitlines("a\nb\r\nc\rd\u{2028}e\n"),
            ["a", "b", "c", "d", "e"]
        );
        assert_eq!(py_splitlines(""), Vec::<&str>::new());
        assert_eq!(py_splitlines("\n\n"), ["", ""]);
    }
}
