"""Classifies one line of the driver's log: XRLinuxDriver's driver.log, and xr-luma-driver's stderr.

XRLinuxDriver's dated lines are "YYYY-MM-DD HH:MM:SS.mmm text"; libglasses and CARINA lines carry no wall time.

The C++ xr-luma-driver today (spike/driver/src/main.cpp log_line): "luma: <word> key=value ... at=<epoch s>.<ms>":
    luma: disconnected reason=gone at=1793700000.123     luma: reconnected gap_ms=4210 calibration=kept at=...
    luma: stale ms=250 at=...                            luma: retry attempt=1 reason=silent at=...

A future driver may instead write one event per line, either a JSON object or logfmt, with an `event` key:
    {"event": "cmd_sent", "cmd": "0x15"}             luma event=cmd_sent cmd=0x15
    event=cmd_failed cmd=0x15 rc=-7                  event=sample_gap ms=38.2
    event=reconnect                                  event=usb_error rc=-7
    event=state state=calibrated                     optional on any line: unix_ms=1793700000123
"""
from datetime import datetime
import json
import re

_DATED = re.compile(r'^(\d{4}-\d\d-\d\d \d\d:\d\d:\d\d\.\d{3}) (.*)$')
_FAILED_SEND = re.compile(r'Failed to send USB command - MsgID: (0x[0-9a-fA-F]+), error code: (-?\d+)')
_RETURNED = re.compile(r'\[E\]\[libglasses\] (\w+) \w+ returned (-?\d+)')
_POPPED = re.compile(r'\[I\]\[libglasses\] SendQueue: Popped Task ID')
_SYNC = re.compile(r'do sync data transfer :(-\d+)')
_OPEN_IMU = re.compile(r'open_imu failed at \d+Hz \((-?\d+)')
_CPP_LUMA = re.compile(r'^luma: (\w+)(.*)$')
_CPP_AT = re.compile(r'\sat=(\d+(?:\.\d+)?)\s*$')
_CPP_STATES = ('connected', 'calibrating', 'calibrated', 'waiting', 'fresh', 'stopped', 'reconnected')
_LOGFMT = re.compile(r'(\w+)=("[^"]*"|\S+)')
_STATES = ('Retrying driver connection', 'Starting up XR driver', 'Waiting for glasses', 'Device connected', 'Device calibration complete')
_LUMA_KINDS = {'cmd_sent': 'command_sent', 'cmd': 'command_sent', 'command': 'command_sent', 'cmd_failed': 'command_failed',
               'sample_gap': 'sample_gap', 'gap': 'sample_gap', 'reconnect': 'reconnect', 'usb_error': 'usb_error', 'state': 'state'}


def own_time(text):
    """(epoch seconds or None, the line without its date)."""
    found = _DATED.match(text)
    if found:
        return datetime.strptime(found[1], '%Y-%m-%d %H:%M:%S.%f').timestamp(), found[2]
    return None, text


def _structured(text):
    fields = None
    if text.startswith('{'):
        try:
            fields = json.loads(text)
        except ValueError:
            return None
    elif 'event=' in text:
        fields = {key: value.strip('"') for key, value in _LOGFMT.findall(text)}
    return fields if isinstance(fields, dict) and 'event' in fields else None


def _number(value):
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def line_time(text):
    """A structured line's own unix_ms, a C++ luma line's at=, or the dated prefix's time, else None."""
    fields = _structured(text)
    if fields and (ms := _number(fields.get('unix_ms'))) is not None:
        return ms / 1000
    if _CPP_LUMA.match(text) and (found := _CPP_AT.search(text)):
        return float(found[1])
    return own_time(text)[0]


def _cpp_luma(text):
    """A C++ xr-luma-driver line as a dict with `kind`, or None for a line that is not one."""
    found = _CPP_LUMA.match(text)
    if not found:
        return None
    word, fields = found[1], {key: value.strip('"') for key, value in _LOGFMT.findall(found[2])}
    if word in ('disconnected', 'error'):
        return {'kind': 'usb_error', 'cmd': word, 'reason': fields.get('reason', '?')}
    if word == 'stale':
        return {'kind': 'sample_gap', **({'ms': ms} if (ms := _number(fields.get('ms'))) is not None else {})}
    if word == 'retry':
        return {'kind': 'reconnect', 'reason': fields.get('reason', '?')}
    if word in _CPP_STATES:
        out = {'kind': 'state', 'state': word}
        if (ms := _number(fields.get('gap_ms'))) is not None:
            out['ms'] = ms
        if 'calibration' in fields:
            out['calibration'] = fields['calibration']
        return out
    return {'kind': 'other', 'event': f'luma {word}'}


def classify(text):
    """A dict with `kind` for a line that says something about USB traffic, else None."""
    if fields := _structured(text):
        kind = _LUMA_KINDS.get(str(fields['event']))
        if kind is None:
            return {'kind': 'other', 'event': str(fields['event'])}
        out = {'kind': kind}
        if 'cmd' in fields:
            out['cmd'] = str(fields['cmd'])
        if (rc := _number(fields.get('rc'))) is not None:
            out['rc'] = int(rc)
        if (ms := _number(fields.get('ms'))) is not None:
            out['ms'] = ms
        if 'state' in fields:
            out['state'] = str(fields['state'])
        return out
    if (cpp := _cpp_luma(text)) is not None:
        return cpp
    _, body = own_time(text)
    if found := _FAILED_SEND.search(body):
        return {'kind': 'command_failed', 'cmd': found[1].lower(), 'rc': int(found[2])}
    if found := _RETURNED.search(body):
        return {'kind': 'usb_error', 'cmd': found[1], 'rc': int(found[2])}
    if found := _SYNC.search(body):
        return {'kind': 'usb_error', 'cmd': 'sync_transfer', 'rc': int(found[1])}
    if found := _OPEN_IMU.search(body):
        return {'kind': 'usb_error', 'cmd': 'open_imu', 'rc': int(found[1])}
    if _POPPED.search(body):
        return {'kind': 'command_sent', 'cmd': '?'}
    if body.startswith('Retrying driver connection'):
        return {'kind': 'reconnect'}
    for state in _STATES:
        if body.startswith(state):
            return {'kind': 'state', 'state': state}
    return None
