# AI depth engine

The first half of the "make anything 3D" filter: every XR desktop in, a smooth depth map per desktop out, fast enough to
draw each eye from. This document covers the engine only. Drawing the two eyes from the map, the 3D button and the
glasses' side-by-side mode are separate pieces of work.

Status: built and measured off screen on the RX 7800 XT. It has never run in the real renderer, on a real desktop or in
the glasses.

## What was chosen

**Depth Anything V2 Small (Apache-2.0), half precision, PyTorch on ROCm, in its own process.** No sudo: ROCm comes
inside PyTorch's pip package and only needs `/dev/kfd`, which this machine opens to everyone. ncnn on Vulkan is the
fallback: half the speed, but it is one small library.

| Target | Result |
| --- | --- |
| At least 60 fps, one 1920x1200 desktop | 98 fps at 518x322, 8.4 ms from frame to map |
| At least 30 fps, three desktops | 53 fps each at 518x322, 77 fps each at 420x266 |
| At least 30 fps, four desktops | 43 fps each at 518x322, 62 fps each at 420x266 |
| Must not disturb a 120 Hz renderer | At most 1 stand-in frame in 720 over budget, 0 in most runs; over a minute, 1 in 7200 with one desktop and 7 in 7194 with four |

## Models

Speed and looks were measured for the two models marked. The rest were read about and not run. Licences are from each
project's own repository or model card on 2026-11-03.

| Model | Licence | Verdict |
| --- | --- | --- |
| **Depth Anything V2 Small** | Apache-2.0, code and weights | **Chosen.** Sharp edges; windows lift off the wallpaper as clean panels |
| Depth Anything V2 Base, Large | **CC-BY-NC-4.0: non-commercial** | Do not use. Same repository, different licence |
| MiDaS v2.1 Small | MIT | Rejected on looks: soft, and it read the sample desktop close to backwards. Also 64 ms a frame in ONNX Runtime WebGPU |
| Depth Anything 3 Small | Apache-2.0 on its model card | Not run. A multi-view model, with no real-time figures found |
| Video Depth Anything Small | Apache-2.0 on its model card | Not run. Worth a look if flicker on real video is a problem, since it is built for that |
| Distill-Any-Depth Small | MIT on a third-party conversion; upstream not checked | Not run |
| MoGe-2 ViT-S | MIT on its model card | Not run |
| Apple Depth Pro | `apple-amlr`: research licence | Do not use |

Input size matters more than anything else. At 252 px the sample desktop's windows vanish into the wallpaper. At 378 px
and above they are sharp. Keeping the desktop's 16:10 shape helps too: 518x322 has 38% fewer image patches than
518x518 for the same detail across. So there are two presets, both meeting the design contract's 256 px minimum:

- `518x322` for one desktop.
- `420x266` for three or four. 378x238 would be faster but is under the 256 px minimum.

## Runtimes

518x322, Depth Anything V2 Small, flat out, beside the stand-in renderer.

| Runtime | Depth rate | Stand-in frames over 8.33 ms | Install |
| --- | --- | --- | --- |
| **PyTorch ROCm 6.4, half precision** | 115 to 152 fps | **0 in most runs** | 15 GB, pip, no sudo |
| ncnn Vulkan, half precision | 55 to 63 fps | 0 or 1 in 720 through the engine; 1.4% in one flat-out raw run | Small pip package, plus a model conversion |
| ONNX Runtime 1.31 WebGPU | 55 to 62 fps | **5.8%, 15.8%, 18.4%, 37.5%** across four runs | Small pip package |
| PyTorch ROCm, full precision | 40 fps | Not run | The same 15 GB |

- ONNX Runtime is out for the misses alone, whatever its speed: WebGPU (Dawn) runs on the graphics queue, the same one
  the renderer draws on, so a 16 ms inference blocks whole renderer frames. ROCm and ncnn use compute queues, which
  run alongside it.
- ROCm half precision is four times faster than full, an unusually large gap. It was checked with a forced readback, and
  its output is within 0.3% of ONNX Runtime on the CPU, so the speed is real.
- A hand-written Vulkan compute port was not attempted: ROCm already beats every target.
- MIGraphX and the system ROCm packages were not needed. None of ROCm, ONNX Runtime or ncnn is installed system-wide.

## How it fits together

```
renderer                                        depth engine (its own process)
  desktop texture                                 newest frame of each desktop
  -> DepthFeed: shrink on the GPU                 -> skip it if the picture has not changed
  -> shared memory, input side  ------------->    -> Depth Anything V2 Small
                                                  -> temporal filter
  GL_R16F texture  <-------------  output side <- shared memory
```

