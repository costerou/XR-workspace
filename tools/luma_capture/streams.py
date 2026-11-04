"""What a stream endpoint carries, worked out from the bytes.

Nobody has published the Luma Ultra's IMU frame, so this looks for what must be in one: a device clock (a field
that climbs steadily), three numbers whose length is one gravity while the glasses are still (the accelerometer,
as floats or as 16 bit integers), and how many samples share a transfer. Each finding is a candidate for a person
to confirm. The head must be still for the gravity test to mean anything, and one pose is not enough: with gravity
on one axis, any three neighbouring numbers that include that axis pass. A pose on each of the three axes settles it.

Arrival statistics describe the host's side only: how evenly transfers reached the kernel.
"""
from collections import Counter
import math
import statistics
import struct

SCAN_BYTES = 256
CLOCK_SCAN = 64
PROFILE_PACKETS = 400
MIN_PACKETS = 20
REGULAR = 0.9
GAP_FACTOR = 5
OUTAGE_US = 1_000_000
KEEP = 5
UNITS = {10**3: 'ms', 10**6: 'us', 10**9: 'ns'}
GRAVITY = {'g': 1.0, 'm/s2': 9.80665}
STILL = 0.1             # a share of one gravity
QUIET_INT16 = 0.03      # spread of an integer vector's length, for the same test without a known scale


def percentile(counts, share):
    """From a Counter of value to occurrences."""
    total = sum(counts.values())
    if not total:
        return 0
    wanted = share * total
    seen = 0
    for value in sorted(counts):
        seen += counts[value]
        if seen >= wanted:
            return value
    return value


def arrivals(deltas_us):
    """Summary of a Counter of microseconds between transfers. Gaps past a second are outages, not jitter."""
    steady = Counter({value: count for value, count in deltas_us.items() if value < OUTAGE_US})
    typical = percentile(steady, 0.5)
    late = sum(count for value, count in steady.items() if typical and value > GAP_FACTOR * typical)
    total = sum(steady.values())
    return {'count': total, 'hz': round(10**6 * total / sum(v * c for v, c in steady.items()), 1) if total and
            any(steady) else 0.0, 'p50_us': typical, 'p99_us': percentile(steady, 0.99),
            'p999_us': percentile(steady, 0.999), 'max_us': max(steady, default=0), 'gaps': late,
            'outages': sum(deltas_us.values()) - total}


def _common_prefix(payloads, limit=4):
    first = payloads[0]
    size = min(limit, min(map(len, payloads)))
    for index in range(size):
        if any(data[index] != first[index] for data in payloads):
            return first[:index]
    return first[:size]


def _clocks(packets, times):
    found = []
    size = min(len(data) for data in packets)
    span = (times[-1] - times[0]) / 1e9
    for width in (8, 4, 2):
        modulus = 1 << width * 8
        for endian in ('little', 'big'):
            for offset in range(0, min(size, CLOCK_SCAN) - width + 1):
                # A counter's lowest byte moves. If it does not, this field only overlaps a real one.
                low = offset if endian == 'little' else offset + width - 1
                if len({data[low] for data in packets}) == 1:
                    continue
                values = [int.from_bytes(data[offset:offset + width], endian) for data in packets]
                deltas = [(b - a) % modulus for a, b in zip(values, values[1:])]
                step = statistics.median(deltas)
                if step <= 0 or step > modulus / 4:
                    continue
                regular = sum(abs(d - step) <= max(step * 0.1, 1) for d in deltas) / len(deltas)
                if regular < REGULAR:
                    continue
                entry = {'offset': offset, 'width': width, 'endian': endian, 'step': step,
                         'regularity': round(regular, 3)}
                if span > 0:
                    rate = sum(deltas) / span
                    entry['ticks_per_s'] = round(rate, 1)
                    for scale, unit in UNITS.items():
                        if abs(rate / scale - 1) < 0.02:
                            entry['unit'] = unit
                            entry['drift_ppm'] = round((rate / scale - 1) * 1e6, 1)
                found.append(entry)
    # A wide field holds narrower ones, so the widest regular field at an offset wins, and a byte inside it is noise.
    found.sort(key=lambda e: (-e['regularity'], -e['width'], e['offset']))
    chosen = []
    for entry in found:
        bytes_used = set(range(entry['offset'], entry['offset'] + entry['width']))
        if not any(bytes_used & set(range(c['offset'], c['offset'] + c['width'])) for c in chosen):
            chosen.append(entry)
    return chosen[:KEEP]


