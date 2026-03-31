"""
db.py — Lightweight SQLite persistence for track metadata and analysis state.

Replaces history.json for track lookups. Keeps filesystem cache (outputs/) for
heavy payloads (stems, tabs, result JSON).

Statuses:
  - available: analysis complete, cached result exists
  - pending: analysis in progress or queued
  - unavailable: no analysis yet, not started
"""

import sqlite3
import time
import json
from pathlib import Path
from contextlib import contextmanager
from cache_version import ANALYSIS_VERSION

DB_PATH = Path("riffd.db")


def _get_conn():
    conn = sqlite3.connect(str(DB_PATH), timeout=5)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA foreign_keys=ON")
    return conn


@contextmanager
def _db():
    conn = _get_conn()
    try:
        yield conn
        conn.commit()
    finally:
        conn.close()


def init_db():
    """Create tables if they don't exist. Safe to call on every startup."""
    with _db() as conn:
        conn.execute("""
            CREATE TABLE IF NOT EXISTS tracks (
                spotify_track_id TEXT PRIMARY KEY,
                title TEXT NOT NULL,
                artist TEXT NOT NULL,
                album TEXT DEFAULT '',
                artwork_url TEXT,
                duration_ms INTEGER DEFAULT 0,
                year TEXT DEFAULT '',
                artist_id TEXT,
                yt_query TEXT DEFAULT '',
                analysis_status TEXT DEFAULT 'unavailable',
                analysis_version TEXT,
                job_id TEXT,
                created_at REAL,
                updated_at REAL,
                last_viewed REAL,
                times_opened INTEGER DEFAULT 0
            )
        """)
        conn.execute("""
            CREATE INDEX IF NOT EXISTS idx_tracks_status ON tracks(analysis_status)
        """)
        conn.execute("""
            CREATE INDEX IF NOT EXISTS idx_tracks_viewed ON tracks(last_viewed DESC)
        """)
    print(f"[db] initialized at {DB_PATH}")


def migrate_from_history_json():
    """One-time migration from history.json to SQLite. Idempotent."""
    history_file = Path("history.json")
    if not history_file.exists():
        return

    try:
        data = json.loads(history_file.read_text())
        if not isinstance(data, dict) or not data:
            return
    except (json.JSONDecodeError, IOError):
        return

    migrated = 0
    with _db() as conn:
        for track_id, entry in data.items():
            if not track_id or len(track_id) < 4:
                continue
            name = (entry.get("name") or "").strip()
            artist = (entry.get("artist") or "").strip()
            if not name or not artist:
                continue

            # Determine status from existing cache
            has_cache = entry.get("has_cache", False)
            version = entry.get("analysis_version", "")
            status = "available" if (has_cache and version == ANALYSIS_VERSION) else "unavailable"

            conn.execute("""
                INSERT OR IGNORE INTO tracks
                (spotify_track_id, title, artist, artwork_url, year, artist_id, yt_query,
                 analysis_status, analysis_version, job_id, created_at, updated_at,
                 last_viewed, times_opened)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """, (
                track_id, name, artist,
                entry.get("image_url"), entry.get("year", ""),
                entry.get("artist_id"), entry.get("yt_query", ""),
                status, version, entry.get("job_id"),
                entry.get("last_viewed", time.time()),
                time.time(),
                entry.get("last_viewed", time.time()),
                entry.get("times_opened", 0),
            ))
            migrated += 1

    if migrated:
        print(f"[db] migrated {migrated} entries from history.json")


# ─── Public API ──────────────────────────────────────────────────────────────

def get_track(spotify_track_id: str) -> dict | None:
    """Look up a track by Spotify ID. Returns dict or None."""
    with _db() as conn:
        row = conn.execute(
            "SELECT * FROM tracks WHERE spotify_track_id = ?",
            (spotify_track_id,)
        ).fetchone()
        return dict(row) if row else None


def upsert_track(spotify_track_id: str, title: str, artist: str, **kwargs):
    """Insert or update a track. kwargs can include any column name."""
    now = time.time()
    existing = get_track(spotify_track_id)

    with _db() as conn:
        if existing:
            sets = ["updated_at = ?"]
            vals = [now]
            for k, v in kwargs.items():
                if v is not None:
                    sets.append(f"{k} = ?")
                    vals.append(v)
            vals.append(spotify_track_id)
            conn.execute(f"UPDATE tracks SET {', '.join(sets)} WHERE spotify_track_id = ?", vals)
        else:
            cols = ["spotify_track_id", "title", "artist", "created_at", "updated_at"]
            vals = [spotify_track_id, title, artist, now, now]
            for k, v in kwargs.items():
                if v is not None:
                    cols.append(k)
                    vals.append(v)
            placeholders = ", ".join(["?"] * len(vals))
            conn.execute(f"INSERT INTO tracks ({', '.join(cols)}) VALUES ({placeholders})", vals)


def set_track_status(spotify_track_id: str, status: str, **kwargs):
    """Update analysis status and optional fields."""
    now = time.time()
    sets = ["analysis_status = ?", "updated_at = ?"]
    vals = [status, now]
    for k, v in kwargs.items():
        if v is not None:
            sets.append(f"{k} = ?")
            vals.append(v)
    vals.append(spotify_track_id)
    with _db() as conn:
        conn.execute(f"UPDATE tracks SET {', '.join(sets)} WHERE spotify_track_id = ?", vals)


def touch_track(spotify_track_id: str):
    """Update last_viewed and increment times_opened."""
    now = time.time()
    with _db() as conn:
        conn.execute("""
            UPDATE tracks SET last_viewed = ?, times_opened = times_opened + 1, updated_at = ?
            WHERE spotify_track_id = ?
        """, (now, now, spotify_track_id))


def get_recent_tracks(limit: int = 8) -> list[dict]:
    """Get recently viewed tracks with available analysis, sorted by last_viewed."""
    with _db() as conn:
        rows = conn.execute("""
            SELECT * FROM tracks
            WHERE analysis_status = 'available' AND title != '' AND artist != ''
            ORDER BY last_viewed DESC LIMIT ?
        """, (limit,)).fetchall()
        return [dict(r) for r in rows]


# ─── Analysis integration hook ───────────────────────────────────────────────

def get_analysis_for_track(spotify_track_id: str) -> dict | None:
    """
    Check if a completed analysis exists for this track.
    Returns the cached result dict or None.

    This is the single lookup point. A future Moises or other analysis
    provider would plug in here — check the DB for status, and if
    available, load the result from the filesystem cache.
    """
    track = get_track(spotify_track_id)
    if not track:
        return None
    if track["analysis_status"] != "available":
        return None
    if track.get("analysis_version") != ANALYSIS_VERSION:
        return None

    # Load from filesystem cache
    job_id = track.get("job_id")
    if not job_id:
        return None

    cache_file = Path("outputs") / job_id / "result_cache.json"
    if not cache_file.exists():
        # Cache file missing — mark as unavailable
        set_track_status(spotify_track_id, "unavailable")
        return None

    try:
        result = json.loads(cache_file.read_text())
        if result.get("_analysis_version") != ANALYSIS_VERSION:
            set_track_status(spotify_track_id, "unavailable")
            return None
        return result
    except (json.JSONDecodeError, IOError):
        return None
