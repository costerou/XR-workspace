"""Contract frames through the actual renderer decoder, never a Python substitute.

CONTROL_WIRE_CHECK must name a freshly built spike control_wire_check executable.
"""
import copy
import json
import os
import subprocess

import pytest


def snapshot():
    return dict(open=True, namespace='session', monitor='XR-3', scope='monitor',
                category='placement', focus=0, scroll=0, revision=1, status='idle', message='',
                rows=[dict(id='placement_x', label='Horizontal orbit', scope='monitor',
                           kind='range', value='-12', minimum=-180, maximum=180,
                           step=1, unit='deg', enabled=True, choices=[])])


def decode(payloads):
    tool = os.environ.get('CONTROL_WIRE_CHECK')
    assert tool, 'Build control_wire_check and set CONTROL_WIRE_CHECK; native validation is required'
    frames = [dict(schema=1, run_id='run', session='session', request_id=i,
                   op='options_menu', payload=p) for i, p in enumerate(payloads, 1)]
    raw = ''.join(json.dumps(f, ensure_ascii=False) + '\n' for f in frames).encode()
    done = subprocess.run([tool, 'run', 'session'], input=raw, capture_output=True,
                          timeout=15, check=True)
    records = [json.loads(line) for line in done.stdout.splitlines()]
    totals = records.pop()
    assert totals['accepted'] + totals['refused'] == len(frames)
    return totals, records


def test_valid_signed_units_and_stable_output_gap():
    totals, errors = decode([snapshot()])
    assert totals == dict(accepted=1, refused=0), errors


BAD = [('unknown', 1), ('open', 1), ('namespace', 'n'*65), ('monitor', 'XR-'+'1'*30),
       ('scope', 'global'), ('category', 'unknown'), ('focus', -2), ('focus', 64),
       ('focus', True), ('scroll', -1), ('scroll', 4097), ('revision', -1),
       ('revision', 2**32), ('status', 'forwarded'), ('message', 'é'*129),
       ('rows', [{}]*65)]


@pytest.mark.parametrize('key,value', BAD)
def test_malformed_snapshot_isolated_from_next_valid_frame(key, value):
    bad = snapshot(); bad[key] = value
    totals, errors = decode([bad, snapshot()])
    assert totals == dict(accepted=1, refused=1), errors


ROW_BAD = [('unknown', 1), ('id', 'a'*65), ('label', 'a'*97), ('scope', 'all'),
           ('kind', 'slider'), ('value', 'a'*257), ('unit', 'a'*17),
           ('enabled', 1), ('choices', ['x']*33), ('choices', ['x'*97]),
           ('minimum', float('nan')), ('maximum', float('inf')), ('step', -float('inf'))]


@pytest.mark.parametrize('key,value', ROW_BAD)
def test_malformed_row_isolated(key, value):
    bad = snapshot(); bad['rows'][0][key] = value
    totals, errors = decode([bad, snapshot()])
    assert totals == dict(accepted=1, refused=1), errors


def test_duplicate_row_ids_rejected_without_poisoning_stream():
    bad = snapshot(); bad['rows'].append(copy.deepcopy(bad['rows'][0]))
    totals, errors = decode([bad, snapshot()])
    assert totals == dict(accepted=1, refused=1), errors


def test_closed_and_uint32_boundary():
    closed = snapshot(); closed.update(open=False, rows=[], focus=-1, revision=2**32-1)
    totals, errors = decode([closed])
    assert totals == dict(accepted=1, refused=0), errors
