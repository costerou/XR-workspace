"""Supervisor-owned options state. Native code only displays validated snapshots."""
import copy
import json
import math
import re
import time
import threading
from collections import deque

CATEGORIES = ('placement','output','3d','stabilization','layout','environment','view','video','input','keybinds','windows','privacy','help','session')
SCOPES = ('monitor', 'workspace')
KINDS = ('action','toggle','range','choice','text','readonly')
OPS = tuple('options_'+n for n in ('open','close','scope','category','focus','scroll','activate','set','confirm','cancel'))
ROW_KEYS = {'id','label','scope','kind','value','minimum','maximum','step','unit','enabled','choices'}
SNAP_KEYS = {'open','namespace','monitor','scope','category','focus','scroll','revision','status','message','rows'}

def string(value, limit):
    if type(value) is not str or len(value.encode('utf-8')) > limit: raise ValueError('invalid options string')
    return value

def integer(value, low, high):
    if type(value) is not int or not low <= value <= high: raise ValueError('invalid options integer')

def monitor(value):
    string(value,32)
    if re.fullmatch(r'XR-[1-9][0-9]*', value) is None: raise ValueError('invalid options monitor')

def validate_rows(rows):
    if type(rows) is not list or len(rows)>64: raise ValueError('invalid options rows')
    seen=set()
    for row in rows:
        if type(row) is not dict or set(row)-ROW_KEYS or not {'id','label','scope','kind','value','enabled'} <= set(row): raise ValueError('invalid options row')
        for key,limit in (('id',64),('label',96),('value',256),('unit',16)):
            if key in row: string(row[key],limit)
        if not row['id'] or row['id'] in seen: raise ValueError('duplicate options id')
        seen.add(row['id'])
        if row['scope'] not in SCOPES or row['kind'] not in KINDS or type(row['enabled']) is not bool: raise ValueError('invalid options row enum')
        for key in ('minimum','maximum','step'):
            if key in row and (type(row[key]) not in (int,float) or not math.isfinite(row[key])): raise ValueError('invalid options number')
        choices=row.get('choices',[])
        if type(choices) is not list or len(choices)>32: raise ValueError('invalid choices')
        for choice in choices: string(choice,96)
    return copy.deepcopy(rows)

def validate_snapshot(value):
    if type(value) is not dict or set(value)!=SNAP_KEYS: raise ValueError('invalid options snapshot')
    if type(value['open']) is not bool: raise ValueError('invalid open')
    string(value['namespace'],64); monitor(value['monitor']); string(value['message'],256)
    if value['scope'] not in SCOPES or value['category'] not in CATEGORIES or value['status'] not in ('idle','pending','applied','error'): raise ValueError('invalid options enum')
    integer(value['focus'],-4,63); integer(value['scroll'],0,4096); integer(value['revision'],0,2**32-1)
    validate_rows(value['rows'])
    return copy.deepcopy(value)

def validate_intent(op, payload):
    if op not in OPS or type(payload) is not dict: raise ValueError('invalid options intent')
    required = {'options_open':{'monitor'},'options_close':set(),'options_scope':{'scope'},'options_category':{'category'},'options_focus':{'focus'},'options_scroll':{'delta'},'options_cancel':set()}.get(op, {'row_id','monitor','scope','revision','value'})
    if set(payload)!=required: raise ValueError('invalid options fields')
    if 'monitor' in payload: monitor(payload['monitor'])
    if 'scope' in payload and payload['scope'] not in SCOPES: raise ValueError('invalid scope')
    if 'category' in payload and payload['category'] not in CATEGORIES: raise ValueError('invalid category')
    if 'focus' in payload: integer(payload['focus'],-4,63)
    if 'delta' in payload: integer(payload['delta'],-4096,4096)
    if 'revision' in payload: integer(payload['revision'],0,2**32-1)
    if 'row_id' in payload: string(payload['row_id'],64)
    if 'value' in payload:
        v=payload['value']
        if type(v) is str: string(v,256)
        elif type(v) not in (bool,int,float) or (type(v) in (int,float) and not math.isfinite(v)): raise ValueError('invalid typed value')
    return dict(payload)

