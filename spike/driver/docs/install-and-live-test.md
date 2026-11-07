# Installing the new driver, and its first live run

**Superseded for installing and for what the panel shows:** since 2026-11-06 `xr-luma-driver` is the Luma Ultra's default, the panel has no driver box, and nothing falls back. Use [docs/own-drivers-default.md](../../../docs/own-drivers-default.md). Below stays as the record of the first runs, and for its driver-only update steps.

For the orchestrator. Everything here is a live step, so each needs Cody's go-ahead. Read `live-test-plan.md`'s rules first:
stop at once on a USB disconnect, a picture change or `luma: error`, and never try command bytes that are not in this plan.

**What changes.** The panel gains a Tracking driver box under Startup options: Standard, 3DoF test, New driver. Standard stays
the default, so until someone picks another choice, XR runs exactly as it does today. The renderer and the plugin are byte for
byte the ones installed now, so there is no new plugin to accept: the install adds `xr-luma-driver` and updates the Python
controller and the panel.

| Binary | sha256 starts | Against what is installed |
|---|---|---|
| `xr-native-spike` | `6310f1270327` | Same |
| `libxr-ownership.so` | `9587ca186ac1` | Same |
| `xr-luma-driver` | `66028d8c3fb0` | New |

## Install

**A1. Set up, and look.** `SHA` must be the commit named in the hand-over.

```sh
export LIVE="$HOME/.t3/worktrees/viture-workspace/feature-placement-menu-integrate"
export BUILD="$HOME/.t3/worktrees/viture-workspace/drv-assemble/build/release"
export SHA=$(git -C "$BUILD/../.." rev-parse origin/drv-assemble)
export STATE="$HOME/.local/share/viture-workspace"
xr_status() { python3 - <<'PY'
import json, os, socket
s = socket.socket(socket.AF_UNIX); s.settimeout(3)
s.connect(os.environ['XDG_RUNTIME_DIR'] + '/viture-workspace/controller.sock')
s.sendall(b'{"schema":1,"request_id":1,"op":"status"}\n')
r = json.loads(s.makefile().readline())
print({k: r.get(k) for k in ('state', 'model', 'output', 'run_id', 'luma_available')})
print([(d['key'], d['available']) for d in r.get('tracking_drivers', [])])
PY
}
git -C "$BUILD/../.." status -sb | head -3; git -C "$BUILD/../.." log -1 --format='%h %s'
sha256sum "$BUILD/xr-native-spike" "$BUILD/libxr-ownership.so" "$BUILD/driver/xr-luma-driver"
git -C "$LIVE" status -sb | head -3
xr_status
```

Pass: the worktree is on `SHA` with nothing changed, the three hashes match the table, the live worktree is clean, and the
controller says `idle`. Anything else, such as `recovery-needed`, stop and ask.

**A2. Back up.**

```sh
TS=$(date +%Y%m%d-%H%M%S); export B="$STATE/backups/pre-luma-$TS"; mkdir -p "$B"
cp -p "$STATE/installed.json" "$B/"
cp -p "$HOME/.config/systemd/user/viture-workspace-controller.service" "$B/"
git -C "$LIVE" rev-parse HEAD > "$B/old-HEAD.txt"
git -C "$LIVE" status -sb > "$B/old-status.txt"
readlink "$HOME/.config/omarchy/plugins/cody.viture-workspace" > "$B/panel-symlink-target.txt"
git -C "$LIVE" tag "pre-luma-$TS"
ls "$B"; echo "$B"
```

**A3. Install, in one step.** Run units import Python from the live worktree, so a checkout must never land under a run that is
starting: this gate has to sit in the same command as the checkout. Nothing here starts XR.

```sh
test -z "$(systemctl --user list-units 'viture-xr-*' --no-legend)" \
  && xr_status | grep -q "'state': 'idle'" \
  && ! pgrep -x xrDriver && ! pgrep -x xr-luma-driver \
  && git -C "$LIVE" fetch -q origin \
  && git -C "$LIVE" checkout -q --detach "$SHA" \
  && (cd "$LIVE" && python3 -m tools.xr_controller install --build "$BUILD") \
  && systemctl --user restart viture-workspace-controller.service
echo "install=$?"
```

Pass: `install=0`. A non-zero status before the checkout changed nothing. After it, go to Rollback.

**A4. Panel and check.**

```sh
omarchy-restart-shell     # the panel's QML. It refuses while the session is locked
sleep 3; xr_status
python3 -c "import json,os; d=json.load(open(os.path.expanduser('~/.local/share/viture-workspace/installed.json'))); print({k:v['sha256'][:12] for k,v in d.items()})"
```

Pass: `state` is `idle`, `luma_available` is `True`, the drivers read `[('xrdriver', True), ('3dof', True), ('luma', True)]`, and the
pins match the table. Then Cody opens the panel, opens Startup options and sees Tracking driver offering all three.

## Find the start that works

With the install done, run stage G of `live-test-plan.md`, on the pinned binary. **XR stays off** and the glasses lie still on
the desk. Do its setup block, its stages P and D, and then this before stage G:

```sh
export BIN=$(python3 -c "import json,os; print(json.load(open(os.path.expanduser('~/.local/share/viture-workspace/installed.json')))['luma_driver']['path'])")
"$BIN" --help | head -1
```

