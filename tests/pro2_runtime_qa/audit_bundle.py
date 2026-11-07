"""Read-only independent bundle audit. Never execute artifacts or load any SDK."""
import argparse
import hashlib
import json
from pathlib import Path
import subprocess

PIN = '8dbe441e41aacf3592e94a94e8151a644c1a6f3749b2f684c33282b251b3e19e'
REPO = Path(__file__).resolve().parents[2]


def audit(bundle, source):
    root = Path(bundle).resolve(strict=True)
    data = json.loads((root / 'manifest.json').read_text())
    if data.get('schema') != 1 or data.get('sdk_sha256') != PIN or data.get('firmware') != '30.0.00.002_20260804':
        raise ValueError('bundle pin/schema mismatch')
    expected = subprocess.check_output(['git', 'rev-parse', source], cwd=REPO, text=True).strip()
    if data.get('source_commit') != expected:
        raise ValueError('bundle source mismatch: ' + str(data.get('source_commit')) + ' != ' + expected)
    for name, digest in data['files'].items():
        relative = Path(name)
        if relative.is_absolute() or '..' in relative.parts:
            raise ValueError('unsafe manifest path')
        path = root / relative
        if path.is_symlink() or not path.resolve(strict=True).is_relative_to(root):
            raise ValueError('bundle link escaped immutable artifact')
        if hashlib.sha256(path.read_bytes()).hexdigest() != digest:
            raise ValueError('artifact hash mismatch: ' + name)
    for name in ('tools/pro2_runtime/bridge.cpp', 'tools/pro2_runtime/launch.py',
                 'tools/pro2_runtime/scope.py',
                 'tools/xr_controller/pro2_test.py', 'tools/native_spike/managed.py',
                 'tools/native_spike/supervisor.py'):
        original = subprocess.check_output(['git', 'show', expected + ':' + name], cwd=REPO)
        if (root / name).read_bytes() != original:
            raise ValueError('packaged source differs from pinned commit: ' + name)
    if 'from tools.pro2_runtime.supervised_parent import main' not in (root / 'bin/pro2-supervised-parent').read_text():
        raise ValueError('packaged parent entry missing')
    entry = (root / 'bin/pro2-supervised-test').read_text()
    if 'runpy' in entry or 'from tools.xr_controller.pro2_test import main' not in entry:
        raise ValueError('packaged CLI must use canonical imported main')
    bridge = (root / 'bin/pro2-sdk-bridge').read_bytes()
    renderer = (root / 'bin/xr-native-spike').read_bytes()
    if not bridge.startswith(b'\x7fELF') or not renderer.startswith(b'\x7fELF'):
        raise ValueError('review executables must be ELF artifacts')
    if b'--fixture-root' in bridge or b'--fixture-hash' in bridge:
        raise ValueError('synthetic-only flags found in production bridge')
    if b'--pro2-test-pose-prefix' not in renderer or b'--pro2-test-projection' not in renderer or b'--pro2-test-smooth-ms' not in renderer:
        raise ValueError('renderer missing dedicated supervised route flags')
    if any(n.startswith('tests/') or (Path(n).suffix == '.so' and 'fixture' in Path(n).name.lower()) or Path(n).name == 'libglasses.so' for n in data['files']):
        raise ValueError('synthetic fixture or vendor SDK must not ship in review bundle')
    return dict(source_commit=expected, files_verified=len(data['files']),
                manifest_sha256=hashlib.sha256((root / 'manifest.json').read_bytes()).hexdigest(),
                executables={n: data['files'][n] for n in ('bin/pro2-sdk-bridge', 'bin/xr-native-spike', 'bin/libxr-ownership.so')},
                sdk_loaded=False, artifacts_executed=False)


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('bundle', type=Path)
    parser.add_argument('--source', required=True)
    args = parser.parse_args()
    print(json.dumps(audit(args.bundle, args.source), indent=2))
