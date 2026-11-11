//! The installer's gate: what `xr-workspace install` writes is read unchanged by the Python
//! installer's readers (the "old installer"), restoring the previous pin files rolls an install
//! back, and nothing the install points at runs from the checkout.
//!
//! These run the real checkout's Python (`/usr/bin/python3`), `git`, `c++` and `pkg-config`, into
//! a temporary data, config and home folder. Nothing touches the user's install or systemd.

use std::fs;
use std::os::unix::fs::PermissionsExt;
use std::path::{Path, PathBuf};
use std::process::Command;

use xr_workspace::install::{self, Options, Report};
use xr_workspace::runner;

fn repo() -> PathBuf {
    Path::new(env!("CARGO_MANIFEST_DIR"))
        .join("../../..")
        .canonicalize()
        .unwrap()
}

/// A temporary folder, removed afterwards. Files in the bundles are read-only, so restore write
/// permission on folders before removing.
struct Scratch(PathBuf);

impl Scratch {
    fn new(name: &str) -> Self {
        let base = std::env::temp_dir().canonicalize().unwrap();
        let path = base.join(format!("xr-install-{name}-{}", std::process::id()));
        let _ = fs::remove_dir_all(&path);
        fs::create_dir_all(&path).unwrap();
        Scratch(path)
    }
}

impl Drop for Scratch {
    fn drop(&mut self) {
        let _ = Command::new("chmod")
            .args(["-R", "u+w"])
            .arg(&self.0)
            .status();
        let _ = fs::remove_dir_all(&self.0);
    }
}

struct Setup {
    _scratch: Scratch,
    options: Options,
}

/// A build folder with stand-in binaries, a home with a stand-in depth environment, and empty
/// data and config folders.
fn setup(name: &str) -> Setup {
    let scratch = Scratch::new(name);
    let dir = &scratch.0;
    let build = dir.join("build");
    fs::create_dir_all(build.join("driver")).unwrap();
    fs::write(build.join(install::RENDERER), b"renderer A").unwrap();
    fs::write(build.join(install::PLUGIN), b"plugin A").unwrap();
    fs::write(build.join("driver").join(install::LUMA_DRIVER), b"luma A").unwrap();
    fs::write(build.join(install::SHIM), b"shim A").unwrap();
    let home = dir.join("home");
    let python = home.join(".cache/viture-depth/venv/bin");
    fs::create_dir_all(&python).unwrap();
    fs::write(python.join("python"), b"").unwrap();
    let self_exe = dir.join("xr-workspace-A");
    fs::write(&self_exe, b"xr-workspace A").unwrap();
    let options = Options {
        build,
        repo: repo(),
        data_home: dir.join("data"),
        config_home: dir.join("config"),
        home,
        self_exe,
        rust_renderer: None,
    };
    Setup {
        _scratch: scratch,
        options,
    }
}

/// Run `script` in the checkout's Python, with the installer modules importable and
/// `DATA`, `CONFIG`, `REPO_DIR` set. It asserts; a non-zero exit fails the test.
fn python(o: &Options, extra: &[(&str, &Path)], script: &str) -> String {
    let prelude = "import json, os, sys\nfrom pathlib import Path\nsys.path.insert(0, os.environ['REPO_DIR'])\n\
                   DATA = Path(os.environ['XDG_DATA_HOME']); CONFIG = Path(os.environ['XDG_CONFIG_HOME'])\n";
    let mut command = Command::new("/usr/bin/python3");
    command
        .arg("-I")
        .arg("-c")
        .arg(format!("{prelude}{script}"))
        .env("REPO_DIR", &o.repo)
        .env("XDG_DATA_HOME", &o.data_home)
        .env("XDG_CONFIG_HOME", &o.config_home)
        .env("HOME", &o.home)
        .env("TMPDIR", "/tmp");
    for (k, v) in extra {
        command.env(k, v);
    }
    let out = command.output().unwrap();
    let stdout = String::from_utf8_lossy(&out.stdout).into_owned();
    assert!(
        out.status.success(),
        "python failed:\n{stdout}\n{}",
        String::from_utf8_lossy(&out.stderr)
    );
    stdout
}

