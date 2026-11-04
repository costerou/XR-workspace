import os
from pathlib import Path
import tempfile
import time
import unittest

from tests.luma_soak.fakes import Feed, Machine
from tools.luma_soak import probes


class TreeTests(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.machine = Machine(tmp.name)
        self.machine.glasses()

    def tree(self):
        return probes.usb_tree(self.machine.sysfs)

    def test_reads_devices_and_skips_interfaces_and_root_hubs(self):
        (self.machine.sysfs / '5-2.1:1.0').mkdir()
        (self.machine.sysfs / 'usb5').mkdir()
        self.assertEqual(sorted(self.tree()), ['5-2', '5-2.1'])
        self.assertEqual(self.tree()['5-2.1']['id'], '35ca:1104')
        self.assertEqual(self.tree()['5-2.1']['devnum'], 4)

    def test_missing_sysfs_is_an_empty_tree(self):
        self.assertEqual(probes.usb_tree(self.machine.root / 'nope'), {})

    def test_a_new_device_number_is_a_re_enumeration(self):
        before = self.tree()
        self.machine.plug('5-2.1', '35ca:1104', 9)
        self.assertEqual([c['kind'] for c in probes.tree_changes(before, self.tree(), {'35ca:1104'})], ['renumbered'])

    def test_gone_and_appeared(self):
        before = {'5-2.1': {'id': '35ca:1104', 'devnum': 4}}
        self.assertEqual(probes.tree_changes(before, {}, {'35ca:1104'})[0]['kind'], 'gone')
        self.assertEqual(probes.tree_changes({}, before, {'35ca:1104'})[0]['kind'], 'appeared')

    def test_a_moved_port_reads_as_gone_and_appeared(self):
        before, after = {'5-2.1': {'id': '35ca:1104', 'devnum': 4}}, {'5-2.3': {'id': '35ca:1104', 'devnum': 9}}
        self.assertEqual(sorted(c['kind'] for c in probes.tree_changes(before, after, {'35ca:1104'})), ['appeared', 'gone'])

    def test_unwatched_devices_are_silent(self):
        self.assertEqual(probes.tree_changes({'1-4': {'id': '057e:2009', 'devnum': 2}}, {}, {'35ca:1104'}), [])


class CpuTests(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.machine = Machine(tmp.name)
        self.now = 100.0

    def sampler(self):
        return probes.CpuSampler(self.machine.proc, clock=lambda: self.now)

    def test_busy_core_and_driver(self):
        self.machine.driver()
        sampler = self.sampler()
        first = sampler.sample()
        self.assertEqual(first['driver_pids'], [4242])
        self.assertNotIn('system_pct', first)
        (self.machine.proc / 'stat').write_text('cpu  150 0 150 900 0 0 0 0 0 0\ncpu0 100 0 100 400 0 0 0 0 0 0\n')
        (self.machine.proc / '4242/stat').write_text('4242 (xrDriver x) S ' + ' '.join(['0'] * 10) + f' {5 + sampler.tick // 2} 5 0\n')
        (self.machine.proc / '4242/task/4242/schedstat').write_text('100 12000000 3\n')
        self.now += 1
        out = sampler.sample()
        self.assertEqual((out['system_pct'], out['core_max_pct']), (50.0, 100.0))
        self.assertAlmostEqual(out['driver_pct'], 50.0, delta=1.0)
        self.assertEqual(out['run_delay_ms'], 10.0)

    def test_name_with_a_bracket_in_stat(self):
        self.machine.driver()
        (self.machine.proc / '4242/stat').write_text('4242 (a) b) S ' + ' '.join(['0'] * 10) + ' 7 8 0\n')
        self.assertEqual(self.sampler()._driver(4242)[0], 15)

    def test_ticks_match_the_kernels_own_format(self):
        sampler = probes.CpuSampler()
        deadline = time.monotonic() + 0.3
        while time.monotonic() < deadline:
            pass
        ticks, delay = sampler._driver(os.getpid())
        times = os.times()
        self.assertAlmostEqual(ticks, (times.user + times.system) * sampler.tick, delta=sampler.tick * 0.2)
        self.assertGreaterEqual(delay, 0)

    def test_a_driver_that_exits_stops_being_counted(self):
        self.machine.driver()
        sampler = self.sampler()
        sampler.sample()
        (self.machine.proc / '4242/stat').unlink()
        self.assertEqual(sampler.sample()['driver_pids'], [])

    def test_other_processes_are_not_the_driver(self):
        self.machine.driver(name='bash')
        self.assertEqual(self.sampler().sample()['driver_pids'], [])

    def test_real_proc_reads(self):
        sampler = probes.CpuSampler()
        sampler.sample()
        time.sleep(0.05)
        self.assertIn('system_pct', sampler.sample())


class FeedTests(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.path = f'{tmp.name}/feed'
        self.feed = Feed(self.path)
        self.addCleanup(self.feed.close)

    def test_sees_a_change_through_a_read_only_attachment(self):
        value = probes.FeedValue(self.path)
        self.addCleanup(value.close)
        self.assertTrue(value.attach())
        before = value.read()
        self.feed.value.value = 33.0
        self.assertNotEqual(value.read(), before)

    def test_the_attachment_is_mapped_read_only(self):
        value = probes.FeedValue(self.path)
        self.addCleanup(value.close)
        value.attach()
        start = f'{value.addr:x}-'
        mapping = next(line for line in Path('/proc/self/maps').read_text().splitlines() if line.startswith(start))
        self.assertTrue(mapping.split()[1].startswith('r-'), mapping)   # never a real write: a crash here would dump core on Cody's desktop

    def test_nan_counts_as_one_value(self):
        value = probes.FeedValue(self.path)
        self.addCleanup(value.close)
        value.attach()
        self.feed.value.value = float('nan')
        self.assertEqual(value.read(), value.read())

    def test_no_segment_is_no_reading(self):
        value = probes.FeedValue(self.path + '-absent')
        self.assertFalse(value.attach())
        self.assertIsNone(value.read())
