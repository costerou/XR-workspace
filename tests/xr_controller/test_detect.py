import unittest
from tools.xr_controller.detect import identify, pick_fallback

class DetectionTests(unittest.TestCase):
    def test_devices(self):
        for product, pid, model, edid in [('VITURE Beast XR Glasses','1201','beast','VITURE Beast'), ('VITURE Luma Ultra XR GLASSES','1104','luma-ultra','VITURE')]:
            usb = [dict(vendor_id='35ca',product_id=pid,product=product)]
            self.assertEqual(identify(usb, [], [dict(name='DP-2',model=edid)])['model'],model)
            self.assertEqual(identify(usb, [], [dict(name='DP-2',model=edid)])['output'],'DP-2')
            self.assertIsNone(identify(usb, [], [dict(name='DP-2',model=edid),dict(name='DP-3',model=edid)])['output'])
            usb[0]['product_id']='ffff'
            self.assertEqual(identify(usb, [], [])['model'],'unknown')
    def test_microphone(self):
        self.assertIsNone(identify([dict(vendor_id='35ca',product_id='1102')],[],[])['model'])
    def test_fallback(self):
        monitors=[dict(name='DP-2'),dict(name='DP-1')]
        self.assertEqual(pick_fallback(monitors,'DP-2',None),'DP-1')
        self.assertIsNone(pick_fallback(monitors[:1],'DP-2',None))
        monitors.append(dict(name='HDMI-A-1'))
        self.assertEqual(pick_fallback(monitors,'DP-2',None),'choose')
        self.assertEqual(pick_fallback(monitors,'DP-2','DP-1'),'DP-1')
