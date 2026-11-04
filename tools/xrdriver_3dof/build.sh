#!/bin/sh
# Builds the 3DoF test XRLinuxDriver for the tracking A/B (docs/tracking-3dof-ab.md). Per user: no sudo, nothing in /usr.
#
# Nothing here runs the driver. xrDriver reads no command line, so ANY run, even with --help, starts it against the
# glasses: the checks below only read the file. The source stays beside the binary because its RUNPATH finds the vendor
# libraries inside src/. Do not use xr_driver_setup or bin/setup: the AUR package refuses to build alongside their copy.
set -eu

COMMIT=958908487a36aad15c6cbb5b7865e404c7b7dfac   # upstream main: 2.11.9 plus a README change
VERSION=2.11.9-958908-pr147-pr148-3dof
REPO=https://github.com/wheaney/XRLinuxDriver.git
HERE=$(cd "$(dirname "$0")" && pwd)
TARGET=${XDG_DATA_HOME:-$HOME/.local/share}/viture-workspace/xrdriver-3dof/$VERSION

if [ -e "$TARGET" ]; then echo "refusing: $TARGET exists" >&2; exit 2; fi
mkdir -p "$TARGET"
cd "$TARGET"
cp "$HERE/3dof.patch" 3dof.patch

git clone -q "$REPO" src
cd src
git checkout -q "$COMMIT"
git fetch -q origin pull/148/head:pr148 pull/147/head:pr147
git -c user.name=t -c user.email=t@t merge -q --no-edit pr148 pr147
git apply ../3dof.patch
git -c user.name=t -c user.email=t@t commit -q -am 'Request 3DoF on Carina (A/B test build)'
git submodule update --init --recursive modules/xrealInterfaceLibrary

mkdir -p build
cd build
cmake ..
make -j"$(nproc)"
cd ../..

cp src/build/xrDriver xrDriver
chmod 500 xrDriver

# File checks only (see the top of this file). ldd asks the loader for the list and stops before any code of ours runs.
readelf -d xrDriver | grep -E 'RPATH|RUNPATH' | grep -q "$TARGET/src/lib/" || { echo 'RUNPATH is not inside the build' >&2; exit 1; }
if ldd xrDriver | grep -q 'not found'; then echo 'a library is missing' >&2; exit 1; fi
nm -D --undefined-only xrDriver | grep -q xr_device_provider_set_dof_type_carina || { echo 'no 3DoF call in the binary' >&2; exit 1; }

python3 -I - "$VERSION" "$COMMIT" <<'PY'
import hashlib, json, subprocess, sys, time
def sha(path): return hashlib.sha256(open(path, 'rb').read()).hexdigest()
def rev(name): return subprocess.check_output(['git', '-C', 'src', 'rev-parse', name], text=True).strip()
json.dump({'version': sys.argv[1], 'upstream_commit': sys.argv[2], 'pr147': rev('pr147'), 'pr148': rev('pr148'),
           'source_commit': rev('HEAD'), 'patch_sha256': sha('3dof.patch'), 'binary_sha256': sha('xrDriver'),
           'libglasses_sha256': sha('src/lib/x86_64/viture/libglasses.so'),
           'libcarina_vio_sha256': sha('src/lib/x86_64/viture/libcarina_vio.so'),
           'built': time.strftime('%Y-%m-%dT%H:%M:%S%z')}, open('manifest.json', 'w'), indent=2)
PY
cat manifest.json; echo
echo "never run it by hand to check it: it would start against the glasses"
