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
                prediction_enabled=prediction.get('enabled'))


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
