"""Turns captures into numbers: command rates, and what was on the wire before each disconnect.

Two passes over the files, so a long capture needs memory only for the windows around drops. The first
finds the drops, the stream endpoints and samples for working out the framing; the second counts.
"""
from bisect import bisect_right
from collections import Counter, defaultdict
import statistics
import time

from .capfile import XFER_CTRL, expand
from .protocol import (CANCELLED, GET_DISPLAY_MODE, STREAM_MIN_HZ, STREAM_OVER_COMMANDS, Labeler, confirms_drop,
                       drop_signal, infer_framing, status_name)

CLUSTER_NS = 2_000_000_000       # signals of one disconnect arrive together; the hub's pairs are 2 s or more apart
AFTER_NS = 1_000_000_000         # rows kept after the disconnect
SAMPLES = 5000
HEX_BYTES = 24


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


def _first_pass(readers):
    scan = {'first': None, 'last': None, 'usb': 0, 'notes': 0, 'lost': 0, 'seconds': set(), 'signals': [],
            'endpoints': defaultdict(lambda: {'transfers': 0, 'bytes': 0, 'first': None, 'last': None}),
            'driver_errors': 0, 'kernel_log': False, 'driver_log': False}
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
        else:
            scan['usb'] += 1
            scan['first'] = record.t_ns if scan['first'] is None else min(scan['first'], record.t_ns)
            scan['last'] = record.t_ns if scan['last'] is None else max(scan['last'], record.t_ns)
            scan['seconds'].add(record.t_ns // 10**9)
            if record.glasses and record.event == ('C' if record.is_in else 'S') and record.xfer != XFER_CTRL:
                stats = scan['endpoints'][record.ep]
                stats['transfers'] += 1
                stats['bytes'] += record.urb_len
                stats['first'] = stats['first'] if stats['first'] is not None else record.t_ns
                stats['last'] = record.t_ns
            if record.glasses and record.data:
                if _is_command(record):
                    commands += 1
                    if len(outgoing) < SAMPLES:
                        outgoing.append((record.t_ns, False, record.data))
                elif record.is_in and record.event == 'C' and len(incoming[record.ep]) < SAMPLES:
                    incoming[record.ep].append((record.t_ns, True, record.data))
        signal = drop_signal(record, port)
        if signal:
            scan['signals'].append((record.t_ns, signal))
    streams = set()
    for ep, stats in scan['endpoints'].items():
        span = (stats['last'] - stats['first']) / 1e9
        stats['rate'] = stats['transfers'] / span if span > 0 else 0.0
        # Fast is not enough: replies to a command flood are fast too, and hiding those would hide the flood.
        if (ep & 0x80 and stats['transfers'] >= STREAM_MIN_HZ and stats['rate'] >= STREAM_MIN_HZ
                and stats['transfers'] > STREAM_OVER_COMMANDS * commands):
            streams.add(ep)
    samples = sorted(outgoing + [s for ep, batch in incoming.items() if ep not in streams for s in batch],
                     key=lambda sample: sample[0])
    return scan, streams, samples


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


def _new_window(drop, window_ns):
    return {'t_ns': drop['t_ns'], 'clock': clock(drop['t_ns']), 'signals': drop['signals'][:6],
            'confirmed': any(map(confirms_drop, drop['signals'])),
            'start': drop['t_ns'] - window_ns, 'end': drop['t_ns'] + AFTER_NS, 'rows': [],
            'seconds': defaultdict(lambda: {'commands': 0, 'target': 0, 'stream': 0, 'failed': 0}),
            'stream_last': {}, 'stream_gap': {}, 'firsts': {}, 'in_flight': None, 'reads_posted': 0}


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
        if note.get('type') in ('kernel', 'driver_log'):
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
        what = f'{label.text if origin is None else origin["text"]} ({status_name(record.status)})'
        row = _row(record, 'FAILED ' + what, anchor)
        row['status'] = ''
        window['rows'].append(row)
        kind = 'first cancelled transfer (a timeout)' if record.status in CANCELLED else 'first failed transfer'
        firsts.setdefault(kind, record.t_ns)


def _second_pass(readers, framing, streams, drops, window_s, rows):
    labeler = Labeler(framing, streams)
    per_key = defaultdict(Counter)
    sizes = {}
    latency = defaultdict(list)
    failures = Counter()
    windows = [_new_window(drop, _ns(window_s)) for drop in drops]
    starts = [window['start'] for window in windows]
    pending = {}
    next_snapshot = 0
    for record in _records(readers):
        while next_snapshot < len(windows) and record.t_ns >= windows[next_snapshot]['t_ns']:
            window = windows[next_snapshot]
            window['in_flight'] = [{'age_s': round((window['t_ns'] - p['t_ns']) / 1e9, 6), 'text': p['text']}
                                   for p in pending.values() if not p['is_in']]
            window['reads_posted'] = sum(p['is_in'] for p in pending.values())
            next_snapshot += 1
        label = None
        if not record.is_note:
            label = labeler.label(record)
            if _is_command(record):
                key, _ = labeler.command_key(record)
                per_key[key][record.t_ns // 10**9] += 1
                sizes[key] = record.urb_len
            if record.glasses and record.xfer != XFER_CTRL:
                if record.event == 'S':
                    pending[record.urb_id] = {'t_ns': record.t_ns, 'is_in': record.is_in, 'text': label.text}
                elif record.event == 'C':
                    if record.status:
                        failures[(record.ep, record.status)] += 1
                    origin = pending.get(record.urb_id)
                    if origin and not record.is_in:
                        latency[record.ep].append((record.t_ns - origin['t_ns']) / 1e6)
        for candidate in reversed(windows[:bisect_right(starts, record.t_ns)]):
            if record.t_ns > candidate['end']:
                break     # ends only get earlier from here, so no older window can hold it
            _window_add(candidate, record, label, pending)
        if label is not None and record.glasses and record.xfer != XFER_CTRL and record.event == 'C':
            pending.pop(record.urb_id, None)
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
    return per_key, sizes, latency, failures, windows


def _rate(per_second, seconds):
    total = sum(per_second.values())
    return {'total': total, 'per_s': round(total / max(seconds, 1), 3), 'peak_per_s': max(per_second.values())}


def analyze(paths, *, framing=None, window_s=5.0, rows=200):
    """Everything the report prints, as plain data. Pass a Framing to overrule inference."""
    files = expand(paths)
    scan, streams, samples = _first_pass(files)
    inference = infer_framing(samples)
    chosen = framing or inference.framing
    drops = _cluster(scan['signals'])
    per_key, sizes, latency, failures, windows = _second_pass(files, chosen, streams, drops, window_s, rows)
    seconds = len(scan['seconds'])
    commands = {key: {**_rate(counts, seconds), 'size': sizes[key]} for key, counts in per_key.items()}
    target = commands.get(f'MsgID {GET_DISPLAY_MODE:#06x}')
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
        'framing': {'spec': chosen.spec() if chosen else None, 'source': 'given' if framing else inference.verdict,
                    'candidates': [{'spec': c.framing.spec(), 'packets_0x0007': c.packets,
                                    'distinct_values': c.distinct, 'replies_echo': round(c.echo, 3)}
                                   for c in inference.candidates]},
        'msgid_0x0007': None if chosen is None else (target or {'total': 0, 'per_s': 0.0, 'peak_per_s': 0}),
        'commands': dict(sorted(commands.items(), key=lambda item: -item[1]['total'])),
        'endpoints': {f'ep{ep:02x}': {'transfers': stats['transfers'], 'bytes': stats['bytes'],
                                      'per_s': round(stats['rate'], 1), 'stream': ep in streams}
                      for ep, stats in sorted(scan['endpoints'].items())},
        'send_latency_ms': {f'ep{ep:02x}': {'p50': round(statistics.median(values), 3),
                                            'max': round(max(values), 3), 'count': len(values)}
                            for ep, values in sorted(latency.items())},
        'failures': [{'ep': f'ep{ep:02x}', 'status': status_name(status), 'count': count}
                     for (ep, status), count in failures.most_common()],
        'drops': windows,
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
        lines.append(f"  field: {framing['spec']} ({framing['source']}"
                     f"{', not confirmed by a spec' if framing['source'] == 'inferred' else ''})")
    for candidate in framing['candidates']:
        lines.append(f"  candidate {candidate['spec']}: 0x0007 in {candidate['packets_0x0007']} sampled, "
                     f"{candidate['distinct_values']} values, replies echo {candidate['replies_echo']:.0%}")

    lines += ['', 'Commands (host to glasses)', f"  {'what':<32}{'total':>8}{'per s':>9}{'peak/s':>8}"]
    for key, row in result['commands'].items():
        lines.append(f"  {key:<32}{row['total']:>8}{row['per_s']:>9}{row['peak_per_s']:>8}")
    if not result['commands']:
        lines.append('  none')

    lines += ['', 'Endpoints']
    for name, row in result['endpoints'].items():
        kind = ' stream' if row['stream'] else ''
        lines.append(f"  {name}: {row['transfers']} transfers, {row['bytes']} bytes, {row['per_s']}/s{kind}")
    for name, row in result['send_latency_ms'].items():
        lines.append(f"  {name} send took: median {row['p50']} ms, worst {row['max']} ms")
    for row in result['failures']:
        lines.append(f"  {row['ep']} failed {row['count']} times with {row['status']}")

    confirmed = sum(drop['confirmed'] for drop in result['drops'])
    unconfirmed = len(result['drops']) - confirmed
    lines += ['', f"Disconnects: {confirmed}" + (f' (and {unconfirmed} unconfirmed)' if unconfirmed else '')]
    for number, drop in enumerate(result['drops'], 1):
        lines += _render_drop(number, drop)
    return '\n'.join(lines) + '\n'


def _render_drop(number, drop):
    lines = ['', f"--- Disconnect {number} at {drop['clock']} (all times relative to this) ---"]
    if not drop['confirmed']:
        lines.append('  UNCONFIRMED: transfers failed, but neither the kernel log nor sysfs saw the device leave.')
        lines.append('  This may be a driver reconfiguring the device rather than a real disconnect.')
    lines += [f'  signal: {signal}' for signal in drop['signals']]
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
