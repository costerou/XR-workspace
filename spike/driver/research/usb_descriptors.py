#!/usr/bin/env python3
"""Decode a USB device's cached descriptors from sysfs. Sends nothing on the bus.

`/sys/bus/usb/devices/<port>/descriptors` is a copy the kernel took at enumeration, so reading it
neither opens the device nor wakes a suspended one. Opening /dev/video* does wake it: that is why
this exists rather than `v4l2-ctl --all` or `lsusb -v` (lsusb asks the device for its strings).

    python3 -I spike/driver/research/usb_descriptors.py 5-2.2
"""
import struct
import sys
from pathlib import Path

DEVICE, CONFIG, INTERFACE, ENDPOINT, CS_INTERFACE = 1, 2, 4, 5, 0x24
UVC_CLASS, VIDEO_CONTROL, VIDEO_STREAMING = 14, 1, 2
VS_FORMAT_UNCOMPRESSED, VS_FRAME_UNCOMPRESSED, VS_FORMAT_MJPEG, VS_FRAME_MJPEG = 4, 5, 6, 7
ENDPOINT_TYPES = ('control', 'iso', 'bulk', 'interrupt')


def _frame(body):
    width, height = struct.unpack('<HH', body[5:9])
    count = body[25]
    intervals = struct.unpack(f'<{count or 3}I', body[26:26 + 4 * (count or 3)])
    return {'width': width, 'height': height, 'fps': [round(1e7 / i, 2) for i in intervals]}


def _endpoint(body):
    size = struct.unpack('<H', body[4:6])[0]
    kind = ENDPOINT_TYPES[body[3] & 3]
    packet, per_uframe = size & 0x7ff, ((size >> 11) & 3) + 1
    # High speed: periodic endpoints are polled every 2^(bInterval-1) microframes of 125 us.
    period_us = 125.0 * 2 ** (body[6] - 1) if kind in ('iso', 'interrupt') else None
    return {'address': body[2], 'direction': 'in' if body[2] & 0x80 else 'out', 'type': kind, 'packet': packet,
            'per_uframe': per_uframe, 'period_us': period_us,
            'bytes_per_s': packet * per_uframe * 1e6 / period_us if period_us else None}


def parse(data):
    """The blob as {'max_power_ma', 'attributes', 'interfaces': [...], 'formats': [...]}."""
    out = {'max_power_ma': None, 'attributes': None, 'interfaces': [], 'formats': []}
    current, position = None, 0
    while position + 2 <= len(data):
        length, kind = data[position], data[position + 1]
        if length == 0: break
        body = data[position:position + length]
        position += length
        if kind == CONFIG:
            out['max_power_ma'], out['attributes'] = body[8] * 2, body[7]
        elif kind == INTERFACE:
            current = {'number': body[2], 'alt': body[3], 'class': body[5], 'subclass': body[6], 'endpoints': []}
            out['interfaces'].append(current)
        elif kind == ENDPOINT and current is not None:
            current['endpoints'].append(_endpoint(body))
        elif kind == CS_INTERFACE and current and (current['class'], current['subclass']) == (UVC_CLASS, VIDEO_STREAMING):
            subtype = body[2]
            if subtype == VS_FORMAT_MJPEG:
                out['formats'].append({'name': 'MJPEG', 'frames': []})
            elif subtype == VS_FORMAT_UNCOMPRESSED:
                out['formats'].append({'name': bytes(body[5:9]).decode('ascii', 'replace'), 'frames': []})
            elif subtype in (VS_FRAME_MJPEG, VS_FRAME_UNCOMPRESSED):
                out['formats'][-1]['frames'].append(_frame(body))
    return out


def self_powered(parsed):
    return bool(parsed['attributes'] & 0x40)


def uvc_streaming_interfaces(parsed):
    return {i['number'] for i in parsed['interfaces'] if (i['class'], i['subclass']) == (UVC_CLASS, VIDEO_STREAMING)}


def largest_iso_reservation(parsed):
    """Bytes per second the biggest isochronous alternate setting reserves on the bus, wanted or not."""
    rates = [e['bytes_per_s'] for i in parsed['interfaces'] for e in i['endpoints'] if e['type'] == 'iso']
    return max(rates, default=0.0)


def report(parsed):
    lines = [f"bMaxPower {parsed['max_power_ma']} mA, {'self' if self_powered(parsed) else 'bus'}-powered"]
    for i in parsed['interfaces']:
        lines.append(f"interface {i['number']} alt {i['alt']} class {i['class']}/{i['subclass']}")
        for e in i['endpoints']:
            rate = f", {e['bytes_per_s'] / 1e6:.2f} MB/s" if e['bytes_per_s'] else ''
            period = f", every {e['period_us']:g} us" if e['period_us'] else ''
            lines.append(f"  ep 0x{e['address']:02x} {e['direction']} {e['type']} {e['packet']}x{e['per_uframe']}{period}{rate}")
    for f in parsed['formats']:
        lines.append(f"format {f['name']}")
        lines.extend(f"  {x['width']}x{x['height']} @ {x['fps']}" for x in f['frames'])
    return '\n'.join(lines)


if __name__ == '__main__':
    target = sys.argv[1]
    path = Path(target) if '/' in target else Path('/sys/bus/usb/devices') / target / 'descriptors'
    print(report(parse(path.read_bytes())))
