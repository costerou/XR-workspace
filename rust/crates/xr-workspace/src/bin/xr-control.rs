//! The bind helper Hyprland runs per bound key press. See `xr_workspace::control`.

fn main() {
    let args: Vec<std::ffi::OsString> = std::env::args_os().collect();
    std::process::exit(xr_workspace::control::main(
        &args,
        &xr_workspace::control::Host::system(),
    ));
}
