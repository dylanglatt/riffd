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

    # Output accumulators — float16 halves memory (~177MB savings for 5-min song).
    # Per-frame STFT math stays float32; results are cast to float16 before accumulation.
    # Final output is normalized and truncated to 16-bit PCM, so float16 precision suffices.
    c_l = np.zeros(out_len, dtype=np.float16)
    c_r = np.zeros(out_len, dtype=np.float16)
    p_ll = np.zeros(out_len, dtype=np.float16)
    p_lr = np.zeros(out_len, dtype=np.float16)
    p_rl = np.zeros(out_len, dtype=np.float16)
    p_rr = np.zeros(out_len, dtype=np.float16)
    win_sq = np.zeros(out_len, dtype=np.float16)
    win_f16 = win.astype(np.float16)

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

        # Reconstruct and overlap-add (compute in float32, accumulate in float16)
        c_l[s:s + N] += (np.fft.irfft(L * cm, n=N) * win).astype(np.float16)
        c_r[s:s + N] += (np.fft.irfft(R * cm, n=N) * win).astype(np.float16)
        p_ll[s:s + N] += (np.fft.irfft(L * lm, n=N) * win).astype(np.float16)
        p_lr[s:s + N] += (np.fft.irfft(R * lm, n=N) * win).astype(np.float16)
        p_rl[s:s + N] += (np.fft.irfft(L * rm, n=N) * win).astype(np.float16)
        p_rr[s:s + N] += (np.fft.irfft(R * rm, n=N) * win).astype(np.float16)
        win_sq[s:s + N] += win_f16 ** 2

    # Release padded inputs
    del left_p, right_p
    _log_mem("[stereo_separate] after STFT loop")

    # Normalize overlap-add — upcast to float32 for division precision
    norm = np.maximum(win_sq[:orig_len].astype(np.float32), 1e-8)
    del win_sq

    components = {}

    cl = c_l[:orig_len].astype(np.float32) / norm
    cr = c_r[:orig_len].astype(np.float32) / norm
    del c_l, c_r
    if _rms((cl + cr) / 2) > SILENCE_THRESHOLD:
        components["center"] = (cl, cr)

    ll = p_ll[:orig_len].astype(np.float32) / norm
    lr = p_lr[:orig_len].astype(np.float32) / norm
    del p_ll, p_lr
    if _rms(ll) > SILENCE_THRESHOLD * 0.5:
        components["left"] = (ll, lr)

    rl = p_rl[:orig_len].astype(np.float32) / norm
    rr = p_rr[:orig_len].astype(np.float32) / norm
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
        # Lead/Rhythm distinction is handled by _melodic_split_pass via pitch detection —
        # spectral centroid alone is too unreliable and causes double-numbering artifacts.
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


# ─── Melodic Split ──────────────────────────────────────────────────────────

