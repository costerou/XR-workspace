"""The kernel's binary usbmon tap and the sysfs facts around it.

Reading usbmon sends nothing to any device. Layout and ioctl numbers are from the kernel's
drivers/usb/mon/mon_bin.c and Documentation/usb/usbmon.rst (struct mon_bin_hdr, the 64 byte form).
Neither mon_bin_open nor mon_bin_ioctl checks a capability, so read permission on /dev/usbmonN is all it
takes; /sys/kernel/debug is not needed.
"""
import ctypes
from dataclasses import dataclass
import errno
import fcntl
import getpass
import os
from pathlib import Path
import select
import struct

MON_HEADER = struct.Struct('<QBBBBHbbqiiII8siiII')
GET_ARGS = struct.Struct('<QQQ')     # struct mon_bin_get: hdr pointer, data pointer, alloc
STATS = struct.Struct('<II')         # struct mon_bin_stats: queued, dropped


def _ioc(direction, nr, size):
    return direction << 30 | size << 16 | 0x92 << 8 | nr


MON_IOCG_STATS = _ioc(2, 3, STATS.size)
MON_IOCT_RING_SIZE = _ioc(0, 4, 0)
MON_IOCX_GETX = _ioc(1, 10, GET_ARGS.size)

RING_BYTES = 16 << 20    # BUFF_MAX is 64 MiB today, older kernels stop at 1.2 MiB; see set_ring
BATCH = 4096

VITURE_VID, LUMA_ULTRA_PID, COMPANION_PID = 0x35ca, 0x1104, 0x1102
SYSFS = '/sys/bus/usb/devices'


@dataclass(slots=True)
class Event:
    """One usbmon event, header fields as the kernel names them."""
    id: int
    type: str
    xfer_type: int
    epnum: int
    devnum: int
    busnum: int
    has_setup: bool
    has_data: bool
    ts_ns: int
    status: int
    len_urb: int
    len_cap: int
    setup: bytes
    data: bytes


def parse_event(header, data=b''):
    urb, kind, xfer, ep, dev, bus, flag_setup, flag_data, sec, usec, status, len_urb, len_cap, setup, *_ = \
        MON_HEADER.unpack(header)
    return Event(urb, chr(kind), xfer, ep, dev, bus, flag_setup == 0, flag_data == 0, sec * 10**9 + usec * 1000,
                 status, len_urb, len_cap, setup, data)


class UsbmonSource:
    """Reads one bus's tap. The file is opened read-only and non-blocking."""

    def __init__(self, bus, *, path=None, ioctl=fcntl.ioctl, fetch_bytes=1 << 16):
        """fetch_bytes is the most data copied per event, so a camera frame costs us only its first part."""
        self.path = path or f'/dev/usbmon{bus}'
        self._fetch_bytes = fetch_bytes
        self._ioctl = ioctl
        self._fd = os.open(self.path, os.O_RDONLY | os.O_NONBLOCK | os.O_CLOEXEC)
        self._poll = select.poll()
        self._poll.register(self._fd, select.POLLIN)
        self._header = ctypes.create_string_buffer(MON_HEADER.size)
        self._data = ctypes.create_string_buffer(fetch_bytes)
        self._args = GET_ARGS.pack(ctypes.addressof(self._header), ctypes.addressof(self._data), fetch_bytes)
        self.ring_bytes = self.set_ring(RING_BYTES)

    def set_ring(self, size):
        """Largest kernel buffer on offer, halving down; the kernel default of 300 KiB is the last resort."""
        while size >= 1 << 20:
            try:
                self._ioctl(self._fd, MON_IOCT_RING_SIZE, size)
                return size
            except OSError as error:
                if error.errno not in (errno.EINVAL, errno.ENOMEM):
                    raise
                size //= 2
        return 300 << 10

    def read_batch(self, timeout_s):
        """Whatever has arrived, waiting up to timeout_s for the first event."""
        if not self._poll.poll(timeout_s * 1000):
            return []
        events = []
        while len(events) < BATCH:
            try:
                self._ioctl(self._fd, MON_IOCX_GETX, self._args)
            except InterruptedError:
                continue
            except BlockingIOError:
                break
            event = parse_event(self._header.raw)
            event.data = ctypes.string_at(self._data, min(event.len_cap, self._fetch_bytes))
            events.append(event)
        return events

    def stats(self):
        """(events waiting, events the kernel lost because we fell behind)."""
        raw = bytearray(STATS.size)
        self._ioctl(self._fd, MON_IOCG_STATS, raw, True)
        return STATS.unpack(raw)

    def close(self):
        os.close(self._fd)


def _attribute(device, name):
    try:
        return (device / name).read_text().strip()
    except (OSError, UnicodeDecodeError):
        return None


def scan_devices(root=SYSFS):
    """Every USB device sysfs knows, read from attribute files only: the devices are not opened."""
    devices = []
    for device in sorted(Path(root).glob('*')):
        if ':' in device.name:
            continue
        try:
            devices.append({'bus': int(_attribute(device, 'busnum')), 'dev': int(_attribute(device, 'devnum')),
                            'path': device.name, 'vid': int(_attribute(device, 'idVendor'), 16),
                            'pid': int(_attribute(device, 'idProduct'), 16),
                            'hub': _attribute(device, 'bDeviceClass') == '09',
                            'product': _attribute(device, 'product') or ''})
        except (TypeError, ValueError):
            continue  # it vanished between two reads, which is the very event we are here to watch
    return devices


def is_glasses(device):
    return device['vid'] == VITURE_VID and device['pid'] == LUMA_ULTRA_PID


def is_companion(device):
    """The microphone-and-keys device behind the same hub. Its HID reports may be the buttons."""
    return device['vid'] == VITURE_VID and device['pid'] == COMPANION_PID


def find_glasses(root=SYSFS):
    return next((device for device in scan_devices(root) if is_glasses(device)), None)


def grant_commands(bus, user=None):
    """The one-time sudo steps. They last until the next reboot."""
    user = user or getpass.getuser()
    return ['sudo modprobe usbmon', 'sudo udevadm settle', f'sudo setfacl -m u:{user}:r /dev/usbmon{bus}']


def revoke_commands(bus, user=None):
    user = user or getpass.getuser()
    return [f'sudo setfacl -x u:{user} /dev/usbmon{bus}', 'sudo modprobe -r usbmon']
