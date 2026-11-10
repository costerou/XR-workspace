//! `xr-workspace install`: the Rust port of `python -m tools.xr_controller install`.
//!
//! It writes the same layout under `$XDG_DATA_HOME/viture-workspace`, so the Python readers
//! (`load_installed`, `load_shim`, `pro2_route.load_route`) read it unchanged and a rollback is
//! still "restore the previous pin files and unit":
//!
//! - `bin/<sha12>/` with the renderer, the plugin and the Luma driver, named by their hashes, and
//!   `installed.json` naming them, in exactly the shape the Python installer writes.
//! - `quiet-driver.pin.json` and the depth engine launcher beside the renderer.
//! - `pro2/<manifest-sha12>/` and `pro2-bundle.pin.json`, as `tools/pro2_runtime/package.py` builds them.
//!
//! New here: the controller, the supervisor and the key helper are pinned too. Today they are the
//! Python in `tools/`, which the Python installer leaves running from the checkout. This copies
//! the checkout's tracked `tools/`, `packaging/` and `omarchy-plugin/`, plus this binary, into
//! `workspace/<manifest-sha12>/`, names it in `workspace.pin.json` (its own file: `installed.json`
//! is read strictly and an older controller refuses any extra key), and writes the controller unit
//! to run from that bundle. Every path the Python code derives from its own `__file__` then lands
//! in the bundle, so nothing a run starts comes from the checkout. Hash folders are never
//! replaced or removed, so the pins of an earlier install stay true.
//!
//! The install enables, starts and restarts nothing; the CLI only runs `systemctl --user
//! daemon-reload`, as the Python installer does.

use std::fmt;
use std::fs::{self, File, OpenOptions};
use std::io::{self, Read, Write};
use std::os::unix::fs::{DirBuilderExt, OpenOptionsExt, PermissionsExt};
use std::path::{Component, Path, PathBuf};
use std::process::Command;
use std::sync::atomic::{AtomicU64, Ordering};
use std::time::{SystemTime, UNIX_EPOCH};

use sha2::{Digest, Sha256};

use crate::json::Json;

pub const RENDERER: &str = "xr-native-spike";
pub const PLUGIN: &str = "libxr-ownership.so";
pub const SHIM: &str = "libxr-quiet-driver.so";
pub const SHIM_PIN: &str = "quiet-driver.pin.json";
pub const LUMA_DRIVER: &str = "xr-luma-driver";
pub const LUMA_KEY: &str = "luma_driver";
pub const ENGINE: &str = "xr-depth-engine";
pub const PRO2_DIR: &str = "pro2";
pub const PRO2_PIN: &str = "pro2-bundle.pin.json";
pub const WORKSPACE_DIR: &str = "workspace";
pub const WORKSPACE_PIN: &str = "workspace.pin.json";
pub const SELF_NAME: &str = "xr-workspace";
pub const UNIT: &str = "viture-workspace-controller.service";
const ENGINE_PYTHON: [&str; 5] = [".cache", "viture-depth", "venv", "bin", "python"];
/// What the workspace bundle carries from the checkout: the controller, supervisor and key helper
/// (`tools/`), the unit template (`packaging/`) and the panel (`omarchy-plugin/`).
const WORKSPACE_SOURCES: [&str; 3] = ["tools", "packaging", "omarchy-plugin"];
const PRO2_SOURCES: [&str; 3] = ["tools", "omarchy-plugin", "docs/pro2"];
// tools/pro2_runtime/package.py and launch.py: the Pro 2 manifest's fixed fields.
const PRO2_SDK_SHA: &str = "8dbe441e41aacf3592e94a94e8151a644c1a6f3749b2f684c33282b251b3e19e";
const PRO2_FIRMWARE: &str = "30.0.00.002_20260804";
const PRO2_PROJECTION: &str =
    "explicit provisional diagonal44.5..50, seed44.5 TBD; no optical correction";
