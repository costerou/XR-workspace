"""The cross-language wire fuzz: the same generated frames through the Python, C++ and Rust decoders, every verdict compared
(docs/rust-rebuild-plan.md, phase 1: "a fuzz corpus give identical accept/refuse results in C++ and Rust").

Each frame is one line, generated from a seed: the frames of control_frames.json and the payload fixtures, the run frames
of rust/corpus/sockets/frames.json and the strict-JSON fuzz's values, re-encoded with their envelope and payload changed,
damaged a byte at a time, or sent as they are. Three wires read them, each decided the same way on every side:

  * `json`, the strict-JSON layer: Python `ipc.strict_json` (on the UTF-8 text, strict, as decode_control does), C++
    `decode_control`'s JSON layer (control_wire_check; refused when the detail is one of strict_json_corpus.CPP_JSON_REFUSALS)
    and Rust `xr_wire::json::parse`. Rust must give Python's and C++'s verdict where they agree, and the decided rule's
    (strict_json_corpus.DIVERGENCES) where they do not; a Python/C++ difference no rule explains counts as a difference.
    Frames past 16 KiB are left out of this wire (both decoders check the size first).
  * `renderer`, the renderer's control stream: C++ `ControlStream` (control_wire_check --each) and Rust `control::Stream`, each
    fed the whole batch at once on one stream, so the replay floor carries from frame to frame. Every frame must get the same
    verdict: accepted with the same request id, or refused with the same code, detail, op and id.
  * `run`, a run's control socket: Python `ipc.ControlQueue` and Rust `run_control::Queue`, one queue per batch, each taken
    frame popped at once. The same verdict text, with the JSON layer's refusals as `json` (their wording is each parser's
    own). Rust passes the payload of `options_*`, `video_*`, environment, immersive, placement and stabilization through for
    the module that applies it (xr_wire::run_control's docs): there the Python verdict is the envelope's, the payload being an
    object, and an `options_`/`video_` op that module does not know refused as `invalid operation`.

Run the gate (both decoders built; see rust/README.md):
    CONTROL_WIRE_CHECK=build/release/control_wire_check python3 tests/native_spike/wire_fuzz.py --frames 1000000
test_wire_fuzz.py runs a short one wherever both are built.
"""
import argparse
import copy
import json
import math
import os
from pathlib import Path
import random
import re
import subprocess
import sys
import threading

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[1]
for path in (str(ROOT), str(HERE)):
    if path not in sys.path: sys.path.insert(0, path)

import control_corpus  # noqa: E402
import strict_json_corpus as sj  # noqa: E402
from tools.native_spike import ipc, options_menu, video  # noqa: E402

RUN, SESSION = 'run', 'session'
RUST_TOOL = ROOT / 'rust' / 'target' / 'release' / 'examples' / 'wire_fuzz_verdicts'
SEED, BATCH = 20261110, 10_000  # per batch one fresh stream and queue; both streams drain a line from the front of what one feed gave, so one huge feed is quadratic
JSON_LIMIT = 16384               # the strict-JSON layer is compared up to the run socket's frame limit
OWNED = ('environment', 'immersive', 'placement', 'stabilization')  # xr_wire::run_control::OWNED_PAYLOADS
RUN_OPS = ('zoom_in', 'next', 'select', 'binds_key', 'stabilization', 'placement', 'menu_toggle', 'layout_reset', 'video_open',
           'video_pause', 'options_open', 'options_scroll', 'options_set', 'environment', 'immersive', 'options_bogus', 'video_x')
