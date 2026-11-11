//! Controller, supervisor, key helper and installer.
//!
//! `xr-workspace runs` prints what a controller starting now would adopt, from the same folders the Python
//! controller uses. It only reads: files, `systemctl --user is-active`, and a connect to a run's control socket.
//!
//! `xr-workspace install --build DIR [--repo DIR]`: see [`xr_workspace::install`].
//!
//! `xr-workspace supervisor --consent PATH --state-dir DIR`: one run, as the run unit; see [`xr_workspace::runner`].

mod host;
// The incident monitor runs once the controller state machine is wired into `main`.
#[allow(dead_code)]
mod incident;
use xr_workspace::private_file;
// `spawn` and `stop` are for the controller state machine, which is not wired into `main` yet.
#[cfg(test)]
mod testdir;
#[allow(dead_code)]
mod units;

use std::env;
use std::path::{Path, PathBuf};
use std::process::{Command, ExitCode};

use xr_workspace::install::{self, Options};

const USAGE: &str = "usage: xr-workspace runs | xr-workspace install --build DIR [--repo DIR] [--rust-renderer PATH] | xr-workspace supervisor --consent PATH --state-dir DIR";

fn main() -> ExitCode {
    // startup.py `runner`: when this process began, before anything else runs.
    let started_ns = xr_workspace::runner::monotonic_ns();
    let raw: Vec<std::ffi::OsString> = std::env::args_os().skip(1).collect();
    if raw.first().is_some_and(|c| c == "supervisor") {
        return ExitCode::from(supervisor(&raw[1..], started_ns));
    }
    let args: Vec<String> = std::env::args().skip(1).collect();
    match args.as_slice() {
        [cmd] if cmd == "runs" => runs(),
        [cmd, rest @ ..] if cmd == "install" => {
            match parse_install(rest).and_then(|o| run_install(&o)) {
                Ok(()) => ExitCode::SUCCESS,
                Err(msg) => {
                    eprintln!("install failed: {msg}");
                    ExitCode::FAILURE
                }
            }
        }
        _ => {
            eprintln!("{USAGE}");
            ExitCode::from(2)
        }
    }
}

fn supervisor(args: &[std::ffi::OsString], started_ns: i64) -> u8 {
    use xr_workspace::runner;
    // The loopback backend exists only in debug builds, for the tests (runner/fake.rs).
    #[cfg(debug_assertions)]
    if let Some(fake) = runner::fake::Fake::from_env() {
        return match fake {
            Ok(mut fake) => runner::main(args, &mut fake, started_ns),
            Err(e) => {
                eprintln!("{e}");
                2
            }
        };
    }
    runner::main(args, &mut runner::real::Real::default(), started_ns)
}

fn runs() -> ExitCode {
    let Some(runtime) = std::env::var_os("XDG_RUNTIME_DIR").map(PathBuf::from) else {
        eprintln!("XDG_RUNTIME_DIR not set");
        return ExitCode::from(2);
    };
    let state_home = std::env::var_os("XDG_STATE_HOME")
        .map(PathBuf::from)
        .or_else(|| std::env::var_os("HOME").map(|h| PathBuf::from(h).join(".local/state")));
    let Some(state_dir) = state_home.map(|s| s.join("viture-workspace")) else {
        eprintln!("neither XDG_STATE_HOME nor HOME is set");
        return ExitCode::from(2);
    };
    let units = units::Units::default();
    let result = host::probe_runs(&runtime, &state_dir, &units)
        .and_then(|runs| host::adopt(&runs, &runtime, &state_dir, &units).map(|a| (runs, a)));
    match result {
        Ok((runs, adoption)) => {
            let ids = |r: &[host::Run]| r.iter().map(|r| r.run_id.clone()).collect::<Vec<_>>();
            println!("own unfinished runs: {:?}", ids(&runs.journals));
            println!("external runs: {:?}", ids(&runs.external));
            println!("adoption: {}", adoption.describe());
            ExitCode::SUCCESS
        }
        Err(e) => {
            eprintln!("probe failed: {e}");
            ExitCode::FAILURE
        }
    }
}

fn parse_install(args: &[String]) -> Result<Options, String> {
    let (mut build, mut repo, mut rust_renderer) = (None, None, None);
    let mut it = args.iter();
    while let Some(flag) = it.next() {
        let value = it.next().ok_or_else(|| USAGE.to_string())?;
        match flag.as_str() {
            "--build" => build = Some(PathBuf::from(value)),
            "--repo" => repo = Some(PathBuf::from(value)),
            // The Rust renderer (`cargo build -p xr-renderer`), pinned beside the C++ one; a start runs it only while
            // the renderer setting says rust.
            "--rust-renderer" => rust_renderer = Some(PathBuf::from(value)),
            _ => return Err(USAGE.into()),
        }
    }
    let build = build.ok_or_else(|| USAGE.to_string())?;
    let home = PathBuf::from(env::var_os("HOME").ok_or("HOME is not set")?);
    let xdg = |name: &str, fallback: &str| {
        env::var_os(name)
            .filter(|v| !v.is_empty())
            .map_or_else(|| home.join(fallback), PathBuf::from)
    };
    let absolute = |p: PathBuf| std::path::absolute(&p).map_err(|e| e.to_string());
    Ok(Options {
        build: absolute(build)?,
        // The checkout this binary was built from, as the Python installer uses the checkout it runs from.
        repo: repo
            .unwrap_or_else(|| Path::new(env!("CARGO_MANIFEST_DIR")).join("../../.."))
            .canonicalize()
            .map_err(|e| format!("checkout: {e}"))?,
        data_home: xdg("XDG_DATA_HOME", ".local/share"),
        config_home: xdg("XDG_CONFIG_HOME", ".config"),
        self_exe: env::current_exe().map_err(|e| e.to_string())?,
        rust_renderer: rust_renderer.map(absolute).transpose()?,
        home,
    })
}

fn run_install(o: &Options) -> Result<(), String> {
    let report = install::install(o).map_err(|e| e.to_string())?;
    eprintln!("Pro 2 bundle: {}", report.pro2_bundle.display());
    eprintln!("Workspace bundle: {}", report.workspace_bundle.display());
    let status = Command::new("systemctl")
        .args(["--user", "daemon-reload"])
        .status()
        .map_err(|e| e.to_string())?;
    if !status.success() {
        return Err(format!("systemctl --user daemon-reload failed: {status}"));
    }
    println!("{}", report.summary_json().pretty());
    Ok(())
}
