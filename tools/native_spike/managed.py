"""Explicit managed test-pattern orchestration; never loads a plugin or captures."""
import os
from pathlib import Path
from dataclasses import dataclass
from .hyprland import monitors, plan_outputs, create_outputs, OwnershipBlocked
from .ipc import private_dir, token
from .journal import Journal, recover, managed_owner
from .ownership import OwnershipBrokerClient
from .environment import environment
from .quality import video_quality
from .supervisor import validate_physical_pair, _launch


@dataclass
class ManagedGuard:
    broker: OwnershipBrokerClient
    output: str
    specs: list

    def __call__(self):
        # The fallback may be DPMS-off during a run: Ctrl+Alt+D's privacy mode (privacy.py).
        validate_physical_pair(self.broker.transport, self.broker.session,
                               self.output, self.broker.approved_physical, recovery_asleep_ok=True)
        for spec in self.specs:
            self.broker.verify(self.broker.inspect(spec['id']), spec)


def recover_managed(client, consent, path, clear_rules=False):
    """Recovery from a run's journal. `clear_rules`: the person agreed to the monitor rules being taken out (monitor_rules.py)."""
    # Copies must never select a different owner lock or receive recovery writes
    # while leaving the canonical journal stale. Do not resolve symlink aliases:
    # the existing no-follow directory/file opens must continue to reject them.
    canonical = client.runtime_dir / 'xr-workspace-spike' / token(consent['run_id']) / 'journal.json'
    if Path(path).absolute() != canonical:
        raise OwnershipBlocked('managed recovery requires the canonical runtime journal')
    path = canonical
    session = client.verified_session()['id']
    from .privacy import wake
    wake(client, consent['recovery_output'])  # a crash in privacy mode (Ctrl+Alt+D) left it DPMS-off
    validate_physical_pair(client, session, consent['output'], consent['recovery_output'])
    with managed_owner(path):
        journal = Journal.open(path, consent['run_id'], session, existing_only=True)
        broker = OwnershipBrokerClient.from_journal(client, journal)
        if any(journal.broker[key] != consent[key] for key in ('instance', 'output', 'recovery_output')):
            raise OwnershipBlocked('consent does not match saved broker scope')
        result = recover_restoring(client, journal, broker, consent['run_id'], clear_rules=clear_rules)
    return {'journal': str(journal.path), 'complete': result.complete, 'remaining': result.remaining, 'reasons': result.reasons}


def recover_restoring(client, journal, broker, run_id, snapshot=None, layouts=None, *, clear_rules=False):
    """Recovery, preceded by the run's layout restore (restore.py) while its XR outputs still exist: the snapshot
    taken at start (`snapshot`, else the one saved in the state directory). The saved one goes once recovery completes;
    an incomplete recovery keeps it, so the next attempt restores again. A monitor rule Omarchy wrote for one of the run's
    desktops goes first if `clear_rules`, else it is only named (monitor_rules.py). An incomplete result says why
    (RecoveryResult.reasons); a complete one that leaves such a rule says so, as the next start needs it gone."""
    from . import monitor_rules, restore
    from .privacy import wake
    from .send import notify as notify_text
    # Privacy mode (Ctrl+Alt+D) may have left the fallback DPMS-off: it comes back first, whatever ended the run.
    saved = getattr(journal, 'broker', None)
    if isinstance(saved, dict): wake(client, saved.get('recovery_output'))
    names = getattr(journal, 'output_names', list)()
    unsolved = monitor_rules.clear(client, names, notify=notify_text) if clear_rules else monitor_rules.named(client, names)
    if snapshot is None:
        try: snapshot = restore.load(run_id, layouts)
        except (OSError, ValueError): snapshot = None
    if snapshot is not None:
        restore.restore(client, snapshot, notify=notify_text)
    result = recover(journal, broker)
    if result.complete:
        try: restore.discard(run_id, layouts)
        except OSError: pass
        for text in unsolved: notify_text(text)
    else:
        result.reasons = unsolved + _left_on(client, names) + result.reasons
    return result


def _left_on(client, names):
    """The windows still on `names` after a recovery that did not finish, for its reasons: each one's application class,
    never its title. A failed read costs only this: the reasons already there stand."""
    try:
        monitors = {m.get('id'): m.get('name') for m in client.query('monitors')}
        windows = [c for c in client.query('clients') if monitors.get(c.get('monitor')) in names and c.get('mapped') is not False]
    except (OSError, ValueError, TypeError, AttributeError):
        return []
    def one(c):
        space = c.get('workspace'); space = space.get('id') if isinstance(space, dict) else '?'
        return (f"{c.get('class') or 'a window'} is still on {monitors[c['monitor']]}, workspace {space}"
                f"{' (pinned: unpin it first)' if c.get('pinned') else ''}")
    return [one(c) for c in windows[:8]]


