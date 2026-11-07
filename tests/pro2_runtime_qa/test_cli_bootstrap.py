"""Actual packaged shell CLI in child process, external endpoints faked only."""
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import unittest

HERE=Path(__file__).resolve().parent
REPO=HERE.parents[1]


class PackagedBootstrapQA(unittest.TestCase):
    @unittest.skipUnless(os.environ.get('PRO2_QA_CLI_BUNDLE'),'explicit fresh packaged artifact required; standalone CLI acceptance remains mandatory')
    def test_actual_packaged_cli_does_not_create_main_scope_class(self):
        bundle=Path(os.environ['PRO2_QA_CLI_BUNDLE'])
        with tempfile.TemporaryDirectory(prefix='pro2-cli-qa-') as tmp:
            root=Path(tmp);extracted=root/'pro2-runtime-release/runtime';extracted.parent.mkdir()
            shutil.copytree(bundle,extracted)
            shim=root/'shim';shim.mkdir()
            # Execute the exact packaged script; intercept only its python interpreter
            # to install fake external SDK/compositor endpoints before its original -c.
            interpreter=shim/'python3'
            interpreter.write_text('#!/bin/sh\nexec '+sys.executable+' -I '+str(HERE/'cli_bootstrap_hook.py')+' "$@"\n');interpreter.chmod(0o700)
            runtime=root/'runtime';runtime.mkdir(mode=0o700)
            consent=root/'consent.json';consent.write_text(json.dumps(dict(schema=1,gate='pro2-supervised-v1',model='pro2',run_id='cli-qa',instance='offline',output='DP-2',recovery_output='DP-1',seconds=1,projection=44.5,parent_authorized=True,exclusive_topology=True,approximate_optics=True,desktops=[dict(width=1920,height=1080,scale=1)])));consent.chmod(0o600)
            config=root/'config';trace=root/'trace.json';config.write_text(json.dumps(dict(trace=str(trace))))
            env=dict(os.environ,PATH=str(shim)+':'+os.environ['PATH'],XDG_RUNTIME_DIR=str(runtime),XDG_STATE_HOME=str(root/'xdgstate'),PRO2_QA_BOOTSTRAP_CONFIG=str(config))
            result=subprocess.run(['unshare','-Ur','-i','-n','--',str(extracted/'bin/pro2-supervised-test'),'--consent',str(consent),'--state-dir',str(root/'state')],env=env,capture_output=True,text=True,timeout=10)
            records=json.loads(trace.read_text())
            entry=next(row for row in records if row['event']=='managed-entry')
            self.assertNotEqual(entry['scope_module'],'__main__',(result.stdout,records))
            self.assertNotEqual(entry['client_module'],'__main__',(result.stdout,records))
            self.assertNotIn('explicit Pro2 supervised test scope required',result.stdout)
            self.assertIn('provider-close-simulated',[r['event'] for r in records])
            self.assertIn('plugin-unload-spy',[r['event'] for r in records])
