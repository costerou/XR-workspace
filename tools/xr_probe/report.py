"""Schema-v1 diagnostic reporting; observation is not performance validation."""
from datetime import datetime, timezone
import html
import math
from .hardware import clean_text
from .session import classify_output
from .model import HostReport, ProbeResult, SessionCandidate, to_json as model_json


def _field(value, source):
    return {'value': value, 'status': 'ok' if value is not None else 'unknown', 'source': source}


def _number(value):
    if type(value) not in (int, float):
        return None
    try:
        return value if math.isfinite(value) and 0 < value <= 100000 else None
    except (OverflowError, ValueError):
        return None


def _available(raw, source):
    modes = []
    if isinstance(raw, list):
        for index, mode in enumerate(raw[:256]):
            if isinstance(mode, dict):
                modes.append({key: _field(_number(mode.get(original)), f'{source}[{index}].{original}')
                              for key, original in [('width', 'width'), ('height', 'height'),
                                                    ('refresh_hz', 'refreshRate')]})
    return _field(modes or None, source)


def _inventory(result, kind):
    if result.status != 'ok':
        return ProbeResult(result.status, None, 'Inventory unavailable; see status')
    if not isinstance(result.data, list):
        return ProbeResult('error', None, 'Invalid inventory shape')
    fields = ('vendor_id', 'product_id', 'product', 'authorized') if kind == 'usb' else ('status', 'modes', 'gpu_device')
    label = 'path' if kind == 'usb' else 'name'
    rows = []
    for index, raw in enumerate(result.data):
        if not isinstance(raw, dict):
            continue
        row: dict = {label: clean_text(raw.get(label))}
        for key in fields:
            item = raw.get(key)
            item = item if isinstance(item, dict) else {}
            status = item.get('status')
            status = status if status in ('ok', 'unknown', 'missing', 'permission_denied', 'error', 'truncated') else 'unknown'
            value = item.get('value') if status == 'ok' else None
            if key == 'authorized':
                value = value if type(value) is bool else None
            elif key == 'modes':
                value = [clean_text(v) for v in value[:256] if isinstance(v, str)] if isinstance(value, list) else None
                value = value or None
            else:
                value = clean_text(value)
            row[key] = {'value': value, 'status': 'unknown' if status == 'ok' and value is None else status,
                        'source': clean_text(item.get('source')) or f'{kind}.data[{index}].{key}'}
        rows.append(row)
    return ProbeResult('ok', rows, '')


def _host(raw):
    packages = raw.get('packages')
    packages = packages if isinstance(packages, dict) else {}
    return {'architecture': _field(clean_text(raw.get('architecture')), 'host.architecture'),
            'gpu': _field(clean_text(raw.get('gpu')), 'host.gpu'),
            'packages': {name: _field(clean_text(packages.get(name)), f'host.packages.{name}')
                         for name in ('hyprland', 'mesa', 'libdrm', 'linux', 'nvidia-utils')}}


def _output(raw, index):
    source = f'session.data.outputs[{index}]'
    classification = raw.get('classification', classify_output(raw))
    if classification not in ('physical', 'headless_only', 'unknown'):
        classification = 'unknown'
    return {'classification': classification,
            'name': _field(clean_text(raw.get('name')), source + '.name'),
            'make': _field(clean_text(raw.get('make')), source + '.make'),
            'model': _field(clean_text(raw.get('model')), source + '.model'),
            'disabled': _field(raw.get('disabled') if type(raw.get('disabled')) is bool else None,
                               source + '.disabled'),
            'active_mode': {key: _field(_number(raw.get(original)), source + '.' + original)
                            for key, original in [('width', 'width'), ('height', 'height'),
                                                  ('refresh_hz', 'refreshRate')]},
            'available_modes': _available(raw.get('available_modes'), source + '.available_modes')}


def build_report(session: ProbeResult, usb: ProbeResult, drm: ProbeResult,
                 host: dict, *, collected_at: str) -> HostReport:
    """Accept a *selected* SessionCandidate, not a discovery candidate list.

    Caller supplies actual collection time using datetime.now(timezone.utc).
    """
    timestamp = datetime.fromisoformat(collected_at)
    if timestamp.tzinfo is None or timestamp.utcoffset() is None:
        raise ValueError('collected_at must be timezone-aware')
    selected = (session.data if session.status == 'ok' and isinstance(session.data, SessionCandidate)
                and session.data.classification == 'physical' else None)
    outputs = [_output(raw, i) for i, raw in enumerate(selected.outputs) if isinstance(raw, dict)] if selected else []
    normalized = {'status': session.status if selected or session.status != 'ok' else 'error',
                  'classification': selected.classification if selected else 'unknown',
                  'outputs': outputs}
    active = [output['active_mode']['refresh_hz']['source'] for output in outputs
              if output['classification'] == 'physical'
              and output['active_mode']['refresh_hz']['value'] == 120
              and output['disabled']['value'] is False]
    advertised = [mode['refresh_hz']['source'] for output in outputs
                  if output['classification'] == 'physical'
                  for mode in (output['available_modes']['value'] or [])
                  if mode['refresh_hz']['value'] == 120]
    capabilities = {
        'active_120_hz': {'state': 'observed' if active else 'untested', 'evidence': active,
                          'reason': 'Selected compositor output only; not attributed to VITURE and not measured presentation.'},
        'advertised_120_hz': {'state': 'advertised' if advertised else 'untested', 'evidence': advertised,
                              'reason': 'Available-mode advertisement only; not active or sustained.' if advertised else 'Refresh-bearing available modes missing or inconclusive; support unknown.'},
        'sustained_120_hz': {'state': 'untested', 'evidence': [],
                             'reason': 'No presentation timing measurements in milestone A.'},
        'tracking_quality': {'state': 'untested', 'evidence': [],
                             'reason': 'No SDK execution or tracking measurements in milestone A.'}}
    return HostReport(1, timestamp.astimezone(timezone.utc).isoformat(), _host(host), normalized,
                      _inventory(usb, 'usb'), _inventory(drm, 'drm'), capabilities)


def to_json(report: HostReport) -> str:
    """Serialize a report produced by build_report (not arbitrary raw inputs)."""
    return model_json(report)


def render_markdown(report: HostReport) -> str:
    """Embed escaped JSON in HTML pre, so device strings cannot inject Markdown."""
    return '# XR feasibility report\n\nRead-only inventory; performance and tracking remain untested.\n\n<pre>\n' + html.escape(to_json(report)) + '\n</pre>\n'
