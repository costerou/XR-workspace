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


def run_managed(client, consent, runtime, *, desktops=None, store=None, renderer=None):
    session = client.verified_session()['id']
    output, fallback = consent['output'], consent['recovery_output']
    validate_physical_pair(client, session, output, fallback)
    specs = plan_outputs(monitors(client.query('monitors')), client.query('workspaces'), consent['run_id'], desktops)
    broker = OwnershipBrokerClient(client, consent['run_id'], session, fallback)
    broker.require_output_ownership()  # no journal or output mutations without a broker
    directory = runtime / 'xr-workspace-spike'
    os.close(private_dir(directory, create=True))
    directory /= consent['run_id']
    os.close(private_dir(directory, create=True))
    with managed_owner(directory / 'journal.json'):
        journal = Journal.open(directory / 'journal.json', consent['run_id'], session)
        return _run_owned(client, journal, broker, output, fallback, specs, capture=capture_names(consent, specs),
                          pose_feed=consent.get('tracking') == 'xrlinuxdriver', store=store, renderer=renderer)


from contextlib import contextmanager


@contextmanager
def view_controls(client, run_id, session, capture, output=None, store=None, fallback=None, desktops=None):
    """Glasses view controls for capture runs: private control socket and runtime-only Ctrl+Alt binds.

    `fallback` is the run's physical recovery output: Ctrl+Alt+B sends a window back there (send.py). `desktops`: the
    run's desktops.DesktopManager (the pills' Ultrawide / Add / Remove); closed here, before the run's recovery."""
    if not capture:
        yield None
        return
    import sys
    from . import view
    from .ipc import PrivateServer
    from .profiles import LayoutStore
    store = store or LayoutStore(view.settings_path())
    video = None  # VR video (video.py) wraps the view control
    server = PrivateServer(client.runtime_dir, run_id, session)
    installed = False
    events = control = sampler = pointer = None
    try:
        saved = store.load()
        bind_args = (sys.executable, str(Path(view.__file__).with_name('xr_control.py')), str(server.path), run_id, session)
        mode = None
        if output is not None:
            try: mode = view.session_mode(next((m for m in client.query('monitors') if m.get('name') == output), None))
            except (OSError, ValueError, TypeError, KeyError): mode = None

        def after_reload():
            # Config reloads (Omarchy theme switches) drop runtime binds and reset the session mode.
            reinstall = getattr(client, 'reinstall_view_binds', None)
            # Typing mode (Ctrl+Alt+T) survives a reload: only its toggle comes back.
            if reinstall: reinstall(*bind_args, **({'typing': True} if control is not None and control.state.typing else {}))
            # Park first: an asynchronous mode restore followed by parking could
            # read the pre-restore mode and overwrite the requested 120 Hz rule.
            if output is not None and hasattr(client, 'park_presentation'):
                client.park_presentation(output)
            if mode is not None and hasattr(client, 'apply_session_mode'):
                now = next((m for m in client.query('monitors', timeout=0.5) if m.get('name') == output), None)
                if view.session_mode(now) != mode: client.apply_session_mode(output)

        def menu_binds(open_):
            # Placement panel (an expanded glass pill): bare arrows/Tab/R/Escape exist only while one is open.
            if open_: client.install_menu_binds(*bind_args)
            else: client.remove_menu_binds()

        def typing_binds(on):
            # Typing mode: the run's bind table holds only Ctrl+Alt+T; off: the full set again (view.py docstring).
            client.reinstall_view_binds(*bind_args, typing=on)

        # Per-glasses placements when the store is a profile (panel runs); the CLI's layout.json store has none.
        from .profiles import PlacementStore, ProfileStore
        placements = PlacementStore(store.path, store.model) if isinstance(store, ProfileStore) else None
        try: initial = placements.load() if placements is not None else None
        except (OSError, ValueError): initial = None
        control = view.ViewControl(view.ViewState(capture, saved['spacing'], saved['mode'],
                                                   saved['prediction'], _initial_placements(initial, capture)), run_id, session,
                                   lambda name: client.mutate(view.focus_command(name)), store, server,
                                   border=lambda: client.query('getoption', 'general:col.active_border', timeout=0.5),
                                   on_reload=after_reload, placements=placements, menu_binds=menu_binds,
                                   typing_binds=typing_binds if hasattr(client, 'reinstall_view_binds') else None)
        # Overview free pointer (glass pills, corners, panel sliders): cursor reads, its modal left-button binds and cursor
        # hiding, off the supervisor loop. Unified cursor (unified_cursor.py): the real cursor follows the dot, on the
        # matching monitor and point while the dot is over a desktop (its clicks and wheel reach the window there); past
        # a desktop's edge it is parked and the dot carries on through space to the next desktop.
        from .unified_cursor import UnifiedPointer, UnifiedSampler
        pointer = control.overview_pointer = UnifiedPointer(control)
        control.wake = pointer.wake_reader
        if hasattr(client, 'lock_pointer') and hasattr(client, 'enter_desktop'):
            sampler = UnifiedSampler(client, capture, pointer, bind_args).start()
            control.desktop_listeners.append(sampler.set_names)  # a pill's Add / Remove: its monitors again
        # Send the focused window to the glasses / back (Ctrl+Alt+G, 1-4, B). Closed first at run end (below).
        if hasattr(client, 'mutate'):
            from .send import WindowSender, notify as notify_text
            control.sender = WindowSender(client, capture, fallback)
            control.notify = notify_text  # a desktop change that could not be done says why
        if desktops is not None:
            control.desktops = desktops
            if placements is not None: desktops.store = placements  # the profile takes the new desktops
            control.state.wide = desktops.wide()
        stream = getattr(client, 'event_stream', None)  # config reloads; optional
        try: events = control.events = stream() if stream else None
        except (OSError, ValueError): events = None
        client.install_view_binds(*bind_args)
        installed = True
        from .video import VideoControl, notify_send
        video_binds = None
        if hasattr(client, 'install_video_binds'):
            def video_binds(on):  # modal Space/arrows/Tab/Escape exist only while a video plays
                if on: client.install_video_binds(*bind_args)
                else: client.remove_video_binds()
        video = VideoControl(control, binds=video_binds, notify=notify_send)
        yield video
    finally:
        if desktops is not None: desktops.close()  # no desktop change starts now; one in flight lands before recovery
        if video is not None: video.close()  # removes the video keys if a video was playing; none are added after this
        if control is not None and control.sender is not None:
            control.sender.close()  # no window move may start after this; one in flight lands before recovery runs
        if control is not None:
            control.flush()  # a panel or drag change still inside its save delay
            control.stop_menu_binds()  # no modal panel bind may land after the removal below
        if sampler is not None: sampler.stop()  # removes the free pointer's binds and shows the cursor; none after this
        try:
            if installed: client.remove_view_binds()  # also removes an open panel's modal binds
        except (OSError, ValueError):
            pass  # never block recovery on bind removal; binds also vanish on config reload
        if events is not None: events.close()
        if pointer is not None: pointer.close()
        server.close()


