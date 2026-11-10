//! The renderer's geometry against the Python supervisor's: the same contract scripts the C++ dumps pass
//! (`spike/tests/glass_contract_test.py`, `placement_contract_test.py`), run on the Rust dumps.

use std::path::Path;
use std::process::Command;

fn contract(script: &str, dump: &str) {
    let root = Path::new(env!("CARGO_MANIFEST_DIR")).join("../../..");
    let out = Command::new("python3")
        .arg(root.join("spike/tests").join(script))
        .arg(dump)
        .arg(&root)
        .output()
        .expect("python3 runs");
    assert!(
        out.status.success(),
        "{script} failed:\n{}{}",
        String::from_utf8_lossy(&out.stdout),
        String::from_utf8_lossy(&out.stderr)
    );
}

#[test]
fn glass_contract() {
    contract("glass_contract_test.py", env!("CARGO_BIN_EXE_glass_geometry_dump"));
}

#[test]
fn placement_contract() {
    contract("placement_contract_test.py", env!("CARGO_BIN_EXE_placement_geometry_dump"));
}
