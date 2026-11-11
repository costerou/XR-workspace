"""The Python side of tests/managed_real.rs: `tools.xr_controller.runner` as the run unit runs it, with one change.

`python3 -I oracle.py <repo> runner --consent C --state-dir S`, or `... recover-run --consent C --journal J`.

The change: `managed.view_controls` yields no view control, as the Rust supervisor's managed run has none yet (the glasses'
Ctrl+Alt controls, the overview pointer, the panel's control socket: rust/README.md). It keeps one thing of its `finally`,
the window memory's last capture, which the Rust run has ported. Everything else (the consent, the
pins, the plugin, the broker, the journal, adoption, the renderer launch and its watchdog, recovery) is the shipped code.
Which Rust port takes this (and the other, implicit, exclusions) away: tests/managed_real.rs's module doc and
rust/crates/xr-workspace/VIEW_CONTROLS.md.
"""
import contextlib
import sys



@contextlib.contextmanager
def no_view_controls(client, run_id, session, capture, output=None, store=None, fallback=None, desktops=None, windows=None,
                     **kwargs):
    """No view control; only the window memory's last capture, as `view_controls`'s `finally` takes it (window_memory port)."""
    try:
        yield None
    finally:
        if windows is not None: windows.close()


# The guard's worker is a multiprocessing spawn of this file: it must not start a run of its own.
if __name__ == '__main__':
    sys.path.insert(0, sys.argv[1])
    from tools.native_spike import managed
    managed.view_controls = no_view_controls
    if sys.argv[2] == 'runner':
        from tools.xr_controller import runner
        sys.exit(runner.main(sys.argv[3:]))
    if sys.argv[2] == 'recover-run':
        import runpy
        sys.argv = ['tools.xr_controller', 'recover-run', *sys.argv[3:]]
        runpy.run_module('tools.xr_controller', run_name='__main__')
    raise SystemExit('usage: oracle.py REPO runner|recover-run ...')
