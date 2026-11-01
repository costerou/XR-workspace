import json
import os
from pathlib import Path
import socket
import tempfile
import unittest
from unittest.mock import patch
from tools.xr_controller.server import Server

class Controller:
    def __init__(self): self.state='idle'
    def snapshot(self): return dict(state=self.state)
    def tick(self): pass
    def request(self,msg): return dict(ok=True,state=self.state)

class ServerTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory(); self.addCleanup(self.tmp.cleanup)
        self.c=Controller(); self.s=Server(self.c,Path(self.tmp.name)/'controller.sock'); self.addCleanup(self.s.close)
    def connect(self):
        c=socket.socket(socket.AF_UNIX); c.connect(str(self.s.path)); c.settimeout(.2); self.addCleanup(c.close); self.s.step(0); return c
    def receive(self,c):
        self.s.step(0); self.s.step(0); return json.loads(c.recv(16384).splitlines()[-1])
    def test_status_survives_client_disconnect(self):
        c=self.connect(); c.close(); self.s.step(0)
        self.c.state='running'; c=self.connect(); c.sendall(b'{"schema":1,"op":"status","request_id":7}\n')
        self.assertEqual(self.receive(c)['state'],'running')
    def test_rejects_other_uid(self):
        with patch('tools.xr_controller.server.check_peer',side_effect=PermissionError):
            c=self.connect(); self.assertEqual(c.recv(10),b'')
    def test_rejects_oversize_and_bad_schema(self):
        for raw in (b'x'*16385,b'{"schema":2,"op":"status"}\n',b'{"schema":true}\n'):
            c=self.connect(); c.sendall(raw); self.s.step(0); self.assertEqual(c.recv(10),b'')
    def test_push_on_state_change(self):
        c=self.connect(); self.c.state='running'
        r=self.receive(c); self.assertEqual(r,dict(schema=1,event='state',state='running'))
    def test_permissions(self):
        self.assertEqual(self.s.path.stat().st_mode & 0o777,0o600)
