"""Concrete adapters tested without systemd, Hyprland, or device access."""
import os
from pathlib import Path
import subprocess
import tempfile
import unittest
from unittest.mock import patch
from tools.xr_controller.__main__ import isolated, install_unit, Units

class ServiceTests(unittest.TestCase):
    def test_isolated_launcher_imports_repo(self):
        args=isolated('tools.xr_controller','--help')
        r=subprocess.run(args,capture_output=True,text=True)
        self.assertEqual(r.returncode,0,r.stderr)
    def test_install_unit_does_not_enable_or_start(self):
        with tempfile.TemporaryDirectory() as tmp, patch('tools.xr_controller.__main__.subprocess.run') as run:
            install_unit(Path(tmp))
            text=(Path(tmp)/'systemd/user/viture-workspace-controller.service').read_text()
            self.assertNotIn('@EXEC@',text); self.assertIn('Restart=on-failure',text)
            self.assertEqual(run.call_count,1)
            self.assertEqual(run.call_args.args[0],['systemctl','--user','daemon-reload'])
    def test_stop_uses_main_sigint_for_teardown(self):
        with patch('tools.xr_controller.__main__.subprocess.run') as run:
            Units('/runtime').stop('run')
            self.assertIn('--signal=SIGINT',run.call_args.args[0]); self.assertIn('--kill-whom=main',run.call_args.args[0])
    def test_spawn_is_separate_transient_unit(self):
        from tools.xr_controller.__main__ import Host
        with patch('tools.xr_controller.__main__.read_private_json',return_value={'run_id':'run'}), patch('tools.xr_controller.__main__.subprocess.run') as run:
            Host('/runtime','/state','/data').spawn('start','/state/run.json',None)
            argv=run.call_args.args[0]
            self.assertEqual(argv[:4],['systemd-run','--user','--unit=viture-xr-run','--collect'])
            self.assertIn('--consent',argv); self.assertNotIn('--scope',argv)
    def test_host_uses_probe_result_data(self):
        from tools.xr_controller.__main__ import Host
        from tools.xr_probe.model import ProbeResult
        usb=ProbeResult('ok',[dict(vendor_id='35ca',product='VITURE Beast XR Glasses',product_id='1201')],'')
        drm=ProbeResult('ok',[dict(name='card1-DP-2',status={'value':'connected'})],'')
        with tempfile.TemporaryDirectory() as tmp, patch('tools.xr_controller.__main__.HyprlandClient') as client, patch('tools.xr_controller.__main__.probe_usb',return_value=usb), patch('tools.xr_controller.__main__.probe_drm',return_value=drm), patch('tools.xr_controller.__main__.load_installed',return_value={}):
            client.return_value.query.return_value=[dict(name='DP-2',model='VITURE Beast')]
            client.return_value.plugins_loaded.return_value=[]
            result=Host(tmp,tmp,tmp).probe()
            self.assertEqual(result['model'],'beast'); self.assertEqual(result['output'],'DP-2')
