"""The VR video control's corpus for the Rust rebuild (rust/crates/xr-workspace/VIEW_CONTROLS.md, `video`, level 1).

rust/corpus/video/control.json: scripted sessions of the shipped `video.VideoControl`, each step and what came of it,
made only by this module. The control wraps a recording stand-in for the view (`View`): it has only what VideoControl
reads and changes on the view (the frame ids, the clock, typing mode, the placement panel, help card and key bind
panel it closes, the saved quality and background, the toasts, the owner readback), and it answers the ops handed to
it with a marker line. The files are real (a temporary folder), the probe, the web probe and the relay are stand-ins,
the bind work and toasts are joined after each step. tests/video_control_corpus.rs replays every session against
`xr_workspace::view_controls::video::Control` with the same stand-ins and compares each step's record.

Each step's record: the frames sent (`out`), the descriptors with them (`fds`: `file` or `pipe`), the panel's status
entry (`snapshot`), and the stand-ins' logs so far: the bind calls, the video's and the view's toasts, the view's
markers, the owner completions and frame registrations, the saves, the relays.

Regenerate (after a deliberate change to video.py) with:

    TMPDIR=/tmp /usr/bin/python3 tests/native_spike/video_control_corpus.py --write

Without --write it checks the file against the current Python and exits 1 on a difference.
"""
import json
import os
from pathlib import Path
import stat
import sys
import tempfile
import threading

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path: sys.path.insert(0, str(ROOT))

from tools.native_spike import environment, quality, video  # noqa: E402

CORPUS = ROOT / 'rust' / 'corpus' / 'video' / 'control.json'


def marker(name, **fields):
    return (json.dumps({'marker': name, **fields}, separators=(',', ':')) + '\n').encode()


class Clock:
    def __init__(self): self.now = 100.0
    def __call__(self): return self.now


class State:
    def __init__(self):
        self.typing = self.menu_open = False
        self.video_quality = quality.clean(None)
        self.environment = environment.clean(None)

    def settings(self): return {'video_quality': dict(self.video_quality)}


class Settings:
    def __init__(self, log): self.log, self.fail = log, None

    def save(self, settings):
        if self.fail: raise OSError(self.fail)
        self.log.append(settings)


class Keys:
    def __init__(self, view): self.view = view

    def close(self):
        self.view.keys_open = False
        return marker('keys_closed')


class View:
    """What VideoControl uses of view.ViewControl, recorded."""
    def __init__(self, clock):
        self.clock, self.run_id, self.session, self.next_id = clock, 'run1', 's1', 2
        self.state, self.help_visible, self.keys_open, self.video_active = State(), False, False, False
        self.last_input, self.owner_readback = 0.0, None
        self.told, self.markers, self.saves = [], [], []
        self.settings, self.keys = Settings(self.saves), Keys(self)

    def _on_menu(self, op):
        self.state.menu_open = False
        return marker('menu_closed')

    def _help_toggle(self):
        self.help_visible = False
        return marker('help_closed')

    def set_environment(self, env):
        self.state.environment = env
        return marker('environment', background=env['background'])

    def _tell(self, text): self.told.append(text)

    def on_message(self, message):
        self.markers.append(message['op'])
        if message['op'] == 'typing_toggle': self.state.typing = not self.state.typing
        return marker('view', op=message['op'])

    def poll(self, now): return b''
    def on_events(self, data): return b''
    def renderer_restarted(self): self.markers.append('renderer_restarted')


class Relay:
    def __init__(self, argv, log):
        self.argv, self.stopped, self.why = argv, False, None
        log.append(self)

    def start(self):
        r, w = os.pipe(); os.close(w)
        return r

    def stop(self): self.stopped = True
    def failure(self): return self.why


class Supervisor:
    def __init__(self): self.status, self.stopping_at = {}, None


