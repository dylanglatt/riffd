"""
music_intelligence.py
Key detection (Krumhansl-Schmuckler), BPM estimation, chord progression via
template matching against diatonic chord pitch-class sets.
"""

from collections import Counter
from compat import patch_lzma as _patch_lzma

# Heavy imports deferred to first use
np = None
pd = None


def _ensure_imports():
    global np, pd
    if np is not None:
        return
    _patch_lzma()
    import numpy as _np
    import pandas as _pd
    np = _np
    pd = _pd
    print("[music_intelligence] heavy imports loaded (numpy, pandas)")


NOTE_NAMES = ["C", "C#", "D", "D#", "E", "F", "F#", "G", "G#", "A", "A#", "B"]

MAJOR_INTERVALS = [0, 2, 4, 5, 7, 9, 11]
MINOR_INTERVALS = [0, 2, 3, 5, 7, 8, 10]

MAJOR_NUMERALS = ["I", "ii", "iii", "IV", "V", "vi", "vii\u00b0"]
MINOR_NUMERALS = ["i", "ii\u00b0", "III", "iv", "v", "VI", "VII"]

# Stored as plain lists — converted to np.array on first use
_MAJOR_PROFILE_RAW = [6.35, 2.23, 3.48, 2.33, 4.38, 4.09, 2.52, 5.19, 2.39, 3.66, 2.29, 2.88]
_MINOR_PROFILE_RAW = [6.33, 2.68, 3.52, 5.38, 2.60, 3.53, 2.54, 4.75, 3.98, 2.69, 3.34, 3.17]
MAJOR_PROFILE = None
MINOR_PROFILE = None


def _ensure_profiles():
    global MAJOR_PROFILE, MINOR_PROFILE
    if MAJOR_PROFILE is not None:
        return
    _ensure_imports()
    MAJOR_PROFILE = np.array(_MAJOR_PROFILE_RAW)
    MINOR_PROFILE = np.array(_MINOR_PROFILE_RAW)

# Triad intervals relative to root (in semitones)
MAJOR_TRIAD = [0, 4, 7]
MINOR_TRIAD = [0, 3, 7]
DIM_TRIAD = [0, 3, 6]

# For each scale degree in a major key, the chord quality
MAJOR_KEY_CHORDS = [
    (MAJOR_TRIAD, "I"),     # I
    (MINOR_TRIAD, "ii"),    # ii
    (MINOR_TRIAD, "iii"),   # iii
    (MAJOR_TRIAD, "IV"),    # IV
    (MAJOR_TRIAD, "V"),     # V
    (MINOR_TRIAD, "vi"),    # vi
    (DIM_TRIAD, "vii\u00b0"),  # vii°
]

MINOR_KEY_CHORDS = [
    (MINOR_TRIAD, "i"),     # i
    (DIM_TRIAD, "ii\u00b0"),   # ii°
    (MAJOR_TRIAD, "III"),   # III
    (MINOR_TRIAD, "iv"),    # iv
    (MINOR_TRIAD, "v"),     # v
    (MAJOR_TRIAD, "VI"),    # VI
    (MAJOR_TRIAD, "VII"),   # VII
]

# Stems to exclude from harmonic analysis
NON_HARMONIC = {"vocals", "lead_vocal", "backing_vocals", "drums"}

# Stem priority for harmonic content (higher = preferred)
STEM_PRIORITY = {
    "piano": 10, "keys": 10,
    "rhythm_guitar": 8, "acoustic_guitar": 8,
    "guitar": 7, "other": 5,
    "lead_guitar": 4, "lead_guitar_1": 4, "lead_guitar_2": 4,
    "banjo": 6,
}

# Minimum confidence to report a progression
MIN_PROGRESSION_CONFIDENCE = 0.35


# ─── Key Detection (Essentia — primary) ──────────────────────────────────────

# Map Essentia key strings to our note index
_ESSENTIA_KEY_MAP = {
    "C": 0, "C#": 1, "Db": 1, "D": 2, "D#": 3, "Eb": 3,
    "E": 4, "F": 5, "F#": 6, "Gb": 6, "G": 7, "G#": 8,
    "Ab": 8, "A": 9, "A#": 10, "Bb": 10, "B": 11,
}


