"""Build an uninstalled immutable-content Pro2 supervised-test bundle, no SDK load."""
import argparse
import hashlib
import json
from pathlib import Path
import shutil
import subprocess

ROOT=Path(__file__).resolve().parents[2]
SDK_SHA='8dbe441e41aacf3592e94a94e8151a644c1a6f3749b2f684c33282b251b3e19e'

def build(build_dir,destination):
    build_dir=Path(build_dir).absolute();destination=Path(destination).absolute()
    destination.mkdir(mode=0o700,parents=False,exist_ok=False)
    from tools.pro2_runtime.launch import validate_bundle_location
    validate_bundle_location(destination)
    (destination/'bin').mkdir(mode=0o700)
    bridge=destination/'bin/pro2-sdk-bridge'
    subprocess.run(['c++','-std=c++20','-O2','-Wall','-Wextra','-Werror','-Wno-misleading-indentation','-pthread',
        '-I'+str(ROOT/'spike/driver/include'),str(ROOT/'tools/pro2_runtime/bridge.cpp'),str(ROOT/'spike/driver/src/publish.cpp'),'-lcrypto','-ldl','-o',str(bridge)],check=True)
    for name in ('xr-native-spike','libxr-ownership.so'):shutil.copy2(build_dir/name,destination/'bin'/name)
    files=subprocess.check_output(['git','ls-files','-z','tools','omarchy-plugin','docs/pro2'],cwd=ROOT).split(b'\0')
    for item in files:
        if not item:continue
        relative=Path(item.decode());target=destination/relative;target.parent.mkdir(parents=True,exist_ok=True);shutil.copy2(ROOT/relative,target)
    script=destination/'bin/pro2-supervised-test'
    script.write_text('''#!/bin/sh
set -eu
ROOT=$(CDPATH= cd -- "$(dirname -- "$0")/.." && pwd)
exec python3 -I -c 'import sys;sys.path.insert(0,sys.argv.pop(1));from tools.xr_controller.pro2_test import main;raise SystemExit(main())' "$ROOT" --bundle "$ROOT" "$@"
''');script.chmod(0o500)
    manifest=dict(schema=1,sdk_sha256=SDK_SHA,firmware='30.0.00.002_20260804',
        source_commit=subprocess.check_output(['git','rev-parse','HEAD'],cwd=ROOT,text=True).strip(),
        projection='explicit provisional diagonal44.5..50, seed44.5 TBD; no optical correction',
        ownership_abi=subprocess.check_output(['pkg-config','--modversion','hyprland','aquamarine'],text=True).splitlines(),files={})
    for p in sorted(destination.rglob('*')):
        if p.is_file():manifest['files'][str(p.relative_to(destination))]=hashlib.sha256(p.read_bytes()).hexdigest()
    (destination/'manifest.json').write_text(json.dumps(manifest,indent=2)+'\n');(destination/'manifest.json').chmod(0o400)
    for p in destination.rglob('*'):
        if p.is_file():p.chmod(0o500 if p.parent==destination/'bin' else 0o400)
    return manifest

if __name__=='__main__':
    p=argparse.ArgumentParser(description=__doc__);p.add_argument('--build',required=True,type=Path);p.add_argument('--destination',required=True,type=Path);a=p.parse_args()
    result=build(a.build,a.destination);print(json.dumps(dict(source=result['source_commit'],files=len(result['files']))))
