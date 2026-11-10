"""The run that stopped at about 60 s: the guard reads a health file another process replaces 50 times a second."""
import json
import os
from pathlib import Path
import signal
import tempfile
import threading
import time
import unittest
from unittest.mock import patch
from tools.pro2_runtime import launch
from tools.pro2_runtime.scope import HEALTH_MAX_AGE, TestScope, read_health


def session(directory):
    s=launch.RuntimeSession.__new__(launch.RuntimeSession)
    s.directory=Path(directory);s.health_path=s.directory/'health.json';s.nonce='n'
    return s


class Race(unittest.TestCase):
    def test_a_reader_racing_the_replace_never_fails(self):
        """Hammered, this raised 'single-link' about once in 11568 reads, so three seconds of it is thousands of chances."""
        with tempfile.TemporaryDirectory() as d:
            s=session(d);s.write_health(True)
            child=os.fork()
            if child==0:
                try:
                    while True:s.write_health(True);time.sleep(.0002)
                finally:os._exit(0)
            try:
                scope=TestScope('/p',44.5,str(s.health_path),'n');end=time.monotonic()+3;reads=0;errors={}
                while time.monotonic()<end:
                    try:scope.healthy()
                    except Exception as exc:errors[str(exc)]=errors.get(str(exc),0)+1
                    reads+=1
            finally:os.kill(child,signal.SIGKILL);os.waitpid(child,0)
            self.assertGreater(reads,10000);self.assertEqual(errors,{})

    def test_the_reader_still_refuses_what_is_not_the_health_file(self):
        with tempfile.TemporaryDirectory() as d:
            p=Path(d)/'h';p.write_text('{}');p.chmod(0o644)
            with self.assertRaises(PermissionError):read_health(p)
            p.chmod(0o600);os.link(p,Path(d)/'second')
            with self.assertRaises(PermissionError):read_health(p)
            q=Path(d)/'link';q.symlink_to(p)
            with self.assertRaises(OSError):read_health(q)


class Reasons(unittest.TestCase):
    def test_a_failed_provider_says_why(self):
        with tempfile.TemporaryDirectory() as d:
            s=session(d);s.write_health(False,'competing provider during bounded test');s.health_path.chmod(0o600)
            with self.assertRaisesRegex(ValueError,'competing provider'):TestScope('/p',44.5,str(s.health_path),'n').healthy()
    def test_the_age_limit(self):
        with tempfile.TemporaryDirectory() as d:
            s=session(d);p=s.health_path
            p.write_text(json.dumps(dict(nonce='n',ready=True,monotonic=time.monotonic()-HEALTH_MAX_AGE+.5)));p.chmod(0o600)
            TestScope('/p',44.5,str(p),'n').healthy()
            p.write_text(json.dumps(dict(nonce='n',ready=True,monotonic=time.monotonic()-HEALTH_MAX_AGE-.1)))
            with self.assertRaisesRegex(ValueError,'age'):TestScope('/p',44.5,str(p),'n').healthy()
    def test_a_slow_scan_does_not_stop_the_heartbeat(self):
        with tempfile.TemporaryDirectory() as d:
            s=session(d);s.lock=threading.RLock();s.stop_monitor=threading.Event();s.failure=None;s.ready=True;s.done=None;s.started=None;s.status=None
            class Child:
                pid=1
                def poll(self):return None
            s.child=Child();s.write_health(True)
            ticks=[];real=s.write_health
            s.write_health=lambda *a:(ticks.append(time.monotonic()),real(*a))
            def slow(exclude=()):time.sleep(.8);return False
            with patch.object(launch,'competing',slow),patch.object(launch,'COMPETING_EVERY_S',.01):
                beat=threading.Thread(target=s.monitor,daemon=True);scan=threading.Thread(target=s.scan,daemon=True);beat.start();scan.start()
                time.sleep(1.2);s.stop_monitor.set();beat.join(2);scan.join(2)
            self.assertLess(max(b-a for a,b in zip(ticks,ticks[1:])),.3)
    def test_the_guard_worker_names_what_failed(self):
        from tools.native_spike import supervisor
        import contextlib,io
        class Pipe:
            sent=[]
            def __enter__(self):return self
            def __exit__(self,*a):return False
            def send_bytes(self,b):self.sent.append(b)
        def guard():raise ValueError('approved usable physical output missing')
        err=io.StringIO()
        with contextlib.redirect_stderr(err),patch('signal.signal'):supervisor._guard_worker(guard,Pipe())
        self.assertEqual(Pipe.sent,[b'F']);self.assertIn('run guard: ValueError: approved usable physical output missing',err.getvalue())
    def test_the_result_carries_the_failure(self):
        from tools.xr_controller.pro2_test import provider_summary
        class P:done=dict(samples=9,seconds=1.0);failure='x'
        self.assertEqual(provider_summary(P())['failure'],'x')
        class Q:done=dict(samples=9,error='');failure=None
        self.assertEqual(provider_summary(Q()),dict(samples=9))
    def test_the_result_names_the_bridges_error(self):
        from tools.xr_controller.pro2_test import provider_summary
        class P:done=dict(samples=9,error='stale');failure='Pro2 test provider no longer ready'
        self.assertEqual(provider_summary(P()),dict(samples=9,error='stale',failure='Pro2 test provider no longer ready'))