fn read(path: &Path) -> String {
    fs::read_to_string(path).unwrap_or_else(|e| panic!("{}: {e}", path.display()))
}

fn root(o: &Options) -> PathBuf {
    install::root(&o.data_home)
}

/// The files a rollback restores.
fn pin_files(o: &Options) -> Vec<PathBuf> {
    let root = root(o);
    vec![
        root.join("installed.json"),
        root.join(install::SHIM_PIN),
        root.join(install::PRO2_PIN),
        root.join(install::WORKSPACE_PIN),
        o.config_home.join("systemd/user").join(install::UNIT),
    ]
}

/// Checks, with the Python installer's own readers, that the install pinned `report`'s files and
/// that every bundle file still matches its manifest.
const OLD_READERS: &str = r#"
from tools.xr_controller import install, pro2_route
from tools.pro2_runtime.launch import verify_manifest
installed = install.load_installed(DATA)
assert list(installed) == ['renderer', 'plugin', 'luma_driver'], installed
for entry in installed.values(): install.verify_pinned(entry)
assert installed == json.loads((DATA / 'viture-workspace/installed.json').read_text())
shim = install.load_shim(DATA)
assert shim == str(Path(installed['renderer']['path']).parent / 'libxr-quiet-driver.so'), shim
drivers = {d['key']: d for d in install.tracking_drivers(DATA, installed)}
assert drivers['luma']['available'] and drivers['luma']['path'] == installed['luma_driver']['path']
route = pro2_route.load_route(DATA)
assert route is not None and route['bundle'] == os.environ['PRO2_BUNDLE'], route
verify_manifest(route['bundle'])
pin = json.loads((DATA / 'viture-workspace/workspace.pin.json').read_text())
assert set(pin) == {'schema', 'bundle', 'manifest_sha256'} and pin['schema'] == 1
bundle = Path(pin['bundle'])
assert str(bundle) == os.environ['WORKSPACE_BUNDLE'], pin
import hashlib
assert hashlib.sha256((bundle / 'manifest.json').read_bytes()).hexdigest() == pin['manifest_sha256']
assert bundle.name == pin['manifest_sha256'][:12]
manifest = json.loads((bundle / 'manifest.json').read_text())
for name, sha in manifest['files'].items(): install.verify_pinned(dict(path=str(bundle / name), sha256=sha))
for name in ('bin/xr-workspace', 'tools/xr_controller/__main__.py', 'tools/xr_controller/runner.py',
             'tools/native_spike/supervisor.py', 'tools/native_spike/xr_control.py', 'packaging/viture-workspace-controller.service'):
    assert name in manifest['files'], name
print('ok')
"#;

fn check_old_readers(o: &Options, report: &Report) {
    let out = python(
        o,
        &[
            ("PRO2_BUNDLE", &report.pro2_bundle),
            ("WORKSPACE_BUNDLE", &report.workspace_bundle),
        ],
        OLD_READERS,
    );
    assert_eq!(out.trim(), "ok");
}

#[test]
fn the_old_installer_reads_what_the_rust_one_writes() {
    let s = setup("readers");
    let o = &s.options;
    let report = install::install(o).unwrap();
    check_old_readers(o, &report);

    // The Python installer, given the same build, writes the same installed.json and quiet-driver
    // pin, byte for byte (into its own data folder; the paths differ only by that folder).
    let python_data = o.data_home.with_file_name("python-data");
    python(
        o,
        &[("BUILD", &o.build), ("PY_DATA", &python_data)],
        "from tools.xr_controller import install\n\
           install.install(Path(os.environ['BUILD']), Path(os.environ['PY_DATA']))\n",
    );
    for name in ["installed.json", install::SHIM_PIN] {
        let ours = read(&root(o).join(name));
        let theirs = read(&install::root(&python_data).join(name));
        let theirs = theirs.replace(
            &python_data.to_string_lossy().into_owned(),
            &o.data_home.to_string_lossy(),
        );
        assert_eq!(ours, theirs, "{name}");
    }

    // The unit is exactly what the Python installer's install_unit writes for a controller
    // living in the bundle.
    let python_config = o.config_home.with_file_name("python-config");
    python(
        o,
        &[
            ("BUNDLE", &report.workspace_bundle),
            ("PY_CONFIG", &python_config),
        ],
        "import subprocess\n\
           import tools.xr_controller.__main__ as m\n\
           m.REPO = Path(os.environ['BUNDLE']); subprocess.run = lambda *a, **k: None\n\
           m.install_unit(Path(os.environ['PY_CONFIG']))\n",
    );
    assert_eq!(
        read(&report.unit),
        read(&python_config.join("systemd/user").join(install::UNIT))
    );
}

