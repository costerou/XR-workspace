"""Where the depth comes from for an offline run: a saved map, or Depth Anything V2 Small on the CPU.

The ONNX path needs onnxruntime and the weights, and neither is required by the tests or by scoring saved maps:

    uv venv --python 3.12 ~/.cache/viture-stereo-eval/venv
    uv pip install --python ~/.cache/viture-stereo-eval/venv/bin/python onnxruntime numpy pillow

Weights: onnx-community/depth-anything-v2-small at the pinned revision below, licence Apache-2.0 (only the Small size is; Base,
Large and Giant are CC-BY-NC-4.0). Nothing leaves the machine at runtime; downloading is `fetch_model`, and only that.
"""
import hashlib
import os
from pathlib import Path
import urllib.request

import numpy as np
from PIL import Image

from .conditioning import content_box, luma, resize

CACHE = Path(os.environ.get('STEREO_EVAL_CACHE', Path.home() / '.cache/viture-stereo-eval'))
MODEL = CACHE / 'models/da2s.onnx'
REVISION = '4472b7362082ad9968fee890ca0f1e5aca36b93d'
MODEL_URL = f'https://huggingface.co/onnx-community/depth-anything-v2-small/resolve/{REVISION}/onnx/model.onnx'
MEAN, STD = np.array([0.485, 0.456, 0.406], np.float32), np.array([0.229, 0.224, 0.225], np.float32)
_session = None


def fetch_model() -> Path:
    if not MODEL.exists():
        MODEL.parent.mkdir(parents=True, exist_ok=True)
        urllib.request.urlretrieve(MODEL_URL, MODEL)
    return MODEL


def model_size(w: int, h: int, height: int) -> tuple[int, int]:
    """The ViT wants multiples of 14. Keeps the aspect ratio; `height` is the map's height."""
    mh = max(14, round(height / 14) * 14)
    return max(14, round(mh * w / h / 14) * 14), mh


def _load():
    global _session
    if _session is None:
        try:
            import onnxruntime as ort
        except ImportError as error:
            raise SystemExit(f'onnxruntime is missing; see tools/stereo_eval/depth_source.py ({error})')
        if not MODEL.exists():
            raise SystemExit(f'no weights at {MODEL}; run: python -m tools.stereo_eval fetch-model')
        options = ort.SessionOptions()
        options.intra_op_num_threads = int(os.environ.get('STEREO_EVAL_THREADS', '6'))
        _session = ort.InferenceSession(str(MODEL), options, providers=['CPUExecutionProvider'])
    return _session


def infer(rgb: np.ndarray, height: int = 518, cache: bool = True) -> np.ndarray:
    """Nearness map (bigger = nearer) from Depth Anything V2 Small, `height` rows tall. Results are cached by content."""
    key = hashlib.sha1(rgb.tobytes()).hexdigest()[:20] + f'-{height}.npy'
    path = CACHE / 'depth' / key
    if cache and path.exists():
        return np.load(path)
    session = _load()
    w, h = model_size(rgb.shape[1], rgb.shape[0], height)
    small = np.asarray(Image.fromarray(rgb.astype(np.uint8)).resize((w, h), Image.BICUBIC), np.float32) / 255
    x = ((small - MEAN) / STD).transpose(2, 0, 1)[None]
    name = session.get_inputs()[0].name
    out = np.squeeze(session.run(None, {name: x})[0]).astype(np.float32)
    if cache:
        path.parent.mkdir(parents=True, exist_ok=True)
        np.save(path, out)
    return out


def infer_content(rgb: np.ndarray, height: int = 518) -> np.ndarray:
    """Depth of the picture inside any black bars: the bars never reach the model. Frame-sized; 0 (far) in the bars."""
    box = content_box(luma(rgb))
    if box is None:
        return np.zeros(rgb.shape[:2], np.float32)
    x0, y0, x1, y1 = box
    out = np.zeros(rgb.shape[:2], np.float32)
    out[y0:y1, x0:x1] = resize(infer(rgb[y0:y1, x0:x1], height), x1 - x0, y1 - y0)
    return out


def load_map(path: str | Path) -> np.ndarray:
    """A saved nearness map: .npy, or a 16-bit PNG."""
    path = Path(path)
    if path.suffix == '.npy':
        return np.load(path).astype(np.float32)
    return np.asarray(Image.open(path), np.float32)