const PRO2_TEST_SCRIPT: &str = r#"#!/bin/sh
set -eu
ROOT=$(CDPATH= cd -- "$(dirname -- "$0")/.." && pwd)
exec python3 -I -c 'import sys;sys.path.insert(0,sys.argv.pop(1));from tools.xr_controller.pro2_test import main;raise SystemExit(main())' "$ROOT" --bundle "$ROOT" "$@"
"#;
const PRO2_PARENT_SCRIPT: &str = r#"#!/bin/sh
set -eu
ROOT=$(CDPATH= cd -- "$(dirname -- "$0")/.." && pwd)
exec python3 -I -c 'import sys;sys.path.insert(0,sys.argv.pop(1));from tools.pro2_runtime.supervised_parent import main;raise SystemExit(main())' "$ROOT" "$@"
"#;

#[derive(Debug)]
pub struct Error(pub String);

impl fmt::Display for Error {
    fn fmt(&self, f: &mut fmt::Formatter<'_>) -> fmt::Result {
        f.write_str(&self.0)
    }
}

impl std::error::Error for Error {}

impl From<io::Error> for Error {
    fn from(e: io::Error) -> Self {
        Error(e.to_string())
    }
}

fn err<T>(msg: impl Into<String>) -> Result<T, Error> {
    Err(Error(msg.into()))
}

pub type Result<T, E = Error> = std::result::Result<T, E>;

/// Where everything comes from and goes to. The CLI fills it from the environment.
#[derive(Clone)]
pub struct Options {
    /// The CMake build folder (`--build`).
    pub build: PathBuf,
    /// The checkout whose tracked files are pinned.
    pub repo: PathBuf,
    /// `$XDG_DATA_HOME`; the install lives in its `viture-workspace/`.
    pub data_home: PathBuf,
    /// `$XDG_CONFIG_HOME`; the unit goes to its `systemd/user/`.
    pub config_home: PathBuf,
    /// `$HOME`, where the depth engine's environment is looked for.
    pub home: PathBuf,
    /// The `xr-workspace` binary to pin (the running one, for the CLI).
    pub self_exe: PathBuf,
}

/// One pinned file, as `installed.json` and the pin files name it.
#[derive(Clone, Debug, PartialEq, Eq)]
pub struct Pin {
    pub path: PathBuf,
    pub sha256: String,
}

impl Pin {
    fn json(&self) -> Json {
        Json::obj([
            ("path", Json::str(path_str(&self.path))),
            ("sha256", Json::str(&self.sha256)),
        ])
    }
}

/// What an install pinned.
pub struct Report {
    /// `(key, pin)` in `installed.json`'s order: renderer, plugin, Luma driver.
    pub installed: Vec<(String, Pin)>,
    pub pro2_bundle: PathBuf,
    pub workspace_bundle: PathBuf,
    pub unit: PathBuf,
}

impl Report {
    pub fn installed_json(&self) -> Json {
        Json::Obj(
            self.installed
                .iter()
                .map(|(k, p)| (k.clone(), p.json()))
                .collect(),
        )
    }
}

/// The whole install, in the Python installer's order: a step that fails leaves every pin written
/// before it as it was. The bundles come first, since they only add new hash folders; the unit
/// comes last, so it never names a bundle whose pins were not written.
pub fn install(o: &Options) -> Result<Report> {
    let luma = luma_output(&o.build).ok_or_else(|| {
        Error("this build has no xr-luma-driver, the Luma Ultra's default: build with -DXR_ENABLE_LUMA_DRIVER=ON".into())
    })?;
    let root = root(&o.data_home);
    create_dir_mode(&root, true)?;
    let workspace = workspace_bundle(&o.repo, &root, &o.self_exe)?;
    let pro2_bundle = pro2_bundle(&o.build, &o.repo, &root, &o.home, &workspace.path)?;
    let installed = install_binaries(&o.build, &luma, &root, &o.home, &workspace.path)?;
    let pin = Json::obj([
        ("schema", Json::Int(1)),
        ("bundle", Json::str(path_str(&workspace.path))),
        ("manifest_sha256", Json::str(&workspace.manifest_sha256)),
    ]);
    atomic_write(&root.join(WORKSPACE_PIN), pin.compact().as_bytes(), 0o600)?;
    let unit = write_unit(&workspace.path, &o.config_home)?;
    Ok(Report {
        installed,
        pro2_bundle,
        workspace_bundle: workspace.path,
        unit,
    })
}

