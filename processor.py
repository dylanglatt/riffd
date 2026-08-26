"""
processor.py — Core audio processing pipeline for Riffd.

Used ONLY in deep analysis mode (not instant). All functions here are heavy.
Imports are deferred: numpy/pandas via _ensure_imports(), basic_pitch via
_ensure_pitch_imports(), which only child processes should call.

Pipeline (called by app.py process_audio deep path):
  1. separate_stems()  — Demucs subprocess → stereo refinement → labeled WAV stems
     - Tries htdemucs_6s first, falls back to htdemucs 4-stem
     - Can use Replicate hosted GPU via USE_HOSTED_SEPARATION env var
     - Output: {stem_key: {path, energy, active, label}} saved to outputs/<job_id>/stems/
     - Execution time: 2-5 min on CPU, ~20s on GPU (Replicate)
     - Peak memory: ~500MB during Demucs

  2. extract_note_events() — Basic Pitch inference per pitched stem → note events DataFrame
     - Per-instrument confidence thresholds and frequency ranges
     - Drums skipped entirely (no useful pitch data)
     - No file output — inference only, result passed to analyze_song_from_notes()
     - Execution time: ~5-10s per stem

Downstream contract (must not change):
  - separate_stems() returns {stem_key: {path: str, energy: float, active: bool, label: str}}
  - Stem WAV files at outputs/<job_id>/stems/<stem_key>.wav
  - Audio served via /api/audio/<job_id>/<stem_name> in app.py
"""

import struct as _struct
import shutil
import subprocess
import sys
import wave
from pathlib import Path

# Heavy imports deferred to first use — saves ~200MB at boot
np = None
pd = None
ICASSP_2022_MODEL_PATH = None
predict = None
PITCH_BACKEND = None


from compat import patch_lzma as _patch_lzma


def _ensure_imports():
    """Lazy-load the DSP imports (numpy, pandas) on first use.

    Deliberately does NOT import basic_pitch. Even on the ONNX backend that is
    ~360MB of librosa/numba/scipy that the process never gives back, and this
    function runs in the parent gunicorn worker on every job. Pitch inference
    happens in a child process (app.py spawns it) precisely so the OS reclaims
    that memory on exit; see _ensure_pitch_imports below.
    """
    global np, pd
    if np is not None:
        return
    _patch_lzma()
    import numpy as _np
    import pandas as _pd
    np = _np
    pd = _pd
    print("[processor] DSP imports loaded (numpy, pandas)")


# Basic Pitch backend preference, most-preferred first.
#
# All four backends are the same ICASSP-2022 weights (<300KB each) and ship in
# the basic-pitch wheel; they produce byte-identical note events. What differs
# is the runtime behind them, and basic_pitch's own default picks the worst one:
# its module-level ICASSP_2022_MODEL_PATH resolves TF -> CoreML -> TFLite -> ONNX,
# so merely having `tensorflow` importable costs ~460MB of child RSS and ~2.5x
# the wall clock for identical output. Choose explicitly instead of inheriting
# whatever happens to be installed.
_PITCH_BACKENDS = ("onnx", "tflite", "coreml")


def _ensure_pitch_imports():
    """Lazy-load basic_pitch on first use and pin the inference backend.

    Call this ONLY from code that genuinely runs inference in the current
    process. Today that is extract_note_events(), which app.py invokes in a
    child process. Calling it in the parent puts ~360MB into a worker that has
    to stay lean while that child is alive, which is how the OOMs happened —
    and if `tensorflow` is ever reinstalled, importing basic_pitch alone drags
    TF in on top of that.
    """
    global ICASSP_2022_MODEL_PATH, predict, PITCH_BACKEND
    if predict is not None:
        return
    _ensure_imports()
    import basic_pitch as _bp
    from basic_pitch.inference import predict as _predict

    _available = {
        "onnx": _bp.ONNX_PRESENT,
        "tflite": _bp.TFLITE_PRESENT,
        "coreml": _bp.CT_PRESENT,
    }
    for _name in _PITCH_BACKENDS:
        if not _available[_name]:
            continue
        _path = _bp.build_icassp_2022_model_path(_bp.FilenameSuffix[_name])
        if not _path.exists():
            # Asset missing from this wheel — try the next backend rather than
            # letting predict() fall through to whatever else is importable.
            print(f"[processor] pitch backend {_name} unavailable: {_path} not found")
            continue
        ICASSP_2022_MODEL_PATH = _path
        PITCH_BACKEND = _name
        break
    else:
        # Nothing but TensorFlow. Still works, but it is the memory regression
        # this preference list exists to avoid — say so loudly.
        ICASSP_2022_MODEL_PATH = _bp.build_icassp_2022_model_path(_bp.FilenameSuffix.tf)
        PITCH_BACKEND = "tensorflow"
        print("[processor] WARNING: no ONNX/TFLite/CoreML backend for Basic Pitch — "
              "falling back to TensorFlow (~460MB extra per inference child). "
              "Install onnxruntime.")

    predict = _predict
    print(f"[processor] pitch imports loaded (basic_pitch, {PITCH_BACKEND} backend, "
          f"model={ICASSP_2022_MODEL_PATH.name})")


# ── Replicate constants (module-level so warm-up + separation share them) ──
# Model: ryan5453/demucs (htdemucs_6s). Switched from cjwbw/demucs 2026-07-08:
# that model began failing 100% of the time with Replicate-internal errors
# ("Director: unexpected error handling prediction (E1001)") — its last version
# was from 2023 and appears incompatible with Replicate's current runtime.
# ryan5453/demucs is actively maintained (Dec 2024), has 1.6M+ runs, and
# returns the same {stem_name: url} output dict, so downstream code is unchanged.
# NOTE: its input field is "model" (cjwbw used "model_name").
REPLICATE_API = "https://api.replicate.com/v1"
REPLICATE_DEMUCS_VERSION = "5a7041cc9b82e5a558fea6b3d7b12dea89625e89da33f0447bd727c2d0ab9e77"

# Warm-up state: one warm-up per cooldown window, process-wide.
_warmup_last = 0.0
WARMUP_COOLDOWN_S = 480  # Replicate keeps containers warm for a few minutes after a run


def _probe_duration_s(path) -> float:
    """Return audio duration in seconds via ffprobe, or 0.0 if unknown."""
    try:
        result = subprocess.run(
            ["ffprobe", "-v", "error", "-show_entries", "format=duration",
             "-of", "default=noprint_wrappers=1:nokey=1", str(path)],
            capture_output=True, text=True, timeout=15,
        )
        return max(0.0, float(result.stdout.strip()))
    except Exception:
        return 0.0


def warm_replicate_model() -> bool:
    """
    Fire a ~1-second silent clip at the Demucs model on Replicate so the
    container boots BEFORE the user triggers a real analysis.

    Cold-boot of cjwbw/demucs is the dominant cost of separation (1-4+ min
    observed vs ~20s of actual GPU time). Calling this when a user selects
    a song means the boot happens while they're still deciding, so the real
    prediction usually lands on a warm instance.

    Fire-and-forget: creates the prediction and returns without polling.
    Costs ~1s of GPU time. Never raises. Returns True if a warm-up was sent.
    """
    global _warmup_last
    import os as _os
    import time as _time

    if _os.getenv("USE_HOSTED_SEPARATION", "false").strip().lower() not in ("true", "1", "yes"):
        return False
    token = _os.getenv("REPLICATE_API_TOKEN", "").strip()
    if not token:
        return False

    now = _time.time()
    if now - _warmup_last < WARMUP_COOLDOWN_S:
        return False
    _warmup_last = now  # set optimistically — worst case a failed warm-up waits out the cooldown

    try:
        import base64 as _b64
        import tempfile as _tmp
        import requests as _requests

        # 1s of silence at 32kbps ≈ 4KB — small enough for a data URI (no file upload needed)
        with _tmp.NamedTemporaryFile(suffix=".mp3", delete=False) as _f:
            silence_path = _f.name
        try:
            gen = subprocess.run(
                ["ffmpeg", "-y", "-f", "lavfi", "-i", "anullsrc=r=44100:cl=stereo",
                 "-t", "1", "-b:a", "32k", silence_path],
                capture_output=True, timeout=15,
            )
            if gen.returncode != 0:
                print(f"[warmup] silence generation failed (rc={gen.returncode}) — skipping")
                return False
            with open(silence_path, "rb") as _sf:
                data_uri = "data:audio/mpeg;base64," + _b64.b64encode(_sf.read()).decode("ascii")
        finally:
            try:
                Path(silence_path).unlink(missing_ok=True)
            except Exception:
                pass

        payload = {
            "version": REPLICATE_DEMUCS_VERSION,
            "input": {
                "audio": data_uri,
                "model": "htdemucs_6s",  # must match the real job so the same container boots
                "output_format": "mp3",
                "shifts": 1,
            },
        }
        resp = _requests.post(
            f"{REPLICATE_API}/predictions",
            headers={"Authorization": f"Bearer {token}", "Content-Type": "application/json"},
            json=payload, timeout=15,
        )
        if resp.ok:
            pred_id = resp.json().get("id", "?")
            print(f"[warmup] Replicate warm-up prediction sent (id={pred_id}) — model booting")
            return True
        print(f"[warmup] Replicate warm-up rejected (HTTP {resp.status_code}): {resp.text[:150]}")
        return False
    except Exception as e:
        print(f"[warmup] skipped (non-fatal): {e}")
        return False