JUNK_OPS = ('', 'Status', 'view ', 'options_', 'video_', 'x' * 129, 5, None, ['view'], 'zoom-in')
TOKENS = ('other', 'r' * 128, 'r' * 129, '', 'a b', '-run', 'runé', 'run.1:2_x-y', 5, None, ['run'])
REQUEST_IDS = (0, -1, 1, 2**64 - 1, 2**64, 2**63, 1.0, 2.5, '5', True, False, None, [], -0.0)
SCHEMAS = (1, 2, 0, -1, 1.0, True, '1', None, 2**64, [1])
MAX_RENDERER_FRAME = 524_288  # control::MAX_OPTIONS_MENU_FRAME: past it both streams skip to the newline
NUMBER_BYTES = tuple(bytes([c]) for c in b'.eE0123456789+-')
MAX_FRAME = 16_384  # control::MAX_FRAME
JSON_REFUSAL = {'code': 'invalid_control', 'detail': 'not JSON', 'op': '', 'id': 0}
RAW = '\u0000RAW'  # a string swapped for raw JSON text after encoding: numbers and spellings json.dumps never writes


# ---------- seeds and the generator ----------

def seeds():
    """Every recorded frame (lines) and the parsed envelopes among them."""
    corpus = json.loads(control_corpus.FIXTURE.read_text())
    lines = []
    for cases in corpus['ops'].values():
        lines += cases['encoded'] + [c['frame'] for c in cases['mutated']]
    for case in corpus['envelope']: lines += case['frames']
    for name in sorted(control_corpus.PAYLOAD_FIXTURES.values()):
        op = name.removesuffix('_payloads.json')
        fixture = json.loads((control_corpus.FIXTURES / name).read_text())
        for entry in fixture.get('valid', []) + fixture.get('invalid', []):
            payload = entry['payload'] if isinstance(entry, dict) and set(entry) == {'why', 'payload'} else entry
            lines.append(control_corpus.frame(op, payload))
    sockets = json.loads((ROOT / 'rust' / 'corpus' / 'sockets' / 'frames.json').read_text())
    lines += [c['text'] for c in sockets['run_frames']] + [c['text'] for c in sockets['run_cases'] if 'text' in c]
    frames = sorted({line if line.endswith('\n') else line + '\n' for line in lines})
    parsed = []
    for line in frames:
        try: message = ipc.strict_json(line)
        except ValueError: continue
        if isinstance(message, dict) and 'payload' in message: parsed.append(message)
    return frames, parsed