pub fn root(data_home: &Path) -> PathBuf {
    data_home.join("viture-workspace")
}

/// The built Luma driver, if this build has one: beside the renderer or in its own build folder.
pub fn luma_output(build: &Path) -> Option<PathBuf> {
    [
        build.join(LUMA_DRIVER),
        build.join("driver").join(LUMA_DRIVER),
    ]
    .into_iter()
    .find(|p| p.is_file())
}

/// `install.install`: copy each binary into `bin/<sha12>/` and write `installed.json`.
fn install_binaries(
    build: &Path,
    luma: &Path,
    root: &Path,
    home: &Path,
    workspace: &Path,
) -> Result<Vec<(String, Pin)>> {
    let sources = [
        ("renderer", build.join(RENDERER)),
        ("plugin", build.join(PLUGIN)),
        (LUMA_KEY, luma.to_path_buf()),
    ];
    let mut shas = Vec::new();
    for (_, path) in &sources {
        shas.push(sha_file(path).map_err(|e| Error(format!("build output unreadable: {e}")))?);
    }
    // The Luma driver joins the hash, so a new driver alone gets its own folder and a running session's pins stay true.
    let combined = sha_hex(shas.concat().as_bytes());
    let target = root.join("bin").join(&combined[..12]);
    create_dir_mode(&root.join("bin"), false)?;
    create_dir_mode(&target, false)?;
    let mut installed = Vec::new();
    for ((key, source), sha) in sources.iter().zip(shas) {
        let name = source
            .file_name()
            .ok_or_else(|| Error("build output has no name".into()))?;
        installed.push((key.to_string(), pin_copy(source, &target.join(name), &sha)?));
    }
    let shim = build.join(SHIM);
    if shim.exists() {
        let sha = sha_file(&shim)?;
        let pin = pin_copy(&shim, &target.join(SHIM), &sha)?;
        atomic_write(&root.join(SHIM_PIN), pin.json().pretty().as_bytes(), 0o600)?;
    } else {
        remove_if_present(&root.join(SHIM_PIN))?;
    }
    write_engine(&target, home, workspace)?;
    let entry = Json::Obj(
        installed
            .iter()
            .map(|(k, p)| (k.clone(), p.json()))
            .collect(),
    );
    atomic_write(
        &root.join("installed.json"),
        entry.pretty().as_bytes(),
        0o600,
    )?;
    Ok(installed)
}

/// Copy `source` to `dest` read-only, unless identical bytes are already pinned there.
fn pin_copy(source: &Path, dest: &Path, sha: &str) -> Result<Pin> {
    let pin = Pin {
        path: dest.to_path_buf(),
        sha256: sha.to_string(),
    };
    if verify_pinned(&pin) {
        return Ok(pin);
    }
    let dir = dest
        .parent()
        .ok_or_else(|| Error("pin has no folder".into()))?;
    let (mut out, tmp) = temp_file(dir)?;
    let result = (|| -> Result<()> {
        io::copy(&mut File::open(source)?, &mut out)?;
        out.sync_all()?;
        fs::set_permissions(&tmp, fs::Permissions::from_mode(0o500))?;
        if sha_file(&tmp)? != sha {
            return err("build output changed during install");
        }
        fs::rename(&tmp, dest)?;
        Ok(())
    })();
    if result.is_err() {
        let _ = fs::remove_file(&tmp);
    }
    result.map(|()| pin)
}

pub fn verify_pinned(pin: &Pin) -> bool {
    sha_file(&pin.path).is_ok_and(|sha| sha == pin.sha256)
}

