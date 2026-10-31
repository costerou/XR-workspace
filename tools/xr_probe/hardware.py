"""Read-only USB/DRM inventory. No EDID, serial, SDK or device writes."""
from pathlib import Path
import re
import unicodedata
from .model import ProbeResult


def clean_text(value: object) -> str | None:
    """Bound untrusted single-line text; discard control/format characters."""
    if not isinstance(value, str):
        return None
    return ''.join(c if not unicodedata.category(c).startswith('C') and c not in '\u2028\u2029' else ' '
                   for c in value[:512]).strip()


def _field(path: Path, kind: str = 'text') -> dict:
    status = 'ok'
    value = None
    try:
        with path.open('r', encoding='utf-8', errors='replace') as stream:
            raw = stream.read(4097)
        if len(raw) > 4096:
            status = 'truncated'
        else:
            raw = raw.strip()
            if kind == 'id':
                value = raw.lower() if re.fullmatch(r'[0-9a-fA-F]{4}', raw) else None
            elif kind == 'modes':
                value = [clean_text(line) for line in raw.splitlines() if line.strip()] or None
            elif kind == 'authorized':
                value = raw == '1' if raw in ('0', '1') else None
            else:
                value = clean_text(raw) or None
            if value is None:
                status = 'unknown'
    except PermissionError:
        status = 'permission_denied'
    except FileNotFoundError:
        status = 'missing'
    except OSError:
        status = 'error'
    return {'value': value, 'status': status, 'source': clean_text(str(path))}


def _entries(root: Path):
    try:
        return sorted(root.iterdir()), None
    except PermissionError:
        return [], ProbeResult('permission_denied', None, 'Inventory root unreadable')
    except FileNotFoundError:
        return [], ProbeResult('missing', None, 'Inventory root missing')
    except OSError:
        return [], ProbeResult('error', None, 'Inventory root failed')


def probe_usb(sysfs_root: Path = Path('/sys/bus/usb/devices')) -> ProbeResult:
    """Match exact VITURE vendor IDs, never infer a model from product IDs."""
    entries, failure = _entries(sysfs_root)
    if failure:
        return failure
    devices = []
    seen = set()
    for node in entries:
        if ':' in node.name:
            continue  # USB interfaces are not physical devices.
        try:
            physical = node.resolve()
        except PermissionError:
            return ProbeResult('permission_denied', None, 'USB device path resolution denied')
        except FileNotFoundError:
            return ProbeResult('missing', None, 'USB device path disappeared')
        except OSError:
            return ProbeResult('error', None, 'USB device path resolution failed')
        if physical in seen:
            continue
        seen.add(physical)
        vendor = _field(node / 'idVendor', 'id')
        if vendor['status'] == 'ok' and vendor['value'] != '35ca':
            continue
        if vendor['status'] == 'unknown':
            continue  # Malformed IDs are not matches.
        devices.append({'path': clean_text(str(physical)), 'vendor_id': vendor,
                        'product_id': _field(node / 'idProduct', 'id'),
                        'product': _field(node / 'product'),
                        'authorized': _field(node / 'authorized', 'authorized')})
    return ProbeResult('ok', devices, '')


def probe_drm(sysfs_root: Path = Path('/sys/class/drm')) -> ProbeResult:
    """Mode names lack refresh metadata; they are not proof of 120 Hz."""
    entries, failure = _entries(sysfs_root)
    if failure:
        return failure
    connectors = []
    for node in entries:
        if not re.fullmatch(r'card\d+-.+', node.name):
            continue
        link = node / 'device'
        try:
            target = link.readlink()
            gpu = {'value': clean_text(str((link.parent / target).resolve())),
                   'status': 'ok', 'source': clean_text(str(link))}
        except OSError as exc:
            status = 'permission_denied' if isinstance(exc, PermissionError) else 'unknown'
            gpu = {'value': None, 'status': status, 'source': clean_text(str(link))}
        connectors.append({'name': clean_text(node.name),
                           'status': _field(node / 'status'),
                           'modes': _field(node / 'modes', 'modes'), 'gpu_device': gpu})
    return ProbeResult('ok', connectors, '')
