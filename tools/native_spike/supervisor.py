"""Independent watchdog. Caller owns its Popen child, heartbeat FD and recovery adapter."""
import math
import selectors
import time


class Supervisor:
    def __init__(self, child, recovery, *, now=None, grace=0.5):
        if not math.isfinite(grace) or grace < 0: raise ValueError("invalid grace")
        self.child, self.recovery = child, recovery
        self.last_heartbeat = time.monotonic() if now is None else now
        self.grace = grace
        self.stopping_at = None
        self.finished = False
        self.kill_sent = False
        self.result = None
    def heartbeat(self, now):
        if self.stopping_at is None and math.isfinite(now) and now >= self.last_heartbeat:
            self.last_heartbeat = now
    def disconnect(self, now):
        if self.finished or self.stopping_at is not None: return
        self.stopping_at = now
        if self.child.poll() is None:
            try: self.child.terminate()
            except ProcessLookupError: pass
    def tick(self, now):
        if self.finished: return
        if self.child.poll() is None:
            if self.stopping_at is None and now - self.last_heartbeat >= 2:
                self.disconnect(now)
            if self.stopping_at is not None and not self.kill_sent and now - self.stopping_at >= self.grace:
                self.kill_sent = True
                try: self.child.kill()
                except ProcessLookupError: pass
            if self.child.poll() is None: return
        self.result = self.recovery()
        self.finished = True
    def run(self, heartbeat_socket, *, interval=0.05):
        """Run in supervisor process, not renderer. EOF means disconnect; only b'H' is valid."""
        if not 0 < interval <= 0.1: raise ValueError("invalid poll interval")
        with selectors.DefaultSelector() as selector:
            selector.register(heartbeat_socket, selectors.EVENT_READ)
            while not self.finished:
                for key, _ in selector.select(interval):
                    data = heartbeat_socket.recv(256)
                    now = time.monotonic()
                    if data and all(byte == ord("H") for byte in data): self.heartbeat(now)
                    else:
                        self.disconnect(now)
                        selector.unregister(heartbeat_socket)
                self.tick(time.monotonic())
        return self.result