def validate_owner_readback(value):
    """Bounded production reports. No arbitrary nesting or non-JSON numeric values."""
    if type(value) is not dict or set(value)!={'video','immersive','outputs'}: raise ValueError('invalid owner readback')
    def walk(node, depth=0):
        if depth>6: raise ValueError('owner readback nesting')
        if node is None or type(node) is bool: return
        if type(node) in (int,float):
            if not math.isfinite(node): raise ValueError('nonfinite readback')
        elif type(node) is str: string(node,1024)
        elif type(node) is list:
            if len(node)>64: raise ValueError('readback array bound')
            for child in node: walk(child,depth+1)
        elif type(node) is dict:
            if len(node)>64: raise ValueError('readback object bound')
            for key,child in node.items(): string(key,64);walk(child,depth+1)
        else: raise ValueError('invalid readback type')
    walk(value)
    if len(json.dumps(value,ensure_ascii=False,allow_nan=False).encode())>16000: raise ValueError('owner readback bound')
    if value['video'] is not None and type(value['video']) is not dict: raise ValueError('invalid video readback')
    im=value['immersive']
    if im is not None:
        if type(im) is not dict or set(im)!={'available','enabled','physical_sbs','error','frames','depth_updates','params','desktops','engine','engine_error'}: raise ValueError('invalid 3D readback')
        for key in ('available','enabled','physical_sbs'):
            if type(im[key]) is not bool: raise ValueError('invalid 3D flag')
        for key in ('frames','depth_updates'): integer(im[key],0,2**64-1)
        for key in ('error','engine_error'): string(im[key],256)
        if im['engine'] is not None and type(im['engine']) is not dict: raise ValueError('invalid engine report')
        params=im['params']
        if type(params) is not dict or set(params)!={'strength','convergence','near_deg','far_deg','text_keep'}: raise ValueError('invalid depth params')
        for key,hi in (('strength',2),('convergence',1),('near_deg',180),('far_deg',180),('text_keep',1)):
            v=params[key]
            if type(v) not in (int,float) or not 0<=v<=hi: raise ValueError('invalid depth value')
        if type(im['desktops']) is not list or len(im['desktops'])>4: raise ValueError('invalid depth desktops')
        seen=set()
        for d in im['desktops']:
            if type(d) is not dict or set(d)!={'monitor','configured','depth_ready','gain','drawing','strength'}: raise ValueError('invalid depth desktop')
            monitor(d['monitor'])
            if d['monitor'] in seen: raise ValueError('duplicate depth monitor')
            seen.add(d['monitor'])
            for key in ('configured','depth_ready','drawing'):
                if type(d[key]) is not bool: raise ValueError('invalid depth flag')
            for key,hi in (('gain',1),('strength',2)):
                if type(d[key]) not in (int,float) or not 0<=d[key]<=hi: raise ValueError('invalid depth gain')
    video=value['video']
    if video is not None:
        if type(video) is not dict: raise ValueError('invalid video report')
        for key in ('opened','frames_drawn','card_shown'):
            if key in video: integer(video[key],0,2**64-1)
        for key in ('format_state','format_applied'):
            if key in video and video[key] is not None:
                fmt=video[key]
                if type(fmt) is not dict or set(fmt)!={'projection','stereo','fov'} or fmt['projection'] not in ('360','180','fisheye') or fmt['stereo'] not in ('mono','sbs','tb') or type(fmt['fov']) not in (int,float) or not 180<=fmt['fov']<=220: raise ValueError('invalid actual video format')
        if 'quality_readback' in video:
            quality=video['quality_readback'];filters=('catmull-rom','sharp','bilinear','nearest')
            if type(quality) is not dict or set(quality)!={'filter_requested','deband_requested','filter_applied','deband_applied'} or quality['filter_requested'] not in filters or (quality['filter_applied'] is not None and quality['filter_applied'] not in filters) or type(quality['deband_requested']) is not bool or (quality['deband_applied'] is not None and type(quality['deband_applied']) is not bool): raise ValueError('invalid actual video quality')
        for key in ('playback',):
            if key in video:
                pb=video[key]
                if type(pb) is not dict: raise ValueError('invalid playback report')
                if pb.get('state') not in ('off','loading','playing','paused','ended','error'): raise ValueError('invalid playback state')
                if pb.get('paused') is not None and type(pb['paused']) is not bool: raise ValueError('invalid pause state')
                for k in ('position_s','duration_s'):
                    if pb.get(k) is not None and (type(pb[k]) not in (int,float) or pb[k]<0): raise ValueError('invalid playback time')
                for k in ('revision','command_replies','command_errors'): integer(pb[k],0,2**64-1)
                for k in ('command_error','error'): string(pb[k],256)
    outputs=value['outputs']
    if type(outputs) is not list or len(outputs)>4: raise ValueError('invalid outputs readback')
    seen=set()
    for output in outputs:
        if type(output) is not dict or set(output)!={'name','ready','width','height'}: raise ValueError('invalid capture readback')
        monitor(output['name'])
        if output['name'] in seen: raise ValueError('duplicate captured output')
        seen.add(output['name'])
        for key in ('width','height'): integer(output[key],0,32768)
        for key in ('ready','stopped'):
            if key in output and type(output[key]) is not bool: raise ValueError('invalid capture readiness')
    return copy.deepcopy(value)


