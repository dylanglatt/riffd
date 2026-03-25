"""
processor.py
Core audio processing pipeline:
  1. Stem separation via Demucs (6-stem model)
  2. Stereo-field analysis to split stems into individually panned instruments
  3. Spectral classification to label each sub-stem
  4. Tab/MIDI generation via Basic Pitch
"""

import struct as _struct
import shutil
import subprocess
import wave
from pathlib import Path

import numpy as np
import pandas as pd
from basic_pitch import ICASSP_2022_MODEL_PATH
from basic_pitch.inference import predict


UPLOAD_DIR = Path("uploads")
OUTPUT_DIR = Path("outputs")

# ─── Demucs Configuration ────────────────────────────────────────────────────
DEMUCS_MODEL = "htdemucs_6stems"
STEM_NAMES_6 = ["vocals", "drums", "bass", "guitar", "piano", "other"]
STEM_NAMES_4 = ["vocals", "drums", "bass", "other"]

# RMS threshold — below this a component is considered silent
SILENCE_THRESHOLD = 0.003

# Minimum component energy relative to the stem's total energy
# to be included (avoids showing ghost components)
MIN_RELATIVE_ENERGY = 0.08


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

def _detect_wav_format(filepath):
    """Detect WAV format tag: 1=PCM, 3=IEEE float."""
    try:
        with open(filepath, "rb") as f:
            riff = f.read(4)
            if riff != b"RIFF":
                return 1
            f.read(4)
            wav = f.read(4)
            if wav != b"WAVE":
                return 1
            while True:
                chunk_id = f.read(4)
                if len(chunk_id) < 4:
                    return 1
                chunk_size = _struct.unpack("<I", f.read(4))[0]
                if chunk_id == b"fmt ":
                    fmt_tag = _struct.unpack("<H", f.read(2))[0]
                    return fmt_tag
                f.seek(chunk_size, 1)
    except Exception:
        return 1


def _read_wav(filepath):
    """
    Read a WAV file (PCM or float32).
    Returns (left, right, sample_rate). Mono files return identical L/R.
    """
    fmt_tag = _detect_wav_format(filepath)
    is_float = fmt_tag == 3

    with wave.open(filepath, "rb") as wf:
        sr = wf.getframerate()
        n_ch = wf.getnchannels()
        n_frames = wf.getnframes()
        sw = wf.getsampwidth()
        raw = wf.readframes(n_frames)

    if is_float and sw == 4:
        samples = np.frombuffer(raw, dtype=np.float32).astype(np.float64)
    elif sw == 2:
        samples = np.frombuffer(raw, dtype=np.int16).astype(np.float64) / 32768.0
    elif sw == 4:
        samples = np.frombuffer(raw, dtype=np.int32).astype(np.float64) / 2147483648.0
    else:
        samples = np.frombuffer(raw, dtype=np.uint8).astype(np.float64)
        samples = (samples - 128.0) / 128.0

    if n_ch >= 2:
        samples = samples.reshape(-1, n_ch)
        return samples[:, 0].copy(), samples[:, 1].copy(), sr
    else:
        mono = samples.flatten()
        return mono, mono.copy(), sr


