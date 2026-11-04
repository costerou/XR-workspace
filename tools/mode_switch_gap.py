"""Offline fake-clock display reconnect timing; no hardware or live services."""
import json
from tools.native_spike.survive import wait_for_glasses


def measure(settle):
    now = [0.0]
    def serve(seconds): now[0] += seconds
    restored = []
    wait_for_glasses(lambda: now[0] >= 1.0,
                     restore=lambda: restored.append(now[0]), serve=serve,
                     clock=lambda: now[0], settle=settle)
    return restored[0]


if __name__ == '__main__':
    print(json.dumps({'fake_link_down_s': 1.0, 'before_resume_s': measure(3.0),
                      'pending_switch_resume_s': measure(0.0),
                      'ordinary_loss_resume_s': measure(3.0),
                      'excludes': 'renderer startup, scanout and physical R1 blackout'}, indent=2))
