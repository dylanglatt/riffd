"""
harmonic_analysis.py — Section-based harmonic analysis.

Produces section-aware chord analysis by combining:
1. Chord data from web scraping (chord_source.py)
2. Lyric section tags from Genius (external_apis.py)
3. Audio-based chord detection (music_intelligence.py)
4. Key detection from note data

Output: list of harmonic sections with chords, roman numerals, and confidence.
"""

import re
from collections import Counter

NOTE_NAMES = ["C", "C#", "D", "D#", "E", "F", "F#", "G", "G#", "A", "A#", "B"]
FLAT_TO_SHARP = {"Db": "C#", "Eb": "D#", "Fb": "E", "Gb": "F#", "Ab": "G#", "Bb": "A#", "Cb": "B"}

MAJOR_SCALE = [0, 2, 4, 5, 7, 9, 11]
MINOR_SCALE = [0, 2, 3, 5, 7, 8, 10]
MAJOR_QUALITIES = ["", "m", "m", "", "", "m", "dim"]  # I ii iii IV V vi vii°
MINOR_QUALITIES = ["m", "dim", "", "m", "m", "", ""]   # i ii° III iv v VI VII
MAJOR_NUMERALS = ["I", "ii", "iii", "IV", "V", "vi", "vii°"]
MINOR_NUMERALS = ["i", "ii°", "III", "iv", "v", "VI", "VII"]

# Section label normalization
SECTION_NORM = {
    "intro": "intro", "introduction": "intro",
    "verse": "verse",
    "chorus": "chorus", "hook": "chorus",
    "bridge": "bridge",
    "pre-chorus": "pre-chorus", "prechorus": "pre-chorus", "pre chorus": "pre-chorus",
    "outro": "outro", "coda": "outro",
    "instrumental": "instrumental", "solo": "instrumental", "guitar solo": "instrumental",
    "interlude": "instrumental",
    "post-chorus": "chorus",
}


# ─── Lyric Section Parsing ────────────────────────────────────────────────────

def parse_lyric_sections(lyrics_text: str) -> list[dict]:
    """
    Parse lyrics with [Section] tags into structured sections.
    Returns list of {label, normalized_label, lyrics, line_count}.
    """
    if not lyrics_text:
        return []

    sections = []
    current_label = None
    current_lines = []

    for line in lyrics_text.split("\n"):
        stripped = line.strip()
        # Check for section marker: [Verse 1], [Chorus], etc.
        sec_match = re.match(r"^\[(.+)\]$", stripped)
        if sec_match:
            # Save previous section
            if current_label is not None and current_lines:
                text = "\n".join(current_lines).strip()
                if text:
                    sections.append({
                        "label": current_label,
                        "normalized_label": _normalize_section_label(current_label),
                        "lyrics": text,
                        "line_count": len([l for l in current_lines if l.strip()]),
                    })
            current_label = sec_match.group(1).strip()
            current_lines = []
        elif current_label is not None:
            current_lines.append(stripped)
        else:
            # Lines before any section tag — skip chrome/junk
            pass

    # Save last section
    if current_label is not None and current_lines:
        text = "\n".join(current_lines).strip()
        if text:
            sections.append({
                "label": current_label,
                "normalized_label": _normalize_section_label(current_label),
                "lyrics": text,
                "line_count": len([l for l in current_lines if l.strip()]),
            })

    return sections


def _normalize_section_label(label: str) -> str:
    """Normalize 'Verse 1', 'CHORUS', 'Guitar Solo' etc. to standard types."""
    lower = label.lower().strip()
    # Strip trailing numbers: "Verse 1" → "verse"
    base = re.sub(r"\s*\d+\s*$", "", lower).strip()
    return SECTION_NORM.get(base, "section")


# ─── Chord-to-Section Alignment ──────────────────────────────────────────────

