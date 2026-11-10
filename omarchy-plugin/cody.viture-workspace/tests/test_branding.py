"""Offline: the product shows as "XR Workspace" (panel window title and logo, bar tooltip, plugin manifest, toasts,
desktop entry, controller unit description, logo files, README and docs headings), while the identifiers installs and
recovery depend on keep their viture-workspace names. VITURE may still name the glasses themselves.

Run from the repository root:
python -m unittest discover -s omarchy-plugin/cody.viture-workspace/tests -p 'test_*.py' -v
"""
import json
from pathlib import Path
import re
import unittest

PLUGIN = Path(__file__).resolve().parents[1]
REPO = PLUGIN.parents[1]
NAME = 'XR Workspace'

# Everything a user reads that once said "VITURE Workspace" (tools/native_spike/adopt.py keeps the old title on purpose:
# see test_the_panel_is_titled_xr_workspace_and_its_old_title_still_stays in tests/native_spike/test_adopt.py).
USER_VISIBLE = [
    PLUGIN / 'Panel.qml', PLUGIN / 'HomePage.qml', PLUGIN / 'PanelHeader.qml', PLUGIN / 'Logo.qml', PLUGIN / 'BarWidget.qml', PLUGIN / 'restart.sh',
    PLUGIN / 'manifest.json', PLUGIN / 'logo-emblem.svg', PLUGIN / 'icon-symbolic.svg',
    REPO / 'packaging' / 'viture-workspace.desktop', REPO / 'packaging' / 'viture-workspace-controller.service',
    REPO / 'assets' / 'logo.svg', REPO / 'assets' / 'icon.svg', REPO / 'README.md', REPO / 'spike' / 'README.md',
    REPO / 'docs' / 'vr-video.md', REPO / 'docs' / 'web-video.md',
    REPO / 'tools' / 'native_spike' / 'send.py', REPO / 'tools' / 'native_spike' / 'xr_control.py',
    REPO / 'tools' / 'native_spike' / 'video.py', REPO / 'tools' / 'native_spike' / 'web_video.py',
]


def desktop_entry():
    entry = {}
    for line in (REPO / 'packaging' / 'viture-workspace.desktop').read_text().splitlines():
        if '=' in line and not line.startswith('#'):
            key, value = line.split('=', 1)
            entry[key] = value
    return entry


def panel_title():
    titles = re.findall(r'^\s*title:"([^"]*)"', (PLUGIN / 'Panel.qml').read_text(), re.M)
    assert len(titles) == 1, titles
    return titles[0]


def emblem_group(svg, opening):
    start = svg.index(opening) + len(opening)
    end = svg.index('\n</g>\n', start)  # the group's own close: nested groups are indented
    return svg[start:end]


