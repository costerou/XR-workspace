"""A fault in the supervisor: what the journal says of it, and what the run's result keeps for its incident report.

A run's handlers (a key, a Hyprland event, a renderer status line, the loop's poll) all share one loop, and a bug in any of
them used to take the session with it, with one line in the result to show for it (Supervisor._contained, runner.execute).

The journal gets the whole traceback, once for each place that fails. The result gets a short form: read_private_json refuses
a file over 16 KiB, and a run with no readable result looks like one that was killed.
"""
import sys
import traceback

RUN = 'the run'     # the `where` of an exception that ended the run (runner.execute)
MAX_FAULTS = 3      # different ones kept in full; any more are only counted
MAX_FRAMES = 12     # the innermost
MAX_TEXT = 160      # characters of a line


def _site(frame):
    name = frame.filename
    at = name.rfind('/tools/')
    return f'{name[at + 1:] if at >= 0 else name.rsplit("/", 1)[-1]}:{frame.lineno} {frame.name}'[-MAX_TEXT:]


def short_traceback(exc):
    """`exc` in a few lines: where, innermost last, one line each, then the exception itself."""
    frames = traceback.extract_tb(exc.__traceback__)[-MAX_FRAMES:]
    return [_site(frame) for frame in frames] + [''.join(traceback.format_exception_only(exc)).strip()[:MAX_TEXT]]


class Faults:
    """What went wrong in a run. Only ever called from the supervisor's thread, and then runner.execute's after it."""
    def __init__(self):
        self.kept = {}
        self.more = 0

    def record(self, where, exc, *, fatal=False):
        """`exc`, caught at `where`. Never raises: it runs inside the net that keeps a session alive."""
        last = traceback.extract_tb(exc.__traceback__)[-1:]
        key = (where, type(exc), last[0].filename if last else '', last[0].lineno if last else 0)
        known = self.kept.get(key)
        if known is not None:
            known['count'] += 1
            return
        if len(self.kept) >= MAX_FAULTS: self.more += 1
        else: self.kept[key] = dict(where=where[:MAX_TEXT], count=1, traceback=short_traceback(exc))
        try:
            print(f"xr-workspace: error in {where}{'' if fatal else ', the run carries on'}:\n{''.join(traceback.format_exception(exc))}",
                  file=sys.stderr, flush=True)
        except (OSError, ValueError): pass

    def report(self):
        """For the run's result: [] when all went well, so a clean result stays as it was."""
        out = [dict(fault) for fault in self.kept.values()]
        return out + [dict(where='other faults', count=self.more, traceback=[])] if self.more else out