class Generator:
    def __init__(self, seed):
        self.rng = random.Random(seed)
        self.lines, self.parsed = seeds()
        self.ops = list(control_corpus.OPS) + list(RUN_OPS)
        self.rid = 1

    def next_id(self):
        self.rid += self.rng.randint(1, 3)
        return self.rid

    def frame(self):
        """One frame: bytes ending in its only newline."""
        rng, pick = self.rng, self.rng.random()
        if pick < 0.50: data = self.structural()
        elif pick < 0.75: data = self.damaged_seed()
        elif pick < 0.88: data = self.fresh_id(rng.choice(self.lines)).encode()
        elif pick < 0.96: data = self.json_text().encode()
        else: data = self.special()
        data = data.rstrip(b'\n').replace(b'\n', b'\r')
        return data + b'\n'

    def fresh_id(self, line):
        return re.sub(r'"request_id":\s*\d+', f'"request_id":{self.next_id()}', line, count=1)

    def damaged_seed(self):
        return sj.damaged(self.rng, self.fresh_id(self.rng.choice(self.lines)).rstrip('\n')).encode()

    def json_text(self):
        rng = self.rng
        text = sj.deep(rng) if rng.random() < 0.12 else sj.value(rng, 0)
        if rng.random() < 0.3: text = sj.spaced(rng, text)
        if rng.random() < 0.35: text = sj.damaged(rng, text)
        return text

    def wrong(self):
        rng = self.rng
        choice = rng.random()
        if choice < 0.3: return RAW + rng.choice(sj.NUMBERS)
        if choice < 0.5: return RAW + sj.value(rng, rng.randint(0, 34))
        return rng.choice([None, True, False, 0, -1, 1, 255, 256, 7, 0.5, -0.0, 1e300, 2**64, '', 'x', 'é', 'one', [], {},
                           [1], {'k': 1}, 'x' * 300])

    def mutate_value(self, value, depth=0):
        """`value` with one thing inside it changed."""
        rng = self.rng
        if isinstance(value, dict) and value and rng.random() < 0.75:
            key = rng.choice(list(value))
            how = rng.random()
            if how < 0.2: return {k: v for k, v in value.items() if k != key}
            if how < 0.35: return {**value, rng.choice(['unknown', 'x', key + '_', '']): self.wrong()}
            return {**value, key: self.mutate_value(value[key], depth + 1)}
        if isinstance(value, list) and value and rng.random() < 0.75:
            i = rng.randrange(len(value))
            how = rng.random()
            if how < 0.2: return value[:i] + value[i + 1:]
            if how < 0.35: return value + [copy.deepcopy(value[i])] * rng.randint(1, 70)
            return value[:i] + [self.mutate_value(value[i], depth + 1)] + value[i + 1:]
        if isinstance(value, bool): return rng.choice([not value, 1, None])
        if isinstance(value, int): return rng.choice([value + 1, value - 1, -value, 0, value * 1000, 2**31, 2**32, 2**64,
                                                      float(value), str(value), self.wrong()])
        if isinstance(value, float): return rng.choice([value * 2, -value, value + 0.001, 0.0, 1e308, int(value) if math.isfinite(value) else 0, self.wrong()])
        if isinstance(value, str) and value.startswith(RAW): return self.wrong()
        if isinstance(value, str): return rng.choice(['', value + 'x', value[:-1], value.upper(), value * 40, 'é' + value,
                                                     '\x01', self.wrong()])
        return self.wrong()

    def structural(self):
        rng = self.rng
        message = copy.deepcopy(rng.choice(self.parsed))
        message['run_id'], message['session'] = RUN, SESSION
        message['request_id'] = self.next_id()
        for _ in range(rng.choice([0, 1, 1, 1, 2, 2, 3])):
            what = rng.random()
            if what < 0.45: message['payload'] = self.mutate_value(message.get('payload', {}))
            elif what < 0.55: message['payload'] = self.wrong()
            elif what < 0.65: message['op'] = rng.choice(self.ops) if rng.random() < 0.7 else rng.choice(JUNK_OPS)
            elif what < 0.73: message['request_id'] = rng.choice(REQUEST_IDS + (self.rid - rng.randint(0, 5),))
            elif what < 0.79: message['schema'] = rng.choice(SCHEMAS)
            elif what < 0.87: message[rng.choice(['run_id', 'session'])] = rng.choice(TOKENS)
            elif what < 0.93: message.pop(rng.choice(list(message)), None)
            else: message[rng.choice(['extra', 'schema ', 'Op'])] = self.wrong()
        return self.encode(message)

    def encode(self, message):
        rng = self.rng
        items = list(message.items())
        if rng.random() < 0.1: rng.shuffle(items)
        separators = (',', ':') if rng.random() < 0.8 else (', ', ': ')
        text = json.dumps(dict(items), separators=separators, ensure_ascii=rng.random() < 0.5)
        raws = []
        def swap(match):
            raws.append(json.loads(match.group(0))[len(RAW):])
            return raws[-1]
        text = re.sub(r'"\\u0000RAW(?:[^"\\]|\\.)*"', swap, text)
        if rng.random() < 0.05: text = sj.spaced(rng, text)
        return text.encode()

    def special(self):
        rng, pick = self.rng, self.rng.random()
        base = self.fresh_id(rng.choice(self.lines)).rstrip('\n').encode()
        if pick < 0.25:  # bad UTF-8 somewhere
            i = rng.randrange(len(base) + 1)
            return base[:i] + rng.choice(sj.BAD_UTF8 + [b'\xff', b'\xc3', b'\xed\xa0\x80', b'\x00', b'\x7f']) + base[i:]
        if pick < 0.40: return b'\xef\xbb\xbf' + base
        if pick < 0.55: return rng.choice([b' ', b'\t', b'\r', b'']) + base + rng.choice([b'\r', b' \r', b'\t', b''])
        if pick < 0.70: return base + rng.choice([b'x', b' 1', b'{}', b',', b']'])
        # past a size limit: a long string in the payload, around 16 KiB or the options menu's 512 KiB
        size = rng.choice([JSON_LIMIT - 200, JSON_LIMIT - 120, JSON_LIMIT - 1, JSON_LIMIT, JSON_LIMIT + 1, JSON_LIMIT + 500,
                           524_288 - 120, 524_288 + 10]) if rng.random() < 0.02 else rng.randint(JSON_LIMIT - 300, JSON_LIMIT + 300)
        message = copy.deepcopy(rng.choice(self.parsed))
        message.update(run_id=RUN, session=SESSION, request_id=self.next_id())
        if not isinstance(message['payload'], dict): message['payload'] = {}
        message['payload']['pad'] = ''
        text = json.dumps(message, separators=(',', ':'))
        message['payload']['pad'] = 'p' * max(size - len(text) - 1, 0)
        return json.dumps(message, separators=(',', ':')).encode()


