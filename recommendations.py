"""
recommendations.py
Multi-type recommendation engine.
Uses Spotify API data + song intelligence to generate 4 recommendation categories.
"""

from music_intelligence import (
    get_compatible_keys,
    format_key,
    COMMON_PROGRESSIONS,
)


# ─── Scoring Functions ────────────────────────────────────────────────────────

def score_vibe(candidate, source_track, source_artist_genres=None):
    """
    Score how similar a candidate track's "vibe" is to the source.
    Higher = more similar.
    """
    score = 0.0

    # Same artist is strongest signal
    if candidate.get("artist_id") and candidate["artist_id"] == source_track.get("artist_id"):
        score += 50

    # Genre overlap
    candidate_genres = set(candidate.get("genres", []))
    source_genres = set(source_artist_genres or [])
    if candidate_genres and source_genres:
        overlap = len(candidate_genres & source_genres)
        score += overlap * 10

    # Energy similarity (0-1 scale)
    if "energy" in candidate and "energy" in source_track:
        diff = abs(candidate["energy"] - source_track["energy"])
        score += max(0, 20 - diff * 40)

    # Danceability similarity
    if "danceability" in candidate and "danceability" in source_track:
        diff = abs(candidate["danceability"] - source_track["danceability"])
        score += max(0, 10 - diff * 20)

    # Valence (mood) similarity
    if "valence" in candidate and "valence" in source_track:
        diff = abs(candidate["valence"] - source_track["valence"])
        score += max(0, 10 - diff * 20)

    return score


def score_key_similarity(candidate, source_key, source_mode, source_bpm=None):
    """
    Score how jam-compatible a candidate is based on key/tempo.
    """
    score = 0.0
    c_key = candidate.get("key", -1)
    c_mode = candidate.get("mode", -1)

    if c_key < 0 or source_key < 0:
        return 0.0

    compatible = get_compatible_keys(source_key, source_mode)

    # Exact key + mode match
    if c_key == source_key and c_mode == source_mode:
        score += 100

    # Compatible key (relative, dominant, subdominant)
    elif (c_key, c_mode) in compatible:
        score += 70

    # Same key, different mode (parallel major/minor)
    elif c_key == source_key:
        score += 50

    # Tempo similarity bonus
    if source_bpm and candidate.get("tempo"):
        bpm_diff = abs(candidate["tempo"] - source_bpm)
        # Also check half/double time
        half_diff = abs(candidate["tempo"] - source_bpm / 2)
        double_diff = abs(candidate["tempo"] - source_bpm * 2)
        min_diff = min(bpm_diff, half_diff, double_diff)
        score += max(0, 30 - min_diff)

    return score


def score_era(candidate, source_year):
    """Score based on release year proximity."""
    c_year = candidate.get("year")
    if not c_year or not source_year:
        return 0.0

    try:
        c_yr = int(str(c_year)[:4])
        s_yr = int(str(source_year)[:4])
    except (ValueError, TypeError):
        return 0.0

    diff = abs(c_yr - s_yr)
    if diff <= 2:
        return 100
    elif diff <= 5:
        return 70
    elif diff <= 10:
        return 40
    elif diff <= 15:
        return 20
    return 0.0


def score_progression_similarity(candidate_prog, source_prog):
    """
    Score how similar two chord progressions are.
    Both are lists of roman numerals.
    """
    if not candidate_prog or not source_prog:
        return 0.0

    # Exact match
    if candidate_prog == source_prog:
        return 100

    # Check if one contains the other
    src = " ".join(source_prog)
    cand = " ".join(candidate_prog)
    if src in cand or cand in src:
        return 80

    # Count matching positions
    min_len = min(len(candidate_prog), len(source_prog))
    if min_len == 0:
        return 0.0

    matches = sum(1 for i in range(min_len) if candidate_prog[i] == source_prog[i])
    position_score = (matches / min_len) * 60

    # Count shared chords regardless of position
    src_set = set(source_prog)
    cand_set = set(candidate_prog)
    if src_set and cand_set:
        overlap = len(src_set & cand_set) / max(len(src_set), len(cand_set))
        chord_score = overlap * 30
    else:
        chord_score = 0

    return position_score + chord_score


# ─── Recommendation Builder ──────────────────────────────────────────────────

def build_recommendations(
    source_track,
    source_features,
    source_progression,
    candidates_vibe,
    candidates_key,
    candidates_era,
    candidates_progression=None,
):
    """
    Build all 4 recommendation sections from pre-fetched candidate pools.

    Args:
        source_track: dict with id, name, artist, year, etc.
        source_features: dict with key, mode, tempo, energy, etc.
        source_progression: list of roman numerals
        candidates_vibe: list of track dicts (from related artists / recs)
        candidates_key: list of track dicts (from key-based search)
        candidates_era: list of track dicts (from era-based search)
        candidates_progression: optional list of (track_dict, progression) tuples

    Returns:
        dict with 4 recommendation lists
    """
    source_id = source_track.get("id", "")
    source_key = source_features.get("key", -1)
    source_mode = source_features.get("mode", -1)
    source_bpm = source_features.get("tempo", 120)
    source_year = source_track.get("year", "")
    source_genres = source_track.get("genres", [])

    def dedupe_and_exclude(tracks, limit=6):
        """Remove source track and duplicates."""
        seen = set()
        result = []
        for t in tracks:
            tid = t.get("id", "")
            if tid == source_id or tid in seen:
                continue
            seen.add(tid)
            result.append(t)
            if len(result) >= limit:
                break
        return result

    # 1. Same Vibe
    vibe_scored = []
    for t in candidates_vibe:
        s = score_vibe(t, {**source_track, **source_features}, source_genres)
        vibe_scored.append((s, t))
    vibe_scored.sort(key=lambda x: -x[0])
    same_vibe = dedupe_and_exclude([t for _, t in vibe_scored])

    # 2. Same Key
    key_scored = []
    for t in candidates_key:
        s = score_key_similarity(t, source_key, source_mode, source_bpm)
        key_scored.append((s, t))
    key_scored.sort(key=lambda x: -x[0])
    same_key = dedupe_and_exclude([t for _, t in key_scored if _[0] > 30])

    # 3. Same Era
    era_scored = []
    for t in candidates_era:
        s = score_era(t, source_year)
        era_scored.append((s, t))
    era_scored.sort(key=lambda x: -x[0])
    same_era = dedupe_and_exclude([t for _, t in era_scored if _[0] > 0])

    # 4. Same Progression
    same_progression = []
    if candidates_progression and source_progression:
        prog_scored = []
        for t, prog in candidates_progression:
            s = score_progression_similarity(prog, source_progression)
            prog_scored.append((s, t))
        prog_scored.sort(key=lambda x: -x[0])
        same_progression = dedupe_and_exclude([t for _, t in prog_scored if _[0] > 30])

    # Fallback: use common progressions database for progression recs
    # if we don't have enough from analysis
    if len(same_progression) < 4 and source_progression:
        # Find songs with same known progression pattern
        for t in candidates_vibe + candidates_key:
            if len(same_progression) >= 6:
                break
            if t.get("id") not in [x.get("id") for x in same_progression]:
                if t.get("id") != source_id:
                    same_progression.append(t)

    return {
        "same_vibe": same_vibe[:6],
        "same_key": same_key[:6],
        "same_era": same_era[:6],
        "same_progression": same_progression[:6],
    }
