"""The strict-JSON corpus (rust/corpus/json/strict_json.json): JSON texts, hand-picked edges and a seeded fuzz, each with
the verdict of the two strict decoders the Rust xr-wire crate must match (docs/rust-rebuild-plan.md, phase 1):

  * `python`: tools/native_spike/ipc.strict_json, on the text (bytes cases: decoded as UTF-8, strict, first, as
    decode_control does).
  * `cpp`: the renderer's decode_control (spike/src/ipc.cpp) through control_wire_check, the case being the whole frame.
    Its JSON layer refused the text when the detail is one of CPP_JSON_REFUSALS ("not JSON", "duplicate key", "nesting
    limit"); any other verdict means the JSON was taken and only the envelope was not. A frame cannot hold a newline,
    so C++ gets the text with each newline made a carriage return: the same JSON either way (both are whitespace
    between tokens, both refused raw inside a string).
  * `rust`: the verdict xr-wire must give. Where the two agree, theirs. Where they differ, the case's `divergence` names
    the rule decided below (DIVERGENCES); a difference no rule explains stops the generator.

Regenerate after a deliberate change to either decoder, with the checker built:
    CONTROL_WIRE_CHECK=build/release/control_wire_check python3 tests/native_spike/strict_json_corpus.py --write
test_strict_json_corpus.py checks the Python verdicts (any run) and the C++ ones (ctest control_wire).
"""
import json
import math
import os
from pathlib import Path
import random
import re
import subprocess
import sys

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path: sys.path.insert(0, str(ROOT))

from tools.native_spike.ipc import strict_json  # noqa: E402

FILE = ROOT / 'rust' / 'corpus' / 'json' / 'strict_json.json'
CPP_JSON_REFUSALS = {'not JSON', 'duplicate key', 'nesting limit'}
MAX_DEPTH = 32  # decode_control: a value inside more than 32 arrays or objects is refused ("nesting limit")
SEED, FUZZ = 20261110, 6000
# Where Python and C++ disagree, which one the Rust layer follows, and why.
DIVERGENCES = {
    'depth': ('cpp', 'Python has no depth limit (only RecursionError near 1000); C++ refuses values nested deeper than 32, '
                     'checked before parsing'),
    'lone_surrogate': ('cpp', 'Python keeps a lone \\uD800-\\uDFFF escape as a lone surrogate, which is not text; C++ refuses it'),
    'overflow': ('cpp', 'Python turns a number past the f64 range into inf (or an exact int); C++ refuses it, like NaN and Infinity'),
    'bom': ('python', 'C++ skips a leading UTF-8 byte order mark; Python refuses it, and no sender writes one'),
    'nul': ('python', 'C++ stops reading at a raw NUL byte, taking the JSON before it; Python refuses it, and no sender '
                      'writes one'),
}
EDGES = [
    # NaN and Infinity, in every spelling the decoders know
    'NaN', '-NaN', 'Infinity', '-Infinity', '[NaN]', '{"a":Infinity}', '[-Infinity]', 'nan', 'inf', '[1,NaN]',
    # numbers past the f64 range, and just inside it
    '1e400', '-1e400', '1e309', '1.7976931348623157e308', '1.7976931348623159e308', '[1E+999]', '{"x":-2e308}', '1e-400',
    '4.9e-324', '1' * 309, '1' * 308, '-' + '9' * 400, '99999999999999999999999', '18446744073709551615', '18446744073709551616',
    '-9223372036854775808', '-9223372036854775809', '-0', '-0.0', '0e0', '1E5', '1e+5', '1.5e-5',
    # number syntax
    '01', '-01', '1.', '.5', '+1', '1e', '1e+', '0x10', '1_000', '--1', '1.0.0',
    # duplicate keys, at every depth and after unescaping
    '{"a":1,"a":2}', '{"a":1,"a":1}', '{"a":{"b":1,"b":1}}', '[{"a":1},{"a":1}]', '{"a":{"a":1}}', '{"\\u0061":1,"a":2}',
    '{"":1,"":2}', '{"a":1,"b":2,"a":3}', '[[{"k":0,"k":0}]]', '{"a":1,"A":2}', '{"\\u00e9":1,"\u00e9":2}',
    '{"\\ud83d\\ude00":1,"\U0001F600":2}',
    # strings and escapes
    '"\\ud800"', '"\\udc00"', '"\\ud800\\u0041"', '"\\udbff\\udfff"', '"\\ud83d\\ude00"', '"\\uD83D\\uDE00"', '"\\u0000"',
    '"\\x41"', '"\\/"', '"\\b\\f\\n\\r\\t\\"\\\\"', '"\\u12"', '"\\U0041"', '"\x01"', '"\x1f"', '"\x7f"', '"\t"', '"\u2028"',
    '"\U0001F600"', '"abc', "'a'",
    # structure
    '', ' ', '1 2', '{"a":1}x', '[1,]', '{"a":1,}', '[,1]', '{,}', '{"a"}', '{"a":}', '{1:2}', '[}', '{]', '[[[', ']',
    'true', 'false', 'null', 'True', 'nul', 'nulll', '[]', '{}', '""', '[[], {}]', ' 1 ', '\t1\r', '\n1\n', '\x0b1', '\f1',
    '\u00a01', '1\u00a0', '/*c*/1', '1//c',
    # a byte order mark
    '\ufeff1', '\ufeff{}', '1\ufeff',
    # a raw NUL byte, after, before and inside a value (wire_fuzz.py found the first)
    '1\x00', '{}\x00', '[1]\x00', '{"a":1}\x00x', '\x001', '[1\x00]', '"a\x00"',
]
BAD_UTF8 = [b'"\xff"', b'"\xc3"', b'"\xed\xa0\x80"', b'"\xc0\xaf"', b'"\xf4\x90\x80\x80"', b'[1]\xff', b'\xef\xbb\xbf1',
            b'{"\xe9":1}', b'"\xe2\x82"']


