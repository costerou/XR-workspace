"""Strip what a shared fixture must not carry from kernel and driver log lines.

Serial numbers, the host name and XRLinuxDriver's hardware id are removed. Unprintable bytes are removed
too: the "Error opening file (...)" lines print uninitialised heap memory (a bug in the driver).
"""
import re

_HOST = re.compile(r'^(\d{4}-\d\d-\d\dT\S+\s+)\S+(\s+kernel:)')
_SERIAL = re.compile(r'(SerialNumber: ).*$')
_HARDWARE = re.compile(r'(hardware id ).*$', re.I)
_UNPRINTABLE = re.compile(r'[^\x20-\x7e\t]')
_KERNEL_USB = re.compile(r'kernel: (usb |hub |xhci_hcd )')


def line(text):
    text = _HOST.sub(r'\1host\2', text)
    text = _SERIAL.sub(r'\1REDACTED', text)
    text = _HARDWARE.sub(r'\1REDACTED', text)
    return _UNPRINTABLE.sub('?', text)


def lines(stream, kernel_usb_only=False):
    for text in stream:
        if kernel_usb_only and not _KERNEL_USB.search(text):
            continue
        yield line(text.rstrip('\n'))
