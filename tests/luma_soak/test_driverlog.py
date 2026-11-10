from pathlib import Path
import re
import unittest

from tools.luma_soak import driverlog

FIXTURE = Path(__file__).parent / 'fixtures/driver_2026-11-03.log'


class XrDriverLines(unittest.TestCase):
    def test_failed_send(self):
        self.assertEqual(driverlog.classify('[E][libglasses] Failed to send USB command - MsgID: 0x0007, error code: -1'),
                         {'kind': 'command_failed', 'cmd': '0x0007', 'rc': -1})

    def test_display_mode_timeout(self):
        self.assertEqual(driverlog.classify('[E][libglasses] get_display_mode execute_usb_command_with_response returned -7'),
                         {'kind': 'usb_error', 'cmd': 'get_display_mode', 'rc': -7})

    def test_carina_transfer(self):
        line = '[CARINA][TIME]:258363748201\t[PID:1][TID:2]\tdo sync data transfer :-7 512 0'
        self.assertEqual(driverlog.classify(line), {'kind': 'usb_error', 'cmd': 'sync_transfer', 'rc': -7})

    def test_retry_is_a_reconnect(self):
        self.assertEqual(driverlog.classify('2026-11-03 23:00:01.403 Retrying driver connection in 1 second'), {'kind': 'reconnect'})

    def test_dated_line_carries_its_time(self):
        self.assertAlmostEqual(driverlog.line_time('2026-11-03 23:00:01.403 Waiting for glasses') % 60, 1.403, places=3)
        self.assertIsNone(driverlog.line_time('[E][libglasses] m_StateCallback is not initialized'))

    def test_noise_is_nothing(self):
        for line in ('2026-11-03 23:00:28.098 [ERROR] curl_easy_perform() failed: Could not resolve hostname', 'Error opening file (��): 2', ''):
            self.assertIsNone(driverlog.classify(line))

    def test_the_real_log_is_mostly_understood(self):
        kinds = [c['kind'] for line in FIXTURE.read_text().splitlines() if (c := driverlog.classify(line))]
        self.assertGreater(kinds.count('command_failed'), 50)
        self.assertGreater(kinds.count('reconnect'), 10)


class LumaLines(unittest.TestCase):
    def test_json(self):
        self.assertEqual(driverlog.classify('{"event": "cmd_sent", "cmd": "0x15"}'), {'kind': 'command_sent', 'cmd': '0x15'})

    def test_logfmt(self):
        self.assertEqual(driverlog.classify('luma event=cmd_failed cmd=0x15 rc=-7'), {'kind': 'command_failed', 'cmd': '0x15', 'rc': -7})

    def test_sample_gap(self):
        self.assertEqual(driverlog.classify('event=sample_gap ms=38.2'), {'kind': 'sample_gap', 'ms': 38.2})

    def test_unknown_event_is_kept_but_harmless(self):
        self.assertEqual(driverlog.classify('event=latency_stats p99=3'), {'kind': 'other', 'event': 'latency_stats'})

    def test_own_clock(self):
        self.assertEqual(driverlog.line_time('event=reconnect unix_ms=1793700000123'), 1793700000.123)

    def test_broken_json_is_not_fatal(self):
        self.assertIsNone(driverlog.classify('{"event": '))

    def test_equals_in_prose_is_not_structured(self):
        self.assertIsNone(driverlog.classify('some value=3'))


class CppLumaDriverLines(unittest.TestCase):
    """What the shipped C++ xr-luma-driver writes today: "luma: <word> key=value ... at=<epoch s>.<ms>" (main.cpp log_line)."""

    def test_disconnect(self):
        self.assertEqual(driverlog.classify('luma: disconnected reason=gone at=1793700000.123'),
                         {'kind': 'usb_error', 'cmd': 'disconnected', 'reason': 'gone'})

    def test_stale_is_a_sample_gap(self):
        self.assertEqual(driverlog.classify('luma: stale ms=250 at=1793700000.123'), {'kind': 'sample_gap', 'ms': 250.0})

    def test_retry_is_a_reconnect(self):
        self.assertEqual(driverlog.classify('luma: retry attempt=1 reason=silent at=1793700000.123'), {'kind': 'reconnect', 'reason': 'silent'})

    def test_reconnected_carries_its_gap(self):
        self.assertEqual(driverlog.classify('luma: reconnected gap_ms=4210 calibration=kept at=1793700000.123'),
                         {'kind': 'state', 'state': 'reconnected', 'ms': 4210.0, 'calibration': 'kept'})

    def test_states(self):
        for word in ('connected', 'calibrating', 'calibrated', 'waiting', 'fresh', 'stopped'):
            self.assertEqual(driverlog.classify(f'luma: {word} at=1793700000.123')['state'], word)

    def test_stats_are_kept_but_harmless(self):
        line = 'luma: stats samples=60000 rate_hz=1000 lost=0 disconnects=0 at=1793700000.123'
        self.assertEqual(driverlog.classify(line), {'kind': 'other', 'event': 'luma stats'})

    def test_own_clock(self):
        self.assertEqual(driverlog.line_time('luma: stale ms=250 at=1793700000.123'), 1793700000.123)
        self.assertIsNone(driverlog.line_time('luma: stale ms=250'))

    def test_every_line_the_cpp_driver_writes_is_understood(self):
        """A new log line in the driver must not be silently ignored by the soak."""
        source = Path(__file__).resolve().parents[2] / 'spike/driver/src'
        words = set(re.findall(r'"luma: ([a-z_]+)', ''.join(p.read_text() for p in source.glob('*.cpp'))))
        self.assertGreater(len(words), 10)
        for word in sorted(words):
            self.assertIsNotNone(driverlog.classify(f'luma: {word} x=1 at=1793700000.123'), word)

    def test_every_line_the_rust_driver_writes_is_understood(self):
        """The Rust xr-luma-driver writes the same lines (D13 compares the two), so its soak must read every one."""
        source = Path(__file__).resolve().parents[2] / 'rust/crates/xr-luma-driver/src'
        words = set(re.findall(r'"luma: ([a-z_]+)', ''.join(p.read_text() for p in source.glob('*.rs'))))
        self.assertGreater(len(words), 10)
        for word in sorted(words):
            self.assertIsNotNone(driverlog.classify(f'luma: {word} x=1 at=1793700000.123'), word)
