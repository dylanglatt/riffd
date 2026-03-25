"""
chord_source.py
Chord progression detection via web scraping + functional harmony analysis.

Pipeline:
1. Fetch raw chord names from web
2. Simplify chords (strip extensions, inversions)
3. Parse to (root_pc, quality)
4. Estimate key using diatonic fitness scoring
5. Convert to scale degrees (1-7)
6. Normalize (remove consecutive dupes, find repeating loop)
7. Fuzzy-match against canonical templates
8. Return structured result with confidence
"""

import re
import requests
from collections import Counter

NOTE_NAMES = ["C", "C#", "D", "D#", "E", "F", "F#", "G", "G#", "A", "A#", "B"]
FLAT_TO_SHARP = {"Db":"C#","Eb":"D#","Fb":"E","Gb":"F#","Ab":"G#","Bb":"A#","Cb":"B"}

MAJOR_SCALE = [0, 2, 4, 5, 7, 9, 11]       # semitones from root
NATURAL_MINOR_SCALE = [0, 2, 3, 5, 7, 8, 10]

# Diatonic chord qualities in major key: M m m M M m dim
MAJOR_QUALITIES = [0, 1, 1, 0, 0, 1, 2]  # 0=major, 1=minor, 2=dim
# In minor key: m dim M m m M M
MINOR_QUALITIES = [1, 2, 0, 1, 1, 0, 0]

# Roman numeral labels indexed by (degree, quality)
ROMAN = {
    (1,0):"I",  (1,1):"i",  (2,0):"II", (2,1):"ii", (2,2):"ii\u00b0",
    (3,0):"III",(3,1):"iii",(4,0):"IV", (4,1):"iv", (5,0):"V",  (5,1):"v",
    (6,0):"VI", (6,1):"vi", (7,0):"VII",(7,1):"vii",(7,2):"vii\u00b0",
}

# ─── Canonical progression templates (degree-based, transposition-invariant) ──
TEMPLATES = [
    {"name":"I – V – vi – IV",      "degrees":[1,5,6,4]},
    {"name":"vi – IV – I – V",      "degrees":[6,4,1,5]},
    {"name":"I – vi – IV – V",      "degrees":[1,6,4,5]},
    {"name":"I – IV – V – IV",      "degrees":[1,4,5,4]},
    {"name":"I – IV – vi – V",      "degrees":[1,4,6,5]},
    {"name":"I – IV – V",           "degrees":[1,4,5]},
    {"name":"I – IV – V – I",       "degrees":[1,4,5,1]},
    {"name":"I – V – IV – V",       "degrees":[1,5,4,5]},
    {"name":"I – V – IV",           "degrees":[1,5,4]},
    {"name":"ii – V – I",           "degrees":[2,5,1]},
    {"name":"I – iii – IV – V",     "degrees":[1,3,4,5]},
    {"name":"I – V – vi – iii – IV","degrees":[1,5,6,3,4]},
    {"name":"I – IV – I – V",       "degrees":[1,4,1,5]},
    {"name":"I – V – I – IV",       "degrees":[1,5,1,4]},
    {"name":"i – VI – III – VII",   "degrees":[1,6,3,7]},  # minor context
    {"name":"i – iv – v – i",       "degrees":[1,4,5,1]},
    {"name":"i – VII – VI – V",     "degrees":[1,7,6,5]},
    {"name":"i – iv – VII – III",   "degrees":[1,4,7,3]},
    {"name":"12-bar blues",          "degrees":[1,1,1,1,4,4,1,1,5,4,1,5]},
]


# ═══════════════════════════════════════════════════════════════════════════════
# STEP 3: Chord Simplification
# ═══════════════════════════════════════════════════════════════════════════════

def simplify_chord(chord: str) -> str:
    """
    Reduce a chord to its essential root + quality.
    Cmaj7 → C, Am7 → Am, Gsus4 → G, F/C → F, Dm9 → Dm
    """
    chord = chord.strip()
    if "/" in chord:
        chord = chord.split("/")[0]

    m = re.match(r"^([A-G][b#]?)(.*)", chord)
    if not m:
        return chord

    root = m.group(1)
    suffix = m.group(2).lower()

    # Determine if minor
    is_minor = (
        suffix.startswith("m") and not suffix.startswith("maj")
    ) or "min" in suffix

    # Normalize flats
    if root in FLAT_TO_SHARP:
        root = FLAT_TO_SHARP[root]

    return root + ("m" if is_minor else "")


