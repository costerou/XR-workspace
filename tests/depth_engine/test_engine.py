import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

import numpy as np

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
from tools.depth_engine import sample, shm as S  # noqa: E402
from tools.depth_engine.backends import BackendUnavailable, FakeBackend, make_backend, resize_rgba  # noqa: E402
from tools.depth_engine.engine import BOX_HOLD, Engine, picture_box  # noqa: E402
from tools.depth_engine.scheduler import Scheduler  # noqa: E402


def frame(value, w=42, h=28):
    px = np.full((h, w, 4), value, np.uint8)
    px[:, :, 3] = 255
    px[h // 2:, :, :3] = 255 - value  # Some structure, so the map is not flat.
    return px


class Clock:
    def __init__(self):
        self.t = 1_000_000_000

    def __call__(self):
        return self.t


class Base(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.TemporaryDirectory()
        self.addCleanup(self.dir.cleanup)
        self.path = os.path.join(self.dir.name, "shm")
        self.shm = S.DepthShm.create(self.path, 4, 56, 42, 56, 42)
        self.addCleanup(self.shm.close)
        self.clock = Clock()

    def engine(self, backend=None, **kw):
        sched = Scheduler(**kw) if kw else Scheduler(focused_hz=1e6, other_hz=1e6)
        return Engine(self.shm, backend or FakeBackend(), sched, self.clock)

    def tick(self, ms=20):
        self.clock.t += ms * 1_000_000


class Shm(Base):
    def test_round_trip(self):
        px = frame(10)
        self.assertTrue(self.shm.publish(S.INPUT, 2, px, S.FMT_RGBA8, 7, 100))
        f = self.shm.fetch(S.INPUT, 2)
        self.assertEqual((f.w, f.h, f.source_seq, f.source_ns, f.format), (42, 28, 7, 100, S.FMT_RGBA8))
        self.assertTrue((f.pixels == px).all())
        self.assertIsNone(self.shm.fetch(S.INPUT, 2, 7))
        self.assertIsNone(self.shm.fetch(S.OUTPUT, 2))
        self.assertIsNone(self.shm.fetch(S.INPUT, 1))

    def test_refuses_what_does_not_fit(self):
        self.assertFalse(self.shm.publish(S.INPUT, 0, frame(1, 70, 28), S.FMT_RGBA8, 1, 1))
        self.assertFalse(self.shm.publish(S.INPUT, 9, frame(1), S.FMT_RGBA8, 1, 1))
        self.assertFalse(self.shm.publish(S.INPUT, 0, frame(1), 99, 1, 1))
        self.assertFalse(self.shm.publish(S.INPUT, 0, np.zeros((28, 42), np.float16), S.FMT_RGBA8, 1, 1))

    def test_float_maps(self):
        d = np.linspace(0, 1, 42 * 28, dtype=np.float16).reshape(28, 42)
        self.shm.publish(S.OUTPUT, 0, d, S.FMT_R16F, 3, 5, 9, S.FLAG_SCENE_CUT)
        f = self.shm.fetch(S.OUTPUT, 0)
        self.assertEqual(f.flags, S.FLAG_SCENE_CUT)
        self.assertEqual(f.done_ns, 9)
        self.assertTrue((f.pixels == d).all())

    def test_open_refuses_a_stale_or_foreign_file(self):
        bad = os.path.join(self.dir.name, "bad")
        with open(bad, "wb") as f:
            f.write(b"\0" * 8192)
        with self.assertRaises(ValueError):
            S.DepthShm.open(bad)
        with open(bad, "wb") as f:
            f.write(b"\0" * 10)
        with self.assertRaises(ValueError):
            S.DepthShm.open(bad)

    def test_a_reader_never_sees_a_torn_frame(self):
        # A second process, so the writer really runs during the reader's copy.
        script = (
            "import sys,time\nimport numpy as np\nfrom tools.depth_engine import shm as S\n"
            "s=S.DepthShm.open(sys.argv[1]);end=time.time()+1.5;i=1\n"
            "while time.time()<end:\n"
            "    s.publish(S.OUTPUT,0,np.full((42,56,4),i%251,np.uint8),S.FMT_RGBA8,i,1);i+=1\n")
        writer = subprocess.Popen([sys.executable, "-c", script, self.path], cwd=ROOT)
        try:
            seen = got = 0
            while writer.poll() is None:
                f = self.shm.fetch(S.OUTPUT, 0, seen)
                if f is None:
                    continue
                seen = f.source_seq
                got += 1
                self.assertTrue((f.pixels == seen % 251).all())
        finally:
            writer.wait()
        self.assertEqual(writer.returncode, 0)
        self.assertGreater(got, 100)


class Serving(Base):
    def test_a_frame_comes_back_as_a_map(self):
        self.shm.set_control(1, S.ENABLED)
        self.shm.publish(S.INPUT, 1, frame(30), S.FMT_RGBA8, 5, 123)
        self.assertEqual(self.engine().step(), 1)
        f = self.shm.fetch(S.OUTPUT, 1)
        self.assertEqual((f.w, f.h, f.format, f.source_seq, f.source_ns), (42, 28, S.FMT_R16F, 5, 123))
        self.assertGreaterEqual(f.done_ns, f.source_ns)
        self.assertLess(float(f.pixels[:14].mean()), float(f.pixels[14:].mean()))  # Brighter half is nearer.

    def test_off_by_default(self):
        self.shm.publish(S.INPUT, 0, frame(30), S.FMT_RGBA8, 1, 1)
        self.assertEqual(self.engine().step(), 0)
        self.assertIsNone(self.shm.fetch(S.OUTPUT, 0))

    def test_a_still_desktop_costs_nothing(self):
        self.shm.set_control(0, S.ENABLED)
        e = self.engine()
        for seq in range(1, 6):
            self.shm.publish(S.INPUT, 0, frame(30), S.FMT_RGBA8, seq, seq)
            self.tick()
            e.step()
        self.assertEqual(e.stats.inferred, 1)
        self.assertEqual(e.stats.unchanged, 4)

    def test_a_changed_picture_runs_again(self):
        self.shm.set_control(0, S.ENABLED)
        e = self.engine()
        for seq, v in enumerate((30, 31, 32), 1):
            self.shm.publish(S.INPUT, 0, frame(v), S.FMT_RGBA8, seq, seq)
            self.tick()
            e.step()
        self.assertEqual(e.stats.inferred, 3)

    def test_only_the_newest_frame_is_used(self):
        self.shm.set_control(0, S.ENABLED)
        for seq in (1, 2, 3, 4, 5):
            self.shm.publish(S.INPUT, 0, frame(seq * 20), S.FMT_RGBA8, seq, seq)
        self.engine().step()
        self.assertEqual(self.shm.fetch(S.OUTPUT, 0).source_seq, 5)

    def test_bgra_is_swapped_before_the_model(self):
        seen = []

        class Spy(FakeBackend):
            def infer(self, frames):
                seen.extend(frames)
                return super().infer(frames)
        self.shm.set_control(0, S.ENABLED)
        px = np.zeros((28, 42, 4), np.uint8)
        px[..., 0] = 200  # Blue, in BGRA.
        self.shm.publish(S.INPUT, 0, px, S.FMT_BGRA8, 1, 1)
        self.engine(Spy()).step()
        self.assertEqual(int(seen[0][0, 0, 2]), 200)
        self.assertEqual(int(seen[0][0, 0, 0]), 0)

    def test_frames_are_resized_for_a_fixed_size_model(self):
        class Fixed(FakeBackend):
            size = (28, 14)
        self.shm.set_control(0, S.ENABLED)
        self.shm.publish(S.INPUT, 0, frame(30), S.FMT_RGBA8, 1, 1)
        self.engine(Fixed()).step()
        f = self.shm.fetch(S.OUTPUT, 0)
        self.assertEqual((f.w, f.h), (28, 14))

    def test_desktops_of_different_shapes_are_not_batched_together(self):
        self.shm.set_control(0, S.ENABLED)
        self.shm.set_control(1, S.ENABLED)
        self.shm.publish(S.INPUT, 0, frame(30, 42, 28), S.FMT_RGBA8, 1, 1)
        self.shm.publish(S.INPUT, 1, frame(30, 28, 42), S.FMT_RGBA8, 1, 1)
        self.assertEqual(self.engine().step(), 2)

    def test_channels_are_filtered_apart(self):
        for ch in (0, 1):
            self.shm.set_control(ch, S.ENABLED)
        e = self.engine()
        self.shm.publish(S.INPUT, 0, frame(20), S.FMT_RGBA8, 1, 1)
        self.shm.publish(S.INPUT, 1, frame(200), S.FMT_RGBA8, 1, 1)
        e.step()
        self.assertIsNot(e._filters[0], e._filters[1])

    def test_switching_off_forgets_the_desktop(self):
        self.shm.set_control(0, S.ENABLED)
        e = self.engine()
        self.shm.publish(S.INPUT, 0, frame(30), S.FMT_RGBA8, 1, 1)
        e.step()
        self.shm.set_control(0, 0)
        e.step()
        self.assertNotIn(0, e._filters)
        self.shm.set_control(0, S.ENABLED)
        self.shm.publish(S.INPUT, 0, frame(30), S.FMT_RGBA8, 2, 2)  # Same picture: it must still get a fresh map.
        self.tick()
        self.assertEqual(e.step(), 1)

    def test_heartbeat(self):
        self.assertEqual(self.shm.engine_ns(), 0)
        e = self.engine()
        e.step()
        self.assertEqual(self.shm.engine_ns(), self.clock.t)


class BlackBars(Base):
    """The model reads black bars as the nearest thing on screen (docs/3d-comfort.md), so they never reach it."""
    def picture(self, top=0, side=0):
        px = np.zeros((42, 56, 4), np.uint8)
        px[top:42 - top, side:56 - side, :3] = 120
        px[..., 3] = 255
        return px

    def test_finding_them(self):
        self.assertEqual(picture_box(self.picture(top=6)), (0, 6, 56, 36))
        self.assertEqual(picture_box(self.picture(side=8)), (8, 0, 48, 42))
        self.assertIsNone(picture_box(self.picture()))
        self.assertIsNone(picture_box(np.zeros((42, 56, 4), np.uint8)))  # all black: nothing to crop to

    def test_a_dark_edge_is_not_a_bar(self):
        px = np.full((266, 420, 4), 120, np.uint8)
        px[:3, :, :3] = px[-3:, :, :3] = 0  # 2% of the height: a dark top of the picture, not a letterbox
        self.assertIsNone(picture_box(px))

    def test_the_model_sees_only_the_picture_and_the_bars_are_mid_distance(self):
        seen = []
        class Recording(FakeBackend):
            def infer(inner, frames):
                seen.extend(f.copy() for f in frames)
                return [np.linspace(0, 1, f.shape[0], dtype=np.float32)[:, None].repeat(f.shape[1], 1) for f in frames]
        e = self.engine(Recording())
        self.shm.set_control(0, S.ENABLED)
        self.shm.publish(S.INPUT, 0, self.picture(top=6), S.FMT_RGBA8, 1, 1)
        e.step()
        self.assertEqual(seen[0].shape[:2], (30, 56))
        self.assertTrue((seen[0][..., :3] == 120).all())
        out = self.shm.fetch(S.OUTPUT, 0)
        self.assertEqual((out.w, out.h), (56, 42))
        px = np.asarray(out.pixels, np.float32)
        self.assertAlmostEqual(float(px[0, 0]), float(px[41, 0]), places=3)  # both bars the same depth
        self.assertLess(float(px[7, 0]), float(px[34, 0]))  # the picture keeps its depth

    def test_a_dark_scene_does_not_make_the_crop_flicker(self):
        e = self.engine()
        self.assertEqual(e._stable_box(0, (0, 6, 56, 36)), (0, 6, 56, 36))
        for _ in range(BOX_HOLD - 1):
            self.assertEqual(e._stable_box(0, (0, 10, 56, 32)), (0, 6, 56, 36))
        self.assertEqual(e._stable_box(0, (0, 10, 56, 32)), (0, 10, 56, 32))

    def test_a_fixed_size_model_keeps_its_size(self):
        class Fixed(FakeBackend):
            size = (42, 28)
        e = self.engine(Fixed())
        self.shm.set_control(0, S.ENABLED)
        self.shm.publish(S.INPUT, 0, self.picture(top=6), S.FMT_RGBA8, 1, 1)
        e.step()
        out = self.shm.fetch(S.OUTPUT, 0)
        self.assertEqual((out.w, out.h), (42, 28))


class PacedServing(Base):
    def test_the_rate_is_counted_from_the_start_of_the_work(self):
        # With 6 ms of model time a 60 Hz cap must still give 60 Hz. Counting from the end of the work gave 44.
        clock = self.clock

        class Slow(FakeBackend):
            def infer(self, frames):
                clock.t += 6_000_000
                return super().infer(frames)
        self.shm.set_control(0, S.ENABLED | S.FOCUSED)
        e = Engine(self.shm, Slow(), Scheduler(focused_hz=60, other_hz=30), clock)
        start, done, seq = clock.t, 0, 0
        while clock.t - start < 1_000_000_000:
            seq += 1
            self.shm.publish(S.INPUT, 0, frame(seq % 200), S.FMT_RGBA8, seq, clock.t)
            done += e.step()
            clock.t += 1_000_000
        self.assertGreaterEqual(done, 57)
        self.assertLessEqual(done, 61)


    def test_four_desktops_hold_the_rates_they_were_promised(self):
        # A batch every 16.7 ms for the focused desktop: the others fall due just after one leaves and must not miss it.
        clock = self.clock

        class Slow(FakeBackend):
            def infer(self, frames):
                clock.t += 9_000_000
                return super().infer(frames)
        for ch in range(4):
            self.shm.set_control(ch, S.ENABLED | (S.FOCUSED if ch == 0 else 0))
        e = Engine(self.shm, Slow(), Scheduler(focused_hz=60, other_hz=30), clock)
        start, seq = clock.t, 0
        seen, counts = [0] * 4, [0] * 4
        while clock.t - start < 2_000_000_000:
            seq += 1
            for ch in range(4):
                self.shm.publish(S.INPUT, ch, frame(seq % 200), S.FMT_RGBA8, seq, clock.t)
            e.step()
            for ch in range(4):
                f = self.shm.fetch(S.OUTPUT, ch, seen[ch])
                if f:
                    seen[ch] = f.source_seq
                    counts[ch] += 1
            clock.t += 1_000_000
        self.assertGreaterEqual(counts[0] / 2, 57)
        self.assertGreaterEqual(min(counts[1:]) / 2, 29)


class Pacing(unittest.TestCase):
    def test_most_overdue_first_within_the_batch(self):
        s = Scheduler(focused_hz=60, other_hz=30, batch=2)
        s.ran([0], 0.0)
        s.ran([1], 0.5)
        s.ran([2], 0.9)
        self.assertEqual(s.pick(1.0, [0, 1, 2], set()), [0, 1])

    def test_a_new_desktop_goes_first(self):
        s = Scheduler(batch=1)
        s.ran([0], 0.0)
        self.assertEqual(s.pick(1.0, [0, 1], set()), [1])

    def test_rate_caps(self):
        s = Scheduler(focused_hz=60, other_hz=30)
        s.ran([0, 1], 1.0)
        self.assertEqual(s.pick(1.0 + 1 / 50, [0, 1], {0}), [0])
        self.assertEqual(sorted(s.pick(1.0 + 1 / 25, [0, 1], {0})), [0, 1])

    def test_a_nearly_due_desktop_joins_a_batch_that_is_leaving(self):
        s = Scheduler(focused_hz=60, other_hz=30)
        s.ran([0], 1.0)
        s.ran([1], 0.99)
        self.assertEqual(s.pick(1.018, [0, 1], {0}), [0])           # 5.3 ms early: it waits.
        self.assertEqual(sorted(s.pick(1.02, [0, 1], {0})), [0, 1])  # 3.3 ms early: it comes along.
        self.assertEqual(s.pick(1.02, [1], set()), [])              # But no batch leaves just for it.

    def test_every_desktop_gets_a_turn_under_load(self):
        s = Scheduler(focused_hz=1e6, other_hz=1e6, batch=1)
        counts = dict.fromkeys(range(4), 0)
        t = 0.0
        for _ in range(400):
            for ch in s.pick(t, [0, 1, 2, 3], {0}):
                counts[ch] += 1
                s.ran([ch], t)
            t += 0.01
        self.assertEqual(min(counts.values()), 100)


class Backends(unittest.TestCase):
    def test_an_unknown_backend_is_refused(self):
        with self.assertRaises(BackendUnavailable):
            make_backend("nope", 14, 14)

    def test_resize(self):
        out = resize_rgba(sample.desktop(192, 120), 98, 56)
        self.assertEqual(out.shape, (56, 98, 4))
        px = frame(9)
        self.assertIs(resize_rgba(px, 42, 28), px)


class Samples(unittest.TestCase):
    def test_pictures_are_stable(self):
        self.assertTrue((sample.street(192, 120) == sample.street(192, 120)).all())
        self.assertTrue((sample.desktop(192, 120) == sample.desktop(192, 120)).all())

    def test_size(self):
        self.assertEqual(sample.street(192, 120).shape, (120, 192, 4))
        self.assertEqual(sample.desktop(192, 120).shape, (120, 192, 4))


class Sizes(unittest.TestCase):
    def test_python_rounding_matches_lround(self):
        self.assertEqual(S.round_patch(35), 42)  # 2.5 patches rounds up, as lround does; Python's round() would give 28.


class SupervisedExit(unittest.TestCase):
    def test_failed_attach_exits_cleanly_with_stdin_open(self):
        """A supervised engine that dies before its final os._exit must not abort at interpreter exit: the stdin watcher
        blocked in a buffered read held the reader's lock, and 3.12+ dumped core (SIGABRT) over it."""
        p = subprocess.Popen([sys.executable, "-m", "tools.depth_engine", "serve", "--supervised", "--backend", "fake",
                              "--wait", "0.1", "--desktops", "XR-1", "--shm", "/nonexistent-xr-probe"],
                             cwd=ROOT, stdin=subprocess.PIPE, stdout=subprocess.DEVNULL, stderr=subprocess.PIPE)
        try:  # stdin stays open until the engine has exited, as a live supervisor's pipe would.
            err = p.stderr.read()
            code = p.wait(timeout=20)
        finally:
            p.kill()
            p.stdin.close()
            p.stderr.close()
        self.assertNotIn(b"Fatal Python error", err)
        self.assertGreater(code, 0, err.decode(errors="replace"))

if __name__ == "__main__":
    unittest.main()
