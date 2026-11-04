# Stereo renderer (global immersive 3D)

The renderer half of global immersive 3D: it draws the whole XR view side by side, one 1920x1200 image per eye in a 3840x1200
buffer, and turns each desktop's capture plus a depth map into two views. Status: **offline tests only.** Nothing here has run
in the glasses, no depth engine feeds it yet, and nothing in the supervisor turns it on.

The code: `spike/include/xr/depth.hpp` (`DepthInput`, what the depth engine hands over), `spike/include/xr/stereo.hpp` and
`spike/src/stereo.cpp` (the maths, and a CPU mirror of each GL pass), `spike/src/stereo_gl.*` (the GL passes),
`GlCompositor::set_stereo` / `set_depth` in `spike/src/gl.*`.

## How a frame is drawn

`resize(1920, 1200, 3840, 1200)` keeps the canvas, so no layout, hit test or card changes. With `set_stereo({enabled = true})`
`draw_and_swap` runs the frame once per eye inside `eye_viewports()` (the split VR video already uses). A buffer narrower than 3:1
stays 2D, so turning 3D on before the glasses have switched modes draws a normal frame.

| Thing | Where it sits in depth |
| --- | --- |
| Desktop's plane | Its real distance. `XR_PROJECT` offsets each eye, converged on the canvas plane, so a desktop at distance 1 has no disparity and one further away sits behind the screen. |
| Desktop's content | The warp below, on top of the plane. |
| Border, glow, pill, level bar, corner caps, open panel, drop frame | On their desktop's plane, not warped. At distance 1 that is the screen plane. |
| Help card, key bind panel, pop-ups, video card | Head-locked, the same picture in each eye. |
| Free pointer | The disparity of the surface under it, plane plus depth, and one pixel in front, so it never sinks into what it points at. |
| Background | At infinity, cut to the far limit. |

Each eye's clip-space x is `(q.x + side b (1 + q.z)) / half_w`. In 2D `b` is 0 and no extra uniform call is made, so the frame is
the 2D frame to the bit, and `stereo_render` checks that after 3D has been on and off.

## The warp

Depth frames arrive at 30 Hz or so. Per depth frame and desktop, two small passes at the depth map's resolution:

1. **Text mask.** Eight taps across three rows of each texel count how often the brightness turns back by more than 0.1. Strokes
   go up and down several times in a texel; a photo's edge goes one way, once.
2. **Guide map** (R16F, two textures in turn). Raw depth through `lo..hi` to nearness; nearness through strength and convergence
   to `v`, -1..1, with a soft knee near the limits; text reduces `v` to `text_keep` of itself; `v` tapers to 0 over 3% at each edge of
   the desktop, so nothing crosses its frame; a temporal filter with a 60 ms time constant lets go when `v` jumps by more than 0.35
   (a cut), so a new scene does not smear. NaN and infinity come out as 0: flat.

Per displayed frame, one fragment shader on the desktop's own mesh finds which source pixel lands on each eye pixel: 12 steps from
the near limit downwards (the first crossing is the front-most surface), then 3 bisections. `dFdx(uv)` gives the direction, so it
follows the eye's horizontal through roll, curvature and size. That keeps the cost to the pixels on screen, whatever the desktops'
sizes, and resamples the desktop once, so text is as sharp as in 2D.

**Disocclusion fill.** Where the surface stretches by more than about 2x along the march, the pixel is in a hole behind a near edge.
It fades to the background beside the hole, a margin of 1.5 depth texels back from the edge (depth is softer than colour, so the
edge's own mixed pixels are skipped), mirrored about that point so it does not streak, averaged over three rows. The fill only reads
the background side.

Colour is only ever sampled, so no pixel gets brighter than the source. Nothing can exceed the OLED cap that 2D does not.

## Controls and comfort

`StereoParams`: `strength` (0..2), `convergence` (the nearness that stays on the screen plane), `near_deg`, `far_deg`, `text_keep`. Each
goes through `stereo_clamped`, so a saved profile or wire value cannot pass the ceilings of 1.5 degrees crossed and 1.0 degree
uncrossed. Defaults are 1.0 and 0.75 degrees. A virtual image at 4 m leaves about 0.93 degrees before the eyes must diverge, and that
4 m is an estimate.

The limits are for the sum: the plane's disparity and the warp's together stay inside them (`stereo_budget`), and the baseline shrinks
if a desktop would otherwise go past them (`stereo_safe_baseline`). A small desktop gets proportionally less depth, down to 35%
(`stereo_tile_scale`). On, off and a depth source that stopped for 500 ms all ease over 300 ms.

## Depth input

`GlCompositor::set_depth(source, DepthInput)`, with either shared-memory R32F pixels or a GL texture name (an imported dmabuf, R16F
or R32F). Values are nearness: larger is nearer, and `lo` / `hi` give the frame's range. Any resolution at least 256 px tall works.
`spike/tests/fake_depth.hpp` is the stand-in the tests use.

## Evidence

`stereo` (maths and mirrors, no GL) and `stereo_render` (surfaceless EGL, the real `GlCompositor`). `stereo_render` compares the guide
pass and the warp with their CPU mirrors, three golden frames in `spike/tests/golden` (`stereo_render_test --write-goldens DIR`
re-creates them; look at them before committing), disparity measured from the pixels by block matching, and the whole view.

| Check | Result on the RX 7800 XT |
| --- | --- |
| Frame, 3840x1200, every eye pixel warped, three desktops of depth at 30 Hz | median 0.41 ms, p99 1.24 ms (limit 8.33 ms) |
| Depth update of three desktops at 518x324 | 0.44 ms |
| The same on the Radeon 610M integrated GPU | median 10.71 ms, p99 13.49 ms: too slow for 120 Hz, fine for 60 |
| Harshest settings, rough depth | 30.3 / 20.2 px measured, limits 30.3 / 20.2 px |
| Flat depth | 10.00 and -7.76 px, expected 10.11 and -7.58 px |
| Each eye against the 2D frame, no depth or baseline | at most 1/255 away, the dot apart |
| Non-finite values in the guide map from NaN and infinite depth | 0 |
| Brightest output pixel | 230, source 230 |

The frame time is GPU time with `glFinish` on a synthetic scene, with no depth engine sharing the GPU. Deliberately breaking the
shader (fill from the foreground, no text safety, double disparity) fails 2, 5 and 9 checks.

## Not known yet

- **Refresh rate.** VITURE's SDK headers (`viture_protocol_public.h` in XRLinuxDriver) list 3840x1200 at 60 and 90 Hz only, while
  2D has 120 Hz. Tests hold the renderer to 120 Hz, but the glasses look limited to 90 Hz in 3D, a hard change for a run built on 120.
- **Mode switch.** The 2D EDID lists no 3840x1200 mode, and the headers list it as a display-mode command, so this looks like a USB
  command, then a new mode on DisplayPort. The modeset and the surface resize are not done here.
- **How it looks.** Every comfort number comes from published rules of thumb, not from anyone wearing the glasses. Strength,
  convergence, the text threshold and the 4 m figure all need tuning there.
- **Real depth.** The fake maps are clean. A network's maps flicker and miss thin things, the warp can miss a surface thinner than a
  march step (about 3.5 px at the defaults), and depth 33 ms behind the colour will show as halos round fast motion.
- **Text mask.** Tested on synthetic text. Fine real textures (grass, fabric) will read as text and flatten, which is the safe way to
  be wrong.
- **VR video.** The picture and card are right in SBS, but a background behind a 180 degree video stays on the screen plane.
