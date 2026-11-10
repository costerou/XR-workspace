"""python3 -m tools.luma_soak: watch for USB drops of the Luma Ultra, and judge a soak. Read-only on the machine.

    monitor   watch until --duration or Ctrl-C, then print the report
    gate      the same, then exit 0 only for a proven zero-drop soak; or judge an existing --session
    report    print the report of a --session, or of saved logs
    compare   the D13 drop gate: a Rust soak's stored report against the C++ baseline's
    sanitize  strip serials and the host name from a log on stdin, to make a fixture

gate exit codes: 0 PASS, 1 a drop (or a --strict or --max-feed-gap-ms limit), 2 INCONCLUSIVE (the soak proved nothing).
compare exit codes: 0 PASS (no more drops than the C++ baseline over equal hours), 1 FAIL, 2 INCONCLUSIVE (a report proves nothing).
"""
import argparse
import json
import os
from pathlib import Path
import signal
import sys
import time

from . import report as report_text, sanitize
from .analyze import BUILDS, EXIT_INCONCLUSIVE, Policy, analyze, records_from_logs
from .compare import compare
from .kernel import GLASSES_IDS, HUB_IDS
from .monitor import Config, Monitor, load_session
from .probes import DEFAULT_FEED, DRIVER_NAMES

DRIVER_LOG = Path.home() / '.local/state/xr_driver/driver.log'
STATE = Path(os.environ.get('XDG_STATE_HOME') or Path.home() / '.local/state') / 'luma_soak'


