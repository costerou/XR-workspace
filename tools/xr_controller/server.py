"""Bounded same-UID newline JSON service, with one serialized selector loop."""
import json
import os
from pathlib import Path
import selectors
import socket
import time
from tools.native_spike.ipc import MAX_FRAME, check_peer, private_dir, strict_json


class Server:
    def __init__(self,controller,path):
        self.controller,self.path=controller,Path(path)
        self.directory=private_dir(self.path.parent,create=True)
        self.selector=selectors.DefaultSelector(); self.clients={}
        self.listener=socket.socket(socket.AF_UNIX)
        try:
            self.listener.bind(f'/proc/self/fd/{self.directory}/{self.path.name}')
            os.chmod(self.path.name,0o600,dir_fd=self.directory,follow_symlinks=False)
            self.identity=os.stat(self.path.name,dir_fd=self.directory,follow_symlinks=False).st_ino
            self.listener.listen(16); self.listener.setblocking(False)
            self.selector.register(self.listener,selectors.EVENT_READ)
        except BaseException:
            self.listener.close(); self.selector.close(); os.close(self.directory); raise
        self.last=controller.snapshot(); self.next_tick=time.monotonic()+1

    def _drop(self,c):
        self.selector.unregister(c); self.clients.pop(c,None); c.close()

    def _send(self,c,obj):
        if c not in self.clients: return
        data=(json.dumps(obj,allow_nan=False,separators=(',',':'))+'\n').encode()
        output=self.clients[c][1]
        if len(data)>MAX_FRAME or len(output)+len(data)>4*MAX_FRAME: self._drop(c); return
        output.extend(data); self.selector.modify(c,selectors.EVENT_READ|selectors.EVENT_WRITE)

    def step(self,timeout=1):
        for key,mask in self.selector.select(timeout):
            c=key.fileobj
            if c is self.listener:
                conn,_=c.accept()
                try: check_peer(conn,os.getuid())
                except (OSError,PermissionError): conn.close(); continue
                conn.setblocking(False)
                if len(self.clients)>=32: conn.close(); continue
                self.clients[conn]=[bytearray(),bytearray()]; self.selector.register(conn,selectors.EVENT_READ)
                continue
            try:
                if mask & selectors.EVENT_READ:
                    raw=c.recv(MAX_FRAME+1)
                    if not raw: self._drop(c); continue
                    incoming=self.clients[c][0]; incoming.extend(raw)
                    while b'\n' in incoming:
                        line,_,rest=incoming.partition(b'\n'); incoming[:]=rest
                        if len(line)+1>MAX_FRAME: raise ValueError('oversize frame')
                        msg=strict_json(line)
                        if not isinstance(msg,dict) or type(msg.get('schema')) is not int or msg['schema']!=1: raise ValueError('bad schema')
                        rid=msg.get('request_id')
                        if rid is not None and (type(rid) is not int or not 0<rid<=2**64-1): raise ValueError('bad request_id')
                        reply=self.controller.request(msg)
                        self._send(c,dict(schema=1,**reply,**({'request_id':rid} if rid is not None else {})))
                        if c not in self.clients: break
                    if c in self.clients and len(incoming)>=MAX_FRAME: raise ValueError('oversize frame')
                if c in self.clients and mask & selectors.EVENT_WRITE:
                    output=self.clients[c][1]; sent=c.send(output); del output[:sent]
                    if not output: self.selector.modify(c,selectors.EVENT_READ)
            except BlockingIOError: pass
            except (OSError,ValueError,TypeError):
                if c in self.clients: self._drop(c)
        if time.monotonic()>=self.next_tick:
            self.controller.tick(); self.next_tick=time.monotonic()+1
        snapshot=self.controller.snapshot()
        if snapshot!=self.last:
            self.last=snapshot
            for c in list(self.clients): self._send(c,dict(schema=1,event='state',**snapshot))

    def close(self):
        for c in list(self.clients): self._drop(c)
        self.selector.close(); self.listener.close()
        try:
            if os.stat(self.path.name,dir_fd=self.directory,follow_symlinks=False).st_ino==self.identity:
                os.unlink(self.path.name,dir_fd=self.directory)
        finally: os.close(self.directory)


def serve(controller,path):
    server=Server(controller,path)
    try:
        while True: server.step()
    finally: server.close()
