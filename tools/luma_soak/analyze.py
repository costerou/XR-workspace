"""Turns a session's records into a report. A live soak and a replayed log share this one code path.

Records are dicts with `src` and `t` (epoch seconds):
    meta   kind=start {tree, glasses_at_start, ids}; kind=stop {reason, duration_s, audit, glasses_at_end, suspended_s}; kind=blind {why}
    kernel line (raw journal line)
    driver line, t_hi (only when replayed: an undated line is known only to lie between t and t_hi)
    cpu    driver_pids, system_pct, core_max_pct, driver_pct, run_delay_ms
    sysfs  kind=gone|renumbered|appeared, path, id, devnum
    feed   kind=gap, start, ms; kind=stats, advances, window_s
"""
from collections import Counter
from dataclasses import dataclass, field
from datetime import datetime

from . import driverlog
from .kernel import GLASSES_IDS, HUB_IDS, UsbModel, parse_line

CPU_HIGH_PCT = 80.0
CORE_HIGH_PCT = 98.0
RUN_DELAY_HIGH_MS = 50.0
SYSFS_MATCH_S = 3.0        # a poll lands up to a second after the disconnect it saw
BURST_S = 30.0             # the hub drops in pairs 2 to 23 s apart
QUIET_AFTER_S = 30.0       # the seconds after a drop are the driver reconnecting: not a baseline
COVERAGE = 0.95            # share of the soak the driver and the feed must have been alive for

EXIT_PASS, EXIT_DROP, EXIT_INCONCLUSIVE = 0, 1, 2


@dataclass
class Policy:
    window_s: float = 5.0
    glasses_ids: frozenset = GLASSES_IDS
    hub_ids: frozenset = HUB_IDS
    port: str = None
    since: float = None
    until: float = None
    budget: int = 0
    strict: bool = False             # the camera or mic leaving, kernel USB trouble and a driver restart count against the budget
    min_duration_s: float = 1800.0
    require_driver: bool = True
    require_feed: bool = True
    max_feed_gap_ms: float = None
    notes: list = field(default_factory=list)


def iso(t):
    return datetime.fromtimestamp(t).astimezone().isoformat(timespec='milliseconds')


def _avg(values):
    return round(sum(values) / len(values), 1) if values else None


def _driver_window(lines, start, end):
    sent, failed, rc = Counter(), Counter(), Counter()
    reconnects = uncertain = 0
    gaps, last = [], []
    for at, hi, what, text in lines:
        if at > end or (hi if hi is not None else at) < start:
            continue
        if at < start or (hi is not None and hi > end):    # an undated line that may lie outside the window
            uncertain += what['kind'] in ('command_failed', 'usb_error', 'reconnect')
            continue
        kind = what['kind']
        if kind == 'command_sent':
            sent[what.get('cmd', '?')] += 1
        elif kind == 'command_failed':
            failed[what.get('cmd', '?')] += 1
            rc[str(what.get('rc'))] += 1
        elif kind == 'usb_error':
            rc[str(what.get('rc'))] += 1
        elif kind == 'reconnect':
            reconnects += 1
        elif kind == 'sample_gap':
            gaps.append(what.get('ms'))
        if kind != 'other':
            last.append(text[:160])
    return {'commands_sent': dict(sent), 'commands_failed': dict(failed), 'usb_return_codes': dict(rc), 'reconnects': reconnects,
            'sample_gaps_ms': [g for g in gaps if g is not None], 'uncertain_lines': uncertain, 'last_lines': last[-5:]}


def _feed_window(gaps, stats, start, end):
    hit = [g for g in gaps if g['start'] < end and g['start'] + g['ms'] / 1000 > start]
    stalled = [g for g in hit if g['start'] + g['ms'] / 1000 >= end]
    rates = [s['advances'] / s['window_s'] for s in stats if start < s['t'] <= end and s.get('window_s')]
    return {'gaps': len(hit), 'max_gap_ms': max((round(g['ms'], 1) for g in hit), default=None),
            'stalled_before_drop_ms': round((end - stalled[-1]['start']) * 1000, 1) if stalled else None,
            'samples_per_s': _avg(rates), 'observed': bool(rates or hit)}