def _parser():
    parser = argparse.ArgumentParser(prog='luma_soak', description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    commands = parser.add_subparsers(dest='command', required=True)
    judge = argparse.ArgumentParser(add_help=False)
    judge.add_argument('--window', type=float, default=5.0, help='seconds looked at before each drop')
    judge.add_argument('--budget', type=int, default=0, help='drops allowed')
    judge.add_argument('--strict', action='store_true', help='a camera/mic disconnect, a kernel USB warning or a driver restart also fails')
    judge.add_argument('--max-feed-gap-ms', type=float, help='also fail on a pose feed gap longer than this')
    judge.add_argument('--no-require-driver', action='store_true', help='a soak with no driver running can still pass')
    judge.add_argument('--no-require-feed', action='store_true', help='a soak with no advancing pose feed can still pass')
    judge.add_argument('--driver-build', choices=BUILDS, help='only a soak of this build of xr-luma-driver can pass')
    judge.add_argument('--port', help='treat an unidentified disconnect at or under this port (e.g. 5-2) as a drop')
    judge.add_argument('--json', action='store_true', help='print the report as JSON')
    judge.add_argument('--glasses-id', action='append', metavar='VID:PID', help=f'default {", ".join(sorted(GLASSES_IDS))}')
    judge.add_argument('--hub-id', action='append', metavar='VID:PID', help=f'default {", ".join(sorted(HUB_IDS))}')
    live = argparse.ArgumentParser(add_help=False)
    live.add_argument('--duration', type=float, help='seconds to watch')
    live.add_argument('--session', type=Path, help=f'session file; default a new one in {STATE}')
    live.add_argument('--driver-log', action='append', type=Path, help=f'log or stderr file to follow; default {DRIVER_LOG}')
    live.add_argument('--driver-name', action='append', help=f'process name to watch; default {", ".join(DRIVER_NAMES)}')
    live.add_argument('--feed', default=DEFAULT_FEED, help='pose segment path')
    live.add_argument('--fail-fast', action='store_true', help='stop at the first drop')
    live.add_argument('--journalctl', default='journalctl', help=argparse.SUPPRESS)
    live.add_argument('--sysfs', default='/sys/bus/usb/devices', help=argparse.SUPPRESS)
    live.add_argument('--proc', default='/proc', help=argparse.SUPPRESS)
    live.add_argument('--settle', type=float, default=1.0, help=argparse.SUPPRESS)
    live.add_argument('--tick', type=float, default=1.0, help=argparse.SUPPRESS)
    commands.add_parser('monitor', parents=[judge, live], help='watch and report')
    gate = commands.add_parser('gate', parents=[judge, live], help='exit nonzero unless the soak proved zero drops')
    gate.add_argument('--min-duration', type=float, help='seconds a soak must have run; default --duration, else 1800')
    gate.add_argument('--kernel-log', type=Path, help='judge a saved log: can fail, can never pass')
    gate.add_argument('--replay-driver-log', type=Path, help=argparse.SUPPRESS)
    report = commands.add_parser('report', parents=[judge], help='report on a session or on saved logs')
    report.add_argument('--session', type=Path)
    report.add_argument('--kernel-log', type=Path)
    report.add_argument('--replay-driver-log', '--driver-log', dest='replay_driver_log', type=Path)
    report.add_argument('--since', type=float, help='epoch seconds')
    report.add_argument('--until', type=float, help='epoch seconds')
    report.add_argument('--last', type=int, default=20, help='drops to detail in the text report')
    against = commands.add_parser('compare', help='the D13 drop gate: a Rust soak report against the C++ baseline report')
    against.add_argument('--baseline', type=Path, required=True, help='the C++ soak, a stored .report.json')
    against.add_argument('--candidate', type=Path, required=True, help='the Rust soak, a stored .report.json')
    against.add_argument('--json', action='store_true')
    clean = commands.add_parser('sanitize', help='stdin to stdout')
    clean.add_argument('--kernel-usb', action='store_true', help='keep only kernel USB lines')
    return parser


def _ids(given, default):
    return frozenset(i.lower() for i in given) if given else default


def _policy(args):
    return Policy(window_s=args.window, port=args.port, budget=args.budget, strict=args.strict, max_feed_gap_ms=args.max_feed_gap_ms,
                  driver_build=args.driver_build, require_driver=not args.no_require_driver, require_feed=not args.no_require_feed,
                  glasses_ids=_ids(args.glasses_id, GLASSES_IDS), hub_ids=_ids(args.hub_id, HUB_IDS),
                  since=getattr(args, 'since', None), until=getattr(args, 'until', None))


def _text(path):
    return Path(path).read_bytes().decode(errors='replace').splitlines()


def _replay(args):
    return records_from_logs(_text(args.kernel_log), _text(args.replay_driver_log) if args.replay_driver_log else ())


def _live(args):
    session = args.session or STATE / time.strftime('soak-%Y%m%d-%H%M%S.jsonl')
    logs = args.driver_log if args.driver_log else [DRIVER_LOG]
    config = Config(session=session, duration_s=args.duration, journalctl=args.journalctl, driver_logs=logs, sysfs=args.sysfs, proc=args.proc,
                    feed=args.feed, driver_names=tuple(args.driver_name) if args.driver_name else DRIVER_NAMES,
                    glasses_ids=_ids(args.glasses_id, GLASSES_IDS), hub_ids=_ids(args.hub_id, HUB_IDS),
                    fail_fast=args.fail_fast, settle_s=args.settle, tick_s=args.tick)
    print(f'session: {session}', file=sys.stderr)
    monitor = Monitor(config, log=lambda text: print(text, file=sys.stderr, flush=True))
    for name in (signal.SIGINT, signal.SIGTERM):
        signal.signal(name, lambda *_: monitor.request_stop('interrupted'))
    records = monitor.run()
    return records, session


def _show(report, args):
    print(json.dumps(report, indent=2) if args.json else report_text.render(report, getattr(args, 'last', 20)), end='\n' if args.json else '')


def _run(args):
    if args.command == 'sanitize':
        sys.stdin.reconfigure(errors='replace')  # the driver log holds raw heap bytes
        for line in sanitize.lines(sys.stdin, args.kernel_usb):
            print(line)
        return 0
    if args.command == 'compare':
        result = compare(json.loads(args.baseline.read_text()), json.loads(args.candidate.read_text()))
        if args.json:
            print(json.dumps(result, indent=2))
        else:
            print(f"drop gate: {result['result']}")
            for reason in result['reasons']:
                print(f'  {reason}')
        return result['exit']
    policy = _policy(args)
    if args.command == 'report':
        if bool(args.session) == bool(args.kernel_log):
            raise SystemExit('report needs --session or --kernel-log')
        _show(analyze(load_session(args.session) if args.session else _replay(args), policy), args)
        return 0
    if args.command == 'gate':
        policy.min_duration_s = args.min_duration if args.min_duration is not None else (args.duration or 1800.0)
        if args.kernel_log:
            records = _replay(args)
        elif args.session and args.duration is None:
            records = load_session(args.session)
        else:
            if args.duration is None:
                raise SystemExit('gate needs --duration to watch, or --session or --kernel-log to judge')
            records, _ = _live(args)
        report = analyze(records, policy)
        _show(report, args)
        return report['verdict']['exit']
    records, session = _live(args)
    report = analyze(records, policy)
    _show(report, args)
    Path(str(session) + '.report.json').write_text(json.dumps(report, indent=2))
    return 0


def main(argv=None):
    args = _parser().parse_args(argv)
    try:
        return _run(args)
    except Exception as error:  # a broken monitor proved nothing, and must never read as a clean soak or as a drop
        print(f'luma_soak: {type(error).__name__}: {error}', file=sys.stderr)
        return EXIT_INCONCLUSIVE