def _initial_placements(saved, names):
    """The profile's placements for this run's desktops (one each; missing ones flat), or None."""
    if saved is None: return None
    from .geometry import DEFAULT_PLACEMENT
    return (list(saved) + [dict(DEFAULT_PLACEMENT)] * len(names))[:len(names)]


def capture_names(consent, specs):
    from .supervisor import CAPTURE_SCOPE
    return [s['name'] for s in specs] if consent.get('capture') == CAPTURE_SCOPE else None


def _run_owned(client, journal, broker, output, fallback, specs, *, context=None, guard=None, capture=None, pose_feed=False, store=None, renderer=None):
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
        manager = None
        if guard is None:
            guard = ManagedGuard(broker, output, specs)
            if capture and isinstance(broker, OwnershipBrokerClient):  # the pills' Ultrawide / Add / Remove (desktops.py)
                from .desktops import DesktopManager
                manager = DesktopManager(broker, journal, specs, guard_factory=lambda live: ManagedGuard(broker, output, live))
        guard()
        with client.connect_wayland(session) as wayland, view_controls(client, journal.run_id, session, capture, output, store,
                                                                                   fallback, manager) as control:
            returncode = _launch(client, journal.run_id, session, output, wayland, None, guard=guard, capture=capture,
                                 control=control, pose_feed=pose_feed, renderer=renderer)
    finally:
        # _launch owns/reaps the renderer first. Creation, connect and spawn failures
        # also reach this recovery; a storage error is fatal and is never retried.
        result = recover(journal, broker)
    return {'journal': str(journal.path), 'renderer_returncode': returncode,
            'complete': result.complete, 'remaining': result.remaining}
