"""
history.py
Song history + versioned result cache.
Uses a JSON file for history entries and the outputs/ dir for cached result payloads.
"""

import json
import time
from pathlib import Path

HISTORY_FILE = Path("history.json")
CACHE_DIR = Path("outputs")

# Bump this when analysis logic changes to invalidate old caches
ANALYSIS_VERSION = "v3"  # bumped: fixed BPM, stem labels, cache stems

# In-memory cache for history.json — avoids re-reading the file 4-6 times per song flow
_history_cache = None
_history_cache_time = 0
_HISTORY_CACHE_TTL = 5  # seconds


def _load_history() -> dict:
    """Load history from JSON file with short TTL cache. Returns {track_id: entry}."""
    global _history_cache, _history_cache_time
    now = time.time()
    if _history_cache is not None and (now - _history_cache_time) < _HISTORY_CACHE_TTL:
        return _history_cache
    if HISTORY_FILE.exists():
        try:
            data = json.loads(HISTORY_FILE.read_text())
            if isinstance(data, dict):
                _history_cache = data
                _history_cache_time = now
                return data
        except (json.JSONDecodeError, IOError):
            pass
    return {}


def _save_history(history: dict):
    """Save history to JSON file and update the in-memory cache."""
    global _history_cache, _history_cache_time
    try:
        HISTORY_FILE.write_text(json.dumps(history, indent=2, default=str))
        _history_cache = history
        _history_cache_time = time.time()
    except IOError as e:
        print(f"[history] save error: {e}")


def add_to_history(track_id: str, track_meta: dict, job_id: str):
    """
    Add or update a history entry after processing a song.
    Only saves entries with real metadata (name + artist).
    """
    if not track_id or len(track_id) < 4:
        print(f"[history] SKIPPED: invalid track_id ({track_id!r})")
        return

    name = (track_meta.get("name") or "").strip()
    artist = (track_meta.get("artist") or "").strip()
    if not name or not artist:
        print(f"[history] SKIPPED: missing name/artist (name={name!r}, artist={artist!r})")
        return
    # Filter out placeholder/test data
    if name.lower() in ("test", "untitled", "uploaded file") and artist.lower() in ("test", "unknown", "uploaded file", ""):
        print(f"[history] SKIPPED: placeholder data (name={name!r}, artist={artist!r})")
        return

    history = _load_history()

    entry = history.get(track_id, {})
    entry.update({
        "track_id": track_id,
        "name": track_meta.get("name", ""),
        "artist": track_meta.get("artist", ""),
        "image_url": track_meta.get("image_url"),
        "year": track_meta.get("year", ""),
        "artist_id": track_meta.get("artist_id"),
        "yt_query": track_meta.get("yt_query", ""),
        "preview_url": track_meta.get("preview_url") or entry.get("preview_url"),
        "job_id": job_id,
        "last_viewed": time.time(),
        "times_opened": entry.get("times_opened", 0) + 1,
        "analysis_version": ANALYSIS_VERSION,
        "has_cache": True,
    })

    history[track_id] = entry
    _save_history(history)
    print(f"[history] saved: {entry['name']} by {entry['artist']} (job={job_id}, v={ANALYSIS_VERSION})")


def _is_valid_entry(entry: dict) -> bool:
    """Check if a history entry has enough real metadata to display."""
    name = (entry.get("name") or "").strip()
    artist = (entry.get("artist") or "").strip()
    if not name or not artist:
        return False
    tid = entry.get("track_id") or ""
    if not tid or len(tid) < 4:
        return False
    if name.lower() in ("test", "untitled") and artist.lower() in ("test", "unknown", ""):
        return False
    return True


def get_recent(limit: int = 8) -> list[dict]:
    """
    Get recent songs sorted by last_viewed, newest first.
    Filters out invalid/placeholder entries.
    Returns list of history entry dicts.
    """
    history = _load_history()
    entries = sorted(history.values(), key=lambda e: e.get("last_viewed", 0), reverse=True)
    result = [e for e in entries if _is_valid_entry(e)][:limit]
    print(f"[history] get_recent: {len(result)} entries (total={len(history)})")
    for e in result[:3]:
        print(f"[history]   {e.get('name','')} by {e.get('artist','')} (v={e.get('analysis_version','?')}, cache={e.get('has_cache',False)})")
    return result


def touch_history(track_id: str):
    """Update last_viewed timestamp for a track (e.g. when reopening from cache)."""
    if not track_id:
        return
    history = _load_history()
    if track_id in history:
        history[track_id]["last_viewed"] = time.time()
        history[track_id]["times_opened"] = history[track_id].get("times_opened", 0) + 1
        _save_history(history)
        print(f"[history] touched: {history[track_id].get('name','')} (times={history[track_id]['times_opened']})")


def get_cached_result(track_id: str) -> dict | None:
    """
    Load a cached result for a track_id if it exists and version matches.
    Returns the full result dict (tabs, intelligence, etc.) or None.
    """
    if not track_id:
        return None

    history = _load_history()
    entry = history.get(track_id)

    if not entry or not entry.get("has_cache"):
        return None

    # Version check — critical for correctness
    if entry.get("analysis_version") != ANALYSIS_VERSION:
        print(f"[cache] stale version for {track_id}: {entry.get('analysis_version')} != {ANALYSIS_VERSION}")
        return None

    job_id = entry.get("job_id")
    if not job_id:
        return None

    # Load the cached result JSON
    cache_file = CACHE_DIR / job_id / "result_cache.json"
    if not cache_file.exists():
        print(f"[cache] no cache file at {cache_file}")
        return None

    try:
        result = json.loads(cache_file.read_text())
        # Verify version in the cache file itself
        if result.get("_analysis_version") != ANALYSIS_VERSION:
            print(f"[cache] cache file version mismatch")
            return None

        print(f"[cache] HIT for {track_id} (job={job_id}, v={ANALYSIS_VERSION})")
        return result
    except (json.JSONDecodeError, IOError) as e:
        print(f"[cache] load error: {e}")
        return None


def save_cached_result(job_id: str, result: dict):
    """
    Save a processing result to disk for future cache hits.
    """
    cache_dir = CACHE_DIR / job_id
    cache_dir.mkdir(parents=True, exist_ok=True)
    cache_file = cache_dir / "result_cache.json"

    # Stamp version
    result["_analysis_version"] = ANALYSIS_VERSION
    result["_cached_at"] = time.time()

    # Filter out non-serializable data (stems have file paths which are fine)
    # But tabs may have large text — keep it, it's the point of caching
    try:
        cache_file.write_text(json.dumps(result, indent=2, default=str))
        print(f"[cache] saved result for job={job_id} (v={ANALYSIS_VERSION})")
    except (IOError, TypeError) as e:
        print(f"[cache] save error: {e}")
