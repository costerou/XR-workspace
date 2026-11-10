"""The two recordings heading-drift work replays (plan D6): a 20-minute desk-still and a cold-start warm-up.

A fixture is a .lcap that keeps only the glasses' IMU stream on 0x81 and a few harmless notes, so it can sit in the
repository: no descriptors, no command replies, no driver or kernel log, no URB ids (kernel addresses). Written as
.lcap.xz it is a fraction of the size; tools.luma_capture reads that directly, the C++ and Rust replays need `xz -dk`
first.

summarise() reads the IMU packets and says what the recording holds: how long, how many samples were lost, the
temperature span, how far gravity moved and the worst gyro jolt. problems() holds that against what each kind must be.
Packet layout: spike/driver/docs/capture-findings.md. Temperature is `raw / 132.48 + 25` as in protocol.cpp.
"""
from dataclasses import asdict, dataclass, field
import hashlib
import json
import math
from pathlib import Path
import statistics
import struct
import time

from . import capfile
from .rawmon import IMU_HEAD, IMU_PACKET, RawError

IMU_EP = 0x81
GYRO_LSB_PER_DPS = 16.4
TEMP_LSB_PER_C, TEMP_OFFSET_C = 132.48, 25.0
DEVICE_TICK_S = 1e-4
PACKET = struct.Struct('<4xHxxI3h3hH2x')      # sequence, device ticks, gyro, accel, temperature word

WINDOW = 100            # samples per motion window, 0.1 s
BLOCK_WINDOWS = 600     # a jolt is judged against the median of its 60 s block, which bias cannot move far in
KEEP_NOTES = frozenset({'clock', 'mark', 'usbmon_stats', 'glasses_gone', 'glasses_back', 'stop'})

KINDS = ('desk-still', 'warm-up')
MIN_SECONDS = 1200      # 20 minutes, both kinds
MAX_LOST = 0.001        # share of samples
MAX_TILT_DEG = 1.0      # gravity direction against the first second
MAX_JOLT_DPS = 1.0      # a 0.1 s gyro mean against its 60 s median
WARMUP_FIRST_DEVICE_S = 120   # the stream must start within 2 minutes of the glasses powering up
WARMUP_MIN_RISE_C = 5.0


@dataclass
class Summary:
    samples: int = 0
    seconds: float = 0.0
    lost: int = 0
    lost_share: float = 0.0
    usbmon_dropped: int = 0
    disconnects: int = 0
    recorded_utc: str = ''
    first_device_s: float = 0.0
    temp_start_c: float = 0.0
    temp_end_c: float = 0.0
    temp_min_c: float = 0.0
    temp_max_c: float = 0.0
    tilt_max_deg: float = 0.0
    jolt_max_dps: float = 0.0
    jolt_at_s: float = 0.0
    gyro_bias_start_dps: list = field(default_factory=list)
    gyro_bias_end_dps: list = field(default_factory=list)
    marks: list = field(default_factory=list)

    @property
    def temp_span_c(self):
        return self.temp_max_c - self.temp_min_c


def imu_records(paths):
    """Every IMU packet the glasses sent, and the notes kept beside them, in file order."""
    for record in capfile.records(paths):
        if record.is_note:
            if record.note.get('type') in KEEP_NOTES:
                yield record
        elif record.replayable and record.ep == IMU_EP:
            if len(record.data) != IMU_PACKET or record.data[:4] != IMU_HEAD:
                raise RawError(f'not an IMU packet on 0x81: {record.data[:8].hex()}')
            yield record


def write_fixture(paths, output):
    """Copies the IMU stream and the kept notes into one file, .xz compressed if output ends so. Returns samples."""
    readers = capfile.expand(paths)
    samples = 0
    with capfile.Writer(output, start_ns=readers[0].start_ns, bus=readers[0].bus) as writer:
        for record in imu_records(paths):
            if not record.is_note:
                record = capfile.Record(t_ns=record.t_ns, event='C', xfer=record.xfer, ep=IMU_EP, bus=record.bus,
                                        dev=record.dev, flags=capfile.F_GLASSES, urb_len=IMU_PACKET, data=record.data)
                samples += 1
            writer.write(record)
    if not samples:
        Path(output).unlink()
        raise RawError('no IMU packets on 0x81')
    return samples


