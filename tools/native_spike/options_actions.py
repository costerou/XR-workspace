"""Supervisor-only runtime adapters; never execute commands in the renderer.

Returned bytes must be sent by supervisor in order. A return is NOT applied readback:
async desktop/video/3D/privacy/send owners must be polled by OptionsMenu.
"""
import math
from . import immersive, quality
from .options_editors import NativeEditors, row

CATEGORIES = ('3d','stabilization','layout','view','video','input','keybinds','windows','privacy','help','session')

ALIASES = {'video_open':'video_browse', 'video_paste_ui':'video_paste', 'immersive_strength_presets':'immersive_strength'}

# Explicit routes; modal navigation remains owned by the existing native child.
# Not here on purpose (Cody, 2026-11-07): what the placement pill's panel or the level bar already do (placement, tilt, resolution,
# scale, ultrawide, add, remove, background), what a click already does (selecting a desktop) or the Maximize button, the VR
# video's own controls (the menu is closed while a video plays), and sending a window to a named desktop (open the menu on it).
ACTIONS = {
 'view': ('recenter', 'mode_follow', 'mode_locked'),
 'layout': ('spacing_down', 'spacing_up', 'layout_reset'),
 'input': ('typing_toggle',), 'windows': ('send_glasses', 'send_back'),
 'privacy': ('privacy_display',), 'help': ('help', 'help_prev', 'help_next'),
 'video': ('video_browse', 'video_play', 'video_paste'),
 'keybinds': ('binds_toggle',),
 'session': ('stop',),
}
MONITOR_ACTIONS = {'send_glasses', 'mode_follow', 'mode_locked'}
LABELS = {'video_browse':'Choose local video', 'video_play':'Enter video URL', 'binds_toggle':'Edit key bindings', 'layout_reset':'Reset layout (migration confirmation)'}
LABELS.update(mode_follow='Maximize in Follow mode',mode_locked='Maximize in Locked mode')
LABELS.update(privacy_display='Privacy: main display off')


def _index(control, scope, monitor):
    if scope not in ('monitor', 'workspace'): raise ValueError('invalid scope')
    if monitor not in control.state.names: raise ValueError('removed monitor')
    return control.state.names.index(monitor)


def _number(value, lo, hi, integer=True):
    if type(value) not in (int, float) or not math.isfinite(value) or not lo <= value <= hi or (integer and int(value) != value):
        raise ValueError('value outside backend bounds')
    return int(value) if integer else value


def _range(id, label, value, bounds, scope, unit, step=1):
    return {**row(id, label, 'range', value), 'scope': scope,
            'minimum': bounds[0], 'maximum': bounds[1], 'step': step, 'unit': unit}