def detect_key_from_audio(audio_path):
    """
    Detect key directly from audio file using Essentia.
    Works on any audio format (mp3, wav, etc).
    Returns (key_num, mode_num, confidence) — same format as detect_key_from_notes.
    mode_num: 1=major, 0=minor
    """
    try:
        import essentia.standard as es

        # Load audio at 44100Hz mono (Essentia's default)
        audio = es.MonoLoader(filename=str(audio_path), sampleRate=44100)()

        # KeyExtractor runs HPCP + key profile correlation internally
        # It's the same Krumhansl-Schmuckler idea but on raw audio chromagram
        # which is far more accurate than routing through Basic Pitch first
        key, scale, confidence = es.KeyExtractor()(audio)

        key_num = _ESSENTIA_KEY_MAP.get(key, -1)
        mode_num = 1 if scale == "major" else 0

        print(f"[essentia] key={key} {scale} confidence={confidence:.3f}")
        return key_num, mode_num, float(confidence)

    except Exception as e:
        print(f"[essentia] key detection failed: {e}")
        return -1, -1, 0.0


def detect_bpm_from_audio(audio_path):
    """
    Detect BPM directly from audio file using Essentia's RhythmExtractor.
    Returns (bpm, confidence).
    """
    try:
        import essentia.standard as es

        audio = es.MonoLoader(filename=str(audio_path), sampleRate=44100)()
        rhythm = es.RhythmExtractor2013(method="multifeature")
        bpm, beats, beats_confidence, _, beats_intervals = rhythm(audio)

        # beats_confidence can be a scalar float or array depending on Essentia version —
        # atleast_1d handles both so len() doesn't fail on a plain float
        import numpy as _np
        beats_conf_arr = _np.atleast_1d(beats_confidence)
        conf = float(beats_conf_arr.mean()) if len(beats_conf_arr) > 0 else 0.0
        # Clamp to plausible range
        if bpm < 40 or bpm > 250:
            return 0, 0.0

        print(f"[essentia] bpm={bpm:.1f} confidence={conf:.3f} ({len(beats)} beats)")
        return round(float(bpm), 1), round(min(1.0, conf), 3)

    except Exception as e:
        print(f"[essentia] bpm detection failed: {e}")
        return 0, 0.0


# ─── Key Detection (Krumhansl-Schmuckler fallback from note data) ────────────

def detect_key_from_notes(note_events_df):
    """Fallback: detect key from Basic Pitch note data. Used when Essentia unavailable."""
    _ensure_profiles()
    if note_events_df is None or len(note_events_df) == 0:
        return -1, -1, 0.0
    pitches = note_events_df["pitch_midi"].values.astype(int)
    durations = (note_events_df["end_time_s"].values - note_events_df["start_time_s"].values).clip(0.01)
    pc_hist = np.zeros(12)
    for p, d in zip(pitches, durations):
        pc_hist[p % 12] += d
    if pc_hist.sum() < 0.01:
        return -1, -1, 0.0
    pc_hist /= pc_hist.sum()
    best_key, best_mode, best_corr = 0, 1, -1.0
    for root in range(12):
        for mode, profile in [(1, MAJOR_PROFILE), (0, MINOR_PROFILE)]:
            rotated = np.roll(profile, root)
            corr = np.corrcoef(pc_hist, rotated)[0, 1]
            if corr > best_corr:
                best_corr, best_key, best_mode = corr, root, mode
    return best_key, best_mode, float(max(best_corr, 0))


def format_key(key_num, mode_num):
    if key_num < 0 or key_num > 11:
        return "Unknown"
    return f"{NOTE_NAMES[key_num]} {'Major' if mode_num == 1 else 'Minor'}"


# ─── BPM Estimation (hardened with forced plausibility) ───────────────────────

