"""Read-only diagnostic CLI. No SDK, capture, or configuration changes."""
import argparse
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import sys
import tempfile

from .commands import run_probe
from .hardware import probe_drm, probe_usb
from .report import build_report, render_markdown, to_json
from .session import discover_sessions, select_session


def collect_host(instance, *, run=run_probe):
    """Optional metadata from existing allowlisted probes; failures stay unknown.

    pkg-config reports the installed development metadata version, not a
    package-manager version. Unqueried package versions are deliberately absent.
    """
    host = {'packages': {}}
    architecture = run(['uname', '-m'])
    if architecture.status == 'ok' and isinstance(architecture.data, str):
        host['architecture'] = architecture.data.strip()
    pci = run(['lspci'])
    if pci.status == 'ok' and isinstance(pci.data, str):
        gpu = [line for line in pci.data.splitlines() if any(label in line for label in
               ('VGA compatible controller:', '3D controller:', 'Display controller:'))]
        if gpu:
            host['gpu'] = '; '.join(gpu)
    version = run(['hyprctl', '-i', instance, 'version', '-j'])
    if version.status == 'ok':
        try:
            raw = json.loads(version.data)
            if isinstance(raw, dict) and isinstance(raw.get('version'), str):
                host['packages']['hyprland'] = raw['version']
        except (ValueError, TypeError):
            pass
    drm = run(['pkg-config', '--modversion', 'libdrm'])
    if drm.status == 'ok' and isinstance(drm.data, str):
        host['packages']['libdrm'] = drm.data.strip()
    return host


def write_reports(directory, report, *, overwrite=False):
    """Stage both complete files before replacing either; roll back on errors.

    A directory lock serializes cooperating CLI writers. Each rename is atomic,
    NOT the pair: readers must wait for CLI completion. A crash/power loss may
    leave the lock/staging directory for manual recovery. If rollback itself
    fails, backups are retained there rather than deleting prior evidence.
    """
    if directory.exists() and not directory.is_dir():
        raise NotADirectoryError('Output path is not a directory')
    directory.mkdir(parents=True, exist_ok=True)
    paths = [directory / 'host-report.json', directory / 'host-report.md']
    lock = directory / '.xr-probe.lock'
    lock.mkdir()
    stage = None
    preserve = False
    try:
        for path in paths:
            if path.is_symlink() or (path.exists() and not path.is_file()):
                raise FileExistsError('Report target is not a regular file')
            if path.exists() and not overwrite:
                raise FileExistsError('Existing report requires --overwrite')
        stage = Path(tempfile.mkdtemp(prefix='staged-', dir=lock))
        payloads = [to_json(report) + '\n', render_markdown(report)]
        backups = {}
        for index, (path, text) in enumerate(zip(paths, payloads)):
            if path.exists():
                backup = stage / f'old-{index}'
                # Hard links preserve old bytes without a second potentially
                # partial copy; replacement never writes into an existing inode.
                os.link(path, backup)
                backups[path] = backup
            with (stage / f'new-{index}').open('x', encoding='utf-8') as stream:
                stream.write(text)
                stream.flush()
                os.fsync(stream.fileno())
        replaced = []
        try:
            for index, path in enumerate(paths):
                os.replace(stage / f'new-{index}', path)
                replaced.append(path)
        except OSError:
            try:
                for path in reversed(replaced):
                    if path in backups:
                        os.replace(backups[path], path)
                    else:
                        path.unlink()
            except OSError:
                preserve = True
                raise OSError('Rollback failed; backups retained in .xr-probe.lock') from None
            raise
    finally:
        if not preserve:
            if stage is not None:
                for child in stage.iterdir():
                    child.unlink()
                stage.rmdir()
            lock.rmdir()


def main(argv=None, *, discover=discover_sessions, usb=probe_usb,
         drm=probe_drm, host=collect_host):
    parser = argparse.ArgumentParser(description='Read-only XR host inventory; not an XR workspace app.')
    parser.add_argument('--list-sessions', action='store_true', help='List observed instance identifiers and classifications as JSON')
    parser.add_argument('--instance', help='Exact observed physical Hyprland instance identifier')
    parser.add_argument('--output-dir', type=Path, help='Directory for host-report.json and host-report.md')
    parser.add_argument('--overwrite', action='store_true', help='Explicitly replace existing report evidence (version it in Git first)')
    try:
        args = parser.parse_args(argv)
        if args.list_sessions:
            if args.instance is not None or args.output_dir is not None or args.overwrite:
                parser.error('--list-sessions cannot be combined with collection options')
        elif args.output_dir is None:
            parser.error('--output-dir is required for collection')
    except SystemExit as exc:
        return exc.code
    try:
        discovery = discover()
        if discovery.status != 'ok':
            print('Session discovery failed: ' + discovery.status, file=sys.stderr)
            return 3
        if args.list_sessions:
            print(json.dumps([{'instance': c.instance, 'classification': c.classification}
                              for c in discovery.data], indent=2))
            return 0
        selected = select_session(discovery.data, args.instance)
        if selected.status != 'ok':
            print(selected.detail, file=sys.stderr)
            return 2
        usb_result, drm_result = usb(), drm()
        report = build_report(selected, usb_result, drm_result, host(selected.data.instance),
                              collected_at=datetime.now(timezone.utc).isoformat())
        try:
            write_reports(args.output_dir, report, overwrite=args.overwrite)
        except FileExistsError:
            print('Reports protected: existing target, unsafe target, or writer lock; use a new directory or --overwrite for regular reports.', file=sys.stderr)
            return 2
        print('Wrote host-report.json and host-report.md. Tracking and sustained refresh remain untested.')
        if report.usb.status == 'ok' and report.usb.data == []:
            print('No matching VITURE USB device observed; glasses display association and capabilities remain unknown.')
        if report.usb.status != 'ok' or report.drm.status != 'ok':
            print('Partial report: a required inventory failed; see report statuses.', file=sys.stderr)
            return 3
        return 0
    except (OSError, ValueError, TypeError, OverflowError):
        print('Host collection or report write failed. Check output permissions and .xr-probe.lock for retained recovery files; raw errors withheld.', file=sys.stderr)
        return 3


if __name__ == '__main__':
    raise SystemExit(main())