def _write_wav(filepath, left, right, sr):
    """Write stereo 16-bit WAV."""
    interleaved = np.column_stack([left, right])
    data = (np.clip(interleaved, -1.0, 1.0) * 32767).astype(np.int16)
    with wave.open(str(filepath), "wb") as wf:
        wf.setnchannels(2)
        wf.setsampwidth(2)
        wf.setframerate(sr)
        wf.writeframes(data.tobytes())


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
    win = np.hanning(N)

    orig_len = len(left)
    pad = (hop - (orig_len % hop)) % hop + N
    left_p = np.pad(left, (0, pad))
    right_p = np.pad(right, (0, pad))
    out_len = len(left_p)

    n_frames = (out_len - N) // hop + 1

    # Output accumulators (center, left-panned, right-panned) × (L, R channels)
    c_l = np.zeros(out_len)
    c_r = np.zeros(out_len)
    p_ll = np.zeros(out_len)
    p_lr = np.zeros(out_len)
    p_rl = np.zeros(out_len)
    p_rr = np.zeros(out_len)
    win_sq = np.zeros(out_len)

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

    # Normalize overlap-add
    norm = np.maximum(win_sq[:orig_len], 1e-8)

    components = {}

    cl = c_l[:orig_len] / norm
    cr = c_r[:orig_len] / norm
    if _rms((cl + cr) / 2) > SILENCE_THRESHOLD:
        components["center"] = (cl, cr)

    ll = p_ll[:orig_len] / norm
    lr = p_lr[:orig_len] / norm
    if _rms(ll) > SILENCE_THRESHOLD * 0.5:
        components["left"] = (ll, lr)

    rl = p_rl[:orig_len] / norm
    rr = p_rr[:orig_len] / norm
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
            return "Lead Vocal"
        elif position == "left":
            return "Backing Vocal"
        else:
            return "Harmony Vocal"

    if stem_category == "guitar":
        if c > 3000 and zcr > 0.15:
            return "Banjo"
        if c > 2200 and bw > 1200 and zcr > 0.10:
            if position == "center":
                return "Acoustic Guitar"
            return "Acoustic Guitar"
        if c > 1800 and c_std > 400:
            if position == "center":
                return "Lead Guitar"
            return "Guitar Layer"
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
            return "Keys"
        if c > 800:
            return "Instrument"
        return "Synth Pad"

    if stem_category == "piano":
        if c > 1500 and bw > 1500:
            return "Piano"
        return "Keys"

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
    if any(k in label_lower for k in ("piano", "keys", "organ", "synth")):
        return "note_list"
    return "note_list"


# ─── Main Separation Pipeline ────────────────────────────────────────────────

