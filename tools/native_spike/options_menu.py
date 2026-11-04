"""Supervisor-owned options state. Native code only displays validated snapshots."""
import copy
import json
import math
import re
import time

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
    integer(value['focus'],-1,63); integer(value['scroll'],0,4096); integer(value['revision'],0,2**32-1)
    validate_rows(value['rows'])
    return copy.deepcopy(value)

def validate_intent(op, payload):
    if op not in OPS or type(payload) is not dict: raise ValueError('invalid options intent')
    required = {'options_open':{'monitor'},'options_close':set(),'options_scope':{'scope'},'options_category':{'category'},'options_focus':{'focus'},'options_scroll':{'delta'},'options_cancel':set()}.get(op, {'row_id','monitor','scope','revision','value'})
    if set(payload)!=required: raise ValueError('invalid options fields')
    if 'monitor' in payload: monitor(payload['monitor'])
    if 'scope' in payload and payload['scope'] not in SCOPES: raise ValueError('invalid scope')
    if 'category' in payload and payload['category'] not in CATEGORIES: raise ValueError('invalid category')
    if 'focus' in payload: integer(payload['focus'],-1,63)
    if 'delta' in payload: integer(payload['delta'],-4096,4096)
    if 'revision' in payload: integer(payload['revision'],0,2**32-1)
    if 'row_id' in payload: string(payload['row_id'],64)
    if 'value' in payload:
        v=payload['value']
        if type(v) is str: string(v,256)
        elif type(v) not in (bool,int,float) or (type(v) in (int,float) and not math.isfinite(v)): raise ValueError('invalid typed value')
    return dict(payload)

class OptionsMenu:
    def __init__(self, control, clock=time.monotonic, rows=None, apply=None):
        self.control,self.clock=control,clock
        self._rows,self._apply=rows,apply
        self.output=b''
        self.pending_ids=set()
        self.pending_since=0
        self.action_has_receipt=False
        self.focused=False
        self.confirmation=None
        self.child=self.capture=self.pending=None
        self.last_input=clock()
        self.data=dict(open=False,namespace=control.session,monitor=control.state.names[0],scope='monitor',category='placement',focus=-1,scroll=0,revision=0,status='idle',message='',rows=[])
    @property
    def keyboard_owned(self):
        return self.data['open'] and self.focused and not self.control.keys_open
    def _bump(self): self.data['revision']=(self.data['revision']+1) % 2**32
    def snapshot(self): return validate_snapshot(self.data)
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
        self.data.update(open=False,namespace=self.control.session,status='idle',message='',rows=[],focus=-1,scroll=0)
        self.focused=False; self.child=self.capture=self.pending=None; self.confirmation=None; self._bump()
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
        if not self.data['open']: return
        if self.confirmation is not None: return
        try:
            callback=self._rows
            if callback is None:
                from .options_actions import options_rows
                callback=options_rows
            editor=getattr(self.control,'options_editor',None)
            if editor is not None and editor.kind is not None:
                self.child=editor.kind
                self.data['scope']='workspace'
                rows=validate_rows(editor.rows())
            else:
                self.child=None
                rows=validate_rows(callback(self.control,self.data['scope'],self.data['monitor'],self.data['category']))
            rows=[r for r in rows if r['scope']==self.data['scope']]
            if rows!=self.data['rows']: self.data['rows']=rows; self._bump()
            if self.pending:
                rid,expected=self.pending
                row=next((r for r in rows if r['id']==rid),None)
                actual=None if row is None else row['value']
                equal = actual==expected
                if row is not None and row['kind']=='range':
                    try: equal=float(actual)==float(expected)
                    except (ValueError,TypeError): pass
                if row is not None and row['kind']=='action' and not self.action_has_receipt: equal=False
                if equal and not self.pending_ids:
                    self.data.update(status='applied',message=''); self.pending=None
                elif self.clock()-self.pending_since > 10:
                    self.data.update(status='error',message='Owner readback timed out'); self.pending=None
            if self.data['focus']>=len(rows): self.data['focus']=-1
        except Exception as exc:
            self.data.update(status='error',message=str(exc).encode('utf-8')[:256].decode('utf-8','ignore'))
    def tick(self):
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
            key=name[8:]; self.data[key]=fields[key]; self.data.update(focus=-1,scroll=0,status='idle'); self.pending=None; self.child=None; self._bump(); self.refresh(); return True
        if name=='options_focus':
            if fields['focus']>=len(self.data['rows']): return False
            self.data['focus']=fields['focus']; self.focused=True; return True
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
        if row['id'] in ('remove_monitor','layout_reset') and name!='options_confirm':
            self.confirmation=dict(fields); self.child='confirmation'
            warning='Evacuate windows to physical fallback; keep at least one monitor' if row['id']=='remove_monitor' else 'Reset remembered layout with window migration'
            self.data['rows']=[dict(id='confirmation.apply',label=warning,scope=self.data['scope'],kind='action',value='',enabled=True),dict(id='confirmation.cancel',label='Cancel',scope=self.data['scope'],kind='action',value='',enabled=True)]
            self.data['focus']=0; self._bump(); return True
        if row['kind']=='toggle' and type(value) is not bool: return False
        if row['kind']=='range' and (type(value) not in (int,float) or not row.get('minimum',value)<=value<=row.get('maximum',value)): return False
        if row['kind']=='choice' and value not in row.get('choices',[]): return False
        try:
            callback=self._apply
            if callback is None:
                from .options_actions import options_apply
                callback=options_apply
            result=callback(self.control,row['id'],value,fields['monitor'],fields['scope'])
            if result is False: raise ValueError('Owner rejected change')
            if isinstance(result,bytes): self.output += result
            self.pending_ids=set()
            if isinstance(result,bytes):
                for line in result.splitlines():
                    frame=json.loads(line)
                    if frame.get('op') not in ('options_menu','placement_menu','move_handle','overview_pointer','help','binds'):
                        self.pending_ids.add(frame['request_id'])
            self.action_has_receipt=bool(self.pending_ids)
            self.pending_since=self.clock()
            editor=getattr(self.control,'options_editor',None)
            if row['id'].startswith('editor.') or (editor is not None and editor.kind is not None):
                self.data.update(status='idle',message='')
                self._bump(); self.refresh(); return True
            self.pending=(row['id'],str(value).lower() if type(value) is bool else str(value))
            self.data.update(status='pending',message='Waiting for owner readback'); self._bump()
            return True
        except Exception as exc:
            self.data.update(status='error',message=str(exc).encode('utf-8')[:256].decode('utf-8','ignore')); return False
