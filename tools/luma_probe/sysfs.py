"""What the kernel already knows about the glasses, read from sysfs, without touching the device.

Every attribute read here is served from memory the kernel filled in at enumeration (`descriptors`,
`report_descriptor`, `speed`, `power/*`): reading them sends nothing down the cable. Device nodes are
only ever looked at from outside with stat, access and getxattr, which never call the driver's open.
"""
from dataclasses import dataclass, field
import os
from pathlib import Path
import re
import stat
import struct

VITURE_VENDOR = '35ca'
GLASSES_PRODUCT = '1104'
TRACKING_DRIVERS = ('xrDriver', 'xr-luma-driver')
TRANSFER_KINDS = ('control', 'isochronous', 'bulk', 'interrupt')
_MAX_ATTRIBUTE = 1 << 16
_ACL_USER = 0x02
_PCI_ABOVE_USB = re.compile(r'/([0-9a-f]{4}:[0-9a-f]{2}:[0-9a-f]{2}\.[0-9a-f])/usb\d+/')


@dataclass(frozen=True)
class Roots:
    sys: Path
    dev: Path
    proc: Path
    udev: tuple  # highest priority first, as udev itself reads them

    @classmethod
    def live(cls):
        return cls(Path('/sys'), Path('/dev'), Path('/proc'),
                   (Path('/etc/udev/rules.d'), Path('/run/udev/rules.d'), Path('/usr/lib/udev/rules.d')))

    @classmethod
    def under(cls, base):
        base = Path(base)
        return cls(base / 'sys', base / 'dev', base / 'proc', (base / 'etc-udev', base / 'run-udev', base / 'lib-udev'))


@dataclass
class Endpoint:
    address: int
    direction: str
    kind: str
    max_packet: int
    interval: int
    period_us: int | None = None


@dataclass
class ParsedInterface:
    number: int
    alt: int
    cls: int
    endpoints: list = field(default_factory=list)


@dataclass
class Node:
    path: str
    exists: bool = False
    mode: str | None = None
    uid: int | None = None
    gid: int | None = None
    acl_users: dict = field(default_factory=dict)
    readable: bool = False
    writable: bool = False


@dataclass
class Interface:
    number: int
    cls: int | None = None
    driver: str | None = None
    endpoints: list = field(default_factory=list)
    hidraw: list = field(default_factory=list)
    hid_report_bytes: int | None = None
    hid_usage_page: int | None = None


@dataclass
class Peer:
    name: str
    vid_pid: str
    product: str | None
    speed: int | None
    power_control: str | None


@dataclass
class Holder:
    pid: int
    comm: str
    exe: str | None = None


@dataclass
class Device:
    name: str
    vid: str
    pid: str
    product: str | None
    manufacturer: str | None
    speed: int | None
    busnum: int | None
    devnum: int | None
    power_control: str | None
    runtime_status: str | None
    controller: str | None
    node: Node
    interfaces: list = field(default_factory=list)
    upstream: list = field(default_factory=list)
    neighbours: list = field(default_factory=list)
    holders: list = field(default_factory=list)


@dataclass
class Scan:
    devices: list
    drivers: list


def read_bytes(path):
    """Contents of a regular file, or None. A link to a device or pipe is refused before it is opened."""
    try:
        if not stat.S_ISREG(os.stat(path).st_mode):
            return None
        fd = os.open(path, os.O_RDONLY | os.O_NONBLOCK | os.O_CLOEXEC)
    except OSError:
        return None
    try:
        return os.read(fd, _MAX_ATTRIBUTE)
    except OSError:
        return None
    finally:
        os.close(fd)


def read_text(path):
    data = read_bytes(path)
    return None if data is None else data.decode(errors='replace').strip()


def _read_int(path, base=10):
    text = read_text(path)
    try:
        return None if text is None else int(text, base)
    except ValueError:
        return None


def parse_descriptors(raw):
    """The interfaces and endpoints in a device's raw descriptors (USB 2.0 spec, chapter 9.6)."""
    interfaces, i = [], 0
    while i + 2 <= len(raw):
        length, kind = raw[i], raw[i + 1]
        if length < 2 or i + length > len(raw):
            break
        body = raw[i:i + length]
        if kind == 4 and length >= 9:
            interfaces.append(ParsedInterface(body[2], body[3], body[5]))
        elif kind == 5 and length >= 7 and interfaces:
            size = struct.unpack_from('<H', body, 4)[0] & 0x7ff  # bits 11-12 are extra transactions
            interfaces[-1].endpoints.append(Endpoint(body[2], 'in' if body[2] & 0x80 else 'out',
                                                     TRANSFER_KINDS[body[3] & 3], size, body[6]))
        i += length
    return interfaces


def poll_period_us(interval, speed):
    """bInterval for an interrupt endpoint: 2^(n-1) microframes at high speed, n milliseconds below it."""
    if interval < 1:
        return None
    return 125 * 2 ** (interval - 1) if (speed or 0) >= 480 else interval * 1000


def parse_acl_users(raw):
    """Named users in a system.posix_acl_access value: {uid: 'rw'}. uaccess grants access this way."""
    users = {}
    if not raw or len(raw) < 4 or (len(raw) - 4) % 8:
        return users
    for offset in range(4, len(raw), 8):
        tag, perm, ident = struct.unpack_from('<HHI', raw, offset)
        if tag == _ACL_USER:
            users[ident] = ('r' if perm & 4 else '') + ('w' if perm & 2 else '')
    return users