def align_chords_to_sections(
    chord_sequence: list[str],
    lyric_sections: list[dict],
    key_num: int = -1,
    mode_num: int = 1,
) -> list[dict]:
    """
    Distribute ordered chord sequence across lyric sections.

    Strategy:
    1. Weight sections by line count (proxy for duration)
    2. Allocate chords proportionally
    3. Detect repeated patterns within each section
    4. Compute roman numerals and confidence
    """
    if not chord_sequence:
        return []

    # If no lyric sections, create one "Full Song" section
    if not lyric_sections:
        return [_build_section(
            label="Full Song",
            normalized_label="section",
            chords=chord_sequence,
            key_num=key_num,
            mode_num=mode_num,
            confidence=0.5,
        )]

    # Weight sections by line count for proportional chord allocation
    total_lines = sum(s.get("line_count", 1) for s in lyric_sections)
    if total_lines == 0:
        total_lines = len(lyric_sections)

    total_chords = len(chord_sequence)
    sections = []
    chord_idx = 0

    for sec in lyric_sections:
        lines = sec.get("line_count", 1) or 1
        proportion = lines / total_lines
        # Allocate at least 2 chords per section, proportionally distribute the rest
        n_chords = max(2, round(proportion * total_chords))
        # Don't exceed remaining chords
        n_chords = min(n_chords, total_chords - chord_idx)
        if n_chords <= 0:
            n_chords = 1
        if chord_idx >= total_chords:
            break

        section_chords = chord_sequence[chord_idx:chord_idx + n_chords]
        chord_idx += n_chords

        # Compute alignment confidence
        # Higher if the section has enough chords relative to its size
        conf = min(0.85, 0.5 + proportion * 0.5) if len(section_chords) >= 2 else 0.4

        sections.append(_build_section(
            label=sec["label"],
            normalized_label=sec["normalized_label"],
            chords=section_chords,
            key_num=key_num,
            mode_num=mode_num,
            confidence=round(conf, 2),
        ))

    # If chords remain, append to last section
    if chord_idx < total_chords and sections:
        extra = chord_sequence[chord_idx:]
        sections[-1]["chords"].extend(extra)
        sections[-1]["roman_numerals"] = _chords_to_numerals(
            sections[-1]["chords"], key_num, mode_num
        )
        sections[-1]["summary"] = _summarize_section(sections[-1]["chords"], key_num, mode_num)

    return sections


def _build_section(label, normalized_label, chords, key_num, mode_num, confidence):
    """Build a complete section dict with chords, numerals, and summary."""
    numerals = _chords_to_numerals(chords, key_num, mode_num)
    summary = _summarize_section(chords, key_num, mode_num)

    return {
        "label": label,
        "normalized_label": normalized_label,
        "chords": chords,
        "roman_numerals": numerals,
        "summary": summary,
        "confidence": confidence,
    }


# ─── Roman Numeral Conversion ────────────────────────────────────────────────

def _parse_chord_root(chord_str: str) -> tuple[int, str] | None:
    """Parse a chord string to (root_pc, quality_suffix). Returns None if unparsable."""
    chord = chord_str.strip()
    if not chord:
        return None

    # Handle slash chords: take the part before /
    if "/" in chord:
        chord = chord.split("/")[0]

    m = re.match(r"^([A-G][b#]?)(.*)", chord)
    if not m:
        return None

    root_str = m.group(1)
    suffix = m.group(2).lower()

    # Normalize flats
    if root_str in FLAT_TO_SHARP:
        root_str = FLAT_TO_SHARP[root_str]

    if root_str not in NOTE_NAMES:
        return None

    root_pc = NOTE_NAMES.index(root_str)
    is_minor = suffix.startswith("m") and not suffix.startswith("maj")

    return root_pc, "m" if is_minor else ""


