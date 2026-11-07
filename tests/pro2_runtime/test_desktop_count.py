"""The first live test's consent asked for one desktop and the run ended with XR-1 and XR-2. Where the second came from."""
import unittest
from unittest.mock import MagicMock,patch
from tools.native_spike import desktops as dk, managed, view
from tools.native_spike.hyprland import plan_outputs
from tools.native_spike.ownership import OwnershipBrokerClient
from tools.pro2_runtime.scope import TestScope

ONE=[dict(width=1920,height=1080,scale=1)]


class Origin(unittest.TestCase):
    def test_the_start_creates_exactly_the_consented_desktops(self):
        self.assertEqual([s['name'] for s in plan_outputs([],[],'run',ONE)],['XR-1'])

    def test_add_is_the_one_way_to_a_second_desktop(self):
        specs=plan_outputs([],[],'run',ONE)
        change=dk.plan(specs,[dict(x=0,y=0,distance=50,curvature=0)],'add',0,held=())
        self.assertEqual([s['name'] for s in change.specs],['XR-1','XR-2'])
        self.assertEqual(change.specs[1]['width'],1920)  # the size of the desktop whose pill it is, as the run showed


class Lock(unittest.TestCase):
    def control(self):
        state=view.ViewState(['XR-1'],spacing=40);state.menu_open=True;state.menu_desktop=0
        c=view.ViewControl(state,'run','session',lambda n:None,None,None,clock=lambda:100.0)
        c.notify=told=MagicMock();c.fixed_desktops=True;c.desktops=MagicMock()
        return c,told

    def manager_for(self,scope):
        """The real _run_owned up to its view controls: which DesktopManager the run would hand the panel."""
        class Stop(Exception):pass
        seen=[]
        def controls(client,run_id,session,capture,output,store,fallback,manager,*a,**k):
            seen.append(manager);raise Stop
        journal=MagicMock(operations=[],broker=None,run_id='run',session='s')
        journal.transaction.return_value=MagicMock(__enter__=lambda *_:None,__exit__=lambda *_:None)
        broker=MagicMock(spec=OwnershipBrokerClient);broker.epoch='e';broker.fallback='f'
        specs=plan_outputs([],[],'run',ONE)
        with patch.object(managed,'view_controls',controls),patch.object(managed,'create_outputs'),patch.object(managed,'ManagedGuard',MagicMock()),\
                patch.object(managed,'recover_restoring',return_value=MagicMock()),self.assertRaises(Stop):
            managed._run_owned(MagicMock(instance='i'),journal,broker,'DP-2','DP-1',specs,capture=['XR-1'],**({'pro2_scope':scope} if scope else {}))
        return seen[0]

    def test_a_supervised_test_gets_no_desktop_manager(self):
        scope=MagicMock(spec=TestScope,fixed_desktops=True)
        self.assertIsNone(self.manager_for(scope))

    def test_a_panel_session_and_an_ordinary_run_keep_theirs(self):
        self.assertIsInstance(self.manager_for(MagicMock(spec=TestScope,fixed_desktops=False)),dk.DesktopManager)
        self.assertIsInstance(self.manager_for(None),dk.DesktopManager)

    def test_add_remove_and_reshape_are_refused_and_said(self):
        for action in ('add','remove','ultrawide','scale','resolution'):
            with self.subTest(action=action):
                c,told=self.control()
                c.desktop_action(0,action)
                c.desktops.start.assert_not_called()
                self.assertFalse(c.state.desktops_busy)
                for t in c._threads:t.join()
                told.assert_called_once()

    def test_an_ordinary_run_is_not_locked(self):
        state=view.ViewState(['XR-1'],spacing=40)
        self.assertFalse(view.ViewControl(state,'run','session',lambda n:None,None,None).fixed_desktops)
