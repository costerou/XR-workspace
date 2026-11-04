**For the orchestrator to run with Cody later. Nothing below was executed in the investigation.**

Do not run a second driver alongside an existing driver. Agree the trial with Cody and stop XR through its normal UI first. Keep the same port, cable, glasses, display content, and room conditions. Record start/end times, actual mode and driver command line. For trials that stay healthy, use matched durations (at least30minutes initially, preferably repeated60minute runs) and alternate order. Record first failures separately from recovery clusters. Existing evidence does not establish a reliable failure rate under every condition.

**Common read-only recording**

Create a separate experiment directory; keep raw captures private:

```bash
trial_dir=$(mktemp -d "$HOME/.local/state/viture-workspace/rootcause-trial.XXXXXX")
chmod 700 "$trial_dir"
date --iso-8601=seconds | tee "$trial_dir/start.txt"
hyprctl monitors all -j > "$trial_dir/monitors-before.json"
journalctl -k -b -f -o short-monotonic > "$trial_dir/kernel.log"
```

The last command runs in its own terminal; Ctrl-C ends only that reader. In another terminal, `udevadm monitor --kernel --property --subsystem-match=drm` records DRM uevents; it cannot observe every link-training event. Record its output in the same trial directory. Do not infer the root cause from polling timestamps alone.

Find the current hub and tracking-device identity before each trial:

```bash
for d in /sys/bus/usb/devices/*; do
  test -f "$d/idVendor" || continue
  v=$(cat "$d/idVendor"); p=$(cat "$d/idProduct")
  case "$v:$p" in
    1a86:8091|35ca:1104|0c45:636b)
      printf '%s %s:%s bus=' "$d" "$v" "$p"
      cat "$d/busnum"
      cat "$d/power/control" "$d/power/runtime_status"
      ;;
  esac
done
```

Do not keep relying on `usbmon5`: the glasses were on bus1 at the cutoff. Once Cody has approved capture access, a passive recorder can use the current bus:

```bash
# Only when the identity listing above confirms bus1 and this node is readable.
test -r /dev/usbmon1 && timeout --signal=INT 1800 cat /dev/usbmon1 > "$trial_dir/usbmon1.bin"
```

This opens the monitor, not the glasses. The dump includes other bus1 devices and may contain sensitive peripheral data: keep it local. It is a bounded raw capture, not an automatically rotating one; budget disk space. Do not substitute another bus merely because this one is unreadable. Access preparation, if needed, is a separate orchestrator action. Parse with48-byte API0 headers. Capture completeness does not prove absence of kernel ring loss.

**1. Tracking-only versus display-only, same120Hz**

Before starting anything, verify that normal XR shutdown has left no driver or renderer. The following command is inspection only:

```bash
pgrep -af 'xr-luma-driver|xrDriver|xr-native-spike'
```

Do not proceed with standalone tracking if another driver remains. Have the orchestrator use the normal lifecycle to stop it; do not kill arbitrary matching PIDs. Save/set the display mode using the mode script below (use120 instead of60). Run a matched plain-display baseline with no driver. Then, with XR still off, start only the installed minimal driver, using private IPC and bias paths:

```bash
driver="$HOME/.local/share/viture-workspace/bin/6744ab90ce82/xr-luma-driver"
sha256sum "$driver"
# Expected:66028d8c3fb0faa37067d7a26d7222980a2ec173fb9a2a4426134bc33f915db6
mkdir -m 700 "$trial_dir/driver-state"
timeout --signal=INT --kill-after=5s 1800 "$driver" \
  --start stream --no-rt \
  --ipc-prefix "$trial_dir/pose_" \
  --state-dir "$trial_dir/driver-state" \
  2> "$trial_dir/tracking-only.log"
```

A hub drop here means XR's renderer, controller recovery and depth engine are not required. A clean trial is inconclusive until repeated. `--no-rt` makes scheduling explicit; match it in comparisons or record the difference. This driver knows no Ultra stop command, so exiting does **not** guarantee that internal streaming stopped. A subsequent zero-stream baseline needs an independently verified reset to idle, coordinated with Cody, rather than simply closing the driver. Likewise `--start listen` means no start command, **not** no USB access; it still opens/claims/reads and may receive a pre-existing stream. It is not a clean zero-USB control.

For the complementary full-XR test, select head tracking off in the normal startup settings, keep120Hz and all desktops2D, and confirm no old tracking process remains before starting via the UI. Compare with the same XR scene and tracking enabled. Do not edit consent files or force a live session's internal state. If XR without tracking drops, focus on display/workload/host behavior; if only tracking-enabled conditions drop, focus on shared transport/firmware behavior.

**2. Fixed60Hz XR**

With XR stopped, save the automatic120Hz startup preference and disable it in the panel. Keep tracking and desktop content the same as the120Hz trial. Set60Hz *before* starting XR. First verify that the current monitor advertises the requested mode; do not force an unadvertised mode.