def _node(path):
    node = Node(str(path))
    try:
        info = os.stat(path)
    except OSError:
        return node
    node.exists, node.mode = True, f'{stat.S_IMODE(info.st_mode):04o}'
    node.uid, node.gid = info.st_uid, info.st_gid
    try:
        node.acl_users = parse_acl_users(os.getxattr(path, 'system.posix_acl_access'))
    except OSError:
        pass
    node.readable, node.writable = os.access(path, os.R_OK), os.access(path, os.W_OK)
    return node


def _usage_page(report):
    if len(report) >= 2 and report[0] == 0x05:
        return report[1]
    if len(report) >= 3 and report[0] == 0x06:
        return report[1] | report[2] << 8
    return None


def _interfaces(directory, name, speed, roots):
    parsed = parse_descriptors(read_bytes(directory / 'descriptors') or b'')
    found = {}
    for item in parsed:
        interface = found.setdefault(item.number, Interface(item.number, item.cls))
        for endpoint in item.endpoints:
            if all(e.address != endpoint.address for e in interface.endpoints):  # alt settings repeat them
                if endpoint.kind == 'interrupt':
                    endpoint.period_us = poll_period_us(endpoint.interval, speed)
                interface.endpoints.append(endpoint)
    for path in sorted(directory.parent.glob(f'{name}:*')):
        number = _read_int(path / 'bInterfaceNumber', 16)
        if number is None:
            continue
        interface = found.setdefault(number, Interface(number, _read_int(path / 'bInterfaceClass', 16)))
        try:
            interface.driver = os.path.basename(os.readlink(path / 'driver'))
        except OSError:
            pass
        for raw in sorted(path.glob('*/hidraw/hidraw*')):
            interface.hidraw.append(_node(roots.dev / raw.name))
            report = read_bytes(raw.parent.parent / 'report_descriptor') or b''
            interface.hid_report_bytes, interface.hid_usage_page = len(report), _usage_page(report)
    return [found[n] for n in sorted(found)]


def _peer(directory):
    if not directory.is_dir():
        return None
    return Peer(directory.name, f"{read_text(directory / 'idVendor')}:{read_text(directory / 'idProduct')}",
                read_text(directory / 'product'), _read_int(directory / 'speed'), read_text(directory / 'power/control'))


def _upstream(devices, name):
    """Hubs between this device and the root port, nearest first. 5-2.1.3 sits on 5-2.1, then 5-2."""
    chain = []
    while '.' in name:
        name = name.rsplit('.', 1)[0]
        peer = _peer(devices / name)
        if peer:
            chain.append(peer)
    return chain


def _neighbours(devices, name):
    if '.' not in name:
        return []
    parent = name.rsplit('.', 1)[0]
    shape = re.compile(re.escape(parent) + r'\.\d+$')
    return [p for p in (_peer(d) for d in sorted(devices.iterdir()) if shape.match(d.name) and d.name != name) if p]


def _controller(directory):
    match = _PCI_ABOVE_USB.search(os.path.realpath(directory))
    return match.group(1) if match else None


def _device(directory, roots):
    devices = directory.parent
    name = directory.name
    speed, bus, num = (_read_int(directory / k) for k in ('speed', 'busnum', 'devnum'))
    node = roots.dev / f'bus/usb/{bus or 0:03d}/{num or 0:03d}'
    return Device(name, read_text(directory / 'idVendor'), read_text(directory / 'idProduct'),
                  read_text(directory / 'product'), read_text(directory / 'manufacturer'), speed, bus, num,
                  read_text(directory / 'power/control'), read_text(directory / 'power/runtime_status'),
                  _controller(directory), _node(node), _interfaces(directory, name, speed, roots),
                  _upstream(devices, name), _neighbours(devices, name))


def _state(directory):
    text = read_text(directory / 'stat') or ''
    after = text.rsplit(')', 1)[-1].split()  # the name in brackets may itself hold spaces or brackets
    return after[0] if after else None


def _exe(directory):
    try:
        return os.readlink(directory / 'exe').removesuffix(' (deleted)')
    except OSError:  # not ours to read
        return None


def _is_driver(comm, exe):
    """A script can take a driver's name (test fakes do), so the binary decides when it can be read."""
    return comm in TRACKING_DRIVERS and (exe is None or os.path.basename(exe) == comm)


def _processes(proc):
    try:
        names = os.listdir(proc)
    except OSError:
        return
    for name in names:
        if name.isdigit():
            comm = read_text(proc / name / 'comm')
            if comm is not None and _state(proc / name) != 'Z':
                yield int(name), comm, _exe(proc / name)


def _open_paths(proc, pid):
    try:
        fds = os.listdir(proc / str(pid) / 'fd')
    except OSError:  # someone else's process, or gone
        return set()
    paths = set()
    for fd in fds:
        try:
            paths.add(os.readlink(proc / str(pid) / 'fd' / fd))
        except OSError:
            pass
    return paths


def scan(roots):
    devices_dir = roots.sys / 'bus/usb/devices'
    try:
        entries = sorted(devices_dir.iterdir())
    except OSError:
        entries = []
    devices = [_device(d, roots) for d in entries if read_text(d / 'idVendor') == VITURE_VENDOR]
    processes = list(_processes(roots.proc))
    drivers = [Holder(pid, comm, exe) for pid, comm, exe in processes if _is_driver(comm, exe)]
    for device in devices:
        nodes = {device.node.path, *(n.path for i in device.interfaces for n in i.hidraw)}
        device.holders = [Holder(pid, comm, exe) for pid, comm, exe in processes if _open_paths(roots.proc, pid) & nodes]
    return Scan(devices, drivers)