def nested(n, inner, kind):
    return ('[' * n + inner + ']' * n) if kind == 'array' else ('{"k":' * n + inner + '}' * n)


def depth_cases():
    out = []
    for n in range(MAX_DEPTH - 2, MAX_DEPTH + 4):
        out += [nested(n, '', 'array'), nested(n, '1', 'array'), nested(n, '{}', 'array'), nested(n, '[]', 'array'),
                nested(n, '1', 'object'), nested(n, '{}', 'object'), nested(n, '[]', 'object'), nested(n, '{"a":1}', 'object')]
    out += [nested(200, '1', 'array'), nested(990, '', 'array'), nested(2000, '1', 'array'),
            '[' + nested(MAX_DEPTH, '', 'array') + ',1]', '[' + nested(MAX_DEPTH - 1, '1', 'array') + ',' + nested(MAX_DEPTH, '1', 'array') + ']',
            '[' * 40, ']' * 40, '"' + '[' * 40 + '"', '["' + '{' * 40 + '"]', '{"[[[[[[[[[[[[[[[[[[[[[[[[[[[[[[[[[[[[[[":1}']
    return out


# The fuzz: random JSON-ish values, written with random spacing, then a share of them damaged a byte at a time.
KEYS = ['a', 'b', 'k', '', 'A', '\\u0061', 'é', '\\u00e9', 'long_key_name', '\\ud83d\\ude00', '😀']
STRINGS = ['', 'x', 'hello world', '\\n', '\\"', '\\\\', '\\u0000', '\\u00e9', '\\ud83d\\ude00', '\\ud800', '\\udfff', 'é', '😀',
           '\\t\\b\\f\\r', '\x7f', '\x01', '\\/', '\\q', '\\u12g4', '\u2028']
NUMBERS = ['0', '-0', '1', '-1', '42', '3.25', '-0.5', '1e10', '1E-7', '2.5e+3', '18446744073709551615', '18446744073709551616',
           '99999999999999999999999999', '1e308', '1e309', '-1e400', '1e-400', '01', '1.', '.5', '+1', 'NaN', 'Infinity',
           '-Infinity', '1' * 320]
ALPHABET = '{}[]:,"\\ 0123456789.eE+-tfnrul\n\tabxNI'


def value(rng, depth):
    pick = rng.random()
    if depth < 38 and pick < 0.35:
        if rng.random() < 0.5:
            return '[' + ','.join(value(rng, depth + 1) for _ in range(rng.randint(0, 3))) + ']'
        keys = [rng.choice(KEYS) for _ in range(rng.randint(0, 3))]
        if not rng.random() < 0.15: keys = list(dict.fromkeys(keys))  # mostly distinct keys, sometimes repeated ones
        return '{' + ','.join(f'"{k}":{value(rng, depth + 1)}' for k in keys) + '}'
    if pick < 0.6: return '"' + rng.choice(STRINGS) + '"'
    if pick < 0.9: return rng.choice(NUMBERS)
    return rng.choice(['true', 'false', 'null'])


def deep(rng):
    """A value wrapped to just around the depth limit, for the fuzz to hit both sides of it."""
    n = rng.randint(MAX_DEPTH - 3, MAX_DEPTH + 2)
    text = value(rng, 30)
    for _ in range(n):
        text = f'[{text}]' if rng.random() < 0.5 else '{"' + rng.choice(['a', 'k']) + '":' + text + '}'
    return text


def spaced(rng, text):
    return ''.join(c + (rng.choice([' ', '\t', '\r', '\n', '  ']) if c in ',:[]{}' and rng.random() < 0.08 else '') for c in text)


def damaged(rng, text):
    for _ in range(rng.randint(1, 3)):
        i = rng.randrange(len(text) + 1)
        how = rng.randrange(3)
        if how == 0 and text: text = text[:max(i - 1, 0)] + text[i:]
        elif how == 1: text = text[:i] + rng.choice(ALPHABET) + text[i:]
        elif text: text = text[:max(i - 1, 0)] + rng.choice(ALPHABET) + text[i:]
    return text