def options_rows(control, scope, monitor, category):
    i = _index(control, scope, monitor)
    s = control.state
    out = []
    for id in ACTIONS.get(category, ()):
        rs = 'monitor' if id in MONITOR_ACTIONS else 'workspace'
        if rs == scope:
            out.append({**row(id, LABELS.get(id, id.replace('_', ' ').capitalize())), 'scope': rs})
    if category == 'stabilization' and scope == 'workspace':
        out += [_range('stabilization', 'Display stabilization', s.stabilization, (0,100), scope, '%'),
                row('prediction', 'Pose prediction', 'toggle', str(s.prediction).lower())]
    if category == '3d':
        status=control._immersive_status()
        run=getattr(control,'immersive_run',None)
        report=(getattr(control,'owner_readback',None) or {}).get('immersive')
        phase=status['phase']
        detail='Physical R1 required; waiting for SBS' if phase=='glasses' else 'Failure latched; explicit retry required' if status['failed'] else phase
        out += [{**row('immersive.phase','3D actual phase','readonly',detail),'scope':scope},
                {**row('immersive.applied','3D actual drawing','readonly',str(bool(isinstance(report,dict) and any(r.get('drawing') for r in report.get('desktops',[])))).lower()),'scope':scope},
                {**row('immersive.error','3D error','readonly',report.get('error') or report.get('engine_error') or getattr(run,'error','') if isinstance(report,dict) else getattr(run,'error','')),'scope':scope}]
        v = immersive.immersive(s)
        if scope == 'workspace':
            out += [row('immersive_all','All desktops 3D','toggle',str(v['global']).lower()),
                    row('immersive_preset','3D look','choice',v['preset'],choices=list(immersive.PRESETS)),
                    _range('immersive_strength','3D strength',v['strength'],(0,immersive.STRENGTH_MAX),scope,'%'),
                    _range('immersive_plane','Screen plane',v['convergence'],(0,100),scope,'%')]
        else:
            out += [{**row('immersive_monitor','Monitor 3D','toggle',str(v['desktops'].get(monitor,v['global'])).lower()),'scope':scope},
                    _range('immersive_monitor_strength','Monitor 3D strength',v.get('strengths',{}).get(monitor,v['strength']),(0,immersive.STRENGTH_MAX),scope,'%')]
    if category == 'video' and scope == 'workspace':
        video=getattr(control,'options_video',None)
        if video is not None:
            snapshot=video.snapshot()
            out += [row('video.actual','Playback state','readonly',snapshot['state']),row('video.error','Playback error','readonly',snapshot['error'] or '')]
        for setting in quality.SETTINGS:
            out.append(row('quality_'+setting.key,setting.label,'choice',str(s.video_quality[setting.key]), choices=[str(v) for v,_ in setting.choices]))
    if category == 'session' and scope == 'workspace':
        out.append(row('session_identity','Session','readonly',control.session))
        measured=getattr(getattr(control,'supervisor',None),'status',{})
        for key in ('presented_hz','missed','capture_path','pose_age_ms','tracking'):
            out.append(row('status_'+key,key.replace('_',' ').capitalize(),'readonly',measured.get(key,'Not measured')))
    for item in out:
        id = item['id']
        if s.typing and id != 'typing_toggle': item['enabled'] = False
        if id.startswith('video_') or id.startswith('quality_'):
            item['enabled'] = getattr(control,'options_video',None) is not None
        if id == 'binds_toggle': item['enabled'] = control.keys is not None
        if id.startswith('send_'): item['enabled'] = control.sender is not None
        if id == 'privacy_display':
            item['enabled'] = control.privacy is not None
            item['value'] = '' if control.privacy is None else 'on' if control.privacy.blanked else 'off'
    if s.typing:
        for item in out:
            if item['id'] != 'typing_toggle': item['enabled'] = False
    return out


def owner_callback(control, row_id, monitor, scope):
    """Only OptionsMenu owns request identity; worker receives an opaque callback."""
    menu=getattr(control,'options',None)
    if menu is None or not callable(getattr(menu,'begin_owner',None)): return None
    token=menu.begin_owner(row_id,monitor,scope)
    def done(success,detail): return menu.finish_owner(token,success,detail)
    done.options_token=token
    done.retain_frames=lambda frames: menu.retain_owner_frames(token,frames)
    return done


def video_request(control, op, payload, row_id='video_open', monitor=None, scope='workspace'):
    video=getattr(control,'options_video',None)
    if video is None: raise ValueError('video owner unavailable')
    monitor=monitor or control.state.names[control.state.selected]
    done=owner_callback(control,row_id,monitor,scope)
    message={'op':op,'payload':payload}
    return video.on_message(message,done=done) if done is not None else video.on_message(message)