# ---------- the decided differences ----------

BOM = b'\xef\xbb\xbf'


def as_rust_reads(data):
    """`data` as C++ and Python are given it, so the Rust verdict on `data` is theirs on this (the decided differences):

    * Every integer `-0` outside a string is spelled `-0.0`: serde_json reads `-0` as the float -0.0, so Rust refuses it where
      an integer is required (xr_wire::control's docs: "Rust is stricter"); Python and C++ read the integer 0. No sender
      writes `-0` (json.dumps and JSON.stringify write 0).
    * A leading byte order mark, which Rust refuses before it reads anything (strict_json_corpus.DIVERGENCES['bom']) and C++
      skips, is three bytes that are not UTF-8, so C++ refuses the frame too and its replay floor stays where Rust's does.
    * A raw NUL byte, which C++ stops reading at (strict_json_corpus.DIVERGENCES['nul']) and Python and Rust refuse, is a byte
      that is not UTF-8, for the same reason.
    Only the `-0` rewrite changes the length.
    """
    if data.startswith(BOM): data = b'\xff\xff\xff' + data[3:]
    data = data.replace(b'\x00', b'\xff')
    if b'-0' not in data: return data
    out, in_string, escaped, i = bytearray(), False, False, 0
    while i < len(data):
        b = data[i]
        if in_string:
            if escaped: escaped = False
            elif b == 0x5c: escaped = True
            elif b == 0x22: in_string = False
        elif b == 0x22: in_string = True
        elif (b == 0x2d and data[i + 1:i + 2] == b'0' and data[i + 2:i + 3] not in NUMBER_BYTES
              and data[i - 1:i] not in NUMBER_BYTES + (b'e', b'E')):  # a number's own sign, not an exponent's
            out += b'-0.0'
            i += 2
            continue
        out.append(b)
        i += 1
    return bytes(out)


def rust_bom_refusal(raw):
    """xr_wire::control::decode on a frame starting with a byte order mark: framing, depth, then the parse, which fails."""
    detail = 'nesting limit' if sj.depth(raw) > sj.MAX_DEPTH else 'bad framing' if len(raw) > MAX_FRAME else 'not JSON'
    return {**JSON_REFUSAL, 'detail': detail}


# ---------- Python's verdicts ----------

def owned(op):
    return isinstance(op, str) and (op.startswith(('options_', 'video_')) or op in OWNED)