def separate_stems(audio_path: str, song_id: str, progress_callback=None) -> dict:
    """
    Full pipeline:
      1. Run Demucs for initial separation
      2. Analyze stereo field of each stem
      3. Split into individually panned components
      4. Classify each component
      5. Return only stems with meaningful audio content

    Returns dict: {stem_key: {path, energy, active, label}}
    """
    audio_path = Path(audio_path)
    out_dir = OUTPUT_DIR / song_id / "stems"
    out_dir.mkdir(parents=True, exist_ok=True)

    # ── Step 1: Run Demucs ──
    model = DEMUCS_MODEL
    stem_names = STEM_NAMES_6

    if progress_callback:
        progress_callback("Running Demucs separation...")

    result = subprocess.run(
        ["python", "-m", "demucs", "--out", str(out_dir), "--name", model, str(audio_path)],
        capture_output=True, text=True,
    )

    # Fallback to 4-stem if 6-stem fails
    if result.returncode != 0 and model == "htdemucs_6stems":
        model = "htdemucs"
        stem_names = STEM_NAMES_4
        result = subprocess.run(
            ["python", "-m", "demucs", "--out", str(out_dir), "--name", model, str(audio_path)],
            capture_output=True, text=True,
        )

    if result.returncode != 0:
        raise RuntimeError(f"Demucs failed:\n{result.stderr}")

    song_name = audio_path.stem
    stem_dir = out_dir / model / song_name

    # Copy Demucs output to standard location
    raw_stems = {}
    for stem_name in stem_names:
        stem_file = stem_dir / f"{stem_name}.wav"
        if stem_file.exists():
            dest = out_dir / f"_raw_{stem_name}.wav"
            shutil.copy2(stem_file, dest)
            raw_stems[stem_name] = str(dest)

    # ── Step 2: Refine each stem ──
    if progress_callback:
        progress_callback("Analyzing instruments...")

    refined = {}

    for stem_name, raw_path in raw_stems.items():
        # Drums and bass: keep as-is (Demucs handles these well)
        if stem_name in ("drums", "bass"):
            dest = out_dir / f"{stem_name}.wav"
            shutil.copy2(raw_path, dest)
            left, right, sr = _read_wav(raw_path)
            energy = _rms((left + right) / 2)
            refined[stem_name] = {
                "path": str(dest),
                "energy": round(energy, 6),
                "active": energy > SILENCE_THRESHOLD,
                "label": "Drum Kit" if stem_name == "drums" else "Bass Guitar",
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
            continue  # Skip entirely silent stems

        if progress_callback:
            progress_callback(f"Analyzing {stem_name}...")

        # Split by stereo panning
        components = _stereo_separate(left, right)

        if not components:
            # No meaningful separation — keep original
            dest = out_dir / f"{stem_name}.wav"
            shutil.copy2(raw_path, dest)
            mono = (left + right) / 2
            feat = _spectral_features(mono, sr)
            label = _classify_component(feat, stem_name, "center")
            refined[stem_name] = {
                "path": str(dest),
                "energy": round(stem_energy, 6),
                "active": True,
                "label": label,
            }
            continue

        # Classify each component
        sub_parts = []
        for position, (comp_l, comp_r) in components.items():
            mono = (comp_l + comp_r) / 2
            energy = _rms(mono)

            # Skip components that are too quiet relative to the stem
            if energy < stem_energy * MIN_RELATIVE_ENERGY:
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

        # Multiple components — save each with numbered duplicates
        _save_sub_parts(sub_parts, sr, out_dir, refined)

    # Also save the full original stems directory for the mixer "All" option
    # (the refined stems are what the user sees)

    return refined


def _label_to_key(label):
    """Convert a label like 'Lead Guitar' to a dict key like 'lead_guitar'."""
    return label.lower().replace(" ", "_")


# Variation names for duplicates within an instrument family
_FAMILY_VARIATIONS = {
    "Lead Guitar": ["Lead Guitar", "Guitar Overdub", "Guitar Layer"],
    "Rhythm Guitar": ["Rhythm Guitar", "Strumming Guitar", "Guitar Comping"],
    "Acoustic Guitar": ["Acoustic Guitar", "Acoustic Layer", "Fingerpicking Guitar"],
    "Guitar": ["Guitar", "Guitar Layer", "Guitar Part"],
    "Guitar Layer": ["Guitar Layer", "Guitar Overdub", "Guitar Part"],
    "Lead Vocal": ["Lead Vocal", "Vocal Double", "Vocal Layer"],
    "Backing Vocal": ["Backing Vocal", "Harmony Vocal", "Vocal Pad"],
    "Harmony Vocal": ["Harmony Vocal", "Backing Vocal", "Vocal Layer"],
    "Keys": ["Keys", "Synth", "Pad"],
    "Instrument": ["Instrument", "Texture", "Layer"],
}


def _save_sub_parts(parts, sr, out_dir, refined):
    """Save multiple sub-parts with musically meaningful variation labels."""
    # Count label occurrences
    label_counts = {}
    for part in parts:
        base = part["label"]
        label_counts[base] = label_counts.get(base, 0) + 1

    label_index = {}

    for part in parts:
        base_label = part["label"]
        count = label_counts[base_label]

        if count > 1:
            idx = label_index.get(base_label, 0)
            label_index[base_label] = idx + 1
            # Use variation names instead of numbered duplicates
            variations = _FAMILY_VARIATIONS.get(base_label, [base_label])
            if idx < len(variations):
                label = variations[idx]
            else:
                label = f"{base_label} {idx + 1}"
        else:
            label = base_label

        key = _label_to_key(label)

        # Avoid key collisions
        orig_key = key
        n = 2
        while key in refined:
            key = f"{orig_key}_{n}"
            label = f"{label} {n}" if not label[-1].isdigit() else label
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


def generate_tabs(stem_path: str, song_id: str, stem_name: str, label: str = "", bpm: float = 120.0) -> dict:
    """
    Run Basic Pitch on a stem audio file with per-instrument parameters.
    Returns paths to: MIDI file, note events CSV, and rendered tab text.
    """
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

    midi_path = tab_dir / f"{stem_name}.mid"
    midi_data.write(str(midi_path))

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


def _normalize_note_events(note_events) -> pd.DataFrame:
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


def _filter_by_confidence(note_events, threshold: float) -> pd.DataFrame:
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
