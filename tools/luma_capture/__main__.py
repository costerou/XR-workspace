"""python3 -m tools.luma_capture check|record|report|show|merge|pcap. See tools/luma_capture/README.md."""
import argparse
import json
import os
from pathlib import Path
import signal
import sys

from . import capfile, pcap
from .audit import audit, compare, passed, render_audit, render_compare
from .protocol import Framing, Labeler, status_name
from .recorder import Recorder, add_mark, describe, start_followers
from .report import analyze, clock, render
from .usbmon import UsbmonSource, find_glasses, grant_commands, revoke_commands, scan_devices

DEFAULT_OUT = Path.home() / '.local/state/viture-workspace/luma-capture'
DRIVER_LOG = Path.home() / '.local/state/xr_driver/driver.log'


def access_problem(bus, *, dev_root='/dev'):
    """Why /dev/usbmonN cannot be read, or None. Does not open it."""
    path = Path(dev_root) / f'usbmon{bus}'
    if not path.exists():
        return f'{path} does not exist: the usbmon module is not loaded'
    if not os.access(path, os.R_OK):
        return f'{path} exists but this user may not read it'
    return None


def command_check(args):
    glasses = find_glasses()
    bus = args.bus or (glasses['bus'] if glasses else None)
    if glasses:
        print(f"Glasses: bus {glasses['bus']} device {glasses['dev']} at {glasses['path']}")
    else:
        print('Glasses: not plugged in')
    if bus is None:
        print('Bus unknown: plug the glasses in or pass --bus')
        return 1
    problem = access_problem(bus)
    if problem is None:
        print(f'Capture access: ready (/dev/usbmon{bus})')
        return 0
    print(f'Capture access: {problem}\n\nRun these once, they last until the next reboot:')
    print('\n'.join('  ' + line for line in grant_commands(bus)))
    print('\nTo take the access back sooner:')
    print('\n'.join('  ' + line for line in revoke_commands(bus)))
    return 1


def command_record(args):
    glasses = find_glasses()
    bus = args.bus or (glasses['bus'] if glasses else None)
    if bus is None:
        sys.exit('Glasses not plugged in, so no bus to watch: plug them in or pass --bus')
    problem = access_problem(bus)
    if problem:
        sys.exit(f'{problem}. Run: python3 -m tools.luma_capture check')
    source = UsbmonSource(bus, fetch_bytes=args.max_payload)
    recorder = Recorder(source, args.out, bus, scan=scan_devices, segment_s=args.segment,
                        keep_segments=args.keep_segments,
                        keep_start=args.keep_start, post_roll_s=args.post_roll,
                        stop_after_drops=args.stop_after_drops, max_payload=args.max_payload,
                        max_total_bytes=args.max_gib << 30, duration_s=args.duration)
    for number in (signal.SIGINT, signal.SIGTERM):
        signal.signal(number, lambda *_: recorder.stop('asked to stop'))
    followers = []

    def started():
        followers.append(start_followers(recorder, args.driver_log))
        print(f'Recording bus {bus} to {args.out}; Ctrl+C to stop', flush=True)

    try:
        recorder.run(started=started)
    finally:
        for follower in followers:
            follower.set()
        source.close()
    print(describe(recorder))
    return 0


def command_report(args):
    framing = Framing.parse(args.framing) if args.framing else None
    result = analyze(args.paths, framing=framing, window_s=args.window, rows=args.rows)
    baseline = analyze(args.baseline, framing=framing) if args.baseline else None
    rules = audit(result, baseline)
    if args.json:
        print(json.dumps({**result, 'wire_rules': rules}, indent=1))
    else:
        print(render(result) + render_audit(rules), end='')
    return 0 if passed(rules) or not args.audit else 1


def command_compare(args):
    framing = Framing.parse(args.framing) if args.framing else None
    before, after = analyze([args.before], framing=framing), analyze([args.after], framing=framing)
    print(render_compare(compare(before, after)), end='')
    print(render_audit(audit(after, before)), end='')
    return 0


