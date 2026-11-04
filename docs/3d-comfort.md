# 3D comfort and quality

Research and recommended settings for the AI 3D filter: how other products do it, what keeps it comfortable, and what offline tests
on real depth maps showed. The tool behind the numbers is [tools/stereo_eval](../tools/stereo_eval/README.md).

Status: **offline only.** The depth is real (Depth Anything V2 Small on the CPU), but the conversion is a Python reference of the
recommended pipeline and not the renderer's shaders. Nobody has seen any of this in the glasses. The numbers are engineering scores,
not measurements of people's comfort. Preview pictures are in `~/.hermes/profiles/coder/previews/3d-samples/`.

## The short version

- **Keep the depth small.** The presets use 18′ to 50′ of total depth (arcminutes; 60′ is one degree). The comfort fence at this
  focal distance is about 200′. Halos grow quickly with strength: 1.5 times the video preset scores more than double.
- **Put text on the screen plane.** Fractions of a pixel of shift blur text, so text gets no shift at all. Any leftover parallax costs
  sharpness. The `text_keep` default of 0.15 in `xr/stereo.hpp` should be 0 on desktops.
- **Keep black bars away from the model.** It reads them as the nearest thing on screen, which pushes the actors behind the plane.
- **Pin game HUDs** to the plane by spotting what stays still while the world moves.
- **Share the warp between both eyes**, fill holes from the background, and taper the depth to zero at the frame's edges.
- **A 266 px tall depth map is enough** (252 scored the same as 518, and 140 did not). It takes about a fifth of the time of 518.

## How other products do it

Vendors publish little, so most of what follows is their own marketing, and none of it is independently tested.