def estimate_bpm(note_events_df):
    """
    Estimate BPM from inter-onset intervals.
    Uses a constrained approach:
    1. Build IOI histogram
    2. Find peaks in the 60-180 BPM range only (the "felt pulse" range)
    3. Score by beat-level IOI strength only (not subdivisions)
    4. Force half/double-time correction for any outlier
    5. Return (bpm, confidence) — bpm is always in 60-180 range

    Returns (bpm, confidence).
    """
    _ensure_imports()
    if note_events_df is None or len(note_events_df) < 10:
        return 0, 0.0

    onsets = np.sort(np.unique(np.round(note_events_df["start_time_s"].values, 3)))
    iois = np.diff(onsets)
    iois = iois[(iois > 0.05) & (iois < 4.0)]
    if len(iois) < 5:
        return 0, 0.0

    # Build IOI histogram with moderate smoothing
    bin_res = 0.008  # ~8ms bins
    bins = np.arange(0, 2.0, bin_res)
    hist, _ = np.histogram(iois, bins=bins)
    kernel = np.array([0.1, 0.25, 0.3, 0.25, 0.1])
    hist_s = np.convolve(hist, kernel, mode="same").astype(float)

    # Score ONLY in the plausible "felt pulse" range: 60-180 BPM
    # This prevents subdivision peaks (sixteenth notes) from winning
    bpm_scores = {}
    for bpm in range(60, 181):
        beat_dur = 60.0 / bpm
        idx = int(beat_dur / bin_res)
        sc = 0.0
        # Check a ±2 bin neighborhood at the beat level
        for offset in range(-2, 3):
            bidx = idx + offset
            if 0 <= bidx < len(hist_s):
                w = [0.3, 0.7, 1.0, 0.7, 0.3][offset + 2]
                sc += hist_s[bidx] * w
        bpm_scores[bpm] = sc

    if not bpm_scores:
        return 0, 0.0

    # Top 5 candidates
    sorted_bpms = sorted(bpm_scores.items(), key=lambda x: -x[1])
    top_bpm, top_score = sorted_bpms[0]

    # Confidence: how much the peak stands out
    all_scores = np.array([s for _, s in sorted_bpms])
    if all_scores.sum() > 0:
        # Peak prominence: top score relative to mean
        mean_score = all_scores.mean()
        confidence = min(1.0, (top_score / max(mean_score * 3, 0.01)))
    else:
        confidence = 0.0

    # Additional plausibility: prefer 75-145 range slightly
    if 75 <= top_bpm <= 145:
        confidence = min(1.0, confidence * 1.1)
    elif top_bpm < 65 or top_bpm > 170:
        confidence *= 0.7

    best_bpm = round(top_bpm)
    print(f"[bpm] detected={best_bpm}, confidence={confidence:.2f}, top5={[(b,round(s,1)) for b,s in sorted_bpms[:5]]}")
    return float(best_bpm), round(confidence, 2)


# ─── Chord Template Matching ─────────────────────────────────────────────────

def _build_chord_templates(key_num, mode_num):
    """
    Build the 7 diatonic chord templates for a key.
    Each template is a (pitch_class_set, numeral, root_pc).
    """
    if mode_num == 1:
        scale_intervals = MAJOR_INTERVALS
        chord_defs = MAJOR_KEY_CHORDS
    else:
        scale_intervals = MINOR_INTERVALS
        chord_defs = MINOR_KEY_CHORDS

    templates = []
    for degree, (triad_intervals, numeral) in enumerate(chord_defs):
        root_pc = (key_num + scale_intervals[degree]) % 12
        chord_pcs = frozenset((root_pc + i) % 12 for i in triad_intervals)
        templates.append({
            "numeral": numeral,
            "root_pc": root_pc,
            "pcs": chord_pcs,
            "degree": degree,
        })

    return templates


def _score_chord_against_histogram(template, pc_hist, bass_pc_hist):
    """
    Score how well a chord template matches the observed pitch-class distribution.

    Uses three signals:
    1. Overlap: how much of the window's energy is on chord tones
    2. Bass match: does the bass agree with the chord root
    3. Completeness: are all chord tones present
    """
    total = pc_hist.sum()
    if total < 0.001:
        return 0.0

    chord_pcs = template["pcs"]
    root_pc = template["root_pc"]

    # 1. Overlap: fraction of total energy on chord tones (0-1)
    chord_energy = sum(pc_hist[pc] for pc in chord_pcs)
    overlap = chord_energy / total

    # 2. Bass agreement: does the bass note match the root? (0 or 1)
    bass_total = bass_pc_hist.sum()
    bass_match = 0.0
    if bass_total > 0.01:
        bass_match = bass_pc_hist[root_pc] / bass_total

    # 3. Completeness: are all 3 chord tones present? (0-1)
    present = sum(1 for pc in chord_pcs if pc_hist[pc] > total * 0.02)
    completeness = present / len(chord_pcs)

    # Weighted score: bass is most important, then overlap, then completeness
    score = bass_match * 0.45 + overlap * 0.35 + completeness * 0.20

    return score