Stage G's result decides the flags. Only if plain `--start stream` did not hold, save what did, as one line. The panel's runs
read this file. `--start listen` is refused here, because a run that sends nothing cannot start.

```sh
mkdir -p "$HOME/.config/viture-workspace"
printf -- '--start prepared --read-events\n' > "$HOME/.config/viture-workspace/luma-driver-flags"   # for example
```

If stage G found nothing that streams, stop here. Standard is still the default and nothing is worse than before.

## First real session

**L1. Pick it.** Cody chooses Startup options, Tracking driver, New driver. It stays chosen until changed.

**L2. Start XR** from the panel, as Cody always does, with the glasses still for a few seconds. The confirm box must say:
*Head tracking via the XR Luma driver, network blocked. If it does not calibrate, XRLinuxDriver takes over, also network blocked.*

Watch from a terminal, which only reads:

```sh
tail -n 20 -F "$HOME/.local/state/viture-workspace/luma-driver.log"
```

Pass: `luma: calibrated` within about 3 s, the picture tracks the head, and `pgrep -x xr-luma-driver` prints a pid.

If the driver does not calibrate within 30 s, the run hands over by itself: a notification says why, `xrDriver` runs instead, and
the session carries on. Cody loses nothing but about 30 s, and the choice stays for the next start. If that happens, record the
notification and the log, and start nothing else.

**L3. Hold.** Cody works normally. Run the soak gate, stage X step X2, from the start of each session. Any drop stops the series.

**L4. Heading.** Cody says how the desktops' slow turn feels after 20 minutes. Expect some: see `capture-findings.md`.

**L5. Stop XR** from the panel. Like the standard driver, the new one stays running between runs, so the next start finds it
ready and calibrated. Then:

```sh
pgrep -x xr-luma-driver
tail -n 6 "$HOME/.local/state/viture-workspace/luma-driver.log"
cat "$HOME/.local/state/viture-workspace/luma-gyro-bias"
```

Pass: a pid; the newest `luma: stats` shows `commands=1` per connection and `bad_packets=0`; and three numbers in the bias file
once the driver has run about 6 minutes. It is written every 5 minutes, once fusion trusts its estimate.

To see the saved bias work, stop the driver and start XR again: `python3 -m tools.native_spike tracking stop`, then Start in the
panel. Pass: `luma: bias restored`, then `luma: calibrated` almost at once.

### Rehearsing the fallback, if Cody wants to see it

This proves the handover with no Luma traffic on the wire at all, because a bad flags file fails the start before the driver runs:

```sh
printf -- '--rehearsal\n' > "$HOME/.config/viture-workspace/luma-driver-flags"
```

Cody starts XR on New driver. Pass: a notification names the unknown flag, `pgrep -x xrDriver` shows the standard driver, and XR
runs normally. Then stop XR and remove the file, or restore the flags from stage G:

```sh
rm "$HOME/.config/viture-workspace/luma-driver-flags"
```

### The 3DoF test

Unchanged from `docs/tracking-3dof-ab.md`: pick 3DoF test in the same box. It never falls back, on purpose: a failure stops the
start and says why, because a test run that quietly used the standard driver would give a false result.

## Updating just the driver

For a build that changes only `xr-luma-driver`. **Never during a run:** XR must be stopped from the panel first. The driver stays
running between runs, so stop it here, or the gate refuses. With `LIVE`, `BUILD`, `SHA`, `STATE` and `xr_status` set as in A1, and
`installed.json` copied aside as in A2:

```sh
test -z "$(systemctl --user list-units 'viture-xr-*' --no-legend)" \
  && xr_status | grep -q "'state': 'idle'" \
  && (cd "$LIVE" && python3 -m tools.native_spike tracking stop) \
  && ! pgrep -x xrDriver && ! pgrep -x xr-luma-driver \
  && git -C "$LIVE" fetch -q origin \
  && git -C "$LIVE" checkout -q --detach "$SHA" \
  && (cd "$LIVE" && python3 -m tools.xr_controller install --build "$BUILD") \
  && systemctl --user restart viture-workspace-controller.service
echo "install=$?"; sleep 3; xr_status
```

Pass: `install=0`, `idle`, and `luma` still available. The next XR start runs the new pin. The panel needs no restart unless the
hand-over says its QML changed.

## Going back

**Just stop using it.** Choose Standard in the same box. Nothing else changes, and this is enough for almost anything.

**Full rollback**, XR off. `B` is the folder from A2.

```sh
test -z "$(systemctl --user list-units 'viture-xr-*' --no-legend)" \
  && xr_status | grep -q "'state': 'idle'" \
  && git -C "$LIVE" checkout -q --detach "$(cat "$B/old-HEAD.txt")" \
  && cp -p "$B/installed.json" "$STATE/installed.json" \
  && cp -p "$B/viture-workspace-controller.service" "$HOME/.config/systemd/user/" \
  && systemctl --user daemon-reload \
  && systemctl --user restart viture-workspace-controller.service
echo "rollback=$?"
omarchy-restart-shell; sleep 3; xr_status
```

Pass: `rollback=0` and `idle`. The old controller refuses an `installed.json` that names the driver, so that file must go back
with the code. A saved `tracking_driver` of `luma` or `3dof` in the profile is ignored by older code, which then runs the
standard driver.