def _extract_melodic_mask(note_events, sr: int, n_samples: int,
                          n_fft: int = 2048, hop_length: int = 512) -> "np.ndarray | None":
    """
    Build an STFT-domain soft mask for lead (melodic) content using a
    pitch-percentile approach.

    Instead of trying to detect monophonic frames via confidence ratios
    (which fails on mixed stems where lead + rhythm guitar coexist),
    we identify the upper-register notes — those above the 55th percentile
    pitch — as "lead" content. In a mixed guitar stem, lead lines are
    consistently higher-pitched than rhythm chord tones.

    Returns a 2D soft mask (n_freq_bins x n_time_frames) or None.
    """
    if note_events is None or len(note_events) == 0:
        return None

    n_freq_bins = n_fft // 2 + 1
    n_time_frames = (n_samples - n_fft) // hop_length + 1
    if n_time_frames <= 0:
        return None

    freqs = np.fft.rfftfreq(n_fft, 1.0 / sr)
    frame_times = np.arange(n_time_frames) * hop_length / sr  # noqa: F841

    # --- Pitch-percentile melody detection ---
    # Filter to confident notes only first (avoid ghost notes polluting the distribution)
    conf_col = "confidence" if "confidence" in note_events.columns else None
    if conf_col:
        confident_notes = note_events[note_events[conf_col] >= 0.30].copy()
    else:
        confident_notes = note_events.copy()

    if len(confident_notes) == 0:
        return None

    pitches = confident_notes["pitch_midi"].values

    # Compute pitch threshold: notes above the 55th percentile are "lead".
    # This captures the upper melodic register while excluding the bulk of
    # chord tones which sit in the lower-mid register.
    pitch_threshold = float(np.percentile(pitches, 55))

    # Filter to upper-register "lead" notes
    lead_notes = confident_notes[confident_notes["pitch_midi"] >= pitch_threshold]

    if len(lead_notes) == 0:
        return None

    starts = lead_notes["start_time_s"].values
    ends = lead_notes["end_time_s"].values
    lead_pitches = lead_notes["pitch_midi"].values

    mask = np.zeros((n_freq_bins, n_time_frames), dtype=np.float32)

    # 100 Hz bandwidth captures enough energy around each harmonic for
    # meaningful signal separation
    bw = 100.0

    for note_idx in range(len(lead_notes)):
        note_start = starts[note_idx]
        note_end = ends[note_idx]
        midi = lead_pitches[note_idx]

        frame_start = max(0, int(note_start * sr / hop_length))
        frame_end = min(n_time_frames, int(note_end * sr / hop_length) + 1)

        if frame_start >= frame_end:
            continue

        # Convert MIDI pitch to Hz and compute harmonics (fundamental + 3)
        f0 = 440.0 * (2.0 ** ((midi - 69) / 12.0))
        harmonics = [f0 * (h + 1) for h in range(4)]

        sigma = bw / 2.0
        for fh in harmonics:
            if fh > sr / 2:
                break
            gauss = np.exp(-((freqs - fh) ** 2) / (2 * sigma * sigma)).astype(np.float32)
            mask[:, frame_start:frame_end] = np.maximum(
                mask[:, frame_start:frame_end],
                gauss[:, np.newaxis]
            )

    # Check mask has meaningful coverage
    mask_coverage = float(np.mean(mask > 0.05))
    print(f"[melodic_split] mask coverage: {mask_coverage:.3f} "
          f"(pitch_threshold={pitch_threshold:.1f} MIDI, "
          f"lead_notes={len(lead_notes)}/{len(confident_notes)})")

    if mask_coverage < 0.005:  # 0.5% minimum — very lenient, quality gate handles the rest
        return None

    return mask


