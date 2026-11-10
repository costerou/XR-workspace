"""The VITURE SDK licence is on file and no VITURE binary or header is redistributed.

See docs/pro2/sdk-licence/README.md: we may load the user's installed libglasses.so, but the
licence's redistribution conditions are not met, so nothing of the SDK may be tracked in git
(and so reach the Pro 2 bundle, which copies git-tracked files).
"""
from pathlib import Path
import re
import subprocess
import unittest

ROOT = Path(__file__).resolve().parents[2]
RECORD = ROOT / 'docs/pro2/sdk-licence'
SDK_FILE = re.compile(r'(^|/)(lib(glasses|GlassSDK|carina_vio|RayNeoXRMiniSDK)[^/]*\.so[^/]*'
                      r'|viture_[^/]*\.h|lib/[^/]+/viture/.*)$')


class SdkLicence(unittest.TestCase):
    def test_licence_text_is_filed(self):
        text = (RECORD / 'viture-sdk-license-agreement-2025-09.txt').read_text(encoding='utf-8')
        self.assertIn('Source: https://www.viture.com/viture-sdk-license-agreement', text)
        for clause in ('Effective September 2025', '1. Grant of License', 'distribute the SDK in object code form only',
                       '2. Restrictions on Licensed Rights', '15. General Provisions'):
            self.assertIn(clause, text)
        self.assertTrue(text.rstrip().endswith('whether written or oral.'))
        notice = (RECORD / 'viture-sdk-open-source-notice-2025-09.txt').read_text(encoding='utf-8')
        self.assertIn('XR Glasses SDK', notice)
        record = (RECORD / 'README.md').read_text(encoding='utf-8')
        self.assertIn('Loading `libglasses.so` from our own host is allowed', record)
        self.assertIn('so we do not redistribute', record)

    def test_no_sdk_file_is_tracked(self):
        tracked = subprocess.check_output(['git', 'ls-files', '-z'], cwd=ROOT).decode().split('\0')
        self.assertEqual([path for path in tracked if SDK_FILE.search(path)], [])

    def test_pattern_catches_sdk_files(self):
        for path in ('lib/libglasses.so', 'x/libcarina_vio.so', 'vendor/libGlassSDK.so.2', 'include/viture_device.h',
                     'lib/x86_64/viture/libopencv_core.so'):
            self.assertRegex(path, SDK_FILE)
        for path in ('tools/pro2_runtime/bridge.cpp', 'docs/pro2/diagnostic-evidence/symbols.txt'):
            self.assertNotRegex(path, SDK_FILE)


if __name__ == '__main__':
    unittest.main()
