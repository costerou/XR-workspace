"""Writes a capture as a classic pcap file, for opening in Wireshark. Notes have no place there and are left out."""
import struct

from .capfile import F_SETUP, records

LINKTYPE_USB_LINUX_MMAPPED = 220
FILE_HEADER = struct.Struct('<IHHiIII')
PACKET_HEADER = struct.Struct('<IIII')
MON = struct.Struct('<QBBBBHbbqiiII8siiII')


def export(paths, output):
    count = 0
    with open(output, 'wb') as file:
        file.write(FILE_HEADER.pack(0xa1b2c3d4, 2, 4, 0, 0, 262144, LINKTYPE_USB_LINUX_MMAPPED))
        for record in records(paths):
            if record.is_note:
                continue
            seconds, nanos = divmod(record.t_ns, 10**9)
            flag_setup = 0 if record.flags & F_SETUP else ord('-')
            flag_data = 0 if record.data else ord('<')
            header = MON.pack(record.urb_id, ord(record.event), record.xfer, record.ep, record.dev, record.bus,
                              flag_setup, flag_data, seconds, nanos // 1000, record.status, record.urb_len,
                              len(record.data), record.setup, 0, 0, 0, 0)
            size = len(header) + len(record.data)
            file.write(PACKET_HEADER.pack(seconds, nanos // 1000, size, size) + header + record.data)
            count += 1
    return count