| Product | What is known | What it says for us |
| --- | --- | --- |
| [INAIR Pod](https://inairspace.com/blogs/blogs/world-s-first-inair-spatial-computer-brings-real-time-2d-to-3d-conversion-for-all-apps) | "AI Real-Time Depth Engine" on the Pod, a Qualcomm 7-series Android box. Applies to all Android apps. One click; depth map, then stereo | The model is a small one running on a phone-class chip, so a small model on an RX 7800 XT has plenty of room |
| [VITURE Immersive 3D](https://www.viture.com/academy/xr_glasses/immersive_3d_windows) | Windows, Mac and SpaceWalker. Three depth levels: Enhanced, Standard, Soft. Movie Mode and Game Mode. Quality and Performance render modes. 1080p at 35 FPS on an RTX 4060 Laptop in Quality Mode; NVIDIA RTX recommended on Windows; a [claimed 30 ms](https://viture.com/blog/a-worlds-first-turn-2d-into-magical-3d-in-real-time) per frame | Soft / Standard / Enhanced and per-content modes are what users of these glasses already know. Their depth updates well under the display rate, so ours need not hit 120 Hz |
| [Meta Quest](https://www.uploadvr.com/quest-instagram-app-now-ai-converts-videos-to-3d-too/) | "AI view synthesis" in Instagram on Quest, photos then video. No switch: on by default. UploadVR's one quirk: text artifacts in low-quality videos | Text is the known weak spot even at Meta |
| [Pico 4 Ultra](https://mixed-news.com/en/pico-4-ultra-is-getting-2d-to-3d-photo-conversion-in-next-update/) | OS 5.13 converts gallery photos, in batches. Nothing found on live video | Offline, so no help on latency |
| PC tools ([Stream to 3D](https://iwantaholodeck.itch.io/stream-to-3d-standalone), [VisionDepth3D](https://visiondepth3d.itch.io/visiondepth3d)) | Depth model plus a warp over a screen capture | Proof that a capture-based design works on a PC GPU; no comfort data |

One hands-on [comparison of VITURE and XREAL](https://www.zhihu.com/tardis/jm/art/2031060274132808750), seen only as a search summary
because the page would not load, reported wavy straight edges, deformed text and jitter on scrolling text on the weaker on-device
pipeline. Those are exactly the three faults measured below.

## What makes it comfortable

**Disparity budget.** Your eyes aim at a depth set by the disparity while they focus on the optics' fixed plane. The further those two
disagree, the sooner it tires. Limits, on a focal distance of 3 m and 63 mm between the eyes (40.5 px per degree, so 1′ is 0.68 px, and
1 % of the width is 27′):

| Limit | In front | Behind | Source |
| --- | --- | --- | --- |
| Zone of comfort, outer fence | 129′ | 72′ (eyes parallel) | [Shibata et al. 2011](https://pmc.ncbi.nlm.nih.gov/articles/PMC3369815), from six sampled points; the authors call it an estimate. At 4 m: 129′ / 54′ |
| Rule of thumb | 60′ | 60′ | ["One degree of screen disparity"](https://arxiv.org/abs/1703.04574) |
| 3D TV broadcast, most shots | 27′ (1 %) | 54′ (2 %) | [BSkyB technical spec](https://www.cinematography.net/3D/BSkyB_Tech_Spec_COMM_Content_ver1.0.pdf). That spec banned automated 2D to 3D outright |
| 3D TV, short moments | 67′ (2.5 %) | 108′ (4 %) | Same |
| Our video preset | 14′ | 36′ | Below |

The far side has a hard stop: past 72′ the eyes would have to point outwards, which they never do naturally. The spec above is for a
46 inch TV, so the percentages are a guide only. Shibata also found behind-the-screen content the less comfortable side at long
distances, while cinema practice favours it because things in front of the frame get cut by it. The presets side with cinema and stay
at half of Shibata's far fence. That choice should be checked in the glasses.

**Convergence.** The screen plane should sit in the scene, not at the very front or back of it. Then most of the picture is behind the
frame, like looking through a window, and a little stands in front. The presets set it by percentile, so it follows the scene.

**Window violations.** Something in front of the plane and cut by the frame's edge makes the eyes disagree about what is in front.
Tapering the disparity to zero at the edges removes it, and keeps a desktop's border, level bar and pill on the plane.

**Temporal stability.** Depth that shimmers is more noticeable than depth that is slightly wrong. A monocular model gives a fresh guess
each frame, so it needs smoothing, and the range needs slower smoothing than the pixels, or the whole picture pumps in and out.

**Edge halos.** When the two eyes look round a foreground edge, each sees background nothing in the source shows. That hole gets
filled, and a poor fill or a depth edge that misses the real edge shows as a shimmering outline.

**Text.** Text has the highest contrast and sharpest edges on a desktop, and we read it with a steady eye. It is the first thing to show
any fault. Shifted by half a pixel, bilinear sampling blurs each eye. Worse, the model invents depth on a page: on a full page of text it
produced 71′ of false depth with the naive mapping.

**Brightness.** Every output pixel is a mix or copy of source pixels, so 3D cannot raise the OLED's peak. The tool checks this.

## What the tests showed

Sequences: a scrolling desktop (drawn, with a real film still in a window), 30 frames; a real game screenshot with a simulated pan, the
world sliding under a fixed HUD; 30 frames of a real film shot with letterbox bars. "Naive" means a straight mapping to 40′ / 60′, no
filtering, the way a first version would be written.

| | Near / far | Border violations | Halo | Text sharpness, worst tenth | Text wobble | Still flicker | Jitter |
| --- | --- | --- | --- | --- | --- | --- | --- |
| Desktop, naive | 12′ / 59′ | 54 % | 0.21 | 0.93 | 0.08′ | 0.12′ | 0.18′ |
| Desktop, preset | 0.5′ / 14′ | 0 | 0.00 | 1.00 | 0.02′ | 0.003′ | 0.006′ |
| Game, naive | 39′ / 42′ | 46 % | 0.42 | 0.74 | 1.45′ | 0.31′ | 0.67′ |
| Game, preset | 8′ / 25′ | 6 % | 0.001 | 1.00 | 0.02′ | 0.09′ | 0.20′ |
| Video, naive | 40′ / 32′ | 3 % | 0.81 | not meaningful: no real text | | 0.43′ | 1.44′ |
| Video, preset | 14′ / 28′ | 0 | 0.24 | not meaningful: no real text | | 0.06′ | 0.18′ |

Each preset meets all its targets. The naive version misses 8 of 10 (desktop), 8 of 9 (game) and 5 of 7 (video). The targets were set
from these same samples, so that shows the method separates them. It is not independent proof.

**Each stage earns its place.** Turning one off at a time:

| Stage off | What got worse |
| --- | --- |
| Guided filter (snaps depth to real edges) | Video halo 0.24 to 0.45; game 0.001 to 0.004 |
| Temporal smoothing | Video jitter 0.18′ to 0.57′, halo 0.24 to 0.50; game jitter 0.20′ to 0.30′ |
| Text pin | Desktop text, worst tenth 1.00 to 0.94; only 37 % of the screen on the plane instead of 73 % |
| HUD lock | Game text, worst tenth 1.00 to 0.95; HUD wobble 0.02′ to 0.11′ |
| Soften | Game halo 0.001 to 0.08; video 0.24 to 0.34 |
| Edge fade | Game border violations 6 % to 22 %, halo 0.001 to 0.12 |
| Letterbox handling | Video: on-plane share 42 % to 6 %, wobble 0.03′ to 0.49′ |

One cost: the HUD lock raised the game's still flicker from 0.02′ to 0.09′, probably the HUD sliding to the plane during the half second
before the lock engages. The panels' start-up glide should be looked at in the glasses.

**Text and fractions of a pixel.** A whole page shifted by 0, 0.25, 0.5 and 1 px scores 1.00, 0.98, 0.94 and 1.00. A whole-pixel shift is
free and half a pixel is the worst case. OCR read 99.7 % or better throughout, so the text stays readable and the loss is sharpness.

| Parallax left on text (`text_keep`), at limits like `xr/stereo.hpp`'s defaults | 0 | 0.15 | 0.5 | 1 |
| --- | --- | --- | --- | --- |
| Text sharpness, average | 0.997 | 0.985 | 0.983 | 0.971 |
| Text sharpness, worst tenth | 1.00 | 0.96 | 0.96 | 0.92 |

So most of the cost arrives with the first bit of parallax, and 0 is the setting to use. This ran in the reference pipeline, not
`stereo_gl.cpp`. The renderer also resamples a desktop onto the eye, which the reference does not, so real text is already slightly
softer and the gap may be smaller.

**Black bars.** With the bars reaching the model, the actor in the middle sat 12′ behind the plane and the bars came forward. With the
bars cropped before inference that actor sat 4′ in front and the bars stayed at 0.

**Hole fill**, at a deliberately high 40′ / 60′ budget so there are holes to judge (halo score, lower is better):

| Fill | Desktop | Game | Video |
| --- | --- | --- | --- |
| Black | 1.26 | 2.24 | 4.60 |
| Foreground smear | 0.62 | 1.11 | 2.20 |
| Background smear | 0.34 | 0.77 | 1.80 |
| Background mirrored | 0.25 | 0.66 | 1.29 |

`stereo_gl.cpp`'s hole fill blends along the surface from the warped sample; which side it draws from at a foreground edge is worth
checking against this table. The `sbs` command will score it.

**Both eyes, or one?** Warping only one eye keeps the other pristine, but it took the video's halo score from 0.24 to 1.12 and
the widest holes from 18 to 35 px. Share it evenly.

**Strength.** Halo on the video sample at 0.5, 1 and 1.5 times the preset: 0.05, 0.24, 0.56. So Soft is nearly clean, and Enhanced at
1.5 misses the 0.5 target. It is fine as a choice someone makes, not as a default.

**Depth map size** (depth from the same model at each height):

| Height | CPU, 6 threads | Game jitter | Video jitter | Video still flicker |
| --- | --- | --- | --- | --- |
| 140 | 32 ms | 1.03′ | 0.45′ | 0.23′ |
| 252 | 67 ms | 0.21′ | 0.20′ | 0.07′ |
| 364 | 140 ms | 0.20′ | 0.19′ | 0.07′ |
| 518 | 349 ms | 0.20′ | 0.18′ | 0.06′ |

The design contract's 256 px minimum holds up. The model needs multiples of 14, so 266 is the size to use. It was not itself tested;
252 and 364 were.

**Smoothing strength.** Doubling the video's time constants takes jitter from 0.18′ to 0.11′ and four times to 0.06′, but 90 % of a depth
change then takes 2.3 time constants: 140 ms at the preset's moving rate and 550 ms at four times. The metrics cannot see that lag,
so the presets stay at the shorter values. If depth trails moving objects in the glasses, this is the setting to change.

**What did not work.** The guard meant to flatten scenes with no real depth does nothing on real pages: the model's invented depth has
enough spread to pass it (gain 0.92 on a full page). Text pinning is what protects a page, so the guard is only a backstop.

## Recommended defaults

3D stays off by default. **Desktop** is the default preset: it is the safe one, and it is the one that sees text. Offer video and games
per desktop. Automatic switching should wait until the presets are proven in the glasses.

<!-- presets -->
| Setting | Desktop / text | Video | Games |
| --- | --- | --- | --- |
| Near limit, in front | 4′ | 14′ | 10′ |
| Far limit, behind | 14′ | 36′ | 30′ |
| Plane at percentile of nearness | 92 | 70 | 75 |
| Depth smoothing, still pixels (ms) | 600 | 300 | 200 |
| Depth smoothing, moving pixels (ms) | 150 | 60 | 30 |
| Range smoothing (ms) | 3000 | 1000 | 800 |
| Edge fade, share of width (%) | 5 | 4 | 4 |
| Soften depth edges (px) | 3 | 2 | 2 |
<!-- /presets -->

| Feature | Desktop / text | Video | Games |
| --- | --- | --- | --- |
| Text pinned to the plane | Yes | No | Yes |
| HUD lock | No | No | Yes |
| Letterbox handling | Yes | Yes | Yes |
| After a scene cut, ease depth back over | 400 ms | 250 ms | 200 ms |

Why they differ: a desktop is read for hours and has little real depth, so most of it stays flat and only pictures and wallpaper recede.
Video can afford the most depth because the eye is not reading and motion hides fill. Games get less than video because a player's eye
goes to edges and small targets and lag matters, so its smoothing is the shortest.

Depth levels for the panel, using VITURE's names since users of these glasses know them: **Soft** 0.5, **Standard** 1, **Enhanced**
1.5 times the preset's strength, never past the limits above.

### In `xr/stereo.hpp` terms

The presets carry straight across. Near and far are the same idea with limits in degrees, and `strength` 1 means the scene reaches them.
`xr/stereo.hpp`'s sign is the opposite of this tool's, but limits are positive in both.

| `StereoParams` | Now | Desktop | Video | Games |
| --- | --- | --- | --- | --- |
| `near_deg` | 1.0 | 0.067 | 0.233 | 0.167 |
| `far_deg` | 0.75 | 0.233 | 0.6 | 0.5 |
| `strength` | 0.6 | 1 | 1 | 1 |
| `text_keep` | 0.15 | 0 | 1 (no text pin) | 0 |

`convergence` there is a fixed nearness and here it is a percentile that follows the scene. Passing the percentile's value per frame
would give the same result. Its current 60 ms filter is close to the video preset's moving rate, but it has no slower rate for still pixels.

### For the other threads

- **Depth engine:** crop black bars before inference. Run about 266 px tall. Report the crop with the frame.
- **Stereo renderer:** per-preset time constants, separate still and moving rates, and a slower filter on the range. Whole-pixel or zero
  shift on text. Check which side fill draws from.
- **Integration:** a per-desktop preset next to the toggle, and Soft / Standard / Enhanced. Saved with the glasses profile.
- **Later idea, not tested:** on a desktop the compositor already knows every window's rectangle and stacking order. Giving each window one
  flat depth from Hyprland's data would need no AI, keep every window pixel-sharp, and could never wobble.

## How the scores work

All in `tools/stereo_eval/metrics.py`, and all have tests on scenes with a known answer.

| Score | Meaning |
| --- | --- |
| Near / far | How far in front of and behind the plane the picture goes, 99.9th percentile, in arcminutes |
| Border violations | Share of the outer 2 % of the width that stands in front of the plane |
| Still flicker | Depth change per frame on pixels whose picture did not change |
| Jitter | Second difference of disparity over time: ignores steady motion, so it works on panning content |
| Pump | Frame-to-frame slide of the whole picture's median depth |
| Halo | Filled area weighted by how much the fill differs from the real background beside it, in colour or in lost texture |
| Text sharpness | SSIM of text tiles against the source after sliding each to its best match, so a whole shift costs nothing and damage does. 1 is perfect |
| Text wobble | Disparity varying along a line of text |
| OCR match | Optional: how much of tesseract's reading of the source survives in an eye |

`edge_misalignment_px` is also reported but is weak. It drops to 0 when softening removes the depth edges it looks for, so it only
compares like with like, and no target uses it. Each preset's targets are in `tools/stereo_eval/presets.json`.

`sbs` scores recorded 3840x1200 output with no depth map, by estimating disparity from the two eyes. Against this pipeline's known answers
it read 0′ / 15′ on the desktop (true 0.5′ / 14′), 9′ / 24′ on the game (8′ / 25′) and 15′ / 27′ on the video (14′ / 28′).

## Sample content

Put these in `~/.cache/viture-stereo-eval/samples/` to rerun the sweep. Results of the last run are in
`tools/stereo_eval/results/sweep.json`.

- `game-0ad.jpg`: [0 A.D. alpha 25, playing as Spartans](https://commons.wikimedia.org/wiki/File:0_A.D._alpha_25_-_playing_as_Spartans.jpg),
  Wikimedia Commons, CC BY-SA 3.0.
- `tos-230.mov`: 4 seconds from 3:50 of [Tears of Steel](https://mango.blender.org/), (c) Blender Foundation, CC BY 3.0:
  `ffmpeg -ss 230 -t 4 -i http://ftp.nluug.nl/pub/graphics/blender/demo/movies/ToS/tears_of_steel_720p.mov -an -c copy tos-230.mov`
- Model: [Depth Anything V2 Small, ONNX](https://huggingface.co/onnx-community/depth-anything-v2-small), Apache-2.0, pinned revision in
  `depth_source.py`. Only the Small size is Apache-2.0; Base, Large and Giant are CC-BY-NC-4.0, so they must not ship.
  [Video Depth Anything](https://huggingface.co/AEmotionStudio/Video-Depth-Anything-Small/blob/main/README.md) (temporally stable) and
  [Depth Anything 3 small](https://huggingface.co/depth-anything/da3-small) are listed as Apache-2.0 but were not tested. The first listing
  is a community mirror, so check upstream before using it.

## Not known yet

- **Whether any of this is comfortable.** Every number here is a proxy. The presets need trying on real eyes, for more than a few minutes.
- **The focal distance.** 3 m is inferred from a "152 inch at 3 m" marketing line, and `xr/stereo.hpp` assumes 4 m. All presets fit
  within both. VITURE publishes no figure.
- **The real desktop.** The text sample is drawn, not captured, and the game pan moves the world as one flat sheet. The film is real.
  A session recorded on the real desktops should be scored before the presets are final.
- **The renderer's own output.** These presets have not been through `stereo_gl.cpp`. Once it can record frames, `sbs` will score them.
- **Latency.** Not measured here: only CPU time per frame, not GPU, and not the delay from a frame to its depth arriving.
- **SBS mode on the Luma Ultra:** refresh rate at 3840x1200 and how to enter and leave it are open on the driver branches.
- **Long-term effects.** No time limit is recommended because no source gave one for converted content.

## Sources

Comfort: [Shibata, Kim, Hoffman, Banks, "The zone of comfort", J. Vision 2011](https://pmc.ncbi.nlm.nih.gov/articles/PMC3369815);
[Causes of discomfort in stereoscopic content: a review](https://arxiv.org/abs/1703.04574);
[BSkyB 3D technical specification](https://www.cinematography.net/3D/BSkyB_Tech_Spec_COMM_Content_ver1.0.pdf).
Glasses: [Luma Ultra specifications](https://vrarwiki.com/wiki/Viture_Luma_Ultra). Products: linked in the table above.
Depth: [Depth Anything V2](https://github.com/DepthAnything/Depth-Anything-V2).