def command_show(args):
    labeler = Labeler(Framing.parse(args.framing) if args.framing else None)
    for count, record in enumerate(capfile.records(args.paths)):
        if count >= args.limit:
            break
        if record.is_note:
            print(clock(record.t_ns), 'note', record.data.decode('utf-8', 'replace'))
        else:
            print(clock(record.t_ns), record.event, f'dev{record.dev}', labeler.label(record).text,
                  status_name(record.status), record.data[:32].hex(' '))
    return 0


def command_mark(args):
    add_mark(args.out, ' '.join(args.text))
    return 0


def command_merge(args):
    print(f'{capfile.merge(args.paths, args.output)} records in {args.output}')
    return 0


def command_pcap(args):
    print(f'{pcap.export(args.paths, args.output)} packets in {args.output}')
    return 0


def parser():
    top = argparse.ArgumentParser(prog='python3 -m tools.luma_capture', description=__doc__)
    commands = top.add_subparsers(dest='command', required=True)
    check = commands.add_parser('check', help='is capture possible, and if not, the sudo steps')
    check.add_argument('--bus', type=int)
    check.set_defaults(run=command_check)
    record = commands.add_parser('record', help='record until stopped; never touches the glasses')
    record.add_argument('--bus', type=int)
    record.add_argument('--out', type=Path, default=DEFAULT_OUT)
    record.add_argument('--driver-log', type=Path, default=DRIVER_LOG)
    record.add_argument('--segment', type=float, default=30, help='seconds per file')
    record.add_argument('--keep-segments', type=int, default=10, help='files kept before a drop')
    record.add_argument('--keep-start', type=int, default=2, help='first files kept for good')
    record.add_argument('--post-roll', type=float, default=30, help='seconds kept after a drop')
    record.add_argument('--stop-after-drops', type=int, default=10)
    record.add_argument('--max-payload', type=int, default=1 << 16, help='most bytes kept per transfer')
    record.add_argument('--max-gib', type=int, default=4, help='stop if the folder grows past this')
    record.add_argument('--duration', type=float, help='stop after this many seconds')
    record.set_defaults(run=command_record)
    report = commands.add_parser('report', help='command rates and what preceded each disconnect')
    report.add_argument('paths', nargs='+')
    report.add_argument('--framing', help='offset=N[,endian=little|big][,magic=hex], when known')
    report.add_argument('--window', type=float, default=5, help='seconds shown before a disconnect')
    report.add_argument('--rows', type=int, default=200)
    report.add_argument('--json', action='store_true')
    report.add_argument('--audit', action='store_true', help='exit 1 if a wire rule fails')
    report.add_argument('--baseline', nargs='+', help="a capture of VITURE's own software, for the audit")
    report.set_defaults(run=command_report)
    versus = commands.add_parser('compare', help='two captures side by side')
    versus.add_argument('before')
    versus.add_argument('after')
    versus.add_argument('--framing')
    versus.set_defaults(run=command_compare)
    show = commands.add_parser('show', help='print records')
    show.add_argument('paths', nargs='+')
    show.add_argument('--limit', type=int, default=200)
    show.add_argument('--framing')
    show.set_defaults(run=command_show)
    mark = commands.add_parser('mark', help='note what you just did, for a running recorder')
    mark.add_argument('text', nargs='+')
    mark.add_argument('--out', type=Path, default=DEFAULT_OUT)
    mark.set_defaults(run=command_mark)
    merge = commands.add_parser('merge', help='join segments into one file, for --replay')
    merge.add_argument('paths', nargs='+')
    merge.add_argument('--output', '-o', required=True)
    merge.set_defaults(run=command_merge)
    export = commands.add_parser('pcap', help='write a pcap for Wireshark')
    export.add_argument('paths', nargs='+')
    export.add_argument('--output', '-o', required=True)
    export.set_defaults(run=command_pcap)
    return top


def main(argv=None):
    args = parser().parse_args(argv)
    try:
        return args.run(args)
    except capfile.CaptureError as error:
        sys.exit(str(error))


if __name__ == '__main__':
    sys.exit(main())
