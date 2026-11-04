"""Turns captures into numbers: what starts the stream, how good the stream is, what precedes a disconnect.

Two passes over the files, so a long capture needs memory only for the windows around drops. The first
finds the drops, the stream endpoints and samples; the second counts.

Whether a soak passed is tools/luma_soak's question, from the kernel log. This answers what only the wire can.
"""
from bisect import bisect_right
from collections import Counter, defaultdict
import statistics
import time

from .capfile import XFER_CTRL, expand
from .protocol import (CANCELLED, GET_DISPLAY_MODE, STREAM_MIN_HZ, STREAM_OVER_COMMANDS, Labeler, confirms_drop,
                       decode_frame, describe_control, drop_signal, infer_framing, parse_setup, status_name)
from .streams import PROFILE_PACKETS, arrivals, pin_accelerometer, profile

CLUSTER_NS = 2_000_000_000       # signals of one disconnect arrive together; the hub's pairs are 2 s or more apart
AFTER_NS = 1_000_000_000         # rows kept after the disconnect
SAMPLES = 5000
HEX_BYTES = 24
START_ROWS = 120
AFTER_STREAM_NS = 1_000_000_000  # how long a connection's start-up is followed once the stream is flowing
STEADY_NS = 10_000_000_000       # a command this long after a connection's first is a steady-state command
MARK_NS = 3_000_000_000
MARK_ROWS = 10


def _ns(seconds):
    return int(seconds * 1e9)