def _log_mem(label=""):
    """Log current RSS from /proc/self/status (Linux). Lightweight — no imports."""
    try:
        with open("/proc/self/status") as f:
            rss = hwm = None
            for line in f:
                if line.startswith("VmRSS:"):
                    rss = int(line.split()[1]) // 1024  # kB → MB
                elif line.startswith("VmHWM:"):
                    hwm = int(line.split()[1]) // 1024
            if rss is not None:
                print(f"[mem] {label} RSS={rss}MB peak={hwm}MB")
    except Exception:
        pass


UPLOAD_DIR = Path("uploads")
OUTPUT_DIR = Path("outputs")

# ─── Demucs Configuration ────────────────────────────────────────────────────
DEMUCS_MODEL = "htdemucs_6s"  # was "htdemucs_6stems" — not a real model name, so the first run always failed
STEM_NAMES_6 = ["vocals", "drums", "bass", "guitar", "piano", "other"]
STEM_NAMES_4 = ["vocals", "drums", "bass", "other"]

# RMS threshold — below this a component is considered silent
SILENCE_THRESHOLD = 0.003

# Minimum component energy relative to the stem's total energy
# to be included (avoids showing ghost components).
# 0.25 = component must be at least 25% as loud as the stem — filters quiet bleed artifacts.
MIN_RELATIVE_ENERGY = 0.25

# Absolute minimum RMS energy for a component to be kept at all.
# Prevents near-silent ghost stems from appearing in the mixer.
MIN_ABSOLUTE_ENERGY = 0.008


# ─── Note Detection Configuration ────────────────────────────────────────────
# Per-instrument Basic Pitch parameters and confidence thresholds.
# Structure allows easy tuning without code changes.

INSTRUMENT_CONFIGS = {
    "bass_tab": {
        "min_freq": 27,       # E1 = 41 Hz, but allow some headroom
        "max_freq": 350,      # Cut above G3 range — eliminates guitar/vocal bleed
        "onset_threshold": 0.5,
        "frame_threshold": 0.3,
        "min_note_length": 127,  # Bass notes are sustained, keep default
        "confidence_threshold": 0.30,  # Bass in isolated stems has lower confidence but usually correct
    },
    "guitar_tab": {
        "min_freq": 75,       # E2 = 82 Hz, slight headroom
        "max_freq": 1400,     # Covers fundamentals + first harmonics, cuts cymbal/vocal bleed
        "onset_threshold": 0.5,
        "frame_threshold": 0.3,
        "min_note_length": 80,   # Faster picking needs shorter min
        "confidence_threshold": 0.35,  # Median guitar confidence ~0.40, this drops the bottom quartile
    },
    "drum_tab": {
        # Basic Pitch is fundamentally wrong for drums (A3 will replace this).
        # For now, keep wide parameters and very low threshold to not lose the few hits we get.
        "min_freq": 40,
        "max_freq": 4000,
        "onset_threshold": 0.5,
        "frame_threshold": 0.3,
        "min_note_length": 58,
        "confidence_threshold": 0.15,  # Drums have very low confidence in Basic Pitch — don't filter yet
    },
    "note_list": {
        # Vocals, keys, other
        "min_freq": 80,
        "max_freq": 2000,
        "onset_threshold": 0.5,
        "frame_threshold": 0.3,
        "min_note_length": 80,
        "confidence_threshold": 0.35,
    },
}

# Fallback for any renderer type not in the dict
_DEFAULT_CONFIG = {
    "min_freq": 40,
    "max_freq": 4000,
    "onset_threshold": 0.5,
    "frame_threshold": 0.3,
    "min_note_length": 58,
    "confidence_threshold": 0.35,
}


def _get_instrument_config(renderer_type: str, configs: dict | None = None) -> dict:
    """Get Basic Pitch parameters + confidence threshold for an instrument type."""
    source = configs if configs is not None else INSTRUMENT_CONFIGS
    return source.get(renderer_type, _DEFAULT_CONFIG)


# ─── Genre Profiles ─────────────────────────────────────────────────────────
# Each profile defines frequency/confidence overrides for note detection and
# energy thresholds for stem filtering. Adding a new genre = one dict entry.
# Keys are category values returned by predict_instruments().

GENRE_PROFILES = {
    "electronic": {
        "bass_tab":  {"min_freq": 20, "max_freq": 250, "confidence_threshold": 0.20},
        "note_list": {"min_freq": 60, "max_freq": 4000, "confidence_threshold": 0.25},
        "drum_tab":  {"min_freq": 20, "confidence_threshold": 0.12},
        "energy": {"other": {"min_relative": 0.08, "min_absolute": 0.003},
                   "bass":  {"min_relative": 0.08, "min_absolute": 0.003}},
    },
    "hiphop": {
        "bass_tab":  {"min_freq": 20, "max_freq": 200, "confidence_threshold": 0.18},
        "drum_tab":  {"min_freq": 20, "confidence_threshold": 0.12},
        "note_list": {"min_freq": 60, "max_freq": 3500, "confidence_threshold": 0.25},
        "energy": {"other": {"min_relative": 0.08, "min_absolute": 0.003},
                   "bass":  {"min_relative": 0.06, "min_absolute": 0.002}},
    },
    "jazz": {
        "bass_tab":  {"min_freq": 30, "max_freq": 500, "confidence_threshold": 0.25},
        "guitar_tab": {"min_freq": 70, "max_freq": 1800},
        "note_list": {"min_freq": 60, "max_freq": 3000, "confidence_threshold": 0.28},
        "energy": {"piano": {"min_relative": 0.10, "min_absolute": 0.004},
                   "other": {"min_relative": 0.10, "min_absolute": 0.004}},
    },
    "classical": {
        "note_list": {"min_freq": 40, "max_freq": 4500, "confidence_threshold": 0.25},
        "bass_tab":  {"min_freq": 30, "max_freq": 600, "confidence_threshold": 0.22},
        "energy": {"other": {"min_relative": 0.06, "min_absolute": 0.002},
                   "piano": {"min_relative": 0.08, "min_absolute": 0.003}},
    },
    "singer_songwriter": {
        "guitar_tab": {"min_freq": 70, "max_freq": 1600, "confidence_threshold": 0.30},
        "note_list":  {"min_freq": 70, "max_freq": 2500, "confidence_threshold": 0.30},
        "energy": {"guitar": {"min_relative": 0.12, "min_absolute": 0.004},
                   "piano":  {"min_relative": 0.10, "min_absolute": 0.004}},
    },
    "world": {
        "note_list": {"min_freq": 50, "max_freq": 4000, "confidence_threshold": 0.25},
        "drum_tab":  {"min_freq": 30, "max_freq": 5000, "confidence_threshold": 0.12},
        "energy": {"other": {"min_relative": 0.08, "min_absolute": 0.003}},
    },
    "ambient": {
        "note_list": {"min_freq": 40, "max_freq": 5000, "confidence_threshold": 0.20},
        "bass_tab":  {"min_freq": 20, "max_freq": 300, "confidence_threshold": 0.18},
        "energy": {"other": {"min_relative": 0.06, "min_absolute": 0.002},
                   "guitar": {"min_relative": 0.08, "min_absolute": 0.003}},
    },
    # "band" uses default INSTRUMENT_CONFIGS — no overrides needed
}


def get_adjusted_configs(instrument_hints: dict | None) -> dict | None:
    """Return a per-job copy of INSTRUMENT_CONFIGS adjusted for this song, or None if no adjustments needed."""
    if not instrument_hints:
        return None
    import copy
    configs = copy.deepcopy(INSTRUMENT_CONFIGS)
    notable = (instrument_hints.get("notable") or "").lower()
    category = (instrument_hints.get("category") or "").lower()
    adjusted = False

    # ── Apply genre profile overrides ──
    profile = GENRE_PROFILES.get(category)
    if profile:
        for renderer_key in ("bass_tab", "guitar_tab", "drum_tab", "note_list"):
            overrides = profile.get(renderer_key)
            if overrides and renderer_key in configs:
                configs[renderer_key].update(overrides)
                adjusted = True
        if adjusted:
            print(f"[hints] applied '{category}' genre profile to note detection configs")

    # ── Notable field tweaks (apply on top of genre profile) ──
    if any(kw in notable for kw in ("drop d", "drop c", "downtuned", "down-tuned", "drop tuning")):
        configs["guitar_tab"]["min_freq"] = 60
        configs["bass_tab"]["min_freq"] = 22
        print("[hints] adjusted guitar/bass freq range for drop tuning")
        adjusted = True

    if any(kw in notable for kw in ("slap", "high register bass", "slap bass")):
        configs["bass_tab"]["max_freq"] = 500
        print("[hints] expanded bass freq range for high register")
        adjusted = True

    if any(kw in notable for kw in ("extended range", "7-string", "8-string", "baritone")):
        configs["guitar_tab"]["min_freq"] = 50
        print("[hints] adjusted guitar freq range for extended range instrument")
        adjusted = True

    if any(kw in notable for kw in ("piccolo", "flute solo", "high register")):
        configs["note_list"]["max_freq"] = 5000
        print("[hints] expanded note_list freq range for high register content")
        adjusted = True

    return configs if adjusted else None


# ─── WAV I/O ─────────────────────────────────────────────────────────────────

