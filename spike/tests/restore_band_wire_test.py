"""CPU contract: actual native geometry through the production supervisor route: a rest on the top edge, the dot into the band, Restore.
No injected hit mesh, compositor socket, ownership hook or GPU.
"""
import json
from pathlib import Path
import subprocess
import sys
sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
from tools.native_spike import free_pointer as fp, restore_band as rb, view


def main(binary):
    result = subprocess.run([binary, '--dump'], check=True, capture_output=True, text=True)
    count = 0
    for line in result.stdout.splitlines():
        g = fp.parse_maximize_geometry(json.loads(line)['maximize_geometry'])
        output = dict(index=1, x=1920, y=80, width=1920, height=1080)
        now = [1.0]
        control = view.ViewControl(view.ViewState(['XR-1', 'XR-3']), 'run', 'session', lambda n: None, None, None)
        control.state.selected = 1
        control.state.apply('zoom_in')
        pointer = fp.OverviewPointer(control)
        control.overview_pointer = pointer
        try:
            control.set_maximize_geometry(g)
            top = (output['x'] + 300, output['y'])
            for tick in range(50):
                pointer.feed(top, (0, 0), [output], pointer.want[0])
                pointer.poll(1 + tick * .01)
            assert pointer.mode == 'restore' and pointer.want[1] == 'free', count
            x, y, w, h = g['rect']
            cx, cy = fp.band_canvas(g, x + w / 2, y + h / 2)
            at = pointer.band_point
            pointer.feed(top, ((cx - at[0]) / pointer.band_gain, (cy - at[1]) / pointer.band_gain), [output], pointer.want[0])
            pointer.poll(2)
            assert pointer.band_hot(), count
            pointer.on_op('resize_press')
            assert control.state.zoomed
            pointer.on_op('resize_release')
            assert not control.state.zoomed and control.state.selected == 1, count
        finally:
            pointer.close()
        count += 1
    assert count == 216, count
    print(f'{count} native-exported Step-Back Band routes passed through production supervisor press/release')


if __name__ == '__main__':
    main(sys.argv[1])
