"""Turns a scan into a verdict: can our driver be tried right now, and if not, what is in the way?"""
from dataclasses import dataclass, field

from .sysfs import GLASSES_PRODUCT
from .udev import add_only, covers, install_commands, scan_rules


@dataclass
class Finding:
    level: str  # blocker, action, warning, info
    code: str
    text: str


@dataclass
class Verdict:
    status: str  # ready, action, busy, absent
    exit_code: int
    findings: list = field(default_factory=list)
    udev_commands: str | None = None


def _glasses(scan):
    return next((d for d in scan.devices if d.pid == GLASSES_PRODUCT), None)


def assess(scan, roots):
    glasses = _glasses(scan)
    if glasses is None:
        return Verdict('absent', 2, [Finding('blocker', 'absent', 'The glasses (35ca:1104) are not on USB.')])
    lines = scan_rules(roots)
    findings, rules = [], covers(lines)
    offer_rule = False
    usable = glasses.node.exists and glasses.node.readable and glasses.node.writable

    for process in scan.drivers:
        findings.append(Finding('blocker', 'driver-running', f'{process.comm} is running (pid {process.pid}, {process.exe or "binary unknown"}).'))
    for interface in glasses.interfaces:
        if interface.driver == 'usbfs':
            findings.append(Finding('blocker', 'interface-claimed',
                                    f'Interface {interface.number} is claimed by a program (usbfs).'))
    for device in scan.devices:
        for holder in device.holders:
            findings.append(Finding('blocker', 'node-held',
                                    f'{holder.comm} (pid {holder.pid}) has {device.name} ({device.pid}) open.'))

    if not usable:
        findings.append(Finding('action', 'usbfs-no-access',
                                f'{glasses.node.path} needs read and write for this user: '
                                f'{"it does not exist" if not glasses.node.exists else "mode " + glasses.node.mode}.'))
        if rules['usb']:
            findings.append(Finding('action', 'rule-not-applied',
                                    'A udev rule already covers this node. Replug the glasses, and check this is a '
                                    'local login: uaccess only serves the active seat.'))
        else:
            offer_rule = True
    if not rules['usb'] and add_only(lines):
        offer_rule = True
        findings.append(Finding('warning' if usable else 'action', 'rule-add-only',
                                'The installed usb rule has ACTION=="add", so access can be lost after a reboot with the '
                                'glasses plugged in, until they are replugged. ' + ('It works now because they were plugged '
                                'in after login.' if usable else 'That is why there is no access now.')))

    hidraw = [(d, n) for d in scan.devices for i in d.interfaces for n in i.hidraw]
    for device, node in hidraw:
        if not (node.readable and node.writable):
            findings.append(Finding('info', 'hidraw-no-access', f'{node.path} ({device.pid}) is not read/write for this user.'))
            offer_rule = offer_rule or not rules['hidraw']

    if not any(i.hidraw for i in glasses.interfaces):
        findings.append(Finding('info', 'usbfs-route', '35ca:1104 has no hidraw node: interface 0 is class 00 '
                                'with no kernel driver, so that device is reached through usbfs (/dev/bus/usb) only.'))
    for interface in glasses.interfaces:
        for endpoint in interface.endpoints:
            if endpoint.direction == 'in' and endpoint.kind == 'interrupt' and endpoint.period_us == 1000:
                findings.append(Finding('info', 'imu-endpoint-hint',
                                        f'Endpoint 0x{endpoint.address:02x} polls every 1 ms, which fits a 1 kHz IMU '
                                        'stream. This is a guess from the descriptor, unproven.'))

    if (glasses.speed or 0) < 480:
        findings.append(Finding('warning', 'slow-link', f'The link runs at {glasses.speed} Mbit/s, not 480: check the cable.'))
    for peer in (glasses, *glasses.upstream):
        if peer.power_control == 'auto':
            findings.append(Finding('warning', 'autosuspend', f'{peer.name} may autosuspend (power/control is auto).'))

    levels = {f.level for f in findings}
    status, code = ('busy', 1) if 'blocker' in levels else ('action', 1) if 'action' in levels else ('ready', 0)
    return Verdict(status, code, findings, install_commands() if offer_rule else None)


def _endpoint_line(e):
    period = f' every {e.period_us} us' if e.period_us else ''
    return f'      0x{e.address:02x} {e.direction} {e.kind} {e.max_packet} B{period}'


def _device_lines(device):
    lines = [f'{device.name}  {device.vid}:{device.pid}  {device.product or "?"}  {device.speed} Mbit/s',
             f'  usbfs node {device.node.path}  mode {device.node.mode}  '
             f'read {"yes" if device.node.readable else "no"}, write {"yes" if device.node.writable else "no"}'
             + (f'  acl users {device.node.acl_users}' if device.node.acl_users else ''),
             f'  power/control {device.power_control}, runtime {device.runtime_status}'
             + (f', controller {device.controller}' if device.controller else '')]
    for peer in device.upstream:
        lines.append(f'  behind {peer.name} {peer.vid_pid} {peer.product or "?"} power/control {peer.power_control}')
    for peer in device.neighbours:
        lines.append(f'  next to {peer.name} {peer.vid_pid} {peer.product or "?"}')
    for interface in device.interfaces:
        cls = '?' if interface.cls is None else f'{interface.cls:02x}'
        lines.append(f'  interface {interface.number} class {cls} driver {interface.driver or "none"}')
        for node in interface.hidraw:
            lines.append(f'      hidraw {node.path} mode {node.mode} read {"yes" if node.readable else "no"}, '
                         f'write {"yes" if node.writable else "no"}, report {interface.hid_report_bytes} B, '
                         f'usage page 0x{interface.hid_usage_page or 0:02x}')
        lines += [_endpoint_line(e) for e in interface.endpoints]
    return lines


def render(scan, verdict):
    if verdict.status == 'absent':
        extra = [f'Other VITURE devices are present: {", ".join(d.pid for d in scan.devices)}.'] if scan.devices else []
        return '\n'.join(['No VITURE Luma Ultra (35ca:1104) found on USB.', *extra])
    headline = {'ready': 'READY: nothing is in the way of opening the glasses.',
                'action': 'ACTION NEEDED before the glasses can be opened.',
                'busy': 'BUSY: something already has the glasses. Do not start a second driver.'}[verdict.status]
    lines = [headline, '']
    for device in scan.devices:
        lines += _device_lines(device) + ['']
    for level in ('blocker', 'action', 'warning', 'info'):
        lines += [f'{level}: {f.text}' for f in verdict.findings if f.level == level]
    if verdict.udev_commands:
        lines += ['', 'To install the udev rule, Cody runs:', '', verdict.udev_commands]
    return '\n'.join(lines)