#[test]
fn restoring_the_previous_pins_rolls_an_install_back() {
    let s = setup("rollback");
    let o = &s.options;
    let first = install::install(o).unwrap();
    let saved: Vec<(PathBuf, Vec<u8>)> = pin_files(o)
        .into_iter()
        .map(|p| {
            let b = fs::read(&p).unwrap();
            (p, b)
        })
        .collect();

    // The same inputs again: the same folders, nothing new.
    let again = install::install(o).unwrap();
    assert_eq!(
        (&again.pro2_bundle, &again.workspace_bundle),
        (&first.pro2_bundle, &first.workspace_bundle)
    );
    assert_eq!(again.installed, first.installed);
    for (path, bytes) in &saved {
        assert_eq!(&fs::read(path).unwrap(), bytes, "{}", path.display());
    }

    // A new renderer and a new xr-workspace: every pin moves to new folders.
    fs::write(o.build.join(install::RENDERER), b"renderer B").unwrap();
    let self_b = o.self_exe.with_file_name("xr-workspace-B");
    fs::write(&self_b, b"xr-workspace B").unwrap();
    let second = install::install(&Options {
        self_exe: self_b,
        ..o.clone()
    })
    .unwrap();
    assert_ne!(second.pro2_bundle, first.pro2_bundle);
    assert_ne!(second.workspace_bundle, first.workspace_bundle);
    assert_ne!(
        second.installed[0].1.path.parent(),
        first.installed[0].1.path.parent()
    );
    for (path, bytes) in &saved {
        assert_ne!(
            &fs::read(path).unwrap(),
            bytes,
            "{} did not change",
            path.display()
        );
    }
    check_old_readers(o, &second);

    // Rollback: put the first install's pin files and unit back. Its hash folders were kept.
    for (path, bytes) in &saved {
        fs::write(path, bytes).unwrap();
    }
    check_old_readers(o, &first);
    let unit = read(&first.unit);
    assert!(
        unit.contains(&format!(
            "WorkingDirectory={}\n",
            first.workspace_bundle.display()
        )),
        "{unit}"
    );
    assert!(second.workspace_bundle.is_dir() && second.pro2_bundle.is_dir());
}

#[test]
fn nothing_the_install_points_at_runs_from_the_checkout() {
    let s = setup("checkout");
    let o = &s.options;
    let report = install::install(o).unwrap();
    let bundle = &report.workspace_bundle;
    let checkout = o.repo.to_string_lossy().into_owned();

    // No written pin, unit or launcher names the checkout; the unit and launchers name the bundle.
    let engine = report.installed[0].1.path.with_file_name(install::ENGINE);
    let pro2_engine = report.pro2_bundle.join("bin").join(install::ENGINE);
    for path in pin_files(o)
        .into_iter()
        .chain([engine.clone(), pro2_engine.clone()])
    {
        let text = read(&path);
        assert!(
            !text.contains(&checkout),
            "{} names the checkout:\n{text}",
            path.display()
        );
    }
    let unit = read(&report.unit);
    assert!(
        unit.contains(&format!("WorkingDirectory={}\n", bundle.display())),
        "{unit}"
    );
    assert!(
        unit.contains(&format!("sys.path.insert(0,'{}')", bundle.display())),
        "{unit}"
    );
    for launcher in [&engine, &pro2_engine] {
        assert!(read(launcher).contains(&format!("sys.path.insert(0,'{}')", bundle.display())));
    }

    // The controller the unit starts, and everything it derives from its own location (the
    // supervisor it spawns, the key helper the supervisor binds), live in the bundle.
    let out = python(
        o,
        &[("BUNDLE", bundle)],
        "B = os.environ['BUNDLE']\n\
        sys.path.remove(os.environ['REPO_DIR']); sys.path.insert(0, B)\n\
        import tools.xr_controller.__main__ as m, tools.xr_controller.runner as r, tools.native_spike.supervisor as s, tools.native_spike.view as v\n\
        assert str(m.REPO) == B, m.REPO\n\
        for mod in (m, r, s, v): assert mod.__file__.startswith(B + '/'), mod.__file__\n\
        helper = Path(v.__file__).with_name('xr_control.py'); assert helper.is_file(), helper\n\
        argv = m.isolated('tools.xr_controller.runner', '--consent', 'c')\n\
        assert repr(B) in argv[3] and os.environ['REPO_DIR'] not in ' '.join(argv), argv\n\
        print('ok')\n",
    );
    assert_eq!(out.trim(), "ok");

    // The unit's own command line starts (asked for --help instead of serve), from the bundle.
    let mut argv = install::controller_argv(bundle);
    *argv.last_mut().unwrap() = "--help".into();
    let run = Command::new(&argv[0])
        .args(&argv[1..])
        .current_dir(bundle)
        .env("TMPDIR", "/tmp")
        .output()
        .unwrap();
    assert!(
        run.status.success(),
        "{}",
        String::from_utf8_lossy(&run.stderr)
    );
    assert!(String::from_utf8_lossy(&run.stdout).contains("install"));
}