def summarise(paths):
    summary = Summary()
    first_ns = last_ns = None
    previous_seq = None
    temps, gyro_sum, accel_sum, count = [], [0.0] * 3, [0.0] * 3, 0
    windows = []     # (seconds since start, mean gyro dps, mean accel lsb, mean temperature c)
    for record in imu_records(paths):
        if record.is_note:
            note = record.note
            kind = note['type']
            if kind == 'mark':
                summary.marks.append(note.get('text', ''))
            elif kind == 'usbmon_stats':
                summary.usbmon_dropped += int(note.get('since_last', 0))
            elif kind == 'glasses_gone':
                summary.disconnects += 1
            continue
        seq, ticks, gx, gy, gz, ax, ay, az, temp = PACKET.unpack(record.data)
        if first_ns is None:
            first_ns = record.t_ns
            summary.first_device_s = ticks * DEVICE_TICK_S
        last_ns = record.t_ns
        if previous_seq is not None:
            summary.lost += max(0, ((seq - previous_seq) & 0xffff) - 1)
        previous_seq = seq
        summary.samples += 1
        for axis, value in enumerate((gx, gy, gz)):
            gyro_sum[axis] += value
        for axis, value in enumerate((ax, ay, az)):
            accel_sum[axis] += value
        temps.append(temp)
        count += 1
        if count == WINDOW:
            windows.append(((record.t_ns - first_ns) * 1e-9, [v / count / GYRO_LSB_PER_DPS for v in gyro_sum],
                            [v / count for v in accel_sum], statistics.fmean(temps) / TEMP_LSB_PER_C + TEMP_OFFSET_C))
            temps, gyro_sum, accel_sum, count = [], [0.0] * 3, [0.0] * 3, 0
    if not summary.samples:
        raise RawError('no IMU packets on 0x81')
    if not windows:
        raise RawError(f'only {summary.samples} IMU packets: too short to judge')
    summary.seconds = (last_ns - first_ns) * 1e-9
    summary.lost_share = summary.lost / (summary.samples + summary.lost)
    summary.recorded_utc = time.strftime('%Y-%m-%dT%H:%M:%SZ', time.gmtime(first_ns // 1_000_000_000))
    per_second = 1000 // WINDOW
    head, tail = windows[:per_second], windows[-per_second:]
    summary.temp_start_c = statistics.fmean(w[3] for w in head)
    summary.temp_end_c = statistics.fmean(w[3] for w in tail)
    seconds = [statistics.fmean(w[3] for w in windows[i:i + per_second]) for i in range(0, len(windows), per_second)]
    summary.temp_min_c, summary.temp_max_c = min(seconds), max(seconds)
    summary.gyro_bias_start_dps = [statistics.fmean(w[1][a] for w in head) for a in range(3)]
    summary.gyro_bias_end_dps = [statistics.fmean(w[1][a] for w in tail) for a in range(3)]
    gravity = [statistics.fmean(w[2][a] for w in head) for a in range(3)]
    summary.tilt_max_deg = max(_angle_deg(gravity, w[2]) for w in windows)
    for start in range(0, len(windows), BLOCK_WINDOWS):
        block = windows[start:start + BLOCK_WINDOWS]
        median = [statistics.median(w[1][a] for w in block) for a in range(3)]
        for at, gyro, _, _ in block:
            jolt = math.dist(gyro, median)
            if jolt > summary.jolt_max_dps:
                summary.jolt_max_dps, summary.jolt_at_s = jolt, at
    return summary


def _angle_deg(a, b):
    dot = sum(x * y for x, y in zip(a, b))
    norm = math.hypot(*a) * math.hypot(*b)
    return math.degrees(math.acos(max(-1.0, min(1.0, dot / norm)))) if norm else 180.0


def problems(summary, kind):
    """What keeps this recording from being a fixture of that kind. Empty means it is one."""
    if kind not in KINDS:
        raise ValueError(f'kind must be one of {", ".join(KINDS)}')
    found = []
    if summary.seconds < MIN_SECONDS:
        found.append(f'{summary.seconds:.0f} s long, needs {MIN_SECONDS} s')
    if summary.lost_share > MAX_LOST:
        found.append(f'{summary.lost} samples lost ({summary.lost_share:.2%}), at most {MAX_LOST:.1%}')
    if summary.usbmon_dropped:
        found.append(f'usbmon dropped {summary.usbmon_dropped} events: the recorder fell behind')
    if summary.disconnects:
        found.append(f'{summary.disconnects} disconnects')
    if summary.tilt_max_deg > MAX_TILT_DEG:
        found.append(f'gravity moved {summary.tilt_max_deg:.2f}°, at most {MAX_TILT_DEG}°: the glasses were moved')
    if summary.jolt_max_dps > MAX_JOLT_DPS:
        found.append(f'gyro jolt of {summary.jolt_max_dps:.2f}°/s at {summary.jolt_at_s:.1f} s, at most '
                     f'{MAX_JOLT_DPS}°/s: the glasses or the desk were touched')
    if kind == 'warm-up':
        if summary.first_device_s > WARMUP_FIRST_DEVICE_S:
            found.append(f'the stream began {summary.first_device_s:.0f} s after the glasses powered up, at most '
                         f'{WARMUP_FIRST_DEVICE_S} s: not a cold start')
        rise = summary.temp_end_c - summary.temp_start_c
        if rise < WARMUP_MIN_RISE_C:
            found.append(f'temperature rose {rise:.1f} °C, needs {WARMUP_MIN_RISE_C} °C: the glasses were not cold')
    return found


def render(summary, kind=None):
    s = summary
    lines = [
        f'Recorded   {s.recorded_utc}, {s.seconds:.1f} s, {s.samples} samples, {s.lost} lost ({s.lost_share:.3%})',
        f'Device     stream began {s.first_device_s:.1f} s after power-up; {s.disconnects} disconnects; '
        f'usbmon dropped {s.usbmon_dropped}',
        f'Temperature {s.temp_start_c:.2f} -> {s.temp_end_c:.2f} °C, span {s.temp_span_c:.2f} °C '
        f'({s.temp_min_c:.2f} to {s.temp_max_c:.2f})',
        f'Gyro bias  start {_vector(s.gyro_bias_start_dps)} °/s, end {_vector(s.gyro_bias_end_dps)} °/s',
        f'Stillness  gravity moved {s.tilt_max_deg:.3f}°, worst jolt {s.jolt_max_dps:.3f}°/s at {s.jolt_at_s:.1f} s',
    ]
    lines += [f'Mark       {text}' for text in s.marks]
    if kind:
        found = problems(s, kind)
        lines.append(f'{kind}: ' + ('PASS' if not found else 'FAIL'))
        lines += [f'  - {problem}' for problem in found]
    return '\n'.join(lines) + '\n'


def _vector(values):
    return '(' + ', '.join(f'{v:+.4f}' for v in values) + ')'


def sidecar(output):
    """desk-still.lcap.xz -> desk-still.json"""
    output = Path(output)
    return output.with_name(output.name.split('.')[0] + '.json')


def describe(output, summary, kind):
    digest = hashlib.sha256(Path(output).read_bytes()).hexdigest()
    return {'kind': kind, 'file': Path(output).name, 'sha256': digest, 'summary': asdict(summary),
            'temp_span_c': summary.temp_span_c, 'problems': problems(summary, kind)}


def make(paths, output, kind):
    """Writes the fixture and its sidecar. Returns the summary and the problems."""
    problems(Summary(), kind)    # refuse an unknown kind before writing anything
    write_fixture(paths, output)
    summary = summarise([output])
    info = describe(output, summary, kind)
    sidecar(output).write_text(json.dumps(info, indent=1, ensure_ascii=False) + '\n')
    return summary, info['problems']