validate_monitor = monitor

class OptionsMenu:
    def __init__(self, control, clock=time.monotonic, rows=None, apply=None):
        self.control,self.clock=control,clock
        self._rows,self._apply=rows,apply
        self.output=b''
        self._owner_lock=threading.Lock()
        self._owner_next=0
        self._owner_active=None
        self._owner_requests={}
        self._owner_results=deque(maxlen=64)
        self._owner_result=None
        self._editor_error=False
        self._owner_epoch=0
        self.pending_ids=set()
        self.pending_since=0
        self.action_has_receipt=False
        self.focused=False
        self.confirmation=None
        self.child=self.capture=self.pending=None
        self.last_input=clock()
        self.data=dict(open=False,namespace=control.session,monitor=control.state.names[0],scope='monitor',category='placement',focus=-1,scroll=0,revision=0,status='idle',message='',rows=[])
    @property
    def visible(self):
        return self.data['open'] and not self.control.keys_open

    @property
    def keyboard_owned(self):
        return self.data['open'] and self.focused and not self.control.keys_open
    def _bump(self): self.data['revision']=(self.data['revision']+1) % 2**32
    def snapshot(self): return validate_snapshot(self.data)
    @staticmethod
    def _detail(detail):
        return str(detail).encode('utf-8', 'replace')[:256].decode('utf-8', 'ignore')

    def _invalidate_owner(self):
        with self._owner_lock:
            self._owner_epoch+=1
            self._owner_active=None
            self._owner_requests.clear()
            self._owner_results.clear()
        self._owner_result=None
        self.pending_ids.clear()

    def begin_owner(self, row_id, monitor, scope):
        """Supervisor-loop API. Call before invoking an owner; never a receipt."""
        string(row_id,64); validate_monitor(monitor)
        if scope not in SCOPES or monitor not in self.control.state.names:
            raise ValueError('stale owner target')
        self._invalidate_owner()
        with self._owner_lock:
            self._owner_next+=1
            token=self._owner_next
            self._owner_requests[token]=dict(row_id=row_id,monitor=monitor,scope=scope,
                namespace=self.control.session,revision=self.data['revision'],epoch=self._owner_epoch,queued=False)
            self._owner_active=token
        self.data.update(status='pending',message='Waiting for owner readback')
        self._bump()
        return token

    def retain_owner_frames(self, token, frames):
        """Loop-thread transport hook for frames produced after options_apply.

        Bind actual emitted IDs to the exact owner request before its callback
        may complete. This registers expectations only, never renderer receipt.
        """
        if type(token) is not int: return False
        with self._owner_lock:
            request=self._owner_requests.get(token)
            if token!=self._owner_active or request is None or request['namespace']!=self.control.session or request['epoch']!=self._owner_epoch or not self._owner_target_exists(request): return False
        if not isinstance(frames,(bytes,bytearray)) or len(frames)>512*1024: raise ValueError('invalid owner frame batch')
        from .ipc import strict_json
        ids=set()
        lines=frames.splitlines()
        if len(lines)>256: raise ValueError('owner frame batch limit')
        for line in lines:
            frame=strict_json(line)
            if type(frame) is not dict or set(frame)!={'schema','run_id','session','request_id','op','payload'} or type(frame['schema']) is not int or frame['schema']!=1 or type(frame['payload']) is not dict: raise ValueError('invalid owner frame envelope')
            if frame['run_id']!=self.control.run_id or frame['session']!=request['namespace']: raise ValueError('stale owner frame envelope')
            integer(frame['request_id'],1,2**64-1);string(frame['op'],64)
            if frame['op'] not in ('options_menu','placement_menu','move_handle','overview_pointer','help','binds'):
                ids.add(frame['request_id'])
        if len(self.pending_ids|ids)>256: raise ValueError('too many owner frame receipts')
        self.pending_ids.update(ids)
        self.action_has_receipt=bool(self.pending_ids)
        return True

    def finish_owner(self, token, success, detail=''):
        """Worker-safe bounded enqueue; UI changes occur only in tick/refresh."""
        if type(token) is not int or type(success) is not bool: return False
        detail=self._detail(detail)
        with self._owner_lock:
            request=self._owner_requests.get(token)
            if request is None or request['queued'] or len(self._owner_results)>=64: return False
            request['queued']=True
            self._owner_results.append((token,success,detail))
        wake=getattr(self.control,'overview_pointer',None)
        if wake is not None: wake._poke()
        return True

    def _owner_target_exists(self, request):
        if request['monitor'] in self.control.state.names: return True
        # Removal pre-releases the renderer capture before the authoritative
        # broker transaction. That prospective list is not output-loss readback.
        before=getattr(self.control,'_desktops_before',None)
        planned=bool(getattr(self.control.state,'desktops_busy',False) and before and
                     request['monitor'] in before[0] and request['row_id'] in
                     ('remove_monitor','add_monitor','resolution','scale','ultrawide','layout_reset'))
        if planned and request['row_id']=='remove_monitor': request['expected_removal']=True
        # A verified removal can finish before its renderer receipts; retain
        # just that correlated transaction, never an unrelated lost target.
        return planned or bool(request.get('expected_removal') and request['queued'])

    def _drain_owner(self):
        with self._owner_lock:
            results=list(self._owner_results);self._owner_results.clear()
            for token,success,detail in results:
                request=self._owner_requests.get(token)
                if token!=self._owner_active or request is None: continue
                if request['namespace']!=self.control.session or not self._owner_target_exists(request) or request['epoch']!=self._owner_epoch:
                    self._owner_requests.pop(token,None);self._owner_active=None
                    continue
                self._owner_result=(success,detail)
            request=self._owner_requests.get(self._owner_active)
            if request is not None and (request['namespace']!=self.control.session or not self._owner_target_exists(request)):
                self._owner_requests.clear();self._owner_active=None;self._owner_result=None
        if self._owner_result is not None:
            self._editor_error=False
            success,detail=self._owner_result
            if not success or not self.pending_ids:
                self.data.update(status='applied' if success else 'error',message=detail)
                self.pending=None
                with self._owner_lock:
                    self._owner_requests.clear();self._owner_active=None
                self._owner_result=None

    def drain_completions(self):
        """Supervisor-loop barrier after consuming owner events; no invented status."""
        self._drain_owner()

    def refuse(self, request_id, op, detail):
        if request_id not in self.pending_ids: return False
        if self._owner_active is not None:
            with self._owner_lock:
                token=self._owner_active
                request=self._owner_requests.get(token)
                if request is None: return False
                # Native refusal must dominate a successful backend callback
                # that is queued or awaiting this very renderer receipt.
                self._owner_results=deque((r for r in self._owner_results if r[0]!=token),maxlen=64)
                request['queued']=True
                self._owner_results.append((token,False,self._detail(detail)))
            return True
        self.pending_ids.clear();self.pending=None
        self.data.update(status='error',message=self._detail(detail))
        return True

    def acknowledge(self, request_id):
        self.pending_ids.discard(request_id)
    def open(self, monitor):
        if monitor not in self.control.state.names: return False
        self.close('reopen')
        self.data.update(open=True,monitor=monitor,namespace=self.control.session,status='idle',message='',focus=-1,scroll=0)
        self.focused=True; self.last_input=self.clock(); self._bump(); self.refresh()
        return True
    def close(self, reason='closed'):
        editor=getattr(self.control,'options_editor',None)
        if editor is not None and editor.kind is not None: self.output += editor.close() or b''
        preserve=reason=='native child'
        if not preserve: self._invalidate_owner()
        self.data.update(open=False,namespace=self.control.session,rows=[],focus=-1,scroll=0)
        if not preserve: self.data.update(status='idle',message=''); self.pending=None
        self.focused=False; self.child=self.capture=None; self.confirmation=None; self._bump()
    def desktop_press(self): self.focused=False
    def escape(self):
        if not self.keyboard_owned: return False
        editor=getattr(self.control,'options_editor',None)
        if editor is not None and editor.kind is not None:
            self.output += editor.close() or b''
            self.child=None; self.refresh()
        elif self.child is not None or self.capture is not None:
            self.child=self.capture=None
            self.confirmation=None
            self.refresh()
        else: self.close('escape')
        return True
    def refresh(self):
        self._drain_owner()
        if not self.data['open']: return
        if self.confirmation is not None: return
        try:
            callback=self._rows
            if callback is None:
                from .options_actions import options_rows
                callback=options_rows
            editor=getattr(self.control,'options_editor',None)
            if editor is not None and editor.kind=='keybinds' and not self.control.keys_open:
                self.output += editor.close() or b''
                editor.kind=None
            if editor is not None and editor.kind is not None:
                self.child=editor.kind
                self.data['scope']='workspace'
                rows=validate_rows(editor.rows())
            else:
                self.child=None
                rows=validate_rows(callback(self.control,self.data['scope'],self.data['monitor'],self.data['category']))
            rows=[r for r in rows if r['scope']==self.data['scope']]
            if rows!=self.data['rows']: self.data['rows']=rows; self._bump()
            if self.pending and self._owner_active is None:
                rid,expected=self.pending
                row=next((r for r in rows if r['id']==rid),None)
                actual=None if row is None else row['value']
                equal = actual==expected
                if row is not None and row['kind']=='range':
                    try: equal=float(actual)==float(expected)
                    except (ValueError,TypeError): pass
                if row is not None and row['kind']=='action': equal=False
                if equal and not self.pending_ids:
                    self.data.update(status='applied',message=''); self.pending=None

            if self.data['focus']>=len(rows): self.data['focus']=-1
        except Exception as exc:
            self.data.update(status='error',message=str(exc).encode('utf-8')[:256].decode('utf-8','ignore'))
    def tick(self):
        self._drain_owner()
        if not self.data['open']: return
        if self.data['monitor'] not in self.control.state.names or self.data['namespace']!=self.control.session: self.close('session changed')
        elif self.capture is None and self.clock()-self.last_input>=30: self.close('idle')
        else: self.refresh()
    def intent(self, name, **fields):
        try: validate_intent(name,fields)
        except (ValueError,TypeError,UnicodeError): return False
        if name=='options_open': return self.open(fields['monitor'])
        if not self.data['open']: return False
        self.last_input=self.clock()
        if name=='options_close': self.close(); return True
        if name=='options_cancel': return self.escape()
        if name in ('options_scope','options_category'):
            key=name[8:]; self.data[key]=fields[key]; self.data.update(focus=self.data['focus'] if self.data['focus']<0 else -3,scroll=0); self.child=None; self._bump(); self.refresh(); return True
        if name=='options_focus':
            if fields['focus']>=len(self.data['rows']): return False
            self.data['focus']=fields['focus']; self.focused=True
            editor=getattr(self.control,'options_editor',None)
            if editor is not None:
                row=self.data['rows'][fields['focus']] if fields['focus']>=0 else None
                editor.text_field=row['id'] if row and row['id'] in ('editor.text','editor.path') else None
                select=getattr(editor,'select_candidate',None)
                if row is not None and select is not None and select(row['id']): self.refresh()
            if fields['focus']>=0:
                top=fields['focus']*38
                self.data['scroll']=min(max(self.data['scroll'],top+38-304),top)
            return True
        if name=='options_scroll':
            self.data['scroll']=min(max(self.data['scroll']+fields['delta'],0),min(4096,max(0,len(self.data['rows'])*38-304))); return True
        if fields['revision']!=self.data['revision'] or fields['monitor']!=self.data['monitor'] or fields['monitor'] not in self.control.state.names or fields['scope']!=self.data['scope']: return False
        row=next((r for r in self.data['rows'] if r['id']==fields['row_id']),None)
        if row is None or not row['enabled'] or row['kind']=='readonly': return False
        value=fields['value']
        if row['id']=='confirmation.cancel':
            self.confirmation=None; self.child=None; self.refresh(); return True
        if row['id']=='confirmation.apply':
            original=self.confirmation
            self.confirmation=None; self.child=None
            self.refresh()
            if original is None: return False
            original['revision']=self.data['revision']
            return self.intent('options_confirm',**original)
        if row['id'] in ('remove_monitor','layout_reset','stop') and name!='options_confirm':
            self.confirmation=dict(fields); self.child='confirmation'
            warning={'remove_monitor':'Evacuate windows to physical fallback; keep at least one monitor','layout_reset':'Reset remembered layout with window migration','stop':'Stop XR and return windows to physical fallback'}[row['id']]
            self.data['rows']=[dict(id='confirmation.apply',label=warning,scope=self.data['scope'],kind='action',value='',enabled=True),dict(id='confirmation.cancel',label='Cancel',scope=self.data['scope'],kind='action',value='',enabled=True)]
            self.data['focus']=0; self._bump(); return True
        if row['kind']=='toggle' and type(value) is not bool: return False
        if row['kind']=='range' and (type(value) not in (int,float) or not row.get('minimum',value)<=value<=row.get('maximum',value)): return False
        if row['kind']=='choice' and value not in row.get('choices',[]): return False
        try:
            if name=='options_confirm' and row['id'] in ('remove_monitor','layout_reset','stop'): value='confirmed'
            callback=self._apply
            if callback is None:
                from .options_actions import options_apply
                callback=options_apply
            if not row['id'].startswith('editor.') and row['id'] not in ('video_browse','video_play','binds_toggle'):
                self._invalidate_owner()
            result=callback(self.control,row['id'],value,fields['monitor'],fields['scope'])
            if result is False: raise ValueError('Owner rejected change')
            if isinstance(result,bytes): self.output += result
            # Preserve callbacks across child transitions. Receipts are necessary
            # for native frames, but can never complete an action on their own.
            if self._owner_active is None:
                self.pending_ids=set()
            if isinstance(result,bytes):
                for line in result.splitlines():
                    frame=json.loads(line)
                    if frame.get('op') not in ('options_menu','placement_menu','move_handle','overview_pointer','help','binds'):
                        self.pending_ids.add(frame['request_id'])
            self.action_has_receipt=bool(self.pending_ids)
            self.pending_since=self.clock()
            editor=getattr(self.control,'options_editor',None)
            is_editor=row['id'].startswith('editor.') or (editor is not None and editor.kind is not None)
            if is_editor and self._editor_error and self._owner_active is None:
                self.data.update(status='idle',message='')
                self._editor_error=False
            if not is_editor or self._owner_active is not None:
                self.pending=(row['id'],str(value).lower() if type(value) is bool else str(value))
                self.data.update(status='pending',message='Waiting for owner readback')
            self._bump()
            if (self._owner_active is not None and getattr(self.control.state,'desktops_busy',False)) or getattr(self.control.state,'menu_open',False) or getattr(self.control.state,'zoomed',False) or getattr(self.control,'video_active',False) or getattr(self.control,'help_visible',False):
                self.close('native child')
                return True
            self.refresh()
            return True
        except Exception as exc:
            self._editor_error=row['id'].startswith('editor.')
            self.data.update(status='error',message=str(exc).encode('utf-8')[:256].decode('utf-8','ignore')); return False