/// `install._write_engine`: the AI depth engine's launcher, byte for byte as the Python writes it,
/// except that it names the pinned workspace bundle instead of the checkout. No environment, no
/// launcher.
fn write_engine(target: &Path, home: &Path, repo: &Path) -> Result<Option<PathBuf>> {
    let python = ENGINE_PYTHON
        .iter()
        .fold(home.to_path_buf(), |p, part| p.join(part));
    let dest = target.join(ENGINE);
    if !python.is_file() {
        remove_if_present(&dest)?;
        return Ok(None);
    }
    let (repo, python) = (path_str(repo), path_str(&python));
    let safe = |s: &str| {
        !s.is_empty()
            && s.bytes()
                .all(|b| b.is_ascii_alphanumeric() || b"_./-".contains(&b))
    };
    if !safe(&repo) || !safe(&python) {
        return err("unsafe path for the depth engine");
    }
    let run = format!(
        "import sys,runpy; sys.path.insert(0,'{repo}'); sys.argv[0]='xr-depth-engine'; runpy.run_module('tools.depth_engine',run_name='__main__')"
    );
    let text =
        format!("#!/bin/sh\nexec nice -n 5 {python} -I -c \"{run}\" serve --supervised \"$@\"\n");
    atomic_write(&dest, text.as_bytes(), 0o500)?;
    Ok(Some(dest))
}

struct Bundle {
    path: PathBuf,
    manifest_sha256: String,
}

/// The controller, supervisor and key helper, with this binary, as one immutable folder named by
/// its manifest hash.
fn workspace_bundle(repo: &Path, root: &Path, self_exe: &Path) -> Result<Bundle> {
    let parent = root.join(WORKSPACE_DIR);
    create_dir_mode(&parent, false)?;
    let staging = Staging::new(&parent)?;
    copy_tracked(repo, &WORKSPACE_SOURCES, &staging.0)?;
    create_dir_mode(&staging.0.join("bin"), false)?;
    fs::copy(self_exe, staging.0.join("bin").join(SELF_NAME))?;
    let files = hash_tree(&staging.0)?;
    let manifest = Json::obj([
        ("schema", Json::Int(1)),
        ("source_commit", Json::str(source_commit(repo)?)),
        ("files", files_json(&files)),
    ]);
    staging.finish(&parent, &manifest, &files)
}

/// `install.install_pro2_bundle` with `tools/pro2_runtime/package.build`: the Pro 2's bridge,
/// renderer, plugin and tools, pinned by `pro2-bundle.pin.json`. Compiles the bridge; loads no SDK
/// and opens no device.
fn pro2_bundle(
    build: &Path,
    repo: &Path,
    root: &Path,
    home: &Path,
    workspace: &Path,
) -> Result<PathBuf> {
    let parent = root.join(PRO2_DIR);
    create_dir_mode(&parent, false)?;
    let staging = Staging::new(&parent)?;
    let dest = &staging.0;
    let bin = dest.join("bin");
    create_dir_mode(&bin, false)?;
    let mut compile = Command::new("c++");
    compile
        .args([
            "-std=c++20",
            "-O2",
            "-Wall",
            "-Wextra",
            "-Werror",
            "-Wno-misleading-indentation",
            "-pthread",
        ])
        .arg(format!(
            "-I{}",
            path_str(&repo.join("spike/driver/include"))
        ))
        .arg(repo.join("tools/pro2_runtime/bridge.cpp"))
        .arg(repo.join("spike/driver/src/publish.cpp"))
        .args(["-lcrypto", "-ldl", "-o"])
        .arg(bin.join("pro2-sdk-bridge"));
    run(&mut compile, "compiling the Pro 2 bridge")?;
    for name in [RENDERER, PLUGIN] {
        fs::copy(build.join(name), bin.join(name))?;
    }
    copy_tracked(repo, &PRO2_SOURCES, dest)?;
    fs::write(bin.join("pro2-supervised-test"), PRO2_TEST_SCRIPT)?;
    fs::write(bin.join("pro2-supervised-parent"), PRO2_PARENT_SCRIPT)?;
    let abi = output(
        Command::new("pkg-config").args(["--modversion", "hyprland", "aquamarine"]),
        "pkg-config",
    )?;
    let mut files = hash_tree(dest)?;
    // As install.py does: the optional engine joins the manifest last, before the bundle is named.
    if let Some(engine) = write_engine(&bin, home, workspace)? {
        files.push((format!("bin/{ENGINE}"), sha_file(&engine)?));
    }
    let manifest = Json::obj([
        ("schema", Json::Int(1)),
        ("sdk_sha256", Json::str(PRO2_SDK_SHA)),
        ("firmware", Json::str(PRO2_FIRMWARE)),
        ("source_commit", Json::str(source_commit(repo)?)),
        ("projection", Json::str(PRO2_PROJECTION)),
        (
            "ownership_abi",
            Json::List(abi.lines().map(Json::str).collect()),
        ),
        ("files", files_json(&files)),
    ]);
    let bundle = staging.finish(&parent, &manifest, &files)?;
    let pin = Json::obj([
        ("schema", Json::Int(1)),
        ("model", Json::str("pro2")),
        ("bundle", Json::str(path_str(&bundle.path))),
        ("manifest_sha256", Json::str(&bundle.manifest_sha256)),
    ]);
    atomic_write(&root.join(PRO2_PIN), pin.compact().as_bytes(), 0o600)?;
    Ok(bundle.path)
}

