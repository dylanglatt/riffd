"""
chord_source.py
Fetch chord progressions from external sources, parse into roman numerals.
Primary source for progression data — audio analysis is fallback only.
"""

import re
import requests

NOTE_NAMES = ["C", "C#", "D", "D#", "E", "F", "F#", "G", "G#", "A", "A#", "B"]

# Enharmonic map for flats
FLAT_TO_SHARP = {
    "Db": "C#", "Eb": "D#", "Fb": "E", "Gb": "F#",
    "Ab": "G#", "Bb": "A#", "Cb": "B",
}

MAJOR_INTERVALS = [0, 2, 4, 5, 7, 9, 11]
MINOR_INTERVALS = [0, 2, 3, 5, 7, 8, 10]

MAJOR_NUMERALS = ["I", "ii", "iii", "IV", "V", "vi", "vii\u00b0"]
MINOR_NUMERALS = ["i", "ii\u00b0", "III", "iv", "v", "VI", "VII"]


# ─── Chord name parsing ──────────────────────────────────────────────────────

def _parse_chord_name(chord_str):
    """
    Parse a chord string like "Am", "F#m", "Gsus4", "Bb", "C/G"
    Returns (root_pc, is_minor) or None if unparsable.
    """
    chord_str = chord_str.strip()
    if not chord_str or len(chord_str) > 10:
        return None

    # Remove slash bass notes: "C/G" → "C"
    if "/" in chord_str:
        chord_str = chord_str.split("/")[0]

    # Extract root note
    m = re.match(r"^([A-G][b#]?)", chord_str)
    if not m:
        return None

    root_str = m.group(1)
    suffix = chord_str[len(root_str):]

    # Normalize flats
    if root_str in FLAT_TO_SHARP:
        root_str = FLAT_TO_SHARP[root_str]

    if root_str not in NOTE_NAMES:
        return None

    root_pc = NOTE_NAMES.index(root_str)

    # Determine quality from suffix
    suffix_lower = suffix.lower()
    is_minor = suffix_lower.startswith("m") and not suffix_lower.startswith("maj")
    # Also catch "min"
    if "min" in suffix_lower:
        is_minor = True

    return root_pc, is_minor


def _chord_to_numeral(root_pc, is_minor, key_num, mode_num):
    """Convert a chord root + quality to a roman numeral in the given key."""
    if mode_num == 1:
        scale = [(key_num + i) % 12 for i in MAJOR_INTERVALS]
        numerals = MAJOR_NUMERALS
    else:
        scale = [(key_num + i) % 12 for i in MINOR_INTERVALS]
        numerals = MINOR_NUMERALS

    if root_pc in scale:
        degree = scale.index(root_pc)
        expected_numeral = numerals[degree]
        # Check if the actual quality matches the diatonic expectation
        # If minor chord on a major degree, use lowercase
        if is_minor and expected_numeral[0].isupper():
            return expected_numeral.lower()
        elif not is_minor and expected_numeral[0].islower():
            return expected_numeral.upper()
        return expected_numeral
    else:
        # Non-diatonic — find nearest and mark
        dists = [min(abs(root_pc - s), 12 - abs(root_pc - s)) for s in scale]
        nearest = int(min(range(len(dists)), key=lambda i: dists[i]))
        base = numerals[nearest]
        return base  # approximate to nearest diatonic


# ─── Chord text extraction from web ──────────────────────────────────────────

def _clean_song_name(name):
    """Remove remaster/version suffixes for search."""
    name = re.sub(r"\s*[-–]\s*\d{4}\s*remaster(ed)?", "", name, flags=re.I)
    name = re.sub(r"\s*[-–]\s*remaster(ed)?(\s*\d{4})?", "", name, flags=re.I)
    name = re.sub(r"\([^)]*\)", "", name)
    name = re.sub(r"\[[^\]]*\]", "", name)
    return name.strip()


def _clean_artist(artist):
    """Take first artist."""
    return artist.split(",")[0].strip()


