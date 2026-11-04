"""Optional real-capture regressions, with an explicit offline recording directory.
No test opens devices or invokes a running driver. Generate logs with --replay first.
"""
import os
from pathlib import Path
import pytest
from tools.stabilization.measure import measure, synthetic

TRACE = Path(__file__).resolve().parents[2] / 'build' / 'stabilization_trace'
RECORDINGS = os.environ.get('XR_STABILIZATION_RECORDINGS')
pytestmark = pytest.mark.skipif(not RECORDINGS or not TRACE.exists(), reason='needs offline replay logs and built stabilization_trace')


def test_real_still_capture_reduces_jitter_at_every_level():
    result, _ = measure(TRACE, Path(RECORDINGS) / 'stage-poses.bin')
    assert result['quiet_windows'] >= 60
    assert all(actual >= minimum for actual, minimum in zip(result['reduction_pct'][1:], [25,45,60]))


def test_twenty_minute_capture_and_recorded_turns():
    result, _ = measure(TRACE, Path(RECORDINGS) / 'usbmon-poses.bin')
    assert result['seconds'] >= 1200
    assert all(actual >= minimum for actual, minimum in zip(result['reduction_pct'][1:], [25,40,45]))
    for speed in ('30', '90'):
        band = result['lag_bands'][speed]
        assert band['samples'] >= 100
        assert max(band['max_added_ms'][1:]) < 2


def test_turn_onsets_between_display_frames():
    for trial in synthetic(TRACE):
        assert max(trial['max_added_ms'][1:]) < 2
