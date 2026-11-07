"""Real owner failure published precisely after the first menu queue drain."""
import json
import threading
import tempfile
from pathlib import Path
import unittest
from tools.native_spike import immersive, view, profiles
from test_immersive import FakeEngine, FakeSbs

class PollCompletionOrderTests(unittest.TestCase):
 def test_terminal_owner_event_and_queued_error_appear_in_same_poll(self):
  entered=threading.Event();release=threading.Event();published=threading.Event()
  class GatedEngine(FakeEngine):
   def start(self,desktops,cancelled=lambda:False):
    entered.set()
    if not release.wait(2):raise AssertionError('engine was not released after first menu drain')
    return super().start(desktops,cancelled)
  owner=immersive.ImmersiveRun(GatedEngine([],fail=immersive.EngineError('authoritative startup failure')),FakeSbs([]))
  real_emit=owner._emit
  def emit(*event):
   real_emit(*event)
   if event[0]=='failed':published.set()
  owner._emit=emit
  directory=tempfile.TemporaryDirectory();self.addCleanup(directory.cleanup)
  c=view.ViewControl(view.ViewState(['XR-1']),'run','session',None,profiles.LayoutStore(Path(directory.name)/'layout.json'),None)
  c.immersive_run=owner
  try:
   c.options_intent('options_open',monitor='XR-1')
   c.options_intent('options_scope',scope='workspace')
   c.options_intent('options_category',category='3d')
   d=c.options.snapshot()
   c.options_intent('options_set',row_id='immersive_all',monitor='XR-1',scope='workspace',revision=d['revision'],value=True)
   self.assertTrue(entered.wait(2));self.assertEqual(c.options.snapshot()['status'],'pending')
   real_tick=c.options.tick
   def tick():
    real_tick() # The result queue is empty at this exact point.
    release.set()
    self.assertTrue(published.wait(2)) # Real callback queued before actual terminal event.
   c.options.tick=tick
   frames=[json.loads(line) for line in c.poll().splitlines()]
   self.assertTrue(c.immersive_failed)
   self.assertEqual(c.options.snapshot()['status'],'error')
   self.assertIn('authoritative startup failure',c.options.snapshot()['message'])
   menu=[f for f in frames if f['op']=='options_menu'][-1]
   self.assertEqual(menu['payload']['status'],'error')
  finally:release.set();owner.close()
