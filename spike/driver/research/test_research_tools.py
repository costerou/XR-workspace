"""Run with: python3 -m pytest spike/driver/research -q"""
import os
import re
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

HERE = Path(__file__).parent
sys.path.insert(0, str(HERE))
import session_probe  # noqa: E402
import usb_descriptors as usb  # noqa: E402

FAKE_SDK = """
#include <stdio.h>
static int order = 0;
int xr_device_provider_get_device_type(void *h) { return (int)(long)h; }
int xr_device_provider_set_dof_type_carina(void *h, int dof) { printf("set_dof %d order %d\\n", dof, order++); return 0; }
int xr_device_provider_initialize(void *h, const char *c, const char *d) { printf("initialize order %d\\n", order++); return 7; }
"""
CALLER = """
#include <stdio.h>
#include <stdlib.h>
int xr_device_provider_initialize(void *, const char *, const char *);
int main(int argc, char **argv) { printf("returned %d\\n", xr_device_provider_initialize((void *)(long)atoi(argv[1]), 0, 0)); }
"""


def fixture(name):
    return usb.parse((HERE / 'fixtures' / name).read_bytes())


def test_camera_is_one_ordinary_uvc_sensor():
    camera = fixture('sonix_0c45_636b.bin')
    assert camera['max_power_ma'] == 500
    assert len(usb.uvc_streaming_interfaces(camera)) == 1  # a stereo pair would need a second stream or a side-by-side size
    assert {f['name'] for f in camera['formats']} == {'MJPEG', 'YUY2'}  # no GREY/Y8/Y16: no infrared or tracking format
    sizes = {(x['width'], x['height']) for f in camera['formats'] for x in f['frames']}
    assert (1920, 1080) in sizes and (3840, 1080) not in sizes and (2560, 720) not in sizes


def test_camera_frame_rates_cap_below_tracking_needs():
    formats = {f['name']: f['frames'] for f in fixture('sonix_0c45_636b.bin')['formats']}
    assert max(max(x['fps']) for x in formats['MJPEG']) == 30
    vga = next(x for x in formats['YUY2'] if (x['width'], x['height']) == (640, 480))
    assert max(vga['fps']) == 25
    full = next(x for x in formats['YUY2'] if x['width'] == 1920)
    assert full['fps'] == [5]


def test_camera_can_reserve_half_the_periodic_budget():
    camera = fixture('sonix_0c45_636b.bin')
    assert usb.largest_iso_reservation(camera) == pytest.approx(24.576e6)


def test_glasses_interface_is_vendor_and_not_uvc():
    glasses = fixture('luma_ultra_35ca_1104.bin')
    assert not usb.uvc_streaming_interfaces(glasses) and glasses['formats'] == []
    assert glasses['max_power_ma'] == 2 and usb.self_powered(glasses)
    endpoints = {e['address']: e for e in glasses['interfaces'][0]['endpoints']}
    assert len(endpoints) == 7
    assert [hex(a) for a, e in endpoints.items() if e['type'] == 'bulk' and e['direction'] == 'in'] == ['0x82', '0x85', '0x87']
    assert endpoints[0x81]['type'] == 'interrupt' and endpoints[0x81]['period_us'] == 500
    assert endpoints[0x83]['period_us'] == 1000


def test_hub_says_it_is_self_powered():
    assert usb.self_powered(fixture('hub_1a86_8091.bin'))


def test_truncated_blob_stops_cleanly():
    blob = (HERE / 'fixtures/luma_ultra_35ca_1104.bin').read_bytes()
    assert usb.parse(blob[:1])['interfaces'] == []
    assert usb.parse(b'\0' * 40)['interfaces'] == []


def test_report_is_printable():
    text = usb.report(fixture('sonix_0c45_636b.bin'))
    assert '500 mA' in text and '1920x1080' in text


def test_parse_task_stat_survives_awkward_names():
    fields = ' '.join(str(n) for n in range(3, 53))
    name, ticks, processor = session_probe.parse_task_stat(f'42 (vio (cam) 1) {fields}')
    assert name == 'vio (cam) 1'
    assert ticks == 14 + 15 and processor == 39


def test_thread_cpu_counts_new_threads_from_zero():
    rows = session_probe.thread_cpu({'1': ('a', 100, 0)}, {'1': ('a', 150, 0), '2': ('vio', 400, 3)}, 10, 100)
    assert [(r['name'], r['cpu_percent']) for r in rows] == [('vio', 40.0), ('a', 5.0)]