/// A bundle being built in `<parent>/.stage-<hex>`, removed unless it was named.
struct Staging(PathBuf);

impl Staging {
    fn new(parent: &Path) -> Result<Self> {
        let path = parent.join(format!(".stage-{}", unique()));
        fs::DirBuilder::new().mode(0o700).create(&path)?;
        Ok(Staging(path))
    }

    /// Write the manifest, make every file read-only, and name the folder by the manifest's hash.
    /// An existing folder of that name is kept and checked, never replaced.
    fn finish(self, parent: &Path, manifest: &Json, files: &[(String, String)]) -> Result<Bundle> {
        let text = manifest.pretty() + "\n";
        let manifest_path = self.0.join("manifest.json");
        fs::write(&manifest_path, &text)?;
        for (name, _) in files {
            let mode = if name.starts_with("bin/") && !name["bin/".len()..].contains('/') {
                0o500
            } else {
                0o400
            };
            fs::set_permissions(self.0.join(name), fs::Permissions::from_mode(mode))?;
        }
        fs::set_permissions(&manifest_path, fs::Permissions::from_mode(0o400))?;
        let manifest_sha256 = sha_hex(text.as_bytes());
        let path = parent.join(&manifest_sha256[..12]);
        check_bundle_location(&path)?;
        if path.exists() {
            if sha_file(&path.join("manifest.json"))? != manifest_sha256 {
                return err(format!("an installed bundle changed: {}", path_str(&path)));
            }
            for (name, sha) in files {
                if !verify_pinned(&Pin {
                    path: path.join(name),
                    sha256: sha.clone(),
                }) {
                    return err(format!(
                        "an installed bundle changed: {}",
                        path_str(&path.join(name))
                    ));
                }
            }
        } else {
            fs::rename(&self.0, &path)?;
        }
        if path.canonicalize()? != path {
            return err("a bundle's folder must not traverse symlinks");
        }
        Ok(Bundle {
            path,
            manifest_sha256,
        })
    }
}

impl Drop for Staging {
    fn drop(&mut self) {
        if self.0.exists() {
            let _ = fs::remove_dir_all(&self.0);
        }
    }
}