# ─── Progression Estimation ──────────────────────────────────────────────────

def estimate_progression_from_stems(stem_events, key_num, mode_num, bpm=120.0):
    """
    Estimate chord progression using template matching against diatonic chords.

    1. Select best harmonic stem(s)
    2. Window the song into ~2s segments
    3. Build pitch-class histogram per window
    4. Score each diatonic chord template against the histogram
    5. Pick best chord per window
    6. Collapse, find pattern, compute confidence

    Returns: (progression_string, confidence) or just string
    """
    if not stem_events or key_num < 0:
        return "Unknown"

    # ── Select stems ──
    bass_df = None
    harmony_df = None
    harmony_name = None

    # Find bass
    for name, df in stem_events.items():
        if isinstance(df, pd.DataFrame) and len(df) > 0 and "bass" in name.lower():
            bass_df = df
            break

    # Find best harmonic stem by priority
    best_priority = -1
    best_frames = []
    for name, df in stem_events.items():
        if not isinstance(df, pd.DataFrame) or len(df) == 0:
            continue
        name_lower = name.lower()
        # Skip non-harmonic
        if any(excl in name_lower for excl in NON_HARMONIC):
            continue
        if "bass" in name_lower:
            continue  # bass handled separately

        priority = 0
        for stem_key, p in STEM_PRIORITY.items():
            if stem_key in name_lower:
                priority = max(priority, p)
                break
        if not priority:
            priority = 3  # unknown harmonic stem

        if priority > best_priority:
            best_priority = priority
            best_frames = [df]
            harmony_name = name
        elif priority == best_priority:
            best_frames.append(df)

    if best_frames:
        harmony_df = pd.concat(best_frames, ignore_index=True)

    # Fallback: use all non-vocal/drum stems
    if harmony_df is None and bass_df is None:
        fallback = []
        for name, df in stem_events.items():
            if isinstance(df, pd.DataFrame) and len(df) > 0:
                if not any(excl in name.lower() for excl in NON_HARMONIC):
                    fallback.append(df)
        if fallback:
            harmony_df = pd.concat(fallback, ignore_index=True)
            harmony_name = "fallback"

    if harmony_df is None and bass_df is None:
        return "Unknown"

    print(f"[progression] using stems: bass={'yes' if bass_df is not None else 'no'}, harmony={harmony_name} ({len(harmony_df) if harmony_df is not None else 0} notes)")

    # ── Build chord templates ──
    templates = _build_chord_templates(key_num, mode_num)
    print(f"[progression] chord templates for {format_key(key_num, mode_num)}:")
    for t in templates:
        pcs_str = ",".join(NOTE_NAMES[pc] for pc in sorted(t['pcs']))
        print(f"[progression]   {t['numeral']:6s} root={NOTE_NAMES[t['root_pc']]:2s} pcs={{{pcs_str}}}")

    # ── Window the song ──
    max_time = 0
    if bass_df is not None:
        max_time = max(max_time, bass_df["end_time_s"].max())
    if harmony_df is not None:
        max_time = max(max_time, harmony_df["end_time_s"].max())

    if max_time < 2:
        return "Unknown"

    # Use ~2 second windows, but try to align with beats
    window = 2.0
    n_windows = min(int(max_time / window), 300)

    # ── Per-window chord detection ──
    window_chords = []  # list of (numeral, score)
    MIN_NOTE_DUR = 0.1  # ignore notes shorter than 100ms

    for wi in range(n_windows):
        t0 = wi * window
        t1 = t0 + window

        # Build pitch-class histograms
        harmony_hist = np.zeros(12)
        bass_hist = np.zeros(12)

        # Harmony histogram (filter short notes, weight by duration + register)
        if harmony_df is not None:
            mask = (harmony_df["start_time_s"] < t1) & (harmony_df["end_time_s"] > t0)
            notes = harmony_df[mask]
            if len(notes) > 0:
                pitches = notes["pitch_midi"].values.astype(int)
                durs = (np.minimum(notes["end_time_s"].values, t1) -
                        np.maximum(notes["start_time_s"].values, t0)).clip(0.001)
                # Filter short ornamental notes
                keep = durs >= MIN_NOTE_DUR
                if keep.any():
                    pitches, durs = pitches[keep], durs[keep]
                for p, d in zip(pitches, durs):
                    harmony_hist[p % 12] += d

        # Bass histogram (strong root signal)
        if bass_df is not None:
            mask = (bass_df["start_time_s"] < t1) & (bass_df["end_time_s"] > t0)
            notes = bass_df[mask]
            if len(notes) > 0:
                pitches = notes["pitch_midi"].values.astype(int)
                durs = (np.minimum(notes["end_time_s"].values, t1) -
                        np.maximum(notes["start_time_s"].values, t0)).clip(0.001)
                keep = durs >= 0.05
                if keep.any():
                    pitches, durs = pitches[keep], durs[keep]
                for p, d in zip(pitches, durs):
                    bass_hist[p % 12] += d

        # Combined histogram for chord matching
        combined_hist = harmony_hist + bass_hist * 0.5  # bass also contributes to pitch content

        if combined_hist.sum() < 0.01:
            continue

        # Score each chord template
        scores = []
        for t in templates:
            sc = _score_chord_against_histogram(t, combined_hist, bass_hist)
            scores.append((sc, t["numeral"]))

        scores.sort(key=lambda x: -x[0])
        best_score, best_numeral = scores[0]

        # Only accept if score exceeds minimum
        if best_score >= 0.15:
            window_chords.append((best_numeral, best_score))

    if not window_chords:
        print("[progression] no chords detected above threshold")
        return "Unknown"

    # ── Step 1: Multi-pass smoothing to remove noise ──
    raw_chords = [n for n, _ in window_chords]
    raw_scores = [s for _, s in window_chords]
    smoothed = list(raw_chords)

    # Pass A: Replace isolated 1-window blips with their neighbor
    for i in range(1, len(smoothed) - 1):
        if smoothed[i] != smoothed[i-1] and smoothed[i] != smoothed[i+1] and raw_scores[i] < 0.45:
            smoothed[i] = smoothed[i-1]

    # Pass B: Replace isolated 2-window segments surrounded by the same chord
    for i in range(1, len(smoothed) - 2):
        if (smoothed[i-1] == smoothed[i+2] and
            smoothed[i] != smoothed[i-1] and
            raw_scores[i] < 0.5 and raw_scores[i+1] < 0.5):
            smoothed[i] = smoothed[i-1]
            smoothed[i+1] = smoothed[i-1]

    # ── Step 2: Collapse consecutive duplicates ──
    collapsed = []
    collapsed_scores = []
    prev = None
    for i, numeral in enumerate(smoothed):
        if numeral != prev:
            collapsed.append(numeral)
            collapsed_scores.append(raw_scores[i])
            prev = numeral
        else:
            collapsed_scores[-1] = max(collapsed_scores[-1], raw_scores[i])

    print(f"[progression] smoothed+collapsed ({len(collapsed)} chords): {' '.join(collapsed[:24])}{'...' if len(collapsed)>24 else ''}")

    # ── Step 3: Remove chords that appear only once with low score ──
    if len(collapsed) > 4:
        chord_counts = Counter(collapsed)
        cleaned = []
        cleaned_scores = []
        for ch, sc in zip(collapsed, collapsed_scores):
            if chord_counts[ch] >= 2 or sc >= 0.4:
                cleaned.append(ch)
                cleaned_scores.append(sc)
        if len(cleaned) >= 3:
            collapsed = cleaned
            collapsed_scores = cleaned_scores

    if len(collapsed) < 2:
        print("[progression] too few chords after cleaning")
        return "Unknown"

    # ── Step 4: Find the core repeating harmonic loop ──
    pattern = _find_pattern(collapsed)

    # ── Step 5: Compute confidence ──
    avg_score = np.mean(collapsed_scores)
    pattern_count = _count_pattern_occurrences(collapsed, pattern)
    # Higher confidence if pattern repeats many times relative to sequence length
    repetition_ratio = (pattern_count * len(pattern)) / max(len(collapsed), 1)
    confidence = min(1.0, avg_score * 0.4 + repetition_ratio * 0.4 + (0.2 if pattern_count >= 3 else 0))

    print(f"[progression] pattern: {pattern}, repeats={pattern_count}, avg_score={avg_score:.2f}, rep_ratio={repetition_ratio:.2f}, confidence={confidence:.2f}")

    if confidence < MIN_PROGRESSION_CONFIDENCE:
        print(f"[progression] SUPPRESSED — confidence {confidence:.2f} < {MIN_PROGRESSION_CONFIDENCE}")
        return None

    # ── Format output ──
    matched = _match_known(pattern)
    result = matched if matched else " – ".join(pattern)
    print(f"[progression] RESULT: {result}")
    return result


