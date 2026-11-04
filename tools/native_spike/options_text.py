"""Focused native text input using the compositor-supplied xkb keymap.

No keyboard subscription/ownership here. Outer supervisor forwards XKB keycodes
(evdev + 8), and compositor modifier masks via modifiers(). close on focus loss.
"""
import ctypes as C
import ctypes.util


class TextInput:
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