class Session:
    def __init__(self, tmp, probe, web_info):
        self.tmp, self.clock = tmp, Clock()
        self.view = View(self.clock)
        self.binds, self.notified, self.relays, self.owners, self.done_log = [], [], [], {}, []
        self.retain = {}

        def web_probe(url, headers, ua):
            if 'forbidden' in url: raise video.VideoRejected('the site refused the stream (HTTP 403)')
            return dict(web_info) if web_info is not None else None

        def resolve(url): raise video.VideoRejected('no page here')
        self.v = video.VideoControl(self.view, binds=self.binds.append, probe=lambda fd: dict(probe) if probe else None,
                                    threads=False, clock=self.clock, web_probe=web_probe,
                                    relay_factory=lambda argv: Relay(argv, self.relays), notify=self.notified.append,
                                    web_resolve=resolve)
        self.sup = Supervisor(); self.v.attach_supervisor(self.sup)

    def owner(self, name):
        if name not in self.owners:
            def done(success, detail): self.done_log.append([name, success, detail])
            mode = self.retain.get(name, True)
            if mode != 'legacy':
                def retain(frames):
                    self.done_log.append([name, 'retain', frames.decode()])
                    mode = self.retain.get(name, True)
                    return not frames if mode == 'preflight' else mode
                done.retain_frames = retain
            self.owners[name] = done
        return self.owners[name]

    def join(self):
        for t in list(self.v._bind_threads): t.join(2)
        for t in threading.enumerate():
            if t is not threading.current_thread() and t.daemon: t.join(0.5)

    def step(self, step):
        out = None
        if 'op' in step:
            message = {'op': step['op'], 'payload': step.get('payload') or {}}
            if 'path' in message['payload']: message['payload'] = {**message['payload'], 'path': message['payload']['path'].replace('{tmp}', self.tmp)}
            done = self.owner(step['owner']) if 'owner' in step else None
            out = self.v.on_message(message, done=done) if done is not None else self.v.on_message(message)
        elif 'poll' in step:
            self.clock.now += step['poll']
            out = self.v.poll(self.clock())
        elif 'status' in step: self.sup.status = step['status']
        elif 'view' in step:
            for k, value in step['view'].items():
                if k in ('typing', 'menu_open'): setattr(self.view.state, k, value)
                else: setattr(self.view, k, value)
        elif 'readback' in step: self.view.owner_readback = {'video': step['readback']}
        elif 'restart' in step: self.v.renderer_restarted()
        elif 'reload' in step: self.v.on_events(b'configreloaded>>\n')
        elif 'close' in step: self.v.close()
        elif 'relay_fail' in step: self.relays[-1].why = step['relay_fail']
        elif 'quality' in step:
            done = self.owner(step['owner']) if 'owner' in step else None
            out = self.v._set_quality(step['quality'], done)
        elif 'save_fails' in step: self.view.settings.fail = step['save_fails']
        elif 'retain' in step: self.retain.update(step['retain'])
        else: raise ValueError(step)
        fds = []
        for fd in getattr(out, 'fds', []):
            fds.append('pipe' if stat.S_ISFIFO(os.fstat(fd).st_mode) else 'file')
        if isinstance(out, video.FdFrame): out.close_fds()
        self.join()
        snap = self.v.snapshot()
        return {'out': bytes(out or b'').decode(), 'fds': fds,
                'snapshot': json.loads(json.dumps(snap).replace(self.tmp, '{tmp}')),
                'binds': list(self.binds), 'notified': [t.replace(self.tmp, '{tmp}') for t in self.notified],
                'told': list(self.view.told), 'markers': list(self.view.markers), 'done': list(self.done_log),
                'saves': list(self.view.saves), 'video_active': self.view.video_active,
                'last_input': self.view.last_input,
                'relays': [{'argv': r.argv, 'stopped': r.stopped} for r in self.relays]}


def info(w=4096, h=4096, codec='hevc', **kw):
    out = {'width': w, 'height': h, 'codec': codec, 'fps': 30.0, 'duration': 60.0, 'projection': None,
           'coverage': None, 'stereo': None}
    out.update(kw)
    return out


WEB_INFO = {'width': 8192, 'height': 4096, 'codec': 'hevc', 'fps': 30.0, 'duration': 600.0, 'projection': None,
            'coverage': None, 'stereo': None}
