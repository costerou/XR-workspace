//! Controller, supervisor, key helper and installer.
//!
//! `xr-workspace runs` prints what a controller starting now would adopt, from the same folders the Python
//! controller uses. It only reads: files, `systemctl --user is-active`, and a connect to a run's control socket.

mod host;
mod private_file;
// `spawn` and `stop` are for the controller state machine, which is not wired into `main` yet.
#[cfg(test)]
mod testdir;
#[allow(dead_code)]
mod units;

use std::path::PathBuf;
use std::process::ExitCode;

fn main() -> ExitCode {
    let args: Vec<String> = std::env::args().skip(1).collect();
    match args.as_slice() {
        [cmd] if cmd == "runs" => runs(),
        _ => {
            eprintln!("usage: xr-workspace runs");
            ExitCode::from(2)
        }
    }
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
