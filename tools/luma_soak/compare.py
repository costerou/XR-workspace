"""The D13 drop gate (docs/rust-rebuild-plan.md): the Rust xr-luma-driver must show no more USB drops than the C++ one over
equal hours. Judges two stored soak reports (`gate --json`), so the 3-hour runs can be days apart."""
from .analyze import EXIT_DROP, EXIT_INCONCLUSIVE, EXIT_PASS, build_problem

EQUAL_HOURS = 0.95  # the Rust soak must cover at least this share of the baseline's hours; a longer one is fine


def compare(baseline, candidate):
    """{'result', 'exit', 'reasons', ...}: PASS when the candidate's drops are at most the baseline's, scaled to its hours."""
    reasons = []
    for name, report, build in (('C++ baseline', baseline, 'cpp'), ('Rust soak', candidate, 'rust')):
        verdict = report.get('verdict') or {}
        if verdict.get('exit') != EXIT_PASS:
            why = '; '.join(verdict.get('reasons') or ()) or verdict.get('result', 'no verdict')
            reasons.append(f'{name} is not a valid measurement: {why}')
        if problem := build_problem((report.get('coverage') or {}).get('driver_exes'), build):
            reasons.append(f'{name}: {problem}')
        if not report.get('duration_s'):
            reasons.append(f'{name} has no duration')
    if not reasons:
        base_h, cand_h = baseline['duration_s'] / 3600, candidate['duration_s'] / 3600
        if cand_h < base_h * EQUAL_HOURS:
            reasons.append(f'Rust soak ran {cand_h:.2f} h, the C++ baseline {base_h:.2f} h: not equal hours')
    if reasons:
        return {'result': 'INCONCLUSIVE', 'exit': EXIT_INCONCLUSIVE, 'reasons': reasons}
    allowed = baseline['drops'] * cand_h / base_h
    passed = candidate['drops'] <= allowed
    summary = (f"Rust {candidate['drops']} drop(s) in {cand_h:.2f} h, C++ {baseline['drops']} in {base_h:.2f} h: "
               f"{allowed:.2f} allowed over the Rust soak's hours")
    return {'result': 'PASS' if passed else 'FAIL', 'exit': EXIT_PASS if passed else EXIT_DROP, 'reasons': [summary],
            'rust': {'drops': candidate['drops'], 'hours': round(cand_h, 3)},
            'cpp': {'drops': baseline['drops'], 'hours': round(base_h, 3)}, 'allowed': round(allowed, 3)}
