"""The phase 0 Hyprland corpus (lua_corpus.py): the committed golden files match the shipped Python, and the Lua cases take
every branch of every generator."""
import dis
import json
import sys
import unittest

import lua_corpus as corpus


def _codes(code):
    """A function's code object and the ones nested in it (generator expressions, lambdas)."""
    yield code
    for const in code.co_consts:
        if hasattr(const, 'co_code'): yield from _codes(const)


class BranchRecorder:
    """Every (code, branch source, destination) taken while recording, from sys.monitoring's branch events."""
    def __init__(self, functions):
        self.codes = [c for fn in functions for c in _codes(fn.__code__)]
        self.taken = set()

    def __enter__(self):
        m = sys.monitoring
        self.tool = next(t for t in range(6) if m.get_tool(t) is None)
        m.use_tool_id(self.tool, 'lua_corpus')
        def hit(code, src, dst):
            self.taken.add((code, src, dst))
            return m.DISABLE
        for event in (m.events.BRANCH_LEFT, m.events.BRANCH_RIGHT): m.register_callback(self.tool, event, hit)
        for code in self.codes: m.set_local_events(self.tool, code, m.events.BRANCH_LEFT | m.events.BRANCH_RIGHT)
        return self

    def __exit__(self, *exc):
        m = sys.monitoring
        for code in self.codes: m.set_local_events(self.tool, code, 0)
        m.free_tool_id(self.tool)

    def missed(self):
        """Branch arms no case took: file:line, function, and the arm's bytecode offsets."""
        out = []
        for code in self.codes:
            at = {i.offset: i.opname for i in dis.get_instructions(code)}
            for src, left, right in code.co_branches():
                # CPython 3.14 inlines tuple(<genexpr>), any(<genexpr>) and the like behind an `is the builtin` check;
                # the other arm runs only when the name is shadowed. A compiler guard, not a branch of the source.
                if at.get(src - 2) == 'IS_OP' and at.get(src - 4) == 'LOAD_COMMON_CONSTANT': continue
                for dst in (left, right):
                    if (code, src, dst) not in self.taken:
                        out.append(f'{code.co_filename.rsplit("/", 1)[1]}:{_line(code, src)} {code.co_qualname} '
                                   f'offset {src} -> {dst}')
        return out


def _line(code, offset):
    for start, end, line in code.co_lines():
        if start <= offset < end: return line
    return None


class LuaCorpusTest(unittest.TestCase):
    def test_every_generator_branch_has_a_case(self):
        # The gate for docs/rust-rebuild-plan.md phase 0: each branch of each Lua generator has a golden string (or a
        # golden refusal), so the Rust port can be held to all of them.
        with BranchRecorder(corpus.GENERATORS) as recorder:
            for _, fn, kwargs in corpus.lua_cases(): corpus.run_case(fn, kwargs)
        self.assertEqual(recorder.missed(), [])

    def test_every_generator_has_a_case(self):
        named = {corpus.name_of(fn) for _, fn, _ in corpus.lua_cases()}
        helpers = {fn for fn in corpus.GENERATORS if fn.__name__.startswith('_')}
        self.assertEqual({corpus.name_of(fn) for fn in corpus.GENERATORS if fn not in helpers} - named, set())

    def test_lua_file_matches_the_python(self):
        self.assertEqual(json.loads(corpus.LUA_FILE.read_text()), json.loads(corpus.dumps(corpus.lua_corpus())),
                         'regenerate: tests/native_spike/lua_corpus.py --write')
        self.assertEqual(corpus.LUA_FILE.read_text(), corpus.dumps(corpus.lua_corpus()))

    def test_transcript_file_matches_the_client(self):
        self.assertEqual(corpus.TRANSCRIPT_FILE.read_text(), corpus.dumps(corpus.transcript_corpus()),
                         'regenerate: tests/native_spike/lua_corpus.py --write')

    def test_every_compositor_call_has_a_transcript(self):
        # Each public HyprlandClient method that talks to the compositor appears in some scenario.
        recorded = set()
        for _, call, replies in corpus.transcript_scenarios():
            names = []
            corpus.record(lambda c: call(_spy(c, names)), replies)
            recorded.update(names)
        public = {n for n in vars(corpus.hyprland.HyprlandClient) if not n.startswith('_')
                  and callable(getattr(corpus.hyprland.HyprlandClient, n))}
        self.assertEqual(public - recorded - NOT_COMPOSITOR, set())

    def test_transcripts_carry_the_generated_lua(self):
        # The client sends the generators' output unchanged: each /eval body in a transcript is a corpus string.
        lua = set()
        for case in corpus.lua_corpus()['cases']:
            value = case.get('lua')
            lua.update(value if isinstance(value, list) else [value] if value else [])
        transcripts = corpus.transcript_corpus()['scenarios']
        evals = [e['request'].removeprefix('/eval ') for s in transcripts for e in s['exchanges']
                 if e['request'].startswith('/eval ')]
        self.assertTrue(evals)
        # Some calls join generated strings with a space into one eval; park_presentation writes its rule inline.
        self.assertEqual([e for e in evals if not _joins(e, lua) and not e.startswith('hl.monitor(')], [])


# Public HyprlandClient methods with no request/reply to record: event_stream only opens socket2 (the event stream is
# not request/reply), connect_wayland opens the Wayland socket, and require_output_ownership always refuses unasked.
NOT_COMPOSITOR = {'event_stream', 'connect_wayland', 'require_output_ownership'}


def _joins(text, parts):
    """Whether `text` is one or more of `parts` joined by single spaces."""
    ends = {0}
    for i in range(len(text) + 1):
        if i not in ends: continue
        for part in parts:
            if text.startswith(part, i):
                end = i + len(part)
                if end == len(text): return True
                if text.startswith(' ', end): ends.add(end + 1)
    return False


def _spy(client, names):
    """`client`, recording which public methods are called on it."""
    class Spy:
        def __getattr__(self, name):
            names.append(name)
            return getattr(client, name)
    return Spy()


if __name__ == '__main__':
    unittest.main()