class PythonRun:
    """ipc.ControlQueue, every taken frame popped at once, with the payload rule Rust leaves to its owner (module docs)."""
    def __init__(self):
        self.queue = ipc.ControlQueue(RUN, SESSION)

    def push(self, raw, json_expected):
        if len(raw) > ipc.MAX_FRAME or not raw.endswith(b'\n') or b'\n' in raw[:-1]: return 'invalid frame'
        if json_expected != 'accepted': return 'json'
        try:
            message = ipc.decode_control(raw)
        except (ValueError, TypeError, KeyError) as exc:
            message = ipc.strict_json(raw.decode())
            if not (isinstance(message, dict) and owned(message.get('op'))): return str(exc)
            message = self.envelope(message)
            if isinstance(message, str): return message
        if message['run_id'] != self.queue.run_id or message['session'] != self.queue.session: return 'wrong run/session'
        if message['request_id'] <= self.queue.last: return 'replayed request'
        self.queue.last = message['request_id']
        return 'accepted'

    @staticmethod
    def envelope(message):
        """decode_control's envelope checks alone for an op whose payload its owner checks: the message, or the error."""
        if set(message) != {'schema', 'request_id', 'run_id', 'session', 'op', 'payload'}: return 'invalid fields'
        try:
            checked = ipc.decode_control((json.dumps({**message, 'op': 'status', 'payload': {}}, ensure_ascii=False) + '\n').encode())
        except (ValueError, TypeError, KeyError) as exc:
            return str(exc)
        op = message['op']
        if op.startswith('options_') and op not in options_menu.OPS: return 'invalid operation'
        if op.startswith('video_') and op not in video.VIDEO_OPS: return 'invalid operation'
        if not isinstance(message['payload'], dict): return 'invalid payload'
        return checked


def json_verdicts(body, cpp_detail):
    """(Python, C++, the verdict Rust must give or None when no decided rule explains a Python/C++ difference)."""
    python = sj.python_verdict(body)
    cpp = 'refused' if cpp_detail in sj.CPP_JSON_REFUSALS else 'accepted'
    if python == cpp: return python, cpp, python
    rule = sj.divergence(body)
    if rule is None: return python, cpp, None
    return python, cpp, {'python': python, 'cpp': cpp}[sj.DIVERGENCES[rule][0]]


# ---------- running a batch ----------

def run_tool(argv, data, out):
    done = subprocess.run(argv, input=data, capture_output=True, timeout=1800)
    if done.returncode != 0: raise RuntimeError(f'{argv[0]} failed ({done.returncode}): {done.stderr.decode()[-2000:]}')
    out.append(done.stdout.splitlines())


def renderer_view(verdict):
    return verdict if 'accepted' in verdict else {k: verdict[k] for k in ('code', 'detail', 'op', 'id')}


def batch(frames, cpp_tool, rust_tool, report):
    read = [as_rust_reads(raw) for raw in frames]
    cpp, rust = [], []
    threads = [threading.Thread(target=run_tool, args=([cpp_tool, RUN, SESSION, '--each'], b''.join(read), cpp)),
               threading.Thread(target=run_tool, args=([rust_tool, RUN, SESSION], b''.join(frames), rust))]
    for t in threads: t.start()
    for t in threads: t.join()
    if len(cpp) != 1 or len(rust) != 1: raise RuntimeError('a decoder did not finish')
    cpp_lines, rust_lines = [json.loads(line) for line in cpp[0]], [json.loads(line) for line in rust[0]]
    totals = cpp_lines.pop()
    if len(cpp_lines) != len(frames) or totals['accepted'] + totals['refused'] != len(frames) or len(rust_lines) != len(frames):
        raise RuntimeError(f'{len(frames)} frames, {len(cpp_lines)} C++ verdicts ({totals}), {len(rust_lines)} Rust verdicts')
    python_run = PythonRun()
    for raw, as_read, c, r in zip(frames, read, cpp_lines, rust_lines):
        report.frames += 1
        body = raw[:-1]
        if len(as_read) != len(raw): report.count('decided', 'negative zero (-0 read as -0.0)')
        # the JSON layer
        if raw.startswith(BOM) or b'\x00' in raw:  # C++ was not given the mark or the NUL (as_rust_reads): Python's refusal
            py = cp = want = sj.python_verdict(body)
            report.count('decided', f"json: {'bom' if raw.startswith(BOM) else 'nul'}, refused as Python does")
        else:
            py, cp, want = json_verdicts(body, c.get('detail'))
        if len(raw) <= JSON_LIMIT:
            report.count('json', want or 'unexplained')
            if want is None: report.differ('json: Python and C++ differ, no decided rule', raw, python=py, cpp=cp)
            elif r['json'] != want: report.differ('json: Rust', raw, want=want, rust=r['json'], python=py, cpp=cp)
            if py != cp: report.count('json_divergence', sj.divergence(body))
        else:
            report.count('json', 'skipped (past 16 KiB)')
        # the renderer's stream: C++'s verdict, except for the decided differences
        c, rr = renderer_view(c), renderer_view(r['renderer'])
        if raw.startswith(BOM) and len(raw) <= MAX_RENDERER_FRAME:
            c = rust_bom_refusal(raw)
            report.count('decided', 'renderer: bom refused by the JSON layer')
        elif c.get('detail') in ('not JSON', 'duplicate key') and sj.depth(body) > sj.MAX_DEPTH:
            c = {**JSON_REFUSAL, 'detail': 'nesting limit'}  # Rust checks the depth before parsing (xr_wire::control's docs)
            report.count('decided', 'renderer: too deep and malformed or with a repeated key, nesting limit first')
        report.count('renderer', 'accepted' if 'accepted' in c else f"{c['code']}: {re.sub(r'[0-9]+', 'N', c['detail'])}")
        if c != rr: report.differ('renderer: C++ vs Rust', raw, cpp=c, rust=rr)
        # the run's control socket
        py_run = python_run.push(as_read, want)
        report.count('run', py_run)
        if r['run'] != py_run: report.differ('run: Python vs Rust', raw, python=py_run, rust=r['run'])