def _cpu_window(samples, start, end):
    inside = [s for s in samples if start < s['t'] <= end]
    pick = lambda key: [s[key] for s in inside if key in s]
    return {'system_pct_avg': _avg(pick('system_pct')), 'system_pct_max': max(pick('system_pct'), default=None),
            'core_max_pct': max(pick('core_max_pct'), default=None), 'driver_pct_avg': _avg(pick('driver_pct')),
            'driver_pct_max': max(pick('driver_pct'), default=None), 'run_delay_ms_max': max(pick('run_delay_ms'), default=None),
            'driver_running': any(s.get('driver_pids') for s in inside) if inside else None}


def _cpu_high(sample):
    return (sample.get('system_pct', 0) >= CPU_HIGH_PCT or sample.get('core_max_pct', 0) >= CORE_HIGH_PCT
            or sample.get('run_delay_ms', 0) >= RUN_DELAY_HIGH_MS)


def _hints(drop):
    hints = []
    driver, feed, cpu = drop['driver'], drop['feed'], drop['cpu']
    if driver['commands_failed']:
        hints.append(f"{sum(driver['commands_failed'].values())} USB command failure(s) before the drop: {driver['commands_failed']}")
    if driver['commands_sent']:
        hints.append(f"{sum(driver['commands_sent'].values())} command(s) sent in the window: {driver['commands_sent']}")
    if driver['sample_gaps_ms']:
        hints.append(f"driver reported {len(driver['sample_gaps_ms'])} sample gap(s), worst {max(driver['sample_gaps_ms'])} ms")
    if feed['stalled_before_drop_ms']:
        hints.append(f"pose feed already stalled {feed['stalled_before_drop_ms']} ms before the drop")
    elif feed['gaps']:
        hints.append(f"{feed['gaps']} pose feed gap(s), worst {feed['max_gap_ms']} ms")
    if (cpu['system_pct_max'] or 0) >= CPU_HIGH_PCT or (cpu['core_max_pct'] or 0) >= CORE_HIGH_PCT:
        hints.append(f"CPU busy: system {cpu['system_pct_max']}%, busiest core {cpu['core_max_pct']}%")
    if (cpu['run_delay_ms_max'] or 0) >= RUN_DELAY_HIGH_MS:
        hints.append(f"driver waited {cpu['run_delay_ms_max']} ms for a CPU")
    if drop['kernel_trouble']:
        hints.append(f"{len(drop['kernel_trouble'])} kernel USB warning(s) before the drop")
    if driver['uncertain_lines']:
        hints.append(f"{driver['uncertain_lines']} undated driver line(s) may fall on either side of the window")
    return hints


