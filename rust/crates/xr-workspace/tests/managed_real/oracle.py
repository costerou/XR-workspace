"""The Python side of tests/managed_real.rs: `tools.xr_controller.runner` as the run unit runs it, narrowed to the Rust port.

`python3 -I oracle.py <repo> runner --consent C --state-dir S`, or `... recover-run --consent C --journal J`.

The shipped `managed.view_controls` runs: the private control socket (`ipc.PrivateServer`), the supervisor's
`control_request` and `serve_without_renderer` on it, the `with` around the renderer. Each view-controls feature the
Rust run still has as a stub (rust/crates/xr-workspace/VIEW_CONTROLS.md) is taken out by its own entry in `STUBS`
below, so it does on the Python side what the stub does on the Rust side: nothing. A feature's port deletes its own
entry (and nothing else) in the commit that ports it. Everything else (the consent, the pins, the plugin, the broker, the
journal, adoption, the renderer launch and its watchdog, holds, recovery) is the shipped code throughout.

An entry is one of two kinds:
- patch a module attribute (a class or a function) with a stand-in: `patch(module, name, value)`
- change the client `view_controls` is given (`Client`): `hide` a method (`view_controls` gates on `hasattr`), or
  `quiet` one (it is called, does nothing and returns None)
"""
import contextlib
import sys


class Client:
    """The run's client as `view_controls` sees it: the same object, less what `hide` takes and `quiet` silences."""
    hidden, quieted, filters = set(), set(), []

    def __init__(self, client):
        self._client = client

    def __getattr__(self, name):
        if name in Client.hidden: raise AttributeError(name)
        if name in Client.quieted: return lambda *args, **kwargs: None
        value = getattr(self._client, name)
        for wrap in Client.filters: value = wrap(name, value)
        return value


def hide(*names): Client.hidden.update(names)
def quiet(*names): Client.quieted.update(names)


def patch(module, name, value):
    setattr(module, name, value)


class Nothing:
    """A stand-in feature object: every method does nothing and gives None (a key bind panel's `bind_kwargs`: {})."""
    def __init__(self, *args, **kwargs): pass
    def bind_kwargs(self): return {}
    def start(self): return self
    def __getattr__(self, name): return lambda *args, **kwargs: None


class NoPointer:
    """`unified_cursor.UnifiedPointer(control)` with no pointer: `view_controls` takes its rolls and wake, then the view
    has none (`control.overview_pointer` is None), as the Rust run has no overview pointer yet."""
    def __init__(self, control): self._control, self.rolls = control, [0] * 4
    @property
    def wake_reader(self):
        self._control.overview_pointer = None
        return None
    def close(self): pass


class NoKeys(Nothing):
    """`keybinds.BindControl(control, ...)` with no panel: `view_controls` installs the binds with it, then the view has
    none (`control.keys` is None), as the Rust run has no panel without `binds`'s helper command line."""
    def __init__(self, control, *args, **kwargs): self._control = control
    def startup_toast(self): self._control.keys = None


class NoVideo:
    """`video.VideoControl(control, ...)` with no video: every call the view's, `close` nothing."""
    def __init__(self, control, **kwargs): self._control = control
    def close(self): pass
    def __getattr__(self, name): return getattr(self._control, name)


def stub_binds(managed):
    """`binds`: no Ctrl+Alt binds in or out, and no read of the session mode in `view_controls`'s body."""
    quiet('install_view_binds', 'remove_view_binds')
    hide('reinstall_view_binds')
    body = managed.view_controls.__wrapped__.__code__

    def no_session_mode(name, value):
        if name != 'query': return value
        def query(*args, **kwargs):
            if sys._getframe(1).f_code is body and args[:1] == ('monitors',): return []
            return value(*args, **kwargs)
        return query
    Client.filters.append(no_session_mode)


def apply_stubs():
    from tools.native_spike import immersive, keybinds, managed, unified_cursor, video
    # One entry per feature still a stub in Rust. A port deletes its own line.
    STUBS = {
        'binds': lambda: stub_binds(managed),
        'overview_pointer': lambda: (patch(unified_cursor, 'UnifiedSampler', Nothing),
                                     patch(unified_cursor, 'UnifiedPointer', NoPointer)),
        'immersive': lambda: patch(immersive, 'find_engine', lambda renderer: None),
        'keybinds': lambda: (hide('binds_text', 'open_key_panel'), patch(keybinds, 'BindControl', NoKeys)),
        'video': lambda: (hide('install_video_binds'), patch(video, 'VideoControl', NoVideo)),
        'desktops': lambda: hide('adopt_windows'),
    }
    for stub in STUBS.values(): stub()
    shipped = managed.view_controls

    @contextlib.contextmanager
    def view_controls(client, *args, **kwargs):
        with shipped(Client(client), *args, **kwargs) as control:
            yield control
    managed.view_controls = view_controls


# The guard's worker is a multiprocessing spawn of this file: it must not start a run of its own.
if __name__ == '__main__':
    sys.path.insert(0, sys.argv[1])
    apply_stubs()
    if sys.argv[2] == 'runner':
        from tools.xr_controller import runner
        sys.exit(runner.main(sys.argv[3:]))
    if sys.argv[2] == 'recover-run':
        import runpy
        sys.argv = ['tools.xr_controller', 'recover-run', *sys.argv[3:]]
        runpy.run_module('tools.xr_controller', run_name='__main__')
    raise SystemExit('usage: oracle.py REPO runner|recover-run ...')
