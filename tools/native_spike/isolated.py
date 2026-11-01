"""Explicit isolated-VM managed mode. Separate consent, never a fallback from normal mode.

VM evidence is read from the guest kernel (proc/sysfs) and joined to the native
broker's DRM object binding. It proves the selected compositor outputs are the
named virtio connectors of a guest whose CPUs report a hypervisor and whose every
DRM card is virtio-gpu. It does NOT attest host QEMU arguments (no network, shares
or passthrough); those are verified host-side. A malicious root/hypervisor can
fabricate this evidence. Virtual outputs are never classified as physical.
"""
from dataclasses import dataclass
import os
from pathlib import Path
import re

from .hyprland import OwnershipBlocked, monitors, plan_outputs
from .ipc import read_private_json, token
from .ownership import MODE, IsolatedBrokerClient

# Tests patch this module attribute; there is no runtime flag, file or env override.
SYSROOT = Path('/')
FORBIDDEN_INSTANCE = 'efb50993780079460b0cbed1363e2166a2de1d9f_1793311125_1142154332'
GATE = 'VM-isolated'
VIRTUAL = re.compile(r'Virtual-[1-9][0-9]{0,3}\Z')
UUID = re.compile(r'[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}\Z')
VIRTIO_PCI_VENDOR = '0x1af4'


def reject_forbidden(instance):
    if instance == FORBIDDEN_INSTANCE:
        raise ValueError('known physical compositor instance is never an isolated target')


def load_isolated_consent(path, instance, output):
    reject_forbidden(instance)
    data = read_private_json(path)
    keys = {'schema', 'gate', 'mode', 'run_id', 'instance', 'boot_id', 'session', 'runtime',
            'output', 'recovery_output', 'allow', 'capture', 'input', 'sdk', 'mode_switch'}
    if isinstance(data, dict) and 'tracking' in data: keys.add('tracking')
    if not isinstance(data, dict) or set(data) != keys: raise ValueError('invalid isolated consent fields')
    if type(data['schema']) is not int or data['schema'] != 1 or data['gate'] != GATE or data['mode'] != MODE:
        raise ValueError(f'{GATE} consent required')
    if data['instance'] != instance or data['output'] != output: raise ValueError('consent scope mismatch')
    reject_forbidden(data['instance'])
    token(data['run_id']); token(data['session'])
    if len(data['run_id']) > 80: raise ValueError('run id too long')
    for name in (data['output'], data['recovery_output']):
        if not isinstance(name, str) or not VIRTUAL.fullmatch(name):
            raise ValueError('isolated mode targets only Virtual-N connectors')
    if data['output'] == data['recovery_output']: raise ValueError('independent recovery output required')
    if not isinstance(data['boot_id'], str) or not UUID.fullmatch(data['boot_id']): raise ValueError('invalid boot id')
    # Exact normalized form: the same string is journaled and compared on recovery.
    if (not isinstance(data['runtime'], str) or not Path(data['runtime']).is_absolute()
            or str(Path(data['runtime'])) != data['runtime']):
        raise ValueError('absolute normalized runtime required')
    from .supervisor import CAPTURE_SCOPE, CAPTURE_ALLOW
    if type(data['capture']) is str and data['capture'] == CAPTURE_SCOPE:
        if data['allow'] != CAPTURE_ALLOW: raise ValueError('exact managed-output capture scope required')
        if 'tracking' in data and (type(data['tracking']) is not str or data['tracking'] != 'xrlinuxdriver'):
            raise ValueError('tracking scope must be "xrlinuxdriver"')
    elif 'tracking' in data: raise ValueError('head tracking requires managed-output capture')
    elif data['allow'] != ['headless-outputs', 'test-pattern'] or data['capture'] is not False:
        raise ValueError('exact test-pattern scope required')
    if any(data[k] is not False for k in ('input', 'sdk', 'mode_switch')):
        raise ValueError('input/SDK/mode transitions not authorized by this CLI')
    return data


def _read(path, limit=4096):
    fd = os.open(path, os.O_RDONLY | os.O_NONBLOCK)
    with os.fdopen(fd, 'rb') as stream:
        raw = stream.read(limit + 1)
    if len(raw) > limit: raise ValueError('kernel evidence too large')
    return raw.decode('ascii').strip()


