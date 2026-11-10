"""The pose age and read-to-present baseline of one run, from the renderer's exit reports in the run's journal. Read-only.

The renderer prints two reports on stderr when it exits, one after the other, and the run's systemd unit keeps them in its
journal: `generated_pattern_presentation_timing` (presented frames, the observation time) and `managed_output_capture`,
whose `latency` holds whole-run histograms (0.25 ms buckets) of
  - `read_to_present`: pose read to the compositor's presented timestamp, both CLOCK_MONOTONIC (software time only);
  - `pose_age_relative`: host read time minus the pose's device time, less the smallest such value over the last 8 s of
    device time (`PoseAge`, spike/include/xr/predict.hpp). It is age above the best recent case, not an absolute age.
The 1 Hz status dict carries neither, so the exit report is the only whole-run record.

A renderer that ran more than once in a run (a respawn) leaves one pair per life. Percentiles cannot be merged, so the
longest life is the measurement and the others are listed.
"""
import json
from pathlib import Path

from tools.xr_controller.incident_detect import exit_report

TIMING, CAPTURE = 'generated_pattern_presentation_timing', 'managed_output_capture'
MIN_DURATION_S = 1800.0
# The D4 late-latch gate (docs/rust-rebuild-plan.md): on a run of at least MIN_DURATION_S, read_to_present p50 and p95 at
# or under these, no discarded frame and no missed latch. Until a live run passes it, late latch stays off by default.
D4_P50_MS, D4_P95_MS = 9.0, 11.0
LATE_LATCH = ('guard_ms', 'margin_ms', 'draw_max_ms', 'boost_ms', 'scheduled', 'waited', 'latched', 'missed', 'immediate',
              'slack_p5_ms', 'slack_p50_ms', 'draw_p50_ms', 'draw_p99_ms', 'draw_worst_ms')
STATS = ('count', 'p50_ms', 'p95_ms', 'mean_ms', 'max_ms')


def lives(lines):
    """One (timing, capture) pair per renderer life, in journal order. A capture report with no timing report before it
    is kept with timing None."""
    out, timing = [], None
    for line in lines:
        report = exit_report(line)
        if report is None: continue
        if report['kind'] == TIMING: timing = report
        elif report['kind'] == CAPTURE: out.append((timing, report)); timing = None
    return out


def _stats(raw, extra=()):
    raw = raw if isinstance(raw, dict) else {}
    return {k: raw.get(k) for k in (*STATS, *extra)}


def _measure(timing, capture):
    timing = timing or {}
    latency = capture.get('latency') if isinstance(capture.get('latency'), dict) else {}
    duration_ns = timing.get('observation_duration_ns') or 0
    intervals, interval_sum = timing.get('interval_count') or 0, timing.get('interval_sum_ns') or 0
    prediction = latency.get('prediction') if isinstance(latency.get('prediction'), dict) else {}
    return dict(duration_s=round(duration_ns / 1e9, 3), presented=timing.get('presented'), discarded=timing.get('discarded'),
                presented_hz=round(intervals * 1e9 / interval_sum, 3) if interval_sum else None,
                clock_monotonic=latency.get('clock_monotonic') is True,
                read_to_present_ms=_stats(latency.get('read_to_present'), ('dropped',)),
                pose_age_ms=dict(basis='relative', **_stats(latency.get('pose_age_relative'))),
                prediction_enabled=prediction.get('enabled'),
                late_latch=_late_latch(latency.get('late_latch')))


def _late_latch(raw):
    """The renderer's late-latch summary (--late-latch-guard-ms), or None when the run had it off or predates it."""
    if not isinstance(raw, dict) or raw.get('enabled') is not True: return None
    return {k: raw.get(k) for k in LATE_LATCH}


