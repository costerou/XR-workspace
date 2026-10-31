"""Bounded Linux read-only probes. No shell and no arbitrary-command CLI.

Executables resolve via the caller's trusted PATH. max_bytes is a per-stream
byte cap; data is stdout text, detail is stderr plus a short status explanation.
Only callers that check status == 'ok' may treat data as complete evidence.
"""
import math
import os
import re
import selectors
import signal
import subprocess
import time

from .model import ProbeResult

# Expand only alongside a specific diagnostic need, never by CLI passthrough.
DEPENDENCIES = frozenset({'wayland-client', 'libdrm', 'egl', 'gbm', 'vulkan',
                          'x11', 'libusb-1.0'})
_INSTANCE = re.compile(r'[A-Za-z0-9][A-Za-z0-9_-]{0,255}', re.ASCII)


def _allowed(argv: list[str]) -> bool:
    if not isinstance(argv, list) or not all(isinstance(x, str) for x in argv):
        return False
    if argv in (['uname', '-m'], ['lspci'], ['hyprctl', 'instances', '-j']):
        return True
    if len(argv) == 3 and argv[:2] == ['pkg-config', '--modversion']:
        return argv[2] in DEPENDENCIES
    return (len(argv) in (5, 6) and argv[:2] == ['hyprctl', '-i']
            and _INSTANCE.fullmatch(argv[2]) is not None
            and argv[3:] in (['version', '-j'], ['monitors', 'all', '-j']))


def _text(raw: bytes | bytearray, limit: int) -> str:
    # Replacement characters can expand invalid UTF-8; cap the encoded result too.
    return raw.decode('utf-8', errors='replace').encode('utf-8')[:limit].decode(
        'utf-8', errors='ignore')


def run_probe(argv: list[str], *, timeout_s: float = 5.0,
              max_bytes: int = 65536) -> ProbeResult:
    """Run exactly an allowlisted argv, terminating/reaping on either bound.

    stdout and stderr are drained concurrently in at most 8192-byte reads.
    Children use a new session so timeout/overflow also kills inherited-pipe
    descendants. Process creation is the internal test injection seam.
    """
    if (type(timeout_s) not in (int, float) or not 0 < timeout_s < math.inf
            or type(max_bytes) is not int or max_bytes <= 0):
        return ProbeResult('error', None, 'Invalid probe limits')
    if not _allowed(argv):
        return ProbeResult('error', None, _text(b'Command is not allowed', max_bytes))
    deadline = time.monotonic() + timeout_s
    try:
        child = subprocess.Popen(list(argv), stdin=subprocess.DEVNULL,
                                 stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                                 shell=False, start_new_session=True, bufsize=0)
    except FileNotFoundError:
        return ProbeResult('missing', None, _text(b'Executable not found', max_bytes))
    except PermissionError:
        return ProbeResult('permission_denied', None, _text(b'Execution denied', max_bytes))
    except OSError:
        return ProbeResult('error', None, _text(b'Could not start probe', max_bytes))

    assert child.stdout is not None and child.stderr is not None
    buffers = [bytearray(), bytearray()]
    status = 'ok'
    reason = ''
    try:
        with selectors.DefaultSelector() as selector:
            for index, pipe in enumerate((child.stdout, child.stderr)):
                os.set_blocking(pipe.fileno(), False)
                selector.register(pipe, selectors.EVENT_READ, index)
            while selector.get_map():
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    status, reason = 'timeout', 'Probe deadline exceeded'
                    break
                for key, _ in selector.select(remaining):
                    buffer = buffers[key.data]
                    chunk = os.read(key.fd, min(8192, max_bytes - len(buffer) + 1))
                    if not chunk:
                        selector.unregister(key.fileobj)
                        continue
                    room = max_bytes - len(buffer)
                    buffer.extend(chunk[:room])
                    if len(chunk) > room:
                        status, reason = 'truncated', 'Probe output limit exceeded'
                        break
                if status != 'ok':
                    break
            if status == 'ok':
                try:
                    code = child.wait(timeout=max(0, deadline - time.monotonic()))
                    if code != 0:
                        status, reason = 'error', f'Probe exited with status {code}'
                except subprocess.TimeoutExpired:
                    status, reason = 'timeout', 'Probe deadline exceeded'
    except OSError:
        status, reason = 'error', 'Could not read probe output'
    finally:
        if child.returncode is None or status in ('timeout', 'truncated'):
            try:
                os.killpg(child.pid, signal.SIGKILL)
            except ProcessLookupError:
                pass
        child.wait()
        child.stdout.close()
        child.stderr.close()
    detail = (reason + '\n').encode() if reason else b''
    detail += buffers[1]
    return ProbeResult(status, _text(buffers[0], max_bytes), _text(detail, max_bytes))