def _chords_to_numerals(chords: list[str], key_num: int, mode_num: int) -> list[str]:
    """Convert chord names to roman numerals relative to key."""
    if key_num < 0:
        return chords  # Can't convert without key

    scale = MAJOR_SCALE if mode_num == 1 else MINOR_SCALE
    scale_pcs = [(key_num + interval) % 12 for interval in scale]
    expected_quals = MAJOR_QUALITIES if mode_num == 1 else MINOR_QUALITIES
    numeral_names = MAJOR_NUMERALS if mode_num == 1 else MINOR_NUMERALS

    result = []
    for chord in chords:
        parsed = _parse_chord_root(chord)
        if parsed is None:
            result.append(chord)  # Keep raw if unparsable
            continue

        root_pc, quality = parsed

        if root_pc in scale_pcs:
            degree_idx = scale_pcs.index(root_pc)
            expected_q = expected_quals[degree_idx]
            if quality == expected_q:
                # Perfect diatonic match
                result.append(numeral_names[degree_idx])
            else:
                # Root is diatonic but quality differs (e.g., V in minor key used as major)
                base = numeral_names[degree_idx]
                if quality == "m" and expected_q == "":
                    # Minor where major expected
                    result.append(base.lower() if base[0].isupper() else base)
                elif quality == "" and expected_q == "m":
                    # Major where minor expected
                    result.append(base.upper())
                else:
                    result.append(base)
        else:
            # Non-diatonic — find closest and mark as borrowed
            # Simple approach: find the interval from tonic
            interval = (root_pc - key_num) % 12
            degree_map = {0: "I", 1: "bII", 2: "II", 3: "bIII", 4: "III", 5: "IV",
                          6: "#IV", 7: "V", 8: "bVI", 9: "VI", 10: "bVII", 11: "VII"}
            numeral = degree_map.get(interval, "?")
            if quality == "m":
                numeral = numeral.lower()
            result.append(numeral)

    return result


# ─── Section Progression Summarization ────────────────────────────────────────

