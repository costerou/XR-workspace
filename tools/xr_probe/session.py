"""Read-only explicit Hyprland discovery; never use the ambient instance.

Discovery is all-or-nothing: a failed instance probe cannot hide a competing
physical session. Output snapshots deliberately omit workspaces, window IDs,
serials and raw descriptions (which can embed serial numbers).
"""
import json
import math
import re
from typing import Callable, Literal

from .commands import run_probe
from .model import ProbeResult, SessionCandidate

_INSTANCE = re.compile(r'[A-Za-z0-9][A-Za-z0-9_-]{0,255}', re.ASCII)
_PHYSICAL = re.compile(r'(?:DP|HDMI-A|HDMI|eDP|LVDS|DVI-D|DVI-I|DVI-A|VGA|DSI)-[0-9]+(?:-[0-9]+)*')
_HEADLESS = re.compile(r'headless', re.IGNORECASE)


def _instances(raw: object) -> list[dict]:
    if not isinstance(raw, list):
        raise ValueError('Instances must be a JSON list')
    seen = set()
    for entry in raw:
        if not isinstance(entry, dict):
            raise ValueError('Invalid instance record')
        name = entry.get('instance')
        # hyprctl interprets numeric -i arguments as indexes, not identifiers.
        if (not isinstance(name, str) or not _INSTANCE.fullmatch(name)
                or name.isdecimal() or name in seen):
            raise ValueError('Invalid or duplicate instance identifier')
        if type(entry.get('pid')) is not int or entry['pid'] <= 0:
            raise ValueError('Invalid or missing instance pid')
        socket = entry.get('wl_socket')
        if socket is not None and (not isinstance(socket, str) or not socket):
            raise ValueError('Invalid Wayland socket')
        seen.add(name)
    return raw


def _outputs(raw: object) -> tuple[list[dict], Literal['physical', 'headless_only', 'unknown']]:
    if not isinstance(raw, list):
        raise ValueError('Monitors must be a JSON list')
    outputs = []
    kinds = []
    for entry in raw:
        if not isinstance(entry, dict) or not isinstance(entry.get('name'), str) or not entry['name']:
            raise ValueError('Invalid or missing monitor name')
        for field in ('description', 'make', 'model'):
            if field in entry and not isinstance(entry[field], str):
                raise ValueError('Invalid monitor text field')
        for field in ('width', 'height'):
            if field in entry and (type(entry[field]) is not int or entry[field] < 0):
                raise ValueError('Invalid monitor dimensions')
        if 'refreshRate' in entry:
            rate = entry['refreshRate']
            if type(rate) not in (int, float) or not math.isfinite(rate) or rate < 0:
                raise ValueError('Invalid monitor refresh rate')
        if 'disabled' in entry and type(entry['disabled']) is not bool:
            raise ValueError('Invalid monitor disabled flag')
        physical = bool(_PHYSICAL.fullmatch(entry['name']))
        headless = bool(_HEADLESS.search(entry['name']) or
                        _HEADLESS.search(entry.get('description', '')))
        kinds.append('physical' if physical and not headless else
                     'headless_only' if headless and not physical else 'unknown')
        outputs.append({key: entry[key] for key in
                        ('name', 'make', 'model', 'width', 'height', 'refreshRate', 'disabled')
                        if key in entry})
    classification: Literal['physical', 'headless_only', 'unknown'] = ('physical' if 'physical' in kinds else
                      'headless_only' if kinds and all(k == 'headless_only' for k in kinds)
                      else 'unknown')
    return outputs, classification


def discover_sessions(run: Callable = run_probe) -> ProbeResult:
    """Return ok/data=list[SessionCandidate], or non-ok/data=None.

    The injected runner has run_probe's argv -> ProbeResult contract (raw stdout
    in data). Validate every instance before issuing explicitly scoped queries.
    No partial candidates are returned on malformed or unsuccessful evidence.
    """
    result = run(['hyprctl', 'instances', '-j'])
    if result.status != 'ok':
        return ProbeResult(result.status, None, 'Instance enumeration failed: ' + result.detail)
    try:
        instances = _instances(json.loads(result.data))
    except (ValueError, TypeError):
        return ProbeResult('error', None, 'Malformed Hyprland instances JSON or fields')
    candidates = []
    for entry in instances:
        name = entry['instance']
        result = run(['hyprctl', '-i', name, 'monitors', 'all', '-j'])
        if result.status != 'ok':
            return ProbeResult(result.status, None, f'Output probe for {name} failed: ' + result.detail)
        try:
            outputs, classification = _outputs(json.loads(result.data))
        except (ValueError, TypeError):
            return ProbeResult('error', None, f'Malformed monitor JSON or fields for {name}')
        candidates.append(SessionCandidate(name, entry['pid'], entry.get('wl_socket'),
                                           outputs, classification))
    return ProbeResult('ok', candidates, '')


def select_session(candidates: list[SessionCandidate], requested: str | None) -> ProbeResult:
    """Return exactly one SessionCandidate dataclass in data, never a dict/list.

    Only physical candidates qualify, even with explicit selection. Unknown
    sessions prevent automatic selection because they may be competing physical
    sessions. Call only after successful discovery, not with partial evidence.
    """
    names = [candidate.instance for candidate in candidates]
    if len(set(names)) != len(names):
        return ProbeResult('error', None, 'Duplicate instance identifiers; selection is ambiguous')
    if requested is not None:
        matches = [candidate for candidate in candidates if candidate.instance == requested]
        if not matches:
            return ProbeResult('error', None, 'Requested instance not found')
        selected = matches[0]
        if selected.classification != 'physical':
            return ProbeResult('error', None, f'Requested session is {selected.classification}, not physical')
        return ProbeResult('ok', selected, '')
    physical = [candidate for candidate in candidates if candidate.classification == 'physical']
    if len(physical) > 1 or any(candidate.classification == 'unknown' for candidate in candidates):
        return ProbeResult('error', None, 'Physical session selection is ambiguous; request an explicit physical instance')
    if not physical:
        return ProbeResult('error', None, 'No physical session found (empty or headless-only sessions)')
    return ProbeResult('ok', physical[0], '')