/// `launch.validate_bundle_location`'s grammar: a bundle's path goes into Hyprland plugin requests,
/// Python source and the unit, so every component is plain ASCII with no quoting needed.
fn check_bundle_location(path: &Path) -> Result<()> {
    let ok = path.is_absolute()
        && path.components().skip(1).all(|c| match c {
            Component::Normal(part) => part.to_str().is_some_and(|s| {
                s.bytes()
                    .all(|b| b.is_ascii_alphanumeric() || b"_.-".contains(&b))
                    && s != "."
                    && s != ".."
            }),
            _ => false,
        });
    if ok {
        Ok(())
    } else {
        err(
            "unsupported bundle location: canonical ASCII path without whitespace/control tokens required",
        )
    }
}

/// Copy the checkout's tracked files under `specs` into `dest`, keeping their relative paths.
fn copy_tracked(repo: &Path, specs: &[&str], dest: &Path) -> Result<()> {
    let listed = output(
        Command::new("git")
            .arg("-C")
            .arg(repo)
            .args(["ls-files", "-z", "--"])
            .args(specs),
        "git ls-files",
    )?;
    for name in listed.split('\0').filter(|n| !n.is_empty()) {
        let relative = Path::new(name);
        if relative
            .components()
            .any(|c| !matches!(c, Component::Normal(_)))
        {
            return err(format!("unexpected tracked path {name}"));
        }
        let target = dest.join(relative);
        if let Some(dir) = target.parent() {
            fs::create_dir_all(dir)?;
        }
        fs::copy(repo.join(relative), &target)?;
    }
    Ok(())
}

/// Every regular file under `dir` with its hash, in Python's `sorted(Path.rglob('*'))` order.
fn hash_tree(dir: &Path) -> Result<Vec<(String, String)>> {
    let mut paths = Vec::new();
    let mut stack = vec![dir.to_path_buf()];
    while let Some(next) = stack.pop() {
        for item in fs::read_dir(&next)? {
            let item = item?;
            let kind = item.file_type()?;
            if kind.is_dir() {
                stack.push(item.path());
            } else if kind.is_file() {
                paths.push(item.path());
            }
        }
    }
    paths.sort();
    paths
        .into_iter()
        .map(|p| {
            let name = path_str(p.strip_prefix(dir).expect("under dir"));
            Ok((name, sha_file(&p)?))
        })
        .collect()
}

fn files_json(files: &[(String, String)]) -> Json {
    Json::Obj(
        files
            .iter()
            .map(|(n, s)| (n.clone(), Json::str(s)))
            .collect(),
    )
}

fn source_commit(repo: &Path) -> Result<String> {
    Ok(output(
        Command::new("git")
            .arg("-C")
            .arg(repo)
            .args(["rev-parse", "HEAD"]),
        "git rev-parse",
    )?
    .trim()
    .to_string())
}

/// The controller's command line, as `tools.xr_controller.__main__.isolated('tools.xr_controller', 'serve')`
/// builds it, but rooted at the pinned bundle instead of the checkout.
pub fn controller_argv(bundle: &Path) -> Vec<String> {
    let code = format!(
        "import sys,runpy; sys.path.insert(0,'{}'); runpy.run_module('tools.xr_controller',run_name=\"__main__\")",
        path_str(bundle)
    );
    ["/usr/bin/python3", "-I", "-c", &code, "serve"]
        .map(String::from)
        .to_vec()
}

/// `__main__.install_unit`: the controller unit from the bundle's template, run from the bundle.
fn write_unit(bundle: &Path, config_home: &Path) -> Result<PathBuf> {
    check_bundle_location(bundle)?;
    let template = fs::read_to_string(bundle.join("packaging").join(UNIT))?;
    let quote = |s: &str| {
        format!(
            "\"{}\"",
            s.replace('\\', "\\\\")
                .replace('"', "\\\"")
                .replace('%', "%%")
        )
    };
    let exec = controller_argv(bundle)
        .iter()
        .map(|a| quote(a))
        .collect::<Vec<_>>()
        .join(" ");
    let text = template
        .replace("@REPO@", &path_str(bundle).replace('%', "%%"))
        .replace("@EXEC@", &exec);
    let dir = config_home.join("systemd/user");
    fs::create_dir_all(&dir)?;
    let dest = dir.join(UNIT);
    atomic_write(&dest, text.as_bytes(), 0o644)?;
    Ok(dest)
}