def _read_wav(filepath):
    """
    Read an audio file (WAV, MP3, FLAC, etc.) via soundfile.
    Handles all WAV variants including WAVE_FORMAT_EXTENSIBLE (format tag 65534)
    which Python's wave module cannot read.

    Returns (left, right, sample_rate) as float32 arrays.
    Mono files return identical L/R.
    """
    import soundfile as sf
    data, sr = sf.read(str(filepath), dtype="float32")

    if data.ndim == 1:
        # Mono
        return data, data.copy(), sr
    else:
        # Stereo or multi-channel — take first two channels
        left, right = data[:, 0].copy(), data[:, 1].copy()
        del data
        return left, right, sr


def _write_wav(filepath, left, right, sr):
    """
    Write stereo 16-bit WAV with rounding + TPDF dither, streamed in chunks.

    Chunked because the whole-track version allocated ~815MB of transient on a
    5-minute track: np.random.random() returns FLOAT64 regardless of the audio's
    dtype, so two full-length noise arrays plus their difference dwarfed the
    float32 signal being dithered. On a 2GB instance that alone could OOM.

    Peak transient is now a function of CHUNK, not track length.
    """
    n = min(len(left), len(right))
    CHUNK = 1 << 20                      # ~1M frames ≈ 8MB of float32 work per pass
    rng = np.random.default_rng()

    with wave.open(str(filepath), "wb") as wf:
        wf.setnchannels(2)
        wf.setsampwidth(2)
        wf.setframerate(sr)
        for s in range(0, n, CHUNK):
            e = min(s + CHUNK, n)
            blk = np.empty((e - s, 2), dtype=np.float32)
            blk[:, 0] = left[s:e]
            blk[:, 1] = right[s:e]
            np.clip(blk, -1.0, 1.0, out=blk)
            blk *= 32767.0
            # TPDF dither, float32 end to end (Generator.random honours dtype)
            d = rng.random((e - s, 2), dtype=np.float32)
            d -= rng.random((e - s, 2), dtype=np.float32)
            blk += d
            np.round(blk, out=blk)
            np.clip(blk, -32768.0, 32767.0, out=blk)   # clip AFTER dither — no wraparound
            wf.writeframes(blk.astype(np.int16).tobytes())


def _rms(samples):
    """RMS energy of a signal."""
    if len(samples) == 0:
        return 0.0
    return float(np.sqrt(np.mean(samples ** 2)))


# ─── STFT Stereo Field Separation ────────────────────────────────────────────

def _stereo_separate(left, right):
    """
    Split stereo audio into center, left-panned, and right-panned components
    using STFT-domain panning analysis with soft Gaussian masks.

    Returns dict of position -> (left_ch, right_ch) for components with energy.
    """
    _log_mem(f"[stereo_separate] START (input={len(left)*4/1024/1024:.0f}MB per ch)")
    N = 4096
    hop = N // 4
    win = np.hanning(N).astype(np.float32)

    orig_len = len(left)
    pad = (hop - (orig_len % hop)) % hop + N
    left_p = np.pad(left, (0, pad)).astype(np.float32)
    right_p = np.pad(right, (0, pad)).astype(np.float32)
    out_len = len(left_p)

    n_frames = (out_len - N) // hop + 1

    # Output accumulators — float32.
    # NOTE: these were float16 to save ~177MB on a 5-min song. Measured cost of
    # that: overlap-add reconstruction SNR drops from 141 dB to 66 dB — roughly
    # 11-bit audio, i.e. 30 dB WORSE than the 16-bit PCM container it is written
    # into. The error is signal-correlated, so it sounds like gritty distortion
    # riding on the music (loudest in solo'd stems and decays), not like hiss.
    # If memory becomes the constraint again, block-process in 30s chunks with
    # N-sample overlap rather than reducing precision.
    c_l = np.zeros(out_len, dtype=np.float32)
    c_r = np.zeros(out_len, dtype=np.float32)
    p_ll = np.zeros(out_len, dtype=np.float32)
    p_lr = np.zeros(out_len, dtype=np.float32)
    p_rl = np.zeros(out_len, dtype=np.float32)
    p_rr = np.zeros(out_len, dtype=np.float32)
    win_sq = np.zeros(out_len, dtype=np.float32)
    win_f32 = win.astype(np.float32)

    # Gaussian mask parameters
    sigma_c = 0.12   # center width
    sigma_s = 0.18   # side width
    var_c = 2 * sigma_c ** 2
    var_s = 2 * sigma_s ** 2

    for i in range(n_frames):
        s = i * hop
        l_frame = left_p[s:s + N] * win
        r_frame = right_p[s:s + N] * win

        L = np.fft.rfft(l_frame)
        R = np.fft.rfft(r_frame)

        L_mag = np.abs(L)
        R_mag = np.abs(R)
        denom = L_mag + R_mag + 1e-10

        # Panning position per frequency bin: 0=left, 0.5=center, 1=right
        pan = R_mag / denom

        # Soft masks
        cm = np.exp(-((pan - 0.5) ** 2) / var_c)
        lm = np.exp(-(pan ** 2) / var_s)
        rm = np.exp(-((pan - 1.0) ** 2) / var_s)

        total = cm + lm + rm + 1e-10
        cm /= total
        lm /= total
        rm /= total

        # Reconstruct and overlap-add (float32 throughout)
        c_l[s:s + N] += (np.fft.irfft(L * cm, n=N) * win).astype(np.float32)
        c_r[s:s + N] += (np.fft.irfft(R * cm, n=N) * win).astype(np.float32)
        p_ll[s:s + N] += (np.fft.irfft(L * lm, n=N) * win).astype(np.float32)
        p_lr[s:s + N] += (np.fft.irfft(R * lm, n=N) * win).astype(np.float32)
        p_rl[s:s + N] += (np.fft.irfft(L * rm, n=N) * win).astype(np.float32)
        p_rr[s:s + N] += (np.fft.irfft(R * rm, n=N) * win).astype(np.float32)
        win_sq[s:s + N] += win_f32 ** 2

    # Release padded inputs
    del left_p, right_p
    _log_mem("[stereo_separate] after STFT loop")

    # Normalize overlap-add. The accumulators are already float32 (they were
    # float16 when this was written), so copy=False makes each of these a view
    # instead of a full-length copy — seven of them, ~50MB each on a 5-min track.
    # The .astype in the STFT loop above is NOT redundant: np.fft.irfft returns
    # float64, so that one is a real conversion.
    norm = np.maximum(win_sq[:orig_len].astype(np.float32, copy=False), 1e-8)
    del win_sq

    components = {}

    cl = c_l[:orig_len].astype(np.float32, copy=False) / norm
    cr = c_r[:orig_len].astype(np.float32, copy=False) / norm
    del c_l, c_r
    if _rms((cl + cr) / 2) > SILENCE_THRESHOLD:
        components["center"] = (cl, cr)

    ll = p_ll[:orig_len].astype(np.float32, copy=False) / norm
    lr = p_lr[:orig_len].astype(np.float32, copy=False) / norm
    del p_ll, p_lr
    if _rms(ll) > SILENCE_THRESHOLD * 0.5:
        components["left"] = (ll, lr)

    rl = p_rl[:orig_len].astype(np.float32, copy=False) / norm
    rr = p_rr[:orig_len].astype(np.float32, copy=False) / norm
    del p_rl, p_rr, norm
    if _rms(rr) > SILENCE_THRESHOLD * 0.5:
        components["right"] = (rl, rr)

    import gc as _gc_stereo
    _gc_stereo.collect()
    _log_mem("[stereo_separate] END")
    return components


# ─── Spectral Feature Extraction ─────────────────────────────────────────────