# ═══════════════════════════════════════════════════════════════════════════════
# STEP 3b: Parse chord to (root_pitch_class, quality)
# ═══════════════════════════════════════════════════════════════════════════════

def parse_chord(chord_str: str):
    """
    Parse a simplified chord string.
    Returns (root_pc: int 0-11, quality: int 0=major 1=minor) or None.
    """
    simplified = simplify_chord(chord_str)
    m = re.match(r"^([A-G][#]?)(m?)$", simplified)
    if not m:
        return None
    root_str = m.group(1)
    if root_str not in NOTE_NAMES:
        return None
    root_pc = NOTE_NAMES.index(root_str)
    quality = 1 if m.group(2) == "m" else 0
    return root_pc, quality


# ═══════════════════════════════════════════════════════════════════════════════
# STEP 4: Key Estimation
# ═══════════════════════════════════════════════════════════════════════════════

def estimate_key(parsed_chords: list[tuple[int, int]]) -> list[tuple[int, int, float]]:
    """
    Estimate the most likely keys for a set of (root_pc, quality) chords.
    Tries all 24 keys (12 major + 12 minor), scores by diatonic fitness.

    Returns top 3 candidates as [(key_pc, mode, score), ...].
    mode: 1=major, 0=minor.
    """
    candidates = []

    for key_pc in range(12):
        for mode in [1, 0]:
            scale = MAJOR_SCALE if mode == 1 else NATURAL_MINOR_SCALE
            expected_qualities = MAJOR_QUALITIES if mode == 1 else MINOR_QUALITIES
            scale_pcs = [(key_pc + interval) % 12 for interval in scale]

            score = 0.0
            for root_pc, quality in parsed_chords:
                if root_pc in scale_pcs:
                    degree_idx = scale_pcs.index(root_pc)
                    # Bonus for matching expected quality
                    if quality == expected_qualities[degree_idx]:
                        score += 2.0  # perfect diatonic match
                    else:
                        score += 0.8  # root is diatonic but quality differs
                else:
                    score -= 0.5  # chromatic chord = slight penalty

            # Bias toward major keys slightly (more common)
            if mode == 1:
                score += 0.3

            candidates.append((key_pc, mode, score))

    candidates.sort(key=lambda x: -x[2])
    return candidates[:3]


# ═══════════════════════════════════════════════════════════════════════════════
# STEP 5: Chord → Scale Degree
# ═══════════════════════════════════════════════════════════════════════════════

def chord_to_degree(root_pc: int, quality: int, key_pc: int, mode: int) -> int | None:
    """
    Convert a chord root to a scale degree (1-7) in the given key.
    Returns None if the chord root is not in the scale.
    """
    scale = MAJOR_SCALE if mode == 1 else NATURAL_MINOR_SCALE
    scale_pcs = [(key_pc + interval) % 12 for interval in scale]

    if root_pc in scale_pcs:
        return scale_pcs.index(root_pc) + 1  # 1-indexed
    return None


def degree_to_roman(degree: int, quality: int) -> str:
    """Convert degree (1-7) + quality (0=maj,1=min,2=dim) to roman numeral."""
    return ROMAN.get((degree, quality), str(degree))


# ═══════════════════════════════════════════════════════════════════════════════
# STEP 6: Normalize Progression
# ═══════════════════════════════════════════════════════════════════════════════