- **`spike/include/xr/depth.hpp`** is the contract: `DepthFrame`, `depth_input_size()` and `DepthShm`, the shared file.
  `tools/depth_engine/shm.py` is its Python twin, and a test compares the two byte for byte.
- **The renderer never waits.** Each direction is a seqlock over three slots. A slow engine means an older map, never a
  dropped renderer frame. A test rewrites frames during reads and checks none comes out torn.
- **A crash stays in the engine.** It writes a heartbeat. `engine_alive()` going false means the renderer draws flat.
- **`spike/src/depth_feed.*`** shrinks a dmabuf or texture on the GPU and reads it back without blocking. A desktop is
  usually a tiled dmabuf that only the GPU can read, so this is the dmabuf input. What crosses to the engine is then
  0.5 MB, so shared memory costs about 0.01 ms and a zero-copy path would save nothing.
- **Still desktops cost nothing.** A checksum skips frames with the same picture, and the skipped frames do no GPU work.
- **`tools/depth_engine/scheduler.py`** runs the focused desktop at up to 60 Hz and the others at up to 30, most overdue
  first, and batches desktops into one model call.

### Flicker

The model's output has no fixed scale, and it changes from frame to frame. `temporal.py` handles this in two stages:

1. Each new map is fitted to the smoothed one, using only pixels whose picture did not change. That removes the
   whole-map jump exactly.
2. Each pixel is eased over about 120 ms, unless its picture changed or its depth moved by more than noise, when it
   follows at once. A moving window leaves no ghost; a test checks this.

A scene cut (over 55% of the picture changing at once) resets the filter and sets `kDepthSceneCut`.

Real model, still picture with camera-like noise, how much each pixel wanders from frame to frame:

| Picture | Before | After | Better by |
| --- | --- | --- | --- |
| Road scene, 518x322 | 0.17% of the depth range | 0.03% | 5.0 times |
| Road scene, 420x266 | 0.18% | 0.05% | 3.6 times |
| Sample desktop, 518x322 | 2.3% | 0.72% | 3.2 times |
| Sample desktop, 420x266 | 2.8% | 0.68% | 4.1 times |

Text-heavy desktops wander most. In a real desktop those noisy frames would be skipped as unchanged, since a
compositor's pixels do not carry camera noise. The figure is the worst case for video of a desktop.

## Measurements

Real engine and shared memory, with a separate process publishing changing frames at 120 Hz per desktop, so 120 fps is
the ceiling. Median of three 6-second runs. Lag is frame capture to finished map. Measured 2026-11-04 on a shared
machine, waiting for the GPU to fall below 30% busy before each run. Power is the whole card: 29 W idle, 43 W with
only the stand-in renderer running. VRAM is the engine process alone. "Stand-in" means it ran alongside.

### One desktop, flat out

| Input | ROCm fps | Lag p50 / p99 ms | Power W | VRAM MB | ncnn fps | ncnn lag p50 ms | ncnn VRAM MB |
| --- | --- | --- | --- | --- | --- | --- | --- |
| 252x252 | 119 (ceiling) | 5.4 / 7.1 | 96 | 540 | 107 | 13.5 | 110 |
| 378x378 | 108 | 7.9 / 12.4 | 149 | 558 | 58 | 21.3 | 149 |
| 518x518 | 88 | 15.6 / 20.0 | 197 | 627 | 25 | 44.6 | 219 |
| 252x154 | 119 (ceiling) | 5.2 / 8.2 | 93 | 512 | | | |
| 420x266 | 119 (ceiling) | 6.5 / 7.6 | 122 | 546 | 69 | 18.6 | 126 |
| 518x322 | 98 | 8.4 / 14.5 | 160 | 560 | 55 | 22.2 | 156 |

### Several desktops, ROCm

| Setup | fps per desktop | Lag p50 / p99 ms | Power W | VRAM MB |
| --- | --- | --- | --- | --- |
| 3 at 420x266, flat out | 77 | 16.2 / 21.3 | 187 | 631 |
| 4 at 420x266, flat out | 62 | 18.8 / 23.9 | 185 | 621 |
| 3 at 518x322, flat out | 53 | 21.7 / 27.3 | 188 | 635 |
| 4 at 518x322, flat out | 43 | 25.6 / 31.4 | 188 | 683 |
| 1 at 518x322, capped at 60, stand-in, 60 s | 59 | 13.1 / 18.1 | 130 | 560 |
| 4 at 420x266, capped at 60/30, stand-in, 60 s | 52 focused, 26 others | 14.7 / 26.2 | 152 | 709 |