def _split_melodic_stem(left, right, sr: int, melodic_mask,
                        n_fft: int = 2048, hop_length: int = 512):
    """
    Split a stem into lead (melodic) and accompaniment using an STFT mask.

    Returns ((lead_left, lead_right), (acc_left, acc_right)) or None if split
    is not meaningful (quality gate: both halves must have >= 15% of total energy).
    """
    def _apply_mask_channel(signal, mask, inverse=False):
        """Apply (or invert) a T-F mask to a signal via STFT → mask → iSTFT."""
        win = np.hanning(n_fft).astype(np.float32)
        # Pad signal
        orig_len = len(signal)
        pad = (hop_length - (orig_len % hop_length)) % hop_length + n_fft
        padded = np.pad(signal, (0, pad)).astype(np.float32)

        n_frames = (len(padded) - n_fft) // hop_length + 1
        out = np.zeros(len(padded), dtype=np.float32)
        win_sq = np.zeros(len(padded), dtype=np.float32)

        m = (1.0 - mask) if inverse else mask

        for i in range(n_frames):
            s = i * hop_length
            frame = padded[s:s + n_fft] * win
            F = np.fft.rfft(frame)

            # Ensure mask frame index is in range
            mi = min(i, m.shape[1] - 1)
            F_masked = F * m[:, mi]

            out[s:s + n_fft] += np.fft.irfft(F_masked, n=n_fft) * win
            win_sq[s:s + n_fft] += win ** 2

        norm = np.maximum(win_sq, 1e-8)
        result = (out / norm)[:orig_len]
        del out, win_sq, padded, norm
        return result

    import gc as _gc_split

    # Process channels one at a time with explicit cleanup between each
    # to reduce peak memory (~200-400MB savings vs all 4 simultaneously).
    _log_mem("[split_melodic] pre-STFT-apply (4 sequential passes)")
    lead_l = _apply_mask_channel(left, melodic_mask, inverse=False)
    _gc_split.collect()

    lead_r = _apply_mask_channel(right, melodic_mask, inverse=False)
    _gc_split.collect()

    acc_l = _apply_mask_channel(left, melodic_mask, inverse=True)
    _gc_split.collect()

    acc_r = _apply_mask_channel(right, melodic_mask, inverse=True)
    _gc_split.collect()
    _log_mem("[split_melodic] post-STFT-apply")

    # Quality gate: both halves must have >= 15% of total energy
    total_energy = _rms((left + right) / 2)
    if total_energy < 1e-8:
        del lead_l, lead_r, acc_l, acc_r
        return None

    lead_energy = _rms((lead_l + lead_r) / 2)
    acc_energy = _rms((acc_l + acc_r) / 2)

    lead_ratio = lead_energy / total_energy
    acc_ratio = acc_energy / total_energy

    # 5% (was 8%, was 15%) — tiled mask approach means both halves should have
    # meaningful energy throughout the song; 5% filters near-silence ghost splits
    # while letting valid subtle separations through.
    if lead_ratio < 0.05 or acc_ratio < 0.05:
        del lead_l, lead_r, acc_l, acc_r
        return None

    return (lead_l, lead_r), (acc_l, acc_r)


_SPLIT_LABEL_MAP = {
    "Guitar":           ("Lead Guitar", "Rhythm Guitar"),
    "Acoustic Guitar":  ("Lead Acoustic Guitar", "Acoustic Guitar Accompaniment"),
    # Vocals intentionally excluded — melodic pitch-split produces false
    # lead/backing dupes on single-voice tracks (e.g. "Kiss Me").  Genuine
    # backing vocals are already caught by the stereo-refinement pass.
    "Piano":            ("Piano Solo", "Piano Accompaniment"),
    "Keyboard":         ("Keyboard Lead", "Keyboard Pad"),
    "Synth":            ("Synth Lead", "Synth Pad"),
    "Pad":              ("Pad Lead", "Pad Accompaniment"),
    "Other":            ("Lead", "Accompaniment"),
    "Atmosphere":       ("Atmosphere Lead", "Atmosphere"),
}


def _get_split_labels(original_label: str) -> tuple:
    """
    Map an instrument label to (lead_label, accompaniment_label).
    """
    if original_label in _SPLIT_LABEL_MAP:
        return _SPLIT_LABEL_MAP[original_label]
    # Fallback: prepend "Lead " / " Accompaniment"
    return (f"Lead {original_label}", f"{original_label} Accompaniment")


# Labels that indicate the stem was already split — don't split again
_ALREADY_SPLIT_KEYWORDS = {"lead", "backing", "rhythm", "solo", "pad", "accompaniment"}


