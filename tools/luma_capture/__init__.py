"""Passive capture, labelling, report and replay files for the glasses' USB traffic.

Nothing here opens the glasses. Live capture reads the kernel's usbmon tap, which only sees what other
programs already put on the bus. File format: docs/capture-format.md.
"""