def collect_guest_evidence(sysroot):
    """Fresh read of positive VM evidence; any missing/inconsistent item fails closed."""
    sysroot = Path(sysroot)
    boot = _read(sysroot / 'proc/sys/kernel/random/boot_id')
    if not UUID.fullmatch(boot): raise ValueError('invalid boot id')
    flags = [line.partition(':')[2].split() for line in _read(sysroot / 'proc/cpuinfo', 1048576).splitlines()
             if line.startswith('flags')]
    if not flags or not all('hypervisor' in f for f in flags):
        raise ValueError('no positive CPU hypervisor evidence')
    drm = sysroot / 'sys/class/drm'
    cards, connectors = {}, {}
    for entry in sorted(os.listdir(drm)):
        if re.fullmatch(r'card[0-9]+', entry):
            # Every DRM card must be virtio-gpu: no host GPU passthrough in the guest.
            # Real layout (verified in guest): cardN/device is the virtio-pci PCI
            # function; its single virtioN child is bound to virtio_gpu.
            device = Path(os.path.realpath(drm / entry / 'device'))
            children = [c for c in os.listdir(device) if re.fullmatch(r'virtio[0-9]+', c)]
            if (Path(os.readlink(device / 'driver')).name != 'virtio-pci'
                    or Path(os.readlink(device / 'subsystem')).name != 'pci'
                    or _read(device / 'vendor') != VIRTIO_PCI_VENDOR or len(children) != 1
                    or Path(os.readlink(device / children[0] / 'driver')).name != 'virtio_gpu'
                    or Path(os.readlink(device / children[0] / 'subsystem')).name != 'virtio'):
                raise ValueError('non-virtio DRM device present')
            dev = _read(drm / entry / 'dev')
            if not re.fullmatch(r'[1-9][0-9]{0,9}:[0-9]{1,10}', dev): raise ValueError('invalid DRM device number')
            cards[entry] = dev
    if not cards: raise ValueError('no virtio DRM device')
    for entry in sorted(os.listdir(drm)):
        match = re.fullmatch(r'(card[0-9]+)-(.+)', entry)
        if not match: continue
        card, name = match.groups()
        if card not in cards: raise ValueError('connector without verified card')
        if name.startswith('Writeback') or _read(drm / entry / 'status') != 'connected': continue
        if _read(drm / entry / 'enabled') != 'enabled': continue
        connector = _read(drm / entry / 'connector_id')
        if not re.fullmatch(r'[1-9][0-9]{0,9}', connector): raise ValueError('invalid connector id')
        if name in connectors: raise ValueError('ambiguous connector name')
        connectors[name] = {'device': cards[card], 'connector': int(connector)}
    return {'boot_id': boot, 'connectors': connectors}


def join_proof(evidence, proof, sides=('target', 'fallback')):
    """Native object binding must equal the guest kernel's connector record."""
    seen = set()
    for key in sides:
        side = proof[key]
        if not VIRTUAL.fullmatch(side['name']): raise ValueError('isolated output is not a Virtual connector')
        if evidence['connectors'].get(side['name']) != {'device': side['device'], 'connector': side['connector']}:
            raise ValueError('native output not bound to guest virtio connector')
        identity = (side['device'], side['connector'])
        if identity in seen: raise ValueError('pair outputs alias')
        seen.add(identity)


def validate_virtual_outputs(client, session, names):
    if client.verified_session() != {'id': session}: raise ValueError('launcher session mismatch')
    snapshot = monitors(client.query('monitors'))
    for name in names:
        if not VIRTUAL.fullmatch(name): raise ValueError('isolated output must be Virtual-N')
        matches = [m for m in snapshot if m['name'] == name and not m['disabled'] and m['dpmsStatus'] and m['mirrorOf'] == 'none']
        if len(matches) != 1: raise ValueError('approved usable virtual output missing')


