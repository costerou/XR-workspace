"""The Pro 2 bridge binary the suites run: the shipped C++ one by default, or the Rust port.

    PRO2_BRIDGE_RUST_DIR=<dir>   copy the Rust bridge from <dir> instead of compiling bridge.cpp

The suites run in private user and network namespaces, often with a private HOME, where cargo
cannot work, so the Rust bridge is built first, outside them:

    /usr/bin/python3 tests/pro2_bridge.py <dir>

That leaves <dir>/synthetic/xr-pro2-bridge (with --fixture-root and --fixture-hash, as
-DPRO2_SYNTHETIC) and <dir>/production/xr-pro2-bridge. Then, for example:

    PRO2_BRIDGE_RUST_DIR=<dir> python3 tests/pro2_runtime_qa/run.py
"""
import os
from pathlib import Path
import shutil
import subprocess
import sys

ROOT = Path(__file__).resolve().parents[1]
CXX = ['c++', '-std=c++20', '-pthread', '-Ispike/driver/include', 'tools/pro2_runtime/bridge.cpp',
       'spike/driver/src/publish.cpp', 'spike/driver/src/predict.cpp', 'spike/driver/src/vsync.cpp', 'spike/driver/src/rt.cpp',
       '-lcrypto', '-ldl']


def cpp_command(out, synthetic=True):
    """The C++ bridge's build, as tools/pro2_runtime/package.py builds it (plus -DPRO2_SYNTHETIC)."""
    return CXX[:2] + (['-DPRO2_SYNTHETIC'] if synthetic else []) + CXX[2:] + ['-o', str(out)]


def implementation():
    return 'rust' if os.environ.get('PRO2_BRIDGE_RUST_DIR') else 'cpp'


def build_bridge(out, synthetic=True):
    """Puts the bridge at `out`: the Rust build from PRO2_BRIDGE_RUST_DIR if set, else bridge.cpp compiled."""
    rust = os.environ.get('PRO2_BRIDGE_RUST_DIR')
    if rust:
        source = Path(rust) / ('synthetic' if synthetic else 'production') / 'xr-pro2-bridge'
        if not source.is_file():
            raise FileNotFoundError(f'{source}: build it with /usr/bin/python3 tests/pro2_bridge.py {rust}')
        shutil.copyfile(source, out)
        os.chmod(out, 0o700)
        return
    subprocess.run(cpp_command(out, synthetic), cwd=ROOT, check=True, capture_output=True)


def build_rust(directory):
    """Both Rust builds, release, into `directory`."""
    directory = Path(directory).resolve()
    for kind, features in (('synthetic', ['--features', 'synthetic']), ('production', [])):
        target = directory / 'target' / kind
        subprocess.run(['cargo', 'build', '--release', '--locked', '-p', 'xr-pro2-bridge', *features, '--target-dir', str(target)],
                       cwd=ROOT / 'rust', check=True)
        (directory / kind).mkdir(parents=True, exist_ok=True)
        shutil.copyfile(target / 'release' / 'xr-pro2-bridge', directory / kind / 'xr-pro2-bridge')
        os.chmod(directory / kind / 'xr-pro2-bridge', 0o700)


if __name__ == '__main__':
    if len(sys.argv) != 2:
        raise SystemExit(__doc__)
    build_rust(sys.argv[1])
