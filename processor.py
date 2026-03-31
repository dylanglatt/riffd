"""
processor.py — Core audio processing pipeline for Riffd.

Used ONLY in deep analysis mode (not instant). All functions here are heavy.
Imports (numpy, pandas, basic_pitch) are deferred via _ensure_imports().

Pipeline (called by app.py process_audio deep path):
  1. separate_stems()  — Demucs subprocess → stereo refinement → labeled WAV stems
     - Tries htdemucs_6stems first, falls back to htdemucs 4-stem
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
import wave
from pathlib import Path

# Heavy imports deferred to first use — saves ~200MB at boot
np = None
pd = None
ICASSP_2022_MODEL_PATH = None
predict = None


from compat import patch_lzma as _patch_lzma


def _ensure_imports():
    """Lazy-load numpy, pandas, and basic_pitch on first use."""
    global np, pd, ICASSP_2022_MODEL_PATH, predict
    if np is not None:
        return
    _patch_lzma()
    import numpy as _np
    import pandas as _pd
    from basic_pitch import ICASSP_2022_MODEL_PATH as _model_path
    from basic_pitch.inference import predict as _predict
    np = _np
    pd = _pd
    ICASSP_2022_MODEL_PATH = _model_path
    predict = _predict
    print("[processor] heavy imports loaded (numpy, pandas, basic_pitch)")


UPLOAD_DIR = Path("uploads")
OUTPUT_DIR = Path("outputs")

# ─── Demucs Configuration ────────────────────────────────────────────────────
DEMUCS_MODEL = "htdemucs_6stems"
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


def _get_instrument_config(renderer_type: str) -> dict:
    """Get Basic Pitch parameters + confidence threshold for an instrument type."""
    return INSTRUMENT_CONFIGS.get(renderer_type, _DEFAULT_CONFIG)


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
    """Write stereo 16-bit WAV."""
    interleaved = np.column_stack([left, right])
    data = (np.clip(interleaved, -1.0, 1.0) * 32767).astype(np.int16)
    del interleaved
    with wave.open(str(filepath), "wb") as wf:
        wf.setnchannels(2)
        wf.setsampwidth(2)
        wf.setframerate(sr)
        wf.writeframes(data.tobytes())
    del data


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
    N = 4096
    hop = N // 4
    win = np.hanning(N).astype(np.float32)

    orig_len = len(left)
    pad = (hop - (orig_len % hop)) % hop + N
    left_p = np.pad(left, (0, pad)).astype(np.float32)
    right_p = np.pad(right, (0, pad)).astype(np.float32)
    out_len = len(left_p)

    n_frames = (out_len - N) // hop + 1

    # Output accumulators (center, left-panned, right-panned) × (L, R channels)
    c_l = np.zeros(out_len, dtype=np.float32)
    c_r = np.zeros(out_len, dtype=np.float32)
    p_ll = np.zeros(out_len, dtype=np.float32)
    p_lr = np.zeros(out_len, dtype=np.float32)
    p_rl = np.zeros(out_len, dtype=np.float32)
    p_rr = np.zeros(out_len, dtype=np.float32)
    win_sq = np.zeros(out_len, dtype=np.float32)

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

        # Reconstruct and overlap-add
        c_l[s:s + N] += np.fft.irfft(L * cm, n=N) * win
        c_r[s:s + N] += np.fft.irfft(R * cm, n=N) * win
        p_ll[s:s + N] += np.fft.irfft(L * lm, n=N) * win
        p_lr[s:s + N] += np.fft.irfft(R * lm, n=N) * win
        p_rl[s:s + N] += np.fft.irfft(L * rm, n=N) * win
        p_rr[s:s + N] += np.fft.irfft(R * rm, n=N) * win
        win_sq[s:s + N] += win ** 2

    # Release padded inputs
    del left_p, right_p

    # Normalize overlap-add
    norm = np.maximum(win_sq[:orig_len], 1e-8)
    del win_sq

    components = {}

    cl = c_l[:orig_len] / norm
    cr = c_r[:orig_len] / norm
    del c_l, c_r
    if _rms((cl + cr) / 2) > SILENCE_THRESHOLD:
        components["center"] = (cl, cr)

    ll = p_ll[:orig_len] / norm
    lr = p_lr[:orig_len] / norm
    del p_ll, p_lr
    if _rms(ll) > SILENCE_THRESHOLD * 0.5:
        components["left"] = (ll, lr)

    rl = p_rl[:orig_len] / norm
    rr = p_rr[:orig_len] / norm
    del p_rl, p_rr, norm
    if _rms(rr) > SILENCE_THRESHOLD * 0.5:
        components["right"] = (rl, rr)

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
        # No banjo check here — if Demucs classified the stem as guitar, it's a guitar.
        # Bright/trebly electric guitars (e.g. Brian May) have high centroid + ZCR and would
        # false-positive as banjo under spectral heuristics alone.
        if c > 2200 and bw > 1200 and zcr > 0.10:
            return "Acoustic Guitar"
        if c > 1800 and c_std > 400:
            return "Lead Guitar"
        if c > 1000:
            return "Rhythm Guitar"
        return "Guitar"

    if stem_category == "other":
        if c > 3000 and zcr > 0.15:
            return "Banjo"
        if c > 2200 and bw > 1200 and zcr > 0.10:
            return "Acoustic Guitar"
        if c > 1800 and c_std > 400:
            return "Lead Guitar"
        if c > 1500 and bw < 1500:
            return "Rhythm Guitar"
        if c > 1200 and bw > 1800:
            return "Synth"
        if c > 800:
            return "Other"
        return "Pad"

    if stem_category == "piano":
        if c > 1500 and bw > 1500:
            return "Piano"
        return "Keyboard"

    return stem_category.title()


def _get_tab_renderer(label):
    """Map a classified instrument label to the right tab renderer."""
    label_lower = label.lower()
    if "vocal" in label_lower:
        return "note_list"
    if "drum" in label_lower:
        return "drum_tab"
    if "bass" in label_lower:
        return "bass_tab"
    if any(k in label_lower for k in ("guitar", "banjo", "mandolin", "ukulele")):
        return "guitar_tab"
    if any(k in label_lower for k in ("piano", "keyboard", "organ", "synth", "pad")):
        return "note_list"
    return "note_list"


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
            ["python", "-m", "demucs", "--out", str(out_dir), "--name", model, str(audio_path)],
            capture_output=True, text=True, timeout=DEMUCS_TIMEOUT,
        )
    except subprocess.TimeoutExpired:
        raise RuntimeError(f"Demucs timed out after {DEMUCS_TIMEOUT}s")

    # Fallback to 4-stem if 6-stem fails
    if result.returncode != 0 and model == "htdemucs_6stems":
        print(f"[separation] 6-stem failed, trying 4-stem fallback. stderr: {result.stderr[-200:]}")
        model = "htdemucs"
        stem_names = STEM_NAMES_4
        try:
            result = subprocess.run(
                ["python", "-m", "demucs", "--out", str(out_dir), "--name", model, str(audio_path)],
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


def _separate_stems_replicate(audio_path: Path, out_dir: Path, progress_callback=None) -> tuple[dict, str]:
    """
    Run Demucs via Replicate REST API. Returns (raw_stems, model_name).
    raw_stems: {stem_name: path_to_raw_wav}

    Uses the REST API directly (not the replicate Python client) for full control
    over the request/response lifecycle and transparent error handling.

    Verified model: cjwbw/demucs
    Verified version: 25a173108cff36ef9f80f854c162d01df9e6528be175794b81158fa03836d953
    API schema confirmed via /v1/models/cjwbw/demucs — 2026-03-26

    Input params (verified from openapi_schema):
      audio:         file URI or URL (required)
      model_name:    "htdemucs" | "htdemucs_ft" | "htdemucs_6s" | etc.
      output_format: "wav" | "mp3" (default mp3)
      shifts:        int (default 1)

    Output (verified from openapi_schema):
      dict with keys: bass, drums, other, piano, guitar, vocals
      each value is a URI string pointing to the separated audio file
    """
    import os
    import time as _time
    import requests as _requests

    token = os.getenv("REPLICATE_API_TOKEN", "").strip()
    if not token:
        raise RuntimeError("REPLICATE_API_TOKEN not set")

    _t0 = _time.time()
    REPLICATE_API = "https://api.replicate.com/v1"
    VERSION = "25a173108cff36ef9f80f854c162d01df9e6528be175794b81158fa03836d953"
    POLL_INTERVAL = 3  # seconds between status checks (reduced from 5)
    MAX_WAIT = 600     # 10 minutes max for separation

    # Expected stems from the model output
    EXPECTED_STEMS = {"vocals", "drums", "bass", "guitar", "piano", "other"}

    headers = {
        "Authorization": f"Bearer {token}",
        "Content-Type": "application/json",
    }

    if progress_callback:
        progress_callback("Running stem separation (cloud)...")

    print(f"[replicate] starting: {audio_path.name}")
    print(f"[replicate] model = cjwbw/demucs")
    print(f"[replicate] version = {VERSION[:16]}...")

    # ── Step 1: Upload audio file to Replicate ──
    # Pre-transcode to 128kbps MP3 to halve upload size (Demucs doesn't need higher bitrate for separation)
    upload_path = audio_path
    _transcode_tmp = None
    try:
        import subprocess as _sp
        _transcode_tmp = audio_path.parent / f"_upload_{audio_path.stem}_128k.mp3"
        _tc = _sp.run(
            ["ffmpeg", "-y", "-i", str(audio_path), "-b:a", "128k", "-ac", "2", str(_transcode_tmp)],
            capture_output=True, timeout=60,
        )
        if _tc.returncode == 0 and _transcode_tmp.exists() and _transcode_tmp.stat().st_size > 0:
            orig_size = audio_path.stat().st_size
            new_size = _transcode_tmp.stat().st_size
            print(f"[replicate] transcoded to 128kbps: {orig_size:,} → {new_size:,} bytes ({new_size*100//orig_size}%)")
            upload_path = _transcode_tmp
        else:
            print(f"[replicate] transcode failed (rc={_tc.returncode}), uploading original")
            _transcode_tmp = None
    except Exception as _te:
        print(f"[replicate] transcode skipped: {_te}")
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
            "model_name": "htdemucs_6s",  # 6-stem: vocals/drums/bass/guitar/piano/other
            "output_format": "mp3",  # mp3 is ~10x smaller than wav; we convert locally after download
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

    # Download all stems in parallel — each is an independent HTTP request
    from concurrent.futures import ThreadPoolExecutor, as_completed as _as_completed

    def _dl_stem(stem_key, url):
        if not url or not isinstance(url, str):
            return stem_key, None, "no URL"
        stem_name = stem_key if stem_key in _KNOWN_STEMS else stem_key
        # Download as MP3 (output_format=mp3 is ~10x smaller than wav)
        mp3_tmp = out_dir / f"_raw_{stem_name}.mp3"
        wav_dest = out_dir / f"_raw_{stem_name}.wav"
        print(f"[replicate] downloading: {stem_name} → {mp3_tmp.name}")
        with _requests.get(url, stream=True, timeout=120) as dl_resp:
            dl_resp.raise_for_status()
            byte_count = 0
            with open(mp3_tmp, "wb") as f:
                for chunk in dl_resp.iter_content(chunk_size=1024 * 1024):  # 1MB chunks
                    f.write(chunk)
                    byte_count += len(chunk)
        print(f"[replicate] downloaded: {stem_name} ({byte_count:,} bytes) — converting to wav")
        # Convert MP3 → WAV so downstream pipeline stays unchanged (expects .wav)
        import soundfile as _sf
        try:
            _data, _sr = _sf.read(str(mp3_tmp), dtype="float32")
            _sf.write(str(wav_dest), _data, _sr)
        except Exception:
            # soundfile may not support MP3 in all environments — fall back to librosa
            import librosa as _librosa
            import numpy as _np
            _data, _sr = _librosa.load(str(mp3_tmp), sr=None, mono=False)
            if _data.ndim == 1:
                _data = _np.stack([_data, _data], axis=0)
            _sf.write(str(wav_dest), _data.T, _sr)
        finally:
            try:
                mp3_tmp.unlink()
            except Exception:
                pass
        print(f"[replicate] saved: {stem_name}")
        return stem_key, str(wav_dest), None

    raw_stems = {}
    dl_items = [(k, v) for k, v in output.items()]
    if progress_callback:
        progress_callback(f"Downloading stems (0/{len(dl_items)})...")
    with ThreadPoolExecutor(max_workers=len(dl_items) or 1) as _pool:
        futures = {_pool.submit(_dl_stem, k, v): k for k, v in dl_items}
        completed_count = 0
        for fut in _as_completed(futures):
            stem_key, path, err = fut.result()
            completed_count += 1
            if err:
                print(f"[replicate] skipping stem '{stem_key}': {err}")
            elif path:
                stem_name = stem_key if stem_key in _KNOWN_STEMS else stem_key
                raw_stems[stem_name] = path
                if progress_callback:
                    progress_callback(f"Downloading stems ({completed_count}/{len(dl_items)})...")

    if not raw_stems:
        raise RuntimeError("Replicate returned no downloadable stems")

    elapsed = _time.time() - _t0
    print(f"[replicate] COMPLETE in {elapsed:.1f}s → {len(raw_stems)} stems: {list(raw_stems.keys())}")
    return raw_stems, "replicate_htdemucs"


def separate_stems(audio_path: str, song_id: str, progress_callback=None) -> dict:
    """
    Full pipeline:
      1. Run Demucs for initial separation (hosted or local)
      2. Analyze stereo field of each stem
      3. Split into individually panned components
      4. Classify each component
      5. Return only stems with meaningful audio content

    Returns dict: {stem_key: {path, energy, active, label}}
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
                raw_stems, model = _separate_stems_replicate(audio_path, out_dir, progress_callback)
                if attempt > 1:
                    print(f"[separation] succeeded on attempt {attempt}")
                last_error = None
                break
            except Exception as e:
                last_error = e
                err_str = str(e)
                # Retry on GPU preemption (code: PA), transient API failures, or network errors
                # "timed out" / "connection aborted" cover socket write timeouts during upload
                is_retryable = (
                    "code: PA" in err_str
                    or "interrupted" in err_str.lower()
                    or "starting" in err_str.lower()
                    or "timed out" in err_str.lower()
                    or "time out" in err_str.lower()
                    or "connection aborted" in err_str.lower()
                    or "connectionerror" in err_str.lower()
                    or "remotedisconnected" in err_str.lower()
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

    # ── Step 2: Refine each stem ──
    if progress_callback:
        progress_callback("Analyzing instruments...")

    refined = {}

    # Cap total refined stems to prevent OOM on complex songs (e.g. Layla → 14 stems).
    # Demucs returns 6 raw stems; stereo separation can multiply this to 15+.
    # Each sub-stem is a full-length WAV — uncapped, a 7-min song creates 2GB+ of temp files.
    # Drums and bass always get slots; remaining 8 slots go to pitched stems by energy.
    MAX_REFINED_STEMS = 10

    import gc as _gc

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
            if energy < stem_energy * MIN_RELATIVE_ENERGY or energy < MIN_ABSOLUTE_ENERGY:
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

    # Clean up intermediate files to save disk/memory
    # Remove Demucs working directory (model output copies are already in _raw_*)
    demucs_work_dir = out_dir / model
    if demucs_work_dir.exists():
        try:
            shutil.rmtree(demucs_work_dir)
            print(f"[processor] cleaned up {demucs_work_dir}")
        except Exception as e:
            print(f"[processor] cleanup warning: {e}")

    # Remove _raw_* intermediate files (refined stems are the final output)
    for raw_file in out_dir.glob("_raw_*.wav"):
        try:
            raw_file.unlink()
        except Exception:
            pass

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
    """Save sub-parts with simple numbered labels, capped at 2 per label globally."""
    # Sort by energy descending — keep the loudest instances of each label
    parts_sorted = sorted(parts, key=lambda p: p["energy"], reverse=True)

    # Cap at 2 per label GLOBALLY — count labels already saved across all previous stems
    # so that two different Demucs stems producing the same label don't bypass the cap.
    MAX_PER_LABEL = 2
    existing_label_counts = {}
    for stem_data in refined.values():
        lbl = stem_data.get("label", "")
        if lbl:
            # Strip trailing " 2", " 3" etc. to get the base label for counting
            base = lbl.rsplit(" ", 1)
            if len(base) == 2 and base[1].isdigit():
                lbl = base[0]
            existing_label_counts[lbl] = existing_label_counts.get(lbl, 0) + 1

    label_seen = dict(existing_label_counts)
    filtered = []
    for part in parts_sorted:
        # Skip near-silent components — stereo separation can produce artifacts
        # just above the 0.5× detection threshold that have no audible content.
        if part["energy"] < SILENCE_THRESHOLD:
            print(f"[processor] skipping silent sub-part '{part['label']}' (energy={part['energy']:.5f})")
            continue
        lbl = part["label"]
        if label_seen.get(lbl, 0) < MAX_PER_LABEL:
            label_seen[lbl] = label_seen.get(lbl, 0) + 1
            filtered.append(part)

    label_index = {}
    for part in filtered:
        base_label = part["label"]
        idx = label_index.get(base_label, 0)
        label_index[base_label] = idx + 1

        # First occurrence keeps the base name; extras get a number suffix
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


def extract_note_events(stem_path: str, stem_name: str, label: str = "", bpm: float = 120.0):
    """
    Run Basic Pitch inference on a stem and return the normalized note events DataFrame.
    No MIDI, CSV, or ASCII tab files are written — inference output only.
    Returns None on failure.
    """
    _ensure_imports()
    renderer = _get_tab_renderer(label or stem_name)
    config = _get_instrument_config(renderer)

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

    note_events = _normalize_note_events(note_events)
    _log_confidence_stats(stem_name, label, renderer, note_events, config["confidence_threshold"])
    return note_events


def generate_tabs(stem_path: str, song_id: str, stem_name: str, label: str = "", bpm: float = 120.0) -> dict:
    """
    Run Basic Pitch on a stem audio file with per-instrument parameters.
    Returns paths to: MIDI file, note events CSV, and rendered tab text.
    """
    _ensure_imports()
    stem_path = Path(stem_path)
    tab_dir = OUTPUT_DIR / song_id / "tabs"
    tab_dir.mkdir(parents=True, exist_ok=True)

    # Select Basic Pitch parameters based on instrument type
    renderer = _get_tab_renderer(label or stem_name)
    config = _get_instrument_config(renderer)

    model_output, midi_data, note_events = predict(
        str(stem_path),
        ICASSP_2022_MODEL_PATH,
        onset_threshold=config["onset_threshold"],
        frame_threshold=config["frame_threshold"],
        minimum_note_length=config["min_note_length"],
        minimum_frequency=config["min_freq"],
        maximum_frequency=config["max_freq"],
    )
    del model_output  # Large tensor, not needed after prediction

    midi_path = tab_dir / f"{stem_name}.mid"
    midi_data.write(str(midi_path))
    del midi_data

    csv_path = tab_dir / f"{stem_name}_notes.csv"
    note_events = _normalize_note_events(note_events)
    note_events.to_csv(str(csv_path), index=False)

    # Log confidence distribution for tuning
    _log_confidence_stats(stem_name, label, renderer, note_events, config["confidence_threshold"])

    # Determine if this stem should get full tab or just a note summary
    stem_key_lower = stem_name.lower()

    # Quality gate: if renderer says note_list, don't force a fake guitar tab
    if renderer == "note_list" or stem_key_lower in _NO_TAB_STEMS:
        tab_text = render_ascii_tab(note_events, stem_name, label, bpm=bpm, confidence_threshold=config["confidence_threshold"])
    elif renderer == "drum_tab":
        tab_text = render_ascii_tab(note_events, stem_name, label, bpm=bpm, confidence_threshold=config["confidence_threshold"])
    else:
        # Guitar/bass tab — check if there are enough confident notes
        confident_notes = _count_confident_notes(note_events, config["confidence_threshold"])
        if confident_notes < 5:
            tab_text = f"=== {(label or stem_name).upper()} ===\n\n(insufficient note data for tab)"
        else:
            tab_text = render_ascii_tab(note_events, stem_name, label, bpm=bpm, confidence_threshold=config["confidence_threshold"])

    tab_txt_path = tab_dir / f"{stem_name}_tab.txt"
    tab_txt_path.write_text(tab_text)

    return {
        "midi": str(midi_path),
        "notes_csv": str(csv_path),
        "tab_txt": str(tab_txt_path),
        "tab_text": tab_text,
    }


def _count_confident_notes(note_events, threshold: float) -> int:
    """Count notes above the confidence threshold."""
    if "confidence" not in note_events.columns:
        return len(note_events)
    return int((note_events["confidence"] >= threshold).sum())


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


def render_ascii_tab(note_events, stem_name: str, label: str = "", bpm: float = 120.0, confidence_threshold: float = 0.35) -> str:
    """
    Render ASCII tab. Uses the label (e.g. "Lead Guitar 1") to pick the renderer.
    Filters notes by confidence before rendering.
    Uses BPM for grid quantization.
    """
    display_name = label or stem_name
    lines = [f"=== {display_name.upper()} TAB ===\n"]

    renderer = _get_tab_renderer(label or stem_name)

    # Filter by confidence before rendering
    df = _filter_by_confidence(note_events, confidence_threshold)

    if renderer == "bass_tab":
        lines.append(_render_string_tab(df, "bass", bpm=bpm))
    elif renderer == "guitar_tab":
        lines.append(_render_string_tab(df, "guitar", bpm=bpm))
    elif renderer == "drum_tab":
        lines.append(_render_drum_tab(df, bpm=bpm))
    else:
        lines.append(_render_note_list(df))

    return "\n".join(lines)


def _filter_by_confidence(note_events, threshold: float) -> "pd.DataFrame":
    """Filter out low-confidence notes. Returns filtered DataFrame."""
    if "confidence" not in note_events.columns:
        return note_events.copy()
    return note_events[note_events["confidence"] >= threshold].copy()


def _render_string_tab(note_events, instrument_type: str, bpm: float = 120.0) -> str:
    """
    Render guitar (6-string) or bass (4-string) ASCII tab.

    Uses BPM for grid quantization instead of hardcoded timing.
    Confidence filtering happens before this function is called.
    """
    is_bass = (instrument_type == "bass")

    if is_bass:
        # Bass: E1=28, A1=33, D2=38, G2=43 (standard bass tuning)
        open_notes = [28, 33, 38, 43]
        string_names = ["G", "D", "A", "E"]
    else:
        # Guitar: E2=40, A2=45, D3=50, G3=55, B3=59, E4=64
        open_notes = [40, 45, 50, 55, 59, 64]
        string_names = ["e", "B", "G", "D", "A", "E"]

    # Reverse so lowest string is at bottom (standard tab layout)
    string_names = string_names[::-1]
    open_notes = open_notes[::-1]
    n_strings = len(string_names)

    if len(note_events) == 0:
        return "(no notes detected)"

    # Filter: remove very short notes (likely detection noise)
    df = note_events.copy()
    df["duration"] = df["end_time_s"] - df["start_time_s"]
    df = df[df["duration"] >= 0.08].copy()

    if len(df) == 0:
        return "(no significant notes detected)"

    # Limit to first 32 seconds for readability
    max_render_time = min(df["end_time_s"].max(), 32.0)
    df = df[df["start_time_s"] < max_render_time].copy()

    # BPM-based quantization grid: 8th notes at actual tempo
    effective_bpm = max(40.0, min(300.0, bpm)) if bpm and bpm > 0 else 120.0
    beat_dur = 60.0 / effective_bpm               # seconds per beat
    slot_dur = beat_dur / 2                        # 8th note grid
    slots_per_measure = 8                          # 8 eighth notes per 4/4 measure
    n_slots = int(max_render_time / slot_dur) + 1

    # Each column is 3 chars wide: "xx-" — handles up to fret 24 cleanly
    COL_WIDTH = 3
    max_fret = 22

    # Build grid: n_strings rows × n_slots columns, each cell is a string of COL_WIDTH
    EMPTY = "-" * COL_WIDTH
    grid = [[EMPTY] * n_slots for _ in range(n_strings)]

    # Place notes
    for _, row in df.iterrows():
        pitch = int(row["pitch_midi"])
        start = row["start_time_s"]
        slot = int(start / slot_dur)
        if slot >= n_slots:
            continue

        # Find best string/fret — prefer lower frets, open strings
        best_string, best_fret = None, None
        for si, open_note in enumerate(open_notes):
            fret = pitch - open_note
            if 0 <= fret <= max_fret:
                # Prefer: open string > low fret > high fret
                if best_fret is None or fret < best_fret:
                    best_string, best_fret = si, fret

        if best_string is None:
            continue

        # Don't overwrite an existing note in the same slot/string
        if grid[best_string][slot] != EMPTY:
            continue

        # Format fret number: left-pad to COL_WIDTH, fill rest with dashes
        fret_str = str(best_fret)
        cell = fret_str + "-" * (COL_WIDTH - len(fret_str))
        grid[best_string][slot] = cell

    # Render output with bar lines and line wrapping
    output_lines = []
    slots_per_line = slots_per_measure * 4  # 4 measures per line

    for line_start in range(0, n_slots, slots_per_line):
        line_end = min(line_start + slots_per_line, n_slots)

        for si, name in enumerate(string_names):
            parts = [f"{name}|"]
            for slot in range(line_start, line_end):
                # Add bar line at measure boundaries
                if slot > line_start and (slot - line_start) % slots_per_measure == 0:
                    parts.append("|")
                parts.append(grid[si][slot])
            parts.append("|")
            output_lines.append("".join(parts))

        output_lines.append("")  # blank line between systems

    # Trim trailing blank lines
    while output_lines and output_lines[-1] == "":
        output_lines.pop()

    return "\n".join(output_lines)


def _render_drum_tab(note_events, bpm: float = 120.0) -> str:
    """Render a basic drum tab using General MIDI percussion mapping.
    Uses BPM for grid timing instead of hardcoded values."""
    drum_map = {
        36: "BD", 38: "SN", 42: "HH", 46: "OH",
        49: "CC", 51: "RC", 43: "FT", 45: "MT", 48: "HT",
    }

    if len(note_events) == 0:
        return "(no drum hits detected)"

    # BPM-based timing
    effective_bpm = max(40.0, min(300.0, bpm)) if bpm and bpm > 0 else 120.0
    beat_duration = 60.0 / effective_bpm
    measure_duration = beat_duration * 4
    cols_per_measure = 16

    max_time = note_events["end_time_s"].max()
    num_measures = max(1, int(max_time / measure_duration) + 1)
    total_cols = num_measures * cols_per_measure

    active_drums = set()
    for _, row in note_events.iterrows():
        p = int(row["pitch_midi"])
        if p in drum_map:
            active_drums.add(p)

    if not active_drums:
        return "(no recognized drum hits)"

    grid = {p: ["-"] * total_cols for p in active_drums}

    for _, row in note_events.iterrows():
        p = int(row["pitch_midi"])
        if p not in drum_map:
            continue
        col = min(int(row["start_time_s"] / measure_duration * cols_per_measure), total_cols - 1)
        grid[p][col] = "X"

    output_lines = []
    chunk = 64
    for start_col in range(0, total_cols, chunk):
        end_col = min(start_col + chunk, total_cols)
        for p in sorted(active_drums):
            name = drum_map[p]
            row_str = "".join(grid[p][start_col:end_col])
            output_lines.append(f"{name}|{row_str}|")
        output_lines.append("")

    return "\n".join(output_lines)


def _render_note_list(note_events) -> str:
    """Fallback: list notes with timing for vocals/keys/other."""
    note_names = ["C", "C#", "D", "D#", "E", "F", "F#", "G", "G#", "A", "A#", "B"]
    lines = ["Time(s)   Note   Duration(s)", "-" * 30]

    for _, row in note_events.iterrows():
        pitch = int(row["pitch_midi"])
        note_name = note_names[pitch % 12] + str(pitch // 12 - 1)
        duration = row["end_time_s"] - row["start_time_s"]
        lines.append(f"{row['start_time_s']:6.2f}s   {note_name:<5}  {duration:.2f}s")

    return "\n".join(lines)