def _melodic_split_pass(refined: dict, out_dir: "Path", sr: int = 44100,
                        progress_callback=None, note_events_dict: dict | None = None) -> dict:
    """
    Post-processing pass: attempt to split non-drum/bass stems into
    lead (melodic) and accompaniment components using pitch detection.

    Modifies `refined` in place and returns it.
    """
    import gc as _gc

    # Snapshot keys — we'll modify the dict during iteration
    raw_candidates = []
    for key, stem_data in list(refined.items()):
        label = stem_data.get("label", "")
        label_lower = label.lower()

        # Skip drums/bass
        if "drum" in label_lower or "bass" in label_lower:
            continue
        # Skip already-split stems
        if any(kw in label_lower for kw in _ALREADY_SPLIT_KEYWORDS):
            continue
        # Skip inactive or silent stems
        if not stem_data.get("active", True):
            continue
        if stem_data.get("energy", 0) < MIN_ABSOLUTE_ENERGY:
            continue

        raw_candidates.append((key, stem_data))

    # De-duplicate by base instrument: stereo refinement can produce "Guitar" and
    # "Guitar 2" (center vs side components of the same Demucs stem). Running a
    # melodic split on both creates doubled artifacts like "Lead Guitar 2" and
    # "Guitar 2 Accompaniment". Only split the highest-energy stem per base label.
    import re as _re
    def _base_label(lbl: str) -> str:
        """Strip trailing number suffix, e.g. 'Guitar 2' → 'guitar', 'Acoustic Guitar 2' → 'acoustic guitar'."""
        return _re.sub(r'\s+\d+$', '', lbl).strip().lower()

    seen_base: dict = {}
    for key, stem_data in raw_candidates:
        base = _base_label(stem_data.get("label", key))
        energy = stem_data.get("energy", 0)
        if base not in seen_base or energy > seen_base[base][1]:
            seen_base[base] = (key, energy, stem_data)

    candidates = [(v[0], v[2]) for v in seen_base.values()]
    print(f"[melodic_split] {len(raw_candidates)} raw candidates → {len(candidates)} after de-dup: "
          f"{[v[2].get('label', v[0]) for v in seen_base.values()]}")

    if not candidates:
        return refined

    MAX_REFINED_STEMS = 10  # must match the cap in separate_stems()

    _log_mem(f"[melodic_split] START ({len(candidates)} candidates)")
    for key, stem_data in candidates:
        # Check cap
        if len(refined) >= MAX_REFINED_STEMS:
            print(f"[melodic_split] stem cap reached ({MAX_REFINED_STEMS}) — stopping")
            break

        stem_path = stem_data["path"]

        # Edge case: skip very short stems (< 5 seconds)
        try:
            left, right, file_sr = _read_wav(stem_path)
        except Exception as e:
            print(f"[melodic_split] failed to read {key}: {e}")
            continue

        duration = len(left) / file_sr
        if duration < 5.0:
            print(f"[melodic_split] skipping {key} — too short ({duration:.1f}s)")
            del left, right
            continue

        if progress_callback:
            progress_callback(f"Refining {stem_data.get('label', key)}...")

        # Use pre-computed note events from the main Basic Pitch pass when available.
        # This eliminates a redundant TF model load (~1.2GB) per melodic split candidate.
        MELODIC_INFER_SECS = 90
        note_events = None
        if note_events_dict:
            # Try exact key match first, then try matching by base stem name
            _pre = note_events_dict.get(key)
            if _pre is None:
                # Melodic split candidates may have different keys than note extraction stems
                # (e.g. "guitar" vs "rhythm_guitar"). Try partial match.
                for _ne_key, _ne_val in note_events_dict.items():
                    if _ne_key.lower() in key.lower() or key.lower() in _ne_key.lower():
                        _pre = _ne_val
                        break
            if _pre is not None and len(_pre) > 0:
                # Filter to first 90s (matching the original MELODIC_INFER_SECS truncation)
                note_events = _pre[_pre["start_time_s"] <= MELODIC_INFER_SECS].copy()
                print(f"[melodic_split] reusing pre-computed note events for {key} "
                      f"({len(note_events)} notes within {MELODIC_INFER_SECS}s)")

        if note_events is None or len(note_events) == 0:
            # Fallback: run Basic Pitch if no pre-computed events available
            import tempfile as _tempfile, subprocess as _subp_mel, os as _os_mel
            _infer_tmp = None
            infer_stem_path = stem_path
            try:
                _tmp_fd, _infer_tmp = _tempfile.mkstemp(suffix=f"_{key}_90s.wav")
                _os_mel.close(_tmp_fd)
                _subp_mel.run(
                    ["ffmpeg", "-y", "-i", stem_path, "-t", str(MELODIC_INFER_SECS), "-c", "copy", _infer_tmp],
                    capture_output=True, timeout=30,
                )
                if _os_mel.path.getsize(_infer_tmp) > 1000:
                    infer_stem_path = _infer_tmp
                    print(f"[melodic_split] truncated {key} to {MELODIC_INFER_SECS}s for Basic Pitch")
            except Exception as _trunc_e:
                print(f"[melodic_split] truncation warning ({key}): {_trunc_e} — using full file")

            _log_mem(f"[melodic_split] pre-basic-pitch ({key}, dur={duration:.0f}s)")
            print(f"[melodic_split] running Basic Pitch on {key} (no pre-computed events)...")
            config = _get_instrument_config("note_list")
            try:
                model_output, midi_data, raw_note_events = predict(
                    str(infer_stem_path),
                    ICASSP_2022_MODEL_PATH,
                    onset_threshold=config["onset_threshold"],
                    frame_threshold=config["frame_threshold"],
                    minimum_note_length=config["min_note_length"],
                    minimum_frequency=config["min_freq"],
                    maximum_frequency=config["max_freq"],
                )
                del model_output, midi_data
            except Exception as e:
                print(f"[melodic_split] Basic Pitch failed for {key}: {e}")
                del left, right
                continue
            finally:
                if _infer_tmp and _os_mel.path.exists(_infer_tmp):
                    try:
                        _os_mel.remove(_infer_tmp)
                    except Exception:
                        pass

            _log_mem(f"[melodic_split] post-basic-pitch ({key})")
            note_events = _normalize_note_events(raw_note_events)
            del raw_note_events

        if len(note_events) == 0:
            print(f"[melodic_split] no notes detected for {key} — skipping")
            del left, right
            _gc.collect()
            continue

        # Build melodic mask.
        # We build it from the 90s inference window only (matching the Basic Pitch
        # truncation above), then tile it to cover the full audio duration.
        # This prevents the second half of the song from being all-zero in the mask
        # (which would make the lead track silent after 90s and fail the quality gate).
        n_fft = 2048
        hop_length = 512
        infer_n_samples = min(len(left), int(MELODIC_INFER_SECS * file_sr))
        mask_90s = _extract_melodic_mask(note_events, file_sr, infer_n_samples,
                                         n_fft=n_fft, hop_length=hop_length)
        del note_events

        if mask_90s is None:
            print(f"[melodic_split] no meaningful melody in {key} — skipping")
            del left, right
            _gc.collect()
            continue

        # Tile the 90s mask to cover the full audio duration
        n_time_full = (len(left) - n_fft) // hop_length + 1
        n_tile = int(np.ceil(n_time_full / mask_90s.shape[1]))
        melodic_mask = np.tile(mask_90s, (1, n_tile))[:, :n_time_full].copy()
        del mask_90s

        # Check energy coverage of mask before splitting
        mask_energy_ratio = float(np.mean(melodic_mask))
        if mask_energy_ratio > 0.85:
            print(f"[melodic_split] {key} is essentially all melody ({mask_energy_ratio:.2f}) — skipping split")
            del left, right, melodic_mask
            _gc.collect()
            continue

        print(f"[melodic_split] {key} mask tiled to full audio (mean={mask_energy_ratio:.4f})")

        # Split
        _log_mem(f"[melodic_split] pre-STFT-split ({key}, mask={melodic_mask.nbytes/1024/1024:.1f}MB)")
        result = _split_melodic_stem(left, right, file_sr, melodic_mask,
                                      n_fft=n_fft, hop_length=hop_length)
        del melodic_mask

        if result is None:
            print(f"[melodic_split] quality gate failed for {key} — keeping original")
            del left, right
            _gc.collect()
            continue

        (lead_l, lead_r), (acc_l, acc_r) = result

        # Log split energies before deleting source arrays
        original_label = stem_data.get("label", key.title())
        lead_label, acc_label = _get_split_labels(original_label)
        lead_e = _rms((lead_l + lead_r) / 2)
        acc_e = _rms((acc_l + acc_r) / 2)
        total_e = _rms((left + right) / 2)
        print(f"[melodic_split] SPLIT SUCCESS: {original_label} → "
              f"{lead_label} (energy={lead_e/max(total_e,1e-8):.2f}) + "
              f"{acc_label} (energy={acc_e/max(total_e,1e-8):.2f})")
        del left, right, result

        # lead_label, acc_label, original_label already set above for logging
        lead_key = _label_to_key(lead_label)
        acc_key = _label_to_key(acc_label)

        # Avoid key collisions
        for k_ref in (lead_key, acc_key):
            if k_ref in refined and k_ref != key:
                n = 2
                while f"{k_ref}_{n}" in refined:
                    n += 1
                if k_ref == lead_key:
                    lead_key = f"{k_ref}_{n}"
                else:
                    acc_key = f"{k_ref}_{n}"

        # Write new stems
        lead_path = Path(out_dir) / f"{lead_key}.wav"
        acc_path = Path(out_dir) / f"{acc_key}.wav"
        _write_wav(lead_path, lead_l, lead_r, file_sr)
        _write_wav(acc_path, acc_l, acc_r, file_sr)

        lead_energy = _rms((lead_l + lead_r) / 2)
        acc_energy = _rms((acc_l + acc_r) / 2)
        del lead_l, lead_r, acc_l, acc_r

        # Remove original, add two new entries
        orig_path = Path(stem_data["path"])
        del refined[key]
        try:
            orig_path.unlink()
        except Exception:
            pass

        refined[lead_key] = {
            "path": str(lead_path),
            "energy": round(lead_energy, 6),
            "active": True,
            "label": lead_label,
        }
        refined[acc_key] = {
            "path": str(acc_path),
            "energy": round(acc_energy, 6),
            "active": True,
            "label": acc_label,
        }

        print(f"[melodic_split] {key} → {lead_key} (E={lead_energy:.4f}) + {acc_key} (E={acc_energy:.4f})")
        _gc.collect()
        _log_mem(f"[melodic_split] post-split ({key})")

    _log_mem(f"[melodic_split] END ({len(refined)} stems)")
    return refined


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
        # Convert MP3 → WAV via ffmpeg subprocess — avoids loading ~190MB float32
        # array per stem into Python memory. ffmpeg streams the conversion.
        try:
            _conv = subprocess.run(
                ["ffmpeg", "-y", "-i", str(mp3_tmp), "-ar", "44100", "-ac", "2", str(wav_dest)],
                capture_output=True, timeout=60,
            )
            if _conv.returncode != 0:
                raise RuntimeError(f"ffmpeg MP3→WAV failed: {_conv.stderr[-300:]}")
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
    with ThreadPoolExecutor(max_workers=1) as _pool:  # Sequential — eliminates overlapping conversion memory
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


def separate_stems(audio_path: str, song_id: str, progress_callback=None, instrument_hints: dict | None = None) -> dict:
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
        MAX_RETRIES = 2
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

      # Remove _raw_* intermediate files (refined stems are the final output)
      _raw_cleaned = 0
      for raw_file in out_dir.glob("_raw_*.wav"):
          try:
              raw_file.unlink()
              _raw_cleaned += 1
          except Exception:
              pass
      if _raw_cleaned:
          print(f"[processor] cleaned {_raw_cleaned} _raw_* intermediate files")

    _log_mem(f"[separate_stems] post-refine ({len(refined)} stems)")
    # Melodic split pass is now called from app.py after note extraction,
    # so pre-computed note events can be reused (avoids redundant TF inference).
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
    _ensure_imports()
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