def fuzz():
    rng = random.Random(SEED)
    out = []
    for _ in range(FUZZ):
        text = deep(rng) if rng.random() < 0.12 else value(rng, 0)
        if rng.random() < 0.3: text = spaced(rng, text)
        if rng.random() < 0.35: text = damaged(rng, text)
        out.append(text)
    return out


def python_verdict(raw):
    try:
        strict_json(raw.decode('utf-8', errors='strict') if isinstance(raw, bytes) else raw)
        return 'accepted'
    except ValueError:  # strict_json's only refusal (UnicodeDecodeError and JSONDecodeError are ValueErrors)
        return 'refused'


def cpp_verdict(tool, data):
    done = subprocess.run([tool, 'run', 'session'], input=data.replace(b'\n', b'\r') + b'\n', capture_output=True, timeout=30, check=True)
    lines = [json.loads(line) for line in done.stdout.splitlines()]
    totals = lines.pop()
    if totals['accepted'] + totals['refused'] != 1: raise AssertionError(f'one frame, but {totals}')
    return 'refused' if lines and lines[0]['detail'] in CPP_JSON_REFUSALS else 'accepted'


def depth(data):
    """The most arrays and objects around any value, as decode_control counts them (strings skipped)."""
    deepest = open_ = 0
    in_string = escaped = False
    for c in data.decode('utf-8', errors='replace'):
        if in_string:
            if escaped: escaped = False
            elif c == '\\': escaped = True
            elif c == '"': in_string = False
            continue
        if c in ' \t\r\n': continue
        if c in ']}':
            open_ = max(open_ - 1, 0)
            continue
        if c not in ',:': deepest = max(deepest, open_)
        if c == '"': in_string = True
        elif c in '[{': open_ += 1
    return deepest


def divergence(data):
    """Which decided rule explains a case where Python and C++ differ, or None."""
    text = data.decode('utf-8', errors='replace')
    if depth(data) > MAX_DEPTH: return 'depth'
    if text.startswith('\ufeff'): return 'bom'
    if '\x00' in text: return 'nul'
    if re.search(r'(?<!\\)(?:\\\\)*\\u[dD][89a-fA-F]', text): return 'lone_surrogate'
    if any(math.isinf(float(n)) for n in re.findall(r'-?\d+(?:\.\d+)?(?:[eE][+-]?\d+)?', text)): return 'overflow'
    return None


def case(kind, raw, tool):
    data = raw if isinstance(raw, bytes) else raw.encode('utf-8')
    entry = {'kind': kind}
    try: entry['text'] = data.decode('utf-8', errors='strict')
    except UnicodeDecodeError: entry['hex'] = data.hex()
    entry['python'] = python_verdict(raw)
    entry['cpp'] = cpp_verdict(tool, data)
    if entry['cpp'] == entry['python']:
        entry['rust'] = entry['python']
    else:
        rule = divergence(data)
        if rule is None: raise AssertionError(f'Python and C++ disagree and no rule says why: {entry}')
        entry['divergence'] = rule
        entry['rust'] = entry[DIVERGENCES[rule][0]]
    return entry


def build(tool):
    cases = ([('edge', t) for t in EDGES] + [('bad_utf8', b) for b in BAD_UTF8] + [('depth', t) for t in depth_cases()] +
             [('fuzz', t) for t in fuzz()])
    seen, out = set(), []
    for kind, raw in cases:
        if raw in seen: continue
        seen.add(raw)
        out.append(case(kind, raw, tool))
    return {'max_depth': MAX_DEPTH, 'cpp_json_refusals': sorted(CPP_JSON_REFUSALS),
            'divergences': {k: {'rust_follows': v[0], 'why': v[1]} for k, v in DIVERGENCES.items()}, 'cases': out}


def dump(corpus):
    """One case a line, so a changed verdict is a one-line diff."""
    head = {k: v for k, v in corpus.items() if k != 'cases'}
    cases = ',\n'.join(json.dumps(c, ensure_ascii=True, separators=(',', ':')) for c in corpus['cases'])
    return json.dumps(head, indent=1, ensure_ascii=True)[:-2] + ',\n "cases": [\n' + cases + '\n ]\n}\n'


if __name__ == '__main__':
    tool = os.environ.get('CONTROL_WIRE_CHECK')
    if tool is None: sys.exit('set CONTROL_WIRE_CHECK to the built control_wire_check')
    corpus = build(tool)
    if '--write' in sys.argv:
        FILE.parent.mkdir(parents=True, exist_ok=True)
        FILE.write_text(dump(corpus))
    counts = {}
    for c in corpus['cases']: counts[(c['rust'], c.get('divergence'))] = counts.get((c['rust'], c.get('divergence')), 0) + 1
    print(len(corpus['cases']), 'cases', {f'{k[0]}/{k[1] or "agreed"}': v for k, v in sorted(counts.items(), key=str)})