#[test]
fn a_build_without_the_luma_driver_is_refused_before_anything_is_written() {
    let s = setup("noluma");
    let o = &s.options;
    fs::remove_file(o.build.join("driver").join(install::LUMA_DRIVER)).unwrap();
    let e = install::install(o).err().expect("refused");
    assert!(e.0.contains("no xr-luma-driver"), "{e}");
    assert!(!root(o).exists());
}

/// A stand-in for `cargo build`'s xr-renderer, outside the C++ build folder.
fn rust_renderer(o: &Options, body: &[u8]) -> Options {
    let cargo = o.build.with_file_name("cargo");
    fs::create_dir_all(&cargo).unwrap();
    let binary = cargo.join(install::RUST_RENDERER);
    fs::write(&binary, body).unwrap();
    Options {
        rust_renderer: Some(binary),
        ..o.clone()
    }
}

#[test]
fn the_rust_renderer_is_pinned_beside_the_cpp_one_in_its_own_file() {
    let s = setup("rust-renderer");
    let o = &s.options;
    let plain = install::install(o).unwrap();
    assert!(plain.rust_renderer.is_none());
    let with = rust_renderer(o, b"xr-renderer A");
    let report = install::install(&with).unwrap();
    let pin = report.rust_renderer.clone().expect("the Rust renderer is pinned");

    // In the hash folder's rust/ folder, read-only, its bytes the build's; a new folder, since its hash joins the name.
    let folder = report.installed[0].1.path.parent().unwrap().to_path_buf();
    assert_eq!(
        pin.path,
        folder.join(install::RUST_DIR).join(install::RUST_RENDERER)
    );
    assert_ne!(Some(folder.as_path()), plain.installed[0].1.path.parent());
    assert_eq!(
        fs::metadata(&pin.path).unwrap().permissions().mode() & 0o777,
        0o500
    );
    assert!(install::verify_pinned(&pin));

    // installed.json is what it always was: every old reader still reads it, and its renderer is the C++ one.
    check_old_readers(o, &report);
    let installed = runner::load_installed(&o.data_home).unwrap();
    assert_eq!(installed.renderer, report.installed[0].1);
    assert_eq!(installed.renderer.path, folder.join(install::RENDERER));
    // Both runners' readers find the Rust one in its pin.
    assert_eq!(
        runner::load_rust_renderer(&o.data_home).unwrap(),
        Some(pin.clone())
    );
    let out = python(
        o,
        &[],
        "from tools.xr_controller import install, renderer_switch\n\
           assert install.load_rust_renderer(DATA) == renderer_switch.rust_renderer(DATA)\n\
           print(json.dumps(install.load_rust_renderer(DATA)))\n",
    );
    let theirs: serde_json::Value = serde_json::from_str(&out).unwrap();
    assert_eq!(theirs["path"], pin.path.to_str().unwrap());
    assert_eq!(theirs["sha256"], pin.sha256);

    // The Python installer, given the same build and Rust renderer, writes the same files byte for byte, and prints
    // the same summary `install` prints.
    let python_data = o.data_home.with_file_name("python-data");
    let printed = python(
        o,
        &[
            ("BUILD", &o.build),
            ("PY_DATA", &python_data),
            ("RUST", with.rust_renderer.as_ref().unwrap()),
        ],
        "from tools.xr_controller import install\n\
           print(json.dumps(install.install(Path(os.environ['BUILD']), Path(os.environ['PY_DATA']), \
           rust_renderer=Path(os.environ['RUST'])), indent=2))\n",
    );
    let ours_data = o.data_home.to_string_lossy().into_owned();
    let theirs_data = python_data.to_string_lossy().into_owned();
    assert_eq!(
        report.summary_json().pretty(),
        printed.trim_end().replace(&theirs_data, &ours_data)
    );
    for name in [
        "installed.json",
        install::SHIM_PIN,
        install::RUST_RENDERER_PIN,
    ] {
        let ours = read(&root(o).join(name));
        let theirs = read(&install::root(&python_data).join(name)).replace(&theirs_data, &ours_data);
        assert_eq!(ours, theirs, "{name}");
    }
    assert_eq!(
        fs::metadata(root(o).join(install::RUST_RENDERER_PIN))
            .unwrap()
            .permissions()
            .mode()
            & 0o777,
        0o600
    );

    // A plain install is the way back: the pin goes, the folder it named stays.
    let again = install::install(o).unwrap();
    assert_eq!(again.installed, plain.installed);
    assert!(again.rust_renderer.is_none());
    assert!(!root(o).join(install::RUST_RENDERER_PIN).exists());
    assert!(pin.path.is_file());
    assert_eq!(runner::load_rust_renderer(&o.data_home).unwrap(), None);
    // With no Rust renderer, `install` prints installed.json, as it always did.
    assert_eq!(
        plain.summary_json().pretty(),
        plain.installed_json().pretty()
    );
}

