import threading
import unittest
from tools.native_spike.options_menu import OptionsMenu
from tools.native_spike.view import ViewControl, ViewState

class OwnerTests(unittest.TestCase):
    def menu(self):
        c=ViewControl(ViewState(['XR-1','XR-3']),'run','session',None,None,None)
        value=['off']
        def rows(*a): return [dict(id='privacy',label='Privacy',scope='workspace',kind='action',value=value[0],enabled=True)]
        m=OptionsMenu(c,rows=rows);c.options=m;m.open('XR-3');m.intent('options_scope',scope='workspace')
        return c,m
    def test_worker_only_enqueues_result_and_loop_applies_failure(self):
        c,m=self.menu();token=m.begin_owner('privacy','XR-3','workspace')
        t=threading.Thread(target=lambda:m.finish_owner(token,False,'DPMS query failed'));t.start();t.join()
        self.assertEqual(m.snapshot()['status'],'pending')
        m.tick()
        self.assertEqual(m.snapshot()['status'],'error')
        self.assertEqual(m.snapshot()['message'],'DPMS query failed')
    def test_child_and_category_do_not_drop_owner_failure(self):
        c,m=self.menu();token=m.begin_owner('privacy','XR-3','workspace')
        m.close('native child');m.finish_owner(token,False,'rollback failed');m.tick()
        self.assertFalse(m.snapshot()['open']);self.assertEqual(m.snapshot()['status'],'error')
    def test_stale_results_ignored_on_new_request_namespace_removal_and_stop(self):
        for reason in ('replacement','namespace','removed','stop','renderer restart'):
            with self.subTest(reason=reason):
                c,m=self.menu();token=m.begin_owner('privacy','XR-3','workspace')
                if reason=='replacement': m.begin_owner('privacy','XR-3','workspace')
                elif reason=='namespace': c.session='new'
                elif reason=='removed': c.state.names.remove('XR-3')
                else:m.close(reason)
                m.finish_owner(token,True,'done');m.tick()
                self.assertNotEqual(m.snapshot()['status'],'applied')
    def test_completion_queue_is_bounded_and_detail_utf8(self):
        c,m=self.menu();token=m.begin_owner('privacy','XR-3','workspace')
        self.assertTrue(m.finish_owner(token,False,'é'*300))
        for _ in range(1000): self.assertFalse(m.finish_owner(token,True,'duplicate'))
        m.tick();self.assertLessEqual(len(m.snapshot()['message'].encode()),256)
    def test_receipt_alone_does_not_complete_an_action(self):
        c,m=self.menu()
        m._apply=lambda *a:b'{"op":"view","request_id":42}\n'
        d=m.snapshot();m.intent('options_activate',row_id='privacy',monitor='XR-3',scope='workspace',revision=d['revision'],value='off')
        m.acknowledge(42);m.refresh()
        self.assertEqual(m.snapshot()['status'],'pending')
    def test_category_change_preserves_pending_and_failure(self):
        c,m=self.menu();token=m.begin_owner('privacy','XR-3','workspace')
        m.intent('options_category',category='help')
        self.assertEqual(m.snapshot()['status'],'pending')
        m.finish_owner(token,False,'actual backend error');m.tick()
        self.assertEqual(m.snapshot()['message'],'actual backend error')
    def test_keybind_child_hides_parent_and_resumes_without_losing_error(self):
        import json
        from types import SimpleNamespace
        c,m=self.menu();c._options_frame();c.keys=SimpleNamespace(open=True)
        c.options_editor=SimpleNamespace(kind='keybinds',close=lambda:b'')
        token=m.begin_owner('privacy','XR-3','workspace')
        self.assertFalse(json.loads(c._options_frame())['payload']['open'])
        self.assertFalse(m.keyboard_owned)
        c.keys.open=False
        m.finish_owner(token,False,'owner failed');m.refresh()
        frame=json.loads(c._options_frame())['payload']
        self.assertTrue(frame['open']);self.assertEqual(frame['status'],'error')
    def test_native_refusal_finishes_current_request_as_error(self):
        c,m=self.menu();token=m.begin_owner('privacy','XR-3','workspace')
        m.pending_ids={42};m.refuse(42,'view','native refused setting');m.tick()
        self.assertEqual(m.snapshot()['status'],'error')
        self.assertEqual(m.snapshot()['message'],'native refused setting')
    def test_backend_completion_waits_for_native_receipt_and_keeps_error_on_child(self):
        c,m=self.menu();tokens=[]
        def apply(*a):
            token=m.begin_owner('privacy','XR-3','workspace');tokens.append(token)
            m.finish_owner(token,True,'verified backend')
            return b'{"op":"view","request_id":82}\n'
        m._apply=apply;d=m.snapshot()
        m.intent('options_activate',row_id='privacy',monitor='XR-3',scope='workspace',revision=d['revision'],value='off')
        self.assertEqual(m.snapshot()['status'],'pending')
        m.acknowledge(82);m.tick()
        self.assertEqual(m.snapshot()['status'],'applied')
    def test_child_open_does_not_erase_existing_pending_owner(self):
        from types import SimpleNamespace
        c,m=self.menu();token=m.begin_owner('privacy','XR-3','workspace')
        c.options_editor=SimpleNamespace(kind='url',text_field='editor.text',rows=lambda:[dict(id='editor.text',label='URL',kind='text',scope='workspace',value='',enabled=True)])
        m.refresh();self.assertEqual(m.snapshot()['status'],'pending')
        m.finish_owner(token,False,'DPMS refused');m.tick()
        self.assertEqual(m.snapshot()['status'],'error')
