"""Serialized controller policy. Adapters are injected so lifecycle tests stay offline.

Probe returns detection fields plus instance, monitors, installed, plugins, journals.
Store.load(model)/save(model, profile); spawn(action, consent_path, journal_path).
Units.alive(run_id)/control(run_id, session, op). No side effects before confirm.
"""
import copy
import os
from pathlib import Path
import secrets
import subprocess
from tools.native_spike.panel_consent import build_consent, write_consent, load_panel_consent, LUMA_MODE
from tools.native_spike.profiles import _check
from tools.native_spike.geometry import validate_placements
from tools.native_spike.view import DEFAULT_SETTINGS, USER_OPS, MODES, SPACING_STEP
from .detect import pick_fallback


class Controller:
    def __init__(self, clock, probe, spawn, units, store, consent_dir_fd, state_dir):
        self.clock,self.probe,self.spawn,self.units,self.store = clock,probe,spawn,units,store
        self.consent_fd,self.state_dir = consent_dir_fd,Path(state_dir)
        self.state='no-glasses'; self.pending=None; self.run=None; self.error=None
        self.observed={}; self.started=None; self.last_failure=None
        self.live={}; self.last_status=None; self.status_run=None
        self.tick()

    def snapshot(self):
        o=self.observed
        monitors=o.get('monitors',[])
        profile=self.store.load(o['model']) if o.get('model') else {}
        candidates=[m['name'] for m in monitors if pick_fallback([m],o.get('output'),None) is not None]
        return copy.deepcopy(dict(fallback=pick_fallback(monitors,o.get('output'),profile.get('fallback')),
            fallback_candidates=candidates, pending=self.pending,
            state=self.state, model=o.get('model'), output=o.get('output'), reason=self.error or self.last_failure or o.get('reason'),
            run_id=self.run.get('run_id') if self.run else None, journal=self.run.get('path') if self.run else None,
            capabilities=self.live.get('capabilities',list(USER_OPS)) if self.state=='running' else [],
            view=self.live.get('view',{}) if self.state=='running' else {},
            status=self.live.get('status',{}) if self.state=='running' else {},
            placements=self.live.get('placements') if self.state=='running' else None))

    def _state(self, state):
        if self.state != state: self.pending=None
        self.state=state
        if state != 'running':
            self.live={}; self.last_status=None; self.status_run=None

    def _poll_status(self, journal):
        now=self.clock()
        if self.status_run != journal['run_id']:
            self.live={}; self.last_status=None; self.status_run=journal['run_id']
        if self.last_status is not None and now-self.last_status<1: return
        self.last_status=now
        try: reply=self.units.control(journal['run_id'],journal['session'],'status')
        except (OSError,ValueError): reply=None
        self.live={k:copy.deepcopy(reply[k]) for k in ('capabilities','view','status','placements') if k in reply} if isinstance(reply,dict) else {}

    def tick(self):
        try: self._tick()
        except (OSError,ValueError,KeyError,TypeError,subprocess.SubprocessError) as exc:
            self.error=str(exc); self.pending=None; self._state('failed')

    def _tick(self):
        old=self.observed
        self.observed=self.probe()
        o=self.observed
        self.error=None
        if self.pending and any(old.get(k)!=o.get(k) for k in ('model','output','instance','monitors','installed','plugins','journals')):
            self.pending=None; self.state='idle'
        journals=o.get('journals',[])
        if len(journals)>1:
            self.error='multiple unfinished journals require CLI recovery'; self._state('cli-recovery'); return
        if journals:
            j=journals[0]; self.run=j
            try: self._consent(j)
            except (OSError,ValueError): self._state('cli-recovery'); return
            if self.units.alive(j['run_id']):
                if self.state == 'stopping': return
                # A scoped connection proves a listener; status replies additionally carry live measurements.
                if j.get('session') and self.units.control(j['run_id'],j['session'],None):
                    if self.state != 'stopping':
                        self._state('running'); self._poll_status(j)
                    return
                if not j.get('session'):
                    self.error=('Starting head tracking and waiting for calibration (up to 120 seconds). Keep the glasses still.'
                                if j.get('phase')=='tracking' else 'Preparing XR session…')
                    self._state('starting')
                else:
                    self.error='XR renderer control unavailable. Stop this session before starting again.'
                    self._state('failed')
                return
            if self.state=='starting' and self.started is not None and self.clock()-self.started<5: return
            if self.state not in ('confirming','starting'): self._state('recovery-needed')
            elif self.state=='starting' and (self.started is None or self.clock()-self.started>=5): self._state('recovery-needed')
            return
        if self.run and self.units.alive(self.run['run_id']): return
        if self.run:
            from tools.native_spike.ipc import read_private_json
            try:
                result=read_private_json(self.state_dir/(self.run['run_id']+'.result.json'))
            except FileNotFoundError:
                result=None
            if result is not None:
                if result.get('run_id')!=self.run['run_id']: raise ValueError('runner result identity mismatch')
                if result.get('error') or result.get('renderer_returncode') not in (None,0) or result.get('complete') is not True:
                    self.last_failure=result.get('error') or 'runner failed during '+str(result.get('phase','run'))
                self.run=None; self.started=None
                self._state('idle' if o.get('model') else 'no-glasses')
                return
        if self.state in ('starting','stopping') and self.started is not None and self.clock()-self.started<5: return
        if self.state=='starting':
            self.error='runner exited before creating a journal'; self._state('failed'); return
        self.run=None
        if self.state!='confirming': self._state('idle' if o.get('model') else 'no-glasses')

    def _consent(self,j):
        path=Path('/proc/self/fd')/str(self.consent_fd)/(j['run_id']+'.json')
        from tools.native_spike.ipc import read_private_json
        raw=read_private_json(path)
        consent=load_panel_consent(path,raw['instance'],raw['output'])
        if consent['run_id']!=j['run_id']: raise ValueError('consent identity mismatch')
        return consent

    def request(self,msg):
        try:
            if not isinstance(msg,dict): raise ValueError('invalid request')
            op=msg.get('op')
            result=self._request(op,msg)
            return {**self.snapshot(), **(result or {}), "ok": True}
        except (OSError,ValueError,KeyError,TypeError,subprocess.SubprocessError) as exc:
            return dict(ok=False,**self.snapshot(),error=str(exc))

    def _request(self,op,msg):
        if op=='status': return
        if op=='get_settings': return dict(settings=self.store.load(self.observed.get('model') or 'unknown'))
        if op=='cancel':
            if self.state!='confirming': raise ValueError('busy')
            self.pending=None; self._state('recovery-needed' if self.run else 'idle'); return
        if op=='set_settings':
            if self.state not in ('idle','running'): raise ValueError('busy')
            settings=msg['settings']
            if not isinstance(settings,dict): raise ValueError('invalid settings')
            if self.state=='running':
                # View settings travel as relative operations; placements are the one absolute, presentation-only message.
                if set(settings)-set(DEFAULT_SETTINGS)-{'placements'}: raise ValueError('resolution/settings locked while running')
                current=self.store.load(self._consent(self.run)['model'])
                target=_check({**current,**settings})
                if 'placements' in settings:
                    if set(settings)!={'placements'}: raise ValueError('send placements on their own')
                    payload={'desktops':validate_placements(settings['placements'])}
                    if not self.units.control(self.run['run_id'],self.run['session'],'placement',payload): raise ValueError('run control unavailable')
                    # The supervisor applies and saves them; the next status poll reports what it holds.
                    self.last_status=None
                    return dict(forwarded=True)
                operations=[]
                if 'mode' in settings:
                    operations += ['mode_next']*((MODES.index(target['mode'])-MODES.index(current['mode']))%len(MODES))
                if 'spacing' in settings:
                    delta=target['spacing']-current['spacing']
                    if delta%SPACING_STEP: raise ValueError('live spacing must use 20 pixel steps')
                    operations += ['spacing_up' if delta>0 else 'spacing_down']*(abs(delta)//SPACING_STEP)
                for key in ('cursor_follow','prediction'):
                    if key in settings and target[key]!=current[key]: operations.append(key)
                if len(operations)>8: raise ValueError('too many live operations; use smaller steps')
                for operation in operations:
                    if not self.units.control(self.run['run_id'],self.run['session'],operation): raise ValueError('run control unavailable')
                # The supervisor saves actual applied values; never claim these send-only operations applied.
                return dict(forwarded=True)
            model=self.observed['model']; profile=self.store.load(model)
            self.store.save(model,_check({**profile,**settings})); return
        if op=='begin_start':
            self.tick()
            if self.state!='idle': raise ValueError('busy')
            o=self.observed; p=self.store.load(o['model'])
            fallback=pick_fallback(o['monitors'],o['output'],p['fallback'])
            if not o['output'] or fallback in (None,'choose'): raise ValueError(o.get('reason') or 'choose a physical fallback output')
            if o.get('plugins'): raise ValueError('ownership plugin already loaded')
            scope=dict(run_id=secrets.token_hex(16),instance=o['instance'],output=o['output'],recovery_output=fallback,model=o['model'],
                tracking='xrlinuxdriver' if p['tracking'] else False,mode_switch=LUMA_MODE.copy() if p['mode_switch'] and o['model']=='luma-ultra' else False,
                desktops=p['desktops'],**o['installed'])
            if getattr(self,"scope_transform",None): scope=self.scope_transform(scope)
            build_consent(scope)
            self.last_failure=None
            self._state('confirming'); self.pending=dict(action='start',scope=scope,scope_id=secrets.token_hex(16))
            return copy.deepcopy(self.pending)
        if op=='begin_recover':
            if self.state!='recovery-needed': raise ValueError('busy or CLI recovery required')
            consent=self._consent(self.run)
            self._state('confirming'); self.pending=dict(action='recover',scope=consent,scope_id=secrets.token_hex(16),journal=self.run['path'])
            return copy.deepcopy(self.pending)
        if op in ('confirm_start','confirm_recover'):
            self.tick()
            action=op.removeprefix('confirm_')
            if self.state!='confirming' or not self.pending or msg.get('scope_id')!=self.pending['scope_id'] or action!=self.pending['action']: raise ValueError('stale scope')
            pending=self.pending; scope=copy.deepcopy(pending['scope'])
            if action=='start':
                optional=msg.get('optional',{})
                if not isinstance(optional,dict) or set(optional)-{'tracking','mode_switch'} or any(type(v) is not bool for v in optional.values()): raise ValueError('invalid optional scope')
                for k,v in optional.items():
                    if not v: scope[k]=False
                    elif scope[k] is False: raise ValueError('cannot expand confirmed scope')
                consent=build_consent(scope)
                name=write_consent(self.consent_fd,consent)
                self.run=dict(run_id=scope['run_id'])
            else: name=scope['run_id']+'.json'
            self._state('starting'); self.pending=None; self.started=self.clock()
            try: self.spawn(action,str(self.state_dir/name),pending.get('journal'))
            except Exception as exc:
                self.error=str(exc); self._state('failed'); raise ValueError(self.error) from exc
            return
        if op in ('stop','view_op'):
            if self.state!='running' and not (op=='stop' and self.run and self.state in ('starting','failed')): raise ValueError('busy')
            operation='stop' if op=='stop' else msg.get('operation',msg.get('view_op'))
            if isinstance(operation,dict): operation=operation.get('op')
            if op=='view_op' and operation not in USER_OPS: raise ValueError('invalid view operation')
            if op=='stop': self.units.stop(self.run['run_id']); self._state('stopping'); self.started=self.clock()
            elif not self.units.control(self.run['run_id'],self.run['session'],operation): raise ValueError('run control unavailable')
            return
        raise ValueError('unknown operation')