def check_scope(client, consent, sysroot):
    """Bind consent to the actual guest boot, verified session and canonical runtime."""
    reject_forbidden(client.instance)
    evidence = collect_guest_evidence(sysroot)
    if evidence['boot_id'] != consent['boot_id']: raise ValueError('consent boot mismatch')
    if Path(consent['runtime']) != Path(client.runtime_dir): raise ValueError('consent runtime mismatch')
    if client.verified_session() != {'id': consent['session']}: raise ValueError('consent session mismatch')
    return evidence


def broker_context(client, broker, boot_id, runtime):
    return {'mode': MODE, 'instance': client.instance, 'output': broker.target_name,
            'recovery_output': broker.approved_physical, 'epoch': broker.epoch, 'fallback': broker.fallback,
            'target': broker.target, 'boot_id': boot_id, 'runtime': str(runtime), 'proof': broker.proof}


@dataclass
class IsolatedGuard:
    """Spawned-guard check: full pair, guest evidence and owned outputs, each period."""
    broker: IsolatedBrokerClient
    output: str
    specs: list
    boot_id: str
    sysroot: Path

    def __call__(self):
        validate_virtual_outputs(self.broker.transport, self.broker.session,
                                 (self.output, self.broker.approved_physical))
        evidence = collect_guest_evidence(self.sysroot)
        if evidence['boot_id'] != self.boot_id: raise ValueError('guest boot changed')
        join_proof(evidence, self.broker.observe_pair())
        for spec in self.specs:
            self.broker.verify(self.broker.inspect(spec['id']), spec)


def run_isolated(client, consent, runtime, *, desktops=None, store=None, renderer=None):
    from .journal import Journal, managed_owner
    from .managed import _run_owned, capture_names
    from .ipc import private_dir
    sysroot = SYSROOT
    evidence = check_scope(client, consent, sysroot)
    session = consent['session']
    output, fallback = consent['output'], consent['recovery_output']
    validate_virtual_outputs(client, session, (output, fallback))
    specs = plan_outputs(monitors(client.query('monitors')), client.query('workspaces'), consent['run_id'], desktops)
    broker = IsolatedBrokerClient(client, consent['run_id'], session, output, fallback)
    broker.require_output_ownership()  # schema-2 pair proof before any journal or mutation
    join_proof(evidence, broker.proof)
    directory = Path(runtime) / 'xr-workspace-spike'
    os.close(private_dir(directory, create=True))
    directory /= consent['run_id']
    os.close(private_dir(directory, create=True))
    with managed_owner(directory / 'journal.json'):
        journal = Journal.open(directory / 'journal.json', consent['run_id'], session)
        return _run_owned(client, journal, broker, output, fallback, specs,
                          context=broker_context(client, broker, consent['boot_id'], runtime),
                          guard=IsolatedGuard(broker, output, specs, consent['boot_id'], sysroot),
                          capture=capture_names(consent, specs), pose_feed=consent.get('tracking') == 'xrlinuxdriver',
                          store=store, renderer=renderer)


def recover_isolated(client, consent, path):
    """Saved authority only; requires the fallback proof, not the (possibly lost) target."""
    from .journal import Journal, managed_owner, recover
    canonical = client.runtime_dir / 'xr-workspace-spike' / token(consent['run_id']) / 'journal.json'
    if Path(path).absolute() != canonical:
        raise OwnershipBlocked('managed recovery requires the canonical runtime journal')
    evidence = check_scope(client, consent, SYSROOT)
    session = consent['session']
    validate_virtual_outputs(client, session, (consent['recovery_output'],))
    with managed_owner(canonical):
        journal = Journal.open(canonical, consent['run_id'], session, existing_only=True)
        broker = IsolatedBrokerClient.from_journal(client, journal)
        saved = journal.broker
        for key in ('instance', 'output', 'recovery_output', 'boot_id', 'runtime'):
            same = Path(saved[key]) == Path(consent[key]) if key == 'runtime' else saved[key] == consent[key]
            if not same: raise OwnershipBlocked('consent does not match saved broker scope')
        join_proof(evidence, saved['proof'], sides=('fallback',))
        result = recover(journal, broker)
    return {'journal': str(journal.path), 'complete': result.complete, 'remaining': result.remaining}
