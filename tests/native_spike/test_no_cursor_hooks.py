"""The ownership plugin takes no function hook on a cursor or input call: not on, not behind a switch.

Crash of 2026-11-03 (docs/technical.md "No cursor hooks"): a hook on Render::IHyprRenderer::setCursorSurface took a reference
that its caller, setCursorHidden, then dropped again, and Hyprland died in CInputManager::processMouseRequest on the freed
surface. A hook has to match what the compiler assumed about the real function, which no signature shows, so this class of
bug cannot be tested away one hook at a time. The rule is checked instead, on the source and on the built plugin.
"""
from pathlib import Path
import re
import shutil
import subprocess
import unittest

ROOT = Path(__file__).resolve().parents[2]
PLUGIN = ROOT / 'spike/ownership/plugin.cpp'
# Every symbol the plugin may hook. A new line here is a decision to read that crash first, not a formality.
ALLOWED_HOOKS = {'_ZN6Config19CMonitorRuleManager5clearEv'}
CURSOR_OR_INPUT = re.compile(r'cursor|mouse|pointer|input|seat|surface|keyboard|touch|tablet|gesture', re.I)
FORBIDDEN = ('setCursorFromName', 'setCursorSurface', 'setCursorHidden', 'processMouseRequest', 'CInputManager', 'g_pInputManager',
             'CSeatManager', 'g_pSeatManager', 'SSetCursorEvent', 'm_cursorSurfaceInfo', 'CWLSurface', 'postEvent', 'xrcursor')


def code(text):
    """`text` with C++ comments gone (none of its string literals here hold a //)."""
    return re.sub(r'/\*.*?\*/', '', re.sub(r'//[^\n]*', '', text), flags=re.S)


def hook_targets(text):
    """What `code` hands to hookSymbol, each a literal or the words that stand in for one."""
    calls = [c for c in re.findall(r'\bhookSymbol\(\s*([^;]*?)\)\s*;', text) if 'const std::string' not in c]
    return [m.group(1) if (m := re.match(r'\w+\s*,\s*"([^"]+)"', c)) else c for c in calls]


def violations(text):
    """Each way the plugin source `text` breaks the rule, in words. Empty is clean."""
    src, found = code(text), []
    targets = hook_targets(src)
    found += [f'hook on {t}' for t in targets if t not in ALLOWED_HOOKS]
    found += [f'cursor or input hook {t}' for t in targets if CURSOR_OR_INPUT.search(t)]
    found += [f'names {word}' for word in FORBIDDEN if word in src]
    if src.count('createFunctionHook') != 1: found.append('createFunctionHook outside hookSymbol')
    # A hook switched on from outside is a hook: nothing the plugin reads may pick or enable one.
    found += [f'reads settings through {word}' for word in ('getenv', 'addConfigValue', 'getConfigValue', 'CConfigValue') if word in src]
    return found


class SourceTests(unittest.TestCase):
    def test_the_plugin_keeps_the_rule(self):
        self.assertEqual(violations(PLUGIN.read_text()), [])

    def test_the_only_hook_is_the_allowed_one(self):
        self.assertEqual(set(hook_targets(code(PLUGIN.read_text()))), ALLOWED_HOOKS)
        self.assertFalse(any(CURSOR_OR_INPUT.search(s) for s in ALLOWED_HOOKS))

    def test_the_read_reads_a_string_and_no_surface(self):
        src = code(PLUGIN.read_text())
        body = src[src.index('std::string cursorShape('):]
        body = body[:body.index('\n    }\n')]
        self.assertIn('cursorShapeName(g_pHyprRenderer->m_lastCursorData.name)', body)
        self.assertEqual(re.findall(r'm_lastCursorData\.(\w+)', src), ['name'])
        self.assertNotIn('surf', body.lower())

    def test_the_check_catches_the_hook_that_crashed(self):
        clean = PLUGIN.read_text()
        crashed = clean + ('\nvoid f(HANDLE handle) { cursorSurfaceHook=hookSymbol(handle,"_ZN6Render13IHyprRenderer16setCursorSurfaceE'
                           'N9Hyprutils6Memory14CSharedPointerIN7Desktop4View10CWLSurfaceEEEiib",reinterpret_cast<const void*>(&g)); }\n')
        self.assertTrue(any('setCursorSurface' in v for v in violations(crashed)), violations(crashed))

    def test_the_check_catches_a_hook_on_any_input_call_and_a_computed_target(self):
        clean = PLUGIN.read_text()
        self.assertTrue(violations(clean + '\nvoid f(HANDLE h) { x=hookSymbol(h,"_ZN13CInputManager13onMouseMovedEv",r); }\n'))
        self.assertTrue(violations(clean + '\nvoid f(HANDLE h) { x=hookSymbol(h,name,r); }\n'))
        self.assertTrue(violations(clean + '\nvoid f(HANDLE h) { x=HyprlandAPI::createFunctionHook(h,a,r); }\n'))
        self.assertTrue(violations(clean + '\nconst char* e=getenv("XR_CURSOR_HOOKS");\n'))


class BuiltPluginTests(unittest.TestCase):
    """The binary that gets pinned, when a build is here: what the source says has to be what shipped."""
    def built(self):
        found = [p for d in ('build/release', 'build/native-spike') if (p := ROOT / d / 'libxr-ownership.so').is_file()]
        if not found: self.skipTest('no ownership build here (-DXR_ENABLE_OWNERSHIP_PLUGIN=ON)')
        return found[0]

    def test_no_cursor_hook_target_or_event_in_the_binary(self):
        data = self.built().read_bytes()
        for word in (b'setCursorFromName', b'setCursorSurface', b'xrcursor', b'processMouseRequest'):
            self.assertNotIn(word, data, word)
        self.assertIn(b'_ZN6Config19CMonitorRuleManager5clearEv', data)

    @unittest.skipUnless(shutil.which('nm'), 'needs nm')
    def test_nothing_imported_touches_cursors_or_input(self):
        out = subprocess.run(['nm', '-D', '-C', '--undefined-only', str(self.built())], capture_output=True, text=True, check=True).stdout
        bad = [line for line in out.splitlines() if re.search(r'CInputManager|CSeatManager|setCursor|CWLSurface::', line)]
        self.assertEqual(bad, [])


if __name__ == '__main__':
    unittest.main()
