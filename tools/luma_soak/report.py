"""The plain-text soak report."""

SIGNAL_NAMES = {'command_failed': 'USB command failed', 'feed_gap': 'pose feed gap', 'cpu_high': 'CPU busy', 'kernel_trouble': 'kernel USB warning'}


def _short(at):
    return at[:23].replace('T', ' ') if at else '?'


def render(report, limit=20):
    verdict = report['verdict']
    out = [f"Luma soak: {verdict['result']}"]
    out.extend(f'  {reason}' for reason in verdict['reasons'])
    out.append('')
    out.append(f"{report['mode']}, {_short(report['started'])} to {_short(report['ended'])}, {report['duration_s']} s"
               + (f", stopped by {report['stop_reason']}" if report['stop_reason'] else ''))
    out.append(f"Drops: {report['drops']} ({report['hub_drops']} hub, {report['glasses_only_drops']} glasses only, {report['sysfs_only_drops']} seen only in sysfs), "
               f"{report['bursts']} burst(s), {report['drops_per_hour']} per hour")
    if report['recovery_s']:
        recovery = report['recovery_s']
        out.append(f"Back on USB: average {recovery['avg']} s, worst {recovery['max']} s; {report['never_recovered']} drop(s) with no return before the next drop or the end")
    if events := report.get('driver_events'):
        gap = events['reconnect_gap_ms']
        out.append(f"Driver saw: {events['disconnects']} disconnect(s), {events['retries']} retry(s), {events['stale']} stall(s), {events['reconnected']} reconnect(s)"
                   + (f", reconnect average {gap['avg']} ms, worst {gap['max']} ms" if gap else ''))
    coverage, watch = report['coverage'], report['watch']
    if report['mode'] == 'live':
        out.append(f"Watch: audit {watch['audit']}, glasses at start {watch['glasses_at_start']}, at end {watch['glasses_at_end']}, "
                   f"driver alive {coverage['driver']} ({coverage['driver_processes']} process(es)), feed advancing {coverage['feed']} at {coverage['feed_samples_per_s']} samples/s, worst feed gap {coverage['worst_feed_gap_ms']} ms")
        out.extend(f'  stream note: {why}' for why in watch['blind'])
    if report['siblings'] or report['kernel_trouble'] or report['unattributed']:
        out.append(f"Also: {len(report['siblings'])} camera/mic disconnect(s), {len(report['kernel_trouble'])} kernel USB warning(s), {len(report['unattributed'])} unidentified disconnect(s)")
    signals = report['signals']
    if signals and report['drops']:
        out.extend(['', f"Before a drop, against ordinary {report['drop_details'][0]['window_s']:g} s windows (a match in both is chance, not cause):"])
        for name, row in signals.items():
            out.append(f"  {SIGNAL_NAMES[name]}: {row['before_drops']} of {row['drops']} drops, {row['quiet_windows']} of {row['quiet_total']} quiet windows")
    details = report['drop_details']
    if details:
        out.extend(['', f"Drops{f' (last {limit} of {len(details)})' if len(details) > limit else ''}:"])
    for drop in details[-limit:]:
        ports = ', '.join(sorted(m['path'] for m in drop['members']))
        back = f"back in {drop['recovered_s']} s" if drop['recovered_s'] is not None else 'did not return'
        out.append(f"  {_short(drop['at'])}  {drop['scope']}  [{ports}]  {back}")
        out.extend(f'      {hint}' for hint in drop['hints'])
        if not drop['hints']:
            observed = drop['feed']['observed'] or drop['cpu']['driver_running'] is not None or drop['driver']['last_lines']
            out.append('      nothing unusual in the window' if observed else '      no driver, feed or CPU data covers this window')
    return '\n'.join(out) + '\n'


