"""Private schema-1 lifecycle wire over the verified, bounded Hyprland transport.

No plugin loading, name-based deletion, raw-command forwarding or epoch adoption.
Unknown UUIDs are unproven even after a fresh hello (including supervisor restart).
"""
import json
import math
import uuid
from .hyprland import OwnershipBlocked, LIMIT
from .ipc import strict_json, token


def _brief(value, items=8, limit=300):
    """Bounded, single-line ASCII rendering of compositor-supplied readback for diagnostics."""
    if isinstance(value, list) and len(value) > items: value = value[:items] + [f'... {len(value) - items} more']
    text = json.dumps(value, ensure_ascii=True, sort_keys=True, default=str)
    return text if len(text) <= limit else text[:limit - 3] + '...'


class OwnershipBrokerClient:
    SCHEMA = 1
    HELLO = 'hello'

    def __init__(self, transport, run_id, session, approved_physical):
        self.transport = transport
        self.client = transport  # Verified raw peer used only by the existing launcher.
        self.run_id, self.session = token(run_id), token(session)
        self.approved_physical = token(approved_physical)
        self.epoch = None
        self.fallback = None
        self.request_id = 0

    @classmethod
    def from_journal(cls, transport, journal):
        """Restore authority, never discover/adopt a replacement epoch or fallback."""
        from .journal import validate_broker
        with journal.transaction():
            context = validate_broker(journal.broker)
        if 'mode' in context: raise OwnershipBlocked('isolated journal requires isolated recovery')
        if transport.instance != context['instance']:
            raise OwnershipBlocked('saved compositor instance mismatch')
        client = cls(transport, journal.run_id, journal.session, context['recovery_output'])
        client.epoch, client.fallback = context['epoch'], context['fallback']
        client.verified_session()
        return client

    def verified_session(self):
        observed = self.transport.verified_session()
        if observed != {'id': self.session}: raise OwnershipBlocked('compositor session changed')
        return observed

    def _call(self, op, payload):
        self.verified_session()
        self.request_id += 1
        if self.request_id > 2147483647: raise OwnershipBlocked('request capacity')
        envelope = {'schema': self.SCHEMA, 'session': self.session, 'run_id': self.run_id,
                    'request_id': self.request_id, 'epoch': self.epoch or 'unknown',
                    'op': op, 'payload': self._payload(payload)}
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
        if op != self.HELLO and self.epoch is None: raise OwnershipBlocked('handshake required')
        if not isinstance(reply['result'], dict): raise OwnershipBlocked('invalid broker result')
        self.verified_session()
        if op == self.HELLO: self.epoch = epoch
        return reply['result']

    def _payload(self, payload):
        return payload

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
        for key in ('width','height','refresh_millihz','scale','x','y'):
            value = o.get(key)
            if type(value) not in (int,float) or not math.isfinite(value) or value != spec[key]:
                raise OwnershipBlocked('actual geometry/workspace mismatch')
        if o.get('enabled') is not True or o.get('dpms') is not True or o.get('mirrored') is not False or type(o.get('transform')) is not int or o['transform'] != 0:
            raise OwnershipBlocked('unusable output')
        # Workspaces belong to Omarchy/the user: any regular ones, windows unpinned on them.
        workspaces = o.get('workspaces')
        if (not isinstance(workspaces, list) or not workspaces or any(
                not isinstance(w, dict) or set(w) != {'id', 'name', 'special'} or type(w['id']) is not int
                or not 0 < w['id'] <= 2147483647 or w['special'] is not False or not isinstance(w['name'], str)
                for w in workspaces) or len({w['id'] for w in workspaces}) != len(workspaces)):
            raise OwnershipBlocked(f'workspace placement mismatch: expected regular workspaces observed {_brief(workspaces)}')
        ids = {w['id'] for w in workspaces}
        if type(o.get('workspace')) is not int or o['workspace'] not in ids:
            raise OwnershipBlocked(f"workspace placement mismatch: active {_brief(o.get('workspace'))} not among {sorted(ids)[:8]}")
        windows = o.get('windows')
        if not isinstance(windows,list) or any(not isinstance(w,dict) or set(w) != {'workspace', 'pinned'} or type(w['workspace']) is not int or w['workspace'] not in ids or w['pinned'] is not False for w in windows):
            raise OwnershipBlocked(f"unsupported or unreachable mapped window: workspaces {sorted(ids)} observed {_brief(windows)}")

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