def _spectral_features(mono, sr):
    """Compute spectral features for instrument classification."""
    frame_size = 2048
    hop = 1024
    n_frames = (len(mono) - frame_size) // hop
    if n_frames <= 0:
        return {"centroid": 0, "centroid_std": 0, "bandwidth": 0, "zcr": 0, "rms": _rms(mono)}

    win = np.hanning(frame_size)
    freqs = np.fft.rfftfreq(frame_size, 1.0 / sr)

    centroids = []
    bandwidths = []

    # Sample up to 600 frames for speed
    step = max(1, n_frames // 600)
    for i in range(0, n_frames, step):
        start = i * hop
        frame = mono[start:start + frame_size]
        if len(frame) < frame_size:
            break
        spectrum = np.abs(np.fft.rfft(frame * win))
        total = spectrum.sum()
        if total < 1e-10:
            continue
        centroid = np.sum(freqs * spectrum) / total
        centroids.append(centroid)
        bw = np.sqrt(np.sum(((freqs - centroid) ** 2) * spectrum) / total)
        bandwidths.append(bw)

    zcr = float(np.sum(np.abs(np.diff(np.sign(mono)))) / max(1, 2.0 * len(mono)))

    return {
        "centroid": float(np.mean(centroids)) if centroids else 0,
        "centroid_std": float(np.std(centroids)) if centroids else 0,
        "bandwidth": float(np.mean(bandwidths)) if bandwidths else 0,
        "zcr": zcr,
        "rms": _rms(mono),
    }


# ─── Instrument Classification ───────────────────────────────────────────────

def _classify_component(features, stem_category, position):
    """
    Classify a stereo component into a musically meaningful label.
    Uses spectral features + Demucs stem category + stereo position.
    """
    c = features["centroid"]
    bw = features["bandwidth"]
    zcr = features["zcr"]
    c_std = features["centroid_std"]

    if stem_category == "vocals":
        if position == "center":
            return "Vocals"
        return "Backing Vocals"

    if stem_category == "guitar":
        # Only sub-classify when spectral evidence is unambiguous (acoustic guitar).
        # No Lead/Rhythm distinction is made — spectral centroid alone is too
        # unreliable and causes double-numbering artifacts.
        if c > 2500 and bw > 1400 and zcr > 0.12:
            return "Acoustic Guitar"
        return "Guitar"

    if stem_category == "other":
        if c > 2500 and bw > 1400 and zcr > 0.12:
            return "Acoustic Guitar"
        if c > 1500 and bw > 1800:
            return "Synth"
        if c > 800:
            return "Atmosphere"
        return "Pad"

    if stem_category == "piano":
        if c > 1500 and bw > 1500:
            return "Piano"
        return "Keyboard"

    return stem_category.title()


def _get_tab_renderer(label):
    """Map a classified instrument label to the right tab renderer."""
    label_lower = label.lower()
    if "vocal" in label_lower and "vocoder" not in label_lower:
        return "note_list"
    if any(k in label_lower for k in ("drum", "808 kick", "percussion", "timpani", "tabla")):
        return "drum_tab"
    # Bass variants — sub bass, acid bass, walking bass, 808 bass all get bass_tab
    if "bass" in label_lower:
        return "bass_tab"
    if any(k in label_lower for k in ("guitar", "banjo", "mandolin", "ukulele",
                                       "pedal steel", "fiddle")):
        return "guitar_tab"
    # Everything pitched that isn't bass/guitar/drums → note_list
    return "note_list"


def apply_instrument_hints(stems: dict, instrument_hints: dict | None) -> dict:
    """
    Post-process refined stems using LLM instrument predictions.
    Adjusts labels for vague "other" bucket classifications.
    Handles both band and electronic/production music vocabulary.

    Mutates and returns the stems dict.
    """
    if not instrument_hints:
        return stems

    predicted = [i.lower() for i in instrument_hints.get("instruments", [])]
    if not predicted:
        return stems

    category = (instrument_hints.get("category") or "").lower()
    predicted_str = " ".join(predicted)

    reclassified = 0
    for key, stem in stems.items():
        label = stem["label"].lower()

        # Only reclassify vague labels from the "other" bucket
        if label not in ("pad", "atmosphere", "synth", "other"):
            continue

        old_label = stem["label"]
        new_label = _match_predicted_label(predicted, predicted_str, category, features=None)
        if new_label and new_label.lower() != label:
            stem["label"] = new_label
            print(f'[hints] reclassified "{old_label}" → "{new_label}" (stem: {key})')
            reclassified += 1

    if reclassified:
        print(f"[hints] reclassified {reclassified} stem(s) using instrument predictions")
    else:
        print("[hints] no stems reclassified (labels already specific or no match)")

    return stems


def _match_predicted_label(predicted: list, predicted_str: str, category: str, features: dict | None) -> str | None:
    """
    Match predicted instruments to a display label.
    Order: most specific first → genre-specific → generic fallbacks.
    Returns None if no confident match.
    """
    # ── Electronic / production ──
    if any(s in predicted for s in ("synth lead", "lead synth", "synth melody")):
        return "Synth Lead"
    if any(s in predicted for s in ("synth pad", "pad synth", "ambient pad", "shimmer pad")):
        return "Synth Pad"
    if any(s in predicted for s in ("pluck synth", "pluck", "synth pluck")):
        return "Pluck"
    if any(s in predicted for s in ("arpeggiator", "arp", "synth arp")):
        return "Arp"
    if any(s in predicted for s in ("acid bassline", "acid", "303")):
        return "Acid Bass"
    if any(s in predicted for s in ("sub bass", "sub-bass", "808 bass", "808")):
        return "Sub Bass"
    if any(s in predicted for s in ("fx", "fx/riser", "riser", "sweep", "impact", "transition")):
        return "FX"
    if any(s in predicted for s in ("vocoder", "talkbox", "vocal synth")):
        return "Vocoder"
    if any(s in predicted for s in ("sampled chops", "vocal chops", "chops", "sampled loop")):
        return "Sample"
    if any(s in predicted for s in ("synth bells", "bells", "chime")):
        return "Bells"
    if any(s in predicted for s in ("granular texture", "granular", "texture")):
        return "Texture"
    if any(s in predicted for s in ("field recording", "ambient noise")):
        return "Field Recording"

    # ── Jazz / soul / funk ──
    if any(s in predicted for s in ("rhodes", "rhodes piano", "wurlitzer", "fender rhodes")):
        return "Rhodes"
    if any(s in predicted for s in ("vibraphone", "vibes", "marimba")):
        return "Vibraphone" if "vibraphone" in predicted_str or "vibes" in predicted_str else "Marimba"
    if any(s in predicted for s in ("horn section", "horns")):
        return "Horns"

    # ── Classical / orchestral ──
    if any(s in predicted for s in ("violin section", "violin", "viola")):
        return "Strings"
    if any(s in predicted for s in ("cello section", "cello", "contrabass")):
        return "Strings"
    if any(s in predicted for s in ("strings", "string section", "orchestra", "orchestral strings")):
        return "Strings"
    if any(s in predicted for s in ("french horn", "timpani")):
        return "Brass" if "french horn" in predicted_str else "Percussion"
    if any(s in predicted for s in ("harp",)):
        return "Harp"
    if any(s in predicted for s in ("oboe", "flute", "clarinet", "bassoon", "woodwind", "woodwind section")):
        return "Woodwind"

    # ── World / Latin / global ──
    if any(s in predicted for s in ("sitar", "tabla", "tanpura", "sarangi")):
        return "Sitar" if "sitar" in predicted_str else "Tabla"
    if any(s in predicted for s in ("congas", "bongos", "timbales", "djembe")):
        return "Percussion"
    if any(s in predicted for s in ("steel drums", "steel pan")):
        return "Steel Drums"
    if any(s in predicted for s in ("kora", "balafon", "kalimba", "mbira")):
        return "Kora" if "kora" in predicted_str else "Kalimba"
    if any(s in predicted for s in ("accordion", "bandoneon", "concertina")):
        return "Accordion"

    # ── Singer-songwriter / folk / country ──
    if any(s in predicted for s in ("harmonica", "mouth harp")):
        return "Harmonica"
    if any(s in predicted for s in ("banjo",)):
        return "Banjo"
    if any(s in predicted for s in ("pedal steel", "lap steel", "steel guitar")):
        return "Pedal Steel"
    if any(s in predicted for s in ("fiddle",)):
        return "Fiddle"

    # ── Common across genres ──
    if any(s in predicted for s in ("organ", "hammond", "b3", "hammond organ")):
        return "Organ"
    if any(s in predicted for s in ("brass", "trumpet", "horn", "trombone", "saxophone", "brass section")):
        return "Brass"

    # ── Generic synth fallback ──
    if category in ("electronic", "hiphop", "ambient") or any(s in predicted for s in ("synth", "synthesizer")):
        return "Synth"

    return None


# ─── Main Separation Pipeline ────────────────────────────────────────────────

def _separate_stems_local(audio_path: Path, out_dir: Path, progress_callback=None) -> tuple[dict, str]:
    """
    Run Demucs locally as subprocess. Returns (raw_stems, model_name).
    raw_stems: {stem_name: path_to_raw_wav}
    """
    import os as _os
    hosted = _os.getenv("USE_HOSTED_SEPARATION", "false").strip().lower() in ("true", "1", "yes")
    if hosted:
        raise RuntimeError("Local separation blocked: USE_HOSTED_SEPARATION is enabled. "
                           "Local Demucs must not run in hosted mode.")

    import time as _time
    _t0 = _time.time()

    model = DEMUCS_MODEL
    stem_names = STEM_NAMES_6

    if progress_callback:
        progress_callback("Running Demucs separation (local)...")

    DEMUCS_TIMEOUT = 600

    print(f"[separation] LOCAL starting: model={model}")
    try:
        result = subprocess.run(
            [sys.executable, "-m", "demucs", "--out", str(out_dir), "--name", model, str(audio_path)],
            capture_output=True, text=True, timeout=DEMUCS_TIMEOUT,
        )
    except subprocess.TimeoutExpired:
        raise RuntimeError(f"Demucs timed out after {DEMUCS_TIMEOUT}s")

    # Fallback to 4-stem if 6-stem fails
    if result.returncode != 0 and model == "htdemucs_6s":
        print(f"[separation] 6-stem failed, trying 4-stem fallback. stderr: {result.stderr[-200:]}")
        model = "htdemucs"
        stem_names = STEM_NAMES_4
        try:
            result = subprocess.run(
                [sys.executable, "-m", "demucs", "--out", str(out_dir), "--name", model, str(audio_path)],
                capture_output=True, text=True, timeout=DEMUCS_TIMEOUT,
            )
        except subprocess.TimeoutExpired:
            raise RuntimeError(f"Demucs fallback timed out after {DEMUCS_TIMEOUT}s")

    if result.returncode != 0:
        raise RuntimeError(f"Demucs failed:\n{result.stderr}")

    song_name = audio_path.stem
    stem_dir = out_dir / model / song_name

    raw_stems = {}
    for stem_name in stem_names:
        stem_file = stem_dir / f"{stem_name}.wav"
        if stem_file.exists():
            dest = out_dir / f"_raw_{stem_name}.wav"
            shutil.copy2(stem_file, dest)
            raw_stems[stem_name] = str(dest)

    elapsed = _time.time() - _t0
    print(f"[separation] LOCAL finished in {elapsed:.1f}s → {len(raw_stems)} stems: {list(raw_stems.keys())}")
    return raw_stems, model


def _separate_stems_replicate(audio_path: Path, out_dir: Path, progress_callback=None,
                              report: dict | None = None) -> tuple[dict, str]:
    """
    Run Demucs via Replicate REST API. Returns (raw_stems, model_name).
    raw_stems: {stem_name: path_to_raw_wav}

    Uses the REST API directly (not the replicate Python client) for full control
    over the request/response lifecycle and transparent error handling.

    Verified model: ryan5453/demucs
    Verified version: 5a7041cc9b82e5a558fea6b3d7b12dea89625e89da33f0447bd727c2d0ab9e77
    API schema + live 2s test confirmed via /v1/models/ryan5453/demucs — 2026-07-08
    (previous model cjwbw/demucs began failing 100% with Replicate E1001 errors)

    Input params (verified from openapi_schema):
      audio:         file URI or URL (required)
      model:         "htdemucs" | "htdemucs_ft" | "htdemucs_6s" | etc.
      output_format: "mp3" | "flac" | "wav" (default mp3)
      shifts:        int (default 1)

    Output (verified from openapi_schema):
      dict with keys: bass, drums, other, piano, guitar, vocals
      each value is a URI string pointing to the separated audio file

    report: optional dict, populated with
      "stem_failures" {stem: error} — model gave a URL, we could not fetch it
                                      (after one retry). A missing instrument.
      "omitted"       [stem, ...]   — model returned no URL. Not a failure.
    """
    import os
    import time as _time
    import requests as _requests

    # Cleared per attempt: separate_stems() may call this up to 3 times, and a
    # later attempt must not inherit an earlier one's failures.
    if report is not None:
        report.clear()

    token = os.getenv("REPLICATE_API_TOKEN", "").strip()
    if not token:
        raise RuntimeError("REPLICATE_API_TOKEN not set")

    _t0 = _time.time()
    VERSION = REPLICATE_DEMUCS_VERSION
    POLL_INTERVAL = 3  # seconds between status checks (reduced from 5)

    # Duration-aware timeout. The old flat 420s ceiling caused long songs to
    # fail: GPU time scales with track length, and a *normal* song was observed
    # completing at 376s (cold start included). Base 420s covers cold start +
    # a typical ~4-min song; add 90s of headroom per minute of audio beyond
    # 4 minutes, capped at 25 min so a hung prediction can't stall the queue.
    # NOTE: this is the ceiling for ONE separation attempt, not an estimate of
    # how long analysis takes (p50 is ~147s end to end). Up to MAX_RETRIES
    # attempts can each spend this long, which is why the frontend's copy is
    # driven by the p50 and not by this number.
    _track_dur_s = _probe_duration_s(audio_path)
    _extra_wait = max(0.0, _track_dur_s - 240.0) / 60.0 * 90.0
    MAX_WAIT = int(min(420 + _extra_wait, 1500))
    if _track_dur_s > 0:
        print(f"[replicate] track duration {_track_dur_s:.0f}s → MAX_WAIT={MAX_WAIT}s")
    else:
        print(f"[replicate] track duration unknown → MAX_WAIT={MAX_WAIT}s (default)")

    # Expected stems from the model output
    EXPECTED_STEMS = {"vocals", "drums", "bass", "guitar", "piano", "other"}

    headers = {
        "Authorization": f"Bearer {token}",
        "Content-Type": "application/json",
    }

    if progress_callback:
        progress_callback("Running stem separation (cloud)...")

    print(f"[replicate] starting: {audio_path.name}")
    print(f"[replicate] model = ryan5453/demucs")
    print(f"[replicate] version = {VERSION[:16]}...")

    # ── Step 1: Upload audio file to Replicate ──
    # Upload the source as-is. This used to pre-transcode to 128kbps MP3 "to
    # halve upload size". That was a bad trade on both axes:
    #   quality — 128k CBR lowpasses ~16kHz AND applies intensity stereo above
    #     ~6kHz, which collapses HF stereo imaging. Those are exactly the cues
    #     Demucs uses to pull sources apart, and exactly the information
    #     _stereo_separate() later tries to read back out of the panning field.
    #   speed  — measured ~3s on a fast core (est. 8-15s on Render) to save
    #     ~3.5MB of upload, i.e. well under 1s of transfer. Net slower.
    upload_path = audio_path
    _transcode_tmp = None

    file_size = upload_path.stat().st_size
    suffix = upload_path.suffix.lower().lstrip(".")
    mime = {"mp3": "audio/mpeg", "wav": "audio/wav", "m4a": "audio/mp4",
            "ogg": "audio/ogg", "flac": "audio/flac"}.get(suffix, "audio/mpeg")
    print(f"[replicate] uploading audio: {upload_path.name} ({file_size} bytes, {mime})")

    try:
        upload_resp = _requests.post(
            f"{REPLICATE_API}/files",
            headers={"Authorization": f"Bearer {token}"},
            files={"content": (upload_path.name, open(upload_path, "rb"), mime)},
            timeout=300,
        )
    finally:
        # Clean up transcode temp file regardless of upload outcome
        if _transcode_tmp and _transcode_tmp.exists():
            try:
                _transcode_tmp.unlink()
            except Exception:
                pass
    if not upload_resp.ok:
        raise RuntimeError(f"Replicate file upload failed (HTTP {upload_resp.status_code}): {upload_resp.text[:300]}")

    file_url = upload_resp.json().get("urls", {}).get("get")
    if not file_url:
        raise RuntimeError(f"Replicate file upload returned no URL: {upload_resp.text[:200]}")
    print(f"[replicate] file uploaded → {file_url[:80]}")

    # ── Step 2: Create prediction using file URL ──
    payload = {
        "version": VERSION,
        "input": {
            "audio": file_url,
            "model": "htdemucs_6s",  # 6-stem: vocals/drums/bass/guitar/piano/other
            # flac, not mp3: lossless and typically ~50-60% of WAV size. Separator
            # output is full of residue/artifacts, which is exactly the signal MP3
            # handles worst — and we were spending a whole lossy generation here
            # to save a few MB per stem.
            "output_format": "flac",
            "shifts": 1,
        },
    }

    print(f"[replicate] creating prediction...")
    resp = _requests.post(f"{REPLICATE_API}/predictions", headers=headers, json=payload, timeout=30)

    if resp.status_code == 422:
        err = resp.json().get("detail", resp.text[:200])
        raise RuntimeError(f"Replicate rejected request (422): {err}")
    if resp.status_code == 402:
        raise RuntimeError("Replicate account has insufficient credit. Add billing at replicate.com/account/billing")
    if not resp.ok:
        raise RuntimeError(f"Replicate API error (HTTP {resp.status_code}): {resp.text[:300]}")

    prediction = resp.json()
    pred_id = prediction["id"]
    print(f"[replicate] prediction created: id={pred_id} status={prediction['status']}")

    # ── Step 3: Poll until complete ──
    poll_start = _time.time()
    while True:
        elapsed_poll = _time.time() - poll_start
        if elapsed_poll > MAX_WAIT:
            # Cancel the prediction so Replicate doesn't keep burning credits on a job we've given up on
            try:
                _requests.post(
                    f"{REPLICATE_API}/predictions/{pred_id}/cancel",
                    headers=headers, timeout=5,
                )
                print(f"[replicate] prediction {pred_id} cancelled after timeout")
            except Exception:
                pass
            raise RuntimeError(f"Replicate prediction timed out after {int(elapsed_poll)}s")

        _time.sleep(POLL_INTERVAL)

        poll_resp = _requests.get(f"{REPLICATE_API}/predictions/{pred_id}", headers=headers, timeout=15)
        if not poll_resp.ok:
            raise RuntimeError(f"Replicate poll failed (HTTP {poll_resp.status_code}): {poll_resp.text[:200]}")

        pred = poll_resp.json()
        status = pred["status"]
        print(f"[replicate] poll: status={status} ({int(elapsed_poll)}s elapsed)")

        if progress_callback:
            progress_callback(f"Separating stems ({int(elapsed_poll)}s)...")

        if status == "succeeded":
            break
        elif status in ("failed", "canceled"):
            error_msg = pred.get("error", "Unknown error")
            raise RuntimeError(f"Replicate prediction {status}: {error_msg}")
        # else: "starting" or "processing" — keep polling

    # ── Step 4: Download output stems ──
    output = pred.get("output")
    if not output or not isinstance(output, dict):
        raise RuntimeError(f"Replicate returned unexpected output type: {type(output).__name__} = {str(output)[:200]}")

    print(f"[replicate] output received: keys={list(output.keys())}")

    # Download whatever stems Replicate returned — do not require a fixed set.
    # htdemucs returns 4 stems (vocals, drums, bass, other).
    # htdemucs_6s returns 6 (adds guitar, piano) but some may be missing.
    _KNOWN_STEMS = {"vocals", "drums", "bass", "guitar", "piano", "other"}

    # Download + convert the stems in parallel. This is network wait time, which
    # overlaps fine on 1 vCPU — while one stem's ffmpeg runs, the others are still
    # streaming bytes. It is also what makes the FLAC output format affordable:
    # ~32MB/stem vs ~5.6MB for MP3, so six sequential downloads cost ~25s.
    #
    # The pool is created and joined INSIDE this function. The previous comment
    # here warned about "cannot schedule new futures after interpreter shutdown",
    # but that only bites a pool that outlives the request and is submitted to
    # after gunicorn has begun recycling the worker. A locally-scoped pool that
    # the `with` block joins before returning cannot reach that state.
    from concurrent.futures import ThreadPoolExecutor as _TPE, as_completed as _as_done

    raw_stems = {}
    dl_items = [(k, v) for k, v in output.items()]
    if progress_callback:
        progress_callback(f"Downloading stems (0/{len(dl_items)})...")

    # Two different things used to look identical from the outside:
    #   omitted — the model returned no URL for this stem. Not our failure, and
    #             not necessarily wrong (htdemucs 4-stem has no guitar/piano).
    #   failed  — the model DID return a URL and we could not fetch or convert
    #             it. That is a missing instrument the user was meant to get,
    #             and the caller has to know about it.
    omitted = []
    stem_failures = {}

    def _fetch_stem(stem_key, url):
        """Download one stem and convert it to WAV.

        Returns (stem_name, wav_path, error). Exactly one of wav_path/error is
        set, except for an omitted stem, where both are None. Swallows its own
        exceptions on purpose: one failed stem must not take down the other five.
        """
        stem_name = stem_key if stem_key in _KNOWN_STEMS else stem_key
        if not url or not isinstance(url, str):
            print(f"[replicate] stem '{stem_key}': model returned no URL")
            return stem_name, None, None
        # extension follows output_format above; ffmpeg sniffs content either way
        src_tmp = out_dir / f"_raw_{stem_name}.flac"
        wav_dest = out_dir / f"_raw_{stem_name}.wav"
        # Convert to a .part file and rename only once the result is known good.
        # ffmpeg cannot infer the muxer from ".part", hence the explicit -f wav.
        # Without this, a failed or timed-out conversion left a partial
        # _raw_*.wav behind: the later sweep only runs after Replicate returns
        # successfully, so all-failure and pool-abort paths leaked it.
        wav_tmp = out_dir / f"_raw_{stem_name}.wav.part"
        print(f"[replicate] downloading: {stem_name} → {src_tmp.name}")
        try:
            with _requests.get(url, stream=True, timeout=120) as dl_resp:
                dl_resp.raise_for_status()
                byte_count = 0
                with open(src_tmp, "wb") as f:
                    for chunk in dl_resp.iter_content(chunk_size=1024 * 1024):  # 1MB chunks
                        f.write(chunk)
                        byte_count += len(chunk)
            print(f"[replicate] downloaded: {stem_name} ({byte_count:,} bytes) — converting to wav")
            # Convert → WAV via ffmpeg subprocess — avoids loading ~190MB float32
            # array per stem into Python memory. ffmpeg streams the conversion and
            # holds no GIL, so it overlaps with the other stems' downloads.
            # Timeout is 120s, not 60s: six ffmpegs sharing one core each take
            # correspondingly longer in wall-clock terms.
            _conv = subprocess.run(
                ["ffmpeg", "-y", "-i", str(src_tmp), "-ar", "44100", "-ac", "2",
                 "-f", "wav", str(wav_tmp)],
                capture_output=True, timeout=120,
            )
            if _conv.returncode != 0:
                raise RuntimeError(f"ffmpeg → WAV failed: {_conv.stderr[-300:]}")
            if not wav_tmp.exists() or wav_tmp.stat().st_size <= 1000:
                raise RuntimeError("ffmpeg produced an empty or truncated WAV")
            os.replace(wav_tmp, wav_dest)   # atomic within the same directory
            print(f"[replicate] saved: {stem_name}")
            return stem_name, str(wav_dest), None
        except Exception as e:
            print(f"[replicate] stem '{stem_key}' FAILED: {e}")
            return stem_name, None, str(e)
        finally:
            for _tmp in (src_tmp, wav_tmp):
                try:
                    _tmp.unlink(missing_ok=True)
                except Exception:
                    pass

    def _run_pass(items, label):
        """Fetch `items` concurrently. Returns {stem: error} for the failures.

        raw_stems is only written from this thread (in the as_completed loop),
        so the workers never touch shared state.
        """
        failures = {}
        with _TPE(max_workers=6) as _dl_pool:
            _futs = [_dl_pool.submit(_fetch_stem, k, u) for k, u in items]
            for _done, _fut in enumerate(_as_done(_futs), start=1):
                _name, _path, _err = _fut.result()
                if _path:
                    raw_stems[_name] = _path
                elif _err:
                    failures[_name] = _err
                else:
                    omitted.append(_name)
                if progress_callback:
                    progress_callback(f"Downloading stems{label} ({_done}/{len(items)})...")
        return failures

    _failed = _run_pass(dl_items, "")

    # One retry for anything that failed. These are transient by nature —
    # a socket reset or a timed-out ffmpeg — and a stem lost here is an
    # instrument missing from the mix, which is worth a few extra seconds.
    if _failed:
        _retry_items = [(k, u) for k, u in dl_items if k in _failed]
        print(f"[replicate] retrying {len(_retry_items)} failed stem(s): {sorted(_failed)}")
        if progress_callback:
            progress_callback(f"Retrying {len(_retry_items)} stem(s)...")
        stem_failures = _run_pass(_retry_items, " (retry)")
        for _name in set(_failed) - set(stem_failures):
            print(f"[replicate] {_name} recovered on retry")
    if omitted:
        print(f"[replicate] model returned no URL for: {sorted(set(omitted))}")

    if report is not None:
        report["stem_failures"] = dict(stem_failures)
        report["omitted"] = sorted(set(omitted))

    if not raw_stems:
        # Keep the underlying errors in the message. The retry classifier in
        # separate_stems() matches on strings like "timed out" / "(E1001)", so
        # collapsing everything into "no downloadable stems" made a wholly
        # retryable failure look permanent.
        detail = "; ".join(f"{k}: {v}" for k, v in sorted(stem_failures.items())) \
            or "model returned no stem URLs"
        raise RuntimeError(f"Replicate returned no downloadable stems ({detail})")

    if stem_failures:
        print(f"[replicate] INCOMPLETE — {len(stem_failures)} stem(s) missing after retry: "
              f"{sorted(stem_failures)}")

    elapsed = _time.time() - _t0
    print(f"[replicate] COMPLETE in {elapsed:.1f}s → {len(raw_stems)} stems: {list(raw_stems.keys())}")
    return raw_stems, "replicate_htdemucs"


def separate_stems(audio_path: str, song_id: str, progress_callback=None,
                   instrument_hints: dict | None = None, report: dict | None = None) -> dict:
    """
    Full pipeline:
      1. Run Demucs for initial separation (hosted or local)
      2. Analyze stereo field of each stem
      3. Split into individually panned components
      4. Classify each component
      5. Return only stems with meaningful audio content

    Returns dict: {stem_key: {path, energy, active, label}}

    report: optional dict. Populated on the hosted path with "stem_failures" and
      "omitted" (see _separate_stems_replicate). The caller needs this to tell a
      genuinely 4-stem result apart from a 6-stem result that lost two stems to
      transient download failures — the second must not be cached as complete.
    """
    import os
    _ensure_imports()
    audio_path = Path(audio_path)
    out_dir = OUTPUT_DIR / song_id / "stems"
    out_dir.mkdir(parents=True, exist_ok=True)

    # ── Step 1: Stem separation (hosted or local) ──
    hosted_raw = os.getenv("USE_HOSTED_SEPARATION", "false").strip()
    use_hosted = hosted_raw.lower() in ("true", "1", "yes")
    has_token = bool(os.getenv("REPLICATE_API_TOKEN", "").strip())

    print(f"[separation] USE_HOSTED_SEPARATION = {hosted_raw!r} → use_hosted={use_hosted}")
    print(f"[separation] HAS_REPLICATE_TOKEN = {has_token}")

    if use_hosted and not has_token:
        raise RuntimeError("USE_HOSTED_SEPARATION is enabled but REPLICATE_API_TOKEN is missing. "
                           "Set the token or disable hosted separation.")

    if use_hosted:
        print(f"[separation] path = replicate (hosted-only, no local fallback)")
        MAX_RETRIES = 3
        last_error = None
        for attempt in range(1, MAX_RETRIES + 1):
            try:
                if attempt > 1:
                    import time as _retry_time
                    wait = 5 * attempt  # 10s, 15s backoff
                    print(f"[separation] retry {attempt}/{MAX_RETRIES} in {wait}s...")
                    if progress_callback:
                        progress_callback(f"Retrying stem separation (attempt {attempt})...")
                    _retry_time.sleep(wait)
                raw_stems, model = _separate_stems_replicate(audio_path, out_dir, progress_callback,
                                                             report=report)
                if attempt > 1:
                    print(f"[separation] succeeded on attempt {attempt}")
                last_error = None
                break
            except Exception as e:
                last_error = e
                err_str = str(e)
                import re as _re
                # Retry on GPU preemption (code: PA), transient API failures, or network errors.
                # "timed out" / "connection aborted" cover socket write timeouts during upload.
                # "director" / "(E####)" cover Replicate-internal worker crashes like
                # "Director: unexpected error handling prediction (E1001)" — observed in
                # production; a retry reschedules onto a different worker and usually succeeds.
                is_retryable = (
                    "code: PA" in err_str
                    or "interrupted" in err_str.lower()
                    or "starting" in err_str.lower()
                    or "timed out" in err_str.lower()
                    or "time out" in err_str.lower()
                    or "connection aborted" in err_str.lower()
                    or "connectionerror" in err_str.lower()
                    or "remotedisconnected" in err_str.lower()
                    or "director" in err_str.lower()
                    or "unexpected error" in err_str.lower()
                    or bool(_re.search(r"\(E\d{3,5}\)", err_str))
                )
                if is_retryable and attempt < MAX_RETRIES:
                    print(f"[separation] attempt {attempt} failed (retryable): {e}")
                    continue
                else:
                    print(f"[separation] REPLICATE FAILED — aborting (no fallback): {e}")
                    raise RuntimeError(f"Cloud stem separation failed: {e}")
        if last_error:
            raise RuntimeError(f"Cloud stem separation failed after {MAX_RETRIES} attempts: {last_error}")
    else:
        print(f"[separation] path = local")
        raw_stems, model = _separate_stems_local(audio_path, out_dir, progress_callback)

    _log_mem(f"[separate_stems] post-demucs ({len(raw_stems)} raw stems)")
    # ── Step 2: Refine each stem ──
    if progress_callback:
        progress_callback("Analyzing instruments...")

    refined = {}

    # Build per-stem energy overrides from instrument hints.
    # Genre profiles define baseline overrides; boolean flags add instrument-specific tweaks.
    energy_overrides = {}
    if instrument_hints:
        category = (instrument_hints.get("category") or "").lower()

        # Apply genre profile energy overrides first (from GENRE_PROFILES)
        profile = GENRE_PROFILES.get(category, {})
        profile_energy = profile.get("energy", {})
        if profile_energy:
            for stem_key, thresholds in profile_energy.items():
                energy_overrides[stem_key] = dict(thresholds)
            print(f"[hints] applied '{category}' energy profile: {list(profile_energy.keys())}")

        # Layer on instrument-specific overrides from boolean flags
        if instrument_hints.get("has_piano"):
            energy_overrides.setdefault("piano", {})
            energy_overrides["piano"].update({"min_relative": 0.10, "min_absolute": 0.004})
            print("[hints] lowered piano energy threshold (predicted present)")
        if instrument_hints.get("has_strings") or instrument_hints.get("has_brass"):
            energy_overrides.setdefault("other", {})
            energy_overrides["other"].update({"min_relative": 0.10, "min_absolute": 0.004})
            print("[hints] lowered 'other' energy threshold (strings/brass predicted)")
        if instrument_hints.get("has_acoustic_guitar"):
            energy_overrides.setdefault("guitar", {})
            energy_overrides["guitar"].update({"min_relative": 0.12, "min_absolute": 0.004})
            print("[hints] adjusted guitar energy threshold (acoustic guitar predicted)")
        if instrument_hints.get("has_sub_bass") or instrument_hints.get("has_808"):
            energy_overrides.setdefault("bass", {})
            energy_overrides["bass"].update({"min_relative": 0.06, "min_absolute": 0.002})
            print("[hints] lowered bass energy threshold (sub bass/808 predicted)")

    # Cap total refined stems to prevent OOM on complex songs (e.g. Layla → 14 stems).
    # Demucs returns 6 raw stems; stereo separation can multiply this to 15+.
    # Each sub-stem is a full-length WAV — uncapped, a 7-min song creates 2GB+ of temp files.
    # Drums and bass always get slots; remaining 8 slots go to pitched stems by energy.
    MAX_REFINED_STEMS = 10

    import gc as _gc

    try:  # try/finally ensures _raw_* intermediates are cleaned even on crash
      for stem_name, raw_path in raw_stems.items():
        # Drums and bass: keep as-is (Demucs handles these well)
        if stem_name in ("drums", "bass"):
            dest = out_dir / f"{stem_name}.wav"
            shutil.copy2(raw_path, dest)
            left, right, sr = _read_wav(raw_path)
            energy = _rms((left + right) / 2)
            del left, right
            refined[stem_name] = {
                "path": str(dest),
                "energy": round(energy, 6),
                "active": energy > SILENCE_THRESHOLD,
                "label": "Drums" if stem_name == "drums" else "Bass",
            }
            continue

        # For vocals, guitar, piano, other: do stereo analysis
        try:
            left, right, sr = _read_wav(raw_path)
        except Exception:
            # Fallback: keep as-is
            dest = out_dir / f"{stem_name}.wav"
            shutil.copy2(raw_path, dest)
            refined[stem_name] = {
                "path": str(dest),
                "energy": 0.01,
                "active": True,
                "label": stem_name.title(),
            }
            continue

        stem_energy = _rms((left + right) / 2)
        if stem_energy < SILENCE_THRESHOLD:
            del left, right
            continue  # Skip entirely silent stems

        if progress_callback:
            progress_callback(f"Analyzing {stem_name}...")

        # Split by stereo panning
        components = _stereo_separate(left, right)

        # Check cap before any further splitting — if we're already at the limit,
        # keep this stem as-is rather than creating more WAV files.
        if len(refined) >= MAX_REFINED_STEMS:
            dest = out_dir / f"{stem_name}.wav"
            shutil.copy2(raw_path, dest)
            del left, right
            refined[stem_name] = {
                "path": str(dest),
                "energy": round(stem_energy, 6),
                "active": True,
                "label": stem_name.title(),
            }
            print(f"[processor] stem cap reached ({MAX_REFINED_STEMS}) — keeping {stem_name} as-is")
            continue

        if not components:
            # No meaningful separation — keep original
            dest = out_dir / f"{stem_name}.wav"
            shutil.copy2(raw_path, dest)
            mono = (left + right) / 2
            del left, right
            feat = _spectral_features(mono, sr)
            del mono
            label = _classify_component(feat, stem_name, "center")
            refined[stem_name] = {
                "path": str(dest),
                "energy": round(stem_energy, 6),
                "active": True,
                "label": label,
            }
            continue
        del left, right  # No longer needed after stereo separation

        # Classify each component
        sub_parts = []
        for position, (comp_l, comp_r) in components.items():
            mono = (comp_l + comp_r) / 2
            energy = _rms(mono)

            # Skip components that are too quiet (relative or absolute)
            # Use per-stem energy overrides from instrument hints if available
            _overrides = energy_overrides.get(stem_name, {})
            _rel_thresh = _overrides.get("min_relative", MIN_RELATIVE_ENERGY)
            _abs_thresh = _overrides.get("min_absolute", MIN_ABSOLUTE_ENERGY)
            if energy < stem_energy * _rel_thresh or energy < _abs_thresh:
                continue

            feat = _spectral_features(mono, sr)
            label = _classify_component(feat, stem_name, position)
            sub_parts.append({
                "position": position,
                "left": comp_l,
                "right": comp_r,
                "energy": energy,
                "label": label,
                "features": feat,
            })

        if not sub_parts:
            # All components too quiet — use original
            dest = out_dir / f"{stem_name}.wav"
            shutil.copy2(raw_path, dest)
            refined[stem_name] = {
                "path": str(dest),
                "energy": round(stem_energy, 6),
                "active": True,
                "label": stem_name.title(),
            }
            continue

        # If only one component, don't split — just relabel
        if len(sub_parts) == 1:
            part = sub_parts[0]
            key = _label_to_key(part["label"])
            dest = out_dir / f"{key}.wav"
            _write_wav(dest, part["left"], part["right"], sr)
            refined[key] = {
                "path": str(dest),
                "energy": round(part["energy"], 6),
                "active": True,
                "label": part["label"],
            }
            continue

        # Multiple components — save each, but respect the cap.
        # Trim sub_parts to fit within remaining slots so we don't blow past the limit.
        remaining_slots = MAX_REFINED_STEMS - len(refined)
        if len(sub_parts) > remaining_slots:
            # Keep the loudest components when trimming
            sub_parts = sorted(sub_parts, key=lambda p: p["energy"], reverse=True)[:remaining_slots]
            print(f"[processor] trimmed {stem_name} to {remaining_slots} sub-parts (cap={MAX_REFINED_STEMS})")
        _save_sub_parts(sub_parts, sr, out_dir, refined)
        del sub_parts, components
        _gc.collect()

    finally:
      # Clean up intermediate files to save disk/memory — ALWAYS runs, even on crash.
      # Remove Demucs working directory (model output copies are already in _raw_*)
      demucs_work_dir = out_dir / model
      if demucs_work_dir.exists():
          try:
              shutil.rmtree(demucs_work_dir)
              print(f"[processor] cleaned up {demucs_work_dir}")
          except Exception as e:
              print(f"[processor] cleanup warning: {e}")

      # Remove _raw_* intermediate files (refined stems are the final output).
      # ".wav.part" catches a conversion temp orphaned by a hard kill — the
      # normal failure paths already unlink it in _fetch_stem's finally.
      _raw_cleaned = 0
      for raw_file in list(out_dir.glob("_raw_*.wav")) + list(out_dir.glob("_raw_*.wav.part")):
          try:
              raw_file.unlink()
              _raw_cleaned += 1
          except Exception:
              pass
      if _raw_cleaned:
          print(f"[processor] cleaned {_raw_cleaned} _raw_* intermediate files")

    _log_mem(f"[separate_stems] post-refine ({len(refined)} stems)")
    _log_mem(f"[separate_stems] done ({len(refined)} stems)")

    # If backing vocals exist, promote "Vocals" → "Lead Vocals" for clarity
    has_backing = any(
        "backing" in v.get("label", "").lower() for v in refined.values()
    )
    if has_backing:
        for key, stem_data in list(refined.items()):
            if stem_data.get("label") == "Vocals":
                stem_data["label"] = "Lead Vocals"
                # Rename the file and dict key to match
                new_key = "lead_vocals"
                orig_path = Path(stem_data["path"])
                new_path = orig_path.parent / "lead_vocals.wav"
                try:
                    orig_path.rename(new_path)
                    stem_data["path"] = str(new_path)
                except Exception:
                    pass
                refined[new_key] = stem_data
                if key != new_key:
                    del refined[key]
                print(f"[processor] promoted 'Vocals' → 'Lead Vocals' (backing vocals present)")
                break

    return refined


def _label_to_key(label):
    """Convert a label like 'Lead Guitar' to a dict key like 'lead_guitar'."""
    return label.lower().replace(" ", "_")


def _save_sub_parts(parts, sr, out_dir, refined):
    """Save sub-parts, merging components with the same label into a single stem.

    When stereo refinement produces multiple components with identical classifications
    (e.g., center Guitar + side Guitar), merging them avoids confusing "Guitar 2"
    artifacts. The merged stem is the sum of the components (preserving loudness).
    """
    # Skip near-silent components first
    active_parts = []
    for part in parts:
        if part["energy"] < SILENCE_THRESHOLD:
            print(f"[processor] skipping silent sub-part '{part['label']}' (energy={part['energy']:.5f})")
            continue
        active_parts.append(part)

    # Group by label — merge same-label components into one stem
    from collections import OrderedDict
    label_groups: OrderedDict = OrderedDict()
    for part in sorted(active_parts, key=lambda p: p["energy"], reverse=True):
        lbl = part["label"]
        if lbl not in label_groups:
            label_groups[lbl] = part.copy()
        else:
            # Merge by summing audio (additive mix of stereo components)
            merged = label_groups[lbl]
            # Match lengths in case components differ by a sample
            min_len = min(len(merged["left"]), len(part["left"]))
            merged["left"] = merged["left"][:min_len] + part["left"][:min_len]
            merged["right"] = merged["right"][:min_len] + part["right"][:min_len]
            merged["energy"] = _rms((merged["left"] + merged["right"]) / 2)
            print(f"[processor] merged duplicate '{lbl}' sub-parts into single stem")

    # Check how many of each label are already saved across all stems in this job
    existing_label_counts: dict = {}
    for stem_data in refined.values():
        lbl = stem_data.get("label", "")
        if lbl:
            base = lbl.rsplit(" ", 1)
            base_lbl = base[0] if len(base) == 2 and base[1].isdigit() else lbl
            existing_label_counts[base_lbl] = existing_label_counts.get(base_lbl, 0) + 1

    # Save each merged group — still allow up to 2 per base label globally
    # (e.g. Backing Vocals from vocals stem + Backing Vocals from other stem)
    MAX_PER_LABEL = 2
    label_seen = dict(existing_label_counts)
    label_index: dict = {}

    for base_label, part in label_groups.items():
        if label_seen.get(base_label, 0) >= MAX_PER_LABEL:
            print(f"[processor] skipping '{base_label}' — already have {MAX_PER_LABEL} instances")
            continue
        label_seen[base_label] = label_seen.get(base_label, 0) + 1

        idx = label_index.get(base_label, 0)
        label_index[base_label] = idx + 1
        label = base_label if idx == 0 else f"{base_label} {idx + 1}"
        key = _label_to_key(label)

        # Avoid key collisions with already-saved stems
        orig_key = key
        n = 2
        while key in refined:
            key = f"{orig_key}_{n}"
            n += 1

        dest = out_dir / f"{key}.wav"
        _write_wav(dest, part["left"], part["right"], sr)
        refined[key] = {
            "path": str(dest),
            "energy": round(part["energy"], 6),
            "active": True,
            "label": label,
        }


# ─── Tab Generation ──────────────────────────────────────────────────────────

# Stems that should NOT get guitar/bass tab — only note list or nothing
_NO_TAB_STEMS = {"vocals", "lead_vocal", "backing_vocal", "harmony_vocal",
                  "vocal_double", "vocal_layer", "vocal_pad"}


def extract_note_events(stem_path: str, stem_name: str, label: str = "", bpm: float = 120.0, configs: dict | None = None):
    """
    Run Basic Pitch inference on a stem and return the normalized note events DataFrame.
    No MIDI, CSV, or ASCII tab files are written — inference output only.
    Returns None on failure.

    Args:
        configs: Optional per-job INSTRUMENT_CONFIGS copy (from get_adjusted_configs).
                 Falls back to module-level INSTRUMENT_CONFIGS when None.
    """
    _ensure_pitch_imports()   # runs in a child process — its RSS is reclaimed on exit
    renderer = _get_tab_renderer(label or stem_name)
    config = _get_instrument_config(renderer, configs)

    _log_mem(f"[extract_notes] pre-predict ({stem_name})")
    model_output, midi_data, note_events = predict(
        str(stem_path),
        ICASSP_2022_MODEL_PATH,
        onset_threshold=config["onset_threshold"],
        frame_threshold=config["frame_threshold"],
        minimum_note_length=config["min_note_length"],
        minimum_frequency=config["min_freq"],
        maximum_frequency=config["max_freq"],
    )
    del model_output, midi_data  # Large objects, not needed
    _log_mem(f"[extract_notes] post-predict ({stem_name})")

    note_events = _normalize_note_events(note_events)
    _log_confidence_stats(stem_name, label, renderer, note_events, config["confidence_threshold"])
    return note_events


def _log_confidence_stats(stem_name: str, label: str, renderer: str, note_events, threshold: float):
    """Log confidence distribution for a stem — helps tune thresholds from real data."""
    display = label or stem_name
    total = len(note_events)
    if total == 0:
        print(f"[notes] {display}: 0 notes")
        return

    if "confidence" not in note_events.columns:
        print(f"[notes] {display}: {total} notes (no confidence data)")
        return

    conf = note_events["confidence"]
    kept = int((conf >= threshold).sum())
    dropped = total - kept

    # Compute percentile buckets
    bins = [0, 0.25, 0.35, 0.45, 0.55, 0.70, 1.01]
    labels_b = ["<0.25", "0.25-0.35", "0.35-0.45", "0.45-0.55", "0.55-0.70", ">0.70"]
    hist = pd.cut(conf, bins=bins, labels=labels_b, right=False).value_counts().sort_index()
    dist_str = "  ".join(f"{l}:{int(v)}" for l, v in hist.items() if v > 0)

    print(f"[notes] {display} ({renderer}): {total} total, {kept} kept, {dropped} dropped (threshold={threshold})")
    print(f"[notes]   confidence: min={conf.min():.2f} median={conf.median():.2f} max={conf.max():.2f}")
    print(f"[notes]   distribution: {dist_str}")


def _normalize_note_events(note_events) -> "pd.DataFrame":
    """
    Normalize Basic Pitch note events into a DataFrame with at least:
    start_time_s, end_time_s, pitch_midi, confidence

    Basic Pitch returns tuples of (start, end, pitch, confidence, pitch_bends).
    The confidence (column index 3 / extra_0) is critical for filtering ghost notes.
    """
    required_cols = ["start_time_s", "end_time_s", "pitch_midi"]

    if isinstance(note_events, pd.DataFrame):
        df = note_events.copy()

    elif isinstance(note_events, list):
        if len(note_events) == 0:
            df = pd.DataFrame(columns=required_cols + ["confidence"])

        elif isinstance(note_events[0], dict):
            df = pd.DataFrame(note_events)

        elif isinstance(note_events[0], (list, tuple)):
            df = pd.DataFrame(note_events)

            if df.shape[1] < 3:
                raise ValueError(f"Unexpected tuple length in note_events: {df.shape[1]}")

            base_cols = ["start_time_s", "end_time_s", "pitch_midi"]
            extra_cols = [f"extra_{i}" for i in range(df.shape[1] - 3)]
            df.columns = base_cols + extra_cols

        else:
            raise ValueError(f"Unexpected note_events format: {type(note_events[0])}")

    else:
        raise ValueError(f"note_events is not a list or DataFrame: {type(note_events)}")

    missing = [col for col in required_cols if col not in df.columns]
    if missing:
        raise ValueError(f"Missing required note event columns: {missing}")

    # Rename extra_0 to confidence if present (Basic Pitch's 4th output column)
    if "extra_0" in df.columns and "confidence" not in df.columns:
        df = df.rename(columns={"extra_0": "confidence"})

    # Ensure confidence column exists with a default of 1.0 (trust all notes if no data)
    if "confidence" not in df.columns:
        df["confidence"] = 1.0

    return df