/// SHA-256 of a regular file, never following a symlink.
pub fn sha_file(path: &Path) -> Result<String> {
    let mut file = OpenOptions::new()
        .read(true)
        .custom_flags(libc_o_nofollow())
        .open(path)?;
    if !file.metadata()?.file_type().is_file() {
        return err("not a regular file");
    }
    let mut digest = Sha256::new();
    let mut buf = vec![0u8; 1 << 20];
    loop {
        let n = file.read(&mut buf)?;
        if n == 0 {
            break;
        }
        digest.update(&buf[..n]);
    }
    Ok(hex(&digest.finalize()))
}

const fn libc_o_nofollow() -> i32 {
    0o400000 // O_NOFOLLOW on Linux (x86_64 and aarch64). std opens with O_CLOEXEC already.
}

pub fn sha_hex(data: &[u8]) -> String {
    hex(&Sha256::digest(data))
}

fn hex(bytes: &[u8]) -> String {
    bytes.iter().map(|b| format!("{b:02x}")).collect()
}

/// Write `data` to `path` through a synced temporary file in the same folder, then rename.
pub fn atomic_write(path: &Path, data: &[u8], mode: u32) -> Result<()> {
    let dir = path.parent().ok_or_else(|| Error("no folder".into()))?;
    let (mut file, tmp) = temp_file(dir)?;
    let result = (|| -> Result<()> {
        file.set_permissions(fs::Permissions::from_mode(mode))?;
        file.write_all(data)?;
        file.sync_all()?;
        fs::rename(&tmp, path)?;
        Ok(())
    })();
    if result.is_err() {
        let _ = fs::remove_file(&tmp);
    }
    result
}

fn temp_file(dir: &Path) -> Result<(File, PathBuf)> {
    let path = dir.join(format!(".tmp-{}", unique()));
    let file = OpenOptions::new()
        .write(true)
        .create_new(true)
        .mode(0o600)
        .open(&path)?;
    Ok((file, path))
}

fn unique() -> String {
    static COUNTER: AtomicU64 = AtomicU64::new(0);
    let nanos = SystemTime::now()
        .duration_since(UNIX_EPOCH)
        .map_or(0, |d| d.as_nanos());
    format!(
        "{}-{nanos:x}-{}",
        std::process::id(),
        COUNTER.fetch_add(1, Ordering::Relaxed)
    )
}

fn create_dir_mode(path: &Path, parents: bool) -> Result<()> {
    let mut builder = fs::DirBuilder::new();
    builder.mode(0o700).recursive(parents);
    match builder.create(path) {
        Err(e) if e.kind() == io::ErrorKind::AlreadyExists && path.is_dir() => Ok(()),
        other => Ok(other?),
    }
}

fn remove_if_present(path: &Path) -> Result<()> {
    match fs::remove_file(path) {
        Err(e) if e.kind() == io::ErrorKind::NotFound => Ok(()),
        other => Ok(other?),
    }
}

fn run(command: &mut Command, what: &str) -> Result<()> {
    let status = command
        .status()
        .map_err(|e| Error(format!("{what}: {e}")))?;
    if status.success() {
        Ok(())
    } else {
        err(format!("{what} failed: {status}"))
    }
}

fn output(command: &mut Command, what: &str) -> Result<String> {
    let out = command
        .output()
        .map_err(|e| Error(format!("{what}: {e}")))?;
    if !out.status.success() {
        return err(format!(
            "{what} failed: {}",
            String::from_utf8_lossy(&out.stderr).trim()
        ));
    }
    String::from_utf8(out.stdout).map_err(|_| Error(format!("{what}: output is not UTF-8")))
}

fn path_str(path: &Path) -> String {
    path.to_string_lossy().into_owned()
}