def _vector_stats(vectors):
    lengths = [math.sqrt(x * x + y * y + z * z) for x, y, z in vectors]
    middle = statistics.median(lengths)
    moving = statistics.pstdev(v for vector in vectors for v in vector) > 0
    return middle, statistics.pstdev(lengths), moving


def gravity_candidates(packets):
    size = min(min(map(len, packets)), SCAN_BYTES)
    found = []
    for code, name in (('<3f', 'float32 little'), ('>3f', 'float32 big')):
        for offset in range(0, size - 11):
            vectors = [struct.unpack_from(code, data, offset) for data in packets]
            if not all(math.isfinite(v) and abs(v) < 1e3 for vector in vectors for v in vector):
                continue
            middle, spread, moving = _vector_stats(vectors)
            for unit, value in GRAVITY.items():
                if moving and abs(middle - value) < value * STILL and spread < value * STILL:
                    found.append({'offset': offset, 'format': name, 'unit': unit, 'length': round(middle, 3),
                                  'spread': round(spread, 4)})
    for code, name in (('<3h', 'int16 little'), ('>3h', 'int16 big')):
        for offset in range(0, size - 5):
            middle, spread, moving = _vector_stats([struct.unpack_from(code, data, offset) for data in packets])
            if moving and middle > 100 and spread < middle * QUIET_INT16:
                found.append({'offset': offset, 'format': name, 'unit': 'raw', 'length': round(middle, 1),
                              'spread': round(spread, 2)})
    found.sort(key=lambda e: (e['unit'] == 'raw', e['spread'] / max(e['length'], 1e-9)))
    return found


def pin_accelerometer(poses):
    """Fields that read one gravity in every still pose, each a list of same-size payloads.

    Only the real accelerometer keeps its length as gravity moves between axes. It takes one pose per axis: after two,
    a window holding those two axes and a neighbouring number still passes. Integers count too, by the same length
    in each pose rather than a known scale.
    """
    if len(poses) < 2:
        return []
    per_pose = [gravity_candidates(packets) for packets in poses]
    keys = [{(item['offset'], item['format'], item['unit']): item for item in pose} for pose in per_pose]
    shared = set(keys[0]).intersection(*keys[1:])
    pinned = []
    for key in sorted(shared):
        lengths = [pose[key]['length'] for pose in keys]
        if max(lengths) - min(lengths) <= max(lengths) * STILL:
            pinned.append({'offset': key[0], 'format': key[1], 'unit': key[2], 'lengths': lengths})
    return pinned


def _per_transfer(packets, magic):
    """How many samples one transfer holds, judged by its leading bytes repeating at a fixed spacing."""
    if len(magic) < 2:
        return 1, None
    hits = Counter()
    for data in packets[:50]:
        places = [i for i in range(len(data) - len(magic) + 1) if data[i:i + len(magic)] == magic]
        spacing = {b - a for a, b in zip(places, places[1:])}
        hits[(len(places), spacing.pop() if len(spacing) == 1 else None)] += 1
    (count, spacing), _ = hits.most_common(1)[0]
    return count, spacing


def profile(packets, times):
    """Everything findable in payloads of one stream, with their arrival times in ns."""
    if not packets:
        return {'packets': 0}
    sizes = Counter(map(len, packets))
    size = sizes.most_common(1)[0][0]
    pairs = [(data, t) for data, t in zip(packets, times) if len(data) == size][:PROFILE_PACKETS]
    result = {'packets': len(pairs), 'sizes': {str(k): v for k, v in sizes.most_common(4)}, 'size': size}
    if len(pairs) < MIN_PACKETS or size < 4:
        return result
    same = [data for data, _ in pairs]
    magic = _common_prefix(same)
    samples, spacing = _per_transfer(same, magic)
    result.update(magic=magic.hex(), samples_per_transfer=samples, sample_spacing_bytes=spacing,
                  clocks=_clocks(same, [t for _, t in pairs]), gravity=gravity_candidates(same)[:KEEP])
    return result
