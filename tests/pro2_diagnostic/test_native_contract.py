"""Native reader/publisher tests: synthetic/private replay, isolated IPC, no GPU/SDK."""
import json
import math
import os
from pathlib import Path
import struct
import subprocess
import tempfile
import unittest
from tools.pro2_diagnostic.adapter import ReplayAdapter

ROOT=Path(__file__).resolve().parents[2]
class NativeContractTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.addCleanup(self.tmp.cleanup)
        self.binary=Path(self.tmp.name)/'native-contract'
        subprocess.run(['c++','-std=c++20','-Wall','-Wextra','-Werror','-pthread',
            '-I'+str(ROOT/'spike/include'),'-I'+str(ROOT/'spike/driver/include'),
            str(Path(__file__).with_name('native_contract.cpp')),str(ROOT/'spike/src/pose.cpp'),str(ROOT/'spike/driver/src/rt.cpp'),
            str(ROOT/'spike/driver/src/publish.cpp'),str(ROOT/'spike/driver/src/predict.cpp'),str(ROOT/'spike/driver/src/vsync.cpp'),'-o',str(self.binary)],check=True)
    def run_rows(self,rows):
        adapter=ReplayAdapter(replay_only=True);data=bytearray();first=rows[0]['host_monotonic_ns']
        try:
            for r in rows:
                packet=adapter.push(r['sdk_values'],r['timestamp_raw'],r['host_monotonic_ns'])
                data.extend(struct.pack('<16fd',*packet,(r['host_monotonic_ns']-first)/1e6))
        finally:adapter.close()
        result=subprocess.run(['unshare','-Ur','-i','--',str(self.binary)],input=data,capture_output=True)
        self.assertEqual(result.returncode,0,result.stderr.decode())
        summary=json.loads(result.stdout);self.assertEqual(summary['native_samples'],len(rows))
        self.assertTrue(summary['unknown_fov_unavailable']);self.assertTrue(summary['private_teardown'])
    def test_synthetic_all_axes(self):
        rows=[]
        for i in range(240):
            axis=i//40%3;a=.2*math.sin(i/20);v=[0,0,0];v[axis]=math.sin(a)
            rows.append(dict(sdk_values=[0,0,0,math.cos(a)]+v,timestamp_raw=i*16666667,host_monotonic_ns=i*16666667))
        self.run_rows(rows)
    @unittest.skipUnless(os.environ.get('PRO2_PRIVATE_REPLAY'),'private replay opt-in')
    def test_private_capture_native_reader(self):
        rows=[json.loads(l) for l in Path(os.environ['PRO2_PRIVATE_REPLAY']).read_text().splitlines()]
        self.run_rows([r for r in rows if r.get('kind')=='pose'])