def fetch_chords_from_web(song_name, artist):
    """
    Search for chord data by scraping a search engine for chord sheet content.
    Returns list of chord name strings, or empty list.
    """
    clean_name = _clean_song_name(song_name)
    clean_artist = _clean_artist(artist)

    print(f"[chords] searching for: {clean_artist} - {clean_name}")

    # Try fetching from a chord API / search
    # Use guitarparty or similar open chord sources via search
    query = f"{clean_artist} {clean_name} chords"

    try:
        # Search via DuckDuckGo lite for chord pages
        resp = requests.get(
            "https://lite.duckduckgo.com/lite/",
            params={"q": query},
            headers={"User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7)"},
            timeout=10,
        )
        if resp.status_code != 200:
            print(f"[chords] search failed: {resp.status_code}")
            return []

        # Find URLs that look like chord sites
        urls = re.findall(r'href="(https?://[^"]+)"', resp.text)
        chord_urls = [u for u in urls if any(site in u for site in
            ["ultimate-guitar.com", "e-chords.com", "chordie.com", "tabs.ultimate-guitar.com"])]

        if not chord_urls:
            print("[chords] no chord site URLs found")
            # Fallback: try to extract chord-like content from the search results page itself
            return _extract_chords_from_text(resp.text)

        # Try the first chord URL
        for url in chord_urls[:2]:
            print(f"[chords] trying: {url}")
            try:
                page = requests.get(url, timeout=10, headers={
                    "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7)"
                })
                if page.status_code == 200:
                    chords = _extract_chords_from_text(page.text)
                    if len(chords) >= 3:
                        return chords
            except Exception:
                continue

        return []

    except Exception as e:
        print(f"[chords] ERROR: {e}")
        return []


def _extract_chords_from_text(text):
    """
    Extract chord names from HTML/text content.
    Looks for common chord patterns: Am, G, D, F#m, Bb, etc.
    """
    # Remove HTML tags
    clean = re.sub(r"<[^>]+>", " ", text)

    # Find chord-like tokens
    # Pattern: A-G optionally followed by # or b, optionally followed by m/maj/7/sus/dim/add/aug
    chord_pattern = r'\b([A-G][b#]?(?:m(?:aj)?|min|dim|aug|sus[24]?|add[0-9]?|[0-9])?(?:/[A-G][b#]?)?)\b'
    candidates = re.findall(chord_pattern, clean)

    if not candidates:
        return []

    # Filter: must be parseable as a chord
    valid = []
    for c in candidates:
        parsed = _parse_chord_name(c)
        if parsed is not None:
            valid.append(c)

    # Remove duplicates while preserving order
    seen = set()
    ordered = []
    for c in valid:
        if c not in seen:
            seen.add(c)
            ordered.append(c)

    print(f"[chords] extracted {len(ordered)} unique chords: {ordered[:12]}")
    return ordered


# ─── Main API ────────────────────────────────────────────────────────────────