def test_find_device_reads_ids_and_skips_interfaces(tmp_path):
    (tmp_path / '5-2:1.0').mkdir()
    device = tmp_path / '5-2.1'
    device.mkdir()
    (device / 'idVendor').write_text('35ca\n')
    (device / 'idProduct').write_text('1104\n')
    assert session_probe.find_device(('35ca', '1104'), tmp_path) == device
    assert session_probe.find_device(('0c45', '636b'), tmp_path) is None


def test_summary_reports_rates_and_motion():
    samples = [{'glasses_urbs': 100, 'camera_urbs': 5, 'position': (0.0, 0.0, 0.0)},
               {'glasses_urbs': 2100, 'camera_urbs': 5, 'position': (0.01, 0.0, 0.0)}]
    out = session_probe.summarise(samples, [{'cpu_percent': 12.5}], 2.0, [])
    assert out['glasses_urbs_per_s'] == 1000 and out['camera_urbs'] == 0
    assert out['position_spread_m'][0] == pytest.approx(0.0254) and out['position_moved']


def test_summary_with_no_samples_is_honest():
    out = session_probe.summarise([], [], 1.0, [])
    assert out['glasses_urbs_per_s'] is None and not out['position_moved']


def test_position_view_without_a_segment_reads_nothing(tmp_path):
    missing = tmp_path / 'nothing-here'
    assert session_probe.PositionView(str(missing)).read() is None


def test_tools_never_name_a_device_node_or_act_on_a_process():
    for name in ('session_probe.py', 'usb_descriptors.py'):
        code = '\n'.join(line for line in (HERE / name).read_text().splitlines() if not line.lstrip().startswith('#'))
        code = re.sub(r'""".*?"""', '', code, flags=re.S)
        assert '/dev/' not in code, name
        for call in ('os.kill', 'subprocess', 'systemctl', 'ioctl'): assert call not in code, name


@pytest.fixture(scope='module')
def shim(tmp_path_factory):
    if not shutil.which('cc'): pytest.skip('no C compiler')
    out = tmp_path_factory.mktemp('shim')
    (out / 'sdk.c').write_text(FAKE_SDK)
    (out / 'caller.c').write_text(CALLER)
    subprocess.run(['cc', '-O2', '-Wall', '-Wextra', '-Werror', '-shared', '-fPIC', '-o', out / 'dof3.so', HERE / 'dof3_preload.c', '-ldl'], check=True)
    subprocess.run(['cc', '-shared', '-fPIC', '-o', out / 'libfake.so', out / 'sdk.c'], check=True)
    subprocess.run(['cc', '-o', out / 'caller', out / 'caller.c', '-L', out, '-lfake', f'-Wl,-rpath,{out}'], check=True)
    return out


def run_shim(out, device_type):
    env = {**os.environ, 'LD_PRELOAD': str(out / 'dof3.so'), 'XDG_RUNTIME_DIR': str(out)}
    return subprocess.run([out / 'caller', str(device_type)], env=env, capture_output=True, text=True, check=True).stdout


def test_shim_sets_3dof_before_initialize_on_carina(shim):
    stdout = run_shim(shim, 2)
    assert stdout.index('set_dof 0 order 0') < stdout.index('initialize order 1')
    assert 'returned 7' in stdout  # the driver still sees the real result
    assert (shim / 'xr-dof3-applied').read_text() == 'dof_type=0 result=0\n'


def test_shim_leaves_other_glasses_alone(shim):
    (shim / 'xr-dof3-applied').unlink(missing_ok=True)
    stdout = run_shim(shim, 1)
    assert 'set_dof' not in stdout and 'returned 7' in stdout
    assert not (shim / 'xr-dof3-applied').exists()


def test_bench_builds_when_opencv_is_present(tmp_path):
    if not Path('/usr/include/opencv5/opencv2/features.hpp').exists() or not shutil.which('c++'): pytest.skip('no OpenCV 5 dev files')
    binary = tmp_path / 'bench'
    libraries = ['-lopencv_core', '-lopencv_imgproc', '-lopencv_features', '-lopencv_video']
    subprocess.run(['c++', '-O1', '-std=c++20', str(HERE / 'frontend_bench.cpp'), '-o', str(binary), '-I/usr/include/opencv5', *libraries], check=True)
    assert 'corners: mean' in subprocess.run([binary, '160', '120', '40', '6'], capture_output=True, text=True, check=True).stdout