CLIP = {'path': '{tmp}/Beach_360_TB.mp4'}


def web(*sources, **kw):
    out = {'viture_xr_video': 1, 'page': 'https://vr.example.com/watch/42', 'title': 'Sunset beach',
           'format': 'STEREO_180_LR', 'fov': None, 'start': 0, 'ua': 'Mozilla/5.0 test',
           'sources': list(sources) or [{'src': 'https://cdn.example.com/a.mp4', 'via': 'dl8'}]}
    out.update(kw)
    return out


def op(name, payload=None, **kw): return {'op': name, 'payload': payload or {}, **kw}


OPEN = [op('video_open', CLIP), {'poll': 0}]
PLAYING = {'status': {'video': 'playing'}}

SESSIONS = {
    'open_and_keys': {'steps': OPEN + [
        op('video_controls'), op('video_pause'), op('video_seek_back'), op('video_seek_fwd'), op('video_seek_back_big'),
        op('video_seek_fwd_big'), op('video_format'), op('video_format'), op('video_controls'), op('video_controls'),
        op('zoom_in'), op('next'), op('menu_toggle'), op('send_glasses'), op('resize_press'), op('super_release'),
        op('help'), op('help'), op('recenter'), op('prediction'), op('video_exit'), op('video_pause'), op('zoom_in'),
        op('help')]},
    'chosen_format': {'steps': [op('video_open', {**CLIP, 'format': {'projection': 'fisheye', 'stereo': 'sbs', 'fov': 200}}),
                                {'poll': 0}, op('video_format')]},
    'open_closes_the_panels': {'steps': [{'view': {'help_visible': True, 'menu_open': True, 'keys_open': True}}] + OPEN},
    'playback_menu_hidden': {'steps': [op('playback_menu_toggle')] + OPEN + [
        op('video_pause'), op('video_seek_fwd'), op('video_controls'), op('video_controls'), op('playback_menu_toggle'),
        op('video_pause'), op('playback_menu_toggle'), {'view': {'typing': True}}, op('playback_menu_toggle')]},
    'idle_close': {'steps': OPEN + [PLAYING, {'poll': 29}, {'poll': 1}, {'poll': 0}, op('video_seek_fwd'),
                                    {'poll': 29}, {'poll': 1}]},
    'renderer_error_and_no_support': {'steps': OPEN + [
        {'status': {'presented_hz': 120.0, 'video': 'playing'}}, {'poll': 10}, op('video_controls'),
        {'status': {'video': 'error'}}, {'poll': 0}, {'poll': 2}, {'poll': 2}, {'status': {}}] + OPEN + [
        {'poll': 4}, {'poll': 1}, {'status': {'video': 'dancing'}}] + OPEN + [{'poll': 5}]},
    'refused_files': {'steps': [op('video_open', {'path': '{tmp}/missing.mp4'}), {'poll': 0},
                                op('video_open', {'path': '{tmp}'}), {'poll': 0},
                                op('video_open', {'path': '{tmp}/empty.mp4'}), {'poll': 0}]},
    'refused_too_large': {'probe': info(w=20000, h=10000), 'steps': OPEN},
    'no_probe': {'probe': None, 'steps': [op('video_open', {'path': '{tmp}/Trip VR180_LR.mkv'}), {'poll': 0}]},
    'typing_mode_and_reload': {'steps': OPEN + [
        op('typing_toggle'), op('video_pause'), op('typing_toggle'), {'reload': True}, op('video_exit'),
        {'reload': True}]},
    'close_drops_the_unsent': {'steps': OPEN + [op('video_open', CLIP), {'close': True}, op('video_pause')]},
    'renderer_restarted': {'steps': OPEN + [{'restart': True}, op('video_pause'), {'restart': True}]},
    'quality_menu': {'steps': OPEN + [
        op('video_quality'), op('video_seek_fwd'), op('video_down'), op('video_seek_back'), op('video_format'),
        op('video_seek_fwd_big'), op('video_up'), op('video_up'), op('video_seek_back_big'), op('video_pause'),
        op('video_quality'), op('video_quality'), op('video_controls'), op('video_quality'), op('video_exit'),
        op('video_down'), {'save_fails': 'disk full'}, op('video_seek_fwd'), op('video_exit'), op('video_exit'),
        op('video_quality')] + OPEN},
    'web_open': {'steps': [
        op('video_open', {'web': web({'src': 'https://cdn.example.com/beach_4k.mp4', 'quality': '4K'},
                                     {'src': 'https://cdn.example.com/beach_8k.mp4', 'quality': '8K'}, start=42.3)}),
        {'poll': 0}, op('video_open', {'url': 'https://cdn.example.com/clips/Alps_360_TB.mp4'}), {'poll': 0},
        op('video_exit')]},
    'web_refusals': {'steps': [
        op('video_open', {'url': 'http://127.0.0.1/a.mp4'}), {'poll': 0},
        op('video_open', {'url': 'https://cdn.example.com/forbidden.mp4'}), {'poll': 0},
        op('video_open', {'web': web(format='MONO_FLAT')}), {'poll': 0},
        op('video_open', {'url': 'https://vr.example.com/watch/42'}), {'poll': 0}]},
    'web_seek_settles': {'steps': [
        op('video_open', {'url': 'https://cdn.example.com/clip.mp4'}), {'poll': 0}, PLAYING, {'poll': 0.5},
        {'poll': 0.5}, {'poll': 3}, op('video_seek_fwd'), {'poll': 0.2}, op('video_seek_fwd_big'), {'poll': 0.2},
        {'poll': 0.3}, op('video_seek_back_big'), op('video_seek_back_big'), {'poll': 0.5}, {'poll': 0.5}]},
    'web_live_stream': {'web_info': {**WEB_INFO, 'duration': None}, 'steps': [
        op('video_open', {'url': 'https://cdn.example.com/live.m3u8'}), {'poll': 0}, op('video_seek_fwd'),
        {'poll': 1}]},
    'web_dropped_link': {'steps': [
        op('video_open', {'url': 'https://cdn.example.com/clip.mp4'}), {'poll': 0}, PLAYING, {'poll': 0.5},
        {'poll': 1}, {'relay_fail': 'the connection dropped'}, {'status': {'video': 'ended'}}, {'poll': 0.1},
        {'relay_fail': 'the connection dropped'}, {'poll': 0.1}, {'relay_fail': 'the connection dropped'},
        {'poll': 0.1}]},
    'web_failure_at_start': {'steps': [
        op('video_open', {'url': 'https://cdn.example.com/clip.mp4'}), {'poll': 0},
        {'relay_fail': 'HTTP 404'}, {'status': {'video': 'error'}}, {'poll': 0.1}]},
    'owner_open_verified': {'steps': [
        op('video_open', CLIP, owner='a'), {'poll': 0},
        {'readback': {'opened': 1, 'player': {'state': 'playing'},
                      'playback': {'state': 'playing', 'paused': False, 'position_s': 0, 'duration_s': 100,
                                   'revision': 1, 'command_replies': 0, 'command_errors': 0}}},
        {'poll': 0},
        op('video_pause', owner='b'), {'poll': 0},
        {'readback': {'opened': 1, 'player': {}, 'playback': {'state': 'paused', 'paused': True, 'position_s': 1,
                                                              'duration_s': 100, 'revision': 2, 'command_replies': 1,
                                                              'command_errors': 0}}},
        {'poll': 0},
        op('video_seek_fwd', owner='c'),
        {'readback': {'opened': 1, 'player': {}, 'playback': {'state': 'paused', 'paused': True, 'position_s': 11.5,
                                                              'duration_s': 100, 'revision': 3, 'command_replies': 2,
                                                              'command_errors': 0}}},
        {'poll': 0},
        op('video_format', owner='d'), {'poll': 0},
        {'readback': {'opened': 1, 'player': {}, 'format_applied': {'projection': '360', 'stereo': 'sbs', 'fov': 180},
                      'playback': {'state': 'paused', 'revision': 3, 'command_replies': 2, 'command_errors': 0}}},
        {'poll': 0},
        op('video_pause', owner='e'),
        {'readback': {'opened': 1, 'player': {}, 'playback': {'state': 'paused', 'revision': 3, 'command_replies': 2,
                                                              'command_errors': 1, 'command_error': 'mpv refused'}}},
        {'poll': 0},
        op('video_exit', owner='f'), {'poll': 0}, {'readback': {'state': 'off'}}, {'poll': 0}]},
    'owner_refusals': {'steps': [
        op('video_pause', owner='a'), op('video_seek_fwd', owner='b'),
        op('video_open', {'path': '{tmp}/missing.mp4'}, owner='c'), {'poll': 0},
        op('video_open', CLIP, owner='d'), {'poll': 0},
        op('video_seek_fwd', owner='e'),
        {'readback': {'opened': 1, 'player': {}, 'playback': {'state': 'error', 'error': 'decoder died', 'revision': 1}}},
        {'poll': 0}, {'restart': True}]},
    'owner_stale': {'steps': [
        {'retain': {'a': False}}, op('video_open', CLIP, owner='a'), {'poll': 0},
        op('video_open', CLIP, owner='b'), {'retain': {'b': 'preflight'}}, {'poll': 0},
        {'retain': {'c': 'legacy'}}, op('video_open', CLIP, owner='c'), {'poll': 0}, {'close': True}]},
    'owner_quality': {'steps': [
        {'quality': {'filter': 'sharp', 'deband': True}, 'owner': 'a'},
        {'readback': {'quality_readback': {'filter_requested': 'sharp', 'deband_requested': True}}}, {'poll': 0}]
        + OPEN + [
        {'quality': {'filter': 'bilinear', 'deband': False}, 'owner': 'b'},
        {'readback': {'quality_readback': {'filter_applied': 'bilinear', 'deband_applied': False}}}, {'poll': 0},
        {'save_fails': 'read-only'}, {'quality': {'filter': 'sharp', 'deband': False}, 'owner': 'c'},
        {'quality': {'filter': 'sharp', 'deband': False}}, {'close': True}]},
    'owner_web_seek': {'steps': [
        op('video_open', {'url': 'https://cdn.example.com/clip.mp4'}), {'poll': 0}, PLAYING,
        {'readback': {'player': {}, 'playback': {'position_s': 20, 'revision': 1, 'command_replies': 0,
                                                 'command_errors': 0}}},
        op('video_seek_fwd', owner='a'), op('video_seek_fwd', owner='b'), {'poll': 0.5},
        {'retain': {'c': False}}, op('video_seek_back', owner='c'), {'poll': 0.5}]},
}