def run_managed(client, consent, runtime, *, desktops=None, store=None, renderer=None):
    session = client.verified_session()['id']
    output, fallback = consent['output'], consent['recovery_output']
    validate_physical_pair(client, session, output, fallback)
    workspaces = client.query('workspaces')
    from .supervisor import CAPTURE_SCOPE
    adopting = consent.get('capture') == CAPTURE_SCOPE  # a capture (panel) run takes the fallback's windows (adopt.py)
    # Every start has the run's own desktops (from the panel: the last session's, profiles.load_setup, else three):
    # adoption spreads the fallback's windows over them rather than adding desktops for more occupied workspaces.
    specs = plan_outputs(monitors(client.query('monitors')), workspaces, consent['run_id'], desktops)
    broker = OwnershipBrokerClient(client, consent['run_id'], session, fallback)
    broker.require_output_ownership()  # no journal or output mutations without a broker
    directory = runtime / 'xr-workspace-spike'
    os.close(private_dir(directory, create=True))
    directory /= consent['run_id']
    os.close(private_dir(directory, create=True))
    with managed_owner(directory / 'journal.json'):
        journal = Journal.open(directory / 'journal.json', consent['run_id'], session)
        return _run_owned(client, journal, broker, output, fallback, specs, capture=capture_names(consent, specs),
                          pose_feed=consent.get('tracking') == 'xrlinuxdriver', store=store, renderer=renderer,
                          adopt=adopting, create_failed=_setup_failed(store, desktops))


# The broker's verdicts on an output itself (ownership.OwnershipBrokerClient.verify): it was not made live, or did not
# read back as asked. Any other failure (the compositor session, the broker's epoch or identity, a transport error) says
# nothing about the saved setup, which is then kept.
UNCREATABLE = ('creation incomplete', 'missing readback', 'actual geometry/workspace mismatch', 'unusable output')


def _setup_failed(store, desktops):
    """For a run restoring a saved setup (`desktops` other than the default three, from a glasses profile): when the
    broker refuses its outputs (UNCREATABLE), the saved setup goes back to the default (profiles.discard_unusable), so
    the next start falls back instead of failing the same way. None when there is nothing to discard."""
    from .profiles import ProfileStore, default_setup, discard_unusable
    if not isinstance(store, ProfileStore) or desktops is None or desktops == default_setup()['desktops']: return None

    def failed(exc):
        if isinstance(exc, OwnershipBlocked) and str(exc) in UNCREATABLE:
            discard_unusable(store.path, store.model, list(desktops), str(exc))
    return failed


from contextlib import contextmanager