def _count_pattern_occurrences(sequence, pattern):
    """Count how many times a pattern appears in the sequence."""
    if not pattern or not sequence:
        return 0
    n = len(pattern)
    count = 0
    for i in range(len(sequence) - n + 1):
        if sequence[i:i + n] == pattern:
            count += 1
    return count


def _find_pattern(seq, min_len=3, max_len=5):
    if len(seq) <= max_len:
        return seq
    best, best_score = None, 0
    for length in range(min_len, max_len + 1):
        pats = Counter()
        for i in range(len(seq) - length + 1):
            pats[tuple(seq[i:i + length])] += 1
        if pats:
            top_pat, top_count = pats.most_common(1)[0]
            # Score: repetitions * preference for length 4
            sc = top_count * (1.15 if length == 4 else 1.0)
            if sc > best_score:
                best_score = sc
                best = list(top_pat)
    if best and best_score >= 2:
        return best
    # Fallback: most common 3-4 chords by frequency
    counts = Counter(seq)
    top = [c for c, _ in counts.most_common(4)]
    return top if len(top) >= 3 else seq[:4]


KNOWN_PROGRESSIONS = [
    (["I", "V", "vi", "IV"], "I – V – vi – IV"),
    (["I", "IV", "V", "IV"], "I – IV – V – IV"),
    (["I", "IV", "vi", "V"], "I – IV – vi – V"),
    (["vi", "IV", "I", "V"], "vi – IV – I – V"),
    (["I", "vi", "IV", "V"], "I – vi – IV – V"),
    (["I", "IV", "V", "I"], "I – IV – V – I"),
    (["I", "V", "IV", "V"], "I – V – IV – V"),
    (["I", "IV", "V"], "I – IV – V"),
    (["I", "V", "IV"], "I – V – IV"),
    (["I", "iii", "IV", "V"], "I – iii – IV – V"),
    (["I", "V", "vi", "iii", "IV"], "I – V – vi – iii – IV"),
    (["ii", "V", "I"], "ii – V – I"),
    (["i", "VI", "III", "VII"], "i – VI – III – VII"),
    (["i", "iv", "v", "i"], "i – iv – v – i"),
    (["i", "VII", "VI", "V"], "i – VII – VI – V"),
    (["i", "iv", "VII", "III"], "i – iv – VII – III"),
    (["I", "IV", "I", "V"], "I – IV – I – V"),
    (["I", "V", "I", "IV"], "I – V – I – IV"),
]


