"""Read-only check of the glasses' USB side. Exit 0 ready, 1 something to do, 2 glasses not found."""
import argparse
from dataclasses import asdict
import json
import sys

from .assess import assess, render
from .sysfs import Roots, scan


def main(argv=None):
    parser = argparse.ArgumentParser(prog='python -m tools.luma_probe', description=__doc__)
    parser.add_argument('--json', action='store_true', help='print everything as JSON')
    parser.add_argument('--root', help='read a copied tree instead of the live system (tests)')
    args = parser.parse_args(argv)
    roots = Roots.under(args.root) if args.root else Roots.live()
    found = scan(roots)
    verdict = assess(found, roots)
    if args.json:
        print(json.dumps({'status': verdict.status, 'findings': [asdict(f) for f in verdict.findings],
                          'udev_commands': verdict.udev_commands, 'devices': [asdict(d) for d in found.devices],
                          'drivers': [asdict(d) for d in found.drivers]}, indent=2))
    else:
        print(render(found, verdict))
    return verdict.exit_code


if __name__ == '__main__':
    sys.exit(main())
