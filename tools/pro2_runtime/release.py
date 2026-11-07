"""Assemble one supervised-test release: runtime bundle, review copy, README, release.json, SHA256SUMS and an archive. No SDK load."""
import argparse
import hashlib
import json
from pathlib import Path
import shutil
import subprocess
import tarfile

from tools.native_spike.display_policy import PRO2_MOUNT_PITCH, PRO2_POSE_RATE
from tools.pro2_runtime.package import ROOT, build

README = """# Pro 2 supervised comparison release

Source {source}. Offline-tested only until the run in docs/pro2/smooth-live-test.md happens.

1. Check:  sha256sum -c SHA256SUMS
2. At the final location:  runtime/bin/pro2-supervised-test --preflight
   Expect this source, {files} pins, plugin_command_path validated, sdk_started false, compositor_contacted false.
3. Run, with Cody's agreement, one short test:  runtime/bin/pro2-supervised-parent --bundle "$PWD/runtime"

Projection is an approximate 44.5 degree diagonal; no optical correction. The steps, what to look at and how to read the result
are in runtime/docs/pro2/smooth-live-test.md. Installing nothing is needed to run this. The panel's normal Start runs the bundle
`python -m tools.xr_controller install` pins, not this one.
"""


def sums(root):
    lines = []
    for path in sorted(p for p in root.rglob('*') if p.is_file() and p.name != 'SHA256SUMS'):
        lines.append(f'{hashlib.sha256(path.read_bytes()).hexdigest()}  {path.relative_to(root)}')
    return '\n'.join(lines) + '\n'


def assemble(build_dir, destination):
    destination = Path(destination).absolute()
    destination.mkdir(mode=0o700, parents=True, exist_ok=False)
    release = destination / 'pro2-runtime-release'
    release.mkdir(mode=0o700)
    manifest = build(build_dir, release / 'runtime')
    review = release / 'review'
    names = subprocess.check_output(['git', 'ls-files', '-z', 'docs/pro2', 'tests/pro2_runtime', 'tests/pro2_runtime_qa', 'tests/pro2_cli'], cwd=ROOT).split(b'\0')
    for item in names:
        if not item: continue
        target = review / item.decode()
        target.parent.mkdir(parents=True, exist_ok=True); shutil.copy2(ROOT / item.decode(), target)
    (release / 'README.md').write_text(README.format(source=manifest['source_commit'], files=len(manifest['files'])))
    runtime_sha = hashlib.sha256((release / 'runtime/manifest.json').read_bytes()).hexdigest()
    native = {name: manifest['files']['bin/' + name] for name in ('pro2-sdk-bridge', 'xr-native-spike', 'libxr-ownership.so')}
    (release / 'release.json').write_text(json.dumps(dict(source=manifest['source_commit'], manifest_sha256=runtime_sha, runtime_files=len(manifest['files']),
                                                          sdk_sha256=manifest['sdk_sha256'], firmware=manifest['firmware'], native=native, smoothing=dict(tau_ms=35, look_ahead_cfg=[0, 0, 0, 40], default_pose_rate_hz=PRO2_POSE_RATE, default_mount_pitch_deg=PRO2_MOUNT_PITCH)), indent=2) + '\n')
    (release / 'SHA256SUMS').write_text(sums(release))
    archive = destination / f"pro2-runtime-release-{manifest['source_commit'][:7]}.tar.gz"
    with tarfile.open(archive, 'w:gz') as stream: stream.add(release, arcname='pro2-runtime-release')
    return dict(release=str(release), archive=str(archive), archive_sha256=hashlib.sha256(archive.read_bytes()).hexdigest(), manifest_sha256=runtime_sha,
                source=manifest['source_commit'], runtime_files=len(manifest['files']), native=native)


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--build', required=True, type=Path); parser.add_argument('--destination', required=True, type=Path)
    args = parser.parse_args()
    print(json.dumps(assemble(args.build, args.destination), indent=2))