@contextmanager
def view_controls(client, run_id, session, capture, output=None, store=None, fallback=None, desktops=None, windows=None,
                  cursor_shape=None):
    """Glasses view controls for capture runs: private control socket and runtime-only Ctrl+Alt binds.

    `fallback` is the run's physical recovery output: Ctrl+Alt+B sends a window back there (send.py). `desktops`: the
    run's desktops.DesktopManager (the pills' Ultrawide / Add / Remove); closed here, before the run's recovery. `windows`:
    the run's window_memory.WindowTracker; it takes its last capture here, while the XR outputs still hold the windows.
    `cursor_shape`: the broker's read of the cursor an app asked for (ownership.cursor_reader), for the dot's look over text."""
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
            # Typing mode (Ctrl+Alt+T) survives a reload: only its toggle comes back. The keys are the key bind panel's.
            keys = control.keys.bind_kwargs() if control is not None and control.keys is not None else {}
            if reinstall: reinstall(*bind_args, **({'typing': True} if control is not None and control.state.typing else {}), **keys)
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
            client.reinstall_view_binds(*bind_args, typing=on, **(control.keys.bind_kwargs() if control.keys is not None else {}))

        # Per-glasses monitor setup when the store is a profile (panel runs); the CLI's layout.json store has none.
        from .profiles import PlacementStore, ProfileStore
        placements = PlacementStore(store.path, store.model) if isinstance(store, ProfileStore) else None
        # The last session's placements and rolls come back with its desktops (profiles.load_setup); flat and level if
        # nothing is saved, the save is unusable, or the run's desktops are not the saved ones.
        initial, rolls = _restored(placements, capture, desktops)
        # Every start is in the overview, anchored, and a maximize follows until Ctrl+Alt+V picks locked (maximize-follow):
        # no saved mode is read; spacing, prediction, the VR video quality and the background still are.
        control = view.ViewControl(view.ViewState(capture, spacing=saved['spacing'], prediction=saved['prediction'],
                                                   placements=_initial_placements(initial, capture),
                                                   video_quality=video_quality(saved),
                                                   environment=environment(saved)), run_id, session,
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
        if rolls: pointer.rolls[:len(rolls)] = rolls  # the saved levelling rolls (the `roll` frame sends them)
        control.wake = pointer.wake_reader
        if hasattr(client, 'lock_pointer') and hasattr(client, 'enter_desktop'):
            sampler = UnifiedSampler(client, capture, pointer, bind_args, cursor_shape=cursor_shape).start()
            control.desktop_listeners.append(sampler.set_names)  # a pill's Add / Remove: its monitors again
        # Send the focused window to the glasses / back (Ctrl+Alt+G, 1-4, B). Closed first at run end (below).
        if hasattr(client, 'mutate'):
            from .send import WindowSender, notify as notify_text
            control.sender = WindowSender(client, capture, fallback)
            control.notify = notify_text  # a desktop change that could not be done says why
        # Privacy mode (Ctrl+Alt+D): the main physical display DPMS off / on. Closed (display back on) before recovery.
        if hasattr(client, 'set_dpms') and output is not None:
            from .privacy import PrivacyDisplay
            control.privacy = PrivacyDisplay(client, output, fallback)
        if desktops is not None:
            control.desktops = desktops
            if placements is not None: desktops.store = placements  # the profile takes the new desktops
            if hasattr(client, 'adopt_windows'): desktops.move_windows = _window_mover(client)  # Ctrl+Alt+R: XR-4 to XR-3
            control.state.wide = desktops.wide()
            control.state.sizes = desktops.desktops()  # the panel's Scale dropdown
        if windows is not None:  # where each window is, kept current (window_memory.py); Super drags and events reach it
            control.windows = windows
            windows.context = control.window_context
            control.desktop_listeners.append(windows.set_names)
            if desktops is not None: desktops.leaving = windows.leaving
        stream = getattr(client, 'event_stream', None)  # config reloads; optional
        try: events = control.events = stream() if stream else None
        except (OSError, ValueError): events = None
        # The key bind panel (Ctrl+Alt+K, keybinds.py): the saved keys, next to the store, and the desktop's own binds read
        # first, so a bind whose keys Hyprland / Omarchy already hold is never installed (removing it would remove theirs).
        from . import keybinds
        keys_path = Path(store.path).with_name('keybinds.json') if getattr(store, 'path', None) else keybinds.config_path()
        desktop = None
        if hasattr(client, 'binds_text'):
            try: desktop = keybinds.DesktopBinds.from_text(client.binds_text(timeout=0.5))
            except (OSError, ValueError): desktop = None  # unknown: every bind is installed, as before the panel
        control.keys = keybinds.BindControl(control, keybinds.load(keys_path), keys_path,
                                            client=client if hasattr(client, 'open_key_panel') else None, bind_args=bind_args,
                                            desktop=desktop, notify=control.notify)
        client.install_view_binds(*bind_args, **control.keys.bind_kwargs())
        control.keys.installed()
        control.keys.startup_toast()  # a conflict on load: off, and said once
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
        if control is not None and control.privacy is not None:
            control.privacy.close()  # the main display back on; no toggle starts after this
        if control is not None:
            control.flush()  # a panel or drag change still inside its save delay
            control.save_setup()  # the whole monitor setup, for the next start (profiles.py)
            if control.windows is not None: control.windows.close()  # and where every window is, before recovery moves them
            control.stop_menu_binds()  # no modal panel bind may land after the removal below
            if control.keys is not None: control.keys.stop()  # nor a key bind panel change (the removal closes it)
        if sampler is not None: sampler.stop()  # removes the free pointer's binds and shows the cursor; none after this
        try:
            if installed: client.remove_view_binds()  # also removes an open panel's modal binds
        except (OSError, ValueError):
            pass  # never block recovery on bind removal; binds also vanish on config reload
        if events is not None: events.close()
        if pointer is not None: pointer.close()
        server.close()


def _window_mover(client):
    """Ctrl+Alt+R removing a desktop: its windows to `target` first, whole workspaces, as adoption moves them (adopt.py);
    what cannot move goes to the fallback with the removal. Never raises for compositor errors."""
    from .adopt import adopt
    from .send import notify

    def move(source, target):
        adopt(client, source, [target], notify=lambda _: notify(f"Could not move {source}'s windows to {target}: "
                                                                 f'they go to your normal monitor.'))
    return move


def _restored(store, names, desktops=None):
    """(placements, rolls) a run starts with: the saved setup's (store.load_setup) when its desktops are the run's own
    (`desktops`: the run's DesktopManager, else the count alone is compared), else flat and level. (None, None) without a
    profile store. A setup not restored is logged with the reason."""
    if store is None: return None, None
    from .geometry import DEFAULT_PLACEMENT
    from .profiles import NOTHING_SAVED, describe_setup, log
    flat = [dict(DEFAULT_PLACEMENT) for _ in names], [0] * len(names)
    try: setup, reason = store.load_setup()
    except (OSError, ValueError) as exc: setup, reason = None, str(exc)
    if reason == NOTHING_SAVED: return flat
    if reason is not None:
        log(f'run: saved monitor setup not used: {reason}; flat placements')
        return flat
    live = desktops.desktops() if desktops is not None else None
    if len(setup['desktops']) != len(names) or (live is not None and live != setup['desktops']):
        log(f"run: the saved placements are for other desktops ({describe_setup(setup)}); flat placements")
        return flat
    return [dict(p) for p in setup['placements']], list(setup['rolls'])


def _initial_placements(saved, names):
    """The profile's placements for this run's desktops (one each; missing ones flat), or None."""
    if saved is None: return None
    from .geometry import DEFAULT_PLACEMENT
    return (list(saved) + [dict(DEFAULT_PLACEMENT)] * len(names))[:len(names)]


def capture_names(consent, specs):
    from .supervisor import CAPTURE_SCOPE
    return [s['name'] for s in specs] if consent.get('capture') == CAPTURE_SCOPE else None


def _run_owned(client, journal, broker, output, fallback, specs, *, context=None, guard=None, capture=None, pose_feed=False, store=None, renderer=None, adopt=False, layouts=None, create_failed=None):
    session = journal.session
    snapshot = memory = None
    report = {}  # why the supervisor ended the renderer, if it did (incident_detect.classify_end)
    with journal.transaction():
        if journal.operations or journal.broker is not None:
            raise OwnershipBlocked('existing run requires explicit recovery; use a fresh run id')
    if context is None:
        context = {'instance': client.instance, 'output': output, 'recovery_output': fallback,
                   'epoch': broker.epoch, 'fallback': broker.fallback}
    journal.bind_broker(context)
    try:
        try: create_outputs(specs, journal, broker)
        except Exception as exc:
            try:
                if create_failed is not None: create_failed(exc)  # a saved setup that cannot be created (_setup_failed)
            except Exception: pass  # never in place of the create failure itself
            raise
        manager = None
        if guard is None:
            guard = ManagedGuard(broker, output, specs)
            if capture and isinstance(broker, OwnershipBrokerClient):  # the pills' Ultrawide / Add / Remove (desktops.py)
                from .desktops import DesktopManager
                manager = DesktopManager(broker, journal, specs, guard_factory=lambda live: ManagedGuard(broker, output, live),
                                         windows=client if hasattr(client, 'fit_windows') else None)
        guard()
        if adopt and capture and hasattr(client, 'adopt_windows'):
            # Once, before the renderer starts: the physical monitor's windows onto the desktops, by workspace, in one
            # compositor tick (adopt.py). Recovery below returns them, like any window on an XR output.
            # First the pre-XR arrangement, kept in memory and the state directory: Stop and Recover put it back.
            from .adopt import adopt as adopt_windows
            from .restore import take
            from .send import notify
            from .window_memory import tracker_for
            snapshot = take(client, fallback, journal.run_id, layouts)
            names = [s['name'] for s in specs]
            # The windows XR last held go back where they were, the desktops being the remembered ones by now; adoption
            # takes what is left, as ever; then whatever needs every window in place (window_memory.py).
            memory = tracker_for(client, store, fallback, names)
            if memory is not None: memory.place(names)
            adopt_windows(client, fallback, names, notify=notify)
            if memory is not None: memory.finish()
        cursor_shape = getattr(broker, 'cursor_reader', lambda: None)()
        with client.connect_wayland(session) as wayland, view_controls(client, journal.run_id, session, capture, output, store, fallback, manager,
                                                                       memory, cursor_shape=cursor_shape) as control:
            returncode = _launch(client, journal.run_id, session, output, wayland, None, guard=guard, capture=capture,
                                 control=control, pose_feed=pose_feed, renderer=renderer, report=report)
    finally:
        # _launch owns/reaps the renderer first. Creation, connect and spawn failures
        # also reach this recovery; a storage error is fatal and is never retried.
        # The windows go back to their pre-XR layout first, while the XR outputs still exist (restore.py).
        result = recover_restoring(client, journal, broker, journal.run_id, snapshot, layouts)
    return {'journal': str(journal.path), 'renderer_returncode': returncode, 'complete': result.complete, 'remaining': result.remaining,
            'reasons': result.reasons, **({'stop_reason': report['stop_reason']} if report.get('stop_reason') else {})}