def _match_known(pattern):
    for known, label in KNOWN_PROGRESSIONS:
        n = len(known)
        if pattern[:n] == known:
            return label
        doubled = known * 2
        for i in range(n):
            if pattern[:n] == doubled[i:i + n]:
                return label
    return None


# ─── Full Analysis (hybrid: external chords → audio fallback) ─────────────

def analyze_song_from_notes(all_note_events, song_name="", artist="", lyrics_text=None, audio_key_override=None):
    """
    Complete song analysis.
    1. Use Essentia key/BPM if provided (audio_key_override), else detect from notes
    2. Try external chord source for progression
    3. Fall back to audio-based estimation
    4. Build section-based harmonic analysis
    5. Suppress low-confidence results

    audio_key_override: dict with key_num, mode_num, key_conf, bpm, bpm_conf from Essentia.
                        When provided, skips note-based key/BPM detection.

    Returns dict with key, bpm, progression, harmonic_sections, etc.
    """
    _ensure_imports()
    frames = []
    if isinstance(all_note_events, dict):
        for df in all_note_events.values():
            if isinstance(df, pd.DataFrame) and len(df) > 0:
                frames.append(df)
    elif isinstance(all_note_events, pd.DataFrame):
        frames = [all_note_events]

    if not frames:
        return {"key": "Unknown", "key_num": -1, "mode_num": -1, "bpm": 120, "progression": None}

    merged = pd.concat(frames, ignore_index=True)

    # Key detection — prefer Essentia (audio-based) over note-based
    if audio_key_override and audio_key_override.get("key_num", -1) >= 0:
        key_num = audio_key_override["key_num"]
        mode_num = audio_key_override["mode_num"]
        key_conf = audio_key_override["key_conf"]
        key_str = format_key(key_num, mode_num)
        print(f"[intel] key (Essentia override): {key_str} (confidence={key_conf:.2f})")

        bpm = audio_key_override.get("bpm", 120)
        bpm_conf = audio_key_override.get("bpm_conf", 0)
        print(f"[intel] bpm (Essentia override): {bpm} (confidence={bpm_conf:.2f})")
    else:
        # Fallback: detect from note data (Krumhansl-Schmuckler)
        key_num, mode_num, key_conf = detect_key_from_notes(merged)
        key_str = format_key(key_num, mode_num)
        print(f"[intel] key (note-based fallback): {key_str} (confidence={key_conf:.2f})")

        bpm, bpm_conf = estimate_bpm(merged)
        print(f"[intel] bpm (note-based fallback): {bpm} (confidence={bpm_conf:.2f})")

    # ── Progression: try external source first ──
    progression = None
    prog_source = "none"
    prog_confidence = 0.0

    if song_name and artist:
        try:
            from chord_source import get_chord_progression
            ext_result, ext_source, ext_conf = get_chord_progression(
                song_name, artist, key_num, mode_num
            )
            if ext_result and ext_conf >= 0.4:
                # ext_result is now a structured dict
                if isinstance(ext_result, dict):
                    progression = ext_result.get("matched_progression")
                    prog_source = ext_source
                    prog_confidence = ext_conf
                    print(f"[intel] progression from {ext_source}: {progression} (confidence={ext_conf:.2f})")
                    print(f"[intel]   degrees={ext_result.get('degrees')}, chords={ext_result.get('chords')}")
                elif isinstance(ext_result, str):
                    progression = ext_result
                    prog_source = ext_source
                    prog_confidence = ext_conf
        except Exception as e:
            print(f"[intel] external chord lookup failed: {e}")
            import traceback; traceback.print_exc()

    # ── Fallback: audio-based estimation ──
    if progression is None and key_num >= 0:
        print("[intel] falling back to audio-based progression estimation")
        if isinstance(all_note_events, dict):
            audio_prog = estimate_progression_from_stems(all_note_events, key_num, mode_num, bpm)
        else:
            audio_prog = estimate_progression_from_stems({"other": merged}, key_num, mode_num, bpm)

        if audio_prog and audio_prog != "Unknown":
            progression = audio_prog
            prog_source = "audio"
            prog_confidence = 0.40  # moderate — audio is less reliable than web chords
            print(f"[intel] progression from audio: {audio_prog} (confidence=0.40)")
        else:
            print("[intel] audio fallback also failed — no progression")

    # ── Final: suppress if confidence too low ──
    if progression and prog_confidence < MIN_PROGRESSION_CONFIDENCE:
        print(f"[intel] progression SUPPRESSED (confidence {prog_confidence:.2f} < {MIN_PROGRESSION_CONFIDENCE})")
        progression = None
        prog_source = "none"

    # ── Section-based harmonic analysis ──
    harmonic_sections = []
    try:
        from harmonic_analysis import build_harmonic_analysis
        # Get raw chord sequence from the external result if available
        raw_chords = None
        if song_name and artist:
            try:
                from chord_source import fetch_chords_from_web
                raw_chords = fetch_chords_from_web(song_name, artist)
                if raw_chords and len(raw_chords) >= 3:
                    print(f"[intel] fetched {len(raw_chords)} raw chords for section analysis")
                else:
                    raw_chords = None
            except Exception:
                raw_chords = None

        ha = build_harmonic_analysis(
            chord_sequence=raw_chords,
            lyrics_text=lyrics_text,
            key_num=key_num,
            mode_num=mode_num,
            key_confidence=key_conf,
        )
        harmonic_sections = ha.get("harmonic_sections", [])
        print(f"[intel] harmonic analysis: {len(harmonic_sections)} sections")
    except Exception as e:
        print(f"[intel] harmonic analysis failed: {e}")
        import traceback; traceback.print_exc()

    return {
        "key": key_str,
        "key_num": key_num,
        "mode_num": mode_num,
        "bpm": bpm,
        "bpm_confidence": round(bpm_conf, 2),
        "progression": progression,  # Legacy — kept for backward compat
        "progression_confidence": round(prog_confidence, 2),
        "progression_source": prog_source,
        "confidence": round(key_conf, 2),
        "harmonic_sections": harmonic_sections,
    }