def _signals(drops, lines, gaps, samples, kernel_trouble, first, last, window, has_feed, replay):
    """How often each signal appears before a drop, against ordinary windows of the same size. Drops
    can line up with something by chance, and the report must not call that a cause."""
    if first is None or last <= first:
        return {}
    size = window
    buckets = {}
    def mark(name, t):
        if first <= t <= last:
            buckets.setdefault(name, set()).add(int((t - first) // size))
    for at, hi, what, _ in lines:
        mark('driver_seen', at)
        if what['kind'] == 'command_failed' and (hi is None or hi - at <= size):    # the same test as _driver_window: placed to within a window
            mark('command_failed', at)
    for gap in gaps:
        mark('feed_gap', gap['start'])
    for sample in samples:
        if _cpu_high(sample):
            mark('cpu_high', sample['t'])
    for trouble in kernel_trouble:
        mark('kernel_trouble', trouble['t'])
    total = int((last - first) // size) + 1
    excluded = set()
    for drop in drops:
        low, high = int((drop['t'] - window - first) // size), int((drop['t'] + QUIET_AFTER_S - first) // size)
        excluded.update(range(low, high + 1))
    quiet = [b for b in range(total) if b not in excluded]
    before = {'command_failed': lambda d: bool(d['driver']['commands_failed']), 'feed_gap': lambda d: bool(d['feed']['gaps']),
              'cpu_high': lambda d: _cpu_high({'system_pct': d['cpu']['system_pct_max'] or 0, 'core_max_pct': d['cpu']['core_max_pct'] or 0, 'run_delay_ms': d['cpu']['run_delay_ms_max'] or 0}),
              'kernel_trouble': lambda d: bool(d['kernel_trouble'])}
    out = {}
    for name, test in before.items():
        if (name == 'feed_gap' and not has_feed) or (name == 'cpu_high' and not samples):
            continue   # nothing watched it: zero of N would read as a finding
        seen = buckets.get(name, set())
        pool = quiet
        if name == 'command_failed' and replay:   # a saved log says nothing about hours with no driver running: they are not quiet
            pool = [b for b in quiet if b in buckets.get('driver_seen', set())]
        out[name] = {'before_drops': sum(1 for d in drops if test(d)), 'drops': len(drops),
                     'quiet_windows': sum(1 for b in pool if b in seen), 'quiet_total': len(pool)}
    return out


def analyze(records, policy=None):
    policy = policy or Policy()
    start = next((r for r in records if r.get('src') == 'meta' and r.get('kind') == 'start'), None)
    stop = next((r for r in reversed(records) if r.get('src') == 'meta' and r.get('kind') == 'stop'), None)
    glasses_ids = frozenset(start.get('glasses_ids', policy.glasses_ids)) if start else policy.glasses_ids
    hub_ids = frozenset(start.get('hub_ids', policy.hub_ids)) if start else policy.hub_ids
    model = UsbModel(glasses_ids, hub_ids, tree=start.get('tree') if start else None, port=policy.port)
    since = policy.since if policy.since is not None else (start['t'] if start else None)
    until = policy.until if policy.until is not None else (stop['t'] if stop else None)

    def inside(t):
        return (since is None or t >= since) and (until is None or t <= until)

    seen = Counter()
    times = []
    lines, gaps, stats, samples, witness, blind = [], [], [], [], [], []
    for record in records:
        src = record.get('src')
        if src == 'kernel':
            parsed = parse_line(record['line'])
            if parsed:
                seen[record['line']] += 1
                if seen[record['line']] == 1:   # the end-of-soak audit re-reads what the stream already gave
                    times.append(parsed)
        elif src == 'driver':
            what = driverlog.classify(record['line'])
            if what and inside(record['t']):
                lines.append((record['t'], record.get('t_hi'), what, record['line']))
        elif src == 'cpu' and inside(record['t']):
            samples.append(record)
        elif src == 'feed' and inside(record['t']):
            (gaps if record.get('kind') == 'gap' else stats).append(record)
        elif src == 'sysfs' and inside(record['t']):
            witness.append(record)
        elif src == 'meta' and record.get('kind') == 'blind':
            blind.append(record.get('why', 'unknown'))
    times.sort(key=lambda item: item[0])
    for t, message in times:
        model.line(t, message)

    clusters = [c for c in model.clusters if inside(c.t)]
    drops, siblings, unattributed = [], [], []
    for cluster in clusters:
        kind, scope = model.classify(cluster)
        entry = {'t': cluster.t, 'at': iso(cluster.t), 'scope': scope,
                 'members': [{'path': m.path, 'id': m.id, 'product': m.product, 'devnum': m.devnum} for m in cluster.members]}
        if kind == 'drop':
            drops.append(entry)
        elif kind == 'sibling':
            siblings.append(entry)
        elif kind == 'unattributed':
            unattributed.append(entry)
    last_sysfs = None
    for change in witness:
        if change.get('kind') not in ('gone', 'renumbered'):
            continue
        t = change['t']
        if any(abs(d['t'] - t) <= SYSFS_MATCH_S for d in drops) or (last_sysfs is not None and t - last_sysfs <= SYSFS_MATCH_S):
            continue
        last_sysfs = t
        drops.append({'t': t, 'at': iso(t), 'scope': 'sysfs', 'members': [{'path': change['path'], 'id': change['id'], 'product': None, 'devnum': change.get('devnum')}]})
    drops.sort(key=lambda d: d['t'])

    trouble = [a for a in model.anomalies if a['related'] and inside(a['t'])]
    for index, drop in enumerate(drops):
        t, low = drop['t'], drop['t'] - policy.window_s
        following = drops[index + 1]['t'] if index + 1 < len(drops) else float('inf')
        back = next((a for a in model.arrivals if t < a < following), None)
        drop['recovered_s'] = round(back - t, 2) if back is not None else None
        drop['since_previous_s'] = round(t - drops[index - 1]['t'], 2) if index else None
        drop['window_s'] = policy.window_s
        drop['driver'] = _driver_window(lines, low, t)
        drop['feed'] = _feed_window(gaps, stats, low, t)
        drop['cpu'] = _cpu_window(samples, low, t)
        drop['kernel_trouble'] = [{'at': iso(a['t']), 'text': a['text']} for a in trouble if low <= a['t'] < t]
        drop['hints'] = _hints(drop)

    bursts = 0
    for index, drop in enumerate(drops):
        bursts += index == 0 or drop['t'] - drops[index - 1]['t'] > BURST_S
    first = since if since is not None else (times[0][0] if times else None)
    last = until if until is not None else max((r['t'] for r in records if r.get('src') not in ('kernel', 'driver')), default=times[-1][0] if times else None)
    duration = stop.get('duration_s') if stop else None
    span = duration if duration is not None else (last - first if first is not None and last is not None else None)
    recovered = [d['recovered_s'] for d in drops if d['recovered_s'] is not None]
    alive = [s for s in samples if s.get('driver_pids')]
    live_stats = [s for s in stats if s.get('advances')]
    rates = [s['advances'] / s['window_s'] for s in live_stats if s.get('window_s')]
    report = {
        'mode': 'live' if start else 'replay',
        'started': iso(first) if first is not None else None, 'ended': iso(last) if last is not None else None,
        'duration_s': round(span, 1) if span is not None else None,
        'stop_reason': stop.get('reason') if stop else None,
        'drops': len(drops), 'bursts': bursts,
        'drops_per_hour': round(len(drops) * 3600 / span, 2) if span else None,
        'hub_drops': sum(1 for d in drops if d['scope'] == 'hub'),
        'glasses_only_drops': sum(1 for d in drops if d['scope'] == 'glasses'),
        'sysfs_only_drops': sum(1 for d in drops if d['scope'] == 'sysfs'),
        'recovery_s': {'count': len(recovered), 'avg': round(sum(recovered) / len(recovered), 2), 'max': max(recovered)} if recovered else None,
        'never_recovered': len(drops) - len(recovered),
        'drop_details': drops,
        'siblings': siblings, 'unattributed': unattributed,
        'kernel_trouble': [{'at': iso(a['t']), 'path': a['path'], 'text': a['text']} for a in trouble],
        'signals': _signals(drops, lines, gaps, samples, trouble, first, last, policy.window_s, bool(gaps or stats), start is None),
        'coverage': {'driver': round(len(alive) / len(samples), 3) if samples else None,
                     'feed': round(len(live_stats) / len(stats), 3) if stats else None,
                     'feed_samples_per_s': _avg(rates), 'worst_feed_gap_ms': max((round(g['ms'], 1) for g in gaps), default=None),
                     'driver_processes': len({pid for s in samples for pid in s.get('driver_pids', ())}),
                     'cpu_samples': len(samples), 'driver_lines': len(lines), 'kernel_lines': len(times)},
        'watch': {'glasses_at_start': start.get('glasses_at_start') if start else None, 'glasses_at_end': stop.get('glasses_at_end') if stop else None,
                  'audit': stop.get('audit') if stop else None, 'suspended_s': stop.get('suspended_s') if stop else None, 'blind': blind},
    }
    report['verdict'] = verdict(report, policy)
    return report


def verdict(report, policy):
    """FAIL on any drop. PASS only with proof the soak really tested something: an idle driver, glasses
    in a drawer, a dead watcher or a short run all show zero drops, and none of them is a zero-drop soak."""
    count = report['drops']
    failures = [f"{count} drop(s), budget {policy.budget}"] if count > policy.budget else []
    if policy.strict:
        restarts = max(report['coverage']['driver_processes'] - 1, 0)
        if len(report['siblings']) + len(report['kernel_trouble']) + restarts:
            failures.append(f"strict: {len(report['siblings'])} camera/mic disconnect(s), {len(report['kernel_trouble'])} kernel USB warning(s), {restarts} driver restart(s)")
    worst = report['coverage']['worst_feed_gap_ms']
    if policy.max_feed_gap_ms is not None and worst is not None and worst > policy.max_feed_gap_ms:
        failures.append(f'worst pose feed gap {worst} ms, limit {policy.max_feed_gap_ms} ms')
    if failures:
        return {'result': 'FAIL', 'exit': EXIT_DROP, 'reasons': failures}
    unproven = []
    if report['mode'] == 'replay':
        unproven.append('a replayed log cannot prove zero drops: nothing shows the glasses, the driver or the log were all live')
    else:
        watch, coverage = report['watch'], report['coverage']
        if (report['duration_s'] or 0) < policy.min_duration_s:
            unproven.append(f"ran {report['duration_s']} s of the required {policy.min_duration_s:g} s")
        if watch['audit'] != 'ok':
            unproven.append(f"kernel log audit at the end: {watch['audit']}")
        if not watch['glasses_at_start']:
            unproven.append('glasses were not on USB when the soak started')
        if not watch['glasses_at_end']:
            unproven.append('glasses were not on USB when the soak ended')
        if watch['suspended_s']:
            unproven.append(f"the machine suspended for {watch['suspended_s']} s")
        if policy.require_driver and (coverage['driver'] or 0) < COVERAGE:
            unproven.append(f"driver running for {coverage['driver']} of the soak, need {COVERAGE}")
        if policy.require_feed and (coverage['feed'] or 0) < COVERAGE:
            unproven.append(f"pose feed advancing for {coverage['feed']} of the soak, need {COVERAGE}")
    if report['unattributed']:
        unproven.append(f"{len(report['unattributed'])} USB disconnect(s) on a port nothing identified")
    if unproven:
        return {'result': 'INCONCLUSIVE', 'exit': EXIT_INCONCLUSIVE, 'reasons': unproven}
    return {'result': 'PASS', 'exit': EXIT_PASS, 'reasons': []}


def records_from_logs(kernel_lines, driver_lines=()):
    """Replay: kernel lines as they are; an undated driver line takes the last date before it, and the next date as an upper bound."""
    records = [{'src': 'kernel', 't': 0, 'line': line} for line in kernel_lines]
    pending, at = [], None
    for line in driver_lines:
        own = driverlog.line_time(line)
        if own is not None:
            for record in pending:
                record['t_hi'] = own
            pending, at = [], own
            records.append({'src': 'driver', 't': own, 'line': line})
        elif at is not None:
            record = {'src': 'driver', 't': at, 'line': line, 't_hi': at}
            pending.append(record)
            records.append(record)
    for record in pending:
        record['t_hi'] = float('inf')
    return records