class Report:
    def __init__(self, keep=20):
        self.frames, self.differences, self.examples, self.keep, self.counts = 0, 0, [], keep, {}

    def count(self, wire, what):
        bucket = self.counts.setdefault(wire, {})
        bucket[what] = bucket.get(what, 0) + 1

    def differ(self, what, raw, **verdicts):
        self.differences += 1
        self.count('differences', what)
        if len(self.examples) < self.keep:
            self.examples.append({'what': what, 'frame': raw[:600].decode('utf-8', errors='backslashreplace'), **verdicts})

    def summary(self):
        return {'frames': self.frames, 'differences': self.differences,
                'counts': {w: dict(sorted(b.items(), key=lambda kv: -kv[1])) for w, b in self.counts.items()},
                'examples': self.examples}


def fuzz(n, cpp_tool, rust_tool, seed=SEED, batch_size=BATCH, progress=None):
    generator, report = Generator(seed), Report()
    done = 0
    while done < n:
        size = min(batch_size, n - done)
        generator.rid = 1  # each batch starts a fresh stream and queue
        batch([generator.frame() for _ in range(size)], cpp_tool, rust_tool, report)
        done += size
        if progress: progress(f'{done} frames, {report.differences} differences')
    return report


def main():
    parser = argparse.ArgumentParser(description=__doc__.split('\n\n')[0])
    parser.add_argument('--frames', type=int, default=1_000_000)
    parser.add_argument('--seed', type=int, default=SEED)
    parser.add_argument('--batch', type=int, default=BATCH)
    parser.add_argument('--rust', default=os.environ.get('WIRE_FUZZ_VERDICTS', str(RUST_TOOL)))
    parser.add_argument('--cpp', default=os.environ.get('CONTROL_WIRE_CHECK'))
    args = parser.parse_args()
    if not args.cpp: sys.exit('set CONTROL_WIRE_CHECK (or --cpp) to the built control_wire_check')
    if not Path(args.rust).exists():
        sys.exit(f'{args.rust} missing: cargo build --release --manifest-path rust/Cargo.toml -p xr-wire --example wire_fuzz_verdicts')
    report = fuzz(args.frames, args.cpp, args.rust, args.seed, args.batch, progress=lambda line: print(line, file=sys.stderr))
    print(json.dumps(report.summary(), indent=1, ensure_ascii=False))
    sys.exit(1 if report.differences else 0)


if __name__ == '__main__':
    main()
