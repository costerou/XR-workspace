"""Native child content. No dialogs, subprocesses, configuration or persistence ownership.

Outer OptionsMenu owns namespace/revision/focus and projects these bounded rows.
Committed text enters apply('editor.text', text); no shell strings are evaluated.
"""
from pathlib import Path
from itertools import islice
import os


def row(id, label, kind='action', value='', **extra):
    def bounded(text, size): return str(text).encode('utf-8')[:size].decode('utf-8','ignore')
    return dict(id=id, label=bounded(label,96), scope='workspace', kind=kind,
                value=bounded(value,256), enabled=True, **extra)


class NativeEditors:
    PAGE = 6

    def __init__(self, control, directory=None):
        self.control = control
        self.directory = Path(directory or Path.home()).resolve()
        self.kind = None
        self.text = ''
        self.page = 0
        self.entries = []
        self.text_input = None
        self.text_field = None
        self.character = 'a'
        self.hex_digits = list('000000')

    def open(self, kind):
        if kind not in ('file', 'url', 'keybinds'): raise ValueError('unknown child')
        self.kind, self.page, self.text = kind, 0, ''
        self.text_field='editor.text' if kind=='url' else None
        if kind == 'keybinds':
            if self.control.keys is None: raise ValueError('keybind owner unavailable')
            return self.control.keys.open_panel()
        if kind == 'file':
            self.text=str(self.directory)
            self._scan()
        return b''

    def _scan(self):
        # Bound a directory before presentation; errors are surfaced to parent.
        with os.scandir(self.directory) as entries:
            limited = [Path(item.path) for item in islice(entries,4097)]
        if len(limited)>4096: raise ValueError('Folder exceeds 4096 entries; enter a narrower folder path')
        self.entries = sorted(limited, key=lambda p: (not p.is_dir(), p.name.casefold()))
        self.text=str(self.directory)

    def close(self):
        out = self.control.keys.close() if self.kind == 'keybinds' and self.control.keys else b''
        if self.text_input: self.text_input.close(); self.text_input = None
        self.kind, self.text, self.entries = None, '', []
        return out

    def rows(self):
        if self.kind == 'url':
            return [row('editor.text', 'Video URL or page link', 'text', self.text),
                    row('editor.submit', 'Submit URL'), row('editor.cancel', 'Cancel')] + self.text_rows()
        if self.kind == 'file':
            out = [row('editor.path', 'Folder path', 'text', self.text), row('editor.parent', 'Parent folder')]
            for n, path in enumerate(self.entries[self.page*self.PAGE:(self.page+1)*self.PAGE]):
                out.append(row(f'editor.file.{n}', ('Folder: ' if path.is_dir() else '') + path.name))
            return out + [row('editor.previous', 'Previous files'), row('editor.next', 'Next files'), row('editor.cancel', 'Cancel'),row('editor.submit','Submit folder path')] + self.text_rows()
        if self.kind == 'keybinds':
            keys = self.control.keys
            data = keys.payload()
            out = [row('editor.bind.status','Keybind status','readonly',data.get('status',''))]
            start = self.page*self.PAGE
            for n, item in enumerate(data.get('rows',[])[start:start+self.PAGE]):
                out.append(row(f'editor.bind.{start+n}',item['action'],'action',item['keys'] + ' ' + item.get('note','')))
            return out + [row('editor.previous','Previous binds'),row('editor.next','Next binds'),row('editor.bind.reset','Reset selected bind'),row('editor.bind.reset_all','Reset all (press twice)'),row('editor.cancel','Cancel')]
        return []

    CHARSETS = {'lower':'abcdefghijklmnopqrstuvwxyz','upper':'ABCDEFGHIJKLMNOPQRSTUVWXYZ',
                'digits':'0123456789','punctuation':":/?#[]@!$&'()*+,;=%._-~\\ "}

    def text_rows(self):
        out=[row('editor.hardware','Physical text input','readonly','Unavailable without authoritative keyboard state; use character controls')]
        for name,chars in self.CHARSETS.items():
            out.append(row('editor.char.'+name,'Choose '+name,'choice',self.character if self.character in chars else chars[0],choices=list(chars)))
        out += [row('editor.selected','Selected character','readonly',self.character),row('editor.insert','Insert character'),row('editor.backspace','Backspace'),row('editor.clear','Clear text')]
        for n,digit in enumerate(self.hex_digits): out.append(row(f'editor.hex.{n}',f'Unicode hex digit {n+1}','choice',digit,choices=list('0123456789abcdef')))
        return out+[row('editor.unicode','Insert Unicode scalar')]

    def _buffer(self,text):
        if not isinstance(text,str) or len(text.encode('utf-8'))>256 or any(not ch.isprintable() for ch in text): raise ValueError('Text requires printable UTF-8, at most 256 bytes')
        self.text=text
        return b''

    def apply(self, id, value=None):
        if id == 'editor.cancel': return self.close()
        if self.kind in ('url','file'):
            if id.startswith('editor.char.'):
                group=id.rsplit('.',1)[1]
                if group not in self.CHARSETS or value not in self.CHARSETS[group] or not isinstance(value,str) or len(value)!=1: raise ValueError('Invalid character')
                self.character=value; return b''
            if id.startswith('editor.hex.'):
                n=int(id.rsplit('.',1)[1])
                if not 0<=n<6 or not isinstance(value,str) or len(value)!=1 or value not in '0123456789abcdef': raise ValueError('Invalid hex digit')
                self.hex_digits[n]=value; return b''
            if id=='editor.unicode':
                scalar=int(''.join(self.hex_digits),16)
                if scalar>0x10ffff or 0xd800<=scalar<=0xdfff or not chr(scalar).isprintable(): raise ValueError('Invalid printable Unicode scalar')
                return self._buffer(self.text+chr(scalar))
            if id=='editor.insert': return self._buffer(self.text+self.character)
            if id=='editor.clear': return self._buffer('')
            if id=='editor.backspace': return self._buffer(self.text[:-1])
            if id in ('editor.text','editor.path'): return self._buffer(value)
            if id=='editor.submit':
                if self.kind=='url': return self.apply('editor.play')
                if not Path(self.text).is_absolute(): raise ValueError('Absolute folder path required')
                directory=Path(self.text).resolve()
                with os.scandir(directory) as entries:
                    limited=[Path(item.path) for item in islice(entries,4097)]
                if len(limited)>4096: raise ValueError('Folder exceeds 4096 entries; enter narrower folder path')
                self.directory,self.page,self.entries=directory,0,sorted(limited,key=lambda p:(not p.is_dir(),p.name.casefold()))
                return b''
        if self.kind == 'keybinds':
            keys = self.control.keys
            if id == 'editor.bind.reset': return keys.key(0,27)
            if id == 'editor.bind.reset_all': return keys.key(1,27)
            if id in ('editor.previous','editor.next'):
                self.page = min(max(0,self.page+(1 if id.endswith('next') else -1)),max(0,(len(keys.payload()['rows'])-1)//self.PAGE))
                return b''
            if id.startswith('editor.bind.'):
                n = int(id.rsplit('.',1)[1])
                if not 0 <= n < len(keys.payload()['rows']): raise ValueError('invalid bind row')
                keys.selected = n
                return keys.key(0,36)
            raise ValueError('unknown keybind action')
        if self.kind == 'url':
            if id == 'editor.text':
                if not isinstance(value, str) or len(value.encode('utf-8')) > 256: raise ValueError('text too long')
                self.text = value
                return b''
            if id == 'editor.play':
                if not self.text.startswith(('https://', 'http://')): raise ValueError('HTTP(S) URL required')
                return self._play({'url': self.text})
        if self.kind == 'file':
            if id == 'editor.parent': self.directory, self.page = self.directory.parent, 0; self._scan()
            elif id in ('editor.previous', 'editor.next'):
                self.page = min(max(0, self.page + (1 if id.endswith('next') else -1)), max(0, (len(self.entries)-1)//self.PAGE))
            elif id.startswith('editor.file.'):
                n = int(id.rsplit('.', 1)[1])
                if not 0 <= n < self.PAGE: raise ValueError('invalid file row')
                p = self.entries[self.page*self.PAGE+n]
                if p.is_dir(): self.directory, self.page = p.resolve(), 0; self._scan()
                else: return self._play({'path': str(p)})
            else: raise ValueError('unknown file action')
            return b''
        raise ValueError('no matching child action')

    def _play(self, payload):
        video = getattr(self.control, 'options_video', None)
        if video is None: raise ValueError('video owner unavailable')
        from .options_actions import video_request
        out = video_request(self.control,'video_open',payload,'editor.play')
        self.close()
        return out

    def begin_text(self, keymap, locale='C.UTF-8'):
        from .options_text import TextInput
        if self.kind not in ('url','file'): raise ValueError('text editor not open')
        self.text_field = 'editor.text' if self.kind == 'url' else 'editor.path'
        if self.kind == 'file': self.text = str(self.directory)
        if self.text_input: self.text_input.close()
        self.text_input = TextInput(keymap,locale)

    def key(self, code):
        """Forward only focused press events, after compositor modifier updates."""
        if self.text_input is None: raise ValueError('text capture not initialized')
        action, text = self.text_input.key(code)
        if action == 'cancel': return self.close()
        if action == 'submit': return self.apply('editor.submit')
        updated = self.text[:-1] if action == 'backspace' else self.text+text
        if len(updated.encode())>256: raise ValueError('text too long')
        self.text = updated
        return self.apply('editor.text',updated) if self.kind == 'url' else b''
