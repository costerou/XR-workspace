"""Require the native production geometry fixture, never an injected hit mesh."""
import importlib.util
import io
import os
from pathlib import Path
import unittest
from tools.native_spike import view


def test_actual_native_serializer_to_sampler_press_release():
    assert os.environ.get('XR_RESTORE_GEOMETRY_BINARY'), 'Build CPU restore_band_test and set XR_RESTORE_GEOMETRY_BINARY'
    path=Path(view.__file__).resolve().parents[2]/'tests/native_spike/test_native_restore_acquisition.py'
    assert path.is_file(), 'Integrate supervisor native-geometry acquisition test before release'
    spec=importlib.util.spec_from_file_location('_xr_native_restore_acceptance',path)
    module=importlib.util.module_from_spec(spec);spec.loader.exec_module(module)
    suite=unittest.defaultTestLoader.loadTestsFromTestCase(module.NativeRestoreTests)
    stream=io.StringIO();result=unittest.TextTestRunner(stream=stream,verbosity=2).run(suite)
    assert result.testsRun>0 and not result.skipped and result.wasSuccessful(),stream.getvalue()