def record():
    out = {}
    for name, spec in SESSIONS.items():
        with tempfile.TemporaryDirectory() as tmp:
            for f in ('Beach_360_TB.mp4', 'Trip VR180_LR.mkv'):
                Path(tmp, f).write_bytes(b'v' * 100)
            Path(tmp, 'empty.mp4').write_bytes(b'')
            s = Session(tmp, spec.get('probe', info()), spec.get('web_info', WEB_INFO))
            records = []
            try:
                for step in spec['steps']:
                    records.append(s.step(step))
            finally:
                s.v.close()
            out[name] = {'probe': spec.get('probe', info()), 'web_info': spec.get('web_info', WEB_INFO),
                         'steps': spec['steps'], 'records': records}
    return out


def main(argv):
    data = record()
    text = json.dumps(data, indent=1, sort_keys=False) + '\n'
    if '--write' in argv:
        CORPUS.parent.mkdir(parents=True, exist_ok=True)
        CORPUS.write_text(text)
        print(f'{CORPUS}: {len(data)} sessions, {sum(len(s["records"]) for s in data.values())} steps')
        return 0
    if not CORPUS.exists() or CORPUS.read_text() != text:
        print(f'{CORPUS} differs from the Python: regenerate with --write', file=sys.stderr)
        return 1
    return 0


if __name__ == '__main__':
    sys.exit(main(sys.argv[1:]))
