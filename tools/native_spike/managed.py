"""Explicit managed test-pattern orchestration; never loads a plugin or captures."""
import os
from pathlib import Path
from dataclasses import dataclass
from .hyprland import monitors, plan_outputs, create_outputs, OwnershipBlocked
from .ipc import private_dir, token
from .journal import Journal, recover, managed_owner
from .ownership import OwnershipBrokerClient
from .supervisor import validate_physical_pair, _launch


@dataclass
class ManagedGuard:
    broker: OwnershipBrokerClient
    output: str
    specs: list

    def __call__(self):
        validate_physical_pair(self.broker.transport, self.broker.session,
                               self.output, self.broker.approved_physical)
        for spec in self.specs:
            self.broker.verify(self.broker.inspect(spec['id']), spec)


def recover_managed(client, consent, path):
    # Copies must never select a different owner lock or receive recovery writes
    # while leaving the canonical journal stale. Do not resolve symlink aliases:
    # the existing no-follow directory/file opens must continue to reject them.
    canonical = client.runtime_dir / 'xr-workspace-spike' / token(consent['run_id']) / 'journal.json'
    if Path(path).absolute() != canonical:
        raise OwnershipBlocked('managed recovery requires the canonical runtime journal')
    path = canonical
    session = client.verified_session()['id']
    validate_physical_pair(client, session, consent['output'], consent['recovery_output'])
    with managed_owner(path):
        journal = Journal.open(path, consent['run_id'], session, existing_only=True)
        broker = OwnershipBrokerClient.from_journal(client, journal)
        if any(journal.broker[key] != consent[key] for key in ('instance', 'output', 'recovery_output')):
            raise OwnershipBlocked('consent does not match saved broker scope')
        result = recover(journal, broker)
    return {'journal': str(journal.path), 'complete': result.complete, 'remaining': result.remaining}


def run_managed(client, consent, runtime):
    session = client.verified_session()['id']
    output, fallback = consent['output'], consent['recovery_output']
    validate_physical_pair(client, session, output, fallback)
    specs = plan_outputs(monitors(client.query('monitors')), client.query('workspaces'), consent['run_id'])
    broker = OwnershipBrokerClient(client, consent['run_id'], session, fallback)
    broker.require_output_ownership()  # no journal or output mutations without a broker
    directory = runtime / 'xr-workspace-spike'
    os.close(private_dir(directory, create=True))
    directory /= consent['run_id']
    os.close(private_dir(directory, create=True))
    with managed_owner(directory / 'journal.json'):
        journal = Journal.open(directory / 'journal.json', consent['run_id'], session)
        return _run_owned(client, journal, broker, output, fallback, specs)


def _run_owned(client, journal, broker, output, fallback, specs, *, context=None, guard=None):
    session = journal.session
    with journal.transaction():
        if journal.operations or journal.broker is not None:
            raise OwnershipBlocked('existing run requires explicit recovery; use a fresh run id')
    if context is None:
        context = {'instance': client.instance, 'output': output, 'recovery_output': fallback,
                   'epoch': broker.epoch, 'fallback': broker.fallback}
    journal.bind_broker(context)
    try:
        create_outputs(specs, journal, broker)
        if guard is None: guard = ManagedGuard(broker, output, specs)
        guard()
        with client.connect_wayland(session) as wayland:
            returncode = _launch(client, journal.run_id, session, output, wayland, None, guard=guard)
    finally:
        # _launch owns/reaps the renderer first. Creation, connect and spawn failures
        # also reach this recovery; a storage error is fatal and is never retried.
        result = recover(journal, broker)
    return {'journal': str(journal.path), 'renderer_returncode': returncode,
            'complete': result.complete, 'remaining': result.remaining}
