# stereo_eval

Offline quality and comfort checks for the AI 3D filter. It scores recorded frames and draws preview pictures. It changes
nothing in the renderer, nothing links it, and it never talks to a run, the glasses or the GPU. Findings and recommended
settings are in [docs/3d-comfort.md](../../docs/3d-comfort.md).

## What is real

The depth comes from a real model, Depth Anything V2 Small, run on the CPU. The conversion is a Python reference of the pipeline in
the doc, not the renderer's shaders: `conditioning.py` turns depth into disparity and `dibr.py` warps the two eyes. So the presets are
tested here and not yet through `stereo_gl.cpp`. To score the real renderer, record its side-by-side output and use `sbs`, which
needs no depth map.

The scores are engineering measures. No person has looked at any of this in the glasses yet.

## Commands

From the repo root. Scoring saved depth maps and the `sbs` command need only `python3` with numpy and Pillow, which the machine has.

```
python -m tools.stereo_eval eval FRAMES --depth maps/ --preset video     # frames plus saved depth maps
python -m tools.stereo_eval sbs RECORDED --preset desktop                # 3840x1200 side-by-side recordings
```

`FRAMES` is a picture, a folder of pictures or a video. A saved map is `.npy` or a 16-bit PNG, larger meaning nearer.
`--out DIR` writes `sbs.png` and `anaglyph.png`. `--json` prints JSON. The exit status is 1 if a preset's targets are missed.

Running the model, the sweeps and the gallery need onnxruntime, which the system Python does not have. A private environment
keeps it out of the system:

```
uv venv --python 3.12 ~/.cache/viture-stereo-eval/venv
uv pip install --python ~/.cache/viture-stereo-eval/venv/bin/python onnxruntime numpy pillow
PY=~/.cache/viture-stereo-eval/venv/bin/python
$PY -m tools.stereo_eval fetch-model                     # the only command that uses the network
$PY -m tools.stereo_eval eval clip.mp4 --preset video --limit 60 --out /tmp/look
$PY -m tools.stereo_eval sweep --out /tmp/sweep          # about 15 minutes on 6 CPU threads
$PY -m tools.stereo_eval gallery --out ~/.hermes/profiles/coder/previews/3d-samples
```

`STEREO_EVAL_THREADS` sets the model's CPU threads (default 6). Depth maps are cached by picture in
`~/.cache/viture-stereo-eval/depth/`.

`sweep` and `gallery` also need the two downloaded samples in `~/.cache/viture-stereo-eval/samples/`; the doc's
[Sample content](../../docs/3d-comfort.md#sample-content) gives the sources and licences. The desktop sample is drawn by
`samples.py` from this repo's own text, so it holds nothing private.

## Files

| File | What it does |
| --- | --- |
| `geometry.py` | Angles on the glasses, and the comfort zone |
| `presets.json`, `presets.py` | The presets and their targets |
| `conditioning.py` | Depth to comfortable disparity: every stage the doc recommends |
| `dibr.py` | One picture and a disparity map to two eyes, with disocclusion fill |
| `metrics.py` | Disparity range, flicker, halo, text legibility, brightness, block matching for `sbs` |
| `depth_source.py` | The model, or saved maps |
| `run.py` | The pipeline end to end |
| `samples.py` | Sample content |
| `preview.py` | Side-by-side, anaglyph, disparity views, contact sheets |
| `study.py` | The sweeps and the gallery |

## Sign

Here disparity is x_right - x_left, so a negative number is in front of the screen. `xr/stereo.hpp` uses the opposite sign, positive
for crossed. Near and far limits are given as positive amounts in both, so the presets carry over unchanged.

## Tests

```
python -m unittest discover -s tests/stereo_eval -v
```

No model, network or GPU. Each metric is tested on a scene with a known answer: it has to rank a bad result below a good one.