def get_chord_progression(song_name, artist, key_num, mode_num):
    """
    Fetch chords from web and convert to roman numerals.
    Since web scraping gives chord vocabulary (not ordered sequence),
    we identify the diatonic chords used and match against known progressions.

    Returns (progression_string, source, confidence) or (None, None, 0)
    """
    if not song_name or not artist:
        return None, "none", 0.0

    # Step 1: Fetch raw chords
    raw_chords = fetch_chords_from_web(song_name, artist)

    if len(raw_chords) < 2:
        print(f"[chords] insufficient chord data ({len(raw_chords)} chords)")
        return None, "none", 0.0

    # Step 2: Parse each chord
    parsed = []
    for chord_str in raw_chords:
        result = _parse_chord_name(chord_str)
        if result:
            root_pc, is_minor = result
            parsed.append((chord_str, root_pc, is_minor))

    if len(parsed) < 2:
        return None, "none", 0.0

    # Step 3: Infer key from chords if not provided
    if key_num < 0:
        key_num, mode_num = _infer_key_from_chords(parsed)
        print(f"[chords] inferred key: {NOTE_NAMES[key_num]} {'Major' if mode_num == 1 else 'Minor'}")

    # Step 4: Convert to numeral set
    numeral_set = set()
    for chord_str, root_pc, is_minor in parsed:
        numeral = _chord_to_numeral(root_pc, is_minor, key_num, mode_num)
        numeral_set.add(numeral)
        print(f"[chords]   {chord_str:6s} → {numeral}")

    print(f"[chords] chord vocabulary: {numeral_set}")

    # Step 5: Match against known progressions
    # Score each known progression by how many of its chords appear in our set
    best_match = None
    best_score = 0

    for known_pattern, known_label in KNOWN_PROGS_FOR_MATCHING:
        known_set = set(known_pattern)
        overlap = len(known_set & numeral_set)
        total = len(known_set)
        # Score: fraction of known prog chords that we found
        score = overlap / total if total > 0 else 0
        # Bonus if all chords match
        if overlap == total:
            score += 0.2
        # Bonus for I being present (strong tonic evidence)
        tonic = "I" if mode_num == 1 else "i"
        if tonic in numeral_set and tonic in known_set:
            score += 0.1

        if score > best_score:
            best_score = score
            best_match = known_label

    print(f"[chords] best known match: {best_match} (score={best_score:.2f})")

    # Require high overlap to claim a match
    if best_score >= 0.75 and best_match:
        confidence = min(0.8, best_score)
        print(f"[chords] RESULT: {best_match} (source=web, confidence={confidence:.2f})")
        return best_match, "web", confidence

    # If no strong known match, report the chord vocabulary as a simple list
    # sorted by diatonic order (I, ii, iii, IV, V, vi)
    if mode_num == 1:
        order = MAJOR_NUMERALS
    else:
        order = MINOR_NUMERALS

    sorted_numerals = sorted(numeral_set, key=lambda n: order.index(n) if n in order else 99)

    if len(sorted_numerals) >= 2:
        result = " – ".join(sorted_numerals)
        confidence = 0.5  # moderate — we know the chords but not the order
        print(f"[chords] RESULT (vocabulary): {result} (confidence={confidence:.2f})")
        return result, "web_vocab", confidence

    return None, "none", 0.0


# Known progressions for vocabulary matching
KNOWN_PROGS_FOR_MATCHING = [
    (["I", "V", "vi", "IV"], "I – V – vi – IV"),
    (["I", "IV", "V"], "I – IV – V"),
    (["I", "IV", "V", "I"], "I – IV – V – I"),
    (["I", "IV", "vi", "V"], "I – IV – vi – V"),
    (["vi", "IV", "I", "V"], "vi – IV – I – V"),
    (["I", "vi", "IV", "V"], "I – vi – IV – V"),
    (["I", "V", "IV", "V"], "I – V – IV – V"),
    (["I", "iii", "IV", "V"], "I – iii – IV – V"),
    (["ii", "V", "I"], "ii – V – I"),
    (["I", "V", "vi", "iii", "IV"], "I – V – vi – iii – IV"),
    (["i", "VI", "III", "VII"], "i – VI – III – VII"),
    (["i", "iv", "v", "i"], "i – iv – v – i"),
    (["i", "VII", "VI", "V"], "i – VII – VI – V"),
    (["I", "IV", "I", "V"], "I – IV – I – V"),
]


def _infer_key_from_chords(parsed_chords):
    """Infer the key from a set of chords using pitch class frequency."""
    pc_counts = {}
    for _, root_pc, _ in parsed_chords:
        pc_counts[root_pc] = pc_counts.get(root_pc, 0) + 1

    # The most common root is likely I or sometimes V/IV
    most_common_pc = max(pc_counts, key=pc_counts.get)

    # Check if it's major or minor by looking at the quality of the most common chord
    is_minor = False
    for _, root_pc, minor in parsed_chords:
        if root_pc == most_common_pc:
            is_minor = minor
            break

    return most_common_pc, 0 if is_minor else 1


def _find_chord_pattern(seq, min_len=3, max_len=5):
    """Find repeating pattern in chord sequence."""
    if len(seq) <= max_len:
        return seq

    from collections import Counter
    best, best_count = None, 0
    for length in range(min_len, max_len + 1):
        pats = Counter()
        for i in range(len(seq) - length + 1):
            pats[tuple(seq[i:i + length])] += 1
        if pats:
            top_pat, top_count = pats.most_common(1)[0]
            sc = top_count * (1.15 if length == 4 else 1.0)
            if sc > best_count:
                best_count = sc
                best = list(top_pat)

    if best and best_count >= 1.5:
        return best
    return seq[:4]