MODE = 'isolated-vm'
_SIDE = {'handle', 'name', 'device_major', 'device_minor', 'connector_id', 'drm_eligible',
         'classification', 'enabled', 'dpms', 'mirrored'}


def parse_pair(result, target_name, fallback_name):
    """Validate a native schema-2 pair reply. Returns ((target, fallback) handles, proof).

    The proof is a DRM object binding only: never a physical or VM classification.
    """
    import re
    if not isinstance(result, dict) or set(result) != {'mode', 'target', 'fallback'} or result['mode'] != MODE:
        raise OwnershipBlocked('invalid pair proof')
    handles, proof = [], {}
    for key, name in (('target', target_name), ('fallback', fallback_name)):
        s = result[key]
        if not isinstance(s, dict) or set(s) != _SIDE or s['name'] != name:
            raise OwnershipBlocked('pair proof scope mismatch')
        if not isinstance(s['handle'], str) or not re.fullmatch(r'[0-9a-f]{48}', s['handle']):
            raise OwnershipBlocked('invalid pair handle')
        for field, low in (('device_major', 1), ('device_minor', 0), ('connector_id', 1)):
            if type(s[field]) is not int or not low <= s[field] <= 4294967295:
                raise OwnershipBlocked('invalid DRM identity')
        if (s['drm_eligible'] is not True or s['classification'] != 'unclassified' or s['enabled'] is not True
                or s['dpms'] is not True or s['mirrored'] is not False):
            raise OwnershipBlocked('pair output not usable or misclassified')
        handles.append(s['handle'])
        proof[key] = {'name': name, 'device': f"{s['device_major']}:{s['device_minor']}", 'connector': s['connector_id']}
    if handles[0] == handles[1] or (proof['target']['device'], proof['target']['connector']) == \
            (proof['fallback']['device'], proof['fallback']['connector']):
        raise OwnershipBlocked('pair outputs alias')
    return tuple(handles), proof


class IsolatedBrokerClient(OwnershipBrokerClient):
    """Explicit isolated-VM schema-2 client. Never sends a schema-1 request."""
    SCHEMA = 2
    HELLO = 'pair_hello'

    def __init__(self, transport, run_id, session, target_name, fallback_name):
        super().__init__(transport, run_id, session, fallback_name)
        self.target_name = token(target_name)
        if self.target_name == self.approved_physical: raise ValueError('independent recovery output required')
        self.target = None
        self.proof = None

    def _payload(self, payload):
        return {**payload, 'mode': MODE}

    @classmethod
    def from_journal(cls, transport, journal):
        """Restore saved epoch/handles/proof; never pair_hello or select by name."""
        from .journal import validate_broker
        with journal.transaction():
            context = validate_broker(journal.broker)
        if context.get('mode') != MODE: raise OwnershipBlocked('journal is not isolated-mode authority')
        if transport.instance != context['instance']:
            raise OwnershipBlocked('saved compositor instance mismatch')
        client = cls(transport, journal.run_id, journal.session, context['output'], context['recovery_output'])
        client.epoch, client.fallback, client.target = context['epoch'], context['fallback'], context['target']
        client.proof = context['proof']
        client.verified_session()
        return client

    def require_output_ownership(self):
        if self.epoch is not None: return
        try:
            result = self._call('pair_hello', {'target_name': self.target_name, 'fallback_name': self.approved_physical})
            (self.target, self.fallback), self.proof = parse_pair(result, self.target_name, self.approved_physical)
        except (ValueError, OSError, TypeError, KeyError):
            self.epoch = self.target = self.fallback = self.proof = None
            raise

    def observe_pair(self):
        """Full target+fallback revalidation through saved handles only."""
        if self.epoch is None or self.target is None: raise OwnershipBlocked('pair authority missing')
        result = self._call('pair_observe', {'target': self.target, 'fallback': self.fallback})
        handles, proof = parse_pair(result, self.target_name, self.approved_physical)
        if handles != (self.target, self.fallback) or proof != self.proof:
            raise OwnershipBlocked('pair identity changed')
        return proof