This writes a temporary Hyprland monitor rule, not its configuration files. It saves the original geometry/mode so it can be restored. Run from a terminal with the same `trial_dir` variable:

```bash
python - "$trial_dir" 60 <<'PY'
import json,pathlib,subprocess,sys
out=pathlib.Path(sys.argv[1]);hz=int(sys.argv[2])
monitors=json.loads(subprocess.check_output(['hyprctl','monitors','all','-j']))
m=next(x for x in monitors if x['name']=='DP-2')
mode=f'1920x1200@{hz}'
assert any(v.startswith(mode) for v in m.get('availableModes',[])), 'requested mode not advertised'
saved=out/'dp2-restore.json'
assert not saved.exists(), 'use a fresh trial directory; do not overwrite restoration data'
saved.write_text(json.dumps(m,indent=2))
pos=str(m['x'])+'x'+str(m['y'])
rule='hl.monitor({output="DP-2",mode='+json.dumps(mode)+',position='+json.dumps(pos)+',scale='+str(m['scale'])+',transform='+str(m.get('transform',0))+'})'
subprocess.run(['hyprctl','eval',rule],check=True)
PY
hyprctl monitors all -j
```

Wait for readback to show60Hz before starting XR via the UI. Keep observing mode, particularly after reload/reconnect; the startup/recovery logic can otherwise silently restore120Hz. If the automatic-mode option is unavailable, do not claim this is a60Hz trial until a reviewed test path exists.

Restore after stopping the trial through the normal UI:

```bash
python - "$trial_dir/dp2-restore.json" <<'PY'
import json,subprocess,sys
m=json.load(open(sys.argv[1]))
mode=f'{m["width"]}x{m["height"]}@{m["refreshRate"]}'
pos=f'{m["x"]}x{m["y"]}'
rule='hl.monitor({output="DP-2",mode='+json.dumps(mode)+',position='+json.dumps(pos)+',scale='+str(m['scale'])+',transform='+str(m.get('transform',0))+'})'
subprocess.run(['hyprctl','eval',rule],check=True)
PY
hyprctl monitors all -j
```

Restore the saved panel preference too. This restores the audited mode/position/scale/transform; if additional custom monitor properties are in use, preserve their rule before the test. If60Hz repeatedly succeeds and120Hz fails, the changed display/load path matters; this alone does not distinguish electrical from firmware behavior.

**3. Camera autosuspend only**

This needs an elevated shell supplied by the orchestrator; do not execute it as part of the read-only investigation. At the cutoff camera1-5.2 is `0c45:636b`. Verify that identity immediately before writing. Record the old value and set only that device on:

```bash
camera=/sys/bus/usb/devices/1-5.2
test "$(cat "$camera/idVendor"):$(cat "$camera/idProduct")" = '0c45:636b' || exit 1
cat "$camera/power/control" > "$trial_dir/camera-control-before.txt"
printf 'on\n' | sudo tee "$camera/power/control"
cat "$camera/power/control" "$camera/power/runtime_status"
```

Keep the same XR workload. One spontaneous repeat while the setting remained on refutes camera autosuspend as necessary in that trial. The setting is lost on re-enumeration, so do not count the remainder as a camera-awake trial without revalidation. No persistent rule or blanket udev trigger is needed for this first test.

Restore, after confirming the current camera identity/path:

```bash
test "$(cat "$camera/idVendor"):$(cat "$camera/idProduct")" = '0c45:636b' || exit 1
cat "$trial_dir/camera-control-before.txt" | sudo tee "$camera/power/control"
```

If the trial is improved, repeat with the saved `auto` state before attributing causality. Parent/controller autosuspend and USB2-LPM changes are lower priority because the observed parent is already kept awake and no USB2-LPM attribute is exposed. Do not write nonexistent attributes or blanket-disable unrelated daemons.

**4. More precise tracing / remaining tests**

For process attribution, prefer a reviewed trace of USBFS ioctls, opens and sysfs writes over guessing from process names. Root-only eBPF/ftrace setup depends on this kernel's available tracepoints and must be prepared separately; no unverified trace command is proposed here. `strace` can perturb a1000Hz driver and should be compared with an untraced run. Capture actual DP/USB event ordering and GPU clocks/load on a shared clock. No retrospective calculation can recover missing telemetry.

A controlled second-OS/kernel minimal-stream test separates common Linux behavior from glasses/adapter behavior better than another same-host port swap. A powered USB hub or cable test is also informative, but changes electrical and USB topology together. Definitive undervoltage attribution requires voltage measurement near the glasses/adapter at failure. Delay reconnect commands only as a separate later experiment: it may reduce secondary failures but cannot explain the initial loss during minutes of healthy streaming.
