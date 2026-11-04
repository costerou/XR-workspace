"""Does a driver keep the rules on the wire? Judged from a capture, for any driver.

The rules are the zero-drop rules in spike/driver/docs/protocol.md ("Against the bar"). None is proven to stop
drops; each removes something the evidence ties to them. tools/luma_soak says whether a run dropped; this says
what the driver did, and XRLinuxDriver failing here is expected: it is the baseline.
"""
MIN_SETTLE_S = 2.0
THIN_SHARE = 0.01
MINIMUM_TRANSFERS = 1000


def audit(result, baseline=None):
    """Rules as {'rule', 'ok', 'detail'}; ok is None when this capture cannot judge it."""
    facts = result['wire_facts']
    rules = []

    def rule(name, ok, detail):
        rules.append({'rule': name, 'ok': ok, 'detail': detail})

    forbidden = facts['forbidden']
    rule('No reset, SET_CONFIGURATION, SET_INTERFACE or clear halt', not forbidden,
         ', '.join(f'{name} x{count}' for name, count in sorted(forbidden.items())) or 'none seen')
    steady = facts['steady_commands']
    rule('No periodic commands once running', not steady,
         (', '.join(f'{name} x{count}' for name, count in sorted(steady.items()))
          + f" ({facts['steady_per_s']}/s)") if steady else 'none after the first 10 s of a connection')
    rule('One write at a time', facts['most_writes_at_once'] <= 1, f"at most {facts['most_writes_at_once']} at once")
    queued = {name: row for name, row in facts['queued_reads'].items() if row['completions'] >= MINIMUM_TRANSFERS}
    if queued:
        thin = {name: row['thin'] / row['completions'] for name, row in queued.items()}
        rule('Two reads always queued on the stream', all(share <= THIN_SHARE for share in thin.values()),
             ', '.join(f'{name}: fewer than two queued for {share:.1%} of transfers' for name, share in thin.items()))
    else:
        rule('Two reads always queued on the stream', None, 'no stream long enough to judge')
    idle = facts['reads_never_answered']
    rule('No reads on endpoints that never answer', not idle, ', '.join(idle) or 'none')
    bad_crc = result['framing']['frames_with_bad_crc']
    rule('Every frame sent has a good CRC', not bad_crc, f'{bad_crc} bad')
    settled = [drop['first_command_after_s'] for drop in result['drops']
               if drop['confirmed'] and drop['first_command_after_s'] is not None]
    if settled:
        rule(f'Waits at least {MIN_SETTLE_S:g} s after a disconnect', min(settled) >= MIN_SETTLE_S,
             f'quickest first command after a disconnect: {min(settled)} s')
    else:
        rule(f'Waits at least {MIN_SETTLE_S:g} s after a disconnect', None, 'no reconnect in this capture')
    used = set(result['commands'])
    if baseline is None:
        mcu = sorted(key for key in used if key.startswith('MCU'))
        rule('Nothing on the flashing pipe beyond what VITURE software sends', None if mcu else True,
             f"sent {', '.join(mcu)}; compare with --baseline" if mcu else 'nothing sent there')
    else:
        extra = sorted(used - set(baseline['commands']))
        rule('Sends nothing the baseline did not', not extra, ', '.join(extra) or 'nothing new')
    return rules


def render_audit(rules):
    marks = {True: 'pass', False: 'FAIL', None: '  ? '}
    lines = ['', 'Wire rules']
    lines += [f"  {marks[item['ok']]}  {item['rule']}: {item['detail']}" for item in rules]
    return '\n'.join(lines) + '\n'


def passed(rules):
    return not any(item['ok'] is False for item in rules)


def compare(before, after):
    """Two captures side by side, usually XRLinuxDriver against ours. Rows are (what, before, after)."""
    rows = []

    def row(what, read):
        rows.append((what, read(before), read(after)))

    def stream(key):
        def read(result):
            found = [s[key] for s in result['streams'].values()]
            return max(found) if found else None
        return read

    row('Commands per second', lambda r: round(sum(c['per_s'] for c in r['commands'].values()), 3))
    row('0x0007 per second', lambda r: r['msgid_0x0007']['per_s'] if r['msgid_0x0007'] else None)
    row('Commands per second once running', lambda r: r['wire_facts']['steady_per_s'])
    row('Bytes per second', lambda r: round(sum(e['bytes'] for e in r['endpoints'].values())
                                            / max(r['seconds_with_traffic'], 1)))
    row('Stream transfers per second', stream('hz'))
    row('Stream spacing, median us', stream('p50_us'))
    row('Stream spacing, 99.9% us', stream('p999_us'))
    row('Stream spacing, worst us', stream('max_us'))
    row('Late arrivals', stream('gaps'))
    row('Disconnects in this capture', lambda r: sum(d['confirmed'] for d in r['drops']))
    row('Failed transfers', lambda r: sum(f['count'] for f in r['failures']))
    row('Wire rules failed', lambda r: sum(item['ok'] is False for item in audit(r)))
    return rows


def render_compare(rows, names=('before', 'after')):
    lines = [f"{'':<36}{names[0]:>14}{names[1]:>14}"]
    lines += [f"{what:<36}{'-' if a is None else a:>14}{'-' if b is None else b:>14}" for what, a, b in rows]
    return '\n'.join(lines) + '\n'
