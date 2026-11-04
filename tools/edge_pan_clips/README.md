# Edge pan clips and metrics

The edge pan (`tools/native_spike/edge_pan.py`) measured and drawn for any layout. It changes nothing in the renderer: it is a separate
CMake project next to `spike/`, nothing links it, and `spike/CMakeLists.txt` knows only about the no-GL trace.

- `motion.py`: the layouts, the hand, and what a trace says about the motion. The hand is the one in option 1 of the switch mockups
  (`switch-anim-mockups`): the real `edge_pan.Plan` at 250 Hz, then a landing. Cody's saved layout is kept here as a fixture, so
  nothing reads his config. Its 52 degree diagonal is the Luma Ultra's published FOV: the driver's own value was not recorded anywhere.
- `spike/tests/edge_pan_trace.cpp` and `edge_pan_run.hpp`: what each frame draws, as JSON, with no GL. `focus_aim` and `pan_dot` are copied
  from `wayland.cpp`, because they are private to its `Impl`.
- `pan_clip.cpp`: the same frames from the real `GlCompositor` on surfaceless EGL.
- `make_clips.py`: renders every layout, then the side-by-sides, the plot and the tables.

ctest `edge_pan_motion` (`spike/tests/edge_pan_motion_test.py`) is the same trace across a grid of layouts, and is what fails on a bounce.

## What is real

Every desktop pixel comes from the real compositor and every moving part from the real animation code, with a still head. The desktops
are synthetic and nothing is captured. Rendering runs on llvmpipe at nice 19, so it uses neither GPU, and nothing here starts, stops
or talks to a run. A clip is not the glasses: it says what is drawn, not how it feels.

## Running it

```
cmake -S spike -B build/release -G Ninja -DCMAKE_BUILD_TYPE=Release -DXR_ENABLE_HARDWARE=OFF -DXR_ENABLE_SDK=OFF
cmake --build build/release --target edge_pan_trace
cmake -S tools/edge_pan_clips -B build/clips -G Ninja -DXR_ENABLE_HARDWARE=OFF -DXR_ENABLE_SDK=OFF
cmake --build build/clips --target pan_clip
nice -n 19 python3 tools/edge_pan_clips/make_clips.py --tag after
nice -n 19 python3 tools/edge_pan_clips/make_clips.py --compare
```

It writes to `~/.hermes/profiles/coder/previews/switch-anim/`: `seamless-<tag>-<layout>.mp4` and `.json`, `seamless-compare-<layout>.mp4` (before on
the left), `seamless-metrics.png`, `seamless-metrics.md`, `seamless-frames.csv` and `seamless.html`, which shows them all. `--only cody,row` narrows it.

"Before" is commit a1f1884: the measuring tools on the renderer and planner as they were. Build it from an export of that commit and
pass its binaries with `--tag before --pan-clip PATH --trace PATH`. The later measuring code takes small changes to read it: that
commit's `pan` frames have no `way`, its `plan` takes no rolls and its trace has no `own_roll`.

It needs Python with `pycairo`, `numpy`, `Pillow` and `matplotlib`, `ffmpeg` with libx264, the JetBrainsMono Nerd Font and `spike/`'s build
dependencies.
