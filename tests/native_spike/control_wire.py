"""What the supervisor sends the renderer, checked by the renderer's own rules.

The two meet on one socket, and a frame the renderer refuses used to end the session. So a frame in the wrong order is a
real bug however good it looks to a test that parses it. Two checks, and both must pass:
  * problems(): what Python can see by itself, which every run of the Python suite has: one JSON object a line, ids rising.
  * refused(): the real C++ decoder (spike/tests/control_wire_check.cpp), when CONTROL_WIRE_CHECK names it, as the ctest
    control_wire does. It also catches what only the renderer knows: a payload it does not accept.
"""
import json
import os
import subprocess

TOOL = os.environ.get('CONTROL_WIRE_CHECK')  # the real decoder: set by the ctests, unset in a plain run of the Python suite


def ids(data):
    return [json.loads(line)['request_id'] for line in bytes(data).splitlines()]


def problems(data, floor=0):
    """Every way `data` breaks the rules, in words, after a last id of `floor`. Empty: nothing wrong."""
    out = []
    for line in bytes(data).splitlines():
        try: frame = json.loads(line)
        except ValueError:
            out.append(f'not JSON: {line[:60]!r}')
            continue
        if not isinstance(frame, dict) or type(frame.get('request_id')) is not int:
            out.append(f'no request id: {line[:60]!r}')
            continue
        if frame['request_id'] <= floor: out.append(f"{frame.get('op')} went out with id {frame['request_id']} after id {floor}")
        floor = max(floor, frame['request_id'])
    return out


def in_order(data):
    """For the suites' frames() helpers: no test may parse frames that would not have reached the renderer."""
    if data:
        found = problems(data)
        if found: raise AssertionError(f'frames the renderer would refuse: {found}')


def refused(data, run_id, session):
    """What the renderer's own decoder refuses: [] if nothing, or without the tool."""
    if TOOL is None: return []
    done = subprocess.run([TOOL, run_id, session], input=bytes(data), capture_output=True, timeout=60, check=True)
    lines = [json.loads(line) for line in done.stdout.splitlines()]
    totals = lines.pop()
    if totals['accepted'] + totals['refused'] != bytes(data).count(b'\n'): raise AssertionError(f'frames lost: {totals}')
    return lines


def faults(data, run_id, session):
    """problems() and refused() together, for an assertEqual(..., [])."""
    return problems(data) + [f"the renderer refused {r['op']} id {r['id']}: {r['code']}: {r['detail']}" for r in refused(data, run_id, session)]
