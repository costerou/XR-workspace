# Switch animation mockups

Preview tooling for choosing how a maximized desktop hands over to its neighbour when the cursor is pushed past an edge. It
changes nothing in the renderer: it is a separate CMake project next to `spike/`, nothing links it, and
`spike/CMakeLists.txt` does not know about it.

Every clip has the same hand and the same layout: the dot goes to the right edge of a maximized XR-1, pushes through to
XR-2, then goes to the top edge and pushes through to XR-3, placed 15 degrees above XR-2. `neighbors.pick_at` has to agree
with that layout or the run stops.

## What is real

Every desktop pixel comes from the real `GlCompositor` on surfaceless EGL (`create_offscreen`). The desktops themselves are
synthetic and nothing is captured. The camera is `gl_bench`'s 46 degree diagonal, not a measured glasses FOV.

- **Options 1 and 2 are the real animation.** `switch_preview`'s `frame` command runs the real `ViewAnimator`,
  `blended_layout`, `zoomed_placements`, `edge_pan_path` and `ModeController` in follow mode with a still head. Option 1 is
  fed by the real `edge_pan.Plan` at the hand's 250 Hz, then a `pan` landing. Option 2 is the three-phase switch still in
  `ViewAnimator`, fired after 72 px of push. `focus_aim` and `pan_dot` are copied from `wayland.cpp` because they are
  private to its `Impl`.
- **Options 3 to 8 are scripted motion through the same compositor.** `make_animations.py` decides where each card goes;
  the compositor draws it. These designs do not exist in the product, so their curves are proposals. A desktop in them
  comes from the side of the push, not from its true placed direction.

The dot on a resting desktop is a mock in every option: a run shows the real cursor in the captured desktop there. It is the
renderer's free pointer here, so all the clips show the same thing.

Layers are combined outside the compositor. `draw_and_swap` takes one view, so a flat card is drawn alone, moved and scaled
by a homography in that view, and a wall of option 5 is drawn the same way with a 3 x 3 homography onto its plane. A card
covers what is behind it, its glow outside that is the brighter of the two, and the dot goes on last.

Rendering runs on llvmpipe at nice 19, so a render uses neither GPU. Nothing here starts, stops or talks to a run. The script
exits non-zero if any label or frame passes the 191/255 OLED cap, before encoding losses.

## Running it

```
cmake -S tools/switch_anim_mockups -B build/switch-mockups -G Ninja -DXR_ENABLE_HARDWARE=OFF -DXR_ENABLE_SDK=OFF
cmake --build build/switch-mockups --target switch_preview
python3 tools/switch_anim_mockups/make_animations.py
```

It writes `option-NN.mp4` (1920 x 1200, 60 fps), `option-NN.gif` (960 x 600, 30 fps), `contact-sheet.png` and `index.html`
to `~/.hermes/profiles/coder/previews/switch-anim/`. Useful flags:

- `--only 3,5`: just those options, with no contact sheet or page.
- `--stills 0.8,1.2`: only PNGs of those moments, for working on a design. Takes seconds.
- `--accent 7aa2f7 --theme-name "Tokyo Night"`: another theme colour instead of the active one.
- `--no-gif`, `--out DIR`.

It needs Python with `pycairo`, `numpy` and `Pillow`, `ffmpeg` with libx264, the JetBrainsMono Nerd Font and `spike/`'s build
dependencies.

## Adding an option

Subclass `Design` in `make_animations.py` and add it to `DESIGNS`. `commit(seg)` is where the hand hands over, in px, and
`transition(seg, t, world)` returns a `Frame`, or `None` once it has landed. `stack()` covers most designs.
