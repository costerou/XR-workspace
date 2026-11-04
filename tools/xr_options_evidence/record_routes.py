#!/usr/bin/env python3
"""Attach executed per-route JUnit evidence. Never promote partial proof to verified."""
import argparse
import hashlib
import json
from pathlib import Path
import xml.etree.ElementTree as ET

ROOT=Path(__file__).resolve().parents[2]


def record(ledger, junit, cases):
    root=ET.fromstring(junit)
    outcomes={}
    for case in root.iter('testcase'):
        name=case.get('name','')
        prefix='test_functional_preserved_route['
        if name.startswith(prefix) and name.endswith(']'):
            id=name[len(prefix):-1]
            if id in outcomes: raise ValueError('duplicate route result: '+id)
            outcomes[id]='failed' if case.find('failure') is not None or case.find('error') is not None else 'skipped' if case.find('skipped') is not None else 'passed'
    for row in ledger['rows']:
        id=row['id']
        row['functional_owner_evidence']=dict(status=outcomes.get(id,'not_run'),
            junit_sha256=hashlib.sha256(junit).hexdigest(),
            scenario='tests/xr_options_integration/test_functional_routes.py::test_functional_preserved_route['+id+']',
            owner_tests=cases[id]['tests'],exception=cases[id]['exception'])
        # The baseline owner scenario cannot satisfy migrated entry/readback/input.
        row['implementation_status']='pending_integration'
        row['limitation']='Executed owner/preserved scenario is partial proof; C1 ingress, native receipts, owner failure/readback and cleanup release gates must independently pass'
    return ledger


def main():
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--junit',type=Path,required=True)
    p.add_argument('--output',type=Path,required=True)
    p.add_argument('--ledger',type=Path,default=ROOT/'docs/xr-options-build-coverage.json')
    a=p.parse_args()
    cases=json.loads((ROOT/'tests/xr_options_integration/route_cases.json').read_text())
    ledger=record(json.loads(a.ledger.read_text()),a.junit.read_bytes(),cases)
    a.output.write_text(json.dumps(ledger,indent=2)+'\n')


if __name__=='__main__': main()
