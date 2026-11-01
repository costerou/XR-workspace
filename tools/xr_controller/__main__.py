import argparse
import json
import os
from pathlib import Path
import sys

from .install import install


def main(argv=None):
    ap = argparse.ArgumentParser(prog='xr_controller')
    sub = ap.add_subparsers(dest='cmd', required=True)
    inst = sub.add_parser('install', help='pin build outputs into the user data directory')
    inst.add_argument('--build', required=True, type=Path)
    args = ap.parse_args(argv)
    data_home = Path(os.environ.get('XDG_DATA_HOME') or Path.home() / '.local/share')
    try:
        print(json.dumps(install(args.build, data_home), indent=2))
    except ValueError as e:
        print(f'install failed: {e}', file=sys.stderr); return 1
    return 0


if __name__ == '__main__':
    sys.exit(main())