def options_apply(control, row_id, value, monitor, scope):
    """Validate typed input then call the original owner. Raises rather than claiming success.

    Revision/namespace checks belong to OptionsMenu immediately before this call.
    Reset requires its explicit confirmation before calling this adapter twice.
    """
    i = _index(control, scope, monitor)
    s = control.state
    row_id = ALIASES.get(row_id,row_id)
    if row_id.startswith('editor.'):
        return control.options_editor.apply(row_id,value)
    rows = [r for cat in (*ACTIONS, 'stabilization','3d') for r in options_rows(control,scope,monitor,cat)]
    matches = [r for r in rows if r['id']==row_id]
    if not matches: raise ValueError('row unavailable in scope')
    if not matches[0]['enabled']: raise ValueError('owner unavailable or action disabled')
    if row_id in ('help_prev','help_next'):
        out=control._help_toggle() if not control.help_visible else b''
        return (out or b'') + (control.on_message({'op':row_id}) or b'')
    if row_id == 'privacy_display':
        control.privacy.request(done=owner_callback(control,row_id,monitor,scope))
        return None
    if row_id.startswith('send_'):
        control.sender.request(row_id,monitor,done=owner_callback(control,row_id,monitor,scope))
        return None
    if row_id == 'stop':
        if value != 'confirmed': raise ValueError('Stop confirmation required')
        raise KeyboardInterrupt  # existing managed run unwinds through Stop/recovery
    if row_id == 'layout_reset' and value == 'confirmed':
        done=owner_callback(control,row_id,monitor,scope)
        if done is not None: control.options_output_done=done
        out=control.layout_reset() or b''
        if control.reset is None: out += control.layout_reset() or b''
        return out
    if row_id in ('video_browse','video_play','binds_toggle'):
        if not hasattr(control,'options_editor'): control.options_editor = NativeEditors(control)
        return control.options_editor.open({'video_browse':'file','video_play':'url','binds_toggle':'keybinds'}[row_id])
    if row_id in ('mode_follow', 'mode_locked'):
        out=control.select_desktop(i) or b''
        if not s.zoomed: out += control.on_message({'op':'zoom_in'}) or b''
        if s.maximized_mode != row_id[5:]: out += control.on_message({'op':'mode_next'}) or b''
        return out
    if row_id == 'stabilization':
        return control.on_message({'op':row_id,'payload':{'strength':_number(value,0,100)}})
    if row_id.startswith('immersive_'):
        v = immersive.immersive(s)
        if row_id in ('immersive_all','immersive_monitor'):
            if type(value) is not bool: raise ValueError('boolean required')
            if row_id == 'immersive_all': v.update({'global':value,'desktops':{}})
            else: v['desktops'][monitor] = value
        elif row_id == 'immersive_preset': v = immersive.with_preset(v,value)
        elif row_id == 'immersive_plane': v['convergence'] = _number(value,0,100)
        elif row_id == 'immersive_strength': v['strength'] = _number(value,0,immersive.STRENGTH_MAX)
        elif row_id == 'immersive_monitor_strength': v.setdefault('strengths',{})[monitor] = _number(value,0,immersive.STRENGTH_MAX)
        done=owner_callback(control,row_id,monitor,scope)
        out=control.set_immersive(v)
        run=getattr(control,'immersive_run',None)
        if done is not None:
            if run is None:
                if immersive.on_names(v,s.names): done(False,'3D engine owner unavailable')
                else:
                    try:
                        control.settings.save(s.settings())
                        done(True,'Saved for next 3D; no engine or live drawing requested')
                    except (OSError,ValueError,AttributeError) as exc: done(False,'Could not save 3D defaults: '+str(exc))
            else: run.request(control._immersive_wanted(),done=done,setting=v)
        return out
    if row_id == 'quality_deband' and value in ('False','True'):
        value = value == 'True'
    if row_id.startswith('quality_'):
        video = getattr(control,'options_video',None)
        if video is None: raise ValueError('video owner unavailable')
        return video._set_quality(quality.validate({**s.video_quality,row_id[8:]:value}),done=owner_callback(control,row_id,monitor,scope))
    if row_id == 'video_paste':
        from .xr_control import read_clipboard, clipboard_payload
        payload, error = clipboard_payload(read_clipboard())
        if error: raise ValueError(error)
        video = getattr(control, 'options_video', None)
        if video is None: raise ValueError('video owner unavailable')
        return video_request(control,'video_open',payload,row_id,monitor,scope)
    return control.on_message({'op':row_id}) or b''