class BrandingTests(unittest.TestCase):
    def test_panel_title_desktop_entry_and_adoption_agree(self):
        # Hyprland sees the panel as org.quickshell; the title is how the desktop entry (StartupWMClass, for bar
        # widgets' icon lookup) and adoption (the panel stays on the physical monitor) recognise it.
        from tools.native_spike import adopt
        self.assertEqual(panel_title(), NAME)
        self.assertEqual(desktop_entry()['StartupWMClass'], NAME)
        self.assertEqual(adopt.PANEL_TITLE, NAME)
        self.assertIn('VITURE Workspace', adopt.LEGACY_PANEL_TITLES)

    def test_desktop_entry_names_the_product(self):
        entry = desktop_entry()
        self.assertEqual(entry['Name'], NAME)
        self.assertNotIn('VITURE XR', entry['Comment'])
        self.assertIn('XR', entry['Keywords'].split(';'))

    def test_manifest_name_changes_and_its_id_does_not(self):
        manifest = json.loads((PLUGIN / 'manifest.json').read_text())
        self.assertEqual(manifest['name'], NAME)
        self.assertEqual(manifest['id'], 'cody.viture-workspace')
        self.assertEqual(manifest['id'], PLUGIN.name)
        self.assertEqual((PLUGIN / 'qmldir').read_text().splitlines()[0], 'module cody.viture-workspace')

    def test_no_viture_product_name_where_users_read_it(self):
        for path in USER_VISIBLE:
            text = path.read_text()
            for old in ('VITURE Workspace', 'VITURE XR', 'not a VITURE'):
                self.assertNotIn(old, text, f'{path.relative_to(REPO)} still says {old!r}')
        self.assertIn('"XR Workspace · "', (PLUGIN / 'Model.js').read_text())  # the bar tooltip, Model.barTooltip
        self.assertIn('Model.barTooltip', (PLUGIN / 'BarWidget.qml').read_text())
        self.assertIn("notify-send 'XR Workspace'", (PLUGIN / 'restart.sh').read_text())

    def test_docs_and_readme_headings(self):
        readme = (REPO / 'README.md').read_text()
        self.assertEqual(re.findall(r'^# .*', readme, re.M)[0], '# XR Workspace')
        self.assertIn('alt="XR Workspace logo"', readme)
        technical = (REPO / 'docs' / 'technical.md').read_text()
        self.assertEqual(technical.splitlines()[0], '# XR Workspace: technical reference')
        # The old name is mentioned only as the panel title adoption still accepts.
        self.assertEqual(technical.count('VITURE Workspace'), 1)
        for heading in re.findall(r'^#+ .*', readme + technical, re.M):
            self.assertNotIn('VITURE Workspace', heading)

    def test_controller_unit_description_changes_and_its_name_does_not(self):
        unit = (REPO / 'packaging' / 'viture-workspace-controller.service').read_text()
        self.assertIn('Description=XR Workspace controller', unit)
        # The unit, socket and state names are what installs, the panel and recovery look for: kept.
        self.assertIn('viture-workspace-controller.service', (PLUGIN / 'Panel.qml').read_text())
        self.assertIn('viture-workspace-controller.service', (PLUGIN / 'restart.sh').read_text())
        self.assertIn('/viture-workspace/controller.sock', (PLUGIN / 'ControllerClient.qml').read_text())

    def test_logo_wordmark_reads_xr_workspace(self):
        logo = (REPO / 'assets' / 'logo.svg').read_text()
        self.assertIn(f'<title id="t">{NAME}</title>', logo)
        self.assertEqual(re.findall(r'<text[^>]*>([^<]*)</text>', logo), ['XR', 'Workspace'])
        self.assertNotIn('VITURE', logo)
        self.assertIn(f'<title>{NAME}</title>', (REPO / 'assets' / 'icon.svg').read_text())

    def test_panel_emblem_is_the_logo_emblem(self):
        # logo-emblem.svg is the logo's emblem group copied as logo.svg's comment says (transform dropped), so the
        # panel's mark cannot drift from the README's logo.
        logo = emblem_group((REPO / 'assets' / 'logo.svg').read_text(), '<g id="emblem" transform="translate(6 6)">')
        panel = emblem_group((PLUGIN / 'logo-emblem.svg').read_text(), '<g id="emblem">')
        self.assertEqual(panel, logo)
        self.assertIn('viewBox="0 0 128 128"', (PLUGIN / 'logo-emblem.svg').read_text())
        self.assertIn('source: Qt.resolvedUrl("logo-emblem.svg")', (PLUGIN / 'Logo.qml').read_text())


if __name__ == '__main__':
    unittest.main()


class TrackingInUse(unittest.TestCase):
    """Panel.qml needs the shell's imports, so the QML tests cannot load it: this keeps the line wired."""
    def test_the_panel_shows_which_driver_is_in_use_on_every_page(self):
        text = (PLUGIN / 'Panel.qml').read_text()
        self.assertIn('objectName:"trackingInUse"', text)
        self.assertIn('Model.trackingInUseLine(root.snapshot.tracking_in_use)', text)
