"""Reads `journalctl -k -o short-iso-precise` lines into USB drops.

A disconnect line names a port, never a device, and ports depend on what is plugged in: on 2026-11-03 the Luma
Ultra sat on 5-2.1 for all 50 of its drops, but what enumerated as VITURE Beast (35ca:1201) sat on 5-2.3. So identity is
learned from the enumeration lines, or seeded from sysfs, and is never assumed from a port number.
"""
from dataclasses import dataclass, field
from datetime import datetime
import re

GLASSES_IDS = frozenset({'35ca:1104', '35ca:1201'})  # Luma Ultra; "VITURE Beast XR Glasses", which also appeared on this port on 2026-11-03
HUB_IDS = frozenset({'1a86:8091'})                   # the hub inside the glasses' cable
CLUSTER_S = 1.0                                      # on 54 real drops: a hub and its children went within 0.35 s, the closest two drops were 2.4 s apart

_LINE = re.compile(r'^(\d{4}-\d\d-\d\dT\S+)\s+\S+\s+kernel:\s(.*)$')
_PATH = r'(\d+-\d+(?:\.\d+)*)'
_DISCONNECT = re.compile(rf'^usb {_PATH}: USB disconnect, device number (\d+)')
_NEW = re.compile(rf'^usb {_PATH}: new ([\w-]+) USB device number (\d+)')
_FOUND = re.compile(rf'^usb {_PATH}: New USB device found, idVendor=([0-9a-f]{{4}}), idProduct=([0-9a-f]{{4}})', re.I)
_PRODUCT = re.compile(rf'^usb {_PATH}: Product: (.*)$')
_RESET = re.compile(rf'^usb {_PATH}: reset ([\w-]+) USB device number (\d+)')
_TROUBLE_USB = re.compile(rf'^(?:usb|hub) {_PATH}(?:-port(\d+))?(?::[\d.]+)?: (.*)$')
_TROUBLE_WORDS = re.compile(r"attempt power cycle|can't read configurations|unable to read config|device descriptor read|"
                            r"unable to enumerate|not accepting address|cannot (?:reset|disable)|over-?current|error -\d+", re.I)
_HOST_TROUBLE = re.compile(r'^xhci_hcd \S+: .*(?:died|not responding|WARN|timeout|error)', re.I)


def parse_line(text):
    """(epoch seconds, message) for a kernel journal line, None for anything else ("-- Boot ... --")."""
    found = _LINE.match(text)
    if not found:
        return None
    try:
        return datetime.fromisoformat(found[1]).timestamp(), found[2]
    except ValueError:
        return None


def _within(path, root):
    return path == root or path.startswith(root + '.')


def _parent(path):
    return path.rpartition('.')[0]


@dataclass
class Device:
    id: str = None
    product: str = None
    devnum: int = None


@dataclass
class Member:
    path: str
    devnum: int
    id: str = None
    product: str = None
    under_watch: bool = False  # sat under the watched hub, or under --port


@dataclass
class Cluster:
    t: float
    last: float
    members: list = field(default_factory=list)


class UsbModel:
    def __init__(self, glasses_ids=GLASSES_IDS, hub_ids=HUB_IDS, tree=None, port=None):
        self.glasses_ids, self.hub_ids, self.port = frozenset(glasses_ids), frozenset(hub_ids), port
        self.devices = {path: Device(d.get('id'), d.get('product'), d.get('devnum')) for path, d in (tree or {}).items()}
        self.clusters, self.arrivals, self.anomalies = [], [], []
        self.watched_ports = {path for path, device in self.devices.items() if device.id in self.watched}  # a warning can outlive the hub it concerns

    @property
    def watched(self):
        return self.glasses_ids | self.hub_ids

    def _under_watch(self, path):
        if self.port and _within(path, self.port):
            return True
        return any(_within(path, known) for known in self.watched_ports)

    def _anomaly(self, t, path, text):
        self.anomalies.append({'t': t, 'path': path, 'text': text, 'related': path is None or self._under_watch(path)})

    def _forget(self, path):
        for known in [p for p in self.devices if _within(p, path)]:
            del self.devices[known]

    def _disconnect(self, t, path, devnum):
        under_watch = self._under_watch(path)
        device = self.devices.pop(path, Device())
        member = Member(path, devnum, device.id, device.product, under_watch)
        for cluster in reversed(self.clusters[-4:]):
            near = t - cluster.last <= CLUSTER_S
            if near and any(_within(path, m.path) or _within(m.path, path) or (_parent(path) and _parent(path) == _parent(m.path)) for m in cluster.members):
                cluster.members.append(member)
                cluster.last = t
                return
        self.clusters.append(Cluster(t, t, [member]))

    def line(self, t, message):
        if found := _DISCONNECT.match(message):
            self._disconnect(t, found[1], int(found[2]))
        elif found := _NEW.match(message):
            self._forget(found[1])
            self.devices[found[1]] = Device(devnum=int(found[3]))
        elif found := _FOUND.match(message):
            device = self.devices.setdefault(found[1], Device())
            device.id = f'{found[2]}:{found[3]}'.lower()
            if device.id in self.watched:
                self.watched_ports.add(found[1])
            if device.id in self.glasses_ids:
                self.arrivals.append(t)
        elif found := _PRODUCT.match(message):
            if found[1] in self.devices:
                self.devices[found[1]].product = found[2]
        elif found := _RESET.match(message):
            self._anomaly(t, found[1], message)
        elif (found := _TROUBLE_USB.match(message)) and _TROUBLE_WORDS.search(found[3]):
            self._anomaly(t, f'{found[1]}.{found[2]}' if found[2] else found[1], message)
        elif _HOST_TROUBLE.match(message):
            self._anomaly(t, None, message)

    def classify(self, cluster):
        """drop: the glasses or their hub left. sibling: only the camera or mic under them did.
        unattributed: a port nothing ever identified, which a zero-drop claim cannot wave away. other: anything else."""
        ids = {m.id for m in cluster.members}
        if ids & self.hub_ids:
            return 'drop', 'hub'
        if ids & self.glasses_ids:
            return 'drop', 'glasses'
        if any(m.id is None and m.under_watch for m in cluster.members):   # never identified, on a port the glasses use: zero means zero
            return 'drop', 'port'
        if any(m.under_watch for m in cluster.members):
            return 'sibling', None
        if None in ids:
            return 'unattributed', None
        return 'other', None