def _summarize_section(chords: list[str], key_num: int, mode_num: int) -> dict:
    """
    Check if a section's chord sequence is a repeating pattern.
    Only returns a summary if repetition is clearly present.
    """
    if len(chords) < 2:
        return {"is_repeating_pattern": False, "confidence": 0.0}

    # Remove consecutive duplicates for pattern detection
    deduped = [chords[0]]
    for c in chords[1:]:
        if c != deduped[-1]:
            deduped.append(c)

    # Try to find repeating pattern of length 2-6
    for plen in range(2, min(7, len(deduped) // 2 + 1)):
        pattern = deduped[:plen]
        repeats = 0
        for i in range(0, len(deduped) - plen + 1, plen):
            if deduped[i:i + plen] == pattern:
                repeats += 1
            else:
                break

        if repeats >= 2:
            numerals = _chords_to_numerals(pattern, key_num, mode_num)
            confidence = min(0.95, 0.6 + repeats * 0.1)
            return {
                "is_repeating_pattern": True,
                "summary_chords": pattern,
                "summary_numerals": numerals,
                "repeated_units": repeats,
                "confidence": round(confidence, 2),
            }

    return {"is_repeating_pattern": False, "confidence": 0.3}


# ─── Enhanced Key Inference ───────────────────────────────────────────────────

def infer_key_from_chords(chord_sequence: list[str]) -> dict:
    """
    Infer key from chord sequence using chord-weight scoring.
    Returns {tonic, mode, display, confidence, alternatives}.
    """
    if not chord_sequence:
        return {"tonic": "C", "mode": "major", "display": "Unknown", "confidence": 0.0}

    parsed = []
    for c in chord_sequence:
        p = _parse_chord_root(c)
        if p:
            parsed.append(p)

    if not parsed:
        return {"tonic": "C", "mode": "major", "display": "Unknown", "confidence": 0.0}

    # Score all 24 possible keys
    candidates = []
    for key_pc in range(12):
        for mode in [1, 0]:  # 1=major, 0=minor
            scale = MAJOR_SCALE if mode == 1 else MINOR_SCALE
            expected_quals = MAJOR_QUALITIES if mode == 1 else MINOR_QUALITIES
            scale_pcs = [(key_pc + interval) % 12 for interval in scale]

            score = 0.0
            for root_pc, quality in parsed:
                if root_pc in scale_pcs:
                    degree_idx = scale_pcs.index(root_pc)
                    if quality == expected_quals[degree_idx]:
                        score += 2.0  # Perfect diatonic match
                    else:
                        score += 0.8  # Root diatonic, quality differs
                else:
                    score -= 0.5  # Chromatic

            # Bonus: first and last chord as tonic
            if parsed[0][0] == key_pc:
                score += 1.5
            if parsed[-1][0] == key_pc:
                score += 1.0
            # Slight major bias
            if mode == 1:
                score += 0.3

            candidates.append((key_pc, mode, score))

    candidates.sort(key=lambda x: -x[2])
    best_pc, best_mode, best_score = candidates[0]

    # Confidence: how much the best stands out
    second_score = candidates[1][2] if len(candidates) > 1 else 0
    max_possible = len(parsed) * 2.0 + 2.8  # all perfect + bonuses
    raw_conf = best_score / max(max_possible, 1)
    gap_conf = (best_score - second_score) / max(best_score, 1) if best_score > 0 else 0
    confidence = min(1.0, raw_conf * 0.6 + gap_conf * 0.4)

    mode_str = "major" if best_mode == 1 else "minor"
    display = f"{NOTE_NAMES[best_pc]} {mode_str}"

    # Top 3 alternatives
    alternatives = []
    for pc, m, s in candidates[1:4]:
        alt_mode = "major" if m == 1 else "minor"
        alt_conf = min(1.0, (s / max(max_possible, 1)) * 0.6)
        alternatives.append({
            "display": f"{NOTE_NAMES[pc]} {alt_mode}",
            "confidence": round(alt_conf, 2),
        })

    return {
        "tonic": NOTE_NAMES[best_pc],
        "mode": mode_str,
        "display": display,
        "confidence": round(confidence, 2),
        "key_num": best_pc,
        "mode_num": best_mode,
        "alternatives": alternatives,
    }


# ─── Main Entry Point ────────────────────────────────────────────────────────

def build_harmonic_analysis(
    chord_sequence: list[str] | None = None,
    lyrics_text: str | None = None,
    key_num: int = -1,
    mode_num: int = 1,
    key_confidence: float = 0.0,
) -> dict:
    """
    Build section-based harmonic analysis from available data.

    Args:
        chord_sequence: Ordered chord names from web scraping or audio analysis
        lyrics_text: Raw lyrics with [Section] tags from Genius
        key_num: Detected key pitch class (0-11), or -1 to infer from chords
        mode_num: 1=major, 0=minor
        key_confidence: Confidence of provided key

    Returns:
        {key, harmonic_sections} — section-based analysis
    """
    print(f"[harmonic] building analysis: {len(chord_sequence or [])} chords, lyrics={'yes' if lyrics_text else 'no'}")

    # Step 1: Parse lyric sections
    lyric_sections = parse_lyric_sections(lyrics_text) if lyrics_text else []
    print(f"[harmonic] lyric sections: {[s['label'] for s in lyric_sections]}")

    # Step 2: Determine key
    if key_num >= 0 and key_confidence >= 0.5:
        # Use provided key
        mode_str = "major" if mode_num == 1 else "minor"
        key_info = {
            "tonic": NOTE_NAMES[key_num],
            "mode": mode_str,
            "display": f"{NOTE_NAMES[key_num]} {mode_str}",
            "confidence": round(key_confidence, 2),
            "key_num": key_num,
            "mode_num": mode_num,
        }
    elif chord_sequence:
        # Infer from chord sequence
        key_info = infer_key_from_chords(chord_sequence)
        key_num = key_info.get("key_num", -1)
        mode_num = key_info.get("mode_num", 1)
    else:
        key_info = {"tonic": "C", "mode": "major", "display": "Unknown", "confidence": 0.0, "key_num": -1, "mode_num": 1}

    print(f"[harmonic] key: {key_info['display']} (confidence={key_info['confidence']})")

    # Step 3: Align chords to sections
    if chord_sequence:
        harmonic_sections = align_chords_to_sections(
            chord_sequence, lyric_sections, key_num, mode_num
        )
    else:
        # No chords available — create empty sections from lyrics
        harmonic_sections = []
        for sec in lyric_sections:
            harmonic_sections.append({
                "label": sec["label"],
                "normalized_label": sec["normalized_label"],
                "chords": [],
                "roman_numerals": [],
                "summary": {"is_repeating_pattern": False, "confidence": 0.0},
                "confidence": 0.0,
            })

    print(f"[harmonic] produced {len(harmonic_sections)} sections")
    for s in harmonic_sections:
        print(f"[harmonic]   {s['label']}: {' – '.join(s['chords'][:8])}{'...' if len(s['chords'])>8 else ''}")

    return {
        "key": key_info,
        "harmonic_sections": harmonic_sections,
    }
