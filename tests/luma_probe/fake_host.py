"""A throwaway sysfs, /dev, udev and /proc tree shaped like Cody's machine.

The descriptor bytes are the real ones, read from /sys/bus/usb/devices/*/descriptors on 2026-11-03.
Device nodes are plain files here: only their mode matters, and nothing may ever open them.
"""
import os
from pathlib import Path

from tools.luma_probe.sysfs import Roots

GLASSES_DESCRIPTORS = bytes.fromhex(
    '1201100200000040ca35041101001020000109024300010130c00109040000070000004007058103000403'
    '070582020002000705830300020407050402000200070585020002000705060200020007058702000200')
MIC_DESCRIPTORS = bytes.fromhex(
    '1201100100000040ca3502110101010203010902bd0005010080320904000000010100000924010001270001'
    '010c2402010102000203000000092403030101000100090401000001020000090401010101020000072401'
    '030101000b24020101021001803e00090581012000010000072501000000000904020001030000000921'
    '000100012242000705820308000109040300020300000009210001000122210007058403400001070503'
    '034000010904040002030000000921000100012221000705860340000107050503400001')
HID_REPORT = bytes.fromhex('050c0901a101090075089540150026ff008102090075089540150026ff009102c0')
# /usr/lib/udev/rules.d/70-viture-xr.rules from xr-driver-git 2.11.9, word for word.
SHIPPED_RULES = (
    'SUBSYSTEM=="usb", ACTION=="add", ATTRS{idVendor}=="35ca", MODE="0660", TAG+="uaccess"\n'
    'SUBSYSTEM=="usb", KERNEL=="hiddev[0-9]*", ATTRS{idVendor}=="35ca", MODE="0660", TAG+="uaccess"\n'
    'SUBSYSTEM=="tty", KERNEL=="ttyACM[0-9]*", ATTRS{idVendor}=="35ca", MODE="0660", TAG+="uaccess"\n'
    'SUBSYSTEM=="hidraw", KERNEL=="hidraw[0-9]*", ATTRS{idVendor}=="35ca", MODE="0660", TAG+="uaccess"\n')
FIXED_RULES = SHIPPED_RULES.replace('ACTION=="add", ', '')


class FakeHost:
    def __init__(self, base):
        self.base = Path(base)
        self.roots = Roots(sys=self.base / 'sys', dev=self.base / 'dev', proc=self.base / 'proc',
                           udev=(self.base / 'etc-udev', self.base / 'run-udev', self.base / 'lib-udev'))
        for d in (self.roots.sys / 'bus/usb/devices', self.roots.dev, self.roots.proc, *self.roots.udev):
            d.mkdir(parents=True)

    def device(self, name, vid, pid, product='', speed='480', devnum=None, descriptors=b'', control='on'):
        d = self.roots.sys / 'bus/usb/devices' / name
        (d / 'power').mkdir(parents=True)
        bus = name.split('-')[0]
        devnum = devnum if devnum is not None else 4
        for key, value in (('idVendor', vid), ('idProduct', pid), ('product', product), ('speed', speed),
                           ('busnum', bus), ('devnum', str(devnum)), ('manufacturer', 'VITURE'),
                           ('power/control', control), ('power/runtime_status', 'active')):
            (d / key).write_text(value + '\n')
        (d / 'descriptors').write_bytes(descriptors)
        node = self.roots.dev / f'bus/usb/{int(bus):03d}/{devnum:03d}'
        node.parent.mkdir(parents=True, exist_ok=True)
        node.write_text('')
        node.chmod(0o660)
        return d

    def interface(self, device, number=0, cls='00', driver=None):
        d = self.roots.sys / 'bus/usb/devices' / f'{device}:1.{number}'
        d.mkdir()
        (d / 'bInterfaceNumber').write_text(f'{number:02x}\n')
        (d / 'bInterfaceClass').write_text(cls + '\n')
        if driver:
            target = self.roots.sys / 'bus/usb/drivers' / driver
            target.mkdir(parents=True, exist_ok=True)
            (d / 'driver').symlink_to(target)
        return d

    def hidraw(self, interface, n, report=HID_REPORT):
        hid = interface / f'0003:35CA:1102.{n:04X}'
        (hid / 'hidraw' / f'hidraw{n}').mkdir(parents=True)
        (hid / 'report_descriptor').write_bytes(report)
        node = self.roots.dev / f'hidraw{n}'
        node.write_text('')
        node.chmod(0o660)
        return node

    def rules(self, text, name='70-viture-xr.rules', where=2):
        (self.roots.udev[where] / name).write_text(text)

    def process(self, pid, comm, holds=None, state='S', exe=None):
        d = self.roots.proc / str(pid)
        (d / 'fd').mkdir(parents=True)
        (d / 'comm').write_text(comm + '\n')
        (d / 'stat').write_text(f'{pid} ({comm}) {state} 1 1 1 0 -1\n')
        if exe:
            (d / 'exe').symlink_to(exe)
        if holds is not None:
            (d / 'fd' / '7').symlink_to(holds)

    def limits(self, rtprio='0', nice='0'):
        (self.roots.proc / 'self').mkdir(exist_ok=True)
        (self.roots.proc / 'self/limits').write_text(
            'Limit                     Soft Limit           Hard Limit           Units\n'
            f'Max nice priority         {nice}                    {nice}\n'
            f'Max realtime priority     {rtprio}                    {rtprio}\n'
            'Max realtime timeout      unlimited            unlimited            us\n')

    def glasses_on_hub(self):
        """The live layout: hub 5-2, glasses 5-2.1, camera 5-2.2, microphone 5-2.3, shipped rules."""
        self.device('5-2', '1a86', '8091', 'USB HUB', devnum=3, control='auto')
        self.device('5-2.1', '35ca', '1104', 'VITURE Luma Ultra XR GLASSES', devnum=4, descriptors=GLASSES_DESCRIPTORS)
        self.interface('5-2.1')
        self.device('5-2.2', '0c45', '636b', 'USB 2.0 Camera', devnum=5, control='auto')
        self.device('5-2.3', '35ca', '1102', 'VITURE Microphone', speed='12', devnum=6, descriptors=MIC_DESCRIPTORS)
        for n, cls, drv in ((0, '01', 'snd-usb-audio'), (1, '01', 'snd-usb-audio'), (2, '03', 'usbhid')):
            self.interface('5-2.3', n, cls, drv)
        self.hidraw(self.interface('5-2.3', 3, '03', 'usbhid'), 10)
        self.hidraw(self.interface('5-2.3', 4, '03', 'usbhid'), 11)
        self.rules(FIXED_RULES)
        return self

    @property
    def glasses_node(self):
        return self.roots.dev / 'bus/usb/005/004'
