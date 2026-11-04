#!/bin/bash
# Installs the depth engine's runtime into ~/.cache/viture-depth. No sudo, touches nothing outside that folder.
# Needs uv (mise) and network, once; about 16 GB, nearly all of it PyTorch's ROCm libraries. The engine itself never
# uses the network.
#
#   tools/depth_engine/setup.sh                  PyTorch on ROCm, weights and model code
#   tools/depth_engine/setup.sh --ncnn 518x322   also convert the model for the ncnn fallback (repeat per size)
#
# Licences: Depth Anything V2 Small weights and code Apache-2.0 (the Base and Large weights are CC-BY-NC, do not swap
# them in); PyTorch BSD-3; ROCm MIT and similar; ncnn BSD-3; OpenCV Apache-2.0; numpy BSD-3.
set -euo pipefail
CACHE="${XR_DEPTH_CACHE:-$HOME/.cache/viture-depth}"
CODE_REV=a561b84
WEIGHTS_REPO=depth-anything/Depth-Anything-V2-Small
WEIGHTS_REV=03876f8651c73a60fe4c2c48294e09fcb6838fcf
WEIGHTS_SHA=715fade13be8f229f8a70cc02066f656f2423a59effd0579197bbf57860e1378
ONNX_REPO=onnx-community/depth-anything-v2-small
ONNX_REV=4472b7362082ad9968fee890ca0f1e5aca36b93d
ONNX_SHA=afb6a5c28f3b6bf1618c6e43f02073ef9dfdc70e937502d51603e57b0a1df10c

fetch() { # url sha path
    if [ -f "$3" ] && echo "$2  $3" | sha256sum -c --status; then return; fi
    mkdir -p "$(dirname "$3")"
    curl -fL --retry 3 -o "$3.part" "$1"
    echo "$2  $3.part" | sha256sum -c --quiet
    mv "$3.part" "$3"
}

mkdir -p "$CACHE/models"
if [ ! -x "$CACHE/venv/bin/python" ]; then uv venv --python 3.12 "$CACHE/venv"; fi
export VIRTUAL_ENV="$CACHE/venv"
if ! "$CACHE/venv/bin/python" -c 'import torch, sys; sys.exit(0 if torch.__version__ == "2.9.1+rocm6.4" else 1)' 2>/dev/null; then
    uv pip install --index-url https://download.pytorch.org/whl/rocm6.4 torch==2.9.1+rocm6.4 torchvision==0.24.1+rocm6.4
fi
uv pip install numpy opencv-python-headless ncnn==1.0.20261020 pillow
if [ ! -d "$CACHE/dav2-code/.git" ]; then git clone -q https://github.com/DepthAnything/Depth-Anything-V2 "$CACHE/dav2-code"; fi
git -C "$CACHE/dav2-code" checkout -q "$CODE_REV"
fetch "https://huggingface.co/$WEIGHTS_REPO/resolve/$WEIGHTS_REV/depth_anything_v2_vits.pth" "$WEIGHTS_SHA" "$CACHE/models/depth_anything_v2_vits.pth"

while [ $# -gt 0 ]; do
    case "$1" in
    --ncnn)
        size="$2"; shift 2
        fetch "https://huggingface.co/$ONNX_REPO/resolve/$ONNX_REV/onnx/model.onnx" "$ONNX_SHA" "$CACHE/models/dav2s.onnx"
        if [ ! -x "$CACHE/venv-convert/bin/pnnx" ]; then uv venv --python 3.12 "$CACHE/venv-convert"; VIRTUAL_ENV="$CACHE/venv-convert" uv pip install pnnx==20261020; fi
        w="${size%x*}"; h="${size#*x}"; out="$CACHE/ncnn/dav2_$size"; mkdir -p "$out"
        (cd "$out" && cp "$CACHE/models/dav2s.onnx" m.onnx && "$CACHE/venv-convert/bin/pnnx" m.onnx "inputshape=[1,3,$h,$w]" fp16=1 >/dev/null \
            && rm -f m.onnx m.pnnx* m.pnnxsim* m_pnnx.py m_ncnn.py)
        ;;
    *) echo "unknown option $1" >&2; exit 2 ;;
    esac
done
du -sh "$CACHE"
