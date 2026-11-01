"""Read-only detection; never infer a model from a PID alone."""
def _value(v):
    return v.get('value') if isinstance(v, dict) else v


def _rows(v):
    return (v.data or []) if hasattr(v, 'data') else (v or [])


def identify(usb, drm, monitors):
    devices = [d for d in _rows(usb) if _value(d.get('vendor_id')) == '35ca' and _value(d.get('product_id')) != '1102']
    if not devices: return dict(model=None, output=None, reason='no glasses')
    if len(devices) != 1: return dict(model='unknown', output=None, reason='multiple VITURE devices')
    d = devices[0]
    model = {('VITURE Beast XR Glasses','1201'):'beast', ('VITURE Luma Ultra XR GLASSES','1104'):'luma-ultra'}.get((_value(d.get('product')), _value(d.get('product_id'))),'unknown')
    wanted = {'beast':('VITURE Beast',), 'luma-ultra':('VITURE',), 'unknown':('VITURE','VITURE Beast')}[model]
    connected = {r['name'].split('-',1)[-1] for r in _rows(drm) if _value(r.get('status')) == 'connected'}
    outputs = [m['name'] for m in monitors if m.get('model') in wanted and not m.get('disabled',False) and (not _rows(drm) or m['name'] in connected)]
    return dict(model=model,output=outputs[0] if len(outputs)==1 else None,reason=None if len(outputs)==1 else 'presentation connector is not unambiguous')


def pick_fallback(monitors, output, saved):
    names = [m['name'] for m in monitors if m.get('name') != output and not m.get('name','').startswith(('XR-','HEADLESS-')) and m.get('physical',True) and not m.get('disabled',False) and m.get('dpmsStatus',True) and m.get('mirrorOf','none') in (None,'','none')]
    if saved in names: return saved
    return names[0] if len(names)==1 else 'choose' if names else None
