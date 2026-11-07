#!/usr/bin/env python3
"""Offline contract integrity only. Never imports runtime modules or contacts XR."""
import argparse
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
HERE = Path(__file__).resolve().parent


def validate(document, inventory, root=ROOT):
    errors = []
    rows = inventory['rows']
    ids = [r['identifier'] for r in rows]
    if len(ids) != 116 or len(set(ids)) != 116:
        errors.append('expected 116 unique inventory IDs')
    ledger = {}
    for line in document.splitlines():
        if line.startswith('| '):
            cells = [s.strip() for s in line.strip('|').split('|')]
            if cells[0] in ids:
                if cells[0] in ledger:
                    errors.append('duplicate ledger row: ' + cells[0])
                ledger[cells[0]] = cells
    for row in rows:
        name = row['identifier']
        cells = ledger.get(name, [])
        expected = [row['wire'].replace('|', ' / '), row['scope'], row['lifecycle']]
        if len(cells) != 5 or cells[1:4] != expected or not cells[4]:
            errors.append('missing or altered route/scope/lifecycle/destination: ' + name)
        source = root / row['source']['file']
        if not source.is_file():
            errors.append('missing source: ' + str(source))
    for n in range(1, 13):
        if f'- [ ] A{n:02d} ' not in document:
            errors.append(f'missing acceptance gate A{n:02d}')
    return errors


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--inventory', type=Path, default=HERE / 'inventory.json')
    args = parser.parse_args()
    inventory = json.loads(args.inventory.read_text())
    baseline = json.loads((HERE / 'inventory.json').read_text())
    errors = validate((ROOT / 'docs/xr-options-acceptance.md').read_text(), inventory)
    if inventory['rows'] != baseline['rows']:
        errors.append('inventory differs from reviewed snapshot; re-audit required')
    if errors:
        print('\n'.join('FAIL: ' + e for e in errors))
        return 1
    print('PASS: 116 unique routes, scopes, lifecycles, destinations and source paths; 12 gates present.')
    print('NOT VERIFIED: integrated runtime behavior, native captures, live XR.')
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