def normalize_progression(degrees: list[int]) -> list[int]:
    """
    Clean up a degree sequence:
    1. Remove consecutive duplicates: [1,1,5,5,6,4] → [1,5,6,4]
    2. Extract the shortest repeating loop
    """
    if not degrees:
        return []

    # Remove consecutive dupes
    deduped = [degrees[0]]
    for d in degrees[1:]:
        if d != deduped[-1]:
            deduped.append(d)

    # Find shortest repeating unit
    for length in range(2, len(deduped) // 2 + 1):
        candidate = deduped[:length]
        is_repeating = True
        for i in range(length, len(deduped)):
            if deduped[i] != candidate[i % length]:
                is_repeating = False
                break
        if is_repeating:
            return candidate

    # No exact repeat found — return up to 6 elements
    return deduped[:min(len(deduped), 6)]


# ═══════════════════════════════════════════════════════════════════════════════
# STEP 8: Fuzzy Template Matching
# ═══════════════════════════════════════════════════════════════════════════════

def score_progression(input_degrees: list[int], template: dict) -> float:
    """
    Score how well input_degrees matches a template.
    Handles: exact match, rotation, repetition, partial overlap.
    Returns 0.0–1.0.
    """
    t = template["degrees"]
    inp = input_degrees

    if not inp or not t:
        return 0.0

    # Exact match
    if inp == t:
        return 1.0

    # Check if input is a rotation of template
    doubled_t = t + t
    for i in range(len(t)):
        if doubled_t[i:i + len(t)] == inp[:len(t)]:
            return 0.95

    # Check if input is a repeated version of template
    if len(inp) >= len(t):
        match_count = 0
        for i in range(len(inp)):
            if inp[i] == t[i % len(t)]:
                match_count += 1
        repeat_score = match_count / len(inp)
        if repeat_score > 0.8:
            return 0.85 + repeat_score * 0.1

    # Subsequence alignment: how many of the first len(t) degrees match in order
    match_len = min(len(inp), len(t))
    positional_matches = sum(1 for i in range(match_len) if inp[i] == t[i])
    positional_score = positional_matches / len(t)

    # Shared degree content (order-independent)
    inp_set = set(inp)
    t_set = set(t)
    overlap = len(inp_set & t_set) / max(len(t_set), 1)

    # Weighted combination
    score = positional_score * 0.65 + overlap * 0.35

    return score


def match_templates(input_degrees: list[int]) -> tuple[dict | None, float]:
    """
    Find the best matching template for a degree sequence.
    Returns (best_template, confidence).
    """
    best = None
    best_score = 0.0

    for template in TEMPLATES:
        s = score_progression(input_degrees, template)
        if s > best_score:
            best_score = s
            best = template

    return best, best_score


# ═══════════════════════════════════════════════════════════════════════════════
# Web chord extraction (reused from before, with simplification)
# ═══════════════════════════════════════════════════════════════════════════════

def _clean_song_name(name):
    name = re.sub(r"\s*[-–]\s*\d{4}\s*remaster(ed)?", "", name, flags=re.I)
    name = re.sub(r"\s*[-–]\s*remaster(ed)?(\s*\d{4})?", "", name, flags=re.I)
    name = re.sub(r"\([^)]*\)", "", name)
    name = re.sub(r"\[[^\]]*\]", "", name)
    return name.strip()


def fetch_chords_from_web(song_name: str, artist: str) -> list[str]:
    """Fetch raw chord names from web search. Returns ordered list."""
    clean_name = _clean_song_name(song_name)
    clean_artist = artist.split(",")[0].strip()
    query = f"{clean_artist} {clean_name} chords"
    print(f"[chords] searching: {query}")

    try:
        resp = requests.get(
            "https://lite.duckduckgo.com/lite/",
            params={"q": query},
            headers={"User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7)"},
            timeout=10,
        )
        if resp.status_code != 200:
            return []

        # Try chord site URLs first
        urls = re.findall(r'href="(https?://[^"]+)"', resp.text)
        chord_urls = [u for u in urls if any(s in u for s in
            ["ultimate-guitar.com", "e-chords.com", "chordie.com"])]

        for url in chord_urls[:2]:
            try:
                page = requests.get(url, timeout=10, headers={
                    "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7)"})
                if page.status_code == 200:
                    chords = _extract_chords(page.text)
                    if len(chords) >= 3:
                        return chords
            except Exception:
                continue

        # Fallback: extract from search page
        return _extract_chords(resp.text)

    except Exception as e:
        print(f"[chords] fetch error: {e}")
        return []


def _extract_chords(html: str) -> list[str]:
    """Extract and simplify chord names from HTML text."""
    clean = re.sub(r"<[^>]+>", " ", html)
    pattern = r'\b([A-G][b#]?(?:m(?:aj)?|min|dim|aug|sus[24]?|add[0-9]?|[0-9])*(?:/[A-G][b#]?)?)\b'
    raw = re.findall(pattern, clean)

    # Simplify and filter
    valid = []
    for c in raw:
        parsed = parse_chord(c)
        if parsed is not None:
            simplified = simplify_chord(c)
            valid.append(simplified)

    # Preserve order, remove consecutive dupes (keep all occurrences for ordering)
    if not valid:
        return []

    print(f"[chords] raw extracted: {len(valid)} chords, unique: {len(set(valid))}")
    return valid


# ═══════════════════════════════════════════════════════════════════════════════
# MAIN API: get_chord_progression
# ═══════════════════════════════════════════════════════════════════════════════

def get_chord_progression(song_name: str, artist: str, key_num: int = -1, mode_num: int = 1):
    """
    Full pipeline: fetch → simplify → key → degrees → normalize → match.

    Returns: (result_dict_or_None, source_str, confidence_float)
    result_dict: {key, chords, degrees, roman, matched_progression, confidence}
    """
    if not song_name or not artist:
        return None, "none", 0.0

    # 1. Fetch raw chords
    raw_chords = fetch_chords_from_web(song_name, artist)
    if len(raw_chords) < 3:
        print(f"[chords] insufficient data: {len(raw_chords)} chords")
        return None, "none", 0.0

    # 2. Parse all chords
    parsed_sequence = []  # [(root_pc, quality), ...]
    chord_names = []
    for c in raw_chords:
        p = parse_chord(c)
        if p:
            parsed_sequence.append(p)
            chord_names.append(c)

    if len(parsed_sequence) < 3:
        return None, "none", 0.0

    # 3. Estimate key (use provided key or compute from chords)
    if key_num >= 0:
        best_key_pc, best_mode = key_num, mode_num
        print(f"[chords] using provided key: {NOTE_NAMES[key_num]} {'Major' if mode_num==1 else 'Minor'}")
    else:
        candidates = estimate_key(parsed_sequence)
        best_key_pc, best_mode, best_score = candidates[0]
        print(f"[chords] estimated key: {NOTE_NAMES[best_key_pc]} {'Major' if best_mode==1 else 'Minor'} (score={best_score:.1f})")
        # Try top candidates and pick the one that produces best template match
        best_overall = None
        for kpc, kmode, kscore in candidates[:3]:
            degrees = _chords_to_degrees(parsed_sequence, kpc, kmode)
            norm = normalize_progression(degrees)
            tmpl, tscore = match_templates(norm)
            if best_overall is None or tscore > best_overall[0]:
                best_overall = (tscore, kpc, kmode, degrees, norm, tmpl)

        if best_overall:
            _, best_key_pc, best_mode, _, _, _ = best_overall
            print(f"[chords] best key after template fitting: {NOTE_NAMES[best_key_pc]} {'Major' if best_mode==1 else 'Minor'}")

    # 4. Convert to degrees
    degree_sequence = _chords_to_degrees(parsed_sequence, best_key_pc, best_mode)

    if len(degree_sequence) < 2:
        print("[chords] too few diatonic chords")
        return None, "none", 0.0

    # 5. Normalize
    normalized = normalize_progression(degree_sequence)
    print(f"[chords] degree sequence: {degree_sequence[:20]}{'...' if len(degree_sequence)>20 else ''}")
    print(f"[chords] normalized: {normalized}")

    # 6. Match against templates
    best_template, match_score = match_templates(normalized)

    # 7. Build roman numeral output
    scale = MAJOR_SCALE if best_mode == 1 else NATURAL_MINOR_SCALE
    expected_q = MAJOR_QUALITIES if best_mode == 1 else MINOR_QUALITIES
    roman_list = []
    for d in normalized:
        q = expected_q[d - 1] if 1 <= d <= 7 else 0
        roman_list.append(degree_to_roman(d, q))

    # 8. Compute final confidence
    confidence = match_score * 0.85  # scale down slightly — web chords are noisy
    if best_template and match_score >= 0.6:
        progression_str = best_template["name"]
        print(f"[chords] MATCHED: {progression_str} (score={match_score:.2f}, conf={confidence:.2f})")
    elif roman_list:
        progression_str = " – ".join(roman_list)
        confidence = min(confidence, 0.5)  # unmatched = lower confidence
        print(f"[chords] UNMATCHED: {progression_str} (conf={confidence:.2f})")
    else:
        return None, "none", 0.0

    key_str = f"{NOTE_NAMES[best_key_pc]} {'Major' if best_mode==1 else 'Minor'}"

    # Build unique chord list for display
    unique_chords = []
    seen = set()
    for c in chord_names:
        if c not in seen:
            seen.add(c)
            unique_chords.append(c)

    result = {
        "key": key_str,
        "chords": unique_chords[:8],
        "degrees": normalized,
        "roman": roman_list,
        "matched_progression": progression_str,
        "confidence": round(confidence, 2),
    }

    return result, "web", confidence


def _chords_to_degrees(parsed_sequence, key_pc, mode):
    """Convert a sequence of (root_pc, quality) to scale degrees, skipping non-diatonic."""
    degrees = []
    for root_pc, quality in parsed_sequence:
        d = chord_to_degree(root_pc, quality, key_pc, mode)
        if d is not None:
            degrees.append(d)
    return degrees
