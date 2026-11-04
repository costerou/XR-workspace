"""Focused native text input using the compositor-supplied xkb keymap.

No keyboard subscription/ownership here. Outer supervisor forwards XKB keycodes
(evdev + 8), and compositor modifier masks via modifiers(). close on focus loss.
"""
import ctypes as C
import ctypes.util


class TextInput:
    @classmethod
    def from_names(cls, names):
        """Compile the active compositor keyboard's reported XKB names, never assume US."""
        fields = ('rules','model','layout','variant','options')
        if not isinstance(names, dict) or not names.get('layout'): raise ValueError('Compositor keyboard layout unavailable')
        values = [names.get(k, '') for k in fields]
        if any(not isinstance(v,str) or len(v.encode()) > 256 for v in values): raise ValueError('Invalid compositor keyboard names')
        class Names(C.Structure):
            _fields_ = [(k,C.c_char_p) for k in fields]
        lib=C.CDLL(ctypes.util.find_library('xkbcommon') or 'libxkbcommon.so.0')
        lib.xkb_context_new.restype=C.c_void_p
        lib.xkb_keymap_new_from_names.argtypes=[C.c_void_p,C.POINTER(Names),C.c_int]
        lib.xkb_keymap_new_from_names.restype=C.c_void_p
        lib.xkb_keymap_get_as_string.argtypes=[C.c_void_p,C.c_int]
        lib.xkb_keymap_get_as_string.restype=C.c_void_p
        lib.xkb_keymap_unref.argtypes=[C.c_void_p]
        lib.xkb_context_unref.argtypes=[C.c_void_p]
        context=lib.xkb_context_new(0); keymap=None; raw=None
        libc=C.CDLL(None); libc.free.argtypes=[C.c_void_p]
        try:
            keymap=lib.xkb_keymap_new_from_names(context,C.byref(Names(*[v.encode() or None for v in values])),0)
            if not keymap: raise ValueError('Cannot compile compositor keyboard layout')
            raw=lib.xkb_keymap_get_as_string(keymap,1)
            return cls(C.string_at(raw).decode())
        finally:
            if raw: libc.free(raw)
            if keymap: lib.xkb_keymap_unref(keymap)
            if context: lib.xkb_context_unref(context)

    def hypr_modifiers(self, mods):
        self.lib.xkb_keymap_mod_get_index.argtypes=[C.c_void_p,C.c_char_p]
        self.lib.xkb_keymap_mod_get_index.restype=C.c_uint32
        mask=0
        for bit,name in ((1,b'Shift'),(2,b'Lock'),(4,b'Control'),(8,b'Mod1'),(16,b'Mod2'),(32,b'Mod3'),(64,b'Mod4'),(128,b'Mod5')):
            index=self.lib.xkb_keymap_mod_get_index(self.keymap,name)
            if mods & bit and index < 32: mask |= 1 << index
        self.modifiers(mask)

    def __init__(self, keymap, locale='C.UTF-8'):
        if not isinstance(keymap,str) or len(keymap.encode()) > 1024*1024: raise ValueError('invalid keymap')
        self.lib = lib = C.CDLL(ctypes.util.find_library('xkbcommon') or 'libxkbcommon.so.0')
        signatures = {
            'context_new':(C.c_void_p,[C.c_int]),
            'keymap_new_from_string':(C.c_void_p,[C.c_void_p,C.c_char_p,C.c_int,C.c_int]),
            'state_new':(C.c_void_p,[C.c_void_p]),
            'state_update_mask':(C.c_int,[C.c_void_p]+[C.c_uint32]*6),
            'state_key_get_utf8':(C.c_int,[C.c_void_p,C.c_uint32,C.c_char_p,C.c_size_t]),
            'state_key_get_one_sym':(C.c_uint32,[C.c_void_p,C.c_uint32]),
            'compose_table_new_from_locale':(C.c_void_p,[C.c_void_p,C.c_char_p,C.c_int]),
            'compose_state_new':(C.c_void_p,[C.c_void_p,C.c_int]),
            'compose_state_feed':(C.c_int,[C.c_void_p,C.c_uint32]),
            'compose_state_get_status':(C.c_int,[C.c_void_p]),
            'compose_state_get_utf8':(C.c_int,[C.c_void_p,C.c_char_p,C.c_size_t]),
            'compose_state_reset':(None,[C.c_void_p]),
        }
        for name,(restype,args) in signatures.items():
            fn=getattr(lib,'xkb_'+name); fn.restype,fn.argtypes=restype,args
        for name in ('state','keymap','context','compose_state','compose_table'):
            fn=getattr(lib,'xkb_'+name+'_unref'); fn.restype,fn.argtypes=None,[C.c_void_p]
        self.context=lib.xkb_context_new(0)
        self.keymap=lib.xkb_keymap_new_from_string(self.context,keymap.encode(),1,0)
        self.state=lib.xkb_state_new(self.keymap) if self.keymap else None
        self.table=lib.xkb_compose_table_new_from_locale(self.context,locale.encode(),0)
        self.compose=lib.xkb_compose_state_new(self.table,0) if self.table else None
        if not self.state:
            self.close(); raise ValueError('cannot compile compositor keymap')

    def modifiers(self,depressed=0,latched=0,locked=0,group=0):
        if any(type(v) is not int or not 0 <= v <= 0xffffffff for v in (depressed,latched,locked,group)): raise ValueError('invalid modifiers')
        self.lib.xkb_state_update_mask(self.state,depressed,latched,locked,0,0,group)

    def key(self,code):
        if type(code) is not int or not 8 <= code <= 65535 or not self.state: raise ValueError('invalid key')
        sym=self.lib.xkb_state_key_get_one_sym(self.state,code)
        if sym == 0xff1b: return 'cancel',''
        if sym in (0xff0d,0xff8d): return 'submit',''
        if sym == 0xff08: return 'backspace',''
        buf=C.create_string_buffer(256)
        if self.compose:
            self.lib.xkb_compose_state_feed(self.compose,sym)
            status=self.lib.xkb_compose_state_get_status(self.compose)
            if status == 1: return 'text',''
            if status == 2:
                self.lib.xkb_compose_state_get_utf8(self.compose,buf,len(buf))
                self.lib.xkb_compose_state_reset(self.compose)
                return 'text',buf.value.decode('utf-8')
            if status == 3:
                self.lib.xkb_compose_state_reset(self.compose); return 'text',''
        self.lib.xkb_state_key_get_utf8(self.state,code,buf,len(buf))
        text=buf.value.decode('utf-8')
        return 'text',''.join(ch for ch in text if ch.isprintable())

    def close(self):
        for attr,name in (('compose','compose_state'),('table','compose_table'),('state','state'),('keymap','keymap'),('context','context')):
            obj=getattr(self,attr,None)
            if obj: getattr(self.lib,'xkb_'+name+'_unref')(obj); setattr(self,attr,None)


class RuntimeText:
    """Lazy text translation from the current compositor keyboard description."""
    def __init__(self, control, client):
        self.control, self.client = control, client

    def __call__(self, mods, code):
        import json
        editor = getattr(self.control, 'options_editor', None)
        if editor is None or editor.kind != 'url': return ''
        if editor.text_input is None:
            devices = json.loads(self.client._request('j/devices'))
            keyboards = devices.get('keyboards', [])
            keyboard = next((k for k in keyboards if k.get('main')), None)
            if keyboard is None and len(keyboards) == 1: keyboard = keyboards[0]
            if keyboard is None: raise ValueError('Active compositor keyboard unavailable')
            editor.text_input = TextInput.from_names(keyboard)
            editor.text_field = 'editor.text'
            editor.text_group = keyboard.get('active_layout_index', 0)
        editor.text_input.hypr_modifiers(mods)
        action, text = editor.text_input.key(code)
        return text if action == 'text' else ''
