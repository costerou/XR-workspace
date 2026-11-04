"""Do the installed udev rules give the logged-in user the glasses, and the rule to add if not."""
import os
import re

from .sysfs import GLASSES_PRODUCT, read_text

RULE_FILE = '71-xr-luma.rules'  # must sort before 73-seat-late.rules, the step that acts on uaccess
RULE_TEXT = (
    '# XR Workspace: the logged-in user may reach the VITURE glasses without root.\n'
    'SUBSYSTEM=="usb", ENV{DEVTYPE}=="usb_device", ATTR{idVendor}=="35ca", MODE="0660", TAG+="uaccess"\n'
    'SUBSYSTEM=="hidraw", ATTRS{idVendor}=="35ca", MODE="0660", TAG+="uaccess"\n')
_VENDOR = re.compile(r'(ATTRS?\{idVendor\}|ENV\{ID_VENDOR_ID\})=="35ca"')  # udev compares case-sensitively
_PRODUCT = re.compile(r'(?:ATTRS?\{idProduct\}|ENV\{ID_MODEL_ID\})=="([^"]*)"')
_ADD_ONLY = 'ACTION=="add"'
_GRANT = re.compile(r'\bMODE:?="|\bGROUP:?="|\bOWNER:?="|TAG\+="uaccess"')


def rule_files(roots):
    """Each file name once: the same name in a higher directory hides the rest, as in udev."""
    chosen = {}
    for directory in roots.udev:
        try:
            names = os.listdir(directory)
        except OSError:
            continue
        for name in names:
            if name.endswith('.rules'):
                chosen.setdefault(name, directory / name)
    return [chosen[name] for name in sorted(chosen)]


def scan_rules(roots):
    lines = []
    for path in rule_files(roots):
        text = (read_text(path) or '').replace('\\\n', ' ')
        lines += [line.strip() for line in text.splitlines() if line.strip() and not line.lstrip().startswith('#')]
    return lines


def _names_usb_device(line):
    """A KERNEL== match narrows a usb rule to something else, such as hiddev nodes."""
    return 'SUBSYSTEM=="usb"' in line and 'KERNEL==' not in line


def covers(lines, product_id=GLASSES_PRODUCT):
    """Which routes a rule grants for good: usb is the glasses' /dev/bus/usb node, hidraw any VITURE hidraw node.

    A usb rule limited to ACTION=="add" does not count. Seen after one reboot, 2026-11-03: TAGS=:uaccess:seat:
    but CURRENT_TAGS=:seat: on the glasses, no ACL, and XRLinuxDriver logs "Cannot open device" and segfaults.
    The hidraw rule, with no such limit, kept both. Most likely the `bind` event after `add` clears the tag:
    plugged in while logged in, the ACL is set during `add` and stays, but at boot nobody is logged in yet,
    and by login the tag is gone. Step B1 of live-test-plan.md checks that a rule without the limit survives.
    """
    found = {'usb': False, 'hidraw': False}
    for line in lines:
        if not _VENDOR.search(line) or not _GRANT.search(line):
            continue
        product = _PRODUCT.search(line)
        if _names_usb_device(line) and _ADD_ONLY not in line and (not product or product.group(1) == product_id):
            found['usb'] = True
        if 'SUBSYSTEM=="hidraw"' in line and (not product or product.group(1) == product_id):
            found['hidraw'] = True
    return found


def add_only(lines):
    """An installed usb rule that would work on a hot-plug but not after a reboot."""
    return any(_VENDOR.search(line) and _GRANT.search(line) and _names_usb_device(line) and _ADD_ONLY in line
               for line in lines)


def install_commands():
    """For Cody to run. Replugging is deliberate: it applies the rule with no synthetic events."""
    return (f"sudo tee /etc/udev/rules.d/{RULE_FILE} > /dev/null <<'EOF'\n{RULE_TEXT.strip()}\nEOF\n"
            'sudo udevadm control --reload-rules\n'
            '# Then unplug the glasses, plug them back in, and run this check again.')