def d4_gate(record, min_duration_s=MIN_DURATION_S):
    """The D4 late-latch gate on a measured record: (PASS or FAIL or INCONCLUSIVE, reasons). None without late latch."""
    latch = record.get('late_latch')
    if not latch: return None
    rtp = record.get('read_to_present_ms') or {}
    inconclusive, fail = [], []
    if (record.get('duration_s') or 0) < min_duration_s:
        inconclusive.append(f"the renderer ran {record.get('duration_s') or 0:.0f} s, under {min_duration_s:.0f} s")
    if not record.get('clock_monotonic'): inconclusive.append('the compositor clock is not CLOCK_MONOTONIC')
    if not rtp.get('count'): inconclusive.append('no read-to-present samples')
    if not latch.get('scheduled'): inconclusive.append('no frame was drawn on the late-latch schedule')
    if rtp.get('p50_ms') is not None and rtp['p50_ms'] > D4_P50_MS: fail.append(f"read_to_present p50 {rtp['p50_ms']} ms > {D4_P50_MS} ms")
    if rtp.get('p95_ms') is not None and rtp['p95_ms'] > D4_P95_MS: fail.append(f"read_to_present p95 {rtp['p95_ms']} ms > {D4_P95_MS} ms")
    if record.get('discarded'): fail.append(f"{record['discarded']} discarded frames")
    if latch.get('missed'): fail.append(f"{latch['missed']} missed latches")
    if fail: return 'FAIL', fail + inconclusive
    return ('INCONCLUSIVE' if inconclusive else 'PASS'), inconclusive


def baseline(lines, run_id, consent=None, result=None, min_duration_s=MIN_DURATION_S, require_tracking='luma'):
    """The record for one run, with `verdict` PASS only if it is a usable baseline, else INCONCLUSIVE and `reasons`."""
    found = lives(lines)
    measured = [_measure(t, c) for t, c in found]
    record = dict(kind='latency_baseline', schema=1, run_id=run_id, tracking=(consent or {}).get('tracking'),
                  stop_reason=(result or {}).get('stop_reason'), lives=len(measured))
    reasons = []
    if require_tracking and record['tracking'] != require_tracking:
        reasons.append(f"tracking is {record['tracking']!r}, not {require_tracking!r}")
    if not measured:
        reasons.append('no renderer exit report in the journal (the renderer did not exit cleanly, or the journal rotated)')
        return dict(record, verdict='INCONCLUSIVE', reasons=reasons)
    best = max(range(len(measured)), key=lambda i: measured[i]['duration_s'])
    record.update(measured[best])
    if len(measured) > 1: record['other_lives_s'] = [m['duration_s'] for i, m in enumerate(measured) if i != best]
    if record['duration_s'] < min_duration_s:
        reasons.append(f"the renderer ran {record['duration_s']:.0f} s, under {min_duration_s:.0f} s")
    if not record['clock_monotonic']: reasons.append('the compositor clock is not CLOCK_MONOTONIC: read-to-present is not comparable')
    if not record['read_to_present_ms']['count']: reasons.append('no read-to-present samples')
    if not record['pose_age_ms']['count']: reasons.append('no pose age samples (no pose feed, or tracking never started)')
    gate = d4_gate(record, min_duration_s)
    if gate: record.update(d4_verdict=gate[0], d4_reasons=gate[1])
    return dict(record, verdict='INCONCLUSIVE' if reasons else 'PASS', reasons=reasons)


def read_json(path):
    """A small private JSON file, or None when it is not there."""
    try:
        with open(path, 'rb') as f: data = f.read(1 << 20)
    except FileNotFoundError: return None
    value = json.loads(data)
    return value if isinstance(value, dict) else None


def latest_run(state_dir, tracking='luma'):
    """The newest finished run (by its result file) whose consent asked for this tracking, or None."""
    results = sorted(Path(state_dir).glob('*.result.json'), key=lambda p: p.stat().st_mtime, reverse=True)
    for path in results:
        run_id = path.name.removesuffix('.result.json')
        consent = read_json(Path(state_dir) / f'{run_id}.json') or {}
        if consent.get('tracking') == tracking: return run_id
    return None
