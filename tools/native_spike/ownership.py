"""Private schema-1 lifecycle wire over the verified, bounded Hyprland transport.

No plugin loading, name-based deletion, raw-command forwarding or epoch adoption.
Unknown UUIDs are unproven even after a fresh hello (including supervisor restart).
"""
import json
import math
import uuid
from .hyprland import OwnershipBlocked, LIMIT
from .ipc import strict_json, token


class OwnershipBrokerClient:
    def __init__(self, transport, run_id, session, approved_physical):
        self.transport = transport
        self.client = transport  # Verified raw peer used only by the existing launcher.
        self.run_id, self.session = token(run_id), token(session)
        self.approved_physical = token(approved_physical)
        self.epoch = None
        self.fallback = None
        self.request_id = 0

    def verified_session(self):
        observed = self.transport.verified_session()
        if observed != {'id': self.session}: raise OwnershipBlocked('compositor session changed')
        return observed

    def _call(self, op, payload):
        self.verified_session()
        self.request_id += 1
        if self.request_id > 2147483647: raise OwnershipBlocked('request capacity')
        envelope = {'schema': 1, 'session': self.session, 'run_id': self.run_id,
                    'request_id': self.request_id, 'epoch': self.epoch or 'unknown',
                    'op': op, 'payload': payload}
        wire = 'j/xrspike ' + json.dumps(envelope, allow_nan=False, separators=(',', ':'), ensure_ascii=True)
        if len(wire) > 8194: raise ValueError('broker request exceeds 8 KiB')
        raw = self.transport._request(wire)
        if len(raw) > LIMIT: raise ValueError('broker reply exceeds 1 MiB')
        reply = strict_json(raw)
        fields = {'schema','session','run_id','request_id','epoch','result'}
        if not isinstance(reply, dict) or set(reply) != fields: raise OwnershipBlocked('missing/invalid ownership broker')
        for key in ('schema','session','run_id','request_id'):
            if type(reply[key]) is not type(envelope[key]) or reply[key] != envelope[key]:
                raise OwnershipBlocked('broker identity/reply mismatch')
        epoch = token(reply['epoch'])
        if self.epoch is not None and self.epoch != epoch: raise OwnershipBlocked('broker epoch lost; preserve journal')
        if op != 'hello' and self.epoch is None: raise OwnershipBlocked('handshake required')
        if not isinstance(reply['result'], dict): raise OwnershipBlocked('invalid broker result')
        self.verified_session()
        if op == 'hello': self.epoch = epoch
        return reply['result']

    def require_output_ownership(self):
        if self.epoch is not None: return
        try:
            result = self._call('hello', {'fallback_name': self.approved_physical})
            if set(result) != {'qualified','fallback'} or result['qualified'] is not True:
                raise OwnershipBlocked('broker handshake not qualified')
            self.fallback = token(result['fallback'])
        except (ValueError, OSError, TypeError, KeyError):
            self.epoch = None
            self.fallback = None
            raise

    def inspect(self, identity):
        if not isinstance(identity, str) or str(uuid.UUID(identity)) != identity: raise ValueError('invalid UUID')
        self.require_output_ownership()
        result = self._call('inspect', {'id': identity})
        self._identity(result, identity)
        return result

    @staticmethod
    def _identity(result, identity):
        if set(result) != {'id','name','state','physical','observation','fallback_usable'} or result['id'] != identity or result['physical'] is not False:
            raise OwnershipBlocked('unproven broker identity')
        token(result['name'])
        if result['state'] not in ('pending','failed','live','invalid','dead','blocked') or type(result['fallback_usable']) is not bool:
            raise OwnershipBlocked('invalid lifecycle')

    def create_journaled(self, specs, journal):
        if journal.run_id != self.run_id or journal.session != self.session: raise OwnershipBlocked('journal identity mismatch')
        self.require_output_ownership()
        # Never recreate a run whose intents may belong to an earlier broker epoch.
        with journal.transaction():
            if journal.operations: raise OwnershipBlocked('existing run requires recovery, not creation')
        result = []
        for spec in specs:
            oid = journal.prepare({'kind':'output', 'id':spec['id'], 'name':spec['name'],
                                   'index':spec['index'], 'before':None})
            self._call('create', spec)  # A receipt is not readback.
            observed = self.inspect(spec['id'])
            self.verify(observed, spec)
            journal.confirm(oid, {'id':spec['id'], 'name':spec['name'], 'physical':False})
            result.append(spec['name'])
        return result

    @staticmethod
    def verify(result, spec):
        if result['state'] != 'live' or result['name'] != spec['name'] or not result['fallback_usable']:
            raise OwnershipBlocked('creation incomplete')
        o = result['observation']
        if not isinstance(o, dict): raise OwnershipBlocked('missing readback')
        for key in ('width','height','refresh_millihz','scale','x','y','workspace'):
            value = o.get(key)
            if type(value) not in (int,float) or not math.isfinite(value) or value != spec[key]:
                raise OwnershipBlocked('actual geometry/workspace mismatch')
        if o.get('enabled') is not True or o.get('dpms') is not True or o.get('mirrored') is not False or type(o.get('transform')) is not int or o['transform'] != 0:
            raise OwnershipBlocked('unusable output')
        workspaces = o.get('workspaces')
        if (workspaces != [{'id':spec['workspace'], 'name':spec['workspace_name'], 'special':False}]
                or type(workspaces[0]['id']) is not int or workspaces[0]['special'] is not False):
            raise OwnershipBlocked('workspace placement mismatch')
        windows = o.get('windows')
        if not isinstance(windows,list) or any(not isinstance(w,dict) or type(w.get('workspace')) is not int or w.get('workspace') != spec['workspace'] or w.get('pinned') is not False for w in windows):
            raise OwnershipBlocked('unsupported or unreachable mapped window')

    def evacuate_remove(self, output_id, output_name):
        observed = self.inspect(output_id)
        if observed['name'] != output_name: raise OwnershipBlocked('output name/UUID mismatch')
        reply = self._call('evacuate_remove', {'id':output_id, 'name':output_name, 'fallback':self.fallback})
        self._identity(reply, output_id)
        if reply['name'] != output_name or reply['state'] != 'dead' or reply['observation'] is not None or reply['fallback_usable'] is not True: return False
        # Separate registry readback before the journal can become done.
        final = self.inspect(output_id)
        return final['name'] == output_name and final['state'] == 'dead' and final['observation'] is None and final['fallback_usable'] is True

    def query(self, kind):
        if kind == 'session': return self.verified_session()
        raise OwnershipBlocked('broker recovery uses handle transaction; settings/workspace mutations disabled')

    def mutate(self, command):
        raise OwnershipBlocked('no raw mutations through broker')
