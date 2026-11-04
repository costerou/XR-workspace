"""Imports a raw usbmon dump, as `cat /dev/usbmonN` writes it, into a .lcap file.

The read() side of /dev/usbmonN gives each event as the 48 byte header of the kernel's struct mon_bin_hdr, followed by
len_cap bytes, unpadded (drivers/usb/mon/mon_bin.c, PKT_SZ_API0). Such a dump holds everything on the bus, including
camera images and the glasses' serial number, so it stays on the machine. The .lcap written here is the sanitised form:

  kept whole      IMU and event packets (0x81, 0x83), commands (0x04, 0x06), replies of 8 bytes or fewer on 0x85, and
                  0x87 replies
  12 bytes kept   each camera tile on bulk 0x82: its header, never its pixels (flag capped)
  dropped         longer replies on 0x85, which hold factory calibration and the serial; USB string descriptors; every
                  other device's data (flag policy dropped)
  left out        isochronous events of any device: nothing here is about the glasses
"""
import os
import struct

from . import capfile
from .usbmon import LUMA_ULTRA_PID, COMPANION_PID, VITURE_VID

HEADER = struct.Struct('<QBBBBHbbqiiII8s')
XFER_ISO = capfile.XFER_ISO
EP_CAMERA = 0x82
EP_MCU_IN = 0x85
CAMERA_HEADER_BYTES = 12
MCU_REPLY_KEPT = 8
DESCRIPTOR_STRING = 0x03
READ_AHEAD = 1 << 20


class RawError(ValueError):
    """The file is not a read() dump of usbmon."""


def _device_id(data):
    """(vid, pid) if these bytes are a USB device descriptor."""
    if len(data) >= 12 and data[0] == 18 and data[1] == 1:
        return int.from_bytes(data[8:10], 'little'), int.from_bytes(data[10:12], 'little')
    return None


def events(path):
    """Every event, as (header tuple, data). Data is only read when it might be kept; see sanitise."""
    with open(path, 'rb', buffering=READ_AHEAD) as file:
        size = os.fstat(file.fileno()).st_size
        at = 0
        while at + HEADER.size <= size:
            head = file.read(HEADER.size)
            fields = HEADER.unpack(head)
            kind, bus, length = fields[1], fields[5], fields[12]
            if kind not in b'SCE' or at + HEADER.size + length > size:
                if at + HEADER.size + length > size and kind in b'SCE':
                    return                  # the recorder was stopped part way through an event
                raise RawError(f'{path}: no usbmon event at byte {at}')
            yield fields, file.read(length)
            at += HEADER.size + length


def sanitise(xfer, ep, flags, data):
    """What of this payload may be kept, and the flags that say so."""
    if not flags & (capfile.F_GLASSES | capfile.F_COMPANION):
        return (b'', flags | capfile.F_POLICY_DROPPED) if data else (b'', flags)
    if flags & capfile.F_COMPANION and xfer != capfile.XFER_CTRL:
        return b'', flags | capfile.F_POLICY_DROPPED
    if xfer == capfile.XFER_CTRL and len(data) >= 2 and data[1] == DESCRIPTOR_STRING:
        return b'', flags | capfile.F_POLICY_DROPPED
    if ep == EP_CAMERA:
        return data[:CAMERA_HEADER_BYTES], flags | capfile.F_CAPPED
    if ep == EP_MCU_IN and len(data) > MCU_REPLY_KEPT:
        return b'', flags | capfile.F_POLICY_DROPPED
    return data, flags


def import_raw(source, output, *, stats=None):
    """Writes the sanitised capture. Returns the number of records, and fills `stats` (a dict) if given."""
    devices = {}
    count = skipped = 0
    writer = None
    last_ns = 0
    try:
        for (urb, kind, xfer, ep, dev, bus, flag_setup, flag_data, sec, usec, status, len_urb, len_cap, setup), data \
                in events(source):
            if xfer == XFER_ISO:
                skipped += 1
                continue
            t_ns = sec * 10**9 + usec * 1000
            found = _device_id(data) if dev and ep == 0x80 else None
            if found == (VITURE_VID, LUMA_ULTRA_PID):
                devices[dev] = capfile.F_GLASSES
            elif found == (VITURE_VID, COMPANION_PID):
                devices[dev] = capfile.F_COMPANION
            if writer is None:
                writer = capfile.Writer(output, start_ns=t_ns, bus=bus)
                writer.write(capfile.note_record(t_ns, type='start', bus=bus, source='usbmon read() dump'))
            flags = devices.get(dev, 0)
            if flag_setup == 0:
                flags |= capfile.F_SETUP
            kept, flags = sanitise(xfer, ep, flags, data)
            writer.write(capfile.Record(t_ns=t_ns, event=chr(kind), xfer=xfer, ep=ep, urb_id=urb, bus=bus, dev=dev,
                                        flags=flags, status=status, urb_len=len_urb,
                                        setup=setup if flag_setup == 0 else bytes(8), data=kept))
            count += 1
            last_ns = t_ns
        if writer is None:
            raise RawError(f'{source}: no usbmon events')
        writer.write(capfile.note_record(last_ns, type='stop', reason='end of dump'))
    finally:
        if writer is not None:
            writer.close()
    if stats is not None:
        stats.update(isochronous_left_out=skipped, devices={str(k): v for k, v in devices.items()})
    return count