/// The pin files with the Rust renderer's: `None` for one that is absent, which a rollback restores by removing it.
fn saved_pins(o: &Options) -> Vec<(PathBuf, Option<Vec<u8>>)> {
    pin_files(o)
        .into_iter()
        .chain([root(o).join(install::RUST_RENDERER_PIN)])
        .map(|p| {
            let b = fs::read(&p).ok();
            (p, b)
        })
        .collect()
}

fn restore(saved: &[(PathBuf, Option<Vec<u8>>)]) {
    for (path, bytes) in saved {
        match bytes {
            Some(b) => fs::write(path, b).unwrap(),
            None => {
                let _ = fs::remove_file(path);
            }
        }
    }
}

#[test]
fn restoring_the_previous_pins_rolls_the_rust_renderer_in_and_out() {
    let s = setup("rust-rollback");
    let o = &s.options;
    let plain = install::install(o).unwrap();
    let before = saved_pins(o);
    let with = install::install(&rust_renderer(o, b"xr-renderer A")).unwrap();
    let pin = with.rust_renderer.clone().unwrap();
    let after = saved_pins(o);

    // Back to the install before the Rust renderer: no pin, and both readers say so.
    restore(&before);
    check_old_readers(o, &plain);
    assert_eq!(runner::load_rust_renderer(&o.data_home).unwrap(), None);
    assert_eq!(
        runner::load_installed(&o.data_home).unwrap().renderer,
        plain.installed[0].1
    );
    let status = python(
        o,
        &[],
        "from tools.xr_controller import renderer_switch\n\
           print(json.dumps(renderer_switch.status(DATA, CONFIG)))\n",
    );
    let status: serde_json::Value = serde_json::from_str(&status).unwrap();
    assert_eq!(status["renderer"], "cpp");
    assert!(status["rust_renderer"].is_null(), "{status}");

    // And forward again: the Rust pin is back, its folder never left.
    restore(&after);
    check_old_readers(o, &with);
    assert_eq!(
        runner::load_rust_renderer(&o.data_home).unwrap(),
        Some(pin.clone())
    );
    assert!(install::verify_pinned(&pin));
}