def clock(t_ns):
    return time.strftime('%H:%M:%S', time.localtime(t_ns // 10**9)) + f'.{t_ns // 10**6 % 1000:03d}'


def _is_command(record):
    """A host-to-glasses transfer as sent. Standard and hub control traffic is not the driver's doing."""
    if not record.glasses or record.event != 'S':
        return False
    return record.setup[0] >> 5 & 3 == 2 if record.xfer == XFER_CTRL else not record.is_in


def _records(readers):
    for reader in readers:
        yield from reader


def _endpoint_name(record):
    return f'ep{record.ep:02x}' if record.glasses else f'companion ep{record.ep:02x}'


def _first_pass(readers):
    scan = {'first': None, 'last': None, 'usb': 0, 'notes': 0, 'lost': 0, 'seconds': set(), 'signals': [],
            'endpoints': defaultdict(lambda: {'transfers': 0, 'bytes': 0, 'first': None, 'last': None}),
            'driver_errors': 0, 'kernel_log': False, 'driver_log': False, 'marks': [],
            'frames': defaultdict(Counter)}
    outgoing, incoming = [], defaultdict(list)
    commands = 0
    port = None
    for record in _records(readers):
        if record.is_note:
            scan['notes'] += 1
            note = record.note
            kind = note.get('type')
            if kind == 'device_map' and note.get('port'):
                port = note['port']
            elif kind == 'usbmon_stats':
                scan['lost'] = max(scan['lost'], note.get('dropped', 0))
            elif kind == 'driver_log':
                scan['driver_log'] = True
                scan['driver_errors'] += 'MsgID' in note.get('text', '')
            elif kind == 'kernel':
                scan['kernel_log'] = True
            elif kind == 'mark':
                scan['marks'].append({'t_ns': record.t_ns, 'clock': clock(record.t_ns), 'text': note.get('text', ''),
                                      'rows': []})
        else:
            scan['usb'] += 1
            scan['first'] = record.t_ns if scan['first'] is None else min(scan['first'], record.t_ns)
            scan['last'] = record.t_ns if scan['last'] is None else max(scan['last'], record.t_ns)
            scan['seconds'].add(record.t_ns // 10**9)
            ours = record.glasses or record.companion
            if ours and record.event == ('C' if record.is_in else 'S') and record.xfer != XFER_CTRL:
                stats = scan['endpoints'][_endpoint_name(record)]
                stats['transfers'] += 1
                stats['bytes'] += record.urb_len
                stats['first'] = stats['first'] if stats['first'] is not None else record.t_ns
                stats['last'] = record.t_ns
                stats['ep'] = record.ep
            if record.glasses and record.data:
                if _is_command(record):
                    commands += 1
                    if len(outgoing) < SAMPLES:
                        outgoing.append((record.t_ns, False, record.data))
                elif record.is_in and record.event == 'C' and record.status == 0 and len(incoming[record.ep]) < SAMPLES:
                    incoming[record.ep].append((record.t_ns, True, record.data))
                if record.xfer != XFER_CTRL and (not record.is_in or record.event == 'C'):
                    frame = decode_frame(record.data)
                    if frame and frame.crc_ok is not None:
                        scan['frames'][record.ep][frame.crc_ok] += 1
        signal = drop_signal(record, port)
        if signal:
            scan['signals'].append((record.t_ns, signal))
    streams = set()
    for stats in scan['endpoints'].values():
        span = (stats['last'] - stats['first']) / 1e9
        stats['rate'] = stats['transfers'] / span if span > 0 else 0.0
        # Fast is not enough: replies to a command flood are fast too, and hiding those would hide the flood.
        if (stats['ep'] & 0x80 and stats['transfers'] >= STREAM_MIN_HZ and stats['rate'] >= STREAM_MIN_HZ
                and stats['transfers'] > STREAM_OVER_COMMANDS * commands):
            streams.add(stats['ep'])
    # Judged only now: sensor bytes in a stream can start with FF FE by chance and must not count as bad frames.
    scan['frames_ok'] = sum(row[True] for ep, row in scan['frames'].items() if ep not in streams)
    scan['frames_bad'] = sum(row[False] for ep, row in scan['frames'].items() if ep not in streams)
    samples = sorted(outgoing + [s for ep, batch in incoming.items() if ep not in streams for s in batch],
                     key=lambda sample: sample[0])
    return scan, streams, samples, {ep: incoming[ep] for ep in streams}


def _cluster(signals):
    drops = []
    for t_ns, reason in sorted(signals):
        if drops and t_ns - drops[-1]['last'] <= CLUSTER_NS:
            drops[-1]['last'] = t_ns
            if reason not in drops[-1]['signals']:
                drops[-1]['signals'].append(reason)
        else:
            drops.append({'t_ns': t_ns, 'last': t_ns, 'signals': [reason]})
    return drops


def _new_window(drop, window_ns, previous):
    return {'t_ns': drop['t_ns'], 'clock': clock(drop['t_ns']), 'signals': drop['signals'][:6],
            'confirmed': any(map(confirms_drop, drop['signals'])),
            'since_previous_s': None if previous is None else round((drop['t_ns'] - previous) / 1e9, 3),
            'start': drop['t_ns'] - window_ns, 'end': drop['t_ns'] + AFTER_NS, 'rows': [],
            'seconds': defaultdict(lambda: {'commands': 0, 'target': 0, 'stream': 0, 'failed': 0}),
            'stream_last': {}, 'stream_gap': {}, 'firsts': {}, 'in_flight': None, 'reads_posted': 0,
            'dev': None, 'first_command_after_s': None, 'first_stream_after_s': None}


def _row(record, text, anchor):
    status = '' if record.is_note or record.event == 'S' else status_name(record.status)
    return {'t_ns': record.t_ns, 'offset_s': round((record.t_ns - anchor) / 1e9, 6), 'event': record.event.strip('\0'),
            'text': text, 'status': status, 'hex': record.data[:HEX_BYTES].hex(' ') if not record.is_note else ''}


def _window_add(window, record, label, pending):
    anchor = window['t_ns']
    second = (record.t_ns - anchor) // 10**9
    seconds = window['seconds'][second]
    firsts = window['firsts']
    if record.is_note:
        note = record.note
        if note.get('type') in ('kernel', 'driver_log', 'mark'):
            window['rows'].append(_row(record, f"{note['type']}: {note.get('text', '')}", anchor))
            if note['type'] == 'driver_log' and 'MsgID' in note.get('text', ''):
                firsts.setdefault('first SDK error line', record.t_ns)
        return
    if _is_command(record):
        seconds['commands'] += 1
        if label.msgid == GET_DISPLAY_MODE:
            seconds['target'] += 1
        if record.t_ns <= anchor:
            firsts['last command before the drop'] = record.t_ns
        window['rows'].append(_row(record, label.text, anchor))
    elif label.kind == 'stream' and record.status == 0:
        seconds['stream'] += 1
        last = window['stream_last'].get(record.ep)
        if last is not None:
            window['stream_gap'][record.ep] = max(window['stream_gap'].get(record.ep, 0), record.t_ns - last)
        if record.t_ns <= anchor:
            window['stream_last'][record.ep] = record.t_ns
    elif label.kind == 'reply' and record.status == 0:
        window['rows'].append(_row(record, label.text, anchor))
    elif label.kind == 'hub' and (record.event == 'C' or 'GET_PORT_STATUS' not in label.text):
        window['rows'].append(_row(record, label.text, anchor))
        if 'connected' not in label.text and 'GET_PORT_STATUS' in label.text and record.event == 'C':
            firsts.setdefault('hub reports the port disconnected', record.t_ns)
    elif label.kind == 'control' and record.event == 'S':
        window['rows'].append(_row(record, label.text, anchor))
    if record.glasses and record.event == 'C' and record.status:
        seconds['failed'] += 1
        origin = pending.get(record.urb_id)
        row = _row(record, f'FAILED {label.text if origin is None else origin["text"]} '
                           f'({status_name(record.status)})', anchor)
        row['status'] = ''
        window['rows'].append(row)
        kind = 'first cancelled transfer (a timeout)' if record.status in CANCELLED else 'first failed transfer'
        firsts.setdefault(kind, record.t_ns)


def _new_connection(record):
    return {'dev': record.dev, 'clock': clock(record.t_ns), 't_ns': record.t_ns, 'commands': 0, 'rows': [],
            'rows_dropped': 0, 'first_command_ns': None, 'first_stream_ns': None}


def _connection_add(connection, record, label):
    """Follows one connection's start-up until the stream has been up a while: this is how the stream gets started."""
    if label.kind == 'command' and connection['first_command_ns'] is None:
        connection['first_command_ns'] = record.t_ns
    if label.kind == 'stream' and record.status == 0 and connection['first_stream_ns'] is None:
        connection['first_stream_ns'] = record.t_ns
    began = connection['first_stream_ns']
    if began is not None and record.t_ns > began + AFTER_STREAM_NS:
        return
    wanted = (label.kind == 'command' or (label.kind == 'reply' and record.event == 'C')
              or (label.kind == 'control' and record.event == 'S')
              or (record.event == 'C' and record.status and label.kind != 'stream'))
    if not wanted:
        return
    if label.kind == 'command':
        connection['commands'] += 1
    if len(connection['rows']) >= START_ROWS:
        connection['rows_dropped'] += 1
        return
    text = label.text if not record.status or record.event == 'S' else f'FAILED {label.text}'
    connection['rows'].append(_row(record, text, connection['first_command_ns'] or connection['t_ns']))


class _Facts:
    """What the wire rules in audit.py are judged on."""

    def __init__(self):
        self.forbidden = Counter()
        self.enumerated = set()
        self.configured = set()
        self.steady = Counter()
        self.steady_seconds = set()
        self.most_writes = 0
        self.queued = defaultdict(lambda: {'completions': 0, 'thin': 0})
        self.idle_reads = defaultdict(lambda: {'posted': 0, 'data': 0})
        self.last_change = None

    def control(self, record, label):
        if record.event != 'S' or not record.flags & 1:
            return
        request_type, request, value, *_ = parse_setup(record.setup)
        text = describe_control(record.setup)
        if 'SET RESET' in text:
            # The kernel resets every device it enumerates, so only one with no connection change nearby counts.
            if self.last_change is None or record.t_ns - self.last_change > 2 * STEADY_NS:
                self.forbidden['port reset'] += 1
            return
        if not record.glasses:
            return
        if request_type >> 5 & 3 != 0:
            return
        if request == 6:
            self.enumerated.add(record.dev)
        elif request == 9:
            first = record.dev in self.enumerated and record.dev not in self.configured
            self.configured.add(record.dev)
            if not first:
                self.forbidden['SET_CONFIGURATION'] += 1     # the one straight after enumeration is the kernel's
        elif request == 11:
            self.forbidden['SET_INTERFACE'] += 1
        elif request == 1 and value == 0 and request_type & 0x1f == 2:
            self.forbidden['clear halt'] += 1


def _pinned(marks, stills):
    """For each stream, what every 'still' mark agrees is the accelerometer. Two marks may leave several."""
    if len(stills) < 2:
        return {}
    pinned = {}
    for ep in {ep for still in stills for ep in still}:
        poses = [still[ep] for still in stills if len(still.get(ep, ())) >= 20]
        size = max(map(len, poses[0]), default=0) if poses else 0
        same = [[data for data in pose if len(data) == size] for pose in poses]
        if len(same) >= 2 and all(same):
            pinned[f'ep{ep:02x}'] = pin_accelerometer(same)
    return pinned


def _second_pass(readers, framing, streams, drops, marks, window_s, rows):
    labeler = Labeler(framing, streams)
    stills = [(mark['t_ns'], defaultdict(list)) for mark in marks if 'still' in mark['text'].lower()]
    per_key = defaultdict(Counter)
    sizes = {}
    incoming = defaultdict(Counter)
    latency = defaultdict(list)
    failures = Counter()
    deltas = defaultdict(Counter)
    last_arrival = {}
    connections = {}
    facts = _Facts()
    previous = None
    windows = []
    for drop in drops:
        windows.append(_new_window(drop, _ns(window_s), previous))
        previous = drop['t_ns']
    starts = [window['start'] for window in windows]
    pending = {}
    reads_on = Counter()
    next_snapshot = 0
    last_dev = None
    waiting = []
    for record in _records(readers):
        while next_snapshot < len(windows) and record.t_ns >= windows[next_snapshot]['t_ns']:
            window = windows[next_snapshot]
            window['in_flight'] = [{'age_s': round((window['t_ns'] - p['t_ns']) / 1e9, 6), 'text': p['text']}
                                   for p in pending.values() if not p['is_in']]
            window['reads_posted'] = sum(p['is_in'] for p in pending.values())
            window['dev'] = last_dev
            facts.last_change = window['t_ns']
            waiting.append(window)
            next_snapshot += 1
        label = None
        if not record.is_note:
            label = labeler.label(record)
            if record.xfer == XFER_CTRL:
                facts.control(record, label)
                if 'changed: connection' in label.text:
                    facts.last_change = record.t_ns
            if record.glasses:
                last_dev = record.dev
                connection = connections.get(record.dev)
                if connection is None:
                    connection = connections[record.dev] = _new_connection(record)
                _connection_add(connection, record, label)
                for window in waiting:
                    if record.dev != window['dev']:
                        moment = round((record.t_ns - window['t_ns']) / 1e9, 6)
                        if _is_command(record) and window['first_command_after_s'] is None:
                            window['first_command_after_s'] = moment
                        if label.kind == 'stream' and not record.status and window['first_stream_after_s'] is None:
                            window['first_stream_after_s'] = moment
                waiting = [w for w in waiting if w['first_stream_after_s'] is None]
            if _is_command(record):
                key, _ = labeler.command_key(record)
                per_key[key][record.t_ns // 10**9] += 1
                sizes[key] = record.urb_len
                first = connection['first_command_ns']
                if first is not None and record.t_ns - first > STEADY_NS:
                    facts.steady[key] += 1
                    facts.steady_seconds.add(record.t_ns // 10**9)
            if label.kind == 'reply' and record.status == 0 and record.data:
                incoming[labeler.incoming_key(record)][record.t_ns // 10**9] += 1
            if record.glasses and record.xfer != XFER_CTRL:
                if record.event == 'S':
                    pending[record.urb_id] = {'t_ns': record.t_ns, 'is_in': record.is_in, 'text': label.text,
                                              'ep': record.ep}
                    if record.is_in:
                        reads_on[record.ep] += 1
                        facts.idle_reads[record.ep]['posted'] += 1
                    else:
                        facts.most_writes = max(facts.most_writes, sum(not p['is_in'] for p in pending.values()))
                elif record.event == 'C':
                    if record.status:
                        failures[(record.ep, record.status)] += 1
                    origin = pending.get(record.urb_id)
                    if origin and not record.is_in:
                        latency[record.ep].append((record.t_ns - origin['t_ns']) / 1e6)
                    if record.is_in and record.status == 0:
                        facts.idle_reads[record.ep]['data'] += bool(record.urb_len)
                    if origin and record.is_in and record.ep in streams and record.status == 0:
                        queued = facts.queued[record.ep]
                        queued['completions'] += 1
                        queued['thin'] += reads_on[record.ep] < 2
            if label.kind == 'stream' and record.status == 0:
                key = (record.dev, record.ep)
                if key in last_arrival:
                    deltas[record.ep][(record.t_ns - last_arrival[key]) // 1000] += 1
                last_arrival[key] = record.t_ns
                for began, packets in stills:
                    if 0 <= record.t_ns - began <= MARK_NS and len(packets[record.ep]) < PROFILE_PACKETS:
                        packets[record.ep].append(record.data)
        for candidate in reversed(windows[:bisect_right(starts, record.t_ns)]):
            if record.t_ns > candidate['end']:
                break     # ends only get earlier from here, so no older window can hold it
            _window_add(candidate, record, label, pending)
        if label is not None and label.kind in ('command', 'reply', 'companion') and record.data:
            for mark in marks:
                if 0 <= record.t_ns - mark['t_ns'] <= MARK_NS and len(mark['rows']) < MARK_ROWS:
                    mark['rows'].append(_row(record, label.text, mark['t_ns']))
        if label is not None and record.glasses and record.xfer != XFER_CTRL and record.event == 'C':
            origin = pending.pop(record.urb_id, None)
            if origin and origin['is_in']:
                reads_on[record.ep] -= 1
    for window in windows:
        window['rows'].sort(key=lambda row: row['t_ns'])
        window['rows_dropped'] = max(len(window['rows']) - rows, 0)
        window['rows'] = window['rows'][-rows:]
        window['seconds'] = {second: window['seconds'][second] for second in sorted(window['seconds'])}
        window['stream_last'] = {f'ep{ep:02x}': round((t - window['t_ns']) / 1e9, 6)
                                 for ep, t in window['stream_last'].items()}
        window['stream_gap'] = {f'ep{ep:02x}': round(gap / 1e6, 3) for ep, gap in window['stream_gap'].items()}
        order = sorted(window['firsts'].items(), key=lambda item: item[1])
        window['order'] = [{'what': what, 'offset_s': round((t - window['t_ns']) / 1e9, 6)} for what, t in order]
        del window['firsts']
    return {'per_key': per_key, 'sizes': sizes, 'latency': latency, 'failures': failures, 'windows': windows,
            'deltas': deltas, 'incoming': incoming, 'connections': connections, 'facts': facts,
            'pinned': _pinned(marks, [packets for _, packets in stills])}


def _rate(per_second, seconds):
    total = sum(per_second.values())
    return {'total': total, 'per_s': round(total / max(seconds, 1), 3), 'peak_per_s': max(per_second.values())}


def _connection_summary(connection):
    began, stream = connection['first_command_ns'], connection['first_stream_ns']
    return {'dev': connection['dev'], 'clock': connection['clock'], 'commands': connection['commands'],
            'stream_after_s': None if began is None or stream is None else round((stream - began) / 1e9, 6),
            'rows': connection['rows'], 'rows_dropped': connection['rows_dropped']}


def analyze(paths, *, framing=None, window_s=5.0, rows=200):
    """Everything the report prints, as plain data. Pass a Framing to overrule what the capture shows."""
    files = expand(paths)
    scan, streams, samples, stream_samples = _first_pass(files)
    documented = framing is None and scan['frames_ok'] > 0
    inference = infer_framing(samples)
    chosen = framing or (None if documented else inference.framing)
    source = 'given' if framing else 'documented' if documented else inference.verdict
    drops = _cluster(scan['signals'])
    done = _second_pass(files, chosen, streams, drops, scan['marks'], window_s, rows)
    seconds = len(scan['seconds'])
    commands = {key: {**_rate(counts, seconds), 'size': done['sizes'][key]} for key, counts in done['per_key'].items()}
    target = commands.get(f'MsgID {GET_DISPLAY_MODE:#06x}')
    facts = done['facts']
    return {
        'files': len(files),
        'damaged_files': [str(reader.path) for reader in files if reader.truncated],
        'usb_events': scan['usb'],
        'notes': scan['notes'],
        'seconds_with_traffic': seconds,
        'first': clock(scan['first']) if scan['first'] else None,
        'last': clock(scan['last']) if scan['last'] else None,
        'events_lost_by_kernel': scan['lost'],
        'kernel_log_seen': scan['kernel_log'],
        'driver_log_seen': scan['driver_log'],
        'sdk_error_lines': scan['driver_errors'],
        'framing': {'spec': chosen.spec() if chosen else None, 'source': source,
                    'frames_with_good_crc': scan['frames_ok'], 'frames_with_bad_crc': scan['frames_bad'],
                    'candidates': [] if documented else [
                        {'spec': c.framing.spec(), 'packets_0x0007': c.packets, 'distinct_values': c.distinct,
                         'replies_echo': round(c.echo, 3)} for c in inference.candidates]},
        'msgid_0x0007': None if chosen is None and not documented else
        (target or {'total': 0, 'per_s': 0.0, 'peak_per_s': 0}),
        'commands': dict(sorted(commands.items(), key=lambda item: -item[1]['total'])),
        'incoming': dict(sorted(((key, _rate(counts, seconds)) for key, counts in done['incoming'].items()),
                                key=lambda item: -item[1]['total'])),
        'endpoints': {name: {'transfers': stats['transfers'], 'bytes': stats['bytes'], 'per_s': round(stats['rate'], 1),
                             'stream': name.startswith('ep') and stats['ep'] in streams}
                      for name, stats in sorted(scan['endpoints'].items())},
        'streams': {f'ep{ep:02x}': {**arrivals(done['deltas'][ep]),
                                    'profile': profile([s[2] for s in stream_samples[ep]],
                                                       [s[0] for s in stream_samples[ep]]),
                                    'accelerometer': done['pinned'].get(f'ep{ep:02x}')}
                    for ep in sorted(streams)},
        'send_latency_ms': {f'ep{ep:02x}': {'p50': round(statistics.median(values), 3),
                                            'max': round(max(values), 3), 'count': len(values)}
                            for ep, values in sorted(done['latency'].items())},
        'failures': [{'ep': f'ep{ep:02x}', 'status': status_name(status), 'count': count}
                     for (ep, status), count in done['failures'].most_common()],
        'connections': [_connection_summary(c) for c in done['connections'].values()],
        'marks': scan['marks'],
        'drops': done['windows'],
        'wire_facts': {'forbidden': dict(facts.forbidden), 'steady_commands': dict(facts.steady),
                       'steady_per_s': round(sum(facts.steady.values()) / max(len(facts.steady_seconds), 1), 3),
                       'most_writes_at_once': facts.most_writes,
                       'queued_reads': {f'ep{ep:02x}': dict(row) for ep, row in sorted(facts.queued.items())},
                       'reads_never_answered': sorted(f'ep{ep:02x}' for ep, row in facts.idle_reads.items()
                                                      if row['posted'] and not row['data'])},
    }


def render(result):
    lines = [f"{result['usb_events']} USB events in {result['files']} file(s), "
             f"{result['first']} to {result['last']}, {result['seconds_with_traffic']} s with traffic"]
    if result['events_lost_by_kernel']:
        lines.append(f"WARNING: usbmon lost {result['events_lost_by_kernel']} events; this capture has holes")
    if result['damaged_files']:
        lines.append('WARNING: cut off mid-record: ' + ', '.join(result['damaged_files']))
    if not result['kernel_log_seen']:
        lines.append('Note: no kernel log lines in this capture')
    if not result['driver_log_seen']:
        lines.append('Note: no driver log lines in this capture')

    framing = result['framing']
    lines += ['', 'MsgID 0x0007 (get_display_mode)']
    if result['msgid_0x0007'] is None:
        lines.append('  Cannot be counted: this capture does not settle where the MsgID sits. Candidates below,')
        lines.append('  and "Commands" still counts every transfer by shape. Rerun with --framing once known.')
    else:
        target = result['msgid_0x0007']
        lines.append(f"  {target['total']} sent, {target['per_s']}/s on average, {target['peak_per_s']}/s at peak")
        if framing['source'] == 'documented':
            lines.append(f"  from the documented frame, proven here: {framing['frames_with_good_crc']} good CRCs")
        else:
            lines.append(f"  field: {framing['spec']} ({framing['source']}"
                         f"{', not confirmed by a spec' if framing['source'] == 'inferred' else ''})")
    if framing['frames_with_bad_crc']:
        lines.append(f"  WARNING: {framing['frames_with_bad_crc']} frames failed their CRC")
    for candidate in framing['candidates']:
        lines.append(f"  candidate {candidate['spec']}: 0x0007 in {candidate['packets_0x0007']} sampled, "
                     f"{candidate['distinct_values']} values, replies echo {candidate['replies_echo']:.0%}")

    lines += ['', 'Commands (host to glasses)', f"  {'what':<32}{'total':>8}{'per s':>9}{'peak/s':>8}"]
    for key, row in result['commands'].items():
        lines.append(f"  {key:<32}{row['total']:>8}{row['per_s']:>9}{row['peak_per_s']:>8}")
    if not result['commands']:
        lines.append('  none')
    if result['incoming']:
        lines += ['', 'Replies and events (glasses to host, streams left out)']
        for key, row in result['incoming'].items():
            lines.append(f"  {key:<32}{row['total']:>8}{row['per_s']:>9}{row['peak_per_s']:>8}")

    lines += ['', 'Endpoints']
    for name, row in result['endpoints'].items():
        kind = ' stream' if row['stream'] else ''
        lines.append(f"  {name}: {row['transfers']} transfers, {row['bytes']} bytes, {row['per_s']}/s{kind}")
    for name, row in result['send_latency_ms'].items():
        lines.append(f"  {name} send took: median {row['p50']} ms, worst {row['max']} ms")
    for row in result['failures']:
        lines.append(f"  {row['ep']} failed {row['count']} times with {row['status']}")

    for name, stream in result['streams'].items():
        lines += _render_stream(name, stream)
    if result['connections']:
        lines += ['', 'Start-up, per connection (times from its first command)']
    for connection in result['connections']:
        lines += _render_connection(connection)
    for mark in result['marks']:
        lines += ['', f"Mark at {mark['clock']}: {mark['text']}"]
        followed = [f"  {row['offset_s']:+7.3f}  {row['text']}  {row['hex']}" for row in mark['rows']]
        lines += followed or ['  nothing followed']

    confirmed = sum(drop['confirmed'] for drop in result['drops'])
    unconfirmed = len(result['drops']) - confirmed
    lines += ['', f"Disconnects: {confirmed}" + (f' (and {unconfirmed} unconfirmed)' if unconfirmed else '')]
    for number, drop in enumerate(result['drops'], 1):
        lines += _render_drop(number, drop)
    return '\n'.join(lines) + '\n'


def _render_stream(name, stream):
    lines = ['', f'Stream {name}',
             f"  {stream['hz']} transfers/s; spacing median {stream['p50_us']} us, 99% {stream['p99_us']} us, "
             f"99.9% {stream['p999_us']} us, worst {stream['max_us']} us",
             f"  {stream['gaps']} late arrivals (over 5 times the median), {stream['outages']} outages over 1 s"]
    found = stream['profile']
    if found.get('packets', 0) == 0:
        return lines
    lines.append(f"  transfer size {found['size']} bytes; sizes seen: {found['sizes']}")
    if 'clocks' not in found:
        return lines + ['  too few transfers to look inside']
    lines.append(f"  starts with {found['magic'] or 'nothing fixed'}; "
                 f"{found['samples_per_transfer']} sample(s) per transfer")
    if found['sample_spacing_bytes']:
        lines.append(f"  samples {found['sample_spacing_bytes']} bytes apart")
    for item in found['clocks']:
        unit = f", counts {item['unit']}, {item['drift_ppm']} ppm from the host clock" if 'unit' in item else ''
        lines.append(f"  possible clock: offset {item['offset']}, {item['width']} bytes {item['endian']}, "
                     f"step {item['step']:g}, {item.get('ticks_per_s', 0):g} ticks/s{unit}")
    for item in found['gravity']:
        lines.append(f"  possible accelerometer: offset {item['offset']}, {item['format']}, length {item['length']} "
                     f"{item['unit']}, spread {item['spread']} (needs the head still)")
    if not found['clocks'] and not found['gravity']:
        lines.append('  no clock or gravity vector found')
    if stream['accelerometer'] is not None:
        for item in stream['accelerometer']:
            lines.append(f"  ACCELEROMETER, by still poses: offset {item['offset']}, {item['format']}, {item['unit']}, "
                         f"lengths {item['lengths']}")
        if not stream['accelerometer']:
            lines.append('  no field held one gravity in every still pose')
    return lines


def _render_connection(connection):
    began = connection['stream_after_s']
    state = 'the stream never started' if began is None else f'stream began {began} s after the first command'
    lines = [f"  device {connection['dev']} at {connection['clock']}: {connection['commands']} commands, {state}"]
    for row in connection['rows']:
        status = f" [{row['status']}]" if row['status'] not in ('', 'ok') else ''
        lines.append(f"    {row['offset_s']:+10.6f}  {row['text']}{status}  {row['hex']}".rstrip())
    if connection['rows_dropped']:
        lines.append(f"    ({connection['rows_dropped']} more rows left out)")
    return lines


def _render_drop(number, drop):
    lines = ['', f"--- Disconnect {number} at {drop['clock']} (all times relative to this) ---"]
    if not drop['confirmed']:
        lines.append('  UNCONFIRMED: transfers failed, but neither the kernel log nor sysfs saw the device leave.')
        lines.append('  This may be a driver reconfiguring the device rather than a real disconnect.')
    lines += [f'  signal: {signal}' for signal in drop['signals']]
    if drop['since_previous_s'] is not None:
        lines.append(f"  {drop['since_previous_s']} s after the previous disconnect")
    if drop['first_stream_after_s'] is not None:
        lines.append(f"  Back: first command +{drop['first_command_after_s']} s, "
                     f"stream +{drop['first_stream_after_s']} s")
    lines.append('  What stopped first:')
    moments = drop['order'] + [{'what': f'last stream packet {ep}', 'offset_s': t}
                               for ep, t in drop['stream_last'].items()]
    for moment in sorted(moments, key=lambda item: item['offset_s']):
        lines.append(f"    {moment['offset_s']:+10.3f} s  {moment['what']}")
    if not moments:
        lines.append('    nothing recorded')
    for ep, gap in drop['stream_gap'].items():
        lines.append(f'  longest gap in stream {ep}: {gap} ms')
    in_flight = drop['in_flight'] or []
    lines.append(f"  Unanswered at the disconnect: {len(in_flight)} sends, {drop['reads_posted']} reads posted")
    lines += [f"    {item['text']}, sent {item['age_s']} s earlier" for item in in_flight]
    lines += ['  Second by second:', f"    {'second':>6}{'commands':>10}{'0x0007':>8}{'stream':>8}{'failed':>8}"]
    for second, row in drop['seconds'].items():
        lines.append(f"    {second:>+6}{row['commands']:>10}{row['target']:>8}{row['stream']:>8}{row['failed']:>8}")
    lines.append('  On the wire (stream packets left out):')
    if drop['rows_dropped']:
        lines.append(f"    ({drop['rows_dropped']} earlier rows left out; --rows shows more)")
    for row in drop['rows']:
        status = f" [{row['status']}]" if row['status'] not in ('', 'ok') else ''
        data = f"  {row['hex']}" if row['hex'] else ''
        lines.append(f"    {row['offset_s']:+10.6f}  {row['text']}{status}{data}")
    return lines
