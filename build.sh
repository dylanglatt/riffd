#!/usr/bin/env bash
# Render build script — ensures Node.js is available for yt-dlp signature solving
set -o errexit

echo "=== Riffd Build ==="

# Check if Node.js is available
if command -v node &> /dev/null; then
    echo "[build] Node.js already available: $(node --version)"
else
    echo "[build] Node.js not found — installing via apt"
    apt-get update -qq && apt-get install -y -qq nodejs npm 2>/dev/null || {
        echo "[build] apt install failed — trying curl install"
        curl -fsSL https://deb.nodesource.com/setup_20.x | bash - 2>/dev/null
        apt-get install -y -qq nodejs 2>/dev/null || echo "[build] WARNING: could not install Node.js"
    }
    if command -v node &> /dev/null; then
        echo "[build] Node.js installed: $(node --version)"
    else
        echo "[build] WARNING: Node.js still not available — yt-dlp signature solving may fail"
    fi
fi

# Install Python dependencies
pip install --upgrade pip
pip install -r requirements.txt

# Basic Pitch, without its dependency metadata.
#
# On Linux + Python >= 3.11 basic-pitch hard-requires tensorflow<2.15.1 (a plain
# Requires-Dist, not an extra, so `basic-pitch[onnx]` does NOT avoid it), and
# basic_pitch picks TensorFlow over ONNX the moment it is importable. Its real
# runtime deps are in requirements.txt and are installed above.
echo "[build] Installing basic-pitch (--no-deps, ONNX backend)..."
pip install --no-deps -r requirements-basic-pitch.txt

# Gate: prove the environment has no TensorFlow and that inference still imports.
# A future transitive dependency that drags TF back in must break the build here,
# loudly, rather than silently costing ~460MB per inference child in production.
echo "[build] Verifying Basic Pitch backend..."
python3 - <<'PYCHECK'
import sys

sys.path.insert(0, ".")  # heredoc via stdin: make the repo root importable regardless of cwd

try:
    import tensorflow  # noqa: F401
except ImportError:
    pass
else:
    sys.exit(
        "[build] FAIL: tensorflow is installed. basic_pitch prefers it over ONNX, "
        "which costs ~460MB per inference child for identical note output. "
        "Find what pulled it in (`pip show tensorflow` -> Required-by) and exclude it."
    )

from processor import _ensure_pitch_imports  # noqa: E402
import processor  # noqa: E402

_ensure_pitch_imports()
if processor.PITCH_BACKEND != "onnx":
    sys.exit(f"[build] FAIL: Basic Pitch backend is {processor.PITCH_BACKEND!r}, expected 'onnx'")
if not processor.ICASSP_2022_MODEL_PATH.exists():
    sys.exit(f"[build] FAIL: model asset missing: {processor.ICASSP_2022_MODEL_PATH}")
print(f"[build] OK: no tensorflow; backend={processor.PITCH_BACKEND} "
      f"model={processor.ICASSP_2022_MODEL_PATH.name}")
PYCHECK

# PANNs CNN10 weights for component tagging.
#
# Fetched ONCE here, never during a job: a 25MB download on the request path is
# a multi-second stall the watchdog cannot see through, and it would land on
# whichever user happens to be first after a deploy. Files written into the
# project directory during build are present at runtime.
echo "[build] Fetching PANNs CNN10 weights..."
PANNS_DIR="${PANNS_MODEL_DIR:-models/panns}"
mkdir -p "${PANNS_DIR}"
PANNS_CKPT="${PANNS_DIR}/Cnn10_mAP=0.380.pth"
PANNS_LABELS="${PANNS_DIR}/class_labels_indices.csv"
PANNS_SHA="5240bdc47444e331bbeb54fd741d2b3f933a4526e84b0c17bf3593df94b13962"

if [ ! -f "${PANNS_CKPT}" ]; then
    curl -fsSL --retry 3 -o "${PANNS_CKPT}.part" \
        "https://zenodo.org/records/3987831/files/Cnn10_mAP%3D0.380.pth"
    mv "${PANNS_CKPT}.part" "${PANNS_CKPT}"
fi
if [ ! -f "${PANNS_LABELS}" ]; then
    curl -fsSL --retry 3 -o "${PANNS_LABELS}.part" \
        "https://storage.googleapis.com/us_audioset/youtube_corpus/v1/csv/class_labels_indices.csv"
    mv "${PANNS_LABELS}.part" "${PANNS_LABELS}"
fi

# Checksum the checkpoint. A truncated or redirected download would otherwise
# surface as load_state_dict blowing up inside a job, long after the build said OK.
echo "${PANNS_SHA}  ${PANNS_CKPT}" | sha256sum -c - || {
    echo "[build] FAIL: PANNs checkpoint checksum mismatch at ${PANNS_CKPT}"
    exit 1
}

# Gate: prove the tagger actually loads and runs before shipping. Same reasoning
# as the Basic Pitch gate above — a missing checkpoint or a renamed layer is
# silent until the first analysis, which is far too late.
echo "[build] Verifying PANNs tagger..."
python3 - <<'PYCHECK'
import sys

sys.path.insert(0, ".")
import torch  # noqa: E402  (build process only — never the gunicorn worker)
import panns_tagger  # noqa: E402

model = panns_tagger.load_model()
labels = panns_tagger.load_labels()

import processor  # noqa: E402
unknown = [c for c in processor.AUDIOSET_TO_LABEL if c not in set(labels)]
if unknown:
    sys.exit(f"[build] FAIL: AUDIOSET_TO_LABEL keys are not AudioSet class names: {unknown}")

with torch.no_grad():
    out = model(torch.zeros(1, panns_tagger.SAMPLE_RATE, dtype=torch.float32))
if out.shape != (1, panns_tagger.CLASSES_NUM):
    sys.exit(f"[build] FAIL: tagger output shape {tuple(out.shape)}")
print(f"[build] OK: PANNs CNN10 loaded, {len(labels)} classes, "
      f"{len(processor.AUDIOSET_TO_LABEL)} mapped to riffd labels")
PYCHECK

# Install Playwright Chromium for cookie refresh
echo "[build] Installing Playwright Chromium browser..."
# Run with full output so failures are visible in Render build logs
playwright install chromium --with-deps || {
    echo "[build] WARNING: playwright install chromium --with-deps failed (exit $?)"
    echo "[build] Trying without --with-deps (system deps may already be present)..."
    playwright install chromium || echo "[build] WARNING: Playwright browser install failed — cookie refresh will be unavailable"
}
# Verify the browser executable actually exists after install
PLAYWRIGHT_CACHE=$(python3 -c "import playwright; import os; print(os.path.join(os.path.dirname(playwright.__file__), '..', '..', '..', '.cache', 'ms-playwright'))" 2>/dev/null || echo "/opt/render/.cache/ms-playwright")
if find "${PLAYWRIGHT_CACHE}" -name "chrome-headless-shell" -type f 2>/dev/null | grep -q .; then
    echo "[build] ✅ Playwright headless shell found"
else
    echo "[build] ⚠️  Playwright headless shell not found in ${PLAYWRIGHT_CACHE} — cookie refresh will fall back gracefully"
fi

echo "=== Build complete ==="
