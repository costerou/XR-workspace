"""CPU contract: actual native geometry, actual app cursor, production Python input route.
No injected hit mesh, pointer excursion, compositor socket, ownership hook or GPU.
"""
import json
from pathlib import Path
import subprocess
import sys
sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
from tools.native_spike import free_pointer as fp, view

def main(binary):
    result = subprocess.run([binary, '--dump'], check=True, capture_output=True, text=True)
    count = 0
    for line in result.stdout.splitlines():
        g = fp.parse_maximize_geometry(json.loads(line)['maximize_geometry'])
        tile, rect = g['tile'], g['rect']
        output = dict(index=1, x=1920, y=80, width=1920, height=1080)
        point = tuple(output[k] + int((rect[i] + rect[i+2]/2 - tile[i])/tile[i+2]*output[size])
                      for i,k,size in ((0,'x','width'),(1,'y','height')))
        assert fp.restore_mesh_hit(g, fp.restore_cursor_ray(g, output, point)), (count, point)
        control = view.ViewControl(view.ViewState(['XR-1','XR-3']), 'run', 'session', lambda n: None, None, None)
        control.state.selected = 1
        control.state.apply('zoom_in')
        pointer = fp.OverviewPointer(control)
        control.overview_pointer = pointer
        try:
            control.set_maximize_geometry(g)
            pointer.feed(point, (0,0), [output], 0)
            pointer.poll(1)
            assert pointer.mode == 'restore' and pointer.want[1] == 'free', count
            pointer.on_op('resize_press')
            assert control.state.zoomed
            pointer.on_op('resize_release')
            assert not control.state.zoomed and control.state.selected == 1, count
        finally:
            pointer.close()
        count += 1
    assert count == 432, count
    print(f'{count} native-exported app-bound Restore routes passed through production supervisor press/release')

if __name__ == '__main__':
    main(sys.argv[1])