The last row predates the scheduler fix that lets a nearly due desktop join a leaving batch; see "Not yet known".
The ncnn fallback manages 17 fps each on four desktops at 420x266, so it is a fallback for one desktop only.

CPU side: about 1 ms a frame for the filter, checksum and conversions. The engine process holds 3.5 GB of ordinary memory
with ROCm (mostly PyTorch) and 0.12 GB with ncnn, so it should start when 3D is first switched on and stop when it goes
off. Two one-minute soaks left no kernel GPU errors, steady memory and the card at 54 C.

The stand-in renderer is `tools/depth_engine/render_proxy.cpp`: a full-screen pass of about 1.3 ms on the same GPU, 120
times a second. It is lighter than a real run and draws nothing to any display, so the contention figures show the
queues do not fight, and do not show what happens with a game holding the GPU at 100%.

## Install (no sudo)

```sh
tools/depth_engine/setup.sh                   # PyTorch with ROCm, weights, model code
tools/depth_engine/setup.sh --ncnn 420x266    # optional: convert for the ncnn fallback, once per size
```

Everything lands in `~/.cache/viture-depth`: 15 GB for ROCm, about 5 GB more for the optional converter. Versions,
commits and weight checksums are pinned in the script. Nothing leaves the machine at run time. No `pacman` step is
needed.

## Running it

```sh
P=~/.cache/viture-depth/venv/bin/python
$P -m tools.depth_engine file picture.jpg depth.png                 # One picture in, one map out
$P -m tools.depth_engine serve --shm /dev/shm/xr-depth --long 420   # Serve a file the renderer made
$P -m tools.depth_engine bench --backend rocm --size 518x322 --channels 1 --proxy
$P -m tools.depth_engine bench --backend rocm --size 518x322 --flicker
```

`bench` refuses to run while an XR session is active.

## Using it from the renderer

1. On the first 3D toggle, make the file with `DepthShm::create()` and start the engine. Channels start switched off.
2. `set_control(channel, kDepthEnabled)` per desktop in 3D, plus `kDepthFocused` on the one being looked at.
3. Per captured frame: `DepthFeed::begin_external(texture)`, then `collect()` on a later frame, then
   `publish(Side::Input, ...)`. `source_seq` starts at 1 and `source_ns` is CLOCK_MONOTONIC.
4. Per render: `fetch(Side::Output, channel, storage, last_seq)`. Upload with `glTexSubImage2D(..., GL_RED, GL_HALF_FLOAT,
   ...)` into a `GL_R16F` texture with linear filtering. 0 is far, 1 is near, row 0 is the top. `kDepthFlat` means nothing
   to separate.
5. If `engine_alive()` is false, draw that desktop flat.

## Tests

```sh
python -m unittest discover -s tests/depth_engine -v     # No GPU needed
ctest --test-dir build/release -R depth                  # The contract; the feed runs on the iGPU
XR_DEPTH_GPU_TESTS=1 python -m unittest discover -s tests/depth_engine -p 'test_real_model.py' -v
```

The last is the sample-image test: the real model on a road scene and a desktop drawn by `sample.py`, so the repo carries
no image licences. It checks the near block is near, the far block and sky are far, each window stands in front of the
wallpaper, blank frames give finite numbers, and ncnn and ROCm draw the same map. It uses the GPU, so do not run it
during a glasses session.

## Not yet known

- **Real content.** Everything here used two synthetic pictures, three stock photos for looks, and sliding frames for
  load. Real apps, video and games have not been through it.
- **A game at full GPU load.** The engine shares the shader hardware with whatever is running. Expect lag to rise. How
  far is unmeasured, and matters most for the "games" part of the feature.
- **The default 60/30 rates on four desktops, on the real GPU.** The fix that lets the other desktops reach 30 passed
  its unit test, but XR started before it could be measured. Flat out they do 62 fps, so the capacity is there.
- **Tiled dmabufs.** `DepthFeed` was tested with a linear dmabuf on the iGPU. The renderer's real ones are tiled and go
  through the same EGL import, but that exact case has not run.
- **Long sessions.** The longest soak is one minute.
- **Engine start time.** A few seconds for PyTorch and the model; not measured properly.
- **A GPU fault in ROCm.** A hang in compute could reset the card under the renderer. None happened in these runs.
